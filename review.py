"""
review.py — the proofreading gate. Runs AFTER generate.py and BEFORE images.py
(so corrected words are what get stamped on the slides) and before the PR email
ever reaches Carson & Jack.

Two layers:

1. STRUCTURAL CHECKS (deterministic, no API):
   - every slide's on-image text is a complete sentence (terminal punctuation,
     balanced quotes/parens, no truncation artifacts like a trailing "…" or "-")
   - alt-text count matches slide count; hashtags are well-formed
   - value drafts carry a `image_headline:` front-matter line (a complete,
     ≤80-char sentence used on the value image — never a chopped caption)

2. CLAUDE PROOFREAD (auto-fix): the full draft is sent to Claude, which returns
   the corrected markdown — spelling, grammar, incomplete sentences fixed,
   structure and front-matter preserved, and `image_headline` added for value
   posts if missing. Changes are written back to the draft and summarized in
   content/queue/.review-report.md (shown in the PR).

Exit codes: 0 = clean or auto-fixed · 1 = structural problem that could not be
fixed — the workflow fails LOUDLY and no review PR/email goes out with errors.

Usage: python review.py            (all queue drafts)
       python review.py <draft.md> (one draft, e.g. local testing)
"""
from __future__ import annotations

import re
import sys

import common

log = common.get_logger("review")

TERMINALS = (".", "!", "?", '."', '!"', '?"', ":", "—")
REPORT = common.QUEUE_DIR / ".review-report.md"


# ---------------------------------------------------------------- structural
def _sentence_problems(label: str, text: str) -> list[str]:
    t = " ".join(text.split())
    problems = []
    if not t:
        problems.append(f"{label}: empty text")
        return problems
    if t.endswith(("…", "...")):
        problems.append(f"{label}: ends with an ellipsis — looks cut off")
    elif t.endswith("-"):
        problems.append(f"{label}: ends mid-word (trailing hyphen)")
    elif not t.endswith(TERMINALS):
        problems.append(f"{label}: not a complete sentence (no closing punctuation): “{t[-40:]}”")
    if t.count('"') % 2 == 1:
        problems.append(f"{label}: unbalanced quote")
    if t.count("(") != t.count(")"):
        problems.append(f"{label}: unbalanced parenthesis")
    return problems


def structural_check(post: dict) -> list[str]:
    problems: list[str] = []
    if post["type"] == "story":
        for i, s in enumerate(post["slides"], 1):
            problems += _sentence_problems(f"slide {i}", s["text"])
        if post["alt_texts"] and len(post["alt_texts"]) != len(post["slides"]):
            problems.append(
                f"alt-text count ({len(post['alt_texts'])}) != slide count ({len(post['slides'])})")
        problems += _sentence_problems("hook", post.get("hook", ""))
    elif post["type"] == "value":
        if not post.get("prompt_payload", "").strip():
            problems.append("value draft: empty ManyChat prompt payload")
        head = post["_frontmatter"].get("image_headline", "")
        if not str(head).strip():
            problems.append("value draft: missing image_headline front-matter (auto-fixable)")
        else:
            problems += _sentence_problems("image_headline", str(head))
            if len(str(head)) > 80:
                problems.append("image_headline exceeds 80 chars")
    problems += _sentence_problems("caption (last line)",
                                   post.get("caption", "").strip().split("\n")[-1])
    for tag in post.get("hashtags", []):
        if not re.fullmatch(r"#\w+", tag):
            problems.append(f"malformed hashtag: {tag}")
    return problems


# ---------------------------------------------------------------- Claude fix
_SYSTEM = """You are the final proofreader for Strumode's Instagram posts.
You receive one post draft as markdown with YAML front-matter. Return the FULL
corrected draft between <corrected> and </corrected> tags and NOTHING else.

Rules:
- Fix ONLY: spelling, grammar, punctuation, incomplete or cut-off sentences,
  duplicated words, malformed hashtags. Keep the author's voice and meaning.
- Every on-image text, hook, caption paragraph, and alt text must be a complete
  sentence with closing punctuation. American English.
- Preserve the exact markdown structure, section names, ordering, and every
  front-matter field. Do not add or remove slides. Do not change dates/ids.
- If type is value and front-matter lacks `image_headline`, add it after the
  `media_type` line: a complete, punchy, ≤80-character sentence summarizing the
  post for the image (not a truncation of anything).
- If nothing needs fixing, return the draft unchanged."""


def claude_fix(raw: str) -> str | None:
    try:
        import anthropic
        cfg = common.load_config()
        model = cfg.get("models", {}).get("content_model", "claude-sonnet-5")
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=model, max_tokens=4000, system=_SYSTEM,
            messages=[{"role": "user", "content": raw}])
        out = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        m = re.search(r"<corrected>\n?(.*?)\n?</corrected>", out, re.S)
        return m.group(1) if m else None
    except Exception as e:  # noqa: BLE001 — proofread must degrade, not crash
        log.warning("Claude proofread unavailable (%s) — structural checks only.", e)
        return None


def _diff_summary(before: str, after: str) -> list[str]:
    b_lines = before.splitlines()
    a_lines = after.splitlines()
    changes = []
    for i, (b, a) in enumerate(zip(b_lines, a_lines), 1):
        if b != a:
            changes.append(f"  line {i}: “{b.strip()[:70]}” → “{a.strip()[:70]}”")
    if len(b_lines) != len(a_lines):
        changes.append(f"  (line count {len(b_lines)} → {len(a_lines)})")
    return changes


# ------------------------------------------------------------------- driver
def review_draft(path) -> tuple[bool, list[str]]:
    """Returns (ok, report_lines). ok=False → unfixable structural problem."""
    report = [f"### {path.name}"]
    raw = path.read_text(encoding="utf-8")
    post = common.parse_draft(path)
    problems = structural_check(post)

    fixed = claude_fix(raw)
    if fixed and fixed.strip() != raw.strip():
        path.write_text(fixed if fixed.endswith("\n") else fixed + "\n",
                        encoding="utf-8")
        changes = _diff_summary(raw, fixed)
        report.append(f"- ✏️ auto-fixed {len(changes)} line(s):")
        report += changes[:20]
        post = common.parse_draft(path)          # re-check after fixes
        problems = structural_check(post)
    elif fixed is not None:
        report.append("- ✅ Claude proofread: no changes needed")

    remaining = [p for p in problems if "auto-fixable" not in p]
    if remaining:
        report.append("- ❌ UNRESOLVED problems:")
        report += [f"  - {p}" for p in remaining]
        return False, report
    report.append("- ✅ structure clean: complete sentences, balanced punctuation")
    return True, report


def main() -> int:
    if common.is_paused():
        log.info("Agent is paused — skipping review.")
        return 0
    single = common.Path(sys.argv[1]) if len(sys.argv) > 1 else None
    drafts = [single] if single else sorted(common.QUEUE_DIR.glob("*.md"))
    if not drafts:
        log.info("No drafts to review.")
        return 0
    all_ok, lines = True, ["## Proofread report (review.py)"]
    for path in drafts:
        ok, rep = review_draft(path)
        all_ok &= ok
        lines += rep
        for line in rep:
            log.info(line)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not all_ok:
        log.error("Review FAILED — fix the problems above; no PR should ship with errors.")
        return 1
    log.info("Review passed — drafts are clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
