"""
clan_art.py
===========
Картинки кланов по макетам создателя бота из Figma («Кланы и новые экраны ·
заготовки»): карточка клана (10 стилей — clan.card.1…10), биллборд недели,
магазин, лидеры направлений, итоги месяца, квесты срока и «Всё о кланах».

Координаты, цвета и кегли сняты с макетов 1:1 (холст 1200 px шириной).
Заголовки и цифры — Coolvetica (fonts/banner), остальное — Inter, как в макетах.
Значки (badge.*) и баннеры (clans.council.banner, pictures.home.banner) —
готовые PNG из Figma в panel_art/clans, дудлы — panel_art/emoji/doodle3d.

Все функции возвращают PNG (bytes) и ничего не читают из базы — данные
собирают clan_hub / clan_billboard / clan_shop.
"""

import io
import math
import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(ROOT, "fonts")
ART_DIR = os.path.join(ROOT, "panel_art", "clans")
DOODLE_DIR = os.path.join(ROOT, "panel_art", "emoji", "doodle3d")
W = 1200

INK, MILK, LIME, ICE, PINK, ROSE, WINE = "#201c1e", "#f1eeec", "#b4ee3a", "#c6d8e6", "#e798b7", "#e3b3c0", "#7a1626"
BRAND_FONT = os.path.join(FONT_DIR, "banner", "Coolvetica-Regular.otf")

# значки: ключ награды -> файл из Figma
BADGE_FILES = {
    "place1": "badge.place1.png", "place2": "badge.place2.png", "place3": "badge.place3.png",
    "reliable": "badge.reliable.png", "help": "badge.help.png", "events": "badge.events.png",
    "art": "badge.creative.png", "dev": "badge.server.png", "compete": "badge.compete.png",
    "community": "badge.community.png",
}

# ============================================================
# СТИЛИ КАРТОЧКИ (clan.card.1 … clan.card.10)
# ============================================================
# pill: (заливка, текст, обводка или None); stat: (заливка, обводка, толщина, радиус, подпись, цифра)
# decor: ("doodle", имя, x, y, размер, поворот) | ("ink_circle", box) | ("stripes",) | ("checker",)
#        | ("dashes", [(x, y, w, цвет)]) | ("scan",)

