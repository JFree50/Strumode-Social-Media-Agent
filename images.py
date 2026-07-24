"""
images.py — Friday, right after generate.py. Turns each draft's subject-only
image prompt into a finished JPEG (1080x1350, 4:5) saved next to the draft in
content/queue/. Every image is composed from the master sketch-style block in
prompts/image_style.md unless the draft carries a Jack-set `style_override:`.

gpt-image-1 only emits 1024x1024 / 1024x1536 / 1536x1024, so we generate portrait
and center-crop + resize to Instagram's 4:5. Pillow re-encodes to JPEG, which is
the format Instagram's publishing API requires.

TEXT IS CODE-STAMPED, NEVER MODEL-DRAWN: the art is generated wordless, then each
story slide's exact "On-image text" is overlaid programmatically (Pillow) in a
hand-drawn-style font — so spelling is guaranteed. The Value Day image gets no
overlay (its words live in the caption). The overlay font is fetched once at
runtime (Google Fonts, OFL license) and cached in assets/fonts/.

Idempotent: skips any image file that already exists (no re-spend on re-runs).
Usage: `python images.py [path/to/draft.md]` — with a path, processes just that
draft and bypasses the schedule guard (handy for regen/testing).
"""
from __future__ import annotations

import base64
import io
import sys

import requests
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont

import common

log = common.get_logger("images")

FONT_DIR = common.REPO_ROOT / "assets" / "fonts"
FONT_FILE = FONT_DIR / "PatrickHand-Regular.ttf"
FONT_URL = ("https://raw.githubusercontent.com/google/fonts/main/"
            "ofl/patrickhand/PatrickHand-Regular.ttf")


def _hex_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _ensure_font() -> str | None:
    """Return a usable TTF path: repo-cached first, else download, else None."""
    if FONT_FILE.exists():
        return str(FONT_FILE)
    try:
        FONT_DIR.mkdir(parents=True, exist_ok=True)
        r = requests.get(FONT_URL, timeout=30)
        r.raise_for_status()
        FONT_FILE.write_bytes(r.content)
        log.info("Downloaded overlay font to %s", FONT_FILE)
        return str(FONT_FILE)
    except Exception as e:  # noqa: BLE001 — overlay must degrade, not crash the run
        log.warning("Font download failed (%s) — falling back to PIL default.", e)
        return None


