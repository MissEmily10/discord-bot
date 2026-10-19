"""
Анимированные аватарки и баннеры профиля бота в фирменном стиле
(смайлики из scripts/render_smileys.py, иконки из render_ui3d / render_doodles3d).

Запуск из корня проекта:
    python3 scripts/render_profile.py            # всё
    python3 scripts/render_profile.py avatar_main profile_banner   # выборочно

Рисует panel_art/profile/*.gif:
- avatar_*.gif — 512×512 (главная: 2D-смайлик в глюке сменяется 3D);
- profile_banner.png / .gif — баннер профиля 680×240 (обычный и анимированный).
Аватар Discord обрезает кругом, а слева снизу на баннер ложится аватарка —
главное держим справа. Баннеры экранов (1200×300) — scripts/render_banners.py.
"""

import math
import pathlib
import random
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import art3d  # noqa: E402
import render_smileys as rs  # noqa: E402
from art3d import ICE, INK, LIME, MAROON, MILK, PINK, WINE  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "panel_art" / "profile"
FONT = str(ROOT / "fonts" / "Inter-Bold.otf")
AV = 512
BW, BH = 750, 300
FRAME_MS = 50


# ---------- смайлики и иконки (с кэшем) ----------

_flat = {}
_lifted = {}


def flat(name):
    if name not in _flat:
        _flat[name] = rs.flat_smiley(name)
    return _flat[name]


def solid(name, phase=0.0, amp=1.0, sx=1.0):
    """3D-смайлик 512×512, покачивание по фазе (0..1) — кэшируется."""
    key = (name, round(phase * 24) % 24, round(amp, 2), round(sx, 2))
    if key not in _lifted:
        a = 2 * math.pi * key[1] / 24
        t = (-6 + 7 * amp * math.sin(a), 0.94, -0.08 + 0.1 * amp * math.cos(a), max(0.06, abs(sx)))
        _lifted[key] = art3d.lift(flat(name), t=t)
    return _lifted[key]


def icon(path):
    return Image.open(ROOT / "panel_art" / "emoji" / path).convert("RGBA")


def fit(img, size):
    return img.resize((size, size), Image.LANCZOS)


def put(canvas, img, cx, cy, size=None):
    if size:
        img = fit(img, size)
    canvas.alpha_composite(img, (int(cx - img.width / 2), int(cy - img.height / 2)))


# ---------- фон, плашка, текст ----------

def brand_bg(w, h, kind="wine", seed=3):
    """Размытый фон как на баннерах: серо-голубой верх, бордо, тёмный низ."""
    palettes = {
        "wine": [(150, 160, 172), (120, 82, 94), (74, 30, 40), (40, 26, 30)],
        "lime": [(30, 36, 20), (60, 84, 30), (110, 160, 40), (40, 52, 24)],
        "ice": [(196, 210, 222), (150, 164, 180), (110, 118, 132), (70, 74, 86)],
        "dark": [(44, 40, 46), (60, 40, 50), (34, 30, 36), (24, 22, 26)],
        "milk": [(244, 240, 236), (236, 230, 228), (230, 224, 222), (222, 216, 216)],
    }
    cols = palettes[kind]
    grad = Image.linear_gradient("L").resize((w, h))
    img = Image.new("RGB", (w, h), cols[0])
    for i in range(1, len(cols)):
        lo, hi = (i - 1) / (len(cols) - 1), i / (len(cols) - 1)
        m = grad.point(lambda v, lo=lo, hi=hi: int(max(0, min(1, (v / 255 - lo) / (hi - lo))) * 255))
        img = Image.composite(Image.new("RGB", (w, h), cols[i]), img, m)
    rng = random.Random(seed)
    blobs = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(blobs)
    for _ in range(5):
        x, y, r = rng.uniform(0, w), rng.uniform(0, h), rng.uniform(0.2, 0.45) * max(w, h)
        col = rng.choice(cols)
        d.ellipse((x - r, y - r * 0.6, x + r, y + r * 0.6), fill=col + (90,))
    blobs = blobs.filter(ImageFilter.GaussianBlur(max(w, h) * 0.08))
    out = img.convert("RGBA")
    out.alpha_composite(blobs)
    return out


