"""
Эмодзи кнопок в стиле объёмных иконок: плоская сторона, наклон в
перспективе и толстый тёмный бок (как на референсе), фирменные цвета.

Запуск из корня проекта:
    python3 scripts/render_ui3d.py

Рисует panel_art/emoji/ui3d/u3_*.png, 128×128 — по одной на каждый
panel_art/emoji/ui/ui_*.png (имена после префикса совпадают).
"""

import math
import pathlib

from art3d import (INK, LIME, MAROON, MAUVE, MILK, PINK, ICE, STONE, arc_line, circle, dark, ell, heart_pts,
                   inter, light, line, minus, mirror, poly, rot, rr, small, soft, star_pts, union)
import art3d

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "panel_art" / "emoji" / "ui3d"

# старые имена цветов -> фирменная палитра
YEL = PINK
ORA = MAUVE
RED = MAROON
GRN = LIME
MINT = LIME
TEAL = ICE
BLUE = (150, 186, 214)
SKY = light(ICE, 0.4)
SLATE = (118, 104, 116)
LIL = PINK
CREAM = MILK
WHITE = MILK
WOOD = MAUVE
GREY = STONE
DARK = INK
GOLD = LIME


def render(parts, details=()):
    return small(art3d.render(parts, details, outline=INK))


# ---------- иконки ----------

def u3_add():
    return render([(union(rr(0.35, 0.09, 0.59, 0.81, 0.08), rr(0.11, 0.33, 0.83, 0.57, 0.08)), GRN)])


def u3_remove():
    return render([(rr(0.11, 0.33, 0.83, 0.57, 0.08), RED)])


def u3_confirm():
    return render([(line([(0.14, 0.46), (0.37, 0.69), (0.8, 0.2)], 0.2), GRN)])


def u3_cancel():
    return render([(union(line([(0.17, 0.15), (0.77, 0.75)], 0.2), line([(0.77, 0.15), (0.17, 0.75)], 0.2)), RED)])


def _arrow():
    return union(rr(0.08, 0.34, 0.52, 0.56, 0.05), poly([(0.4, 0.08), (0.86, 0.45), (0.4, 0.82)], 0.025))


def u3_next():
    return render([(_arrow(), TEAL)])


def u3_prev():
    return render([(mirror(_arrow()), TEAL)])


def u3_back():
    tail = union(line([(0.34, 0.27), (0.52, 0.27)], 0.17), arc_line(0.52, 0.47, 0.2, -90, 90, 0.17),
                 line([(0.52, 0.67), (0.2, 0.67)], 0.17))
    head = poly([(0.06, 0.27), (0.38, 0.03), (0.38, 0.51)], 0.02)
    return render([(union(tail, head), ICE)])


def u3_play():
    return render([(rr(0.08, 0.14, 0.84, 0.76, 0.12), BLUE)],
                  [(poly([(0.36, 0.28), (0.62, 0.45), (0.36, 0.62)], 0.015), INK)])


def u3_delete():
    body = poly([(0.18, 0.28), (0.76, 0.28), (0.7, 0.86), (0.24, 0.86)], 0.025)
    lid = union(rr(0.1, 0.16, 0.84, 0.3, 0.05), rr(0.34, 0.06, 0.6, 0.22, 0.05))
    grooves = union(*(line([(x, 0.4), (x + (0.47 - x) * 0.08, 0.74)], 0.05) for x in (0.34, 0.47, 0.6)))
    return render([(body, RED), (lid, light(MAROON, 0.18))], [(grooves, dark(RED, 0.82))])


def u3_edit():
    parts = [
        (poly([(0.22, 0.38), (0.05, 0.47), (0.22, 0.56)], 0.01), CREAM),
        (rr(0.21, 0.38, 0.68, 0.56, 0.02), YEL),
        (rr(0.66, 0.38, 0.75, 0.56, 0.01), GREY),
        (rr(0.73, 0.38, 0.9, 0.56, 0.06), PINK),
    ]
    details = [(poly([(0.11, 0.44), (0.05, 0.47), (0.11, 0.5)]), DARK), (rr(0.24, 0.43, 0.65, 0.47, 0.02), light(YEL, 0.5))]
    return render([(rot(m, 40, 0.48, 0.48), col) for m, col in parts],
                  [(rot(m, 40, 0.48, 0.48), col) for m, col in details])


