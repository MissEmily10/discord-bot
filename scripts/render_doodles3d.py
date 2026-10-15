"""
3D-версии дудл-эмодзи форм (panel_art/emoji/doodle_*.png): те же предметы и
цвета, наклон в перспективе и толстый бок, как у scripts/render_ui3d.py.

Запуск из корня проекта:
    python3 scripts/render_doodles3d.py

Рисует panel_art/emoji/doodle3d/d3_*.png, 128×128 — по одной на каждый дудл.
"""

import math
import pathlib

from art3d import (ICE, INK, LIME, MAROON, MILK, PINK, STONE, arc_line, arc_pts, circle, dark, ell, heart_pts,
                   inter, light, line, minus, poly, rot, rr, small, soft, star_pts, union)
import art3d

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "panel_art" / "emoji" / "doodle3d"


def render(parts, details=()):
    return small(art3d.render(parts, details, outline=INK))


def spark_pts(cx, cy, r, inner=0.28):
    """Четырёхконечная звёздочка-искра."""
    return [(cx + (r if i % 2 == 0 else r * inner) * math.cos(math.radians(-90 + 45 * i)),
             cy + (r if i % 2 == 0 else r * inner) * math.sin(math.radians(-90 + 45 * i))) for i in range(8)]


def d3_balloon():
    string = line([(0.47, 0.66), (0.43, 0.76), (0.49, 0.86)], 0.022)
    knot = poly([(0.47, 0.6), (0.41, 0.69), (0.53, 0.69)], 0.005)
    return render([(string, INK), (knot, dark(PINK, 0.9)), (ell(0.2, 0.06, 0.74, 0.64), PINK)])


def d3_bell():
    body = soft(union(circle(0.47, 0.37, 0.22), poly([(0.25, 0.37), (0.69, 0.37), (0.78, 0.68), (0.16, 0.68)])), 0.015)
    return render([(circle(0.47, 0.76, 0.075), MAROON), (circle(0.47, 0.13, 0.05), LIME), (body, LIME),
                   (rr(0.11, 0.62, 0.83, 0.73, 0.05), LIME)])


def d3_bolt():
    return render([(poly([(0.58, 0.04), (0.17, 0.5), (0.44, 0.5), (0.35, 0.88), (0.77, 0.38), (0.51, 0.38), (0.62, 0.04)], 0.008), LIME)])


def d3_book():
    left = poly([(0.06, 0.2), (0.46, 0.27), (0.46, 0.82), (0.06, 0.75)], 0.01)
    right = poly([(0.48, 0.27), (0.88, 0.2), (0.88, 0.75), (0.48, 0.82)], 0.01)
    lines = union(*(line([(0.14, y), (0.38, y + 0.04)], 0.025) for y in (0.36, 0.48, 0.6)),
                  *(line([(0.56, y + 0.04), (0.8, y)], 0.025) for y in (0.36, 0.48)))
    mark = poly([(0.68, 0.2), (0.78, 0.18), (0.78, 0.46), (0.73, 0.41), (0.68, 0.46)])
    return render([(left, MILK), (right, MILK)], [(lines, INK), (mark, PINK)])


def d3_brush():
    return render([(line([(0.8, 0.07), (0.46, 0.46)], 0.09), MAROON), (rot(rr(0.36, 0.42, 0.52, 0.56, 0.02), -48, 0.44, 0.49), STONE),
                   (poly(arc_pts(0.32, 0.66, 0.1, 0.1, -30, 210, 30) + [(0.46, 0.46)], 0.01), LIME)])


def d3_cake():
    base = rr(0.12, 0.42, 0.82, 0.84, 0.05)
    icing = soft(union(rr(0.12, 0.38, 0.82, 0.54, 0.05), *(circle(x, 0.54, 0.05) for x in (0.2, 0.36, 0.52, 0.68))), 0.006)
    candles = union(*(rr(x - 0.025, 0.16, x + 0.025, 0.4, 0.01) for x in (0.3, 0.47, 0.64)))
    flames = union(*(poly(arc_pts(x, 0.11, 0.03, 0.03, -30, 210, 16) + [(x, 0.03)]) for x in (0.3, 0.47, 0.64)))
    return render([(candles, PINK), (base, PINK), (icing, MILK)], [(flames, LIME)])


