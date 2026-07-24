"""
slide_template.py — the Strumode slide, as code.

The approved brand frame (Carson + Jack, 7/23/2026): every post is "a little
screen" — a white browser window that CONTAINS the artwork, with the words
stamped ABOVE the window (headline, ink with the final line in gold) and the
handle/hook BELOW it. Space Mono labels, paper background, whisper dot grid.

The frame, type, and layout are deterministic Pillow — pixel-identical every
week, spelling guaranteed. Only the art inside the window comes from the image
model (images.py). If art is missing, the screen falls back to a quiet branded
pattern so a render can never fail the pipeline.

Slide kinds:
  cover — kicker + headline above, art in window, swipe cue below
  body  — slide-counter kicker, headline above, art in window
  final — headline above, art in window w/ terminal status bar, follow cue
  value — Thursday value post: headline above, art + terminal bar in window

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
DOT = (235, 237, 242)
INK = (17, 32, 58)
SLATE = (122, 134, 155)
GOLD = (192, 152, 66)
GREEN = (64, 152, 112)
NAVY = (10, 15, 26)

_FDIR = common.REPO_ROOT / "assets" / "fonts"

MARGIN = 84                       # page side margin = window x bounds
WIN_BOTTOM = 1148                 # window bottom edge
FOOTER_Y = 1224                   # handle line
CHROME_H = 70                     # window title bar height
TERMBAR_H = 64                    # terminal status bar (final/value slides)


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


def _crop_cover(img: Image.Image, out_w: int, out_h: int) -> Image.Image:
    """Center-crop `img` to fill out_w x out_h exactly."""
    target = out_w / out_h
    w, h = img.size
    if w / h > target:
        new_w = int(round(h * target))
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(round(w / target))
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    return img.resize((out_w, out_h), Image.LANCZOS)


def _base() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    for y in range(120, H - 100, 52):                 # whisper dot grid
        for x in range(60, W - 40, 52):
            d.ellipse([x - 1, y - 1, x + 1, y + 1], fill=DOT)
    # crosshair mark + wordmark (top-left)
    cx, cy, arm = 92, 104, 32
    d.line([cx, cy - arm, cx, cy + arm], fill=INK, width=3)
    d.line([cx - arm, cy, cx + arm, cy], fill=INK, width=3)
    sq = 18
    d.rectangle([cx - sq // 2, cy - sq // 2, cx + sq // 2, cy + sq // 2],
                outline=INK, width=3)
    d.text((cx + arm + 16, cy - 12), "STRUMODE", font=monob(21), fill=INK)
    return img, d


def _kicker_right(d: ImageDraw.ImageDraw, text: str) -> None:
    if not text:
        return
    f = mono(19)
    d.text((W - MARGIN - d.textlength(text, font=f), 95), text, font=f, fill=SLATE)


def _headline_above(d: ImageDraw.ImageDraw, text: str) -> int:
    """Headline stamped on the paper, above the window. Ink; final wrapped line
    gold. Auto-sizes 64→38px, max 3 lines. Returns y below the block."""
    x = MARGIN
    max_w = W - 2 * MARGIN
    lines, f, line_h = [text], None, 0
    for size in range(64, 33, -3):
        f = clash_sb(size)
        lines = _wrap(d, text, f, max_w)
        ascent, descent = f.getmetrics()
        line_h = int((ascent + descent) * 1.0)
        if len(lines) <= 3:
            break
    yy = 172
    for i, line in enumerate(lines):
        color = GOLD if (len(lines) > 1 and i == len(lines) - 1) else INK
        d.text((x, yy), line, font=f, fill=color)
        yy += line_h
    return yy + 26


def _window_with_art(img: Image.Image, win_top: int, art: Image.Image | None,
                     url: str = "strumode.com", termbar: str | None = None) -> None:
    """The little screen: chrome bar + the artwork filling the content area.
    Optional mono terminal status bar pinned to the window bottom."""
    wx0, wy0, wx1, wy1 = MARGIN, win_top, W - MARGIN, WIN_BOTTOM
    d = ImageDraw.Draw(img)
    # soft shadow
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.rounded_rectangle([wx0, wy0 + 14, wx1, wy1 + 20], 28, fill=(17, 32, 58, 20))
    sh = sh.filter(ImageFilter.GaussianBlur(12))
    img.paste(sh, (0, 0), sh)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([wx0, wy0, wx1, wy1], 26, fill=CARD, outline=LINEC, width=2)
    # chrome
    d.line([wx0 + 1, wy0 + CHROME_H, wx1 - 1, wy0 + CHROME_H], fill=LINEC, width=2)
    for cxx in (wx0 + 40, wx0 + 70, wx0 + 100):
        d.ellipse([cxx - 8, wy0 + 27, cxx + 8, wy0 + 43],
                  outline=(214, 217, 225), width=2)
    fu = mono(20)
    pw = max(280, int(d.textlength(url, font=fu)) + 64)
    d.rounded_rectangle([(wx0 + wx1) // 2 - pw // 2, wy0 + 16,
                         (wx0 + wx1) // 2 + pw // 2, wy0 + 54], 19,
                        fill=(246, 247, 250))
    d.text(((W - d.textlength(url, font=fu)) // 2, wy0 + 25), url,
           font=fu, fill=SLATE)
    # content area
    ax0, ay0, ax1 = wx0 + 2, wy0 + CHROME_H + 1, wx1 - 2
    ay1 = wy1 - 2 - (TERMBAR_H if termbar else 0)
    aw, ah = ax1 - ax0, ay1 - ay0
    if art is not None:
        pane = _crop_cover(art.convert("RGB"), aw, ah)
    else:                                   # branded fallback — never fail a render
        pane = Image.new("RGB", (aw, ah), (246, 248, 250))
        pd = ImageDraw.Draw(pane)
        for y in range(30, ah, 46):
            for x in range(30, aw, 46):
                pd.ellipse([x - 1, y - 1, x + 1, y + 1], fill=(228, 232, 238))
        pd.text((36, ah - 64), "strumode> loading…", font=mono(24), fill=SLATE)
    # rounded bottom corners when the art reaches the window bottom
    mask = Image.new("L", (aw, ah), 255)
    if not termbar:
        md = ImageDraw.Draw(Image.new("L", (aw, ah), 0))
        rmask = Image.new("L", (aw, ah), 0)
        ImageDraw.Draw(rmask).rounded_rectangle([0, -60, aw, ah], 24, fill=255)
        mask = rmask
    img.paste(pane, (ax0, ay0), mask)
    d = ImageDraw.Draw(img)
    if termbar:
        ty0 = wy1 - 2 - TERMBAR_H
        d.rectangle([ax0, ty0, ax1, wy1 - 26], fill=CARD)
        d.rounded_rectangle([wx0, wy0, wx1, wy1], 26, outline=LINEC, width=2)
        d.line([ax0 + 30, ty0 + 6, ax1 - 30, ty0 + 6], fill=LINEC, width=2)
        d.text((ax0 + 34, ty0 + 20), f"strumode> {termbar}", font=mono(23), fill=INK)
        okx = ax0 + 34 + d.textlength(f"strumode> {termbar}  ", font=mono(23))
        d.text((okx, ty0 + 20), "ok", font=monob(23), fill=GREEN)


def _footer(d: ImageDraw.ImageDraw, cue: str = "") -> None:
    d.text((MARGIN, FOOTER_Y),
           "@strumode · free weekly AI playbook for business owners",
           font=mono(23), fill=SLATE)
    if cue:
        f = clash_md(30)
        cw = d.textlength(cue, font=f)
        ax1 = W - MARGIN
        d.text((ax1 - cw - 74, FOOTER_Y - 8), cue, font=f, fill=INK)
        ay = FOOTER_Y + 10
        d.line([ax1 - 62, ay, ax1 - 20, ay], fill=GOLD, width=4)
        d.polygon([(ax1 - 20, ay - 8), (ax1 - 5, ay), (ax1 - 20, ay + 8)], fill=GOLD)


def render_slide(kind: str, text: str, *, art: Image.Image | None = None,
                 kicker: str = "", url: str = "strumode.com",
                 terminal_cmd: str = "run business --hands-off") -> Image.Image:
    """Render one slide: words above the little screen, art inside it."""
    text = " ".join(text.split())
    img, d = _base()
    _kicker_right(d, kicker.upper())
    win_top = _headline_above(d, text)
    win_top = max(win_top, 330)          # never let a short headline float the window too high
    termbar = terminal_cmd if kind in ("final", "value") else None
    _window_with_art(img, win_top, art, url=url, termbar=termbar)
    d = ImageDraw.Draw(img)
    if kind == "cover":
        _footer(d, cue="Swipe")
    elif kind == "final":
        _footer(d, cue="Follow")
    else:
        _footer(d)
    return img


def render_story_slides(post: dict, arts: list[Image.Image | None]) -> list[Image.Image]:
    """All slides for a story-day carousel. `arts` aligns with post['slides']."""
    slides = post["slides"]
    n = len(slides)
    out = []
    for i, s in enumerate(slides):
        art = arts[i] if i < len(arts) else None
        if i == 0:
            out.append(render_slide("cover", s["text"], art=art,
                                    kicker="A Strumode story"))
        elif i == n - 1:
            out.append(render_slide("final", s["text"], art=art,
                                    kicker=f"{i + 1:02d} / {n:02d}"))
        else:
            out.append(render_slide("body", s["text"], art=art,
                                    kicker=f"{i + 1:02d} / {n:02d}"))
    return out


def render_value_slide(post: dict, art: Image.Image | None) -> Image.Image:
    """Thursday value-day image. Headline = first sentence of the caption."""
    caption = post.get("caption", "").strip()
    first = caption.split("\n")[0].strip()
    for stop in (". ", "? ", "! "):
        if stop in first:
            first = first.split(stop)[0] + stop.strip()
            break
    if len(first) > 90:
        first = first[:87].rstrip() + "…"
    return render_slide("value", first or "This week's free prompt is here.",
                        art=art, kicker="Value day / the free prompt",
                        terminal_cmd="send this week's prompt")