def glass(canvas, box, radius=22, dots=True, alpha=34):
    x0, y0, x1, y1 = box
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(box, radius=radius, fill=(255, 255, 255, alpha), outline=(255, 255, 255, 70), width=1)
    canvas.alpha_composite(layer)
    if dots:
        d = ImageDraw.Draw(canvas)
        for i, col in enumerate((WINE, LIME, ICE)):
            cx, cy, r = x1 - 46 + i * 16, max(y0, 0) + 18, 4
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col)


def font(size):
    return ImageFont.truetype(FONT, int(size))


SLOT_VISIBLE = 0.84   # видимая часть смайлика от размера его картинки
SLOT_GAP = 0.05       # зазор между смайликом и соседними буквами, доля кегля


def char_layout(text, size, track=-0.075, slots=(), scale=1.0):
    """
    Раскладка букв: [(буква, x, ширина, размер смайлика или 0)]. Буква со
    смайликом получает ширину смайлика + зазоры, чтобы не касаться соседей.
    """
    f = font(size)
    out = []
    pen = 0.0
    for i, ch in enumerate(text):
        adv = f.getlength(ch)
        if i in slots:
            sprite = size * 0.78 * scale * 1.25
            width = max(adv, sprite * SLOT_VISIBLE) + 2 * size * SLOT_GAP
            out.append((ch, pen, width, sprite))
            pen += width + size * track
        else:
            out.append((ch, pen, adv, 0))
            pen += adv + size * track
    return out


def text_width(text, size, track=-0.075, slots=(), scale=1.0):
    chars = char_layout(text, size, track, slots, scale)
    return chars[-1][1] + chars[-1][2] if chars else 0


def fit_size(text, max_w, size, track=-0.075, slots=(), scale=1.0):
    """Кегль, при котором текст влезает в max_w."""
    while size > 20 and text_width(text, size, track, slots, scale) > max_w:
        size -= 2
    return size


def text_run(canvas, text, x, y, size, colors=None, track=-0.075, smiley_at=None, smiley=None, shadow=True, smiley_scale=1.0):
    """
    Фирменный текст: жирные строчные с плотным трекингом. colors — цвет каждой
    буквы (список) или один цвет; smiley_at — индексы букв «o», вместо которых
    рисуется смайлик smiley (картинка). y — базовая линия.
    """
    f = font(size)
    cols = colors if isinstance(colors, list) else [colors or MILK] * len(text)
    slots_idx = set(smiley_at or ()) if smiley is not None else set()
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    slots = []
    end = x
    for i, (ch, off, width, sprite) in enumerate(char_layout(text, size, track, slots_idx, smiley_scale)):
        if sprite:
            slots.append((x + off + width / 2, y - size * 0.27, sprite))
        else:
            d.text((x + off, y), ch, font=f, fill=cols[i] + (255,), anchor="ls")
        end = x + off + width
    if shadow:
        sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        sh.putalpha(layer.getchannel("A").filter(ImageFilter.GaussianBlur(size * 0.06)).point(lambda v: v * 0.35))
        canvas.alpha_composite(sh, (0, int(size * 0.03)))
    canvas.alpha_composite(layer)
    for cx, cy, s in slots:
        put(canvas, smiley, cx, cy, int(s))
    return end


def stretch_colors(text, start, end, phase=0.0, palette=(MILK, PINK, MAROON, WINE)):
    """Цвета для растянутых букв: молочный по краям, бордо в середине; phase сдвигает волну."""
    cols = []
    n = max(1, end - start)
    for i in range(len(text)):
        if start <= i < end:
            k = (i - start) / n
            v = 0.5 - 0.5 * math.cos(2 * math.pi * (k - phase))
            v = v ** 1.4
            idx = v * (len(palette) - 1)
            lo = int(idx)
            hi = min(len(palette) - 1, lo + 1)
            f = idx - lo
            cols.append(tuple(int(palette[lo][c] + (palette[hi][c] - palette[lo][c]) * f) for c in range(3)))
        else:
            cols.append(MILK)
    return cols


# ---------- глюк ----------

_scan = {}


def scanlines(size, alpha=26):
    if size not in _scan:
        m = Image.new("L", size, 0)
        d = ImageDraw.Draw(m)
        for y in range(0, size[1], 3):
            d.line((0, y, size[0], y), fill=alpha)
        _scan[size] = m
    return _scan[size]


