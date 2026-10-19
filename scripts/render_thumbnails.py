"""
Иконки панелей (thumbnail) 256×256 — фирменные смайлики в 3D.

Запуск из корня проекта:
    python3 scripts/render_thumbnails.py

Рисует panel_art/thumbnail.*.png. Девять — 3D-версии оригиналов (у каждой
команды свой смайлик, как в макете), три новые — для /library, /interactive
и /eminemovna: смайлик с предметом команды.
"""

import pathlib
import sys

from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import art3d  # noqa: E402
import render_smileys as rs  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "panel_art"
SIZE = 256

# ключ -> (смайлик, значок поверх или None)
THUMBNAILS = {
    "default": ("smile", None),
    "embed": ("wait", None),
    "forms": ("cool", None),
    "access": ("snot", None),
    "buttons": ("monocle", None),
    "templates": ("cracked", None),
    "webhooks": ("kiss", None),
    "select": ("angel", None),
    "logo": ("cry", None),
    "library": ("stars", ("doodle3d/d3_book.png", 0.72, 0.74, 0.42)),
    "interactive": ("wink", ("ui3d/u3_button.png", 0.72, 0.76, 0.44)),
    "eminemovna": ("smile_lime", ("doodle3d/d3_crown.png", 0.5, 0.13, 0.4)),
}


def thumbnail(name, badge):
    img = rs.smiley(name)
    if badge:
        path, cx, cy, size = badge
        icon = Image.open(ROOT / "panel_art" / "emoji" / path).convert("RGBA")
        icon = icon.resize((int(art3d.S * size), int(art3d.S * size)), Image.LANCZOS)
        img = img.copy()
        img.alpha_composite(icon, (int(art3d.S * cx - icon.width / 2), int(art3d.S * cy - icon.height / 2)))
    # обрезать прозрачные поля, чтобы смайлик был крупнее на 80×80
    box = img.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    img = img.crop(box)
    side = max(img.width, img.height)
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.alpha_composite(img, ((side - img.width) // 2, (side - img.height) // 2))
    return out.resize((SIZE, SIZE), Image.LANCZOS)


def main():
    for key, (name, badge) in THUMBNAILS.items():
        path = OUT_DIR / f"thumbnail.{key}.png"
        thumbnail(name, badge).save(path, optimize=True)
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
