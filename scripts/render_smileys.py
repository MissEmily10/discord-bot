"""
Фирменные смайлики в 3D. Основа — оригиналы (плоские иконки панелей из
макета), сохранённые в scripts/smiley_originals/thumbnail.*.png:
рисунок не перерисовывается, только получает толстый бок, лёгкий наклон и
выпуклость (art3d.lift). Остальные эмоции рисуются на том же пустом лице
(обводка и блик оригинала) линиями той же толщины.

Запуск из корня проекта:
    python3 scripts/render_smileys.py

Рисует panel_art/emoji/smiley/smile_*.png, 128×128.
flat_smiley() / smiley() нужны и scripts/render_profile.py (ава, баннеры).
"""

import pathlib

from PIL import Image, ImageChops, ImageFilter

from art3d import (ICE, INK, LIME, MAROON, MILK, PINK, arc_line, circle, heart_pts, inter, line, minus,
                   poly, rr, small, star_pts, union, wobbly_pts)
import art3d

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "panel_art" / "emoji" / "smiley"

CX, CY, R = 0.47, 0.46, 0.36
W = 0.034          # толщина линий черт лица
EX, EY = 0.115, 0.39


def _face(seed=1):
    return poly(wobbly_pts(CX, CY, R, seed=seed))


def _gloss():
    return arc_line(CX, CY, R * 0.8, 200, 245, 0.04)


def dots(dy=0.0, r=0.032, dx=0.0):
    return union(circle(CX - EX + dx, EY + dy, r), circle(CX + EX + dx, EY + dy, r))


def smile_arc(w=0.17, h=0.12, y=0.45, dx=0.0):
    return arc_line(CX + dx, y, w, 22, 158, W, ry=h)


def frown_arc(w=0.13, h=0.08, y=0.6):
    return arc_line(CX, y, w, 205, 335, W, ry=h)


def flat_mouth(w=0.12, y=0.56, dx=0.0):
    return line([(CX - w + dx, y), (CX + w + dx, y)], W)


def happy_eyes(dy=0.0):
    """Глаза-дужки ∩ (смех)."""
    return union(*(arc_line(CX + s * EX, EY + 0.02 + dy, 0.05, 200, 340, W, ry=0.04) for s in (-1, 1)))


def closed_eyes(dy=0.0):
    """Глаза-дужки ∪ (сон, блаженство)."""
    return union(*(arc_line(CX + s * EX, EY - 0.01 + dy, 0.05, 20, 160, W * 0.9, ry=0.03) for s in (-1, 1)))


def open_mouth(y=0.5, w=0.15, h=0.12):
    m = art3d.inter(art3d.ell(CX - w, y - h, CX + w, y + h), art3d.rr(CX - w - 0.01, y, CX + w + 0.01, y + h + 0.01, 0))
    tongue = inter(circle(CX, y + h * 0.95, w * 0.55), m)
    return m, tongue


def blush():
    return union(art3d.ell(CX - 0.24, 0.47, CX - 0.12, 0.52), art3d.ell(CX + 0.12, 0.47, CX + 0.24, 0.52))


def tear(cx, cy, r):
    pts = art3d.arc_pts(cx, cy, r, r, -30, 210, 30) + [(cx, cy - r * 2.3)]
    return poly(pts)


# ---------- смайлики: (черты лица, доп. части, цвет лица) ----------

def smile():
    return [(dots(), INK), (smile_arc(), INK)]


def smile_lime():
    return [(dots(), INK), (smile_arc(), INK)], [], LIME


def smile_pink():
    return [(dots(), INK), (smile_arc(w=0.12, h=0.08), INK), (blush(), MAROON)], [], PINK


def neutral():
    return [(dots(), INK), (flat_mouth(), INK)]


def monocle():
    ring = minus(circle(CX + EX, EY, 0.06), circle(CX + EX, EY, 0.035))
    return [(circle(CX - EX, EY, 0.032), INK), (ring, INK), (circle(CX + EX + 0.01, EY + 0.008, 0.02), INK),
            (flat_mouth(w=0.1), INK)]