def glitch(img, amt, rng):
    """Сдвиг полос, расслоение цветов (лайм / розовый), блоки шума, сканлайны."""
    if amt <= 0:
        return img
    w, h = img.size
    base = img.convert("RGBA")
    out = base.copy()
    # расслоение: силуэт картинки лаймом влево и розовым вправо
    dx = max(1, int(w * 0.018 * amt))
    lum = base.convert("L")
    for col, off in ((LIME, -dx), (PINK, dx)):
        tint = Image.new("RGBA", (w, h), col + (255,))
        tint.putalpha(ImageChops.multiply(lum, Image.new("L", (w, h), int(60 * min(1, amt)))))
        out.alpha_composite(_roll(tint, off, 0))
    out = Image.blend(out, base, 0.35)
    # полосы
    for _ in range(int(4 + 14 * amt)):
        y = rng.randrange(h)
        hh = rng.randrange(2, max(3, int(h * 0.07)))
        off = rng.randint(-int(w * 0.09 * amt) - 1, int(w * 0.09 * amt) + 1)
        band = out.crop((0, y, w, min(h, y + hh)))
        out.paste(_roll(band, off, 0), (0, y))
    # блоки
    d = ImageDraw.Draw(out)
    for _ in range(int(3 * amt)):
        x, y = rng.randrange(w), rng.randrange(h)
        bw, bh = rng.randrange(8, max(9, int(w * 0.18))), rng.randrange(3, max(4, int(h * 0.04)))
        d.rectangle((x, y, x + bw, y + bh), fill=rng.choice((LIME, PINK, ICE, MILK, INK)) + (int(140 + 100 * rng.random()),))
    dark = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    dark.putalpha(scanlines((w, h), int(20 + 30 * amt)))
    out.alpha_composite(dark)
    return out


def _roll(img, dx, dy):
    w, h = img.size
    dx %= w
    out = Image.new(img.mode, img.size)
    out.paste(img.crop((w - dx, 0, w, h)), (0, 0))
    out.paste(img.crop((0, 0, w - dx, h)), (dx, 0))
    return out


def band_mix(a, b, p, rng, step=(6, 40)):
    """Полосами показать b вместо a (p — доля полос b)."""
    out = a.copy()
    y = 0
    while y < a.height:
        hh = rng.randrange(*step)
        if rng.random() < p:
            out.paste(b.crop((0, y, b.width, y + hh)), (0, y))
        y += hh
    return out


def ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


# ---------- сохранение ----------

def save_gif(frames, name, ms=FRAME_MS):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pal = [f.convert("RGB").quantize(colors=192, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)
           for f in frames]
    path = OUT_DIR / f"{name}.gif"
    pal[0].save(path, save_all=True, append_images=pal[1:], duration=ms, loop=0, disposal=1, optimize=False)
    print(f"{path.relative_to(ROOT)}: {len(frames)} кадров, {path.stat().st_size // 1024} КБ")


# ============================================================
# АВАТАРКИ 512×512
# ============================================================