def d3_camera():
    return render([(rr(0.28, 0.12, 0.56, 0.3, 0.04), PINK), (rr(0.07, 0.22, 0.87, 0.78, 0.09), PINK)],
                  [(circle(0.47, 0.5, 0.18), MILK), (circle(0.47, 0.5, 0.12), ICE), (circle(0.47, 0.5, 0.05), INK),
                   (circle(0.76, 0.32, 0.035), LIME)])


def d3_cat():
    head = soft(union(rr(0.12, 0.26, 0.82, 0.84, 0.2), poly([(0.13, 0.36), (0.18, 0.06), (0.4, 0.28)]),
                      poly([(0.81, 0.36), (0.76, 0.06), (0.54, 0.28)])), 0.012)
    eyes = union(circle(0.34, 0.5, 0.035), circle(0.6, 0.5, 0.035))
    return render([(head, MILK)], [(eyes, INK), (poly([(0.43, 0.58), (0.51, 0.58), (0.47, 0.63)], 0.004), PINK),
                                   (arc_line(0.47, 0.64, 0.14, 25, 155, 0.03, ry=0.08), LIME)])


def d3_chat():
    bubble = union(rr(0.07, 0.12, 0.87, 0.66, 0.12), poly([(0.2, 0.6), (0.16, 0.84), (0.4, 0.62)], 0.01))
    return render([(bubble, LIME)], [(union(*(circle(x, 0.39, 0.045) for x in (0.3, 0.47, 0.64))), INK)])


def d3_cherries():
    stems = union(line([(0.28, 0.6), (0.48, 0.14)], 0.03), line([(0.66, 0.62), (0.48, 0.14)], 0.03))
    leaf = rot(ell(0.5, 0.06, 0.78, 0.18), -18, 0.6, 0.12)
    return render([(stems, INK), (leaf, LIME), (circle(0.28, 0.66, 0.15), MAROON), (circle(0.66, 0.68, 0.15), MAROON)])


def d3_cloud():
    return render([(soft(union(circle(0.3, 0.52, 0.17), circle(0.5, 0.4, 0.22), circle(0.68, 0.54, 0.16),
                               rr(0.13, 0.5, 0.84, 0.7, 0.1)), 0.01), MILK)])


def d3_clover():
    leaves = union(*(circle(0.47 + dx, 0.4 + dy, 0.13) for dx, dy in ((-0.13, -0.13), (0.13, -0.13), (-0.13, 0.13), (0.13, 0.13))))
    return render([(line([(0.5, 0.5), (0.62, 0.86)], 0.04), LIME), (leaves, LIME)], [(circle(0.47, 0.4, 0.04), dark(LIME, 0.8))])


def d3_coffee():
    handle = minus(circle(0.7, 0.55, 0.13), circle(0.7, 0.55, 0.065))
    steam = union(*(line([(x, 0.22), (x + 0.03, 0.14), (x, 0.06)], 0.025) for x in (0.3, 0.44, 0.58)))
    return render([(handle, PINK), (rr(0.12, 0.3, 0.68, 0.84, 0.08), PINK)], [(steam, STONE)])


def d3_crown():
    top = poly([(0.1, 0.62), (0.08, 0.2), (0.3, 0.42), (0.47, 0.12), (0.64, 0.42), (0.86, 0.2), (0.84, 0.62)], 0.012)
    return render([(top, LIME), (rr(0.1, 0.6, 0.84, 0.76, 0.03), MAROON)],
                  [(union(circle(0.08, 0.18, 0.04), circle(0.47, 0.1, 0.04), circle(0.86, 0.18, 0.04)), PINK)])


def d3_dice():
    pips = union(*(circle(x, y, 0.05) for x, y in ((0.28, 0.28), (0.66, 0.28), (0.28, 0.66), (0.66, 0.66))))
    return render([(rr(0.1, 0.1, 0.84, 0.84, 0.12), MILK)], [(pips, INK), (circle(0.47, 0.47, 0.055), MAROON)])