def wait():
    clock = circle(0.75, 0.74, 0.12)
    hands = union(line([(0.75, 0.74), (0.75, 0.66)], 0.026), line([(0.75, 0.74), (0.81, 0.77)], 0.026))
    return [(dots(), INK), (flat_mouth(), INK)], [(clock, MILK)], MILK, [(hands, INK)]


def cool():
    glasses = union(rr(CX - 0.25, 0.33, CX - 0.02, 0.43, 0.02), rr(CX + 0.02, 0.33, CX + 0.25, 0.43, 0.02),
                    rr(CX - 0.28, 0.325, CX + 0.28, 0.355, 0.01))
    glint = rr(CX - 0.21, 0.355, CX - 0.12, 0.375, 0.01)
    return [(glasses, INK), (glint, ICE), (smile_arc(w=0.19, h=0.1, y=0.5), INK)]


def cry():
    brows = union(line([(CX - 0.2, 0.33), (CX - 0.06, 0.3)], W), line([(CX + 0.06, 0.3), (CX + 0.2, 0.33)], W))
    tears = union(rr(CX - EX - 0.025, 0.4, CX - EX + 0.025, 0.66, 0.025), rr(CX + EX - 0.025, 0.4, CX + EX + 0.025, 0.66, 0.025))
    return [(brows, INK), (tears, ICE), (dots(dy=0.01, r=0.026), INK), (frown_arc(y=0.64), INK)]


def angel():
    halo = minus(art3d.ell(CX - 0.2, 0.0, CX + 0.2, 0.09), art3d.ell(CX - 0.14, 0.022, CX + 0.14, 0.068))
    return [(closed_eyes(dy=0.02), INK), (smile_arc(w=0.08, h=0.05, y=0.52), INK)], [(halo, LIME)], MILK


def cracked():
    crack = line([(CX - 0.02, 0.12), (CX - 0.09, 0.22), (CX + 0.0, 0.29), (CX - 0.07, 0.4)], 0.03)
    return [(crack, MAROON), (dots(), INK), (smile_arc(), INK)]


def kiss():
    mouth = union(arc_line(CX + 0.0, 0.53, 0.035, -90, 90, W * 0.9, ry=0.03), arc_line(CX + 0.0, 0.59, 0.035, -90, 90, W * 0.9, ry=0.03))
    return [(dots(), INK), (blush(), PINK), (mouth, INK)]


def snot():
    drip = union(line([(CX + 0.02, 0.44), (CX + 0.03, 0.56), (CX + 0.015, 0.66)], 0.04), circle(CX + 0.015, 0.68, 0.032))
    return [(dots(), INK), (smile_arc(), INK), (drip, LIME)]


def angry():
    hair = union(*(line([(CX + x, 0.14), (CX + x * 1.25 + 0.02 * (1 if x > 0 else -1), 0.04)], 0.03)
                   for x in (-0.12, -0.04, 0.04, 0.12)))
    brows = union(line([(CX - 0.2, 0.3), (CX - 0.05, 0.36)], W * 1.1), line([(CX + 0.05, 0.36), (CX + 0.2, 0.3)], W * 1.1))
    return [(brows, INK), (dots(dy=0.03, r=0.028), INK), (frown_arc(w=0.11, y=0.62), INK)], [(hair, INK)], MILK


def sad():
    return [(dots(dy=0.01), INK), (frown_arc(), INK)]


def laugh():
    m, tongue = open_mouth()
    return [(happy_eyes(), INK), (m, INK), (tongue, PINK)]


def love():
    hearts = union(poly(heart_pts(CX - EX, EY, 0.11)), poly(heart_pts(CX + EX, EY, 0.11)))
    return [(hearts, MAROON), (smile_arc(w=0.14, h=0.09, y=0.5), INK)]


def wink():
    eye = arc_line(CX + EX, EY + 0.02, 0.05, 200, 340, W, ry=0.04)
    return [(circle(CX - EX, EY, 0.032), INK), (eye, INK), (smile_arc(w=0.15, h=0.1, dx=0.01), INK)]


