"""
Баннеры для новостей 1200×600 — 20 шаблонов разного типа и композиции в
фирменном стиле (как баннеры экранов): размытый винный/ледяной/лаймовый фон,
стеклянные плашки с тремя точками, жирный строчный текст с растянутыми
буквами, 3D-смайлики и 3D-иконки, плашки-«кнопки» с толстым боком.

Запуск из корня проекта:
    python3 scripts/render_news.py                # все 20
    python3 scripts/render_news.py event poll     # выборочно

Рисует panel_art/news/news.<шаблон>.png (с примером текста) и
panel_art/news/_all.png (все шаблоны на одном листе).

Тексты — параметры: render("event", title="…", date_day="31") — поэтому
шаблоны может рисовать и бот. Каждый шаблон проверяет раскладку: ничего не
выходит за баннер, текст, картинки и плашки не наезжают друг на друга;
длинный текст уменьшается, а если не влезает и так — ошибка, файл не пишется.
"""

import math
import pathlib
import random
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import render_banners as rb  # noqa: E402
import render_profile as rp  # noqa: E402
from art3d import ICE, INK, LIME, MAROON, MILK, PINK, WINE, dark, light  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "panel_art" / "news"
W, H = 1200, 600
FONTS = ROOT / "fonts"
SOFT = (226, 218, 216)          # молочный потише — для обычного текста
LIME_WAVE, ICE_WAVE, PINK_WAVE = rb.LIME_WAVE, rb.ICE_WAVE, rb.PINK_WAVE
INK_WAVE = (INK, (90, 30, 50), MAROON, WINE)     # для светлого фона


# ---------- раскладка ----------

class Layout:
    """Занятые прямоугольники: пересечения запрещены (кроме своей группы)."""

    def __init__(self, name):
        self.name = name
        self.boxes = []

    def add(self, kind, box, group=None, inside=None):
        x0, y0, x1, y1 = [int(v) for v in box]
        if x0 < 0 or x1 > W or y0 < 0 or y1 > H:
            raise ValueError(f"{self.name}: «{kind}» выходит за баннер {box}")
        if inside is not None:
            a = inside
            if x0 < a[0] or y0 < a[1] or x1 > a[2] or y1 > a[3]:
                raise ValueError(f"{self.name}: «{kind}» {box} вылезает из своей плашки {a}")
        for other_kind, other, other_group in self.boxes:
            if group is not None and group == other_group:
                continue
            if x0 < other[2] and other[0] < x1 and y0 < other[3] and other[1] < y1:
                raise ValueError(f"{self.name}: «{kind}» {box} наезжает на «{other_kind}» {other}")
        self.boxes.append((kind, (x0, y0, x1, y1), group or kind))
        return (x0, y0, x1, y1)


_fonts = {}


def font(size, weight="Bold"):
    key = (int(size), weight)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(str(FONTS / f"Inter-{weight}.otf"), int(size))
    return _fonts[key]


# ---------- фон и плашки ----------

def bg(kind="wine", seed=3):
    return rp.brand_bg(W, H, kind, seed=seed)


def glass(img, box, radius=30, alpha=34, dots=True):
    rp.glass(img, box, radius=radius, alpha=alpha, dots=dots)


def plate(img, box, fill, radius=28, depth=12, outline=INK, width=5):
    """Плашка-«кнопка»: толстый тёмный бок снизу и фирменная обводка."""
    x0, y0, x1, y1 = box
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x0, y0 + depth, x1, y1 + depth), radius=radius, fill=dark(fill, 0.62), outline=outline, width=width)
    d.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=fill, outline=outline, width=width)
    # блик сверху
    d.rounded_rectangle((x0 + width + 6, y0 + width + 4, x1 - width - 6, y0 + width + 10), radius=4,
                        fill=light(fill, 0.35))


def plate_box(box, depth=12):
    return (box[0], box[1], box[2], box[3] + depth)


def shadow(img, box, radius=30, blur=18, alpha=90, dy=14):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    x0, y0, x1, y1 = box
    ImageDraw.Draw(layer).rounded_rectangle((x0, y0 + dy, x1, y1 + dy), radius=radius, fill=(10, 6, 8, alpha))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


# ---------- текст ----------

def word(lay, img, kind, text, x, base, size, limit, letter=None, palette=PINK_WAVE, phase=0.3,
         smiley_at=(), smiley=None, color=MILK, center=False, min_size=40):
    """Фирменная надпись (растянутые буквы letter перекрашены волной). x — левый край или центр."""
    slots = set(smiley_at) if smiley else set()
    max_w = limit - x if not center else limit
    size = rp.fit_size(text, max_w, size, slots=slots)
    if size < min_size:
        raise ValueError(f"{lay.name}: «{kind}» не влезает даже кеглем {size}")
    if center:
        x = x - rp.text_width(text, size, slots=slots) / 2
    box = rb.text_box(text, x, base, size, slots=slots)
    if slots:
        for ch, off, width, sprite in rp.char_layout(text, size, -0.075, slots):
            if sprite:
                cy, vis = base - size * 0.27, sprite * rp.SLOT_VISIBLE / 2
                box = (box[0], min(box[1], int(cy - vis)), box[2], max(box[3], int(cy + vis)))
    lay.add(kind, box)
    if letter:
        a, b = rb.run(text, letter)
        cols = rp.stretch_colors(text, a, b, phase=phase, palette=palette)
        cols = [c if a <= i < b else color for i, c in enumerate(cols)]
    else:
        cols = color
    rp.text_run(img, text, x, base, size, cols, smiley_at=slots, smiley=smiley, shadow=color != INK)
    return box