def d3_eye():
    eye = soft(inter(circle(0.46, 0.92, 0.62), circle(0.46, 0.0, 0.62)), 0.01)
    return render([(eye, MILK)], [(circle(0.46, 0.46, 0.17), ICE), (circle(0.46, 0.46, 0.08), INK), (circle(0.41, 0.41, 0.03), MILK)])


def d3_film():
    holes = union(*(rr(x, y, x + 0.06, y + 0.05, 0.01) for x in (0.12, 0.27, 0.42, 0.57, 0.72) for y in (0.26, 0.64)))
    return render([(rr(0.06, 0.2, 0.88, 0.74, 0.04), INK)],
                  [(rr(0.12, 0.34, 0.45, 0.6, 0.02), LIME), (rr(0.49, 0.34, 0.82, 0.6, 0.02), ICE), (holes, MILK)])


def d3_fire():
    outer = poly([(0.47, 0.04), (0.6, 0.24), (0.66, 0.16), (0.8, 0.44), (0.76, 0.72), (0.6, 0.86), (0.34, 0.86),
                  (0.18, 0.72), (0.14, 0.48), (0.28, 0.26), (0.32, 0.38)], 0.03)
    inner = poly([(0.47, 0.42), (0.58, 0.58), (0.6, 0.72), (0.5, 0.8), (0.4, 0.8), (0.32, 0.7), (0.38, 0.54)], 0.02)
    return render([(outer, MAROON)], [(inner, PINK)])


def d3_flag():
    cloth = poly([(0.2, 0.1)] + [(0.2 + 0.64 * i / 20, 0.1 + 0.05 * math.sin(i / 20 * 2 * math.pi)) for i in range(21)] +
                 [(0.2 + 0.64 * i / 20, 0.52 + 0.05 * math.sin(i / 20 * 2 * math.pi)) for i in range(20, -1, -1)])
    return render([(line([(0.16, 0.08), (0.16, 0.88)], 0.05), INK), (cloth, PINK)])


def d3_flower():
    petals = union(*(circle(0.47 + 0.2 * math.cos(math.radians(a)), 0.45 + 0.2 * math.sin(math.radians(a)), 0.15)
                     for a in range(-90, 270, 72)))
    return render([(petals, LIME)], [(circle(0.47, 0.45, 0.1), PINK)])


def d3_gamepad():
    body = soft(union(rr(0.1, 0.26, 0.84, 0.66, 0.16), circle(0.22, 0.62, 0.13), circle(0.72, 0.62, 0.13)), 0.01)
    dpad = union(rr(0.2, 0.42, 0.36, 0.48, 0.01), rr(0.25, 0.37, 0.31, 0.53, 0.01))
    return render([(body, ICE)], [(dpad, INK), (circle(0.62, 0.42, 0.035), LIME), (circle(0.7, 0.5, 0.035), LIME)])


def d3_gear():
    m = circle(0.47, 0.46, 0.27)
    for k in range(8):
        a = math.radians(k * 45)
        ca, sa = math.cos(a), math.sin(a)
        m = union(m, poly([(0.47 + ca * l - sa * w, 0.46 + sa * l + ca * w) for l, w in ((0.15, -0.075), (0.38, -0.06), (0.38, 0.06), (0.15, 0.075))]))
    return render([(minus(soft(m, 0.012), circle(0.47, 0.46, 0.1)), PINK)], [])


def d3_gem():
    gem = poly([(0.2, 0.14), (0.74, 0.14), (0.9, 0.34), (0.47, 0.86), (0.04, 0.34)], 0.006)
    facets = union(line([(0.06, 0.34), (0.88, 0.34)], 0.02), line([(0.32, 0.15), (0.26, 0.34), (0.47, 0.84)], 0.02),
                   line([(0.62, 0.15), (0.68, 0.34), (0.47, 0.84)], 0.02))
    return render([(gem, ICE)], [(facets, light(ICE, 0.55))])