def shock():
    rings = union(*(minus(circle(CX + s * EX, EY, 0.06), circle(CX + s * EX, EY, 0.034)) for s in (-1, 1)))
    o = minus(art3d.ell(CX - 0.06, 0.5, CX + 0.06, 0.63), art3d.ell(CX - 0.03, 0.53, CX + 0.03, 0.6))
    return [(rings, INK), (dots(r=0.016), INK), (o, INK)]


def sleepy():
    z1 = line([(0.7, 0.16), (0.78, 0.16), (0.7, 0.24), (0.78, 0.24)], 0.025)
    z2 = line([(0.8, 0.04), (0.9, 0.04), (0.8, 0.14), (0.9, 0.14)], 0.03)
    return [(closed_eyes(dy=0.02), INK), (art3d.ell(CX - 0.035, 0.54, CX + 0.035, 0.6), INK)], [(z1, ICE), (z2, ICE)], MILK


def dead():
    xs = union(*(union(line([(CX + s * EX - 0.04, EY - 0.04), (CX + s * EX + 0.04, EY + 0.04)], W),
                       line([(CX + s * EX + 0.04, EY - 0.04), (CX + s * EX - 0.04, EY + 0.04)], W)) for s in (-1, 1)))
    return [(xs, INK), (flat_mouth(w=0.1), INK)]


def tongue():
    t = union(rr(CX - 0.05, 0.53, CX + 0.05, 0.62, 0.05), circle(CX, 0.62, 0.05))
    return [(dots(), INK), (t, PINK), (smile_arc(), INK)]


def blush_face():
    return [(happy_eyes(dy=0.02), INK), (blush(), PINK), (smile_arc(w=0.07, h=0.05, y=0.52), INK)]


def think():
    brow = arc_line(CX + EX, EY - 0.07, 0.06, 200, 340, W * 0.9, ry=0.03)
    q = union(arc_line(0.8, 0.12, 0.05, 180, 400, 0.03), line([(0.83, 0.16), (0.8, 0.2), (0.8, 0.22)], 0.03), circle(0.8, 0.28, 0.02))
    return [(dots(dx=0.03), INK), (brow, INK), (line([(CX - 0.1, 0.57), (CX + 0.04, 0.56), (CX + 0.12, 0.52)], W), INK)], \
        [(q, LIME)], MILK


def stars():
    st = union(poly(star_pts(CX - EX, EY, 0.065), 0.003), poly(star_pts(CX + EX, EY, 0.065), 0.003))
    m, tng = open_mouth(y=0.5, w=0.13, h=0.1)
    return [(st, LIME), (m, INK), (tng, PINK)]


def devil():
    horns = union(poly([(CX - 0.26, 0.2), (CX - 0.3, 0.02), (CX - 0.14, 0.13)], 0.01), poly([(CX + 0.26, 0.2), (CX + 0.3, 0.02), (CX + 0.14, 0.13)], 0.01))
    brows = union(line([(CX - 0.19, 0.31), (CX - 0.06, 0.35)], W), line([(CX + 0.06, 0.35), (CX + 0.19, 0.31)], W))
    return [(brows, INK), (dots(dy=0.02), INK), (smile_arc(w=0.16, h=0.08, y=0.5), INK)], [(horns, MAROON)], MILK


SMILEYS = {
    "smile": smile, "smile_lime": smile_lime, "smile_pink": smile_pink, "laugh": laugh, "wink": wink,
    "blush": blush_face, "love": love, "kiss": kiss, "stars": stars, "tongue": tongue, "cool": cool,
    "angel": angel, "devil": devil, "think": think, "neutral": neutral, "monocle": monocle, "wait": wait,
    "shock": shock, "sad": sad, "cry": cry, "angry": angry, "snot": snot, "cracked": cracked,
    "sleepy": sleepy, "dead": dead,
}

# оригиналы: имя -> картинка панели
ORIGINALS = {
    "snot": "thumbnail.access.png", "monocle": "thumbnail.buttons.png", "smile": "thumbnail.default.png",
    "wait": "thumbnail.embed.png", "cool": "thumbnail.forms.png", "cry": "thumbnail.logo.png",
    "angel": "thumbnail.select.png", "cracked": "thumbnail.templates.png", "kiss": "thumbnail.webhooks.png",
}
RECOLOR = {"smile_lime": LIME, "smile_pink": PINK}