STYLES = {
    1: dict(title="Вино-стекло", bg=("#4d1724", "#8c384d"), glows=((LIME, .12), (ICE, .14)),
            poster=("#f1eeec1a", "#c6d8e633", 1, 28), blur=True, shadow=True,
            emblem=("#b4ee3a2e", "#f1eeec59", "#f1eeeccc"), name=MILK,
            tag=(LIME, INK, None), status=(MILK, INK, None), known=(PINK, INK, None),
            stat=("#f1eeec1a", "#c6d8e633", 1, 24, "#f1eeeca6", MILK), team="#f1eeecd9", badges="#f1eeecbf",
            decor=()),
    2: dict(title="Ночь", bg=INK, glows=((WINE, .12), (LIME, .14)),
            poster=("#f1eeec0d", "#f1eeec1f", 1, 28),
            emblem=("#b4ee3a26", LIME, LIME), name=MILK,
            tag=(LIME, INK, None), status=(ICE, INK, None), known=(ROSE, INK, None),
            stat=("#f1eeec0f", "#f1eeec1a", 1, 24, "#aaa2a4e6", MILK), team="#aaa2a4e6", badges="#aaa2a4e6",
            decor=(("doodle", "moon", 960, 90, 110, -10), ("doodle", "sparkles", 1060, 170, 70, 0),
                   ("doodle", "star", 1010, 520, 80, 12))),
    3: dict(title="Салатовый взрыв", bg=LIME,
            poster=("#201c1e0a", "#201c1e26", 2, 28),
            emblem=(INK, INK, LIME), name=INK,
            tag=(INK, LIME, None), status=(MILK, INK, INK), known=(ROSE, INK, INK),
            stat=(INK, None, 0, 24, "#f1eeecbf", LIME), team="#f1eeecbf", badges="#344614e6",
            decor=(("ink_circle", (983, 524, 1210, 761)), ("doodle", "crown", 1038, 533, 114, -8))),
    4: dict(title="Наклейки", bg=MILK,
            poster=("#ffffff", INK, 3, 28), hard_shadow=True,
            emblem=(LIME, INK, INK), name=INK,
            tag=(LIME, INK, INK), status=(ICE, INK, INK), known=(ROSE, INK, INK),
            stat=("#e8e3e0", INK, 2, 24, "#6e6668e6", INK), team="#6e6668e6", badges="#6e6668e6",
            decor=(("doodle", "star", 950, 80, 100, -12), ("doodle", "rocket", 1050, 120, 100, 14),
                   ("doodle", "heart", 865, 553, 80, 8), ("doodle", "gem", 963, 514, 80, -6),
                   ("doodle", "trophy", 1048, 538, 84, 6))),
    5: dict(title="Пудра", bg="#f6dee4",
            poster=("#ffffff73", "#7a162626", 1, 28),
            emblem=(ROSE, WINE, WINE), name=WINE,
            tag=(WINE, "#f6dee4", None), status=("#ffffff", WINE, None), known=(ICE, INK, None),
            stat=("#eeced6", None, 0, 24, "#965a68e6", WINE), team="#965a68e6", badges="#965a68e6",
            decor=(("doodle", "flower", 990, 90, 120, 8), ("doodle", "cherries", 1040, 515, 90, -6))),
    6: dict(title="Небо", bg=(ICE, "#f4f8fc"),
            poster=("#ffffff80", "#54687c33", 1, 28),
            emblem=(ICE, INK, INK), name=INK,
            tag=(LIME, INK, INK), status=("#ffffff", INK, None), known=(ROSE, INK, None),
            stat=("#ffffffb3", None, 0, 24, "#54687ce6", INK), team="#54687ce6", badges="#54687ce6",
            decor=(("doodle", "cloud", 960, 114, 130, 0), ("doodle", "moon", 1060, 80, 80, -10),
                   ("doodle", "planet", 990, 515, 110, 8))),
    7: dict(title="Бордо-герб", bg=("#922236", WINE), glows=((ROSE, .12), (WINE, .14)),
            poster=("#6410208c", "#e3b3c04d", 1, 28),
            emblem=("#641020", ROSE, ROSE), name="#f6dee4",
            tag=(LIME, INK, None), status=("#f6dee4", WINE, None), known=(ROSE, WINE, None),
            stat=("#922236", "#e3b3c040", 1, 24, "#e3b3c0e6", "#f6dee4"), team="#e3b3c0e6", badges="#e3b3c0e6",
            decor=(("doodle", "crown", 1008, 101, 110, -6), ("doodle", "medal", 1038, 493, 90, -9))),
    8: dict(title="Полосы", bg=MILK,
            poster=("#ffffff", INK, 3, 12),
            emblem=(LIME, INK, INK), name=INK,
            tag=(INK, LIME, None), status=(LIME, INK, INK), known=(ROSE, INK, INK),
            stat=("#e8e3e0", INK, 2, 10, "#6e6668e6", INK), team="#6e6668e6", badges="#6e6668e6",
            decor=(("stripes",), ("doodle", "bolt", 990, 103, 100, 10))),
    9: dict(title="Шашки", bg=MILK,
            poster=("#ffffff", "#201c1e26", 2, 28),
            emblem=(INK, INK, LIME), name=INK,
            tag=(LIME, INK, INK), status=(INK, MILK, None), known=(ROSE, INK, None),
            stat=("#e8e3e0", None, 0, 24, "#6e6668e6", INK), team="#6e6668e6", badges="#6e6668e6",
            decor=(("checker",), ("doodle", "dice", 990, 90, 110, 12), ("doodle", "gamepad", 1070, 160, 70, -8))),
    10: dict(title="Глитч", bg="#161418",
             poster=("#f1eeec0a", "#b4ee3a40", 1, 28),
             emblem=("#28242c", LIME, LIME), name=MILK, glitch=True,
             tag=(LIME, INK, None), status=(ROSE, INK, None), known=(ICE, INK, None),
             stat=("#28242c", "#e3b3c033", 1, 24, "#96929ce6", MILK), team="#96929ce6", badges="#96929ce6",
             decor=(("scan",), ("dashes", ((880, 540, 120, LIME), (960, 580, 60, ROSE), (1030, 610, 90, LIME),
                                           (980, 110, 80, ROSE), (1060, 150, 50, LIME))))),
}
DEFAULT_STYLE = 1


def style_names():
    return {number: style["title"] for number, style in STYLES.items()}


