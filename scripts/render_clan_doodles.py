"""
Новые дудлы про кланы — в стиле дудлов форм (толстая обводка, фирменная
палитра) и сразу в двух видах: плоский, как оригиналы Эмилии, и 3D, как
scripts/render_doodles3d.py.

Запуск из корня проекта:
    python3 scripts/render_clan_doodles.py

Рисует 256×256:
- panel_art/emoji/doodle_clan_<имя>.png        — плоские;
- panel_art/emoji/doodle3d/d3_clan_<имя>.png   — 3D;
- panel_art/purr/_clan_doodles.png            — лист для просмотра.
"""

import math
import pathlib

from PIL import Image, ImageDraw, ImageFont

import art3d
from art3d import (ICE, INK, LIME, MAROON, MILK, PINK, STONE, arc_line, circle, dark, ell, inter, light, line, minus,
                   poly, rot, rr, star_pts, union)

ROOT = pathlib.Path(__file__).resolve().parent.parent
FLAT_DIR = ROOT / "panel_art" / "emoji"
D3_DIR = FLAT_DIR / "doodle3d"
SIZE = 256
GOLD = LIME          # «золото» кланов — фирменный лайм
WOOD = (122, 60, 52)


def text_mask(text, cx, cy, size):
    """Цифры/буквы маской (доли холста), жирным Inter."""
    m = art3d.M()
    f = ImageFont.truetype(str(ROOT / "fonts" / "Inter-Bold.otf"), int(size * art3d.S))
    ImageDraw.Draw(m).text((cx * art3d.S, cy * art3d.S), text, font=f, fill=255, anchor="mm")
    return m


# ---------- предметы: (объёмные части, рисунок поверх) ----------

def chest():
    lid = inter(union(rr(0.1, 0.26, 0.84, 0.48, 0.04), ell(0.1, 0.1, 0.84, 0.44)), rr(0, 0, 1, 0.48, 0))
    body = rr(0.1, 0.44, 0.84, 0.84, 0.05)
    bands = union(rr(0.22, 0.12, 0.3, 0.84, 0.01), rr(0.64, 0.12, 0.72, 0.84, 0.01))
    lock = rr(0.39, 0.38, 0.55, 0.58, 0.03)
    hole = union(circle(0.47, 0.45, 0.025), rr(0.455, 0.46, 0.485, 0.53, 0.01))
    return [(body, MAROON), (lid, light(MAROON, 0.15))], [(bands, GOLD), (lock, GOLD), (hole, INK)]


def coin():
    rim = minus(circle(0.47, 0.47, 0.3), circle(0.47, 0.47, 0.25))
    return [(circle(0.47, 0.47, 0.37), GOLD)], [(rim, dark(GOLD, 0.8)), (poly(star_pts(0.47, 0.47, 0.15), 0.004), MILK)]


def coins():
    parts, details = [], []
    for i, y in enumerate((0.62, 0.48, 0.34)):
        parts.append((union(ell(0.14, y, 0.8, y + 0.2), rr(0.14, y + 0.1, 0.8, y + 0.2, 0)), GOLD))
        details.append((arc_line(0.47, y + 0.1, 0.33, 15, 165, 0.018, ry=0.1), dark(GOLD, 0.75)))
    top = ell(0.14, 0.2, 0.8, 0.4)
    parts.append((top, light(GOLD, 0.2)))
    details.append((poly(star_pts(0.47, 0.3, 0.08), 0.003), MILK))
    return parts, details


def sack():
    body = union(circle(0.47, 0.6, 0.3), poly([(0.32, 0.3), (0.62, 0.3), (0.74, 0.52), (0.2, 0.52)], 0.02))
    top = poly([(0.3, 0.14), (0.47, 0.22), (0.64, 0.14), (0.6, 0.3), (0.34, 0.3)], 0.015)
    tie = rr(0.3, 0.26, 0.64, 0.34, 0.03)
    badge = circle(0.47, 0.62, 0.13)
    return [(top, MILK), (body, MILK), (tie, MAROON)], [(badge, GOLD), (poly(star_pts(0.47, 0.62, 0.07), 0.003), MILK)]


def swords():
    def sword(flip):
        pts = [(0.16, 0.14), (0.64, 0.62)]
        guard = [(0.54, 0.74), (0.76, 0.52)]
        grip = [(0.66, 0.64), (0.8, 0.78)]
        if flip:
            pts, guard, grip = ([(0.94 - x, y) for x, y in p] for p in (pts, guard, grip))
        return line(pts, 0.085), line(guard, 0.065), line(grip, 0.075), circle(grip[1][0], grip[1][1], 0.055)
    a, b = sword(False), sword(True)
    return ([(a[0], ICE), (b[0], ICE), (union(a[1], b[1]), MAROON), (union(a[2], b[2]), INK),
             (union(a[3], b[3]), GOLD)], [])