def u3_folder():
    back = union(rr(0.07, 0.18, 0.85, 0.8, 0.06), rr(0.07, 0.1, 0.4, 0.26, 0.05))
    paper = rr(0.17, 0.2, 0.77, 0.62, 0.03)
    front = poly([(0.07, 0.36), (0.87, 0.36), (0.83, 0.82), (0.11, 0.82)], 0.03)
    return render([(back, dark(YEL, 0.88)), (paper, WHITE), (front, YEL)])


def u3_image():
    inner = rr(0.15, 0.19, 0.77, 0.71, 0.04)
    hills = inter(poly([(0.15, 0.72), (0.37, 0.4), (0.54, 0.6), (0.63, 0.5), (0.78, 0.72)]), inner)
    return render([(rr(0.07, 0.11, 0.85, 0.79, 0.08), CREAM)],
                  [(inner, SKY), (hills, GRN), (circle(0.64, 0.33, 0.065), YEL)])


def u3_text():
    page = soft(minus(rr(0.15, 0.06, 0.79, 0.84, 0.07), poly([(0.58, -0.1), (0.95, -0.1), (0.95, 0.27)])), 0.01)
    fold = poly([(0.6, 0.07), (0.62, 0.25), (0.77, 0.23)], 0.008)
    lines = union(rr(0.26, 0.36, 0.67, 0.42, 0.03), rr(0.26, 0.5, 0.67, 0.56, 0.03), rr(0.26, 0.64, 0.52, 0.7, 0.03))
    return render([(page, CREAM)], [(fold, dark(MILK, 0.88)), (lines, SLATE)])


def u3_box():
    front = rr(0.1, 0.3, 0.82, 0.84, 0.06)
    lid = rr(0.06, 0.16, 0.86, 0.36, 0.05)
    tape = union(rr(0.38, 0.16, 0.54, 0.36, 0.01), rr(0.38, 0.36, 0.54, 0.56, 0.01))
    return render([(front, MAUVE), (lid, light(MAUVE, 0.25))], [(tape, CREAM)])


def u3_library():
    pages = rr(0.2, 0.12, 0.85, 0.86, 0.06)
    cover = rr(0.11, 0.06, 0.74, 0.8, 0.08)
    mark = poly([(0.5, 0.06), (0.63, 0.06), (0.63, 0.3), (0.565, 0.24), (0.5, 0.3)])
    lines = union(rr(0.23, 0.42, 0.6, 0.49, 0.035), rr(0.23, 0.56, 0.46, 0.63, 0.035))
    return render([(pages, CREAM), (cover, GRN)], [(mark, ORA), (lines, light(GRN, 0.4))])


def u3_roles():
    tag = soft(union(rr(0.2, 0.3, 0.86, 0.64, 0.06), poly([(0.06, 0.47), (0.26, 0.3), (0.26, 0.64)])), 0.015)
    tag = minus(tag, circle(0.24, 0.47, 0.055))
    stripe = rr(0.38, 0.43, 0.74, 0.51, 0.04)
    return render([(rot(tag, 32), LIL)], [(rot(stripe, 32), light(LIL, 0.45))])


def _arc_arrow(cx, cy, r, a0, a1, w):
    body = arc_line(cx, cy, r, a0, a1, w)
    a = math.radians(a1)
    px, py = cx + r * math.cos(a), cy + r * math.sin(a)
    tx, ty = -math.sin(a), math.cos(a)
    nx, ny = math.cos(a), math.sin(a)
    head = poly([(px + tx * 0.15, py + ty * 0.15), (px + nx * 0.13, py + ny * 0.13), (px - nx * 0.13, py - ny * 0.13)], 0.012)
    return union(body, head)


def u3_refresh():
    return render([(union(_arc_arrow(0.47, 0.45, 0.27, 175, 300, 0.13), _arc_arrow(0.47, 0.45, 0.27, -5, 120, 0.13)), MINT)])


def u3_settings():
    m = circle(0.47, 0.46, 0.27)
    for k in range(8):
        a = math.radians(k * 45)
        ca, sa = math.cos(a), math.sin(a)
        pts = [(0.47 + ca * l - sa * w, 0.46 + sa * l + ca * w) for l, w in ((0.15, -0.075), (0.38, -0.06), (0.38, 0.06), (0.15, 0.075))]
        m = union(m, poly(pts))
    return render([(minus(soft(m, 0.012), circle(0.47, 0.46, 0.1)), PINK)])


