"""
Общий движок объёмной графики для scripts/render_*.py.

Фигура рисуется плоской маской (координаты — доли холста 512×512),
потом наклоняется в перспективе (как иконки на референсе: чуть повёрнута
и видна сверху) и получает толстый тёмный бок вниз-вправо.
Цвета — фирменная палитра макетов (баннеры, дудлы).
"""

import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter

S = 512

# ---------- фирменная палитра ----------
MILK = (242, 238, 234)      # молочный белый — смайлики, плашки
INK = (34, 30, 34)          # почти чёрный — обводка, черты лица
LIME = (182, 236, 52)
PINK = (228, 174, 196)
MAROON = (150, 28, 58)
WINE = (122, 14, 38)        # тёмное бордо из баннеров
ICE = (192, 216, 232)
MAUVE = (170, 128, 146)
STONE = (168, 172, 184)

# наклон по умолчанию: поворот (град), сжатие по вертикали, сдвиг по x, ширина
TILT = (-9, 0.9, -0.14, 1.0)
DEPTH = (0.016, 0.062)
DETAIL_DEPTH = (0.005, 0.016)
OUTLINE = 0.016


def _p(v):
    return v * S


# ---------- маски ----------

def M():
    return Image.new("L", (S, S), 0)


def rr(x0, y0, x1, y1, r=0.06):
    m = M()
    ImageDraw.Draw(m).rounded_rectangle((_p(x0), _p(y0), _p(x1), _p(y1)), radius=_p(r), fill=255)
    return m


def circle(cx, cy, r):
    m = M()
    ImageDraw.Draw(m).ellipse((_p(cx - r), _p(cy - r), _p(cx + r), _p(cy + r)), fill=255)
    return m


def ell(x0, y0, x1, y1):
    m = M()
    ImageDraw.Draw(m).ellipse((_p(x0), _p(y0), _p(x1), _p(y1)), fill=255)
    return m


def poly(pts, smooth=0.0):
    m = M()
    ImageDraw.Draw(m).polygon([(_p(x), _p(y)) for x, y in pts], fill=255)
    return soft(m, smooth) if smooth else m


def line(pts, w):
    m = M()
    d = ImageDraw.Draw(m)
    px = [(_p(x), _p(y)) for x, y in pts]
    d.line(px, fill=255, width=max(1, int(_p(w))), joint="curve")
    r = _p(w) / 2
    for x, y in px:
        d.ellipse((x - r, y - r, x + r, y + r), fill=255)
    return m


