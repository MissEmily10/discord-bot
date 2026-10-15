"""
Готовые стили фона для баннера сервера (960×540): 10 минималистичных и
10 ярких. Цвета — палитра бота, наклейки — дудлы из panel_art/emoji.

Текст строк остаётся настраиваемым; у стиля свои цвета заголовка и подписи,
их бот ставит строкам при выборе стиля. Низ слева у ярких стилей свободен
от наклеек — туда по умолчанию встают строки.
"""

import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter

SIZE = (960, 540)
ROOT = os.path.dirname(os.path.abspath(__file__))
DOODLE_DIR = os.path.join(ROOT, "panel_art", "emoji")

INK = (32, 28, 30)
CREAM = (241, 238, 236)
MUTED = (110, 102, 104)
LIME = (180, 238, 58)
PINK = (227, 179, 192)
SOFT_PINK = (246, 222, 228)
BLUE = (198, 216, 230)
SOFT_BLUE = (226, 236, 244)
MAROON = (122, 22, 38)
WHITE = (255, 255, 255)


def _hex(color):
    return "#%02X%02X%02X" % color


def _canvas(color):
    return Image.new("RGBA", SIZE, color + (255,))


def _doodles(names=None):
    files = sorted(f for f in os.listdir(DOODLE_DIR) if f.startswith("doodle_") and f.endswith(".png")) \
        if os.path.isdir(DOODLE_DIR) else []
    if names:
        files = [f for f in files if f[:-4] in names] or files
    return [os.path.join(DOODLE_DIR, f) for f in files]


def _stickers(image, seed, count, size_range=(90, 150), keep_free=(0, 300, 620, 540), names=None, shadow=True):
    """Наклейки-дудлы вразброс, не залезая в зону текста keep_free."""
    rng = random.Random(seed)
    files = _doodles(names)
    if not files:
        return image
    placed = []
    tries = 0
    while len(placed) < count and tries < 400:
        tries += 1
        size = rng.randint(*size_range)
        x = rng.randint(-size // 4, SIZE[0] - size * 3 // 4)
        y = rng.randint(-size // 4, SIZE[1] - size * 3 // 4)
        box = (x, y, x + size, y + size)
        free = keep_free
        if box[0] < free[2] and box[2] > free[0] and box[1] < free[3] and box[3] > free[1]:
            continue
        if any(abs(x - px) < (size + ps) * 0.45 and abs(y - py) < (size + ps) * 0.45 for px, py, ps in placed):
            continue
        sticker = Image.open(rng.choice(files)).convert("RGBA").resize((size, size), Image.LANCZOS)
        sticker = sticker.rotate(rng.uniform(-18, 18), resample=Image.BICUBIC, expand=True)
        if shadow:
            alpha = sticker.getchannel("A").filter(ImageFilter.GaussianBlur(6))
            dark = Image.new("RGBA", sticker.size, (0, 0, 0, 0))
            dark.putalpha(alpha.point(lambda v: v * 0.25))
            image.alpha_composite(dark, (x + 6, y + 8))
        image.alpha_composite(sticker, (x, y))
        placed.append((x, y, size))
    return image


def _gradient(start, end, diagonal=True):
    image = Image.new("RGBA", SIZE)
    width, height = SIZE
    pixels = []
    for y in range(height):
        for x in range(width):
            p = ((x / width) * 0.65 + (y / height) * 0.35) if diagonal else x / width
            pixels.append(tuple(int(a + (b - a) * p) for a, b in zip(start, end)) + (255,))
    image.putdata(pixels)
    return image


def _round_rect(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


# ---------- минимализм ----------

def cream(_):
    return _canvas(CREAM)


def night(_):
    return _canvas(INK)


def lime_line(_):
    image = _canvas(CREAM)
    ImageDraw.Draw(image).rounded_rectangle((58, 52, 250, 66), radius=7, fill=LIME)
    return image


def soft_pink(_):
    return _canvas(SOFT_PINK)


def soft_blue(_):
    return _canvas(SOFT_BLUE)


def frame(_):
    image = _canvas(CREAM)
    _round_rect(ImageDraw.Draw(image), (24, 24, SIZE[0] - 24, SIZE[1] - 24), 36, outline=INK, width=6)
    return image


def split(_):
    image = _canvas(CREAM)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, SIZE[0] * 0.62, SIZE[1]), fill=INK)
    draw.ellipse((SIZE[0] * 0.62 - 40, SIZE[1] * 0.5 - 40, SIZE[0] * 0.62 + 40, SIZE[1] * 0.5 + 40), fill=LIME)
    return image


def dots(_):
    image = _canvas(CREAM)
    draw = ImageDraw.Draw(image)
    for y in range(30, SIZE[1], 38):
        for x in range(30, SIZE[0], 38):
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(222, 216, 212))
    return image


def bang(_):
    image = _canvas(CREAM)
    draw = ImageDraw.Draw(image)
    _round_rect(draw, (800, 60, 880, 380), 40, fill=BLUE)
    draw.ellipse((800, 410, 880, 490), fill=BLUE)
    return image