def d3_ghost():
    body = soft(union(circle(0.47, 0.36, 0.3), rr(0.17, 0.36, 0.77, 0.74, 0.02),
                      *(poly([(x, 0.73), (x + 0.1, 0.86), (x + 0.2, 0.73)]) for x in (0.17, 0.37, 0.57))), 0.008)
    return render([(body, MILK)], [(union(circle(0.37, 0.4, 0.04), circle(0.57, 0.4, 0.04)), INK),
                                   (union(ell(0.25, 0.48, 0.33, 0.52), ell(0.61, 0.48, 0.69, 0.52)), PINK),
                                   (circle(0.47, 0.52, 0.025), INK)])


def d3_gift():
    bow = union(rot(ell(0.24, 0.08, 0.48, 0.24), 20, 0.46, 0.24), rot(ell(0.46, 0.08, 0.7, 0.24), -20, 0.48, 0.24))
    return render([(bow, LIME), (rr(0.14, 0.38, 0.8, 0.86, 0.04), PINK), (rr(0.08, 0.24, 0.86, 0.4, 0.04), light(PINK, 0.2))],
                  [(union(rr(0.41, 0.24, 0.53, 0.86, 0.0)), LIME)])


def d3_globe():
    globe = circle(0.46, 0.45, 0.37)
    land = union(poly([(0.18, 0.3), (0.36, 0.2), (0.46, 0.34), (0.36, 0.5), (0.22, 0.47)], 0.025),
                 poly([(0.52, 0.52), (0.72, 0.46), (0.8, 0.6), (0.62, 0.78), (0.53, 0.68)], 0.025), ell(0.56, 0.14, 0.7, 0.26))
    return render([(globe, ICE)], [(inter(land, globe), LIME)])


def d3_headset():
    band = arc_line(0.47, 0.46, 0.33, 180, 360, 0.06)
    return render([(band, INK), (line([(0.2, 0.7), (0.32, 0.82), (0.44, 0.82)], 0.03), INK),
                   (rr(0.08, 0.4, 0.24, 0.72, 0.07), LIME), (rr(0.7, 0.4, 0.86, 0.72, 0.07), LIME)])


def d3_heart():
    return render([(poly(heart_pts(0.47, 0.47, 0.82)), MAROON)])


def d3_hourglass():
    glass = poly([(0.24, 0.16), (0.7, 0.16), (0.5, 0.47), (0.7, 0.78), (0.24, 0.78), (0.44, 0.47)], 0.03)
    sand = inter(poly([(0.24, 0.78), (0.7, 0.78), (0.47, 0.56)], 0.01), glass)
    return render([(glass, MILK), (rr(0.14, 0.08, 0.8, 0.18, 0.04), INK), (rr(0.14, 0.76, 0.8, 0.86, 0.04), INK)],
                  [(sand, LIME), (inter(poly([(0.34, 0.24), (0.6, 0.24), (0.47, 0.4)]), glass), LIME)])


def d3_key():
    ring = minus(circle(0.28, 0.3, 0.17), circle(0.28, 0.3, 0.07))
    shaft = union(line([(0.38, 0.4), (0.8, 0.82)], 0.09), line([(0.62, 0.64), (0.72, 0.54)], 0.07), line([(0.72, 0.74), (0.82, 0.64)], 0.07))
    return render([(shaft, LIME), (ring, LIME)])


def d3_laptop():
    return render([(rr(0.16, 0.14, 0.78, 0.6, 0.04), INK), (poly([(0.12, 0.62), (0.82, 0.62), (0.9, 0.78), (0.04, 0.78)], 0.012), MILK)],
                  [(rr(0.21, 0.19, 0.73, 0.55, 0.02), ICE), (union(rr(0.27, 0.27, 0.6, 0.31, 0.02), rr(0.27, 0.36, 0.5, 0.4, 0.02)), MILK)])


def d3_lock():
    shackle = union(arc_line(0.47, 0.34, 0.18, 180, 360, 0.09), line([(0.29, 0.34), (0.29, 0.46)], 0.09), line([(0.65, 0.34), (0.65, 0.46)], 0.09))
    return render([(shackle, STONE), (rr(0.13, 0.4, 0.81, 0.84, 0.08), PINK)],
                  [(union(circle(0.47, 0.57, 0.06), rr(0.44, 0.58, 0.5, 0.72, 0.03)), INK)])