# ============================================================
# ОСНОВА
# ============================================================

def rgba(color, alpha=None):
    """'#rrggbb' / '#rrggbbaa' -> (r, g, b, a)."""
    value = color.lstrip("#")
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    a = int(value[6:8], 16) if len(value) == 8 else 255
    if alpha is not None:
        a = int(round(a * alpha))
    return r, g, b, a


@lru_cache(maxsize=64)
def font(size, weight="Bold"):
    """weight: Regular / SemiBold / Bold (Inter) или Brand — Coolvetica, шрифт заголовков макетов."""
    path = BRAND_FONT if weight == "Brand" else os.path.join(FONT_DIR, f"Inter-{weight}.otf")
    if not os.path.isfile(path):
        path = os.path.join(FONT_DIR, "Inter-Bold.otf")
    return ImageFont.truetype(path, max(8, int(size)))


@lru_cache(maxsize=128)
def _image(path):
    try:
        return Image.open(path).convert("RGBA")
    except (OSError, ValueError):
        return None


def doodle(name):
    return _image(os.path.join(DOODLE_DIR, f"d3_{name}.png"))


def badge_image(key):
    file = BADGE_FILES.get(key)
    return _image(os.path.join(ART_DIR, file)) if file else None


def text_width(text, size, weight="Bold", track=0.0):
    f = font(size, weight)
    if not track:
        return f.getlength(text)
    return sum(f.getlength(ch) for ch in text) + track * size * max(0, len(text) - 1)


def fit(text, max_w, size, weight="Bold", track=0.0, min_size=12):
    while size > min_size and text_width(text, size, weight, track) > max_w:
        size -= 2
    return size


def plain(label):
    """«🤝 Помощь» -> «Помощь»: эмодзи в шрифте нет."""
    label = str(label or "")
    head, _, rest = label.partition(" ")
    return rest if rest and not any(ch.isalnum() for ch in head) else label


def ellipsize(text, max_w, size, weight="Regular"):
    if text_width(text, size, weight) <= max_w:
        return text
    while text and text_width(text + "…", size, weight) > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