def spec(name):
    """(черты, части вокруг лица, цвет лица, черты поверх частей)."""
    out = SMILEYS[name]()
    if isinstance(out, list):
        return out, [], MILK, []
    return tuple(out) + ((),) * (4 - len(out))


def _original(file):
    """Оригинал по центру холста 512×512 с запасом под бок и детали."""
    im = Image.open(ROOT / "scripts" / "smiley_originals" / file).convert("RGBA").resize((380, 380), Image.LANCZOS)
    out = Image.new("RGBA", (art3d.S, art3d.S), (0, 0, 0, 0))
    out.alpha_composite(im, (56, 50))
    return out


def _blank():
    """Пустое лицо: оригинал улыбки без глаз и рта (обводка и блик остаются)."""
    img = _original(ORIGINALS["smile"])
    alpha = img.getchannel("A").point(lambda v: 255 if v > 100 else 0)
    inner = alpha.filter(ImageFilter.MinFilter(41))
    gray = img.convert("L")
    feats = ImageChops.multiply(inner, gray.point(lambda v: 255 if v < 150 else 0)).filter(ImageFilter.MaxFilter(9))
    milk = Image.new("RGBA", img.size, MILK + (255,))
    img.paste(milk, (0, 0), feats)
    return img


def _face_box(img):
    """Рамка лица (без деталей вокруг) — по ней ставятся нарисованные черты."""
    alpha = img.getchannel("A").point(lambda v: 255 if v > 100 else 0)
    return alpha.getbbox()


def _recolor(img, col):
    """Перекрасить молочную заливку (обводка, черты и блик остаются)."""
    px = img.load()
    out = img.copy()
    po = out.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = px[x, y]
            if a and min(r, g, b) > 200 and max(r, g, b) - min(r, g, b) < 24 and max(r, g, b) < 250:
                k = (r + g + b) / 3 / MILK[0]
                po[x, y] = tuple(min(255, int(c * k)) for c in col) + (a,)
    return out


def flat_smiley(name):
    """Плоский фирменный смайлик 512×512 (как в макетах)."""
    if name in ORIGINALS:
        return _original(ORIGINALS[name])
    base = _blank()
    if name in RECOLOR:
        return _recolor(_original(ORIGINALS["smile"]), RECOLOR[name])
    feats, extra, color, over = spec(name)
    layer = art3d.render(list(extra), list(feats) + list(over), flat=True) if extra else \
        art3d.render([], list(feats) + list(over), flat=True)
    # нарисованное лицо (CX, CY, R) -> рамка лица оригинала
    x0, y0, x1, y1 = _face_box(base)
    k = (x1 - x0) / (2 * R * art3d.S)
    w = int(art3d.S * k)
    layer = layer.resize((w, w), Image.LANCZOS)
    ox = int((x0 + x1) / 2 - CX * art3d.S * k)
    oy = int((y0 + y1) / 2 - CY * art3d.S * k)
    out = base.copy()
    if extra:
        # части вокруг лица (нимб, рожки, Zzz) — с той же обводкой
        ring = Image.new("RGBA", out.size, (0, 0, 0, 0))
        ring.alpha_composite(layer, (ox, oy)) if ox >= 0 and oy >= 0 else ring.paste(layer, (ox, oy), layer)
        a = ring.getchannel("A").point(lambda v: 255 if v > 100 else 0)
        art3d.paint(out, minus(art3d.grow(a, 0.012), base.getchannel("A")), INK)
        out.alpha_composite(ring)
    else:
        top = Image.new("RGBA", out.size, (0, 0, 0, 0))
        top.paste(layer, (ox, oy), layer)
        out.alpha_composite(top)
    return out


def smiley(name, t=(-6, 0.94, -0.08, 1.0)):
    """3D-смайлик 512×512."""
    return art3d.lift(flat_smiley(name), t=t)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("*.png"):
        old.unlink()
    for name in SMILEYS:
        small(smiley(name)).save(OUT_DIR / f"smile_{name}.png", optimize=True)
    print(f"{len(SMILEYS)} смайликов в {OUT_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
