"""
insights.py — Monday 8am CT. Pulls per-post + account metrics for everything in
content/published/, appends a timestamped snapshot to data/metrics.json, and
writes a short human-readable data/weekly_report.md. generate.py consumes that
report so Friday's drafts lean toward what's working.

Reading metrics is safe (never publishes). If nothing has been published yet,
it writes a "no data" report and makes no network calls — so it's harmless
during the dry-run phase before any secrets are set.

North-star note: Meta deprecated `profile_views`, so the funnel we track is
reach → profile_links_taps (link taps) → follower growth, per docs/API_NOTES.md.
"""
from __future__ import annotations

import json
import sys

import common
from ig_client import IGClient

log = common.get_logger("insights")


def load_metrics() -> dict:
    if common.METRICS_PATH.exists():
        return json.loads(common.METRICS_PATH.read_text(encoding="utf-8"))
    return {"posts": {}, "account": []}


def save_metrics(data: dict) -> None:
    common.DATA_DIR.mkdir(parents=True, exist_ok=True)
    common.METRICS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _published_posts() -> list[dict]:
    posts = []
    for path in sorted(common.PUBLISHED_DIR.glob("*.md")):
        post = common.parse_draft(path)
        if post.get("media_id"):
            posts.append(post)
    return posts


def _score(snapshot: dict) -> int:
    """Optimise for saves + shares (per the Gameplan), with reach as a tiebreak."""
    return (int(snapshot.get("saved") or 0) + int(snapshot.get("shares") or 0)) * 1000 \
        + int(snapshot.get("reach") or 0)


def _pull(now_iso: str) -> dict:
    data = load_metrics()
    posts = _published_posts()
    if not posts:
        log.info("No published posts yet — nothing to pull.")
        _write_report(data, [], None)
        save_metrics(data)
        return data

    ig = IGClient(common.env("IG_ACCESS_TOKEN", required=True), common.ig_user_id())

    for post in posts:
        mid = post["media_id"]
        snapshot = {"pulled_at": now_iso, **ig.media_insights(mid)}
        entry = data["posts"].setdefault(mid, {
            "type": post["type"], "post_date": post["post_date"],
            "caption_excerpt": post["caption"][:120], "topics": post["hashtags"],
            "history": [],
        })
        entry["history"].append(snapshot)
        log.info("Pulled metrics for %s (%s).", post["post_date"], post["type"])

    fields = ig.account_fields()
    account = ig.account_insights()
    acct_snapshot = {"pulled_at": now_iso,
                     "followers_count": fields.get("followers_count"),
                     "follows_count": fields.get("follows_count"),
                     "media_count": fields.get("media_count"),
                     "reach": account.get("reach"),
                     "profile_links_taps": account.get("profile_links_taps"),
                     "net_follows": account.get("follows_and_unfollows")}
    data["account"].append(acct_snapshot)

    _write_report(data, posts, acct_snapshot)
    save_metrics(data)
    return data


def _latest_snapshot(data: dict, mid: str) -> dict:
    hist = data["posts"].get(mid, {}).get("history", [])
    return hist[-1] if hist else {}


def _write_report(data: dict, posts: list[dict], acct: dict | None) -> None:
    lines = [f"# Weekly Instagram report — {common.now_central().strftime('%Y-%m-%d')}", ""]

    if not posts:
        lines += ["No posts have been published yet, so there's no performance data.",
                  "Next Friday's drafts will use general best-practice content."]
        common.WEEKLY_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        common.WEEKLY_REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    ranked = sorted(posts, key=lambda p: _score(_latest_snapshot(data, p["media_id"])), reverse=True)

    def _fmt(p: dict) -> str:
        s = _latest_snapshot(data, p["media_id"])
        return (f"- **{p['post_date']} ({p['type']})** — reach {s.get('reach')}, "
                f"saves {s.get('saved')}, shares {s.get('shares')}, "
                f"comments {s.get('comments')}, likes {s.get('likes')}. "
                f"“{p['caption'][:70].strip()}…”")

    lines += ["## What won", *[_fmt(p) for p in ranked[:3]], ""]
    if len(ranked) > 3:
        lines += ["## What underperformed", *[_fmt(p) for p in ranked[-2:]], ""]

    top = ranked[0]
    top_topics = ", ".join(top["topics"][:6]) or "(no hashtags)"
    lines += ["## What next week should lean into",
              f"- Best performer was a **{top['type']}** post. Do more of that format.",
              f"- Winning topics/hashtags: {top_topics}.",
              "- Optimise Value Day assets for saves + shares; reply to comments in hour 1.", ""]

    if acct:
        lines += ["## Account",
                  f"- Followers: {acct.get('followers_count')} "
                  f"(net change in period: {acct.get('net_follows')}).",
                  f"- Reach: {acct.get('reach')}; link taps: {acct.get('profile_links_taps')}.",
                  "- (Meta deprecated profile_views; we track reach + link taps + follower growth.)"]

    common.WEEKLY_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    common.WEEKLY_REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("Wrote %s", common.WEEKLY_REPORT_PATH.name)


def main() -> int:
    if common.is_paused():
        log.info("Agent is paused — skipping insights.")
        return 0
    if not common.should_run("Monday", 8, log):
        return 0
    _pull(common.now_central().isoformat(timespec="seconds"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