class Canvas:
    def __init__(self, h, bg):
        self.h = h
        if isinstance(bg, tuple):
            top, bottom = rgba(bg[0]), rgba(bg[1])
            column = Image.new("RGBA", (1, h))
            for y in range(h):
                k = y / max(1, h - 1)
                column.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * k) for i in range(4)))
            self.img = column.resize((W, h))
        else:
            self.img = Image.new("RGBA", (W, h), rgba(bg))

    def _overlay(self):
        return Image.new("RGBA", self.img.size, (0, 0, 0, 0))

    def glow(self, color, alpha, box, blur):
        layer = self._overlay()
        ImageDraw.Draw(layer).ellipse(box, fill=rgba(color, alpha))
        self.img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))

    def glows(self, pair):
        if pair:
            self.glow(pair[0][0], pair[0][1], (-140, -140, 280, 280), 120)
            self.glow(pair[1][0], pair[1][1], (980, -100, 1340, 260), 110)

    def blur_under(self, box, radius, amount=24):
        x0, y0, x1, y1 = [int(v) for v in box]
        region = self.img.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(amount / 2))
        mask = Image.new("L", region.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, x1 - x0 - 1, y1 - y0 - 1), radius, fill=255)
        self.img.paste(region, (x0, y0), mask)

    def shadow(self, box, radius, dx=0, dy=18, blur=40, color="#00000026"):
        layer = self._overlay()
        x0, y0, x1, y1 = box
        ImageDraw.Draw(layer).rounded_rectangle((x0 + dx, y0 + dy, x1 + dx, y1 + dy), radius, fill=rgba(color))
        self.img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur / 2)) if blur else layer)

    def rect(self, box, radius, fill=None, stroke=None, width=0):
        layer = self._overlay()
        draw = ImageDraw.Draw(layer)
        draw.rounded_rectangle(box, radius, fill=rgba(fill) if fill else None,
                               outline=rgba(stroke) if stroke and width else None, width=int(width or 0))
        self.img.alpha_composite(layer)

    def ellipse(self, box, fill=None, stroke=None, width=0):
        layer = self._overlay()
        ImageDraw.Draw(layer).ellipse(box, fill=rgba(fill) if fill else None,
                                      outline=rgba(stroke) if stroke and width else None, width=int(width or 0))
        self.img.alpha_composite(layer)

    def text(self, x, y, text, size, color, weight="Bold", track=0.0, anchor="la"):
        """Строка текста; x — левый край (anchor la), правый (ra) или центр (ma). -> ширина."""
        f = font(size, weight)
        width = text_width(text, size, weight, track)
        if anchor[0] == "r":
            x -= width
        elif anchor[0] == "m":
            x -= width / 2
        layer = self._overlay()
        draw = ImageDraw.Draw(layer)
        fill = rgba(color)
        if not track:
            draw.text((x, y), text, font=f, fill=fill)
        else:
            cx = x
            for ch in text:
                draw.text((cx, y), ch, font=f, fill=fill)
                cx += f.getlength(ch) + track * size
        self.img.alpha_composite(layer)
        return width

    def runs(self, x, y, parts, size, weight="Bold", track=0.0):
        """Строка из кусков разного цвета: [(текст, цвет), …]."""
        for text, color in parts:
            x += self.text(x, y, text, size, color, weight, track) + (track * size if track else 0)
        return x

    def brand(self, x, y, text, size, color, max_w, anchor="la"):
        size = fit(text, max_w, size, weight="Brand")
        self.text(x, y, text, size, color, weight="Brand", anchor=anchor)
        return size

    def pill(self, x, y, text, fill, fg, stroke=None, size=26, h=51, pad=22, align="left"):
        """Плашка-пилюля; -> правый край."""
        tw = text_width(text, size)
        w = tw + 2 * pad
        if align == "right":
            x -= w
        self.rect((x, y, x + w, y + h), h / 2, fill=fill, stroke=stroke, width=3 if stroke else 0)
        self.text(x + pad, y + (h - size * 1.21) / 2, text, size, fg)
        return x + w

    def paste(self, im, cx, cy, size, angle=0):
        if im is None:
            return
        sprite = im.resize((int(size), int(size)), Image.LANCZOS)
        if angle:
            sprite = sprite.rotate(angle, resample=Image.BICUBIC, expand=True)
        self.img.alpha_composite(sprite, (int(cx - sprite.width / 2), int(cy - sprite.height / 2)))

    def dots(self, colors=(PINK, LIME, MILK)):
        for i, color in enumerate(colors):
            x = 1090 + i * 22
            self.ellipse((x, 56, x + 12, 68), fill=color)

    def poster(self, h, style=None):
        """Стеклянная плашка макета (poster 32,32 → 1168)."""
        fill, stroke, width, radius = (style or STYLES[1])["poster"]
        box = (32, 32, 1168, 32 + h)
        if (style or {}).get("hard_shadow"):
            self.rect((40, 40, 1176, 40 + h), radius, fill=INK)
        elif style is None or style.get("shadow"):
            self.shadow(box, radius)
        if style is None or style.get("blur"):
            self.blur_under(box, radius)
        self.rect(box, radius, fill=fill, stroke=stroke, width=width)
        return box

    def png(self):
        buffer = io.BytesIO()
        self.img.convert("RGB").save(buffer, "PNG", optimize=True)
        return buffer.getvalue()


def _glass(c, h):
    """Общий фон экранов клана (вино-стекло, как clans.billboard/shop/…)."""
    c.glows(((LIME, .12), (ICE, .14)))
    c.poster(h)


GLASS_TILE = ("#f1eeec1a", "#c6d8e633")


def tile(c, box, radius=24, fill=GLASS_TILE[0], stroke=GLASS_TILE[1]):
    c.blur_under(box, radius)
    c.rect(box, radius, fill=fill, stroke=stroke, width=1)


# ============================================================
# КАРТОЧКА КЛАНА
# ============================================================

