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


def _overlay_text(img: Image.Image, text: str, img_cfg: dict) -> Image.Image:
    """Stamp the slide's exact text onto the top of the image. Code never misspells."""
    text = " ".join(text.split())
    if not text:
        return img
    W, H = img.size
    margin = int(W * 0.07)
    max_w = W - 2 * margin
    navy = _hex_rgb(img_cfg.get("overlay_color", "#1B3A6B"))
    gold = _hex_rgb(img_cfg.get("overlay_accent", "#C8A24B"))
    font_path = _ensure_font()

    base = img.convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # Largest font size (from H/9 down) that fits the width in <= 4 lines.
    lines, font = [text], None
    for size in range(int(H / 9), int(H / 26), -4):
        font = (ImageFont.truetype(font_path, size) if font_path
                else ImageFont.load_default(size=size))
        lines = _wrap(draw, text.split(), font, max_w)
        if len(lines) <= 4 and all(draw.textlength(l, font=font) <= max_w for l in lines):
            break

    ascent, descent = font.getmetrics()
    line_h = int((ascent + descent) * 1.12)
    block_h = line_h * len(lines)
    pad = int(W * 0.035)
    top = margin

    # Soft cream "paper" panel behind the words so they read over any art.
    draw.rounded_rectangle(
        [margin - pad, top - pad, W - margin + pad, top + block_h + pad],
        radius=int(pad * 1.2), fill=(250, 246, 237, 225),
        outline=navy + (90,), width=3)

    y = top
    for line in lines:
        lw = draw.textlength(line, font=font)
        draw.text(((W - lw) / 2, y), line, font=font, fill=navy + (255,))
        y += line_h
    # Gold accent underline beneath the block — the brand's spark.
    ux = (W - min(int(W * 0.22), max_w)) / 2
    draw.line([ux, y + int(pad * 0.4), W - ux, y + int(pad * 0.4)],
              fill=gold + (255,), width=max(4, int(H * 0.006)))

    return Image.alpha_composite(base, layer).convert("RGB")


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
    kwargs = dict(model=img_cfg["image_model"], prompt=prompt,
                  size=img_cfg["gen_size"], quality=img_cfg["quality"], n=1)
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
            "gen_size": cfg["image"]["gen_size"],
            "quality": cfg["image"]["quality"],
            "out_width": cfg["image"]["out_width"],
            "out_height": cfg["image"]["out_height"],
            "overlay_text": cfg["image"].get("overlay_text", True),
            "overlay_color": cfg["image"].get("overlay_color",
                                              cfg["brand"]["navy"]),
            "overlay_accent": cfg["image"].get("overlay_accent",
                                               cfg["brand"]["gold"])}


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

    client = OpenAI()
    img_cfg = _img_config()
    drafts = [single] if single else sorted(common.QUEUE_DIR.glob("*.md"))
    if not drafts:
        log.info("No drafts in queue — nothing to illustrate.")
        return 0

    total = 0
    for path in drafts:
        total += process_draft(path, client, img_cfg)
    log.info("Generated %d image(s).", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
