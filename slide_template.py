"""
slide_template.py — the Strumode slide, as code.

Renders every post image in the approved brand template (Carson + Jack,
7/23/2026): a white browser window ("a little screen") on paper, Clash Display
headline in ink with the final line in gold, Space Mono labels, and the
terminal sign-off. Deterministic Pillow rendering — the design cannot drift,
words cannot be misspelled, and no image model is involved.

Slide kinds:
  cover — first carousel slide: kicker, headline, swipe hook below the window
  body  — middle slides: slide counter kicker, headline only
  final — last slide: headline + terminal "ok" line + follow hook
  value — Thursday value post: headline + terminal line (words live in caption)

Fonts are vendored in assets/fonts/ (see FONTS_LICENSE.md).
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import common

# ---- canvas + palette (from the approved sample) ---------------------------
W, H = 1080, 1350
PAPER = (250, 251, 253)
CARD = (255, 255, 255)
LINEC = (233, 235, 240)
FAINT = (226, 229, 236)
DOT = (235, 237, 242)
INK = (17, 32, 58)
SLATE = (122, 134, 155)
GOLD = (192, 152, 66)
GREEN = (64, 152, 112)

_FDIR = common.REPO_ROOT / "assets" / "fonts"


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_FDIR / name), size)


def clash_sb(s: int): return _font("ClashDisplay-Semibold.otf", s)
def clash_md(s: int): return _font("ClashDisplay-Medium.otf", s)
def mono(s: int): return _font("SpaceMono-Regular.ttf", s)
def monob(s: int): return _font("SpaceMono-Bold.ttf", s)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _base() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    for y in range(120, H - 100, 52):                 # whisper dot grid
        for x in range(60, W - 40, 52):
            d.ellipse([x - 1, y - 1, x + 1, y + 1], fill=DOT)
    # crosshair mark + wordmark (top-left)
    cx, cy, arm = 92, 108, 34
    d.line([cx, cy - arm, cx, cy + arm], fill=INK, width=3)
    d.line([cx - arm, cy, cx + arm, cy], fill=INK, width=3)
    sq = 19
    d.rectangle([cx - sq // 2, cy - sq // 2, cx + sq // 2, cy + sq // 2],
                outline=INK, width=3)
    d.text((cx + arm + 16, cy - 13), "STRUMODE", font=monob(22), fill=INK)
    return img, d


def _kicker_right(d: ImageDraw.ImageDraw, text: str) -> None:
    f = mono(20)
    d.text((W - 84 - d.textlength(text, font=f), 98), text, font=f, fill=SLATE)


def _window(img: Image.Image, d: ImageDraw.ImageDraw,
            url: str = "strumode.com") -> tuple[int, int, int, int]:
    wx0, wy0, wx1, wy1 = 84, 226, W - 84, 1064
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.rounded_rectangle([wx0, wy0 + 14, wx1, wy1 + 20], 28, fill=(17, 32, 58, 18))
    sh = sh.filter(ImageFilter.GaussianBlur(12))
    img.paste(sh, (0, 0), sh)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([wx0, wy0, wx1, wy1], 26, fill=CARD, outline=LINEC, width=2)
    d.line([wx0 + 1, wy0 + 70, wx1 - 1, wy0 + 70], fill=LINEC, width=2)
    for cxx in (wx0 + 40, wx0 + 70, wx0 + 100):
        d.ellipse([cxx - 8, wy0 + 27, cxx + 8, wy0 + 43], outline=(214, 217, 225), width=2)
    pw = max(300, int(ImageDraw.Draw(img).textlength(url, font=mono(21))) + 70)
    d.rounded_rectangle([(wx0 + wx1) // 2 - pw // 2, wy0 + 16,
                         (wx0 + wx1) // 2 + pw // 2, wy0 + 54], 19, fill=(246, 247, 250))
    d.text(((W - ImageDraw.Draw(img).textlength(url, font=mono(21))) // 2, wy0 + 24),
           url, font=mono(21), fill=SLATE)
    return wx0, wy0 + 70, wx1, wy1


def _headline_block(d: ImageDraw.ImageDraw, text: str, box: tuple[int, int, int, int],
                    reserve_bottom: int = 0) -> int:
    """Draw the auto-sized headline: ink, final wrapped line gold. Returns the
    y coordinate just below the block."""
    x0, y0, x1, y1 = box
    pad = 64
    max_w = (x1 - x0) - 2 * pad
    room = (y1 - y0) - 150 - reserve_bottom      # 150 = kicker zone + air
    lines, f, line_h = [text], None, 0
    for size in range(86, 35, -4):
        f = clash_sb(size)
        lines = _wrap(d, text, f, max_w)
        ascent, descent = f.getmetrics()
        line_h = int((ascent + descent) * 1.02)
        if len(lines) <= 6 and line_h * len(lines) <= room:
            break
    yy = y0 + 130 + max(0, (room - line_h * len(lines)) // 3)
    for i, line in enumerate(lines):
        color = GOLD if (len(lines) > 1 and i == len(lines) - 1) else INK
        d.text((x0 + pad, yy), line, font=f, fill=color)
        yy += line_h
    return yy


def _terminal_line(d: ImageDraw.ImageDraw, box, y: int,
                   cmd: str = "run business --hands-off") -> None:
    x0, _, x1, _ = box
    pad = 64
    d.line([x0 + pad, y, x1 - pad, y], fill=FAINT, width=2)
    d.text((x0 + pad, y + 24), f"strumode> {cmd}", font=mono(25), fill=INK)
    okx = x0 + pad + d.textlength(f"strumode> {cmd}  ", font=mono(25))
    d.text((okx, y + 24), "ok", font=monob(25), fill=GREEN)


def _footer(d: ImageDraw.ImageDraw, hook: str = "", handle_line: str | None = None) -> None:
    if hook:
        f = clash_md(37)
        hook_lines = _wrap(d, hook, f, W - 84 - 180)
        hook_txt = hook_lines[0] + ("…" if len(hook_lines) > 1 else "")
        d.text((84, 1120), hook_txt, font=f, fill=INK)
        aw = d.textlength(hook_txt, font=f)
        ax, ay = int(84 + aw + 24), 1120 + 24
        d.line([ax, ay, ax + 46, ay], fill=GOLD, width=4)
        d.polygon([(ax + 46, ay - 9), (ax + 63, ay), (ax + 46, ay + 9)], fill=GOLD)
    if handle_line is None:
        handle_line = "@strumode · free weekly AI playbook for business owners"
    d.text((84, 1206), handle_line, font=mono(24), fill=SLATE)


def render_slide(kind: str, text: str, *, kicker: str = "",
                 hook: str = "", url: str = "strumode.com",
                 terminal_cmd: str = "run business --hands-off") -> Image.Image:
    """Render one slide in the approved template. `text` is the exact on-image
    copy (code-stamped — spelling guaranteed)."""
    text = " ".join(text.split())
    img, d = _base()
    _kicker_right(d, kicker.upper() if kind != "cover" else "")
    box = _window(img, d, url=url)
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    pad = 64
    if kind == "cover":
        d.text((x0 + pad, y0 + 62), (kicker or "A STRUMODE STORY").upper(),
               font=mono(24), fill=GOLD)
        _headline_block(d, text, box)
        _footer(d, hook=hook or "Swipe")
    elif kind == "body":
        d.text((x0 + pad, y0 + 62), (kicker or "").upper(), font=mono(24), fill=GOLD)
        _headline_block(d, text, box)
        _footer(d)
    elif kind == "final":
        d.text((x0 + pad, y0 + 62), (kicker or "").upper(), font=mono(24), fill=GOLD)
        yy = _headline_block(d, text, box, reserve_bottom=110)
        _terminal_line(d, box, min(yy + 60, y1 - 110), cmd=terminal_cmd)
        _footer(d, hook=hook or "Follow for the weekly playbook")
    elif kind == "value":
        d.text((x0 + pad, y0 + 62), (kicker or "VALUE DAY / THE FREE PROMPT").upper(),
               font=mono(24), fill=GOLD)
        yy = _headline_block(d, text, box, reserve_bottom=110)
        _terminal_line(d, box, min(yy + 60, y1 - 110), cmd="send this week's prompt")
        _footer(d, hook="Prompt's in the caption — grab it")
    else:  # pragma: no cover
        raise ValueError(f"unknown slide kind: {kind}")
    return img


def render_story_slides(post: dict) -> list[Image.Image]:
    """All slides for a story-day carousel, from the parsed draft."""
    slides = post["slides"]
    n = len(slides)
    date_label = post.get("post_date", "")
    out = []
    for i, s in enumerate(slides):
        counter = f"{i + 1:02d} / {n:02d}"
        if i == 0:
            out.append(render_slide("cover", s["text"],
                                    kicker="A Strumode story",
                                    hook=post.get("hook", "") or "Swipe"))
        elif i == n - 1:
            out.append(render_slide("final", s["text"], kicker=counter))
        else:
            out.append(render_slide("body", s["text"], kicker=counter))
    return out


def render_value_slide(post: dict) -> Image.Image:
    """The Thursday value-day image. Headline = first sentence of the caption."""
    caption = post.get("caption", "").strip()
    first = caption.split("\n")[0].strip()
    for stop in (". ", "? ", "! "):
        if stop in first:
            first = first.split(stop)[0] + stop.strip()
            break
    if len(first) > 110:
        first = first[:107].rstrip() + "…"
    return render_slide("value", first or "This week's free prompt is here.")
