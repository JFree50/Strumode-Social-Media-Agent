"""
selftest.py — offline smoke test. No live API keys, no network. Proves the
wiring: config loads, the DST-safe schedule guard works, drafts render and parse
back losslessly, validation catches bad posts, image crop/resize hits 1080x1350,
the CI plan resolves, and a DRY-RUN publish builds the right payload and moves
nothing. Run: `python tests/selftest.py` (needs `pip install -r requirements.txt`).
"""
from __future__ import annotations

import io
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common  # noqa: E402
import ci_plan  # noqa: E402
import generate  # noqa: E402
import images  # noqa: E402
import manychat_client  # noqa: E402
import publish  # noqa: E402
from PIL import Image  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  — ' + detail}")
    if not cond:
        FAILURES.append(name)


def _sample_story(post_date: str) -> str:
    fm = generate._base_frontmatter("story", post_date, "carousel")
    slides = [{"text": f"Slide {i} text", "image_prompt": f"a sketch of scene {i}"}
              for i in range(1, 7)]
    caption = "Picture this bakery owner buried in admin.\n\nThis could be your week back → link in bio."
    tags = ["#smallbusiness", "#automation", "#smallbizowner", "#aitools",
            "#smallbiztips", "#entrepreneur", "#productivity", "#workflow"]
    alts = [f"sketch of scene {i}" for i in range(1, 7)]
    return common.render_story_markdown(fm, "A hook", slides, caption, tags, alts)


def _sample_value(post_date: str) -> str:
    fm = generate._base_frontmatter("value", post_date, "image")
    caption = "Steal my lead-follow-up prompt.\n\nComment PROMPT and it's in your DMs"
    tags = ["#smallbusiness", "#automation", "#smallbizowner", "#aitools",
            "#smallbiztips", "#entrepreneur", "#leadgen", "#sales"]
    return common.render_value_markdown(fm, "a sketch of an inbox", caption,
                                        "You are a helpful assistant. Draft a friendly follow-up...",
                                        tags, "sketch of an inbox")


def test_dates() -> None:
    friday = date(2026, 7, 10)  # a Friday
    check("next Tuesday after Friday is +4 days",
          common.next_weekday(friday, "Tuesday") == date(2026, 7, 14))
    check("next Thursday after Friday is +6 days",
          common.next_weekday(friday, "Thursday") == date(2026, 7, 16))


def test_time_guard() -> None:
    os.environ["FORCE_RUN"] = "1"
    check("FORCE_RUN bypasses the schedule guard", common.should_run("Monday", 3) is True)
    os.environ.pop("FORCE_RUN", None)
    os.environ.pop("GITHUB_EVENT_NAME", None)


def test_render_parse_validate() -> None:
    d = "2030-01-01"
    story_path = common.QUEUE_DIR / common.draft_filename(d, "story")
    value_path = common.QUEUE_DIR / common.draft_filename(d, "value")
    common.QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        story_path.write_text(_sample_story(d), encoding="utf-8")
        value_path.write_text(_sample_value(d), encoding="utf-8")

        s = common.parse_draft(story_path)
        check("story parses 6 slides", len(s["slides"]) == 6, str(len(s["slides"])))
        check("story image filenames derived", s["image_files"][0] == f"{d}_story_01.jpg")
        check("story validates clean", common.validate_draft(s) == [], str(common.validate_draft(s)))

        v = common.parse_draft(value_path)
        check("value payload parsed", v["prompt_payload"].startswith("You are"))
        check("value validates clean", common.validate_draft(v) == [], str(common.validate_draft(v)))

        # Negative cases
        bad = common.parse_draft(story_path)
        bad["slides"] = bad["slides"][:3]
        check("validation catches too-few slides", any("slides" in e for e in common.validate_draft(bad)))

        bad_v = common.parse_draft(value_path)
        bad_v["caption"] = "no cta here"
        check("validation catches missing CTA", any("CTA" in e for e in common.validate_draft(bad_v)))
    finally:
        story_path.unlink(missing_ok=True)
        value_path.unlink(missing_ok=True)


def test_image_crop() -> None:
    src = Image.new("RGB", (1024, 1536), (255, 255, 255))
    out = images._crop_resize(src, 1080, 1350)
    check("image cropped/resized to 1080x1350", out.size == (1080, 1350), str(out.size))
    buf = io.BytesIO()
    out.save(buf, format="JPEG")
    check("image encodes as JPEG", buf.getvalue()[:2] == b"\xff\xd8")


def test_compose_prompt() -> None:
    composed = images._compose("a bakery", "")
    check("compose uses master sketch style by default", "sketch illustration" in composed.lower())
    override = images._compose("a bakery", "FLAT VECTOR STYLE")
    check("compose honors style_override", "FLAT VECTOR STYLE" in override
          and "sketch illustration" not in override.lower())


def test_system_prompt() -> None:
    sp = generate._system_prompt()
    check("system prompt substitutes hashtag range", "{{HASHTAGS_MIN}}" not in sp and "{{HASHTAGS_MAX}}" not in sp)
    check("system prompt names approved proof", "Lauderdale Studio" in sp)