def _wrap(draw: ImageDraw.ImageDraw, words: list[str], font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


CREAM = (250, 246, 237)


def _overlay_text(img: Image.Image, text: str, img_cfg: dict) -> Image.Image:
    """Compose the slide as a card: cream headline band on top, art below.

    The band height is FIXED at H - W (270px at 1080x1350), which makes the art
    area exactly square — so square-generated art fits with ZERO cropping. The
    font auto-shrinks to fit the band (never the other way around), the words
    never cover the art, and code never misspells.
    """
    text = " ".join(text.split())
    if not text:
        return img
    W, H = img.size
    band_h = H - W                       # art area below is exactly W x W (square)
    margin = int(W * 0.07)
    max_w = W - 2 * margin
    navy = _hex_rgb(img_cfg.get("overlay_color", "#1B3A6B"))
    gold = _hex_rgb(img_cfg.get("overlay_accent", "#C8A24B"))
    font_path = _ensure_font()

    canvas = Image.new("RGB", (W, H), CREAM)
    draw = ImageDraw.Draw(canvas)

    pad = int(band_h * 0.14)             # top air
    rule_room = int(band_h * 0.16)       # gold rule + bottom air
    text_room = band_h - pad - rule_room

    # Largest font whose wrapped block fits the band's text room in <= 3 lines.
    lines, font, line_h = [text], None, 0
    for size in range(int(band_h / 2.2), 17, -3):
        font = (ImageFont.truetype(font_path, size) if font_path
                else ImageFont.load_default(size=size))
        lines = _wrap(draw, text.split(), font, max_w)
        ascent, descent = font.getmetrics()
        line_h = int((ascent + descent) * 1.08)
        if (len(lines) <= 3
                and line_h * len(lines) <= text_room
                and all(draw.textlength(l, font=font) <= max_w for l in lines)):
            break

    # Headline, vertically centered in the text room.
    y = pad + max(0, (text_room - line_h * len(lines)) // 2)
    for line in lines:
        lw = draw.textlength(line, font=font)
        draw.text(((W - lw) / 2, y), line, font=font, fill=navy)
        y += line_h
    # Gold accent rule separating headline from art.
    ux = (W - int(W * 0.22)) / 2
    ry = band_h - int(rule_room * 0.55)
    draw.line([ux, ry, W - ux, ry], fill=gold, width=max(4, int(H * 0.006)))

    # Art fills the exact square below the band — zero crop for square art.
    canvas.paste(_crop_resize(img, W, H - band_h), (0, band_h))
    return canvas


def _style_block() -> str:
    return (common.PROMPTS_DIR / "image_style.md").read_text(encoding="utf-8")


def _compose(subject: str, style_override: str) -> str:
    style = style_override.strip() if style_override.strip() else _style_block()
    return f"{style}\n\nSUBJECT TO DRAW: {subject.strip()}"


def _crop_resize(img: Image.Image, out_w: int, out_h: int) -> Image.Image:
    target = out_w / out_h
    w, h = img.size
    if w / h > target:                      # too wide → crop the sides
        new_w = int(round(h * target))
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:                                    # too tall → crop top/bottom
        new_h = int(round(w / target))
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    return img.resize((out_w, out_h), Image.LANCZOS)


def _generate_one(client: OpenAI, prompt: str, out_path, img_cfg: dict,
                  overlay: str = "") -> None:
    # Banded slides get square art (the area under the band is ~square, so the
    # crop loses almost nothing); full-bleed images generate portrait.
    size = img_cfg["gen_size_banded"] if overlay else img_cfg["gen_size_full"]
    kwargs = dict(model=img_cfg["image_model"], prompt=prompt,
                  size=size, quality=img_cfg["quality"], n=1)
    try:
        resp = client.images.generate(output_format="jpeg", **kwargs)
    except TypeError:
        # Older openai SDK without output_format — Pillow re-encodes to JPEG anyway.
        resp = client.images.generate(**kwargs)
    raw = base64.b64decode(resp.data[0].b64_json)
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img = _crop_resize(img, img_cfg["out_width"], img_cfg["out_height"])
    if overlay and img_cfg.get("overlay_text", True):
        img = _overlay_text(img, overlay, img_cfg)
    img.save(out_path, format="JPEG", quality=90)
    log.info("Saved %s%s", out_path.name, "  (text stamped)" if overlay else "")


def _img_config() -> dict:
    cfg = common.load_config()
    return {"image_model": cfg["models"]["image_model"],
            "gen_size_banded": cfg["image"].get("gen_size_banded", "1024x1024"),
            "gen_size_full": cfg["image"].get("gen_size_full",
                                              cfg["image"].get("gen_size", "1024x1536")),
            "quality": cfg["image"]["quality"],
            "out_width": cfg["image"]["out_width"],
            "out_height": cfg["image"]["out_height"],
            "overlay_text": cfg["image"].get("overlay_text", True),
            "overlay_color": cfg["image"].get("overlay_color",
                                              cfg["brand"]["navy"]),
            "overlay_accent": cfg["image"].get("overlay_accent",
                                               cfg["brand"]["gold"])}


def process_draft_template(path, img_cfg: dict) -> int:
    """Brand-template mode (default since 7/23/2026, approved by Carson + Jack):
    every slide is rendered deterministically by slide_template.py — the white
    browser-window design. No image model, no cost, no drift."""
    import slide_template
    post = common.parse_draft(path)
    if post["type"] == "story":
        renders = slide_template.render_story_slides(post)
    elif post["type"] == "value":
        renders = [slide_template.render_value_slide(post)]
    else:
        log.warning("Skipping %s — unknown type.", path.name)
        return 0
    made = 0
    for img, fname in zip(renders, post["image_files"]):
        out_path = path.parent / fname
        if out_path.exists():
            log.info("Exists, skipping %s", fname)
            continue
        img.save(out_path, format="JPEG", quality=92)
        log.info("Saved %s  (brand template)", fname)
        made += 1
    return made


def process_draft(path, client: OpenAI, img_cfg: dict) -> int:
    post = common.parse_draft(path)
    if post["type"] == "story":
        prompts = [s["image_prompt"] for s in post["slides"]]
        overlays = [s["text"] for s in post["slides"]]      # exact words, code-stamped
    elif post["type"] == "value":
        prompts = [post["image_prompt"]]
        overlays = [""]                                     # value image stays wordless
    else:
        log.warning("Skipping %s — unknown type.", path.name)
        return 0
    made = 0
    for subject, overlay, fname in zip(prompts, overlays, post["image_files"]):
        out_path = path.parent / fname
        if out_path.exists():
            log.info("Exists, skipping %s", fname)
            continue
        _generate_one(client, _compose(subject, post["style_override"]), out_path,
                      img_cfg, overlay=overlay)
        made += 1
    return made


def main() -> int:
    if common.is_paused():
        log.info("Agent is paused — skipping image generation.")
        return 0

    # Single-draft mode (path arg) bypasses the schedule guard for regen/testing.
    single = None
    if len(sys.argv) > 1:
        single = common.Path(sys.argv[1])
    elif not common.should_run("Friday", 9, log):
        return 0

    img_cfg = _img_config()
    drafts = [single] if single else sorted(common.QUEUE_DIR.glob("*.md"))
    if not drafts:
        log.info("No drafts in queue — nothing to illustrate.")
        return 0

    # "template" (default) = deterministic brand renderer, no API cost.
    # "paint" = legacy gpt-image pipeline, kept for one-off art experiments.
    mode = common.load_config()["image"].get("mode", "template")
    client = OpenAI() if mode == "paint" else None

    total = 0
    for path in drafts:
        if mode == "paint":
            total += process_draft(path, client, img_cfg)
        else:
            total += process_draft_template(path, img_cfg)
    log.info("Generated %d image(s) [%s mode].", total, mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