def _decor(c, style):
    for item in style.get("decor", ()):
        kind = item[0]
        if kind == "doodle":
            _, name, x, y, size, angle = item
            c.paste(doodle(name), x + size / 2, y + size / 2, size, angle)
        elif kind == "ink_circle":
            c.ellipse(item[1], fill=INK)
        elif kind == "stripes":
            box = (820, 543, 1130, 617)
            band = Image.new("RGBA", (310, 74), rgba(LIME))
            draw = ImageDraw.Draw(band)
            for i in range(-6, 14):
                x = i * 34
                draw.polygon([(x, 74), (x + 16, 74), (x + 16 + 43, 0), (x + 43, 0)], fill=rgba(INK))
            mask = Image.new("L", band.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, 309, 73), 10, fill=255)
            c.img.paste(band, box[:2], mask)
            c.rect(box, 10, stroke=INK, width=3)
        elif kind == "checker":
            for row, y in enumerate((520, 556, 592)):
                for col in range(4 if row != 1 else 3):
                    x = (900 if row == 1 else 860) + col * 80
                    c.rect((x, y, x + 40, y + 36), 0, fill=INK)
        elif kind == "dashes":
            for x, y, w, color in item[1]:
                c.rect((x, y, x + w, y + 6), 0, fill=color)
        elif kind == "scan":
            layer = c._overlay()
            draw = ImageDraw.Draw(layer)
            for y in range(0, c.h, 6):
                draw.rectangle((0, y, W, y + 1), fill=(0, 0, 0, 77))
            c.img.alpha_composite(layer)


