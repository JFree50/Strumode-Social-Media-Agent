"""
common.py — shared helpers for the Strumode IG Agent.

Holds the pieces every entrypoint needs: config + env loading, the global pause
switch, a DST-aware "is it the right Central time to run?" guard, and the draft
markdown render/parse/validate helpers. Draft markdown is the single source of
truth that survives Jack's PR review, so the render format and the parser here
are deliberately kept in lockstep.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

REPO_ROOT = Path(__file__).resolve().parent
PROMPTS_DIR = REPO_ROOT / "prompts"
QUEUE_DIR = REPO_ROOT / "content" / "queue"
PUBLISHED_DIR = REPO_ROOT / "content" / "published"
DATA_DIR = REPO_ROOT / "data"
METRICS_PATH = DATA_DIR / "metrics.json"
WEEKLY_REPORT_PATH = DATA_DIR / "weekly_report.md"

# Captions/CTAs contain non-ASCII (e.g. "→"). Force UTF-8 stdout/stderr so logging
# never dies on a Windows cp1252 console; GitHub's Linux runners are already UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

CENTRAL = ZoneInfo("America/Chicago")
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


# ── logging ──────────────────────────────────────────────────────────────────
def get_logger(name: str = "strumode") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


# ── config + env ─────────────────────────────────────────────────────────────
_CONFIG_CACHE: dict | None = None


def load_config() -> dict:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        with (REPO_ROOT / "config.yaml").open(encoding="utf-8") as fh:
            _CONFIG_CACHE = yaml.safe_load(fh)
    return _CONFIG_CACHE


def parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def env(name: str, required: bool = False, default: str | None = None) -> str | None:
    """Read a secret/config value from the environment. Secrets ONLY come from
    the environment (GitHub Actions secrets) — never from files in the repo."""
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(
            f"Missing required environment variable {name!r}. "
            f"Set it as a GitHub Actions secret (or in a local .env for dry-run)."
        )
    return val


def ig_user_id() -> str:
    """IG user id from secret, falling back to the (optional) config value."""
    val = os.environ.get("IG_USER_ID") or load_config()["account"].get("ig_user_id_fallback")
    if not val:
        raise RuntimeError("IG_USER_ID is not set (secret) and no config fallback provided.")
    return str(val)


# ── pause + time guard ───────────────────────────────────────────────────────
def is_paused() -> bool:
    """True if the whole agent is paused, via either the repo Actions variable
    AGENT_PAUSED=1 (instant, no commit) or config.yaml `paused: true`."""
    if parse_bool(os.environ.get("AGENT_PAUSED", "")):
        return True
    return bool(load_config().get("paused", False))


def now_central() -> datetime:
    return datetime.now(CENTRAL)


def _is_manual_run() -> bool:
    return parse_bool(os.environ.get("FORCE_RUN", "")) or \
        os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"


def should_run(weekday_name: str, hour: int, log: logging.Logger | None = None) -> bool:
    """DST-safe schedule gate. GitHub cron is UTC and cannot express a timezone,
    so each workflow fires at BOTH candidate UTC hours (CST and CDT) and this
    guard lets exactly one of them proceed — the one that is `hour`:00 Central on
    `weekday_name`. Manual `workflow_dispatch` runs bypass the guard."""
    if _is_manual_run():
        return True
    now = now_central()
    target_wd = _WEEKDAYS[weekday_name.lower()]
    ok = now.weekday() == target_wd and now.hour == hour
    if not ok and log:
        log.info(
            "Not the scheduled slot (now %s %02d:00 CT; want %s %02d:00 CT). Exiting cleanly.",
            now.strftime("%A"), now.hour, weekday_name, hour,
        )
    return ok


def next_weekday(from_date: date, weekday_name: str) -> date:
    """The next `weekday_name` strictly after `from_date`. Run on a Friday, this
    returns the following week's Tuesday/Thursday."""
    target = _WEEKDAYS[weekday_name.lower()]
    ahead = (target - from_date.weekday()) % 7
    ahead = ahead or 7  # strictly after
    return from_date + timedelta(days=ahead)


# ── image filename helpers ───────────────────────────────────────────────────
def story_image_names(post_date: str, n_slides: int) -> list[str]:
    return [f"{post_date}_story_{i:02d}.jpg" for i in range(1, n_slides + 1)]


def value_image_name(post_date: str) -> str:
    return f"{post_date}_value.jpg"


def draft_filename(post_date: str, post_type: str) -> str:
    return f"{post_date}_{post_type}.md"


# ── draft discovery ──────────────────────────────────────────────────────────
# Every consumer (review.py, images.py, publish.py) MUST discover drafts through
# iter_drafts(). A bare `QUEUE_DIR.glob("*.md")` is unsafe for two reasons:
#   1. pathlib's glob matches DOTFILES (unlike glob.glob or the shell), so
#      review.py's own `.review-report.md` — written into this very directory —
#      came back as a "draft", sorted FIRST (0x2E < 0x30), and blew up the
#      front-matter parser on every run once it had been committed.
#   2. Any README, template, or hand-added note dropped in the queue would do
#      the same. Content folders attract stray markdown; the pipeline must not
#      be one stray file away from a hard failure.
DRAFT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_(story|value)\.md$")