def d3_lollipop():
    swirl = line([(0.47 + (0.02 + 0.2 * i / 79) * math.cos(i * 0.2), 0.36 + (0.02 + 0.2 * i / 79) * math.sin(i * 0.2))
                  for i in range(80)], 0.035)
    return render([(line([(0.47, 0.5), (0.47, 0.88)], 0.05), MILK), (circle(0.47, 0.36, 0.28), PINK)], [(swirl, MAROON)])


def d3_mail():
    flap = line([(0.13, 0.26), (0.46, 0.5), (0.79, 0.26)], 0.04)
    return render([(rr(0.06, 0.16, 0.86, 0.76, 0.06), MILK)], [(flap, INK), (circle(0.46, 0.5, 0.05), MAROON)])


def d3_medal():
    ribbon = union(poly([(0.22, 0.04), (0.4, 0.04), (0.56, 0.42), (0.42, 0.48)]), poly([(0.72, 0.04), (0.54, 0.04), (0.38, 0.42), (0.52, 0.48)]))
    return render([(ribbon, PINK), (circle(0.47, 0.62, 0.22), LIME)], [(poly(star_pts(0.47, 0.62, 0.12), 0.004), MILK)])


def d3_megaphone():
    return render([(rr(0.3, 0.52, 0.42, 0.78, 0.04), INK), (rr(0.08, 0.32, 0.26, 0.58, 0.04), MAROON),
                   (poly([(0.22, 0.33), (0.72, 0.1), (0.72, 0.8), (0.22, 0.57)], 0.015), PINK), (rr(0.68, 0.07, 0.83, 0.83, 0.07), light(PINK, 0.3))])


def d3_mic():
    stand = union(arc_line(0.47, 0.42, 0.22, 0, 180, 0.04), line([(0.47, 0.64), (0.47, 0.82)], 0.04), line([(0.32, 0.84), (0.62, 0.84)], 0.05))
    grille = union(*(rr(0.4, y, 0.54, y + 0.035, 0.015) for y in (0.2, 0.29, 0.38)))
    return render([(stand, INK), (rr(0.33, 0.06, 0.61, 0.58, 0.14), MAROON)], [(grille, MILK)])


def d3_moon():
    moon = minus(circle(0.44, 0.48, 0.34), circle(0.62, 0.36, 0.28))
    return render([(moon, ICE)], [(poly(spark_pts(0.72, 0.62, 0.06)), PINK), (poly(spark_pts(0.8, 0.2, 0.05)), PINK)])


def d3_mushroom():
    cap = soft(union(inter(ell(0.08, 0.06, 0.86, 0.7), rr(0, 0, 1, 0.5, 0)), rr(0.08, 0.38, 0.86, 0.52, 0.06)), 0.01)
    spots = union(circle(0.3, 0.3, 0.06), circle(0.52, 0.18, 0.05), circle(0.66, 0.36, 0.06))
    return render([(rr(0.32, 0.46, 0.62, 0.86, 0.08), MILK), (cap, MAROON)], [(spots, MILK)])


def d3_music():
    stems = union(rr(0.32, 0.16, 0.38, 0.7, 0.01), rr(0.72, 0.08, 0.78, 0.62, 0.01), poly([(0.32, 0.16), (0.78, 0.06), (0.78, 0.18), (0.32, 0.28)]))
    return render([(stems, INK), (rot(ell(0.12, 0.62, 0.38, 0.8), 18, 0.25, 0.71), MAROON), (rot(ell(0.52, 0.54, 0.78, 0.72), 18, 0.65, 0.63), MAROON)])


def d3_palette():
    board = minus(ell(0.06, 0.12, 0.86, 0.8), circle(0.33, 0.6, 0.075))
    return render([(board, MILK)], [(circle(0.28, 0.34, 0.07), PINK), (circle(0.48, 0.25, 0.07), MAROON),
                                    (circle(0.67, 0.34, 0.07), ICE), (circle(0.68, 0.56, 0.07), LIME)])