def u3_clear():
    stick = line([(0.74, 0.07), (0.44, 0.47)], 0.075)
    bristles = poly([(0.34, 0.38), (0.56, 0.54), (0.4, 0.86), (0.06, 0.66)], 0.03)
    band = rot(rr(0.3, 0.38, 0.6, 0.48, 0.03), -37, 0.45, 0.46)
    sparkle = poly(star_pts(0.74, 0.66, 0.11, inner=0.35), 0.006)
    straws = union(line([(0.33, 0.56), (0.24, 0.72)], 0.03), line([(0.43, 0.6), (0.36, 0.76)], 0.03))
    return render([(stick, WOOD), (bristles, YEL), (band, RED), (sparkle, SKY)], [(straws, dark(YEL, 0.82))])


def u3_block():
    ring = minus(circle(0.46, 0.45, 0.37), circle(0.46, 0.45, 0.24))
    bar = line([(0.3, 0.29), (0.62, 0.61)], 0.13)
    return render([(union(ring, bar), RED)])


def u3_mail():
    flap = line([(0.13, 0.26), (0.46, 0.5), (0.79, 0.26)], 0.05)
    heart_m = poly(heart_pts(0.46, 0.5, 0.2))
    return render([(rr(0.06, 0.16, 0.86, 0.76, 0.07), CREAM)], [(flap, dark(MILK, 0.86)), (heart_m, RED)])


def u3_announce():
    handle = rr(0.3, 0.52, 0.42, 0.78, 0.04)
    back = rr(0.08, 0.32, 0.26, 0.58, 0.04)
    cone = poly([(0.22, 0.33), (0.72, 0.1), (0.72, 0.8), (0.22, 0.57)], 0.015)
    rim = rr(0.68, 0.07, 0.83, 0.83, 0.07)
    return render([(handle, DARK), (back, GREY), (cone, ORA), (rim, light(ORA, 0.3))],
                  [(rr(0.4, 0.34, 0.5, 0.56, 0.03), light(ORA, 0.45))])


def u3_list():
    board = rr(0.12, 0.1, 0.8, 0.86, 0.08)
    paper = rr(0.2, 0.2, 0.72, 0.8, 0.04)
    clip = rr(0.31, 0.04, 0.61, 0.18, 0.05)
    dots = union(*(circle(0.3, y, 0.035) for y in (0.36, 0.5, 0.64)))
    lines = union(*(rr(0.38, y - 0.025, 0.63, y + 0.025, 0.025) for y in (0.36, 0.5, 0.64)))
    return render([(board, WOOD), (paper, CREAM), (clip, GREY)], [(dots, TEAL), (lines, SLATE)])


def u3_web():
    globe = circle(0.46, 0.45, 0.37)
    land = union(poly([(0.18, 0.3), (0.36, 0.2), (0.46, 0.34), (0.36, 0.5), (0.22, 0.47)], 0.025),
                 poly([(0.52, 0.52), (0.72, 0.46), (0.8, 0.6), (0.62, 0.78), (0.53, 0.68)], 0.025),
                 ell(0.56, 0.14, 0.7, 0.26))
    return render([(globe, BLUE)], [(inter(land, globe), GRN)])


def u3_star():
    return render([(poly(star_pts(0.47, 0.48, 0.42, inner=0.5), 0.025), GOLD)])


def u3_warn():
    tri = poly([(0.46, 0.06), (0.87, 0.8), (0.05, 0.8)], 0.05)
    mark = union(rr(0.42, 0.3, 0.5, 0.57, 0.04), circle(0.46, 0.67, 0.045))
    return render([(tri, YEL)], [(mark, DARK)])


def u3_lock():
    shackle = union(arc_line(0.47, 0.34, 0.18, 180, 360, 0.09), line([(0.29, 0.34), (0.29, 0.46)], 0.09),
                    line([(0.65, 0.34), (0.65, 0.46)], 0.09))
    body = rr(0.13, 0.4, 0.81, 0.84, 0.08)
    hole = union(circle(0.47, 0.57, 0.06), rr(0.44, 0.58, 0.5, 0.72, 0.03))
    return render([(shackle, GREY), (body, YEL)], [(hole, INK)])


def u3_people():
    back = union(circle(0.62, 0.24, 0.12), rr(0.42, 0.4, 0.84, 0.82, 0.18))
    front = union(circle(0.35, 0.34, 0.14), rr(0.08, 0.53, 0.62, 0.88, 0.2))
    return render([(back, TEAL), (front, LIL)])