def is_draft_filename(name: str) -> bool:
    return bool(DRAFT_RE.match(name))


def iter_drafts(directory: Path) -> list[Path]:
    """Every real draft in `directory`, sorted by name.

    Only files matching the canonical `YYYY-MM-DD_(story|value).md` shape count.
    Reports, READMEs, templates, dotfiles and anything else are ignored rather
    than being fed to the front-matter parser."""
    if not directory.exists():
        return []
    return sorted(p for p in directory.glob("*.md") if DRAFT_RE.match(p.name))


# ── draft front-matter ───────────────────────────────────────────────────────
# `image_headline` is written by review.py's proofread pass for value posts. It
# MUST be listed here — _render_frontmatter only emits keys in this list, so a
# missing entry means publish.py's update_frontmatter_text() would silently drop
# the headline while stamping media_id/published_at.
_FRONTMATTER_KEYS = ["type", "post_date", "media_type", "status", "image_headline",
                     "style_override", "media_id", "published_at"]


def _yaml_line(key: str, val) -> str:
    """Render one front-matter line as valid YAML.

    Free-text values (notably `image_headline`) routinely contain ':' — writing
    them bare as `key: Flooring: it matters.` makes the whole block unparseable.
    Dumping a single-key mapping lets PyYAML decide on quoting for us, and also
    quotes date-like and numeric values so they survive as strings instead of
    being coerced to date/int on the way back in."""
    s = "" if val is None else str(val)
    if not s:
        return f"{key}: "
    return yaml.safe_dump(
        {key: s}, default_flow_style=False, allow_unicode=True, width=10 ** 9
    ).strip()


def _render_frontmatter(fm: dict) -> str:
    lines = ["---"]
    for key in _FRONTMATTER_KEYS:
        lines.append(_yaml_line(key, fm.get(key, "")))
    lines.append("---")
    return "\n".join(lines)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    # Tolerate a UTF-8 BOM and leading blank lines — drafts get hand-edited in
    # PRs and on Windows, and neither should be a hard failure.
    text = text.lstrip("\ufeff").lstrip()
    if not text.startswith("---"):
        raise ValueError("Draft is missing YAML front-matter.")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not m:
        raise ValueError("Draft front-matter is malformed (no closing '---').")
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Draft front-matter is not valid YAML: {exc}") from exc
    if not isinstance(fm, dict):
        raise ValueError("Draft front-matter did not parse to a mapping.")
    return fm, m.group(2)


def is_wellformed_draft_text(text: str) -> bool:
    """True if `text` would survive parse_draft's front-matter step. Used to vet
    model-rewritten drafts BEFORE overwriting a known-good file on disk."""
    try:
        _parse_frontmatter(text)
        return True
    except ValueError:
        return False


def update_frontmatter_text(text: str, updates: dict) -> str:
    """Return the draft text with its front-matter fields updated, body intact.
    Used when moving a published post (status/media_id/published_at)."""
    fm, body = _parse_frontmatter(text)
    fm.update(updates)
    return _render_frontmatter(fm) + "\n" + body


# ── draft rendering (generation writes these) ────────────────────────────────
def render_story_markdown(fm: dict, hook: str, slides: list[dict],
                          caption: str, hashtags: list[str], alt_texts: list[str]) -> str:
    parts = [_render_frontmatter(fm), "", f"# Story Day — {fm['post_date']}", "",
             "## Hook", hook.strip(), "", "## Slides", ""]
    for i, slide in enumerate(slides, start=1):
        parts += [f"### Slide {i}",
                  f"**On-image text:** {slide['text'].strip()}",
                  f"**Image prompt:** {slide['image_prompt'].strip()}", ""]
    parts += ["## Caption", caption.strip(), "",
              "## Hashtags", " ".join(_normalize_hashtags(hashtags)), "",
              "## Alt text"]
    for i, alt in enumerate(alt_texts, start=1):
        parts.append(f"- Slide {i}: {alt.strip()}")
    parts.append("")
    return "\n".join(parts)


def render_value_markdown(fm: dict, image_prompt: str, caption: str,
                          prompt_payload: str, hashtags: list[str], alt_text: str) -> str:
    parts = [_render_frontmatter(fm), "", f"# Value Day — {fm['post_date']}", "",
             "## Image prompt", image_prompt.strip(), "",
             "## Caption", caption.strip(), "",
             "## Prompt payload (ManyChat DM)", prompt_payload.strip(), "",
             "## Hashtags", " ".join(_normalize_hashtags(hashtags)), "",
             "## Alt text", f"- {alt_text.strip()}", ""]
    return "\n".join(parts)


def _normalize_hashtags(tags: list[str]) -> list[str]:
    out = []
    for t in tags:
        t = t.strip().lstrip("#").lower()
        if t:
            out.append("#" + re.sub(r"\s+", "", t))
    return out