def d3_pencil():
    parts = [(poly([(0.22, 0.38), (0.05, 0.47), (0.22, 0.56)], 0.01), MILK), (rr(0.21, 0.38, 0.68, 0.56, 0.02), LIME),
             (rr(0.66, 0.38, 0.75, 0.56, 0.01), STONE), (rr(0.73, 0.38, 0.9, 0.56, 0.06), PINK)]
    details = [(poly([(0.11, 0.44), (0.05, 0.47), (0.11, 0.5)]), INK)]
    return render([(rot(m, 48, 0.48, 0.48), c) for m, c in parts], [(rot(m, 48, 0.48, 0.48), c) for m, c in details])


def d3_planet():
    ring = minus(rot(ell(0.02, 0.36, 0.92, 0.58), 12), rot(ell(0.14, 0.42, 0.8, 0.52), 12))
    front = inter(ring, rr(0, 0.47, 1, 1, 0))
    return render([(inter(ring, rr(0, 0, 1, 0.47, 0)), LIME), (circle(0.47, 0.47, 0.24), PINK), (front, LIME)])


def d3_rocket():
    body = soft(union(ell(0.32, 0.04, 0.62, 0.6), rr(0.32, 0.3, 0.62, 0.7, 0.04)), 0.01)
    fins = union(poly([(0.32, 0.46), (0.18, 0.72), (0.32, 0.68)], 0.008), poly([(0.62, 0.46), (0.76, 0.72), (0.62, 0.68)], 0.008))
    flame = poly([(0.38, 0.7), (0.56, 0.7), (0.47, 0.92)], 0.01)
    return render([(flame, LIME), (fins, MAROON), (body, MILK)], [(circle(0.47, 0.32, 0.07), ICE)])


def d3_shield():
    sh = poly([(0.47, 0.06), (0.82, 0.18), (0.78, 0.52), (0.47, 0.86), (0.16, 0.52), (0.12, 0.18)], 0.03)
    return render([(sh, ICE)], [(line([(0.32, 0.46), (0.44, 0.58), (0.64, 0.34)], 0.06), INK)])


def d3_sparkles():
    return render([(poly(spark_pts(0.38, 0.54, 0.32)), MILK), (poly(spark_pts(0.74, 0.22, 0.14)), LIME)],
                  [(poly(spark_pts(0.76, 0.72, 0.09)), INK)])


def d3_star():
    return render([(poly(star_pts(0.47, 0.48, 0.42, inner=0.5), 0.025), PINK)])


def d3_sun():
    rays = union(*(rot(rr(0.44, 0.02, 0.5, 0.16, 0.03), a, 0.47, 0.46) for a in range(0, 360, 45)))
    face = union(circle(0.4, 0.42, 0.025), circle(0.54, 0.42, 0.025), arc_line(0.47, 0.48, 0.08, 25, 155, 0.025, ry=0.06))
    return render([(rays, LIME), (circle(0.47, 0.46, 0.24), LIME)], [(face, INK)])


def d3_trophy():
    handles = union(minus(circle(0.2, 0.26, 0.13), circle(0.2, 0.26, 0.065)), minus(circle(0.74, 0.26, 0.13), circle(0.74, 0.26, 0.065)))
    bowl = inter(union(rr(0.2, 0.06, 0.74, 0.3, 0.03), ell(0.2, 0.06, 0.74, 0.58)), rr(0.0, 0.06, 1.0, 1.0, 0))
    return render([(handles, LIME), (rr(0.41, 0.5, 0.53, 0.7, 0.02), LIME), (bowl, LIME), (rr(0.25, 0.66, 0.69, 0.84, 0.05), MAROON)],
                  [(poly(star_pts(0.47, 0.29, 0.11, inner=0.45), 0.006), MILK)])


def d3_wrench():
    head = minus(circle(0.66, 0.28, 0.17), rot(rr(0.62, 0.02, 0.72, 0.28, 0.01), -45, 0.66, 0.28))
    return render([(union(line([(0.2, 0.76), (0.58, 0.38)], 0.12), head, circle(0.2, 0.76, 0.08)), ICE)])


ICONS = [v for k, v in sorted(globals().items()) if k.startswith("d3_") and callable(v)]


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for fn in ICONS:
        fn().save(OUT_DIR / f"{fn.__name__}.png", optimize=True)
    print(f"{len(ICONS)} иконок в {OUT_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