def u3_time():
    hands = union(line([(0.46, 0.45), (0.46, 0.26)], 0.055), line([(0.46, 0.45), (0.6, 0.53)], 0.055))
    return render([(circle(0.46, 0.45, 0.38), BLUE)],
                  [(circle(0.46, 0.45, 0.28), CREAM), (hands, DARK), (circle(0.46, 0.45, 0.04), RED)])


def u3_search():
    handle = line([(0.58, 0.58), (0.82, 0.82)], 0.13)
    ring = minus(circle(0.4, 0.4, 0.28), circle(0.4, 0.4, 0.17))
    return render([(handle, DARK), (ring, LIL)],
                  [(circle(0.4, 0.4, 0.17), SKY), (circle(0.34, 0.34, 0.045), WHITE)])


def u3_button():
    pill = rr(0.05, 0.2, 0.8, 0.6, 0.2)
    cursor = poly([(0.48, 0.38), (0.88, 0.6), (0.68, 0.66), (0.58, 0.86)], 0.02)
    return render([(pill, PINK), (cursor, TEAL)], [(rr(0.16, 0.34, 0.44, 0.44, 0.05), light(PINK, 0.5))])


def u3_trophy():
    handles = union(minus(circle(0.2, 0.26, 0.13), circle(0.2, 0.26, 0.065)),
                    minus(circle(0.74, 0.26, 0.13), circle(0.74, 0.26, 0.065)))
    bowl = inter(union(rr(0.2, 0.06, 0.74, 0.3, 0.03), ell(0.2, 0.06, 0.74, 0.58)), rr(0.0, 0.06, 1.0, 1.0, 0))
    stem = rr(0.41, 0.5, 0.53, 0.7, 0.02)
    base = rr(0.25, 0.66, 0.69, 0.84, 0.05)
    star = poly(star_pts(0.47, 0.29, 0.11, inner=0.45), 0.006)
    return render([(handles, dark(GOLD, 0.9)), (stem, dark(GOLD, 0.9)), (bowl, GOLD), (base, WOOD)], [(star, CREAM)])


def u3_palette():
    board = minus(ell(0.06, 0.12, 0.86, 0.8), circle(0.33, 0.6, 0.075))
    blobs = [(circle(0.28, 0.34, 0.07), RED), (circle(0.48, 0.25, 0.07), YEL), (circle(0.67, 0.34, 0.07), BLUE),
             (circle(0.68, 0.56, 0.07), GRN)]
    return render([(board, MILK)], blobs)


def u3_save():
    body = soft(minus(rr(0.08, 0.08, 0.84, 0.84, 0.08), poly([(0.68, 0.0), (0.95, 0.0), (0.95, 0.27)])), 0.01)
    shutter = rr(0.24, 0.08, 0.62, 0.32, 0.03)
    slot = rr(0.5, 0.12, 0.57, 0.28, 0.02)
    label = rr(0.18, 0.47, 0.74, 0.84, 0.04)
    lines = union(rr(0.27, 0.58, 0.65, 0.62, 0.02), rr(0.27, 0.69, 0.56, 0.73, 0.02))
    return render([(body, BLUE)], [(shutter, GREY), (slot, DARK), (label, CREAM), (lines, SLATE)])


def u3_send():
    plane = poly([(0.05, 0.42), (0.87, 0.1), (0.6, 0.84), (0.44, 0.56)], 0.015)
    fold = poly([(0.44, 0.56), (0.87, 0.1), (0.52, 0.76)])
    return render([(plane, TEAL)], [(inter(fold, plane), dark(TEAL, 0.86))])


def u3_preview():
    eye = soft(inter(circle(0.46, 0.92, 0.62), circle(0.46, 0.0, 0.62)), 0.01)
    return render([(eye, WHITE)],
                  [(circle(0.46, 0.46, 0.17), BLUE), (circle(0.46, 0.46, 0.08), DARK), (circle(0.41, 0.41, 0.035), WHITE)])


ICONS = [
    u3_add, u3_announce, u3_back, u3_block, u3_box, u3_button, u3_cancel, u3_clear, u3_confirm, u3_delete,
    u3_edit, u3_folder, u3_image, u3_library, u3_list, u3_lock, u3_mail, u3_next, u3_palette, u3_people,
    u3_play, u3_prev, u3_preview, u3_refresh, u3_remove, u3_roles, u3_save, u3_search, u3_send, u3_settings,
    u3_star, u3_text, u3_time, u3_trophy, u3_warn, u3_web,
]


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for fn in ICONS:
        fn().save(OUT_DIR / f"{fn.__name__}.png", optimize=True)
    print(f"{len(ICONS)} иконок в {OUT_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