def _avatar_bg():
    bg = brand_bg(AV, AV, "wine", seed=5)
    glow = Image.new("RGBA", (AV, AV), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((90, 80, 430, 420), fill=PINK + (60,))
    bg.alpha_composite(glow.filter(ImageFilter.GaussianBlur(60)))
    return bg


def _shadow(canvas, cx, cy, w, alpha=90):
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse((cx - w / 2, cy - w * 0.09, cx + w / 2, cy + w * 0.09), fill=(10, 6, 8, alpha))
    canvas.alpha_composite(sh.filter(ImageFilter.GaussianBlur(10)))


def avatar_main():
    """2D фирменный смайлик → глюк → 3D → глюк → 2D."""
    rng = random.Random(1)
    bg = _avatar_bg()
    n = 80
    frames = []
    for i in range(n):
        f = bg.copy()
        flat_f = bg.copy()
        _shadow(flat_f, 256, 440, 300, 60)
        blink = "neutral" if i in (10, 11) else "smile"
        put(flat_f, flat(blink), 256, 250, 470)
        solid_f = bg.copy()
        _shadow(solid_f, 262, 446, 320)
        put(solid_f, solid("smile", i / 32), 256, 250 + 6 * math.sin(i / n * 4 * math.pi), 470)
        if i < 24:
            f = flat_f
        elif i < 34:
            p = ease((i - 24) / 10)
            f = glitch(band_mix(flat_f, solid_f, p, rng), 1.2 - abs(p - 0.5), rng)
        elif i < 64:
            f = solid_f
            if i in (48, 49):
                f = glitch(f, 0.35, rng)
        elif i < 74:
            p = ease((i - 64) / 10)
            f = glitch(band_mix(solid_f, flat_f, p, rng), 1.2 - abs(p - 0.5), rng)
        else:
            f = flat_f
        frames.append(f)
    save_gif(frames, "avatar_main")


def avatar_spin():
    """Монетка: смайлик крутится, на обороте — подмигивает; 2D и 3D чередуются."""
    bg = _avatar_bg()
    n = 48
    frames = []
    for i in range(n):
        a = 2 * math.pi * i / n
        sx = math.cos(a)
        name = "smile" if sx >= 0 else "wink"
        f = bg.copy()
        _shadow(f, 262, 446, 320 * max(0.25, abs(sx)))
        put(f, solid(name, 0.0, amp=0.3, sx=sx), 256, 250, 470)
        frames.append(f)
    save_gif(frames, "avatar_spin")


def avatar_moods():
    """Эмоции сменяются короткими вспышками глюка."""
    rng = random.Random(2)
    bg = _avatar_bg()
    moods = ["smile", "laugh", "love", "cool", "wink", "stars"]
    per = 14
    frames = []
    for k, name in enumerate(moods):
        nxt = moods[(k + 1) % len(moods)]
        for j in range(per):
            i = k * per + j
            f = bg.copy()
            _shadow(f, 262, 446, 320)
            put(f, solid(name, i / 24), 256, 250, 470)
            if j >= per - 3:
                g = bg.copy()
                _shadow(g, 262, 446, 320)
                put(g, solid(nxt, i / 24), 256, 250, 470)
                f = glitch(band_mix(f, g, (j - per + 4) / 4, rng), 0.9, rng)
            frames.append(f)
    save_gif(frames, "avatar_moods")


def avatar_bounce():
    """Лаймовый смайлик прыгает и сплющивается."""
    bg = brand_bg(AV, AV, "dark", seed=8)
    n = 32
    frames = []
    for i in range(n):
        p = i / n
        h = abs(math.sin(math.pi * p))
        squash = max(0.0, 1 - h * 4) * 0.18
        f = bg.copy()
        _shadow(f, 262, 452, 300 * (1 - 0.4 * h), int(110 * (1 - 0.6 * h)))
        img = fit(solid("smile_lime", p, amp=0.5), 330)
        img = img.resize((int(330 * (1 + squash)), int(330 * (1 - squash))), Image.LANCZOS)
        bottom = 478 - 130 * h
        f.alpha_composite(img, (int(256 - img.width / 2), int(bottom - img.height)))
        frames.append(f)
    save_gif(frames, "avatar_bounce")


def _spark(canvas, cx, cy, r, col, alpha=255):
    pts = [(cx + (r if k % 2 == 0 else r * 0.28) * math.cos(math.radians(-90 + 45 * k)),
            cy + (r if k % 2 == 0 else r * 0.28) * math.sin(math.radians(-90 + 45 * k))) for k in range(8)]
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).polygon(pts, fill=col + (alpha,))
    canvas.alpha_composite(layer)


def avatar_angel():
    """Ангел парит, вокруг мерцают искры."""
    bg = brand_bg(AV, AV, "ice", seed=11)
    n = 40
    sparks = [(90, 120, 0.0, MILK), (420, 150, 0.3, LIME), (110, 400, 0.6, LIME), (410, 380, 0.15, MILK), (250, 60, 0.8, PINK)]
    frames = []
    for i in range(n):
        p = i / n
        f = bg.copy()
        _shadow(f, 262, 456, 280 - 30 * math.sin(2 * math.pi * p), 70)
        put(f, solid("angel", p, amp=0.6), 256, 256 - 18 * math.sin(2 * math.pi * p), 450)
        for x, y, ph, col in sparks:
            s = math.sin(2 * math.pi * (p + ph))
            if s > 0:
                _spark(f, x, y, 26 * s, col)
        frames.append(f)
    save_gif(frames, "avatar_angel")


