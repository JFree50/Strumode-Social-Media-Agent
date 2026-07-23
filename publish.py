"""
publish.py — Tue/Thu 11am CT. Publishes the ALREADY-MERGED post for a date via
the Instagram Content Publishing API (graph.instagram.com).

The approval gate is permanent and unbypassable here: this script only reads
files that are present in content/queue/ — which, in production, only happens
after Jack merges the PR to main. There is no path that generates, edits, or
regenerates content at publish time; it publishes exactly what was approved.

Safety:
  • enable_publish defaults to FALSE → DRY RUN: every step runs except the final
    IG calls, and the exact payload is logged with "[DRY RUN]".
  • Never publishes a post whose media_id is already set, and moves published
    posts out of the queue — so a re-run can never double-post.
  • Never regenerates images; a missing approved image fails the run loudly.

Usage: python publish.py <story|value> [YYYY-MM-DD]   (date defaults to today CT)
The publish WORKFLOW pushes the approved images to the public assets repo BEFORE
calling this, so the raw image URLs are reachable by Meta when we create the
media container.
"""
from __future__ import annotations

import shutil
import sys

import common
from ig_client import IGClient
from manychat_client import ManyChatClient, ManyChatError

log = common.get_logger("publish")


def _image_url(assets_repo: str, branch: str, post_date: str, ptype: str, fname: str) -> str:
    return f"https://raw.githubusercontent.com/{assets_repo}/{branch}/{post_date}_{ptype}/{fname}"


def _resolve_target() -> tuple[str, str]:
    if len(sys.argv) < 2 or sys.argv[1] not in {"story", "value"}:
        raise SystemExit("usage: python publish.py <story|value> [YYYY-MM-DD]")
    ptype = sys.argv[1]
    post_date = sys.argv[2] if len(sys.argv) > 2 else common.now_central().date().isoformat()
    return ptype, post_date


def _guard_schedule(ptype: str) -> bool:
    weekday, hour = ("Tuesday", 11) if ptype == "story" else ("Thursday", 11)
    return common.should_run(weekday, hour, log)


def _local_images(post: dict) -> list:
    paths = []
    for fname in post["image_files"]:
        p = post["path"].parent / fname
        if not p.exists():
            raise SystemExit(f"Approved image missing: {fname}. Refusing to publish a partial post.")
        paths.append(p)
    return paths


def _move_to_published(post: dict, media_id: str) -> None:
    updates = {"status": "published", "media_id": media_id,
               "published_at": common.now_central().isoformat(timespec="seconds")}
    text = post["path"].read_text(encoding="utf-8")
    new_text = common.update_frontmatter_text(text, updates)
    common.PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    (common.PUBLISHED_DIR / post["path"].name).write_text(new_text, encoding="utf-8")
    for fname in post["image_files"]:
        shutil.move(str(post["path"].parent / fname), str(common.PUBLISHED_DIR / fname))
    post["path"].unlink()
    log.info("Moved %s + %d image(s) to content/published/.",
             post["path"].name, len(post["image_files"]))


def _sync_manychat(post: dict, pub: dict, live: bool) -> None:
    """Push the Value Day prompt payload into a ManyChat Bot Field so the DM
    flows (owned entirely in ManyChat) can reference it as {{field_name}}
    instead of Jack pasting it in by hand each week. Only runs for 'value'
    posts, and only if publishing.manychat_sync is true in config.yaml. A
    failure here does NOT unpublish or roll back the Instagram post — the IG
    post is the thing that must never silently disappear — but it DOES make
    the run exit non-zero so the failure emails Jack."""
    if post["type"] != "value" or not pub.get("manychat_sync", False):
        return
    field_name = pub.get("manychat_bot_field", "latest_prompt")
    payload = post.get("prompt_payload", "")
    if not live:
        log.info("[DRY RUN] would set ManyChat bot field %r (%d chars): %s",
                 field_name, len(payload), payload[:80] + ("…" if len(payload) > 80 else ""))
        return
    mc = ManyChatClient(common.env("MANYCHAT_API_KEY", required=True))
    mc.set_bot_field_by_name(field_name, payload)
    log.info("Synced ManyChat bot field %r with this week's prompt payload.", field_name)