def wrap(text, f, max_w):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for w in para.split(" "):
            test = (cur + " " + w).strip()
            if f.getlength(test) <= max_w or not cur:
                cur = test
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def para(lay, img, kind, text, box, size, color=SOFT, weight="SemiBold", max_lines=3, align="left",
         leading=1.22, min_size=22, valign="top"):
    """Абзац в рамке box: перенос по словам, кегль уменьшается, пока не влезет."""
    x0, y0, x1, y1 = box
    while True:
        f = font(size, weight)
        lines = wrap(text, f, x1 - x0)
        lh = size * leading
        too_wide = any(f.getlength(ln) > x1 - x0 for ln in lines)
        if len(lines) <= max_lines and lh * len(lines) <= y1 - y0 + size * (leading - 1) and not too_wide:
            break
        size -= 2
        if size < min_size:
            raise ValueError(f"{lay.name}: «{kind}» не влезает в {box}")
    total = lh * (len(lines) - 1) + size
    y = y0 if valign == "top" else (y0 + (y1 - y0 - total) / 2 if valign == "middle" else y1 - total)
    d = ImageDraw.Draw(img)
    used = []
    for i, ln in enumerate(lines):
        w = f.getlength(ln)
        x = x0 if align == "left" else ((x0 + x1 - w) / 2 if align == "center" else x1 - w)
        yy = y + i * lh
        d.text((x, yy), ln, font=f, fill=color, anchor="la")
        bb = d.textbbox((x, yy), ln, font=f, anchor="la")
        used.append(bb)
    real = (min(b[0] for b in used), min(b[1] for b in used), max(b[2] for b in used), max(b[3] for b in used))
    lay.add(kind, real, inside=(x0 - 1, y0 - size * 0.5, x1 + 1, y1 + size * 0.4))
    return real


def chip(lay, img, kind, text, x, y, fill=MILK, fg=INK, size=26, icon=None, align="left", depth=6):
    """Плашка-пилюля с текстом (дата, тег, время). x — левый край (или правый при align='right')."""
    f = font(size, "Bold")
    tw = f.getlength(text)
    ih = int(size * 1.25) if icon else 0
    w = tw + size * 1.4 + (ih + size * 0.35 if icon else 0)
    h = size * 1.85
    if align == "right":
        x -= w
    elif align == "center":
        x -= w / 2
    box = (x, y, x + w, y + h)
    lay.add(kind, (x, y, x + w, y + h + depth))
    plate(img, box, fill, radius=h / 2, depth=depth, width=3)
    tx = x + size * 0.7
    if icon:
        rp.put(img, rp.icon(icon), tx + ih / 2, y + h / 2, ih)
        tx += ih + size * 0.35
    ImageDraw.Draw(img).text((tx, y + h / 2), text, font=f, fill=fg, anchor="lm")
    return box


def sprite(lay, img, kind, im, cx, cy, size, angle=0, pad=0.1):
    """3D-смайлик или иконка; в раскладку — видимая часть (без прозрачных полей)."""
    s = rp.fit(im, int(size))
    if angle:
        s = s.rotate(angle, resample=Image.BICUBIC)
    rp.put(img, s, cx, cy)
    p = size * pad
    return lay.add(kind, (cx - size / 2 + p, cy - size / 2 + p, cx + size / 2 - p, cy + size / 2 - p))


def face(name, phase=0.15, amp=0.6):
    return rp.solid(name, phase, amp=amp)


def ic(path):
    return rp.icon(path)


def sparkle(img, cx, cy, r, col=MILK, alpha=255):
    rp._spark(img, cx, cy, r, col, alpha)


# ============================================================
# ШАБЛОНЫ. У каждого — тексты по умолчанию (пример) и функция рисования.
# ============================================================

T = {}


def template(key, label, **defaults):
    def deco(fn):
        T[key] = (label, defaults, fn)
        return fn
    return deco


@template("news", "Новость — классика", tag="#новости", date="19.10", title="Открыли новый канал для артов",
          text="Кидайте свои рисунки в #арты — лучшие попадут в баннер сервера каждую пятницу.")
def t_news(lay, d):
    img = bg("wine", 2)
    glass(img, (40, 40, 1160, 560))
    chip(lay, img, "тег", d["tag"], 84, 78, fill=LIME)
    chip(lay, img, "дата", d["date"], 84 + font(26).getlength(d["tag"]) + 70, 78, fill=MILK)
    word(lay, img, "надпись", "neeeeews", 80, 300, 190, 790, letter="e", palette=PINK_WAVE)
    sprite(lay, img, "смайлик", face("stars"), 975, 230, 270)
    para(lay, img, "заголовок", d["title"], (84, 340, 1100, 420), 52, color=MILK, weight="Bold", max_lines=1)
    para(lay, img, "текст", d["text"], (84, 436, 1100, 530), 30, max_lines=2)
    return img


@template("update", "Обновление бота", version="v2.4", title="Что нового в Purr",
          items="Стили живых картинок: стекло, 3D и глитч\nКнопка «Изменить» прямо под картинкой\nБаннер сервера обновляется сам",
          date="19.10.2026")
def t_update(lay, d):
    img = bg("ice", 7)
    glass(img, (40, 40, 1160, 560))
    word(lay, img, "надпись", "updaaaate", 80, 196, 160, 760, letter="a", palette=ICE_WAVE)
    box = (850, 86, 1110, 216)
    lay.add("версия", plate_box(box))
    plate(img, box, LIME, radius=34)
    ImageDraw.Draw(img).text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), d["version"], font=font(78), fill=INK,
                             anchor="mm")
    sprite(lay, img, "искры", ic("doodle3d/d3_sparkles.png"), 1070, 470, 110, angle=-10)
    para(lay, img, "заголовок", d["title"], (84, 236, 800, 296), 46, color=MILK, weight="Bold", max_lines=1)
    icons = ["ui3d/u3_add.png", "ui3d/u3_edit.png", "ui3d/u3_refresh.png", "ui3d/u3_star.png"]
    for i, line in enumerate(d["items"].split("\n")[:4]):
        y = 330 + i * 64
        sprite(lay, img, f"иконка {i}", ic(icons[i]), 112, y + 22, 62)
        para(lay, img, f"пункт {i}", line, (160, y, 1110, y + 46), 32, color=MILK, max_lines=1)
    chip(lay, img, "дата", d["date"], 1110, 500 if len(d["items"].split("\n")) < 3 else 250, fill=MILK, align="right",
         size=22)
    return img


