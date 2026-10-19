"""
Цифры 1–4 в стиле дудлов кнопок — для галерей выбора (тип живой картинки,
стиль): номер на коллаже и та же цифра на кнопке.

Запуск из корня проекта:
    python3 scripts/render_numbers.py

Рисует 128×128:
- panel_art/emoji/ui/ui_num1…4.png   — набор doodle (по умолчанию);
- panel_art/emoji/ui3d/u3_num1…4.png — объёмный набор;
- panel_art/badges/num1…4.png (256×256) — номера для коллажей.
"""

import pathlib

from PIL import Image, ImageDraw, ImageFont

import art3d
from art3d import ICE, INK, LIME, MILK, PINK, S, small

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = ROOT / "fonts" / "banner" / "Dunkin-Bold.otf"
COLORS = {1: LIME, 2: PINK, 3: ICE, 4: MILK}


def digit_mask(n):
    """Цифра по центру холста, крупно (маска 512×512)."""
    mask = Image.new("L", (S, S), 0)
    draw = ImageDraw.Draw(mask)
    font = ImageFont.truetype(str(FONT), int(S * 0.78))
    box = draw.textbbox((0, 0), str(n), font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    draw.text(((S - w) / 2 - box[0] - S * 0.03, (S - h) / 2 - box[1] - S * 0.03), str(n), font=font, fill=255)
    return mask


def digit_inner(n, scale=0.62):
    """Цифра поменьше — по центру кружка."""
    small_digit = digit_mask(n).resize((int(S * scale), int(S * scale)), Image.LANCZOS)
    canvas = Image.new("L", (S, S), 0)
    canvas.paste(small_digit, ((S - small_digit.width) // 2 - int(S * 0.03),
                               (S - small_digit.height) // 2 - int(S * 0.03)))
    return canvas


def badge(n, flat=False):
    """Круглая плашка с цифрой: цветной кружок, тёмная цифра, толстая обводка."""
    disc = art3d.circle(0.47, 0.47, 0.40)
    return art3d.render([(disc, COLORS[n])], [(digit_inner(n), INK)], outline=INK, flat=flat)


def main():
    ui = ROOT / "panel_art" / "emoji" / "ui"
    ui3d = ROOT / "panel_art" / "emoji" / "ui3d"
    badges = ROOT / "panel_art" / "badges"
    for folder in (ui, ui3d, badges):
        folder.mkdir(parents=True, exist_ok=True)
    for n in COLORS:
        small(badge(n, flat=True)).save(ui / f"ui_num{n}.png", optimize=True)
        small(badge(n)).save(ui3d / f"u3_num{n}.png", optimize=True)
        small(badge(n), 256).save(badges / f"num{n}.png", optimize=True)
    print("цифры 1–4: ui, ui3d, badges")


if __name__ == "__main__":
    main()