def one_sticker(_):
    image = _canvas(CREAM)
    files = _doodles({"doodle_star"})
    if files:
        sticker = Image.open(files[0]).convert("RGBA").resize((300, 300), Image.LANCZOS).rotate(12, expand=True,
                                                                                                resample=Image.BICUBIC)
        image.alpha_composite(sticker, (SIZE[0] - sticker.width - 40, 30))
    return image


# ---------- яркие ----------

def lime_pop(_):
    image = _canvas(LIME)
    draw = ImageDraw.Draw(image)
    draw.ellipse((620, -160, 1140, 360), fill=(160, 214, 40))
    draw.ellipse((760, 260, 1060, 560), fill=INK)
    return image


def maroon(_):
    image = _canvas(MAROON)
    draw = ImageDraw.Draw(image)
    draw.ellipse((640, -120, 1100, 340), fill=(146, 34, 54))
    return _stickers(image, 7, 4, (110, 160), names={"doodle_heart", "doodle_cherries", "doodle_flower",
                                                       "doodle_gift", "doodle_balloon"})


def stickers(_):
    return _stickers(_canvas(CREAM), 11, 12)


def stickers_night(_):
    return _stickers(_canvas(INK), 23, 12)


def pink_blue(_):
    return _stickers(_gradient(PINK, BLUE), 5, 5, (100, 150))


def lime_blue(_):
    return _gradient(LIME, BLUE)


def checker(_):
    image = _canvas(CREAM)
    draw = ImageDraw.Draw(image)
    cell = 90
    for row in range(SIZE[1] // cell + 1):
        for col in range(SIZE[0] // cell + 1):
            if (row + col) % 2 == 0:
                draw.rectangle((col * cell, row * cell, col * cell + cell, row * cell + cell), fill=LIME)
    _round_rect(draw, (30, 300, 700, 510), 30, fill=CREAM, outline=INK, width=5)
    return image


def circles(_):
    image = _canvas(CREAM)
    draw = ImageDraw.Draw(image)
    for box, color in (((600, -140, 960, 220), PINK), ((740, 170, 1060, 490), LIME), ((660, 330, 890, 560), BLUE),
                       ((880, -60, 1010, 70), MAROON)):
        draw.ellipse(box, fill=color)
    return image


def stripes(_):
    image = _canvas(INK)
    draw = ImageDraw.Draw(image)
    for n in range(-6, 20):
        x = n * 110
        draw.polygon([(x, 0), (x + 50, 0), (x + 50 - 300, SIZE[1]), (x - 300, SIZE[1])], fill=LIME)
    _round_rect(draw, (30, 300, 720, 510), 30, fill=INK)
    return image


def burst(_):
    image = _canvas(PINK)
    draw = ImageDraw.Draw(image)
    cx, cy = SIZE[0] + 40, -40
    for n in range(24):
        if n % 2:
            continue
        a1, a2 = math.radians(n * 15), math.radians(n * 15 + 15)
        draw.polygon([(cx, cy), (cx + 1600 * math.cos(a1), cy + 1600 * math.sin(a1)),
                      (cx + 1600 * math.cos(a2), cy + 1600 * math.sin(a2))], fill=(236, 196, 207))
    draw.ellipse((cx - 170, cy - 170, cx + 170, cy + 170), fill=MAROON)
    return image


# key -> (название, яркий ли, рисовалка, цвет заголовка, цвет подписи)
STYLES = {
    "cream": ("Сливочный", False, cream, INK, MUTED),
    "night": ("Ночной", False, night, CREAM, (176, 168, 170)),
    "lime_line": ("Салатовая черта", False, lime_line, INK, MUTED),
    "soft_pink": ("Нежно-розовый", False, soft_pink, MAROON, (150, 90, 104)),
    "soft_blue": ("Нежно-голубой", False, soft_blue, INK, (84, 104, 124)),
    "frame": ("Рамка", False, frame, INK, MUTED),
    "split": ("Половинки", False, split, CREAM, LIME),
    "dots": ("Точки", False, dots, INK, MUTED),
    "bang": ("Восклицание", False, bang, INK, MUTED),
    "one_sticker": ("Одна наклейка", False, one_sticker, INK, MUTED),
    "lime_pop": ("Салатовый взрыв", True, lime_pop, INK, (52, 70, 20)),
    "maroon": ("Бордо", True, maroon, SOFT_PINK, PINK),
    "stickers": ("Наклейки", True, stickers, INK, MUTED),
    "stickers_night": ("Наклейки ночью", True, stickers_night, CREAM, LIME),
    "pink_blue": ("Розово-голубой", True, pink_blue, INK, MAROON),
    "lime_blue": ("Салат и небо", True, lime_blue, INK, (52, 70, 90)),
    "checker": ("Шашки", True, checker, INK, MUTED),
    "circles": ("Кружки", True, circles, INK, MUTED),
    "stripes": ("Полосы", True, stripes, CREAM, LIME),
    "burst": ("Лучи", True, burst, MAROON, (150, 60, 80)),
}

_cache = {}


def render_base(key):
    if key not in STYLES:
        key = "cream"
    if key not in _cache:
        _cache[key] = STYLES[key][2](key)
    return _cache[key].copy()


def colors(key):
    _, _, _, title, sub = STYLES.get(key, STYLES["cream"])
    return _hex(title), _hex(sub)