@template("event", "Ивент с датой", day="31", month="ОКТ", weekday="пятница", title="Хэллоуин-вечеринка",
          text="Костюмы, мафия и страшилки в войсе. Лучший костюм получит роль на месяц.", time="20:00 МСК",
          place="#войс-ивенты")
def t_event(lay, d):
    img = bg("wine", 4)
    cal = (70, 70, 410, 500)
    shadow(img, cal)
    lay.add("календарь", plate_box(cal, 14))
    plate(img, cal, MILK, radius=36, depth=14)
    dd = ImageDraw.Draw(img)
    dd.rounded_rectangle((cal[0], cal[1], cal[2], cal[1] + 110), radius=36, fill=MAROON, outline=INK, width=5)
    dd.rectangle((cal[0] + 5, cal[1] + 70, cal[2] - 5, cal[1] + 108), fill=MAROON)
    dd.line((cal[0], cal[1] + 110, cal[2], cal[1] + 110), fill=INK, width=5)
    for k in (0.3, 0.7):    # кольца календаря
        x = cal[0] + (cal[2] - cal[0]) * k
        dd.rounded_rectangle((x - 12, cal[1] - 26, x + 12, cal[1] + 26), radius=12, fill=SOFT, outline=INK, width=4)
    dd.text(((cal[0] + cal[2]) / 2, cal[1] + 60), d["month"], font=font(54), fill=MILK, anchor="mm")
    dd.text(((cal[0] + cal[2]) / 2, cal[1] + 255), d["day"], font=font(200), fill=INK, anchor="mm")
    dd.text(((cal[0] + cal[2]) / 2, cal[3] - 52), d["weekday"], font=font(34, "SemiBold"), fill=MAROON, anchor="mm")
    glass(img, (460, 40, 1160, 560))
    word(lay, img, "надпись", "eveeeent", 500, 210, 160, 1010, letter="e", palette=PINK_WAVE)
    sprite(lay, img, "шарик", ic("doodle3d/d3_balloon.png"), 1080, 150, 120, angle=8)
    para(lay, img, "заголовок", d["title"], (504, 250, 1120, 310), 48, color=MILK, weight="Bold", max_lines=1)
    para(lay, img, "текст", d["text"], (504, 326, 1120, 430), 29, max_lines=3)
    c = chip(lay, img, "время", d["time"], 504, 456, fill=LIME, icon="ui3d/u3_time.png")
    chip(lay, img, "место", d["place"], c[2] + 18, 456, fill=PINK, icon="ui3d/u3_announce.png")
    return img


@template("giveaway", "Розыгрыш", prize="Nitro на месяц", text="Нажми на подарок под постом — победителя выберет бот.",
          until="до 25.10", winners="3 победителя")
def t_giveaway(lay, d):
    img = bg("lime", 4)
    glass(img, (40, 40, 1160, 560), alpha=24)
    word(lay, img, "надпись", "giveaaaaway", 600, 200, 170, 1000, letter="a", palette=LIME_WAVE, center=True)
    sprite(lay, img, "подарок", ic("doodle3d/d3_gift.png"), 230, 390, 260, angle=-8)
    sparkle(img, 105, 270, 16, LIME)
    sparkle(img, 360, 280, 12, MILK)
    para(lay, img, "приз", d["prize"], (390, 260, 1000, 336), 64, color=LIME, weight="Bold", max_lines=1)
    para(lay, img, "текст", d["text"], (392, 350, 960, 430), 30, color=MILK, max_lines=2)
    c = chip(lay, img, "срок", d["until"], 392, 456, fill=MILK, icon="ui3d/u3_time.png")
    chip(lay, img, "победители", d["winners"], c[2] + 18, 456, fill=LIME, icon="ui3d/u3_trophy.png")
    sprite(lay, img, "смайлик", face("love"), 1060, 420, 170)
    return img


def stripes(img, box, a=LIME, b=INK, step=46):
    x0, y0, x1, y1 = box
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rectangle(box, fill=b)
    h = y1 - y0
    for x in range(x0 - h, x1 + h, step * 2):
        d.polygon([(x, y1), (x + step, y1), (x + step + h, y0), (x + h, y0)], fill=a)
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).rectangle(box, fill=255)
    layer.putalpha(rp.ImageChops.multiply(layer.getchannel("A"), m))
    img.alpha_composite(layer)


@template("alert", "Важное объявление", title="Сервер переезжает на новые роли",
          text="С понедельника старые роли перестанут выдаваться. Загляни в #роли и выбери новые.")
def t_alert(lay, d):
    img = bg("dark", 6)
    stripes(img, (0, 0, W, 54))
    stripes(img, (0, H - 54, W, H))
    lay.add("полосы сверху", (0, 0, W, 54))
    lay.add("полосы снизу", (0, H - 54, W, H))
    glass(img, (40, 90, 1160, 510), dots=False)
    sprite(lay, img, "мегафон", ic("doodle3d/d3_megaphone.png"), 175, 300, 220, angle=-12)
    i = "attention!".index("o")
    word(lay, img, "надпись", "attention!", 320, 250, 150, 1120, smiley_at=(i,), smiley=face("shock", amp=0.8))
    para(lay, img, "заголовок", d["title"], (324, 280, 1120, 344), 46, color=LIME, weight="Bold", max_lines=1)
    para(lay, img, "текст", d["text"], (324, 360, 1120, 470), 30, max_lines=3)
    return img