def arc_pts(cx, cy, rx, ry, a0, a1, n=48):
    """Точки дуги; углы в градусах, 0 — вправо, 90 — вниз."""
    return [(cx + rx * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


def arc_line(cx, cy, r, a0, a1, w, ry=None):
    return line(arc_pts(cx, cy, r, ry if ry is not None else r, a0, a1), w)


def star_pts(cx, cy, r, inner=0.48, angle=-90):
    return [(cx + (r if i % 2 == 0 else r * inner) * math.cos(math.radians(angle + 36 * i)),
             cy + (r if i % 2 == 0 else r * inner) * math.sin(math.radians(angle + 36 * i))) for i in range(10)]


def heart_pts(cx, cy, size):
    pts = []
    for i in range(80):
        t = 2 * math.pi * i / 80
        x = 16 * math.sin(t) ** 3
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((cx + x * size / 32, cy + y * size / 32))
    return pts


def wobbly_pts(cx, cy, r, seed=0, amp=0.022, n=160):
    """Неровный круг фирменных смайликов."""
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        k = 1 + amp * math.sin(7 * t + seed) + amp * 0.6 * math.sin(13 * t + seed * 2.1) + amp * 0.4 * math.sin(23 * t + seed * 0.7)
        pts.append((cx + r * k * math.cos(t), cy + r * k * math.sin(t)))
    return pts


def union(*ms):
    out = ms[0]
    for m in ms[1:]:
        out = ImageChops.lighter(out, m)
    return out


def minus(a, *bs):
    for b in bs:
        a = ImageChops.subtract(a, b)
    return a


def inter(a, b):
    return ImageChops.multiply(a, b)


def soft(m, r=0.02):
    """Скруглить углы: размыть и снова сделать резкой (с мягким краем)."""
    return m.filter(ImageFilter.GaussianBlur(_p(r))).point(lambda v: max(0, min(255, (v - 128) * 6 + 128)))


def grow(m, r):
    return m.filter(ImageFilter.GaussianBlur(_p(r) * 0.5)).point(lambda v: 255 if v > 8 else v * 30)


def shift(m, dx, dy):
    out = Image.new(m.mode, m.size, 0)
    out.paste(m, (int(round(dx)), int(round(dy))))
    return out


def rot(m, angle, cx=0.47, cy=0.47):
    return m.rotate(angle, center=(_p(cx), _p(cy)), resample=Image.BICUBIC)


def mirror(m, cx=0.47):
    return shift(m.transpose(Image.FLIP_LEFT_RIGHT), -(S - 2 * _p(cx)), 0)


def tilt(m, t=TILT, cx=0.47, cy=0.47):
    """Наклон в перспективе: ширина, сдвиг, сжатие по вертикали, поворот."""
    angle, squash, shear, sx = t
    a = math.radians(angle)
    ca, sa = math.cos(a), math.sin(a)
    # прямое: p' = C + R · [[sx, shear], [0, squash]] · (p - C)
    f = [[ca * sx, ca * shear - sa * squash], [sa * sx, sa * shear + ca * squash]]
    det = f[0][0] * f[1][1] - f[0][1] * f[1][0]
    inv = [[f[1][1] / det, -f[0][1] / det], [-f[1][0] / det, f[0][0] / det]]
    c = (_p(cx), _p(cy))
    data = (inv[0][0], inv[0][1], c[0] - inv[0][0] * c[0] - inv[0][1] * c[1],
            inv[1][0], inv[1][1], c[1] - inv[1][0] * c[0] - inv[1][1] * c[1])
    return m.transform(m.size, Image.AFFINE, data, resample=Image.BICUBIC)


# ---------- рисование ----------

def dark(col, k=0.68):
    return tuple(int(c * k) for c in col)


def light(col, k=0.45):
    return tuple(int(c + (255 - c) * k) for c in col)


def paint(canvas, m, col, alpha=1.0):
    top = Image.new("RGBA", canvas.size, col + (255,))
    top.putalpha(m if alpha == 1.0 else m.point(lambda v: int(v * alpha)))
    canvas.alpha_composite(top)


def extrude(m, depth):
    dx, dy = _p(depth[0]), _p(depth[1])
    steps = max(4, int(max(abs(dx), abs(dy))))
    ext = M()
    for i in range(1, steps + 1):
        ext = ImageChops.lighter(ext, shift(m, dx * i / steps, dy * i / steps))
    return ext


def _dome(m, col):
    """Мягкий объём на лицевой стороне: темнее к нижнему правому краю."""
    inner = m.filter(ImageFilter.GaussianBlur(_p(0.09)))
    edge = ImageChops.subtract(m, inner).point(lambda v: min(255, int(v * 1.6)))
    edge = inter(shift(edge, _p(0.02), _p(0.025)), m)
    return edge


def render(parts, details=(), t=TILT, depth=DEPTH, outline=None, dome=False, gloss=None, flat=False):
    """
    parts — объёмные части (сзади вперёд): (маска, цвет); details — рисунок
    поверх: (маска, цвет). outline — цвет обводки всего силуэта (фирменная
    толстая обводка), gloss — маска блика (рисуется белым поверх).
    flat — плоская 2D-версия того же рисунка (без бока и наклона).
    Возвращает 512×512 RGBA.
    """
    if flat:
        t, depth = (0, 1, 0, 1), (0, 0)
    c = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    k = _p(0.014)
    parts = [(tilt(m, t), col) for m, col in parts]
    details = [(tilt(m, t), col) for m, col in details]
    if outline:
        sil = union(*[m if flat else union(m, extrude(m, depth)) for m, _ in parts])
        paint(c, grow(sil, OUTLINE * 2), outline)
    for m, col in parts:
        if flat:
            paint(c, m, col)
            continue
        paint(c, extrude(m, depth), dark(col))
        paint(c, m, col)
        if dome:
            paint(c, _dome(m, col), dark(col, 0.82), 0.9)
        paint(c, minus(m, shift(m, -k, -k)), dark(col, 0.9))
        paint(c, minus(m, shift(m, k, k)), light(col), 0.85)
    for m, col in details:
        if not flat:
            paint(c, extrude(m, DETAIL_DEPTH), dark(col, 0.7) if col != INK else (12, 10, 12))
        paint(c, m, col)
    if gloss is not None:
        g = tilt(gloss, t).filter(ImageFilter.GaussianBlur(_p(0.006)))
        paint(c, g, (255, 255, 255), 0.9)
    return c


def small(img, size=128):
    return img.resize((size, size), Image.LANCZOS)


def lift(img, t=(-6, 0.94, -0.08, 1.0), depth=(0.014, 0.075), side=None, cx=0.5, cy=0.5, dome=True):
    """
    Готовую плоскую картинку (RGBA 512×512, например оригинальный смайлик)
    сделать объёмной: тот же рисунок сверху, чуть наклонён, снизу — толстый
    бок с обводкой. Сам рисунок не перерисовывается.
    """
    top = img.transform(img.size, Image.AFFINE, _affine(t, cx, cy), resample=Image.BICUBIC)
    alpha = top.getchannel("A").point(lambda v: 255 if v > 100 else 0)
    ext = extrude(alpha, depth)
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    paint(out, grow(union(alpha, ext), 0.012), INK)
    side = side or MILK
    box = ext.getbbox() or (0, 0, S, S)
    grad = Image.linear_gradient("L").resize((S, box[3] - box[1]))
    fill = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    fill.paste(Image.composite(Image.new("RGBA", (S, box[3] - box[1]), dark(side, 0.5) + (255,)),
                               Image.new("RGBA", (S, box[3] - box[1]), dark(side, 0.86) + (255,)), grad), (0, box[1]))
    inner = ImageChops.subtract(ext.filter(ImageFilter.MinFilter(int(_p(0.024)) | 1)), alpha)
    fill.putalpha(inner)
    out.alpha_composite(fill)
    out.alpha_composite(top)
    if dome:
        face = alpha.filter(ImageFilter.MinFilter(int(_p(0.06)) | 1))
        paint(out, inter(_dome(face, MILK), face), dark(MILK, 0.8), 0.55)
    return out


def _affine(t, cx, cy):
    angle, squash, shear, sx = t
    a = math.radians(angle)
    ca, sa = math.cos(a), math.sin(a)
    f = [[ca * sx, ca * shear - sa * squash], [sa * sx, sa * shear + ca * squash]]
    det = f[0][0] * f[1][1] - f[0][1] * f[1][0]
    inv = [[f[1][1] / det, -f[0][1] / det], [-f[1][0] / det, f[0][0] / det]]
    c = (_p(cx), _p(cy))
    return (inv[0][0], inv[0][1], c[0] - inv[0][0] * c[0] - inv[0][1] * c[1],
            inv[1][0], inv[1][1], c[1] - inv[1][0] * c[0] - inv[1][1] * c[1])