def avatar_crack():
    """Улыбка трескается, глюк — и снова целая."""
    rng = random.Random(4)
    bg = _avatar_bg()
    n = 60
    frames = []
    for i in range(n):
        name = "cracked" if 22 <= i < 46 else "smile"
        shake = (rng.uniform(-8, 8), rng.uniform(-6, 6)) if 20 <= i < 26 else (0, 0)
        f = bg.copy()
        _shadow(f, 262, 446, 320)
        put(f, solid(name, i / 30, amp=0.5), 256 + shake[0], 250 + shake[1], 470)
        if 20 <= i < 24 or 44 <= i < 49:
            f = glitch(f, 1.0, rng)
        frames.append(f)
    save_gif(frames, "avatar_crack")


# ============================================================
# БАННЕР ПРОФИЛЯ 680×240 (слот bot.profile_banner в Figma)
# ============================================================
# Слева снизу на баннер ложится аватарка (~ круг 0..200 × 140..300 в масштабе
# 680×240 — примерно треть ширины снизу), поэтому смайлики — правее.

PW, PH = 680, 240
AVATAR_ZONE = (0, 120, 230, 240)
PROFILE_ROW = ["smile_lime", "cool", "love", "laugh", "wink"]


def _profile_slots():
    """Центры и размер смайликов ряда; проверка: не налезают друг на друга и на аватарку."""
    size = 88
    xs = [292 + k * 80 for k in range(len(PROFILE_ROW))]
    boxes = []
    for x in xs:
        vis = size * SLOT_VISIBLE / 2
        box = (x - vis, 112 - vis, x + vis, 112 + vis)
        if box[0] < 248 or box[2] > 652:   # внутри стекла 240..660
            raise ValueError("bot.profile_banner: смайлики не влезают")
        a = AVATAR_ZONE
        if box[0] < a[2] and box[3] > a[1]:
            raise ValueError("bot.profile_banner: смайлик под аватаркой")
        for other in boxes:
            if box[0] < other[2] - size * 0.02:
                raise ValueError("bot.profile_banner: смайлики налезают друг на друга")
        boxes.append(box)
    return xs, size


def _profile_bg():
    bg = brand_bg(PW, PH, "wine", seed=5)
    glass(bg, (240, 22, 660, 206), radius=24, alpha=34)
    return bg


def profile_banner():
    """Обычный (статичный) баннер профиля: ряд 3D-смайликов на стекле."""
    xs, size = _profile_slots()
    f = _profile_bg()
    for k, (x, name) in enumerate(zip(xs, PROFILE_ROW)):
        _shadow(f, x + 4, 168, size * 0.8, 70)
        put(f, solid(name, k * 0.17, amp=0.5), x, 112, size)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "profile_banner.png"
    f.convert("RGB").save(path, optimize=True)
    print(f"{path.relative_to(ROOT)}: {path.stat().st_size // 1024} КБ")


def profile_banner_animated():
    """Тот же ряд, смайлики по очереди подпрыгивают."""
    xs, size = _profile_slots()
    bg = _profile_bg()
    n = 40
    frames = []
    for i in range(n):
        p = i / n
        f = bg.copy()
        for k, (x, name) in enumerate(zip(xs, PROFILE_ROW)):
            hop = max(0.0, math.sin(2 * math.pi * (p - k / 10))) ** 2
            _shadow(f, x + 4, 168, size * (0.8 - 0.2 * hop), int(70 - 30 * hop))
            put(f, solid(name, p + k * 0.17, amp=0.5), x, 112 - 14 * hop, size)
        frames.append(f)
    save_gif(frames, "profile_banner")


ALL = [avatar_main, avatar_spin, avatar_moods, avatar_bounce, avatar_angel, avatar_crack,
       profile_banner, profile_banner_animated]


def main():
    wanted = set(sys.argv[1:])
    for fn in ALL:
        if not wanted or fn.__name__ in wanted:
            fn()


if __name__ == "__main__":
    main()