def starburst(img, cx, cy, r, fill, n=14, angle=0):
    pts = []
    for i in range(n * 2):
        rr = r if i % 2 == 0 else r * 0.8
        a = math.radians(angle + 180 * i / n)
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    d = ImageDraw.Draw(img)
    d.polygon([(x + 6, y + 12) for x, y in pts], fill=dark(fill, 0.62), outline=INK, width=5)
    d.polygon(pts, fill=fill, outline=INK, width=5)


@template("feature", "Новая функция (светлый)", name="Своя слэш-команда",
          text="Создай команду для любой живой картинки — /казна +60, и картинка в канале перерисуется сама.",
          how="/pictures → Живые картинки → Своя команда")
def t_feature(lay, d):
    img = bg("milk", 3)
    sb = (1000, 150)
    starburst(img, *sb, 120, LIME, angle=8)
    lay.add("значок new", (sb[0] - 132, sb[1] - 132, sb[0] + 132, sb[1] + 144))
    rot = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    ImageDraw.Draw(rot).text((150, 150), "new!", font=font(74), fill=INK, anchor="mm")
    img.alpha_composite(rot.rotate(14, resample=Image.BICUBIC), (sb[0] - 150, sb[1] - 150))
    word(lay, img, "надпись", "feeeature", 80, 210, 170, 840, letter="e", palette=INK_WAVE, color=INK)
    para(lay, img, "название", d["name"], (84, 250, 860, 316), 54, color=MAROON, weight="Bold", max_lines=1)
    para(lay, img, "текст", d["text"], (84, 334, 1110, 420), 30, color=(70, 60, 66), max_lines=2)
    box = (80, 452, 1120, 530)
    lay.add("подсказка", plate_box(box, 8))
    plate(img, box, MILK, radius=26, depth=8, width=4)
    rp.put(img, ic("ui3d/u3_play.png"), box[0] + 50, (box[1] + box[3]) / 2, 56)
    f = font(30, "SemiBold")
    t = d["how"]
    while f.getlength(t) > box[2] - box[0] - 120 and f.size > 18:
        f = font(f.size - 2, "SemiBold")
    ImageDraw.Draw(img).text((box[0] + 94, (box[1] + box[3]) / 2), t, font=f, fill=INK, anchor="lm")
    return img


@template("poll", "Голосование", question="Какой ивент в субботу?",
          options="Мафия:46\nКино-вечер:31\nКараоке:23", hint="голосуй кнопками под постом")
def t_poll(lay, d):
    img = bg("wine", 8)
    word(lay, img, "надпись", "voooote", 80, 190, 170, 560, letter="o", palette=PINK_WAVE)
    para(lay, img, "вопрос", d["question"], (84, 236, 520, 380), 50, color=MILK, weight="Bold", max_lines=3)
    sprite(lay, img, "смайлик", face("think"), 180, 470, 150)
    para(lay, img, "подсказка", d["hint"], (270, 440, 520, 520), 24, color=SOFT, max_lines=2, valign="middle")
    card = (580, 60, 1150, 540)
    glass(img, card, alpha=40)
    lay.add("карточка", card)
    opts = [o.rsplit(":", 1) for o in d["options"].split("\n")][:4]
    cols = [LIME, PINK, ICE, MILK]
    top = 130
    step = (card[3] - 40 - top) / len(opts)
    dd = ImageDraw.Draw(img)
    for i, (name, pct) in enumerate(opts):
        y = top + i * step
        pct = int(pct)
        dd.text((616, y + 10), name, font=font(34), fill=MILK, anchor="lm")
        dd.text((1112, y + 10), f"{pct}%", font=font(34), fill=cols[i], anchor="rm")
        bar = (616, y + 44, 1112, y + 84)
        dd.rounded_rectangle(bar, radius=20, fill=(255, 255, 255, 40), outline=(255, 255, 255, 80), width=2)
        fill_to = bar[0] + max(44, (bar[2] - bar[0]) * pct / 100)
        plate(img, (bar[0], bar[1], fill_to, bar[3]), cols[i], radius=20, depth=5, width=3)
    return img


@template("winners", "Победители", title="Итоги конкурса артов", first="Лиса", second="Кот", third="Ёжик")
def t_winners(lay, d):
    img = bg("wine", 11)
    glass(img, (40, 40, 1160, 560))
    word(lay, img, "надпись", "winneeeers", 600, 160, 140, 900, letter="e", palette=PINK_WAVE, center=True)
    para(lay, img, "заголовок", d["title"], (240, 186, 960, 232), 34, color=SOFT, max_lines=1, align="center")
    blocks = [(2, d["second"], PINK, 300, 380, "laugh"), (1, d["first"], LIME, 600, 330, "cool"),
              (3, d["third"], ICE, 900, 400, "wink")]
    dd = ImageDraw.Draw(img)
    for place, name, col, cx, top, mood in blocks:
        box = (cx - 130, top, cx + 130, 526)
        lay.add(f"пьедестал {place}", (box[0], box[1], box[2], 540))
        plate(img, box, col, radius=22, depth=14)
        dd.text((cx, top + 36), str(place), font=font(56), fill=INK, anchor="mm")
        f = font(34)
        while f.getlength(name) > 230:
            f = font(f.size - 2)
        dd.text((cx, top + 86), name, font=f, fill=INK, anchor="mm")
        if top + 86 + f.size * 0.6 > box[3] - 8:
            raise ValueError(f"{lay.name}: имя «{name}» вылезает из пьедестала")
        sprite(lay, img, f"смайлик {place}", face(mood), cx, top - 54, 120)
    sprite(lay, img, "кубок", ic("doodle3d/d3_trophy.png"), 1080, 300, 110, angle=10)
    sprite(lay, img, "звезда", ic("doodle3d/d3_star.png"), 120, 330, 90, angle=-12)
    return img