def castle():
    def tower(x0, x1):
        mid = (x0 + x1) / 2
        return union(rr(x0, 0.3, x1, 0.86, 0.02), rr(x0, 0.2, x0 + 0.06, 0.32, 0.01), rr(x1 - 0.06, 0.2, x1, 0.32, 0.01),
                     rr(mid - 0.03, 0.2, mid + 0.03, 0.32, 0.01))
    wall = union(rr(0.2, 0.44, 0.74, 0.86, 0.02), *(rr(x, 0.38, x + 0.06, 0.46, 0.01) for x in (0.32, 0.44, 0.56)))
    gate = union(rr(0.38, 0.62, 0.56, 0.86, 0), circle(0.47, 0.62, 0.09))
    flag = poly([(0.48, 0.04), (0.66, 0.1), (0.48, 0.16)], 0.004)
    return ([(line([(0.47, 0.05), (0.47, 0.4)], 0.025), INK), (flag, PINK), (wall, MILK), (tower(0.06, 0.3), MILK),
             (tower(0.64, 0.88), MILK)], [(gate, MAROON), (union(rr(0.15, 0.48, 0.21, 0.58, 0.02), rr(0.73, 0.48, 0.79, 0.58, 0.02)), INK)])


def pennant():
    cloth = poly([(0.22, 0.14), (0.72, 0.14), (0.72, 0.86), (0.47, 0.7), (0.22, 0.86)], 0.01)
    bar = line([(0.14, 0.13), (0.8, 0.13)], 0.05)
    return [(cloth, MAROON), (bar, INK), (union(circle(0.14, 0.13, 0.04), circle(0.8, 0.13, 0.04)), GOLD)], [
        (poly(star_pts(0.47, 0.42, 0.13), 0.004), GOLD), (rr(0.28, 0.2, 0.66, 0.24, 0.01), PINK)]


def crest():
    sh = poly([(0.47, 0.18), (0.82, 0.28), (0.78, 0.58), (0.47, 0.9), (0.16, 0.58), (0.12, 0.28)], 0.03)
    crown = poly([(0.28, 0.24), (0.27, 0.06), (0.37, 0.14), (0.47, 0.03), (0.57, 0.14), (0.67, 0.06), (0.66, 0.24)], 0.01)
    half = inter(sh, rr(0.47, 0, 1, 1, 0))
    return [(crown, GOLD), (sh, PINK)], [(half, MAROON), (line([(0.47, 0.2), (0.47, 0.88)], 0.03), INK),
                                         (poly(star_pts(0.32, 0.48, 0.09), 0.003), MILK)]


def helmet():
    dome = union(circle(0.47, 0.46, 0.3), rr(0.17, 0.46, 0.77, 0.84, 0.06))
    plume = poly([(0.47, 0.18), (0.56, 0.02), (0.72, 0.04), (0.6, 0.2)], 0.03)
    slits = union(rr(0.24, 0.48, 0.7, 0.54, 0.02), *(rr(x, 0.6, x + 0.04, 0.78, 0.01) for x in (0.32, 0.42, 0.52, 0.62)))
    return [(plume, PINK), (dome, STONE)], [(slits, INK), (rr(0.44, 0.16, 0.5, 0.48, 0.02), light(STONE, 0.4))]


def scroll():
    paper = rr(0.16, 0.2, 0.78, 0.8, 0.02)
    lines_ = union(*(rr(0.26, y, 0.6, y + 0.04, 0.02) for y in (0.32, 0.44, 0.56)))
    check = line([(0.56, 0.66), (0.62, 0.72), (0.74, 0.58)], 0.04)
    return [(paper, MILK), (rr(0.1, 0.12, 0.84, 0.26, 0.07), PINK), (rr(0.1, 0.74, 0.84, 0.88, 0.07), PINK)], [
        (lines_, INK), (check, MAROON)]


def tag():
    t = poly([(0.06, 0.28), (0.64, 0.28), (0.88, 0.5), (0.64, 0.72), (0.06, 0.72)], 0.03)
    return [(t, GOLD)], [(circle(0.7, 0.5, 0.05), INK), (text_mask("#", 0.34, 0.5, 0.36), INK)]


def podium():
    return [(rr(0.06, 0.48, 0.34, 0.86, 0.03), PINK), (rr(0.6, 0.58, 0.88, 0.86, 0.03), ICE),
            (rr(0.32, 0.3, 0.62, 0.86, 0.03), GOLD)], [
        (text_mask("1", 0.47, 0.5, 0.26), INK), (text_mask("2", 0.2, 0.66, 0.2), INK), (text_mask("3", 0.74, 0.72, 0.18), INK),
        (poly(star_pts(0.47, 0.16, 0.1), 0.004), MILK)]