def render_card(data, style_no=DEFAULT_STYLE):
    """
    Карточка клана 1200×675. data: name, tag, status, known, members, treasury,
    streak, mult, respect, leader, deputies [имена], reps [имена], badges [ключи
    BADGE_FILES], emblem (PIL.Image или None).
    """
    style = STYLES.get(int(style_no or 1), STYLES[DEFAULT_STYLE])
    c = Canvas(675, style["bg"])
    c.glows(style.get("glows"))
    c.poster(611, style)
    c.dots()
    # герб
    e_fill, e_stroke, e_text = style["emblem"]
    c.ellipse((73, 77, 243, 247), fill=e_fill, stroke=e_stroke, width=3)
    emblem = data.get("emblem")
    if emblem is not None:
        size = 150
        im = emblem.convert("RGBA").resize((size, size), Image.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        c.img.paste(im, (83, 87), mask)
    else:
        label = str(data.get("tag") or "?")
        c.text(158, 162 - 26, label, fit(label, 130, 52), e_text, anchor="ma")
    # название (правее — декор стиля)
    name = str(data.get("name") or data.get("tag") or "")
    limit = 1090 - 271 if not any(d[0] == "doodle" and d[3] < 260 for d in style.get("decor", ())) else 940 - 271
    size = fit(name, limit, 92, weight="Brand", min_size=40)
    top = 73 + (115 - size * 1.21) / 2
    if style.get("glitch"):
        c.text(277, top, name, size, "#e3b3c0d9", weight="Brand")
        c.text(265, top, name, size, "#b4ee3ad9", weight="Brand")
    c.text(271, top, name, size, style["name"], weight="Brand")
    # метки
    x = 271
    for key, text in (("tag", f"[{data.get('tag', '')}]"), ("status", data.get("status")),
                      ("known", f"чем известен: {data['known']}" if data.get("known") else "")):
        if not text:
            continue
        fill, fg, stroke = style[key]
        size = 26 if x + text_width(text, 26) + 44 < 1120 else fit(text, 1120 - x - 44, 26, min_size=16)
        if x + text_width(text, size) + 44 > 1125:
            break
        x = c.pill(x, 200, text, fill, fg, stroke, size=size) + 12
    # цифры
    s_fill, s_stroke, s_w, s_r, s_label, s_value = style["stat"]
    stats = (("участников", str(data.get("members", "0")), None),
             ("казна", _num(data.get("treasury", 0)), None),
             (f"огонёк · ×{data.get('mult', 1):g}", str(data.get("streak", 0)), "fire"),
             ("респекты", _num(data.get("respect", 0)), None))
    for i, (label, value, icon) in enumerate(stats):
        x0 = 73 + i * 267.7
        box = (x0, 275, x0 + 252, 407)
        if style.get("blur"):
            c.blur_under(box, s_r)
        c.rect(box, s_r, fill=s_fill, stroke=s_stroke, width=s_w)
        c.text(x0 + 23, 298, label, 22, s_label, weight="Regular")
        vx = x0 + 23
        if icon:
            c.paste(doodle("fire"), vx + 22, 358, 44)
            vx += 50
        c.text(vx, 331, value, fit(value, x0 + 230 - vx, 44, weight="Brand"), s_value, weight="Brand")
    # команда
    box = (73, 431, 1127, 506)
    if style.get("blur"):
        c.blur_under(box, s_r)
    c.rect(box, s_r, fill=s_fill, stroke=s_stroke, width=s_w)
    team = _team_line(data)
    size = fit(team, 1008, 24, "Regular", min_size=17)
    c.text(96, 454 + (29 - size * 1.21) / 2, ellipsize(team, 1008, size), size, style["team"], weight="Regular")
    # значки
    c.text(73, 549, "значки", 22, style["badges"], weight="Regular")
    shown = [key for key in data.get("badges") or () if badge_image(key) is not None]
    low_decor = any(d[0] in ("ink_circle", "stripes", "checker", "dashes") or (d[0] == "doodle" and d[3] > 400)
                    for d in style.get("decor", ()))
    right = 840 if low_decor else 1120
    if not shown:
        c.text(159, 549, "пока нет — места месяца и направления", 22, style["badges"], weight="Regular")
    for i, key in enumerate(shown):
        x = 159 + i * 76
        if x + 64 > right:
            break
        c.paste(badge_image(key), x + 32, 562, 64)
    _decor(c, style)
    return c.png()


def _team_line(data):
    parts = [f"глава {data.get('leader') or '—'}"]
    if data.get("deputies"):
        parts.append("замы " + ", ".join(data["deputies"]))
    if data.get("reps"):
        parts.append("представители " + ", ".join(data["reps"]))
    return "  ·  ".join(parts)


def _num(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{value:,.0f}".replace(",", " ") if abs(value) >= 1000 or value == int(value) else f"{value:g}"


# ============================================================
# БИЛЛБОРД НЕДЕЛИ
# ============================================================

def render_billboard(rows, week, time_left):
    """
    rows: [{name, tag, messages, voice_min, respect, points, delta}] по местам.
    week — «20–27 окт», time_left — «2 дня».
    """
    rows = list(rows)[:8]
    count = max(1, len(rows))
    h = 186 + count * 90 - 12 + 24 + 32
    c = Canvas(h, ("#4d1724", "#8c384d"))
    _glass(c, h - 64)
    c.dots((PINK, LIME))
    size = c.brand(73, 73 + 8, "billbooooard", 84, MILK, 430)
    x = 73 + text_width("billbooooard", size, weight="Brand") + 18
    x = c.pill(x, 101, f"неделя {week}", LIME, INK, size=22, h=45, pad=18) + 18
    c.pill(x, 101, f"до закрытия {time_left}", MILK, INK, size=22, h=45, pad=18)
    if not rows:
        tile(c, (73, 186, 1127, 264), 18)
        c.text(96, 210, "кланов пока нет — биллборд оживёт с первым кланом", 24, "#f1eeecb3", weight="Regular")
        return c.png()
    top_points = max((float(row.get("points") or 0) for row in rows), default=0) or 1
    for i, row in enumerate(rows):
        place = i + 1
        y = 186 + i * 90
        fill = "#f1eeec24" if place <= 3 else "#f1eeec12"
        c.blur_under((73, y, 1127, y + 78), 18)
        c.rect((73, y, 1127, y + 78), 18, fill=fill, stroke="#c6d8e62e", width=1)
        color = LIME if place == 1 else PINK if place <= 3 else MILK
        c.text(94, y + 13, str(place), 40, color, weight="Brand")
        x = 131
        if place == 1:
            c.paste(badge_image("place1"), 152, y + 37, 56)
            x = 196
        title = f"{row.get('name') or ''}  [{row.get('tag')}]"
        c.text(x, y + 14, ellipsize(title, 780 - x, 24, "Bold"), 24, MILK)
        sub = (f"{_num(row.get('messages', 0))} сообщ. · {_num(row.get('voice_min', 0))} мин в войсе · "
               f"{_num(row.get('respect', 0))} респ.")
        c.text(x, y + 45, ellipsize(sub, 780 - x, 16, "Regular"), 16, "#f1eeec99", weight="Regular")
        share = max(0.04, float(row.get("points") or 0) / top_points)
        c.rect((797, y + 37, 947, y + 41), 2, fill="#f1eeec1f")
        c.rect((797, y + 37, 797 + 150 * share, y + 41), 2, fill="#b4ee3acc")
        points = _num(row.get("points", 0))
        c.text(1106, y + 9, points, fit(points, 140, 34, weight="Brand"), LIME, weight="Brand", anchor="ra")
        if row.get("delta"):
            c.text(1106, y + 50, str(row["delta"]), 16, "#f1eeec99", weight="Regular", anchor="ra")
    return c.png()


# ============================================================
# МАГАЗИН
# ============================================================

def render_shop(items, treasury):
    """items: [(название, цена)] — в порядке витрины."""
    items = list(items)[:12]
    rows = max(1, math.ceil(len(items) / 4))
    h = 286 + rows * 117 - 13 + 21 + 64 + 32
    h = max(h, 675)
    c = Canvas(h, ("#4d1724", "#8c384d"))
    _glass(c, h - 64)
    c.dots((PINK, LIME))
    size = fit("clan shoooooooop", 900, 100, weight="Brand")
    c.runs(73, 73 + (120 - size * 1.21) / 2, (("clan ", MILK), ("shoooooooop", LIME)), size, weight="Brand")
    x = c.pill(73, 209, f"казна {_num(treasury)}", LIME, INK, size=24, h=53) + 12
    c.pill(x, 209, "чем побалуем клан сегодня? листай ассортимент!", MILK, INK,
           size=fit("чем побалуем клан сегодня? листай ассортимент!", 1127 - x - 44, 24, min_size=16), h=53)
    for i, (label, price) in enumerate(items):
        col, row = i % 4, i // 4
        x0, y0 = 73 + col * 267, 286 + row * 117
        tile(c, (x0, y0, x0 + 253, y0 + 104))
        c.text(x0 + 17, y0 + 13, ellipsize(label, 220, 20, "Regular"), 20, "#f1eeecbf", weight="Regular")
        c.text(x0 + 17, y0 + 41, _num(price), 40, MILK, weight="Brand")
    return c.png()


# ============================================================
# ЛИДЕРЫ НАПРАВЛЕНИЙ
# ============================================================

def render_leaders(leaders):
    """leaders: [(подпись направления, тег лидера или '', сроков подряд)] — 6 штук."""
    h = 650
    c = Canvas(h, ("#4d1724", "#8c384d"))
    _glass(c, h - 64)
    c.dots((PINK, LIME))
    c.brand(73, 73, "лидеры направлений", 72, MILK, 1000)
    fills = (LIME, ICE, PINK)
    for i, (label, tag, streak) in enumerate(list(leaders)[:6]):
        label = plain(label)
        col, row = i % 3, i // 3
        x0, y0 = 73 + col * 356.5, 189 + row * 204
        tile(c, (x0, y0, x0 + 341, y0 + 176))
        c.pill(x0 + 23, y0 + 23, f"[{tag}]" if tag else "свободно", fills[col], INK)
        c.text(x0 + 23, y0 + 82, ellipsize(label, 300, 24), 24, MILK)
        sub = f"держит статус {streak} мес. подряд" if tag and streak else ("ещё никто" if not tag else "впервые")
        c.text(x0 + 23, y0 + 119, sub, 20, "#f1eeeca6", weight="Regular")
    return c.png()


# ============================================================
# ИТОГИ МЕСЯЦА
# ============================================================

PRIZES = ("роль отдельно + продление + градиент + 85%", "роль отдельно + бесплатное продление", "роль отдельно")


def render_month(month, top, leaders):
    """top: [(название, тег, баллы)] до 3; leaders: [(ключ направления, тег)]."""
    h = 675
    c = Canvas(h, ("#4d1724", "#8c384d"))
    _glass(c, h - 64)
    c.dots((PINK, LIME))
    c.brand(73, 73, f"итоги {month}", 88, MILK, 1000)
    fills = (LIME, ICE, PINK)
    for i in range(3):
        x0 = 73 + i * 366
        box = (x0, 189, x0 + 350, 456)
        if i == 0:
            c.blur_under(box, 24)
            c.rect(box, 24, fill=GLASS_TILE[0], stroke=LIME, width=3)
        else:
            tile(c, box)
        c.pill(x0 + 27, 214, f"{i + 1} место", fills[i], INK)
        if i < len(top):
            name, tag, points = top[i]
            title = name or tag
            c.text(x0 + 27, 273, title, fit(title, 300, 56, weight="Brand"), MILK, weight="Brand")
            c.text(x0 + 27, 348, f"[{tag}] · {_num(points)} баллов", 22, "#f1eeecb3", weight="Regular")
        else:
            c.text(x0 + 27, 273, "—", 56, "#f1eeec80", weight="Brand")
        c.text(x0 + 27, 383, ellipsize(PRIZES[i], 300, 18, "Regular"), 18, "#f1eeec8c", weight="Regular")
    tile(c, (73, 466, 1127, 590))
    c.text(96, 489, "лидеры направлений:", 20, "#f1eeeca6", weight="Regular")
    x = 96
    for key, tag in leaders:
        text = f"[{tag}]"
        w = text_width(text, 20) + 38 + 16 + 16
        if x + w > 1110:
            break
        c.rect((x, 525, x + w, 567), 21, fill="#f1eeec1f")
        c.paste(badge_image(key), x + 23, 546, 30)
        c.text(x + 46, 534, text, 20, MILK)
        x += w + 10
    return c.png()


# ============================================================
# КВЕСТЫ СРОКА
# ============================================================

def render_quests(term, quests):
    """quests: [(уровень «лёгкий/средний/сложный», название, статус)]."""
    h = 420
    c = Canvas(h, ("#4d1724", "#8c384d"))
    _glass(c, h - 64)
    c.dots((PINK, LIME))
    c.brand(73, 73, f"quests · {term}", 72, MILK, 1000)
    fills = (LIME, ICE, PINK)
    for i, (level, title, status) in enumerate(list(quests)[:3]):
        x0 = 73 + i * 356.5
        tile(c, (x0, 183, x0 + 341, 349))
        c.pill(x0 + 23, 206, level, fills[i], INK)
        size = fit(title, 300, 24, min_size=16)
        c.text(x0 + 23, 265, ellipsize(title, 300, size, "Bold"), size, MILK)
        c.text(x0 + 23, 302, ellipsize(status, 300, 20, "Regular"), 20, "#f1eeeca6", weight="Regular")
    return c.png()


# ============================================================
# «ВСЁ О КЛАНАХ»
# ============================================================

def render_directory(clans_count, members_count):
    """Картинка над списком кланов: макет clans.directory с живыми цифрами в плашках."""
    base = _image(os.path.join(ART_DIR, "clans.directory.base.png"))
    if base is None:
        return None
    c = Canvas(base.height, MILK)
    c.img = base.copy()
    c.pill(624, 149, f"{clans_count} кланов", LIME, INK, INK, size=24, h=55, pad=25)
    c.pill(624, 212, f"{members_count} участников", ICE, INK, INK, size=24, h=55, pad=25)
    return c.png()


def art_bytes(name):
    """Готовая картинка из panel_art/clans (баннер Совета и т. п.)."""
    path = os.path.join(ART_DIR, name)
    try:
        with open(path, "rb") as file:
            return file.read()
    except OSError:
        return None


def preview_sheet(sample=None):
    """Все 10 стилей карточки одним листом (для выбора стиля) — 2 колонки по 600 px."""
    sample = sample or dict(name="Волки", tag="WOLF", status="активен", known="Ивенты", members="18 / 50",
                            treasury=12400, streak=3, mult=1.2, respect=37, leader="глава",
                            deputies=["зам 1", "зам 2"], reps=["предст. 1"], badges=["place1", "events", "reliable"])
    tiles = []
    for number in STYLES:
        im = Image.open(io.BytesIO(render_card(sample, number))).resize((600, 338), Image.LANCZOS)
        tiles.append(im)
    sheet = Image.new("RGB", (1220, 20 + 5 * 378), (32, 28, 30))
    draw = ImageDraw.Draw(sheet)
    for i, im in enumerate(tiles):
        x, y = 10 + (i % 2) * 605, 10 + (i // 2) * 378
        sheet.paste(im, (x, y + 30))
        draw.text((x + 4, y + 2), f"{i + 1}. {STYLES[i + 1]['title']}", font=font(22), fill=rgba(MILK))
    buffer = io.BytesIO()
    sheet.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


async def file(render, *args, filename="clan.png"):
    """Нарисовать в отдельном потоке (PIL не держит бота) -> discord.File или None."""
    import asyncio

    import discord
    try:
        data = await asyncio.to_thread(render, *args)
    except Exception:  # картинка — украшение: без неё панель всё равно работает
        import logging
        logging.getLogger(__name__).exception("clan art %s", getattr(render, "__name__", render))
        return None
    return discord.File(io.BytesIO(data), filename=filename) if data else None