@template("maintenance", "Технические работы", title="Бот уходит на техработы",
          text="Обновляем базу и картинки. Команды временно не отвечают — всё вернётся само.",
          window="сегодня 03:00–05:00")
def t_maintenance(lay, d):
    img = bg("ice", 9)
    img = rp.glitch(img, 0.5, random.Random(5))
    glass(img, (40, 40, 1160, 560), alpha=30)
    i = "ooooops".index("o")
    word(lay, img, "надпись", "ooooops", 80, 220, 190, 720, letter="o", palette=ICE_WAVE, phase=0.4,
         smiley_at=(i,), smiley=face("cracked", amp=0.8))
    sprite(lay, img, "ключ", ic("doodle3d/d3_wrench.png"), 900, 170, 170, angle=-16)
    sprite(lay, img, "шестерёнка", ic("doodle3d/d3_gear.png"), 1060, 230, 130, angle=12)
    para(lay, img, "заголовок", d["title"], (84, 270, 1110, 336), 52, color=MILK, weight="Bold", max_lines=1)
    para(lay, img, "текст", d["text"], (84, 350, 1110, 440), 30, color=MILK, max_lines=2)
    chip(lay, img, "время", d["window"], 84, 464, fill=MILK, icon="ui3d/u3_time.png")
    return img


@template("welcome", "Добро пожаловать", count="+128", text="новых участников за неделю — привет, рады вам!")
def t_welcome(lay, d):
    img = bg("wine", 13)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((250, 120, 950, 600), fill=PINK + (70,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(90)))
    i = "welcooome".index("o")
    word(lay, img, "надпись", "welcooome", 600, 210, 190, 1080, letter="o", palette=PINK_WAVE, center=True,
         smiley_at=(i,), smiley=face("smile_lime"))
    moods = ["wink", "love", "laugh", "cool", "stars", "kiss", "smile_pink"]
    n = len(moods)
    for k, m in enumerate(moods):
        x = 150 + k * 900 / (n - 1)
        y = 330 + 34 * math.cos(math.pi * (k - (n - 1) / 2) / (n - 1) * 1.6) * -1 + 34
        sprite(lay, img, f"смайлик {k}", face(m, phase=k / n), x, y, 124)
    w = font(40).getlength(d["count"]) + 40 * 1.4
    f = font(30, "SemiBold")
    tw = min(f.getlength(d["text"]), 1000 - w)
    x = 600 - (w + 20 + tw) / 2
    img2 = img
    chip(lay, img2, "число", d["count"], x, 470, fill=LIME, size=40, depth=8)
    para(lay, img2, "текст", d["text"], (x + w + 20, 470, 1140, 545), 30, color=MILK, max_lines=2, valign="middle")
    return img2


def vertical_word(lay, img, text, x, size, palette, letter):
    """Надпись снизу вверх вдоль левого края."""
    layer = Image.new("RGBA", (H, H), (0, 0, 0, 0))
    tmp_lay = Layout(lay.name + " (вертикально)")
    globals_w, globals_h = W, H
    try:
        word(tmp_lay, layer, "надпись", text, 30, size + 10, size, H - 30, letter=letter, palette=palette)
    finally:
        pass
    _, (x0, y0, x1, y1), _ = tmp_lay.boxes[0]
    layer = layer.rotate(90, expand=True)
    # (x, y) в layer -> (y, H - x) после поворота
    img.alpha_composite(layer, (x - y0, 0))
    return lay.add("надпись", (x, H - x1, x + (y1 - y0), H - x0))


@template("rules", "Правила", title="Обновили правила сервера",
          items="Уважай всех в чате и войсе\nБез рекламы и спама в личку\nАрты — только с подписью автора\nСпорные вопросы — в #поддержка")
def t_rules(lay, d):
    img = bg("dark", 12)
    band = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(band).rectangle((0, 0, 210, H), fill=WINE + (210,))
    img.alpha_composite(band)
    vertical_word(lay, img, "ruuuules", 40, 150, LIME_WAVE, "u")
    sprite(lay, img, "щит", ic("doodle3d/d3_shield.png"), 1080, 100, 120, angle=10)
    para(lay, img, "заголовок", d["title"], (260, 54, 1000, 130), 52, color=MILK, weight="Bold", max_lines=1,
         valign="middle")
    items = d["items"].split("\n")[:5]
    top, bottom = 170, 560
    step = (bottom - top) / len(items)
    for i, it in enumerate(items):
        y = top + i * step
        h = step - 18
        card = (260, y, 1140, y + h)
        glass(img, card, radius=20, alpha=26, dots=False)
        lay.add(f"правило {i}", card)
        nb = (276, y + h / 2 - 26, 328, y + h / 2 + 22)
        plate(img, nb, LIME if i % 2 == 0 else PINK, radius=14, depth=4, width=3)
        ImageDraw.Draw(img).text(((nb[0] + nb[2]) / 2, (nb[1] + nb[3]) / 2), str(i + 1), font=font(30), fill=INK,
                                 anchor="mm")
        para(Layout("—"), img, "п", it, (350, y + 8, 1120, y + h - 8), 32, color=MILK, max_lines=1, valign="middle")
    return img


@template("live", "Стрим / эфир", title="Рисуем баннер сервера вместе", time="сегодня в 19:00",
          where="#стрим")