def _dry_run(post: dict, urls: list[str], caption: str) -> None:
    log.info("[DRY RUN] enable_publish is false — not calling Instagram.")
    log.info("[DRY RUN] media_type=%s  post=%s", post["media_type"], post["path"].name)
    log.info("[DRY RUN] caption:\n%s", caption)
    if post["type"] == "story":
        log.info("[DRY RUN] would create %d carousel item containers, then a CAROUSEL "
                 "container with children, poll until FINISHED, then media_publish:", len(urls))
    else:
        log.info("[DRY RUN] would create 1 image container, poll until FINISHED, then media_publish:")
    for i, url in enumerate(urls, 1):
        log.info("[DRY RUN]   image %d -> %s", i, url)
    log.info("[DRY RUN] no files moved; nothing published.")


def _publish_story(ig: IGClient, post: dict, urls: list[str], caption: str,
                   timeout: int, interval: int) -> str:
    children = []
    for i, url in enumerate(urls):
        alt = post["alt_texts"][i] if i < len(post["alt_texts"]) else None
        cid = ig.create_image_container(url, alt_text=alt, is_carousel_item=True)
        ig.wait_until_finished(cid, timeout, interval)
        children.append(cid)
        log.info("Carousel item %d/%d ready (%s).", i + 1, len(urls), cid)
    carousel = ig.create_carousel_container(children, caption)
    ig.wait_until_finished(carousel, timeout, interval)
    return ig.publish(carousel)


def _publish_value(ig: IGClient, post: dict, urls: list[str], caption: str,
                   timeout: int, interval: int) -> str:
    alt = post["alt_texts"][0] if post["alt_texts"] else None
    cid = ig.create_image_container(urls[0], caption=caption, alt_text=alt)
    ig.wait_until_finished(cid, timeout, interval)
    return ig.publish(cid)


def main() -> int:
    if common.is_paused():
        log.info("Agent is paused — skipping publish.")
        return 0

    ptype, post_date = _resolve_target()
    if not _guard_schedule(ptype):
        return 0

    draft_path = common.QUEUE_DIR / common.draft_filename(post_date, ptype)
    if not draft_path.exists():
        log.info("No merged %s draft for %s in queue — nothing to publish.", ptype, post_date)
        return 0

    post = common.parse_draft(draft_path)

    # Never double-post.
    if post.get("media_id"):
        log.warning("%s already has media_id=%s — refusing to republish.",
                    draft_path.name, post["media_id"])
        return 0
    if post.get("status") != "draft":
        log.warning("%s status is %r (expected 'draft') — skipping.", draft_path.name, post["status"])
        return 0

    problems = common.validate_draft(post)
    if problems:
        for pr in problems:
            log.error("VALIDATION: %s", pr)
        raise SystemExit(f"{draft_path.name} failed validation — refusing to publish.")

    cfg = common.load_config()
    pub = cfg["publishing"]
    _local_images(post)  # verify approved images exist locally
    urls = [_image_url(pub["assets_repo"], pub["assets_branch"], post_date, ptype, f)
            for f in post["image_files"]]
    caption = common.caption_with_hashtags(post)

    if not pub.get("enable_publish", False):
        _dry_run(post, urls, caption)
        _sync_manychat(post, pub, live=False)
        return 0

    # ── live publish ─────────────────────────────────────────────────────────
    log.info("enable_publish is TRUE — publishing %s to Instagram.", draft_path.name)
    ig = IGClient(common.env("IG_ACCESS_TOKEN", required=True), common.ig_user_id())
    timeout, interval = pub["poll_timeout_seconds"], pub["poll_interval_seconds"]
    if post["type"] == "story":
        media_id = _publish_story(ig, post, urls, caption, timeout, interval)
    else:
        media_id = _publish_value(ig, post, urls, caption, timeout, interval)
    log.info("Published! media_id=%s", media_id)
    _move_to_published(post, media_id)

    # ManyChat sync happens AFTER the IG post is safely published + moved, so a
    # ManyChat-side failure never rolls back or blocks the Instagram post — the
    # post being live is what matters most. It still fails the run LOUDLY
    # (non-zero exit -> GitHub emails Jack) so a stale DM payload gets caught,
    # not silently ignored.
    try:
        _sync_manychat(post, pub, live=True)
    except ManyChatError as exc:
        log.error("ManyChat sync failed (Instagram post is already live and safe): %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