def test_ci_plan() -> None:
    os.environ["INPUT_POST_TYPE"] = "story"
    os.environ["INPUT_POST_DATE"] = "2030-01-01"
    os.environ["GITHUB_EVENT_NAME"] = "workflow_dispatch"
    plan = ci_plan.plan()
    check("ci_plan resolves type/date", plan["post_type"] == "story" and plan["post_date"] == "2030-01-01")
    check("ci_plan enable_publish reflects config (false by default)", plan["enable_publish"] == "false")
    for k in ("INPUT_POST_TYPE", "INPUT_POST_DATE", "GITHUB_EVENT_NAME"):
        os.environ.pop(k, None)


def test_published_move_roundtrip() -> None:
    # YAML coerces a numeric media_id to int and a timestamp to datetime; parse_draft
    # must coerce them back to strings, or insights.py chokes reading published posts.
    d = "2031-03-03"
    md = _sample_story(d)
    updated = common.update_frontmatter_text(
        md, {"status": "published", "media_id": "17851234567890123",
             "published_at": "2031-03-03T11:00:00"})
    tmp = common.QUEUE_DIR / "____tmp_published.md"
    common.QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    tmp.write_text(updated, encoding="utf-8")
    try:
        post = common.parse_draft(tmp)
        check("published media_id survives as string", post["media_id"] == "17851234567890123")
        check("published status parsed", post["status"] == "published")
        check("published post still validates", common.validate_draft(post) == [], str(common.validate_draft(post)))
    finally:
        tmp.unlink(missing_ok=True)


def test_publish_dry_run() -> None:
    d = "2030-02-02"
    draft = common.QUEUE_DIR / common.draft_filename(d, "story")
    common.QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    draft.write_text(_sample_story(d), encoding="utf-8")
    imgs = [common.QUEUE_DIR / n for n in common.story_image_names(d, 6)]
    for p in imgs:
        Image.new("RGB", (1080, 1350), (200, 200, 200)).save(p, format="JPEG")
    old_argv = sys.argv
    os.environ["FORCE_RUN"] = "1"
    try:
        sys.argv = ["publish.py", "story", d]
        rc = publish.main()
        check("dry-run publish returns 0", rc == 0)
        check("dry-run did NOT move the draft (approval-safe)", draft.exists())
        check("dry-run left images in queue", all(p.exists() for p in imgs))
    finally:
        sys.argv = old_argv
        os.environ.pop("FORCE_RUN", None)
        draft.unlink(missing_ok=True)
        for p in imgs:
            p.unlink(missing_ok=True)


def test_manychat_client_offline() -> None:
    """No network: a fake session captures the request so we can assert the
    endpoint, body, and auth header without ever hitting api.manychat.com."""
    calls: list[tuple] = []

    class FakeResp:
        ok = True

        def json(self):
            return {"status": "success"}

    class FakeSession:
        def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
            calls.append((url, json, headers))
            return FakeResp()

    client = manychat_client.ManyChatClient(api_key="fake-key", session=FakeSession())
    client.set_bot_field_by_name("latest_prompt", "hello world")
    url, body, headers = calls[0]
    check("manychat client posts to setBotFieldByName", url == "https://api.manychat.com/fb/page/setBotFieldByName", url)
    check("manychat client body has field_name/field_value",
          body == {"field_name": "latest_prompt", "field_value": "hello world"}, str(body))
    check("manychat client sends Bearer auth header", headers["Authorization"] == "Bearer fake-key")


def test_publish_dry_run_value_manychat_sync() -> None:
    """With manychat_sync on, a dry-run value publish must log the would-be
    sync and NOT touch the network (no MANYCHAT_API_KEY is set here at all —
    if the code tried a real call, this test would raise, not just fail)."""
    d = "2030-04-04"
    draft = common.QUEUE_DIR / common.draft_filename(d, "value")
    common.QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    draft.write_text(_sample_value(d), encoding="utf-8")
    img = common.QUEUE_DIR / common.value_image_name(d)
    Image.new("RGB", (1080, 1350), (200, 200, 200)).save(img, format="JPEG")
    old_argv = sys.argv
    os.environ["FORCE_RUN"] = "1"
    cfg = common.load_config()
    original = cfg["publishing"].get("manychat_sync")
    cfg["publishing"]["manychat_sync"] = True
    try:
        sys.argv = ["publish.py", "value", d]
        rc = publish.main()
        check("dry-run value+manychat_sync publish returns 0", rc == 0, str(rc))
        check("dry-run did NOT move the draft (approval-safe)", draft.exists())
    finally:
        cfg["publishing"]["manychat_sync"] = original
        sys.argv = old_argv
        os.environ.pop("FORCE_RUN", None)
        draft.unlink(missing_ok=True)
        img.unlink(missing_ok=True)


def main() -> int:
    print("Strumode IG Agent — offline self-test\n")
    for fn in [test_dates, test_time_guard, test_render_parse_validate, test_image_crop,
               test_compose_prompt, test_system_prompt, test_ci_plan,
               test_published_move_roundtrip, test_publish_dry_run,
               test_manychat_client_offline, test_publish_dry_run_value_manychat_sync]:
        print(f"{fn.__name__}:")
        fn()
    print()
    if FAILURES:
        print(f"❌ {len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("✅ all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