def t_live(lay, d):
    img = bg("dark", 14)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((-100, 200, 600, 700), fill=MAROON + (120,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(100)))
    word(lay, img, "надпись", "liiive", 70, 300, 200, 560, letter="i", palette=PINK_WAVE)
    c = chip(lay, img, "в эфире", "  В ЭФИРЕ", 76, 50, fill=MAROON, fg=MILK, size=28)
    ImageDraw.Draw(img).ellipse((c[0] + 24, (c[1] + c[3]) / 2 - 9, c[0] + 42, (c[1] + c[3]) / 2 + 9), fill=LIME)
    para(lay, img, "заголовок", d["title"], (76, 330, 560, 440), 46, color=MILK, weight="Bold", max_lines=2)
    t = chip(lay, img, "время", d["time"], 76, 470, fill=MILK, icon="ui3d/u3_time.png", size=24)
    chip(lay, img, "где", d["where"], t[2] + 16, 470, fill=PINK, size=24)
    scr = (610, 70, 1150, 470)
    shadow(img, scr)
    glass(img, scr, alpha=44)
    lay.add("экран", (scr[0], scr[1], scr[2], 530))
    sprite(Layout("экран"), img, "смайлик", face("cool"), 880, 250, 230)
    rp.put(img, ic("ui3d/u3_play.png"), 1080, 410, 80)
    dd = ImageDraw.Draw(img)
    dd.rounded_rectangle((660, 496, 1100, 512), radius=8, fill=(255, 255, 255, 60))
    plate(img, (660, 496, 930, 512), LIME, radius=8, depth=3, width=2)
    return img


@template("soon", "Скоро / отсчёт", caption="до старта нового сезона", days="02", hours="14", minutes="37")
def t_soon(lay, d):
    img = bg("wine", 16)
    glass(img, (40, 40, 1160, 560))
    word(lay, img, "надпись", "sooooon", 600, 210, 190, 900, letter="o", palette=PINK_WAVE, center=True)
    sprite(lay, img, "часы", ic("doodle3d/d3_hourglass.png"), 1040, 150, 140, angle=12)
    sprite(lay, img, "ракета", ic("doodle3d/d3_rocket.png"), 160, 150, 130, angle=-14)
    para(lay, img, "подпись", d["caption"], (200, 236, 1000, 290), 38, color=SOFT, max_lines=1, align="center")
    parts = [(d["days"], "дней", LIME), (d["hours"], "часов", PINK), (d["minutes"], "минут", ICE)]
    bw, gap = 250, 50
    x0 = 600 - (3 * bw + 2 * gap) / 2
    dd = ImageDraw.Draw(img)
    for k, (num, lab, col) in enumerate(parts):
        box = (x0 + k * (bw + gap), 320, x0 + k * (bw + gap) + bw, 500)
        lay.add(f"ячейка {k}", plate_box(box, 14))
        plate(img, box, col, radius=30, depth=14)
        dd.text(((box[0] + box[2]) / 2, box[1] + 76), num, font=font(110), fill=INK, anchor="mm")
        dd.text(((box[0] + box[2]) / 2, box[3] - 34), lab, font=font(30, "SemiBold"), fill=dark(col, 0.4), anchor="mm")
        if k < 2:
            dd.text((box[2] + gap / 2, 400), ":", font=font(90), fill=MILK, anchor="mm")
    return img


@template("breaking", "Срочно (бегущая строка)", title="Purr научился сам обновлять баннер сервера",
          text="Участники, войс и бусты — теперь цифры на баннере живые.")
def t_breaking(lay, d):
    img = bg("wine", 17)
    dd = ImageDraw.Draw(img)
    dd.rectangle((0, 0, W, 86), fill=INK)
    lay.add("лента сверху", (0, 0, W, 86))
    x = -40
    f = font(46)
    while x < W:
        dd.text((x, 43), "breaking", font=f, fill=LIME, anchor="lm")
        x += f.getlength("breaking") + 26
        sparkle(img, x, 43, 10, PINK)
        x += 26
    dd.rectangle((0, H - 70, W, H), fill=LIME)
    lay.add("лента снизу", (0, H - 70, W, H))
    x, f2 = 20, font(30)
    while x < W:
        dd.text((x, H - 35), "срочно · новости сервера ·", font=f2, fill=INK, anchor="lm")
        x += f2.getlength("срочно · новости сервера ·") + 20
    glass(img, (40, 120, 1160, 500))
    sprite(lay, img, "смайлик", face("shock", amp=0.8), 1020, 250, 220)
    para(lay, img, "заголовок", d["title"], (84, 160, 880, 350), 64, color=MILK, weight="Bold", max_lines=3)
    para(lay, img, "текст", d["text"], (84, 372, 900, 470), 30, max_lines=2)
    return img


@template("split", "Сплит: текст + смайлик", title="Горячая новость недели",
          text="Клановые карточки уже в Figma — скоро их будет рисовать бот.", tag="#кланы")
def t_split(lay, d):
    img = bg("wine", 18)
    left = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(left).rounded_rectangle((-40, -40, 680, H + 40), radius=60, fill=MILK + (255,))
    shadow(img, (-40, -40, 680, H + 40), radius=60, blur=24, dy=0)
    img.alpha_composite(left)
    chip(lay, img, "тег", d["tag"], 70, 60, fill=LIME, size=26)
    word(lay, img, "надпись", "hoooot", 66, 290, 200, 640, letter="o", palette=INK_WAVE, color=INK)
    para(lay, img, "заголовок", d["title"], (70, 320, 630, 390), 46, color=MAROON, weight="Bold", max_lines=1)
    para(lay, img, "текст", d["text"], (70, 404, 630, 540), 30, color=(70, 60, 66), max_lines=3)
    lay.add("край белой половины", (680, 0, 700, H))
    sprite(lay, img, "смайлик", face("cool", amp=0.9), 945, 300, 360)
    sprite(lay, img, "огонь", ic("doodle3d/d3_fire.png"), 1110, 96, 120, angle=12)
    sparkle(img, 760, 120, 18, LIME)
    sparkle(img, 1120, 520, 14, PINK)
    sparkle(img, 780, 500, 10, MILK)
    return img


@template("photo", "Фото-отчёт", title="Как прошёл хэллоуин", caption="31.10 · войс-ивенты",
          text="Сорок человек в костюмах, три раунда мафии и победа команды тыкв.", photo=None)
