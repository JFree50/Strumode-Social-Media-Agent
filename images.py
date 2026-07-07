"""
images.py — Friday, right after generate.py. Turns each draft's subject-only
image prompt into a finished JPEG (1080x1350, 4:5) saved next to the draft in
content/queue/. Every image is composed from the master sketch-style block in
prompts/image_style.md unless the draft carries a Jack-set `style_override:`.

gpt-image-1 only emits 1024x1024 / 1024x1536 / 1536x1024, so we generate portrait
and center-crop + resize to Instagram's 4:5. Pillow re-encodes to JPEG, which is
the format Instagram's publishing API requires.

Idempotent: skips any image file that already exists (no re-spend on re-runs).
Usage: `python images.py [path/to/draft.md]` — with a path, processes just that
draft and bypasses the schedule guard (handy for regen/testing).
"""
from __future__ import annotations

import base64
import io
import sys

from openai import OpenAI
from PIL import Image

import common

log = common.get_logger("images")


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


def _generate_one(client: OpenAI, prompt: str, out_path, img_cfg: dict) -> None:
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
    img.save(out_path, format="JPEG", quality=90)
    log.info("Saved %s", out_path.name)


def _img_config() -> dict:
    cfg = common.load_config()
    return {"image_model": cfg["models"]["image_model"],
            "gen_size": cfg["image"]["gen_size"],
            "quality": cfg["image"]["quality"],
            "out_width": cfg["image"]["out_width"],
            "out_height": cfg["image"]["out_height"]}


def process_draft(path, client: OpenAI, img_cfg: dict) -> int:
    post = common.parse_draft(path)
    if post["type"] == "story":
        prompts = [s["image_prompt"] for s in post["slides"]]
    elif post["type"] == "value":
        prompts = [post["image_prompt"]]
    else:
        log.warning("Skipping %s — unknown type.", path.name)
        return 0
    made = 0
    for subject, fname in zip(prompts, post["image_files"]):
        out_path = path.parent / fname
        if out_path.exists():
            log.info("Exists, skipping %s", fname)
            continue
        _generate_one(client, _compose(subject, post["style_override"]), out_path, img_cfg)
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
