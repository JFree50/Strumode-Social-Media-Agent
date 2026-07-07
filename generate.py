"""
generate.py — Friday 9am CT. Drafts next week's two posts into content/queue/
as YYYY-MM-DD_story.md (Tuesday) and YYYY-MM-DD_value.md (Thursday), biased by
the latest performance summary. The GitHub Actions job then opens a PR — Jack's
merge is the approval gate. Nothing here publishes anything.

Run manually with FORCE_RUN=1 to draft on demand (dry-run friendly).
"""
from __future__ import annotations

import sys

import claude_client
import common

log = common.get_logger("generate")


def _system_prompt() -> str:
    cfg = common.load_config()["content"]
    text = (common.PROMPTS_DIR / "content_system_prompt.md").read_text(encoding="utf-8")
    return (text.replace("{{HASHTAGS_MIN}}", str(cfg["hashtags_min"]))
                .replace("{{HASHTAGS_MAX}}", str(cfg["hashtags_max"])))


def _performance_summary() -> str:
    if common.WEEKLY_REPORT_PATH.exists():
        txt = common.WEEKLY_REPORT_PATH.read_text(encoding="utf-8").strip()
        if txt:
            return txt
    return "No performance data yet — write a strong general post for the ideal reader."


def _clamp_hashtags(tags: list[str]) -> list[str]:
    cfg = common.load_config()["content"]
    return tags[: cfg["hashtags_max"]]


def _ensure_suffix(text: str, suffix: str) -> str:
    return text if suffix.strip().lower() in text.strip().lower() else f"{text.rstrip()}\n\n{suffix}"


def _base_frontmatter(post_type: str, post_date: str, media_type: str) -> dict:
    return {"type": post_type, "post_date": post_date, "media_type": media_type,
            "status": "draft", "style_override": "", "media_id": "", "published_at": ""}


def generate_story(post_date: str, system: str, perf: str) -> str:
    cfg = common.load_config()["content"]
    user = (f"Write the STORY DAY carousel that publishes on Tuesday {post_date}.\n"
            f"Produce {cfg['story_slides_min']}–{cfg['story_slides_max']} slides.\n\n"
            f"Recent performance to lean into:\n{perf}")
    data = claude_client.generate("story", system, user,
                                  common.load_config()["models"]["content_model"],
                                  common.load_config()["models"]["content_max_tokens"])
    slides = [{"text": s["on_image_text"], "image_prompt": s["image_prompt"]}
              for s in data["slides"]]
    # Self-heal alt-text count: the model occasionally returns one too few/many.
    # Pad missing entries from the slide's own on-image text; trim extras.
    alt_texts = list(data.get("alt_texts") or [])[: len(slides)]
    while len(alt_texts) < len(slides):
        alt_texts.append(f"Sketch illustration: {slides[len(alt_texts)]['text']}")
    caption = _ensure_suffix(data["caption"], cfg["story_cta"])
    fm = _base_frontmatter("story", post_date, "carousel")
    md = common.render_story_markdown(fm, data["hook"], slides, caption,
                                      _clamp_hashtags(data["hashtags"]), alt_texts)
    path = common.QUEUE_DIR / common.draft_filename(post_date, "story")
    path.write_text(md, encoding="utf-8")
    return str(path)


def generate_value(post_date: str, system: str, perf: str) -> str:
    cfg = common.load_config()["content"]
    user = (f"Write the VALUE DAY post that publishes on Thursday {post_date}.\n"
            f"The teaser caption MUST end with: {cfg['value_cta']!r}\n"
            f"Include an excellent, specific prompt payload for the DM.\n\n"
            f"Recent performance to lean into:\n{perf}")
    data = claude_client.generate("value", system, user,
                                  common.load_config()["models"]["content_model"],
                                  common.load_config()["models"]["content_max_tokens"])
    caption = _ensure_suffix(data["teaser_caption"], cfg["value_cta"])
    fm = _base_frontmatter("value", post_date, "image")
    md = common.render_value_markdown(fm, data["image_prompt"], caption,
                                      data["prompt_payload"],
                                      _clamp_hashtags(data["hashtags"]), data["alt_text"])
    path = common.QUEUE_DIR / common.draft_filename(post_date, "value")
    path.write_text(md, encoding="utf-8")
    return str(path)


def main() -> int:
    if common.is_paused():
        log.info("Agent is paused — skipping generation.")
        return 0
    if not common.should_run("Friday", 9, log):
        return 0

    common.QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    today = common.now_central().date()
    tuesday = common.next_weekday(today, "Tuesday").isoformat()
    thursday = common.next_weekday(today, "Thursday").isoformat()

    system = _system_prompt()
    perf = _performance_summary()
    log.info("Generating posts for Tue %s (story) and Thu %s (value).", tuesday, thursday)

    paths = [generate_story(tuesday, system, perf),
             generate_value(thursday, system, perf)]

    # Validate before handing to the PR — a broken draft should fail the run,
    # not silently become a PR Jack has to catch by eye.
    problems: list[str] = []
    for p in paths:
        post = common.parse_draft(common.Path(p))
        for err in common.validate_draft(post):
            problems.append(f"{common.Path(p).name}: {err}")
    if problems:
        for pr in problems:
            log.error("VALIDATION: %s", pr)
        raise SystemExit("Generated drafts failed validation — see errors above.")

    for p in paths:
        log.info("Wrote %s", p)
    print("\n".join(paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