def t_photo(lay, d):
    img = bg("wine", 19)
    word(lay, img, "надпись", "recaaaap", 70, 200, 170, 640, letter="a", palette=PINK_WAVE)
    para(lay, img, "заголовок", d["title"], (74, 240, 640, 310), 50, color=MILK, weight="Bold", max_lines=1)
    para(lay, img, "текст", d["text"], (74, 326, 620, 440), 30, max_lines=3)
    sprite(lay, img, "камера", ic("doodle3d/d3_camera.png"), 140, 500, 110, angle=-10)
    # полароид
    pw, ph = 420, 480
    pol = Image.new("RGBA", (pw + 40, ph + 40), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pol)
    pd.rounded_rectangle((20, 20, 20 + pw, 20 + ph), radius=14, fill=MILK, outline=INK, width=5)
    hole = (46, 46, 20 + pw - 26, 20 + ph - 110)
    if d.get("photo"):
        ph_img = Image.open(d["photo"]).convert("RGBA")
        hw, hh = hole[2] - hole[0], hole[3] - hole[1]
        k = max(hw / ph_img.width, hh / ph_img.height)
        ph_img = ph_img.resize((int(ph_img.width * k) + 1, int(ph_img.height * k) + 1), Image.LANCZOS)
        ph_img = ph_img.crop(((ph_img.width - hw) // 2, (ph_img.height - hh) // 2,
                              (ph_img.width - hw) // 2 + hw, (ph_img.height - hh) // 2 + hh))
        pol.alpha_composite(ph_img, hole[:2])
    else:
        ph_bg = rp.brand_bg(hole[2] - hole[0], hole[3] - hole[1], "ice", seed=4)
        pol.alpha_composite(ph_bg, hole[:2])
        rp.put(pol, ic("ui3d/u3_image.png"), (hole[0] + hole[2]) / 2, (hole[1] + hole[3]) / 2 - 16, 110)
        pd.text(((hole[0] + hole[2]) / 2, (hole[1] + hole[3]) / 2 + 66), "фото здесь", font=font(26, "SemiBold"),
                fill=INK, anchor="mm")
    pd.rectangle(hole, outline=INK, width=4)
    f = font(32, "SemiBoldItalic")
    pd.text((20 + pw / 2, 20 + ph - 55), d["caption"], font=f, fill=MAROON, anchor="mm")
    rot = pol.rotate(-6, resample=Image.BICUBIC, expand=True)
    sh = Image.new("RGBA", rot.size, (0, 0, 0, 0))
    sh.putalpha(rot.getchannel("A").point(lambda v: v * 0.45))
    sh = sh.filter(ImageFilter.GaussianBlur(14))
    px, py = 690, (H - rot.height) // 2
    img.alpha_composite(sh, (px + 10, py + 18))
    img.alpha_composite(rot, (px, py))
    a = rot.getchannel("A").getbbox()
    lay.add("полароид", (px + a[0], max(0, py + a[1]), px + a[2], min(H, py + a[3])))
    # «скотч»
    tape = Image.new("RGBA", (150, 44), PINK + (190,))
    tape = tape.rotate(8, expand=True, resample=Image.BICUBIC)
    img.alpha_composite(tape, (px + rot.width // 2 - 60, max(0, py - 2)))
    return img


@template("quote", "Цитата", quote="Лучший сервер, где даже бот улыбается в ответ.", author="Лиса",
          role="участница с 2024")
def t_quote(lay, d):
    img = bg("ice", 20)
    glass(img, (40, 40, 1160, 560), alpha=30)
    dd = ImageDraw.Draw(img)
    for x, ch in ((70, "“"),):
        dd.text((x + 8, 34 + 12), ch, font=font(300), fill=dark(LIME, 0.6), anchor="la")
        dd.text((x, 34), ch, font=font(300), fill=LIME, anchor="la")
    lay.add("кавычки", (70, 70, 230, 210))
    para(lay, img, "цитата", d["quote"], (250, 110, 1100, 360), 60, color=MILK, weight="SemiBoldItalic",
         max_lines=3, leading=1.18, valign="middle")
    sprite(lay, img, "аватар", face("smile_pink"), 330, 450, 140)
    para(lay, img, "автор", d["author"], (410, 400, 900, 450), 44, color=MILK, weight="Bold", max_lines=1)
    para(lay, img, "роль", d["role"], (412, 458, 900, 500), 28, color=WINE, weight="SemiBold", max_lines=1)
    sprite(lay, img, "сердце", ic("doodle3d/d3_heart.png"), 1060, 460, 110, angle=12)
    return img


@template("milestone", "Рекорд / цифра", number="1 000", unit="membeeeers", text="нас уже тысяча — спасибо, что вы с нами!")
def t_milestone(lay, d):
    img = bg("lime", 21)
    glass(img, (40, 40, 1160, 560), alpha=22)
    num = d["number"]
    f = font(270)
    while f.getlength(num) > 820 and f.size > 120:
        f = font(f.size - 6)
    dd = ImageDraw.Draw(img)
    cx, base = 600, 330
    bb = dd.textbbox((cx, base), num, font=f, anchor="ms")
    lay.add("число", bb)
    for k in range(14, 0, -2):     # объём цифры
        dd.text((cx + k * 0.4, base + k), num, font=f, fill=dark(LIME, 0.45), anchor="ms")
    dd.text((cx, base), num, font=f, fill=LIME, anchor="ms", stroke_width=6, stroke_fill=INK)
    word(lay, img, "надпись", d["unit"], 600, 450, 110, 900, letter="e", palette=LIME_WAVE, center=True)
    para(lay, img, "текст", d["text"], (200, 476, 1000, 530), 32, color=MILK, max_lines=1, align="center")
    sprite(lay, img, "смайлик", face("stars"), 1050, 160, 150)
    sprite(lay, img, "звезда", ic("doodle3d/d3_star.png"), 150, 170, 120, angle=-12)
    sprite(lay, img, "медаль", ic("doodle3d/d3_medal.png"), 140, 420, 100, angle=10)
    sprite(lay, img, "конфети", ic("doodle3d/d3_sparkles.png"), 1060, 420, 100)
    return img


def ring_slot(img, cx, cy, r, inner, col):
    dd = ImageDraw.Draw(img)
    dd.ellipse((cx - r + 6, cy - r + 14, cx + r + 6, cy + r + 14), fill=dark(col, 0.55), outline=INK, width=5)
    dd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col, outline=INK, width=5)
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).ellipse((cx - r + 16, cy - r + 16, cx + r - 16, cy + r - 16), fill=255)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    layer.alpha_composite(rp.brand_bg(2 * r, 2 * r, "wine", seed=int(cx)), (int(cx - r), int(cy - r)))
    rp.put(layer, inner, cx, cy + 6, int(r * 1.6))
    layer.putalpha(rp.ImageChops.multiply(layer.getchannel("A"), m))
    img.alpha_composite(layer)
    dd.ellipse((cx - r + 16, cy - r + 16, cx + r - 16, cy + r - 16), outline=INK, width=4)


@template("collab", "Коллаборация", left="Наш сервер", right="Котики RP",
          text="Объединяем ивенты: общие турниры каждую субботу и обмен ролями.")
def t_collab(lay, d):
    img = bg("wine", 22)
    glass(img, (40, 40, 1160, 560))
    word(lay, img, "надпись", "collaaaab", 600, 170, 150, 900, letter="a", palette=PINK_WAVE, center=True)
    for cx, name, mood, col in ((330, d["left"], "smile_lime", PINK), (870, d["right"], "cat", ICE)):
        r = 100
        ring_slot(img, cx, 290, r, ic("doodle3d/d3_cat.png") if mood == "cat" else face(mood), col)
        lay.add(f"аватар {name}", (cx - r, 290 - r, cx + r + 6, 290 + r + 14))
        para(lay, img, f"имя {name}", name, (cx - 200, 418, cx + 200, 462), 38, color=MILK, weight="Bold",
             max_lines=1, align="center")
    dd = ImageDraw.Draw(img)
    dd.text((604, 298), "×", font=font(140), fill=dark(LIME, 0.5), anchor="mm")
    dd.text((600, 290), "×", font=font(140), fill=LIME, anchor="mm")
    lay.add("крестик", (550, 240, 650, 340))
    para(lay, img, "текст", d["text"], (120, 482, 1080, 540), 28, max_lines=2, align="center")
    return img


@template("digest", "Дайджест недели", period="13–19 октября",
          cards="Кланы|Казна и магазин клана уже работают|ui3d/u3_people.png\n"
                "Картинки|Три новых стиля живых картинок|ui3d/u3_image.png\n"
                "Ивенты|Хэллоуин 31.10 в 20:00 в войсе|doodle3d/d3_balloon.png")
def t_digest(lay, d):
    img = bg("dark", 23)
    word(lay, img, "надпись", "weeeekly", 70, 170, 160, 780, letter="e", palette=LIME_WAVE)
    chip(lay, img, "период", d["period"], 1130, 92, fill=LIME, icon="ui3d/u3_time.png", align="right")
    cards = [c.split("|") for c in d["cards"].split("\n")][:3]
    n = len(cards)
    gap = 30
    cw = (1130 - 70 - gap * (n - 1)) / n
    cols = [PINK, LIME, ICE]
    for k, (title, text, icon) in enumerate(cards):
        x0 = 70 + k * (cw + gap)
        box = (x0, 230, x0 + cw, 540)
        shadow(img, box)
        glass(img, box, alpha=36, dots=k == n - 1)
        lay.add(f"карточка {k}", box)
        inner = Layout(f"карточка {k}")
        sprite(inner, img, "иконка", ic(icon), x0 + 70, 300, 96)
        plate(img, (x0 + 130, 276, x0 + 130 + font(30).getlength(title) + 36, 324), cols[k], radius=24, depth=6,
              width=3)
        ImageDraw.Draw(img).text((x0 + 148, 300), title, font=font(30), fill=INK, anchor="lm")
        para(inner, img, "текст", text, (x0 + 30, 370, x0 + cw - 30, 520), 34, color=MILK, weight="Bold", max_lines=3)
    return img


# ============================================================

def render(key, **texts):
    title, defaults, fn = T[key]
    data = dict(defaults, **texts)
    lay = Layout(key)
    img = fn(lay, data)
    return img.convert("RGB"), lay


def contact_sheet(items):
    cols, tw = 4, 560
    th = tw * H // W
    pad, label = 24, 40
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + label + pad) + pad), (30, 26, 30))
    d = ImageDraw.Draw(sheet)
    for n, (key, img) in enumerate(items):
        x, y = pad + (n % cols) * (tw + pad), pad + (n // cols) * (th + label + pad)
        sheet.paste(img.resize((tw, th), Image.LANCZOS), (x, y + label))
        d.text((x, y + label / 2), f"{n + 1}. {T[key][0]}  ·  news.{key}", font=font(22, "SemiBold"), fill=MILK,
               anchor="lm")
    return sheet


def main():
    wanted = sys.argv[1:]
    keys = [k for k in T if not wanted or k in wanted]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    done = []
    for key in keys:
        img, lay = render(key)
        path = OUT_DIR / f"news.{key}.png"
        img.save(path, optimize=True)
        done.append((key, img))
        print(f"{path.relative_to(ROOT)}: ок, {len(lay.boxes)} элементов без наложений")
    if not wanted:
        contact_sheet(done).save(OUT_DIR / "_all.png", optimize=True)


if __name__ == "__main__":
    main()