def torch():
    outer = poly([(0.47, 0.04), (0.56, 0.18), (0.62, 0.12), (0.7, 0.32), (0.66, 0.46), (0.28, 0.46), (0.24, 0.32),
                  (0.32, 0.2), (0.36, 0.28)], 0.03)
    inner = poly([(0.47, 0.22), (0.56, 0.34), (0.54, 0.44), (0.4, 0.44), (0.38, 0.34)], 0.02)
    handle = poly([(0.36, 0.5), (0.58, 0.5), (0.52, 0.9), (0.42, 0.9)], 0.01)
    return [(handle, WOOD), (rr(0.26, 0.42, 0.68, 0.54, 0.03), GOLD), (outer, MAROON)], [(inner, PINK)]


def calendar30():
    return [(rr(0.1, 0.16, 0.84, 0.86, 0.07), MILK), (union(rr(0.28, 0.06, 0.34, 0.22, 0.03), rr(0.6, 0.06, 0.66, 0.22, 0.03)), STONE)], [
        (inter(rr(0.1, 0.16, 0.84, 0.86, 0.07), rr(0, 0, 1, 0.36, 0)), MAROON), (text_mask("30", 0.47, 0.6, 0.34), INK)]


def ledger():
    return [(rr(0.68, 0.12, 0.82, 0.86, 0.02), MILK), (rr(0.14, 0.08, 0.74, 0.88, 0.05), MAROON)], [
        (rr(0.2, 0.08, 0.26, 0.88, 0.01), dark(MAROON, 0.75)), (circle(0.47, 0.42, 0.15), GOLD),
        (poly(star_pts(0.47, 0.42, 0.08), 0.003), MILK), (rr(0.34, 0.66, 0.62, 0.71, 0.02), PINK)]


def respect():
    petals = union(*(circle(0.47 + 0.24 * math.cos(math.radians(a)), 0.4 + 0.24 * math.sin(math.radians(a)), 0.1)
                     for a in range(0, 360, 30)), circle(0.47, 0.4, 0.26))
    tails = union(poly([(0.34, 0.56), (0.46, 0.6), (0.36, 0.92), (0.28, 0.84), (0.22, 0.88)], 0.005),
                  poly([(0.6, 0.56), (0.48, 0.6), (0.58, 0.92), (0.66, 0.84), (0.72, 0.88)], 0.005))
    return [(tails, MAROON), (petals, PINK), (circle(0.47, 0.4, 0.16), GOLD)], [(poly(star_pts(0.47, 0.4, 0.09), 0.003), MILK)]


DOODLES = {
    "chest": chest, "coin": coin, "coins": coins, "sack": sack, "swords": swords, "castle": castle,
    "pennant": pennant, "crest": crest, "helmet": helmet, "scroll": scroll, "tag": tag, "podium": podium,
    "torch": torch, "calendar": calendar30, "ledger": ledger, "respect": respect,
}
TITLES = {
    "chest": "сундук казны", "coin": "монета", "coins": "стопка монет", "sack": "мешок голды", "swords": "клановая война",
    "castle": "замок клана", "pennant": "знамя", "crest": "герб", "helmet": "шлем", "scroll": "свиток квеста",
    "tag": "тег клана", "podium": "биллборд", "torch": "огонёк", "calendar": "продление 30 дней",
    "ledger": "журнал казны", "respect": "респект",
}


def draw(name, flat):
    parts, details = DOODLES[name]()
    img = art3d.render(parts, details, outline=INK, flat=flat)
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main():
    D3_DIR.mkdir(parents=True, exist_ok=True)
    cell = 170
    sheet = Image.new("RGBA", (len(DOODLES) * cell + 20, 2 * cell + 70), (58, 40, 50, 255))
    d = ImageDraw.Draw(sheet)
    f = ImageFont.truetype(str(ROOT / "fonts" / "Inter-SemiBold.otf"), 15)
    for i, name in enumerate(DOODLES):
        flat, solid = draw(name, True), draw(name, False)
        flat.save(FLAT_DIR / f"doodle_clan_{name}.png", optimize=True)
        solid.save(D3_DIR / f"d3_clan_{name}.png", optimize=True)
        x = 10 + i * cell
        sheet.alpha_composite(flat.resize((150, 150), Image.LANCZOS), (x + 10, 10))
        sheet.alpha_composite(solid.resize((150, 150), Image.LANCZOS), (x + 10, cell + 10))
        d.text((x + cell / 2, 2 * cell + 30), TITLES[name], font=f, fill=MILK, anchor="mm")
    sheet.save(FLAT_DIR.parent / "purr" / "_clan_doodles.png", optimize=True)
    print(f"{len(DOODLES)} дудлов × 2 вида")


if __name__ == "__main__":
    main()
