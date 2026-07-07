# Strumode — master image style block

This is the PERMANENT default visual identity for every generated post image. `images.py`
prepends this block to each post's subject-only image prompt before calling gpt-image-1. Jack
edits this file to change the look for ALL future posts. A single post can override it only if
that post's draft file has a non-empty `style_override:` field (set by Jack) — the agent never
decides to deviate on its own.

---

STYLE (apply to every image):
Hand-drawn **sketch illustration** — loose ink and pencil line-art on warm off-white paper,
with a friendly notebook-doodle energy, as if a sharp founder sketched it in a grid notebook.
Confident but imperfect linework. Light cross-hatching for shading. Generous negative space.
Feels hand-made and human, never slick or corporate.

COLOR (restrained, brand accents only):
- Linework: navy #1B3A6B.
- Highlights / one hero accent per image: gold #C8A24B.
- Deep-navy #002452 and electric-blue #2E8BFF used sparingly, for a single small accent only.
- Background: warm off-white / cream paper. Keep the palette calm — mostly ink on cream with
  gold as the spark.

COMPOSITION:
- One clear focal idea per image. Simple, uncluttered, phone-legible at a glance.
- 4:5 portrait framing (the output is cropped to 1080x1350).
- Leave breathing room; don't fill every corner.
- The slide's headline is placed in its own cream band ABOVE the art after
  generation (the art is never covered). Compose the full frame freely with the
  main subject centered; the frame is later cropped a bit shorter, so keep
  crucial detail away from the extreme top and bottom edges.

HARD CONSTRAINTS (never violate):
- NEVER photorealistic. No photography, no 3D renders, no glossy vector/corporate-flat art.
- No real brand logos, no real people or likenesses, no trademarked characters.
- ABSOLUTELY NO text of any kind: no words, letters, numbers, labels, signage, or
  lettering anywhere in the image. All wording is added programmatically after
  generation (guaranteed spelling) — the art must be completely wordless.
- Nothing offensive, political, or off-brand.

END OF STYLE BLOCK.
The specific subject to draw in this style follows below.