# ── draft parsing (images.py + publish.py read these) ────────────────────────
def _split_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    for chunk in re.split(r"(?m)^##\s+", body)[1:]:
        head, _, content = chunk.partition("\n")
        sections[head.strip()] = content.strip("\n").strip()
    return sections


def _extract_field(chunk: str, label: str) -> str | None:
    m = re.search(rf"\*\*{re.escape(label)}:\*\*\s*(.+)", chunk)
    return m.group(1).strip() if m else None


def _parse_slides(block: str) -> list[dict]:
    slides = []
    for chunk in re.split(r"(?m)^###\s+Slide.*$", block):
        chunk = chunk.strip()
        if not chunk:
            continue
        text = _extract_field(chunk, "On-image text")
        prompt = _extract_field(chunk, "Image prompt")
        if text is None and prompt is None:
            continue
        slides.append({"text": text or "", "image_prompt": prompt or ""})
    return slides


def _parse_alt(block: str) -> list[str]:
    alts = []
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("- "):
            alts.append(re.sub(r"^Slide\s*\d+\s*:\s*", "", line[2:].strip()))
    return alts


def parse_draft(path: Path) -> dict:
    """Parse a queued/published draft .md into a structured dict. Used by both
    image generation and publishing so they read exactly what Jack approved."""
    text = path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)
    sections = _split_sections(body)

    # YAML coerces bare values: a date-like post_date becomes a date, a numeric
    # media_id (real IG ids are 17–19 digit ints) becomes an int, a timestamp
    # becomes a datetime. Coerce every front-matter field back to a clean string.
    def _s(v) -> str:
        return "" if v is None else str(v).strip()

    post = {
        "path": path,
        "type": _s(fm.get("type")),
        "post_date": _s(fm.get("post_date")),
        "media_type": _s(fm.get("media_type")),
        "status": _s(fm.get("status")),
        "image_headline": _s(fm.get("image_headline")),
        "style_override": _s(fm.get("style_override")),
        "media_id": _s(fm.get("media_id")),
        "published_at": _s(fm.get("published_at")),
        "hashtags": sections.get("Hashtags", "").split(),
        "alt_texts": _parse_alt(sections.get("Alt text", "")),
        "_frontmatter": fm,
    }

    if post["type"] == "story":
        post["hook"] = sections.get("Hook", "")
        post["slides"] = _parse_slides(sections.get("Slides", ""))
        post["caption"] = sections.get("Caption", "")
        post["image_files"] = story_image_names(post["post_date"], len(post["slides"]))
    elif post["type"] == "value":
        post["image_prompt"] = sections.get("Image prompt", "")
        post["caption"] = sections.get("Caption", "")
        post["prompt_payload"] = sections.get("Prompt payload (ManyChat DM)", "")
        post["image_files"] = [value_image_name(post["post_date"])]

    return post


def caption_with_hashtags(post: dict) -> str:
    tags = " ".join(_normalize_hashtags(post.get("hashtags", [])))
    caption = post.get("caption", "").strip()
    return f"{caption}\n\n{tags}".strip() if tags else caption


def validate_draft(post: dict) -> list[str]:
    """Return a list of problems. Empty list == valid. Callers fail LOUDLY on any."""
    cfg = load_config()["content"]
    errors: list[str] = []

    if post["type"] not in {"story", "value"}:
        errors.append(f"unknown type {post['type']!r}")
        return errors

    if not re.match(r"^\d{4}-\d{2}-\d{2}$", post["post_date"]):
        errors.append(f"bad post_date {post['post_date']!r}")

    if post["type"] == "story":
        n = len(post.get("slides", []))
        if not (cfg["story_slides_min"] <= n <= cfg["story_slides_max"]):
            errors.append(f"story has {n} slides, need {cfg['story_slides_min']}–{cfg['story_slides_max']}")
        for i, s in enumerate(post.get("slides", []), 1):
            if not s["image_prompt"]:
                errors.append(f"slide {i} missing image prompt")
        if len(post.get("alt_texts", [])) != n:
            errors.append(f"{len(post.get('alt_texts', []))} alt texts for {n} slides")
        if post.get("media_type") != "carousel":
            errors.append("story media_type should be 'carousel'")
    else:  # value
        if not post.get("image_prompt"):
            errors.append("value post missing image prompt")
        if not post.get("prompt_payload"):
            errors.append("value post missing prompt payload")
        cta = cfg["value_cta"].strip().lower()
        if cta not in post.get("caption", "").strip().lower():
            errors.append(f"value caption must end with the CTA: {cfg['value_cta']!r}")
        if len(post.get("alt_texts", [])) != 1:
            errors.append("value post needs exactly 1 alt text")
        if post.get("media_type") != "image":
            errors.append("value media_type should be 'image'")

    n_tags = len(post.get("hashtags", []))
    if not (cfg["hashtags_min"] <= n_tags <= cfg["hashtags_max"]):
        errors.append(f"{n_tags} hashtags, need {cfg['hashtags_min']}–{cfg['hashtags_max']}")

    return errors
