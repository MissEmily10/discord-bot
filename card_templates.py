"""
Шаблоны живых картинок: 20 видов (кроме рейтинга — он в cards.py).

Каждый шаблон описан в SPECS одинаково:
- fields  — поля картинки: (ключ, подпись, вид) — вид "text", "number" или "date";
            первое поле — главное (его меняет команда без «кто»);
- columns — столбцы строк (если у шаблона есть строки): (подпись, вид);
            у «Таблицы» столбцы задаёт сам автор;
- max_rows — сколько строк максимум;
- dynamic — картинка меняется со временем (отсчёт, календарь…): бот
            перерисовывает её сам, как картинки с переменными.

Так у любой картинки одинаковые правка («Поля», «Строка», «Убрать строку»),
команда (/своя_команда значение: кто: столбец:) и данные в одном формате:
data = {<поля>, "rows": [[ячейки]], "theme": "cream", "bang": True}.

Темы (THEMES) — цвета картинки; «!» справа можно убрать. Шрифты — как на
баннерах: Coolvetica для заголовков и крупных чисел, Inter для текста.
Когда будут макеты из Figma («Живые картинки — шаблоны»), фон и координаты
переедут сюда.
"""

import calendar
import io
import math
import os
import re
from datetime import datetime, timedelta

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(ROOT, "fonts")
WIDTH, PAD, HEAD = 1200, 56, 190
ROW_INSET = 24  # текст строк списка — внутри выделенной строки, а не на её краю
MAX_TEXT = 600
MAX_TABLE_ROWS, MAX_TABLE_COLUMNS = 12, 5

CREAM, INK, MUTED = (241, 238, 236), (32, 28, 30), (110, 102, 104)
LIME, PINK, BLUE, MAROON = (180, 238, 58), (227, 179, 192), (198, 216, 230), (122, 22, 38)

# цвета: фон, текст, приглушённый, акцент, второй акцент, подложка строк, шапка, текст шапки
THEMES = {
    "cream": dict(bg=CREAM, ink=INK, muted=MUTED, accent=LIME, accent2=BLUE, soft=(232, 227, 224),
                  head=INK, head_ink=CREAM),
    "night": dict(bg=INK, ink=CREAM, muted=(170, 162, 164), accent=LIME, accent2=PINK, soft=(52, 46, 49),
                  head=LIME, head_ink=INK),
    "pink": dict(bg=(246, 222, 228), ink=MAROON, muted=(150, 90, 104), accent=MAROON, accent2=BLUE,
                 soft=(238, 206, 214), head=MAROON, head_ink=(246, 222, 228)),
    "blue": dict(bg=(226, 236, 244), ink=INK, muted=(84, 104, 124), accent=LIME, accent2=PINK,
                 soft=(210, 224, 236), head=INK, head_ink=CREAM),
    "lime": dict(bg=LIME, ink=INK, muted=(52, 70, 20), accent=INK, accent2=CREAM, soft=(160, 214, 40),
                 head=INK, head_ink=LIME),
    "maroon": dict(bg=MAROON, ink=(246, 222, 228), muted=PINK, accent=LIME, accent2=PINK, soft=(146, 34, 54),
                   head=(246, 222, 228), head_ink=MAROON),
    "mono": dict(bg=(255, 255, 255), ink=(20, 20, 20), muted=(120, 120, 120), accent=(20, 20, 20),
                 accent2=(210, 210, 210), soft=(240, 240, 240), head=(20, 20, 20), head_ink=(255, 255, 255)),
}
# по макету из Figma: акцент — градиент (салатовый → бордовый), плитки — светлый → голубой
THEME_GRADIENTS = {
    "cream": dict(grad=(LIME, MAROON), tile=((232, 227, 224), BLUE)),
    "night": dict(grad=(LIME, PINK), tile=((52, 46, 49), (70, 62, 80))),
    "pink": dict(grad=(PINK, MAROON), tile=((238, 206, 214), BLUE)),
    "blue": dict(grad=(LIME, (60, 110, 160)), tile=((210, 224, 236), (176, 200, 222))),
    "lime": dict(grad=(INK, MAROON), tile=((160, 214, 40), CREAM)),
    "maroon": dict(grad=(LIME, PINK), tile=((146, 34, 54), (180, 70, 96))),
    "mono": dict(grad=((20, 20, 20), (150, 150, 150)), tile=((240, 240, 240), (215, 215, 215))),
}
for _key, _extra in THEME_GRADIENTS.items():
    THEMES[_key].update(_extra)
# Стекло и глитч — тёмные стили: из темы берут тёмную основу и два ярких
# («неоновых») цвета, чтобы цвет картинки менялся и у них.
DARK_TONES = {  # тема -> (тёмная основа, яркий 1, яркий 2)
    "cream": ((22, 20, 24), LIME, PINK),
    "night": ((16, 16, 22), LIME, (196, 170, 255)),
    "pink": ((56, 16, 30), (255, 150, 190), (255, 218, 228)),
    "blue": ((14, 28, 46), (130, 200, 255), LIME),
    "lime": ((20, 30, 8), LIME, (236, 255, 190)),
    "maroon": ((46, 8, 18), (255, 96, 128), PINK),
    "mono": ((16, 16, 16), (240, 240, 240), (150, 150, 150)),
}
LIGHT_INK = (244, 240, 236)
# у «Сливочной» стекло — как на баннерах бота (серо-голубой → бордо)
GLASS_CREAM = dict(top=(150, 160, 172), bottom=(54, 26, 34),
                   blobs=((120, 82, 94), (176, 150, 168), (122, 22, 38), (60, 30, 40)))


STYLE_CREAM = {
    "glass": dict(bg=(92, 52, 64), ink=LIGHT_INK, muted=(226, 196, 206), accent=LIME, accent2=BLUE,
                  soft=(112, 70, 84), head=LIGHT_INK, head_ink=(92, 52, 64), grad=(LIME, PINK),
                  tile=((120, 78, 92), (150, 112, 128)), glass=GLASS_CREAM),
    "glitch": dict(bg=(22, 20, 24), ink=(238, 236, 240), muted=(150, 146, 156), accent=LIME, accent2=PINK,
                   soft=(40, 36, 44), head=LIME, head_ink=(22, 20, 24), grad=(LIME, PINK),
                   tile=((40, 36, 44), (62, 54, 70))),
}


def glass_tones(theme):
    """Фон стекла в цветах темы: сверху светлее, снизу — тёмная основа, размытые пятна ярких цветов."""
    if theme not in DARK_TONES or theme == "cream":
        return GLASS_CREAM
    dark, bright, second = DARK_TONES[theme]
    # верх — приглушённый (белый заголовок должен читаться), низ — тёмная основа
    top = mix(mix(mix(second, bright, 0.3), (120, 124, 132), 0.45), dark, 0.4)
    return dict(top=top, bottom=mix(dark, (0, 0, 0), 0.1),
                blobs=(mix(bright, dark, 0.55), mix(second, (255, 255, 255), 0.1), mix(bright, dark, 0.3),
                       mix(dark, (0, 0, 0), 0.3)))


def style_palette(name, theme):
    """Цвета стекла/глитча под тему (тема та же, что у остальных стилей)."""
    if theme not in DARK_TONES or theme == "cream":  # «Сливочная» — исходные цвета стекла и глитча
        return dict(STYLE_CREAM[name])
    dark, bright, second = DARK_TONES[theme]
    if name == "glass":
        tones = glass_tones(theme)
        bg = mix(tones["top"], tones["bottom"], 0.55)
        soft = mix(bg, (255, 255, 255), 0.12)
        return dict(bg=bg, ink=LIGHT_INK, muted=mix(LIGHT_INK, bright, 0.25), accent=bright,
                    accent2=mix(second, (255, 255, 255), 0.35), soft=soft, head=LIGHT_INK, head_ink=bg,
                    grad=(bright, second), tile=(soft, mix(soft, second, 0.25)), glass=tones)
    soft = mix(dark, (255, 255, 255), 0.08)
    # приглушённый текст темнее порога глитча — подписи не двоятся
    return dict(bg=dark, ink=(238, 236, 240), muted=mix((238, 236, 240), dark, 0.46), accent=bright, accent2=second,
                soft=soft, head=bright, head_ink=dark, grad=(bright, second), tile=(soft, mix(soft, second, 0.2)))

# Стиль = приёмы оформления поверх темы (цвета берутся из темы):
#   accent — чем залит главный акцент: gradient / solid / soft / outline / stripes / dots / duo
#   tile   — второстепенные плашки: gradient / solid / outline
#   radius — множитель скруглений (0 — острые углы), stroke — толщина обводки, shadow — жёсткая тень
#   header — что под заголовком: fade / bar / underline / highlight / dots / box / none; case — lower / upper
#   bg     — узор фона: plain / dots / grid / frame / corner / band / confetti; bang — «!» справа
#   fx     — особый приём: glass (стекло), sticker3d (объёмный бок + 3D-дудлы), glitch (расслоение, сканлайны)
#   palette — тёмная палитра из цветов темы (у стекла и глитча, см. style_palette)
def _style(name, accent, tile, radius, stroke, shadow, header, case, bg, bang=True, fx=None, palette=None):
    return dict(name=name, accent=accent, tile=tile, radius=radius, stroke=stroke, shadow=shadow, header=header,
                case=case, bg=bg, bang=bang, fx=fx, palette=palette)


STYLE_DEFS = {
    "figma": _style("Авторский макет", "gradient", "gradient", 1.0, 4, 0, "fade", "lower", "plain"),
    "classic": _style("Классика", "solid", "solid", 1.0, 4, 0, "bar", "lower", "plain"),
    "brutal": _style("Брутализм", "solid", "solid", 0.2, 6, 10, "underline", "upper", "plain"),
    "pastel": _style("Пастель", "soft", "solid", 1.6, 0, 0, "none", "lower", "plain"),
    "outline": _style("Контур", "outline", "outline", 1.0, 4, 0, "underline", "lower", "plain"),
    "grid": _style("Сетка", "gradient", "outline", 0.5, 2, 0, "fade", "upper", "grid"),
    "stripes": _style("Полоски", "stripes", "solid", 1.0, 4, 0, "bar", "lower", "plain"),
    "dots": _style("Горошек", "dots", "solid", 1.0, 4, 0, "dots", "lower", "dots"),
    "notebook": _style("Блокнот", "solid", "solid", 0.3, 2, 0, "underline", "lower", "grid"),
    "minimal": _style("Минимализм", "solid", "solid", 0.5, 0, 0, "none", "lower", "plain", bang=False),
    "bubbles": _style("Пузыри", "gradient", "gradient", 1.6, 0, 0, "highlight", "lower", "corner"),
    "sticker": _style("Стикер", "solid", "solid", 1.0, 4, 6, "highlight", "lower", "plain"),
    "frame": _style("Рамка", "solid", "outline", 0.5, 4, 0, "underline", "upper", "frame"),
    "duo": _style("Дуотон", "duo", "solid", 1.0, 4, 0, "bar", "lower", "plain"),
    "retro": _style("Ретро", "stripes", "outline", 0.0, 4, 6, "box", "upper", "plain"),
    "party": _style("Праздник", "gradient", "solid", 1.0, 4, 0, "fade", "lower", "confetti"),
    "soft": _style("Мягкий", "gradient", "gradient", 1.6, 0, 0, "fade", "lower", "plain"),
    "poster": _style("Плакат", "solid", "solid", 0.0, 0, 0, "box", "upper", "band"),
    "dotted": _style("Пунктир", "dots", "outline", 1.0, 2, 0, "dots", "lower", "plain"),
    "glass": _style("Стекло", "gradient", "solid", 1.0, 0, 0, "fade", "lower", "glass", palette="glass", fx="glass"),
    "sticker3d": _style("3D-наклейки", "solid", "solid", 1.2, 4, 0, "highlight", "lower", "plain", bang=False,
                        fx="sticker3d"),
    "glitch": _style("Глитч", "gradient", "solid", 0.6, 0, 0, "fade", "lower", "scan", palette="glitch", fx="glitch"),
}
STYLES = {key: value["name"] for key, value in STYLE_DEFS.items()}
DEFAULT_STYLE = "figma"

THEME_NAMES = {"cream": "Сливочная", "night": "Ночная", "pink": "Розовая", "blue": "Голубая",
               "lime": "Салатовая", "maroon": "Бордовая", "mono": "Чёрно-белая"}

T, N, D = "text", "number", "date"
SPECS = {
    "progress": dict(name="Прогресс", about="шкала: 340 / 500 золота, проценты",
                     fields=[("value", "Сейчас", N), ("goal", "Цель", N), ("unit", "Единица", T),
                             ("note", "Подпись", T)]),
    "counter": dict(name="Счётчик", about="большое число и подпись",
                    fields=[("value", "Число", N), ("caption", "Подпись", T)]),
    "text": dict(name="Текст", about="окно с объявлением или статусом дня",
                 fields=[("text", "Текст", T)]),
    "table": dict(name="Таблица", about="свои столбцы и строки: кто, что, когда",
                  fields=[], columns=None, max_rows=MAX_TABLE_ROWS),
    "countdown": dict(name="Обратный отсчёт", about="до ивента 2 д 5 ч — считает сам",
                      fields=[("when", "Когда (25.12 20:00)", D), ("caption", "Подпись", T),
                              ("done", "Текст после конца", T)], dynamic=True),
    "poll": dict(name="Голосование", about="варианты с полосками и процентами",
                 fields=[("question", "Вопрос", T)], columns=[("Вариант", T), ("Голоса", N)], max_rows=8),
    "checklist": dict(name="Чек-лист", about="задачи с галочками",
                      fields=[("caption", "Подпись", T)], columns=[("Задача", T), ("Готово (да/нет)", T)],
                      max_rows=12),
    "timeline": dict(name="Расписание", about="время — событие, лентой",
                     fields=[("caption", "Подпись", T)], columns=[("Когда", T), ("Что", T)], max_rows=10),
    "goals": dict(name="Цели", about="несколько шкал прогресса сразу",
                  fields=[("caption", "Подпись", T)], columns=[("Цель", T), ("Сейчас", N), ("Нужно", N)],
                  max_rows=6),
    "stats": dict(name="Статистика", about="плитки с цифрами: участники, победы…",
                  fields=[("caption", "Подпись", T)], columns=[("Что", T), ("Число", T)], max_rows=6),
    "quote": dict(name="Цитата", about="цитата дня и автор",
                  fields=[("text", "Цитата", T), ("author", "Автор", T)]),
    "spotlight": dict(name="Участник недели", about="аватарка, имя и за что",
                      fields=[("who", "Кто (@упоминание или имя)", T), ("reason", "За что", T),
                              ("badge", "Плашка (участник недели)", T)]),
    "versus": dict(name="Счёт матча", about="команда против команды",
                   fields=[("left", "Слева", T), ("left_score", "Счёт слева", N), ("right", "Справа", T),
                           ("right_score", "Счёт справа", N), ("caption", "Подпись", T)]),
    "status": dict(name="Статусы", about="кто/что и цветной статус: онлайн, занят…",
                   fields=[("caption", "Подпись", T)], columns=[("Кто или что", T), ("Статус", T)], max_rows=10),
    "calendar": dict(name="Календарь", about="месяц с отмеченными днями",
                     fields=[("month", "Месяц (12.2026), пусто — текущий", T), ("caption", "Подпись", T)],
                     columns=[("День", N), ("Событие", T)], max_rows=12, dynamic=True),
    "queue": dict(name="Очередь", about="список по порядку, «сейчас» подсвечено",
                  fields=[("current", "Сейчас (номер)", N), ("caption", "Подпись", T)], columns=[("Кто", T)],
                  max_rows=12),
    "birthdays": dict(name="Дни рождения", about="ближайшие даты сами по порядку",
                      fields=[("caption", "Подпись", T)], columns=[("Кто", T), ("Дата (дд.мм)", T)],
                      max_rows=12, dynamic=True),
    "streak": dict(name="Счётчик дней", about="сколько дней прошло с даты — считает сам",
                   fields=[("since", "С какого дня (01.12.2026)", D), ("caption", "Подпись", T)], dynamic=True),
    "prices": dict(name="Прайс", about="товар — цена, как витрина",
                   fields=[("caption", "Подпись", T)], columns=[("Что", T), ("Цена", T)], max_rows=10),
    "chart": dict(name="Диаграмма", about="столбики по дням или по людям",
                  fields=[("caption", "Подпись", T)], columns=[("Подпись", T), ("Число", N)], max_rows=10),
}
TEMPLATES = ("rating",) + tuple(SPECS)


# ============================================================
# ДАННЫЕ
# ============================================================

def spec(template):
    return SPECS.get(template, {})


def columns_of(template, data):
    """[(подпись, вид)] столбцов строк; у «Таблицы» — заданные автором."""
    if template == "table":
        return [(name, T) for name in data.get("columns") or ["—"]]
    return spec(template).get("columns") or []


def has_rows(template):
    return template == "table" or bool(spec(template).get("columns"))


def default_data(template):
    if template == "rating":
        return {"rows": []}
    data = {"rows": [], "theme": "cream", "bang": True, "style": DEFAULT_STYLE}
    for key, _, kind in spec(template).get("fields", []):
        data[key] = 0 if kind == N else ""
    if template == "progress":
        data["goal"] = 100
    if template == "table":
        data["columns"] = ["Кто", "Что", "Когда"]
    if template == "spotlight":
        data["badge"] = "участник недели"
    if template == "countdown":
        data["done"] = "уже началось!"
    return data


def number_text(value):
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{value:,}".replace(",", " ") if isinstance(value, int) else str(value)


def parse_change(raw, current):
    """«+10» / «-5» — прибавить или отнять, «42» или «=42» — поставить. -> число или None."""
    text = (raw or "").strip().replace(",", ".").replace(" ", "").lstrip("=")
    match = re.fullmatch(r"([+-]?)(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    value = float(match[2])
    value = int(value) if value.is_integer() else round(value, 2)
    current = current if isinstance(current, (int, float)) else 0
    return current + value if match[1] == "+" else current - value if match[1] == "-" else value


def to_number(value):
    if isinstance(value, (int, float)):
        return value
    parsed = parse_change(str(value or ""), 0)
    return parsed if parsed is not None else 0


def change_number(data, key, raw):
    value = parse_change(raw, data.get(key) or 0)
    if value is None:
        return "cards.error.bad_value"
    data[key] = value
    return None


# Что показывает поле (выбирается при создании картинки, «Что показывает»):
# number — число вручную, text — текст вместо числа, variable — живое значение
# сервера ({online}, {member_count}…), counter — свой счётчик {counter:имя},
# mention — участник (<@id>, на картинке — его ник).
FIELD_KINDS = ("number", "text", "variable", "counter", "mention")
TEXT_INSTEAD_OF_NUMBER = {("counter", "value")}  # где число — только надпись, его можно заменить текстом


def kind_of(data, key):
    """Переопределённый вид поля этой картинки или None (как в шаблоне)."""
    kind = (data.get("kinds") or {}).get(key)
    return kind if kind in FIELD_KINDS else None


def set_field(template, data, key, raw):
    """Поле по ключу: число принимает +/-, остальное — текст. -> None или ключ ошибки."""
    kind = next((k for f, _, k in spec(template).get("fields", []) if f == key), T)
    if kind_of(data, key) in ("text", "variable", "counter", "mention"):
        data[key] = (raw or "").strip()[:120]  # живое значение / текст / участник — храним как есть
        return None
    if kind == N:
        return change_number(data, key, raw)
    if kind == D and raw.strip() and parse_when(raw) is None:
        return "cards.error.bad_date"
    data[key] = (raw or "").strip()[:MAX_TEXT if key == "text" else 120]
    return None


def field_by_label(template, label):
    """«автор», «цель», «счёт слева» -> ключ поля (по подписи или ключу) или None."""
    label = (label or "").strip().lower()
    for key, name, _ in spec(template).get("fields", []):
        short = name.split(" (")[0].lower()
        if label in (key, short, name.lower()):
            return key
    return None


def set_progress(data, raw):
    """«+50», «=340», «340», «цель 500» или «340/500»."""
    text = (raw or "").strip().lower()
    goal = re.fullmatch(r"(?:цель|goal)\s*:?\s*(\d+(?:[.,]\d+)?)", text)
    if goal:
        return change_number(data, "goal", goal[1])
    both = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*/\s*(\d+(?:[.,]\d+)?)", text)
    if both:
        return change_number(data, "value", both[1]) or change_number(data, "goal", both[2])
    return change_number(data, "value", text)


def set_text(data, raw):
    data["text"] = (raw or "").strip()[:MAX_TEXT]
    return None


def row_index(data, who):
    """Строка по номеру («2») или по первой ячейке («Аня»). None — такой нет."""
    who = (who or "").strip()
    rows = data.get("rows") or []
    if who.isdigit() and 1 <= int(who) <= len(rows):
        return int(who) - 1
    for index, row in enumerate(rows):
        if row and str(row[0]).strip().lower() == who.lower():
            return index
    return None


def set_row(template, data, who, raw, column=None):
    """
    Строка картинки. who — номер строки или первая ячейка; нет такой — новая.
    Без столбца: «значение» — ячейки через «|» (Аня | дизайн | пт); одно значение
    для существующей строки идёт во второй столбец (числа: +1, -2, =5).
    Со столбцом — одна ячейка. «убрать» — удалить строку.
    """
    columns = columns_of(template, data)
    if not columns:
        return "cards.error.no_rows"
    rows = data.setdefault("rows", [])
    raw = (raw or "").strip()
    index = row_index(data, who)
    if raw.lower() in ("убрать", "удалить", "remove", "delete"):
        if index is None:
            return "cards.error.no_row"
        rows.pop(index)
        return None
    if index is None:
        if len(rows) >= (spec(template).get("max_rows") or MAX_TABLE_ROWS):
            return "cards.error.full"
        row = ["" if kind == T else 0 for _, kind in columns]
        if who and not who.strip().isdigit():
            row[0] = who.strip()[:60]
        rows.append(row)
        index = len(rows) - 1
    row = rows[index]
    row.extend(["" if kind == T else 0 for _, kind in columns[len(row):]])

    def put(n, value):
        kind = columns[n][1]
        if kind == N:
            number = parse_change(value, to_number(row[n]))
            if number is None:
                return "cards.error.bad_value"
            row[n] = number
        else:
            row[n] = value.strip()[:60]
        return None

    if column:
        if not 1 <= column <= len(columns):
            return "cards.error.bad_column"
        return put(column - 1, raw)
    cells = [cell.strip() for cell in raw.split("|")]
    if len(cells) == 1 and len(columns) > 1 and (who or row[0]):
        return put(1, cells[0])  # «кто: Аня, значение: +1» — второй столбец
    for n, cell in enumerate(cells[:len(columns)]):
        problem = put(n, cell)
        if problem:
            return problem
    return None


def set_table(data, who, raw, column=None):
    return set_row("table", data, who, raw, column)


def set_columns(data, raw):
    columns = [cell.strip()[:30] for cell in (raw or "").split("|") if cell.strip()][:MAX_TABLE_COLUMNS]
    if not columns:
        return "cards.error.bad_value"
    data["columns"] = columns
    for row in data.get("rows") or []:
        del row[len(columns):]
        row.extend([""] * (len(columns) - len(row)))
    return None


# ============================================================
# ДАТЫ
# ============================================================

def now():
    try:
        import live
        return datetime.now(live.TIMEZONE).replace(tzinfo=None)
    except Exception:
        return datetime.now()


def parse_when(text, base=None):
    """«25.12 20:00», «25.12.2026», «25.12», «20:00» (сегодня/завтра). -> datetime или None."""
    base = base or now()
    text = (text or "").strip()
    match = re.fullmatch(r"(?:(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?)?\s*(?:(\d{1,2}):(\d{2}))?", text)
    if not text or not match or not (match[1] or match[4]):
        return None
    try:
        if match[1]:
            year = int(match[3]) if match[3] else base.year
            year += 2000 if year < 100 else 0
            result = datetime(year, int(match[2]), int(match[1]), int(match[4] or 0), int(match[5] or 0))
            if not match[3] and result < base - timedelta(days=1):
                result = result.replace(year=year + 1)  # «25.12», а декабрь уже прошёл — следующий год
        else:
            result = base.replace(hour=int(match[4]), minute=int(match[5]), second=0, microsecond=0)
            if result < base:
                result += timedelta(days=1)
        return result
    except ValueError:
        return None


def plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


# ============================================================
# РИСОВАНИЕ: ОБЩЕЕ
# ============================================================

_fonts = {}
FONT_FILES = {"title": "banner/Coolvetica-Regular.otf", "bold": "Inter-Bold.otf", "semi": "Inter-SemiBold.otf"}


def font(kind, size):
    key = (kind, size)
    if key not in _fonts:
        path = os.path.join(FONT_DIR, FONT_FILES.get(kind, kind))
        if not os.path.exists(path):
            path = os.path.join(FONT_DIR, "Inter-Bold.otf")
        _fonts[key] = ImageFont.truetype(path, size)
    return _fonts[key]


_EMOJI = re.compile("[\u2190-\u21ff\u2300-\u23ff\u2600-\u27bf\u2b00-\u2bff\ufe0f\u200d"
                    "\U0001f000-\U0001faff]+")


def clean(text):
    """Эмодзи шрифт не рисует (были бы квадратики) — убираем."""
    return re.sub(r"\s{2,}", " ", _EMOJI.sub("", str(text if text is not None else ""))).strip()


def readable(color, fill):
    """color поверх fill, если различимы; иначе — тёмный/светлый по фону."""
    lum = lambda c: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]  # noqa: E731
    return color if abs(lum(color) - lum(fill)) > 100 else contrast(fill)


def contrast(fill):
    """Тёмный или светлый текст поверх заливки fill."""
    r, g, b = fill[:3]
    return INK if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else CREAM


def fit(text, kind, size, width, min_size=18):
    text = clean(text)
    current = font(kind, size)
    while current.getlength(text) > width and size > min_size:
        size -= 2
        current = font(kind, size)
    while current.getlength(text) > width and len(text) > 2:
        text = text[:-2] + "…"
    return text, current


def wrap(text, fnt, width):
    lines = []
    for paragraph in "\n".join(clean(part) for part in str(text or "").split("\n")).split("\n"):
        line = ""
        for word in paragraph.split(" "):
            trial = f"{line} {word}".strip()
            if fnt.getlength(trial) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def gradient(size, colors, stops=(0.0, 1.0), vertical=False, bottom_up=False):
    """Картинка-градиент (RGBA). colors — 2 цвета (RGB или RGBA), stops — где они стоят (0–1)."""
    width, height = max(1, int(size[0])), max(1, int(size[1]))
    length = height if vertical else width
    start, end = [tuple(color) + ((255,) if len(color) == 3 else ()) for color in colors]
    a, b = stops
    line = []
    for i in range(length):
        p = i / max(1, length - 1)
        if vertical and bottom_up:
            p = 1 - p
        k = 0 if p <= a else 1 if p >= b else (p - a) / (b - a)
        line.append(tuple(int(x + (y - x) * k) for x, y in zip(start, end)))
    strip = Image.new("RGBA", (1, length) if vertical else (length, 1))
    strip.putdata(line)
    return strip.resize((width, height), Image.NEAREST)


def grad_box(cv, box, radius, colors, stops=(0.26, 0.79), vertical=False, bottom_up=False, outline=None, width=4):
    """Скруглённый прямоугольник с градиентом (как на макете), по желанию — с обводкой."""
    x0, y0, x1, y1 = [int(v) for v in box]
    if x1 <= x0 or y1 <= y0:
        return
    fill = gradient((x1 - x0, y1 - y0), colors, stops, vertical, bottom_up)
    mask = Image.new("L", fill.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, fill.width - 1, fill.height - 1), radius=radius, fill=255)
    alpha = Image.composite(fill.getchannel("A"), Image.new("L", fill.size, 0), mask)
    cv.image.paste(fill.convert("RGB"), (x0, y0), alpha)
    if outline is not None:
        cv.draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, outline=outline, width=width)


def _inside(cv, box):
    """Плашка у правого отступа + тень стиля -> сузить плашку, чтобы тень не вылезала за отступ."""
    off = cv.st["shadow"]
    x0, y0, x1, y1 = box
    if off and x1 > WIDTH - PAD - off:
        x1 = max(x0 + 1, WIDTH - PAD - off)
    return (x0, y0, x1, y1)


def _r(cv, box, radius):
    x0, y0, x1, y1 = box
    return int(max(0, min(radius * cv.st["radius"], (x1 - x0) / 2, (y1 - y0) / 2)))


def _shadow(cv, box, radius, fill=None):
    if cv.st.get("fx") == "sticker3d":  # объёмный бок вниз, как у 3D-эмодзи
        x0, y0, x1, y1 = box
        side = mix(fill or cv.theme["soft"], cv.theme["ink"], 0.45)
        cv.draw.rounded_rectangle((x0, y0 + 4, x1, y1 + 12), radius=radius, fill=cv.theme["ink"])
        cv.draw.rounded_rectangle((x0 + 4, y0 + 4, x1 - 4, y1 + 8), radius=max(0, radius - 2), fill=side)
        return
    if cv.st["shadow"]:
        off = cv.st["shadow"]
        x0, y0, x1, y1 = box
        cv.draw.rounded_rectangle((x0 + off, y0 + off, x1 + off, y1 + off), radius=radius, fill=cv.theme["ink"])


def _outline(cv, box, radius, outline=True, color=None):
    if outline and cv.st["stroke"]:
        cv.draw.rounded_rectangle(box, radius=radius, outline=color or cv.theme["ink"], width=cv.st["stroke"])


def glass_box(cv, box, radius, alpha=40):
    """Матовое стекло: белый с прозрачностью и светлая кромка поверх фона."""
    x0, y0, x1, y1 = [int(v) for v in box]
    if x1 <= x0 or y1 <= y0:
        return
    layer = Image.new("RGBA", (x1 - x0 + 1, y1 - y0 + 1), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle((0, 0, layer.width - 1, layer.height - 1), radius=radius,
                                            fill=(255, 255, 255, alpha), outline=(255, 255, 255, 90), width=2)
    base = cv.image.crop((x0, y0, x1 + 1, y1 + 1)).convert("RGBA")
    base.alpha_composite(layer)
    cv.image.paste(base.convert("RGB"), (x0, y0))


def shape(cv, box, radius, fill, outline=True):
    """Плашка в духе стиля: скругление, обводка и тень берутся из стиля."""
    box = _inside(cv, box)
    r = _r(cv, box, radius)
    if cv.st.get("fx") == "glass" and fill in (cv.theme["soft"], cv.theme["bg"]):
        glass_box(cv, box, r)
        return
    if outline:
        _shadow(cv, box, r, fill)
    cv.draw.rounded_rectangle(box, radius=r, fill=fill)
    _outline(cv, box, r, outline)


def mix(a, b, k):
    """Смесь двух цветов: k = 0 — a, k = 1 — b."""
    return tuple(int(x + (y - x) * k) for x, y in zip(a[:3], b[:3]))


def _pattern(cv, box, radius, base, ink, kind):
    """Полоски или горошек внутри скруглённой плашки."""
    x0, y0, x1, y1 = [int(v) for v in box]
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    tile = Image.new("RGB", (w, h), base)
    draw = ImageDraw.Draw(tile)
    if kind == "stripes":
        for x in range(-h, w + h, 22):
            draw.polygon([(x, 0), (x + 10, 0), (x + 10 - h, h), (x - h, h)], fill=ink)
    else:
        for y in range(6, h, 18):
            for x in range(6 + (y // 18 % 2) * 9, w, 18):
                draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=ink)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    cv.image.paste(tile, (x0, y0), mask)


def accent_fill(cv, box, radius, outline=True, vertical=False):
    """Главный акцент — по стилю: градиент, сплошной, контур, полоски, горошек, дуотон…"""
    c, kind = cv.theme, cv.st["accent"]
    box = _inside(cv, box) if outline else box
    r = _r(cv, box, radius)
    if outline:
        _shadow(cv, box, r, c["grad"][1] if kind == "gradient" else c["accent"])
    if kind == "gradient":
        grad_box(cv, box, r, c["grad"] if not vertical else vertical_grad(c), (0.26, 0.79) if not vertical
                 else (0.08, 1.0), vertical=vertical, bottom_up=vertical)
    elif kind == "outline":
        line = c["grad"][0] if readable(c["grad"][0], c["bg"]) == c["grad"][0] else c["accent"]  # бледный контур не видно
        cv.draw.rounded_rectangle(box, radius=r, fill=c["bg"], outline=line, width=max(4, cv.st["stroke"]))
        return
    elif kind in ("stripes", "dots"):
        # узор мягкий (тон в тон), чтобы текст поверх читался
        _pattern(cv, box, r, c["accent"], mix(c["accent"], c["grad"][1], 0.3), kind)
    elif kind == "duo":
        x0, y0, x1, y1 = box
        cv.draw.rounded_rectangle(box, radius=r, fill=c["grad"][1])
        if vertical:
            cv.draw.rounded_rectangle((x0, (y0 + y1) // 2, x1, y1), radius=r, fill=c["accent"])
            cv.draw.rectangle((x0, (y0 + y1) // 2, x1, min(y1, (y0 + y1) // 2 + r)), fill=c["accent"])
        else:
            cv.draw.rounded_rectangle((x0, y0, (x0 + x1) // 2, y1), radius=r, fill=c["accent"])
            cv.draw.rectangle((max(x0, (x0 + x1) // 2 - r), y0, (x0 + x1) // 2, y1), fill=c["accent"])
    else:  # solid / soft (пастель — акцент, разбавленный фоном)
        cv.draw.rounded_rectangle(box, radius=r, fill=mix(c["grad"][1], c["bg"], 0.55) if kind == "soft"
                                  else c["accent"])
    _outline(cv, box, r, outline)


def vertical_grad(c):
    """
    Градиент вертикальной плашки (с текстом по центру). Если концы градиента
    требуют разного текста (салатовый → бордо), смесь вышла бы грязной, а текст
    не читался бы на одном из концов — тогда плашка в главном цвете темы с
    лёгким переходом в его же оттенок, и текст читается по всей высоте.
    """
    start, end = c["grad"]
    if contrast(start) == contrast(end):
        return start, end
    base = c["accent"] if c["accent"] in (tuple(start), tuple(end)) else start
    shade = mix(base, (0, 0, 0), 0.18) if contrast(base) == INK else mix(base, (255, 255, 255), 0.18)
    return base, shade


def accent_ink(cv, end=False, vertical=False):
    """Цвет текста поверх акцента (end — у дальнего конца градиента / второй половины)."""
    c, kind = cv.theme, cv.st["accent"]
    if kind == "gradient" and vertical:
        return contrast(vertical_grad(c)[0])
    if kind == "gradient":
        return contrast(c["grad"][1] if end else c["grad"][0])
    if kind == "outline":
        return c["ink"]
    if kind == "soft":
        return contrast(mix(c["grad"][1], c["bg"], 0.55))
    if kind == "duo" and end:
        return contrast(c["grad"][1])
    return contrast(c["accent"])


def tile_fill(cv, box, radius, outline=True):
    """Второстепенная плашка — по стилю: градиент светлый → голубой, сплошная или только контур."""
    c, kind = cv.theme, cv.st["tile"]
    box = _inside(cv, box) if outline else box
    r = _r(cv, box, radius)
    if cv.st.get("fx") == "glass":
        glass_box(cv, box, r, 52)
        return
    if outline:
        _shadow(cv, box, r, c["soft"])
    if kind == "gradient":
        grad_box(cv, box, r, c["tile"], (0.0, 1.0), vertical=True)
    elif kind == "outline":
        cv.draw.rounded_rectangle(box, radius=r, fill=c["bg"])
    else:
        cv.draw.rounded_rectangle(box, radius=r, fill=c["soft"])
    _outline(cv, box, r, outline or kind == "outline")


def background(cv):
    """Узор фона по стилю."""
    c, kind, d = cv.theme, cv.st["bg"], cv.draw
    w, h = cv.image.size
    faint = tuple(int(a * 0.85 + b * 0.15) for a, b in zip(c["bg"], c["muted"]))
    if kind == "glass":
        cv.image.paste(glass_background(w, h, c.get("glass") or GLASS_CREAM), (0, 0))
        return
    if kind == "scan":
        for y in range(0, h, 4):
            d.line((0, y, w, y), fill=mix(c["bg"], (0, 0, 0), 0.35), width=1)
        for x in range(0, w, 80):
            d.line((x, 0, x, h), fill=mix(c["bg"], c["muted"], 0.08), width=1)
        return
    if kind == "dots":
        for y in range(24, h, 34):
            for x in range(24, w, 34):
                d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=faint)
    elif kind == "grid":
        for x in range(0, w, 40):
            d.line((x, 0, x, h), fill=faint, width=1)
        for y in range(0, h, 40):
            d.line((0, y, w, y), fill=faint, width=1)
    elif kind == "frame":
        d.rounded_rectangle((14, 14, w - 14, h - 14), radius=24, outline=c["ink"], width=4)
    elif kind == "corner":
        d.ellipse((w - 230, -190, w + 150, 176), fill=c["soft"])
        d.ellipse((-150, h - 96, 120, h + 170), fill=c["soft"])
    elif kind == "band":
        d.rectangle((0, 0, 18, h), fill=c["accent"])
    elif kind == "confetti":
        import random as _random
        rng = _random.Random(7)
        colors = [c["accent"], c["accent2"], c["grad"][1]]
        for _ in range(60):
            x, y = rng.randint(0, w), rng.randint(0, h)
            if PAD - 22 < x < w - PAD + 6 and 18 < y < h - 26:
                continue  # только поля: на заголовке и тексте конфетти читалось бы как точки и буквы
            size = rng.randint(6, 14)
            d.rounded_rectangle((x, y, x + size, y + size // 2), radius=3, fill=rng.choice(colors))


_glass_cache = {}


def glass_background(w, h, tones=None):
    """Фон «стекла»: вертикальный градиент и размытые пятна (у «Сливочной» — как на баннерах бота)."""
    from PIL import ImageFilter
    tones = tones or GLASS_CREAM
    key = (w, h, tones["top"], tones["bottom"], tones["blobs"])
    if key not in _glass_cache:
        if len(_glass_cache) > 32:
            _glass_cache.clear()
        base = gradient((w, h), (tones["top"], tones["bottom"]), (0.0, 1.0), vertical=True).convert("RGB")
        blobs = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(blobs)
        for (x, y, r), col in zip(((0.15, 0.2, 0.45), (0.85, 0.15, 0.4), (0.7, 0.9, 0.5), (0.1, 0.95, 0.35)),
                                  tones["blobs"]):
            d.ellipse(((x - r) * w, (y - r * 0.6) * h * 1.6, (x + r) * w, (y + r * 0.6) * h * 1.6), fill=col + (150,))
        blobs = blobs.filter(ImageFilter.GaussianBlur(max(w, h) * 0.07))
        out = base.convert("RGBA")
        out.alpha_composite(blobs)
        _glass_cache[key] = out.convert("RGB")
    return _glass_cache[key].copy()


DOODLE_DIR = os.path.join(ROOT, "panel_art", "emoji")


def _stickers(cv, title):
    """3D-дудлы справа в шапке (вместо «!») — набор зависит от названия, чтобы не прыгал."""
    import random as _random
    files = sorted(f for f in os.listdir(DOODLE_DIR) if f.startswith("doodle_") and f.endswith(".png")) \
        if os.path.isdir(DOODLE_DIR) else []
    if not files:
        return
    rng = _random.Random(sum(map(ord, title or "")))
    picks = rng.sample(files, 2)
    for (x, y, size, angle), name in zip(((WIDTH - PAD - 230, 34, 118, -12), (WIDTH - PAD - 112, 62, 104, 10)), picks):
        im = Image.open(os.path.join(DOODLE_DIR, name)).convert("RGBA").resize((size, size), Image.LANCZOS)
        im = im.rotate(angle, resample=Image.BICUBIC, expand=True)
        cv.image.paste(im, (x, y), im)


def glitch(image, seed=3, colors=(LIME, PINK)):
    """
    Глитч: салатовая и розовая кромки у светлых контуров (сдвиг влево/вправо),
    сканлайны и цветные «сбои» на полях. Сам текст и плашки не рвём — читается.
    """
    import random as _random
    from PIL import ImageFilter
    rng = _random.Random(seed)
    base = image.convert("RGBA")
    w, h = base.size
    light = image.convert("L").point(lambda v: 255 if v > 175 else 0)  # подписи (приглушённые) не двоятся
    edges = Image.eval(light, lambda v: v).filter(ImageFilter.FIND_EDGES).point(lambda v: 255 if v > 40 else 0)
    edges = edges.filter(ImageFilter.MaxFilter(3))
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for color, dx in ((colors[0], -4), (colors[1], 4)):
        tint = Image.new("RGBA", (w, h), color + (0,))
        tint.putalpha(edges.point(lambda v: int(v * 0.75)))
        out.paste(tint, (dx, 0), tint)
    result = base.copy()
    result.alpha_composite(out)
    # исходник поверх кромок — сами буквы остаются чистыми; светлые плашки «закрываем» целиком,
    # чтобы тёмный текст на них (подпись на салатовой плашке) не обводился цветом
    keep = light.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.MinFilter(9)).filter(ImageFilter.MinFilter(3))
    result.paste(base, (0, 0), keep)
    d = ImageDraw.Draw(result)
    # «сбои» — только у самого края картинки (у текста они читались бы как минусы)
    for _ in range(5):
        y = rng.randrange(20, max(21, h - 20))
        if rng.random() < 0.5:
            x0, x1 = w - rng.randrange(14, 26), w
        else:
            x0, x1 = 0, rng.randrange(12, 24)
        d.rectangle((x0, y, x1, y + rng.randrange(3, 7)), fill=rng.choice(colors) + (220,))
    # сканлайны
    lines = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lines)
    for y in range(0, h, 3):
        ld.line((0, y, w, y), fill=(0, 0, 0, 30))
    result.alpha_composite(lines)
    return result.convert("RGB")


def look_of(data):
    """(стиль, его описание, цвета) картинки — тема применяется ко всем стилям."""
    style = data.get("style") if data.get("style") in STYLE_DEFS else DEFAULT_STYLE
    st = STYLE_DEFS[style]
    theme = data.get("theme") if data.get("theme") in THEMES else "cream"
    return style, st, style_palette(st["palette"], theme) if st.get("palette") else THEMES[theme]


class Canvas:
    """Картинка шаблона: шапка (заголовок, линия-акцент с растворением, «!») и тема."""

    def __init__(self, title, data, body_height, accent=True):
        self.style, self.st, self.theme = look_of(data)
        self.height = HEAD + body_height + PAD
        self.fx = not data.get("_no_fx")  # в баннере глитч накладывается на весь баннер разом
        backdrop = data.get("_backdrop")
        if backdrop is not None and backdrop.size == (WIDTH, self.height):
            self.image = backdrop.convert("RGB").copy()  # фон — кусок фона баннера, стык не виден
            self.draw = ImageDraw.Draw(self.image)
        else:
            self.image = Image.new("RGB", (WIDTH, self.height), self.theme["bg"])
            self.draw = ImageDraw.Draw(self.image)
            background(self)
        c, st = self.theme, self.st
        bang = data.get("bang", True) and st["bang"]
        stickers = st.get("fx") == "sticker3d"
        right = WIDTH - PAD - (140 if bang else 260 if stickers else 0)
        text = (title or "").upper() if st["case"] == "upper" else (title or "").lower()
        header = st["header"] if accent else "none"
        inset = 22 if header == "box" else 0  # в плашке текст отступает от её края
        shown, title_font = fit(text, "title", 104 if st["case"] == "lower" else 92, right - PAD - 2 * inset, 40)
        title_w = title_font.getlength(shown)
        if header == "highlight":  # плашка-маркер под нижней половиной заголовка (от отступа, не левее)
            marker = c["accent2"] if readable(c["ink"], c["accent2"]) == c["ink"] else mix(c["accent2"], c["bg"], 0.65)
            self.draw.rounded_rectangle((PAD, 84, PAD + title_w + 12, 128), radius=12, fill=marker)
        if header == "box":  # заголовок в тёмной плашке, плашка — ровно по отступу
            self.draw.rounded_rectangle((PAD, 32, PAD + title_w + 2 * inset, 146), radius=_r(self, (0, 0, 999, 999), 10),
                                        fill=c["head"])
        self.draw.text((PAD + inset, 122), shown, font=title_font,
                       fill=c["head_ink"] if header == "box" else c["ink"], anchor="ls")
        if header == "bar":  # короткая черта с обводкой
            self.draw.rounded_rectangle((PAD, 140, PAD + 120, 152), radius=6, fill=c["accent"], outline=c["ink"],
                                        width=3)
        elif header == "fade":  # линия 800×12: цвет акцента растворяется к концу
            start = c["grad"][0]
            grad_box(self, (PAD, 160, PAD + 801, 172), 6, (start, tuple(start) + (0,)), (0.0, 1.0))
        elif header == "underline":  # до «!» / наклеек, не сквозь них
            self.draw.rectangle((PAD, 150, right if (bang or stickers) else WIDTH - PAD, 154), fill=c["ink"])
        elif header == "dots":
            for n in range(12):
                self.draw.ellipse((PAD + n * 22, 150, PAD + n * 22 + 10, 160), fill=c["accent"] if n % 2 else c["ink"])
        if bang:
            self.draw.text((WIDTH - PAD - 28, 150), "!", font=font("bold", 170), fill=c["accent2"], anchor="ms")
        if stickers:
            _stickers(self, title)

    def caption(self, text, y):
        if text:
            shown, fnt = fit(text, "semi", 34, WIDTH - 2 * PAD)
            self.draw.text((PAD, y), shown, font=fnt, fill=self.theme["muted"], anchor="ls")

    def png(self):
        if self.st.get("fx") == "glitch" and self.fx:
            self.image = glitch(self.image, colors=self.theme["grad"])
        output = io.BytesIO()
        self.image.save(output, "PNG", optimize=True)
        return output.getvalue()


def _caption_space(data):
    return 70 if data.get("caption") else 0


# ============================================================
# РИСОВАНИЕ: ШАБЛОНЫ
# ============================================================

def render_progress(title, data):
    cv = Canvas(title, data, 250)
    c, d = cv.theme, cv.draw
    value, goal = to_number(data.get("value")), to_number(data.get("goal"))
    share = max(0.0, min(1.0, value / goal)) if goal else 0.0
    unit = f" {data['unit']}" if data.get("unit") else ""
    big, fnt = fit(f"{number_text(value)} / {number_text(goal)}{unit}", "title", 76, WIDTH - 2 * PAD - 220)
    d.text((PAD, HEAD + 70), big, font=fnt, fill=c["ink"], anchor="ls")
    d.text((WIDTH - PAD, HEAD + 70), f"{int(share * 100)}%", font=font("title", 76), fill=c["ink"], anchor="rs")
    top, bottom = HEAD + 100, HEAD + 160
    shape(cv, (PAD, top, WIDTH - PAD, bottom), 30, c["soft"])
    if share > 0:
        right = PAD + max(60, int((WIDTH - 2 * PAD) * share))
        accent_fill(cv, (PAD, top, right, bottom), 30)
    cv.caption(data.get("note"), bottom + 64)
    return cv.png()


def render_counter(title, data):
    cv = Canvas(title, data, 270)
    raw = data.get("value")
    # текст вместо числа («скоро!», «∞») — как есть; иначе число с пробелами
    shown = raw.strip() if isinstance(raw, str) and raw.strip() and parse_change(raw, 0) is None \
        else number_text(to_number(raw))
    number, fnt = fit(shown, "title", 230, WIDTH - 2 * PAD, 60)
    cv.draw.text((PAD - 6, HEAD + 190), number, font=fnt, fill=cv.theme["ink"], anchor="ls")
    cv.caption(data.get("caption"), HEAD + 260)
    return cv.png()


def render_text(title, data, empty_text="—"):
    text = data.get("text") or empty_text
    size = 44
    fnt = font("semi", size)
    lines = wrap(text, fnt, WIDTH - 2 * PAD)
    while len(lines) > 9 and size > 26:
        size -= 4
        fnt = font("semi", size)
        lines = wrap(text, fnt, WIDTH - 2 * PAD)
    lines = lines[:14]
    step = int(size * 1.35)
    cv = Canvas(title, data, 20 + step * len(lines))
    for n, line in enumerate(lines):
        cv.draw.text((PAD, HEAD + 40 + step * n), line, font=fnt,
                     fill=cv.theme["ink"] if data.get("text") else cv.theme["muted"], anchor="ls")
    return cv.png()


def _grid(cv, columns, rows, top, row_h=74, widths=None):
    """Таблица: шапка с названиями столбцов и строки через одну с подложкой."""
    c, d = cv.theme, cv.draw
    total = WIDTH - 2 * PAD
    widths = widths or [total // len(columns)] * len(columns)
    if cv.st["accent"] != "gradient":
        shape(cv, (PAD, top, WIDTH - PAD, top + row_h), 18, c["head"], outline=False)
        head_ink = c["head_ink"]
    else:
        grad_box(cv, (PAD, top, WIDTH - PAD, top + row_h), 18, c["grad"], (0.0, 0.79), vertical=True)
        head_ink = contrast(c["grad"][1])
    x = PAD
    for (name, _), width in zip(columns, widths):
        shown, fnt = fit(name.split(" (")[0], "bold", 32, width - 30)
        d.text((x + 22, top + row_h // 2), shown, font=fnt, fill=head_ink, anchor="lm")
        x += width
    for index, row in enumerate(rows):
        y = top + row_h * (index + 1)
        if index % 2:
            d.rectangle((PAD, y, WIDTH - PAD, y + row_h), fill=c["soft"])
        x = PAD
        for n, width in enumerate(widths):
            cell = row[n] if n < len(row) else ""
            cell = number_text(cell) if isinstance(cell, (int, float)) else cell
            shown, fnt = fit(cell, "bold" if n == 0 else "semi", 32, width - 30)
            d.text((x + 22, y + row_h // 2), shown, font=fnt, fill=c["ink"], anchor="lm")
            x += width


def render_table(title, data, empty_text="—"):
    columns = columns_of("table", data)
    rows = data.get("rows") or [[empty_text]]
    cv = Canvas(title, data, 74 * (1 + len(rows)))
    _grid(cv, columns, rows, HEAD)
    return cv.png()


def render_countdown(title, data):
    cv = Canvas(title, data, 280)
    c, d = cv.theme, cv.draw
    when = parse_when(data.get("when"))
    if when is None:
        cv.caption("укажи дату: 25.12 20:00", HEAD + 120)
        return cv.png()
    left = when - now()
    if left.total_seconds() <= 0:
        shown, fnt = fit(data.get("done") or "уже началось!", "title", 120, WIDTH - 2 * PAD)
        d.text((PAD, HEAD + 150), shown, font=fnt, fill=c["ink"], anchor="ls")
    else:
        days, hours, minutes = left.days, left.seconds // 3600, left.seconds % 3600 // 60
        parts = [(days, plural(days, "день", "дня", "дней")), (hours, plural(hours, "час", "часа", "часов")),
                 (minutes, plural(minutes, "минута", "минуты", "минут"))]
        box_w = (WIDTH - 2 * PAD - 2 * 30) // 3
        for n, (number, word) in enumerate(parts):
            x = PAD + n * (box_w + 30)
            if n == 0:
                accent_fill(cv, (x, HEAD + 20, x + box_w, HEAD + 200), 28, vertical=True)
            else:
                shape(cv, (x, HEAD + 20, x + box_w, HEAD + 200), 28, c["soft"])
            on = accent_ink(cv, vertical=True) if n == 0 else c["ink"]
            d.text((x + box_w // 2, HEAD + 120), str(number), font=font("title", 110), fill=on, anchor="ms")
            d.text((x + box_w // 2, HEAD + 170), word, font=font("semi", 32), fill=on if n == 0 else c["muted"],
                   anchor="ms")
    cv.caption(data.get("caption") or when.strftime("%d.%m %H:%M"), HEAD + 260)
    return cv.png()


def render_poll(title, data, empty_text="—"):
    rows = data.get("rows") or []
    question = data.get("question") or ""
    q_lines = wrap(question, font("semi", 40), WIDTH - 2 * PAD)[:3] if question else []
    cv = Canvas(title, data, len(q_lines) * 54 + max(1, len(rows)) * 96 + 20)
    c, d = cv.theme, cv.draw
    y = HEAD + 30
    for line in q_lines:
        d.text((PAD, y + 20), line, font=font("semi", 40), fill=c["ink"], anchor="ls")
        y += 54
    total = sum(to_number(row[1]) for row in rows if len(row) > 1) or 0
    best = max((to_number(row[1]) for row in rows if len(row) > 1), default=0)
    if not rows:
        cv.caption(empty_text, y + 40)
    for row in rows:
        votes = to_number(row[1]) if len(row) > 1 else 0
        share = votes / total if total else 0
        shape(cv, (PAD, y + 10, WIDTH - PAD, y + 80), 20, c["soft"], outline=False)
        leader = votes == best and votes > 0
        bar_fill = c["grad"][0] if leader else c["accent2"]
        bar_right = PAD + max(40, int((WIDTH - 2 * PAD) * share)) if share else PAD
        if share and leader:
            accent_fill(cv, (PAD, y + 10, bar_right, y + 80), 20, outline=False)
        elif share:
            shape(cv, (PAD, y + 10, bar_right, y + 80), 20, bar_fill, outline=False)
        name, fnt = fit(row[0], "bold", 34, WIDTH - 2 * PAD - 260)
        on_bar = PAD + 24 + fnt.getlength(name) <= bar_right
        d.text((PAD + 24, y + 45), name, font=fnt,
               fill=(accent_ink(cv) if leader else contrast(bar_fill)) if on_bar else contrast(c["soft"]), anchor="lm")
        d.text((WIDTH - PAD - 24, y + 45), f"{int(round(share * 100))}% · {number_text(votes)}",
               font=font("semi", 30), fill=(accent_ink(cv, end=True) if leader else contrast(bar_fill))
               if share > 0.85 else contrast(c["soft"]), anchor="rm")
        y += 96
    return cv.png()


def _yes(value):
    return str(value).strip().lower() in ("да", "+", "1", "yes", "готово", "✓", "v", "done", "true", "ок", "ok")


def render_checklist(title, data, empty_text="—"):
    rows = data.get("rows") or []
    cv = Canvas(title, data, max(1, len(rows)) * 78 + _caption_space(data))
    c, d = cv.theme, cv.draw
    if not rows:
        cv.caption(empty_text, HEAD + 50)
    y = HEAD + 10
    for row in rows:
        done = _yes(row[1]) if len(row) > 1 else False
        if done:
            accent_fill(cv, (PAD, y + 12, PAD + 50, y + 62), 12)
        else:
            shape(cv, (PAD, y + 12, PAD + 50, y + 62), 12, c["soft"])
        text, fnt = fit(row[0], "semi", 38, WIDTH - 2 * PAD - 80)
        d.text((PAD + 76, y + 37), text, font=fnt, fill=c["muted"] if done else c["ink"], anchor="lm")
        if done:
            d.line((PAD + 76, y + 39, PAD + 76 + fnt.getlength(text), y + 39), fill=c["muted"], width=3)
        y += 78
    cv.caption(data.get("caption"), y + 50)
    return cv.png()


def render_timeline(title, data, empty_text="—"):
    rows = data.get("rows") or []
    cv = Canvas(title, data, max(1, len(rows)) * 90 + _caption_space(data))
    c, d = cv.theme, cv.draw
    if not rows:
        cv.caption(empty_text, HEAD + 50)
    x_line = PAD + 220
    if rows:
        d.rounded_rectangle((x_line - 3, HEAD + 30, x_line + 3, HEAD + 30 + 90 * (len(rows) - 1)), radius=3,
                            fill=c["ink"])
    y = HEAD + 30
    for row in rows:
        when, fnt = fit(row[0], "title", 44, 190)
        d.text((x_line - 30, y), when, font=fnt, fill=c["ink"], anchor="rm")
        accent_fill(cv, (x_line - 16, y - 16, x_line + 16, y + 16), 16)
        what, fnt = fit(row[1] if len(row) > 1 else "", "semi", 38, WIDTH - x_line - PAD - 40)
        d.text((x_line + 40, y), what, font=fnt, fill=c["ink"], anchor="lm")
        y += 90
    cv.caption(data.get("caption"), y + 30)
    return cv.png()


def render_goals(title, data, empty_text="—"):
    rows = data.get("rows") or []
    cv = Canvas(title, data, max(1, len(rows)) * 120 + _caption_space(data))
    c, d = cv.theme, cv.draw
    if not rows:
        cv.caption(empty_text, HEAD + 50)
    y = HEAD + 20
    for row in rows:
        value = to_number(row[1]) if len(row) > 1 else 0
        goal = to_number(row[2]) if len(row) > 2 else 0
        share = max(0.0, min(1.0, value / goal)) if goal else 0.0
        name, fnt = fit(row[0], "bold", 36, WIDTH - 2 * PAD - 320)
        d.text((PAD, y + 30), name, font=fnt, fill=c["ink"], anchor="ls")
        d.text((WIDTH - PAD, y + 30), f"{number_text(value)} / {number_text(goal)}", font=font("semi", 32),
               fill=c["muted"], anchor="rs")
        shape(cv, (PAD, y + 48, WIDTH - PAD, y + 88), 20, c["soft"])
        if share:
            accent_fill(cv, (PAD, y + 48, PAD + max(40, int((WIDTH - 2 * PAD) * share)), y + 88), 20)
        y += 120
    cv.caption(data.get("caption"), y + 40)
    return cv.png()


def render_stats(title, data, empty_text="—"):
    rows = (data.get("rows") or [])[:6]
    per_row = 3 if len(rows) > 4 or len(rows) == 3 else 2
    lines = max(1, math.ceil(len(rows) / per_row))
    tile_h = 200
    cv = Canvas(title, data, lines * (tile_h + 24) + _caption_space(data))
    c, d = cv.theme, cv.draw
    if not rows:
        cv.caption(empty_text, HEAD + 50)
    gap = 24
    tile_w = (WIDTH - 2 * PAD - gap * (per_row - 1)) // per_row
    for n, row in enumerate(rows):
        x = PAD + (n % per_row) * (tile_w + gap)
        y = HEAD + 10 + (n // per_row) * (tile_h + gap)
        if n == 0:  # первая плитка — главная: тёмная
            shape(cv, (x, y, x + tile_w, y + tile_h), 28, c["head"])
            ink = c["head_ink"]
        else:
            tile_fill(cv, (x, y, x + tile_w, y + tile_h), 28)
            ink = c["ink"]
        value = row[1] if len(row) > 1 else ""
        value = number_text(value) if isinstance(value, (int, float)) else value
        shown, fnt = fit(value, "title", 96, tile_w - 50, 30)
        d.text((x + 28, y + 120), shown, font=fnt, fill=ink, anchor="ls")
        label, fnt = fit(row[0], "semi", 30, tile_w - 50)
        d.text((x + 28, y + 168), label, font=fnt, fill=ink, anchor="ls")
    cv.caption(data.get("caption"), HEAD + lines * (tile_h + gap) + 50)
    return cv.png()


def render_quote(title, data, empty_text="—"):
    text = data.get("text") or empty_text
    fnt = font("title", 60)
    lines = wrap(f"«{text}»", fnt, WIDTH - 2 * PAD - 60)[:6]
    cv = Canvas(title, data, 40 + 76 * len(lines) + 80)
    c, d = cv.theme, cv.draw
    if cv.st["accent"] != "gradient":
        d.rounded_rectangle((PAD, HEAD + 10, PAD + 12, HEAD + 20 + 76 * len(lines)), radius=6, fill=c["accent"])
    else:
        grad_box(cv, (PAD, HEAD + 10, PAD + 12, HEAD + 20 + 76 * len(lines)), 6, c["grad"], (0.26, 0.79),
                 vertical=True, bottom_up=True)
    for n, line in enumerate(lines):
        d.text((PAD + 50, HEAD + 70 + 76 * n), line, font=fnt, fill=c["ink"], anchor="ls")
    if data.get("author"):
        author, afnt = fit(f"— {data['author']}", "semi", 36, WIDTH - 2 * PAD)
        d.text((PAD + 50, HEAD + 70 + 76 * len(lines) + 30), author, font=afnt, fill=c["muted"], anchor="ls")
    return cv.png()


def render_spotlight(title, data, empty_text="—"):
    cv = Canvas(title, data, 320, accent=(data.get("style") or DEFAULT_STYLE) != "figma")
    c, d = cv.theme, cv.draw
    size = 226  # аватарка внутри кольца-градиента 276 (толщина 25)
    x, y = PAD + 25, HEAD + 37
    avatar = data.get("_avatar")
    ring = (PAD, HEAD + 12, PAD + 276, HEAD + 12 + 276)
    if cv.st["accent"] != "gradient":
        d.ellipse(ring, fill=c["accent"], outline=c["ink"] if cv.st["stroke"] else None, width=max(1, cv.st["stroke"]))
    else:
        grad_box(cv, ring, 138, c["grad"], (0.26, 0.79), vertical=True)
    d.ellipse((x, y, x + size, y + size), fill=(255, 255, 255))
    if avatar:
        try:
            picture = Image.open(io.BytesIO(avatar)).convert("RGB").resize((size, size), Image.LANCZOS)
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
            cv.image.paste(picture, (x, y), mask)
        except Exception:
            avatar = None
    if not avatar:
        letter = (data.get("_name") or data.get("who") or "?").strip()[:1].upper() or "?"
        d.text((x + size // 2, y + size // 2), letter, font=font("title", 150), fill=INK, anchor="mm")
    left = PAD + 312
    badge = data.get("badge") or ""
    if badge:
        text, bfnt = fit(badge, "bold", 30, WIDTH - left - PAD - 40)
        width = max(int(bfnt.getlength(text)) + 60, 602)
        d.rounded_rectangle((left, y - 10, min(left + width, WIDTH - PAD), y + 59), radius=26, fill=c["head"])
        d.text((left + 30, y + 25), text, font=bfnt, fill=readable(c["accent2"], c["head"]), anchor="lm")
    name, nfnt = fit(data.get("_name") or data.get("who") or empty_text, "title", 96, WIDTH - left - PAD, 40)
    d.text((left, y + 160), name, font=nfnt, fill=c["ink"], anchor="ls")
    reason_lines = wrap(data.get("reason") or "", font("semi", 34), WIDTH - left - PAD)[:3]
    for n, line in enumerate(reason_lines):
        d.text((left, y + 210 + 44 * n), line, font=font("semi", 34), fill=c["muted"], anchor="ls")
    return cv.png()


def render_versus(title, data):
    cv = Canvas(title, data, 300 + _caption_space(data))
    c, d = cv.theme, cv.draw
    half = (WIDTH - 2 * PAD - 120) // 2
    left_score, right_score = to_number(data.get("left_score")), to_number(data.get("right_score"))
    for n, (name, score) in enumerate(((data.get("left") or "—", left_score),
                                       (data.get("right") or "—", right_score))):
        x = PAD + n * (half + 120)
        leader = score > (right_score if n == 0 else left_score)
        if leader:
            accent_fill(cv, (x, HEAD + 20, x + half, HEAD + 280), 32, vertical=True)
        else:
            tile_fill(cv, (x, HEAD + 20, x + half, HEAD + 280), 32)
        ink = accent_ink(cv, vertical=True) if leader else c["ink"]
        d.text((x + half // 2, HEAD + 180), number_text(score), font=font("title", 150), fill=ink, anchor="ms")
        shown, fnt = fit(name, "bold", 38, half - 40)
        d.text((x + half // 2, HEAD + 240), shown, font=fnt, fill=ink, anchor="ms")
    d.text((WIDTH // 2, HEAD + 170), ":", font=font("title", 120), fill=c["ink"], anchor="ms")
    cv.caption(data.get("caption"), HEAD + 350)
    return cv.png()


STATUS_COLORS = (
    (("онлайн", "online", "свободен", "свободна", "готов", "готова", "да", "работает", "открыто", "🟢"), LIME),
    (("занят", "занята", "afk", "отошёл", "отошла", "позже", "в процессе", "🟡"), (246, 210, 92)),
    (("оффлайн", "offline", "нет", "не работает", "закрыто", "спит", "🔴"), (232, 98, 98)),
)


def status_color(text, theme):
    low = str(text or "").strip().lower()
    for words, color in STATUS_COLORS:
        if any(low.startswith(word) for word in words):
            return color
    return theme["accent2"]


def render_status(title, data, empty_text="—"):
    rows = data.get("rows") or []
    cv = Canvas(title, data, max(1, len(rows)) * 80 + _caption_space(data))
    c, d = cv.theme, cv.draw
    if not rows:
        cv.caption(empty_text, HEAD + 50)
    y = HEAD + 10
    for row in rows:
        status = row[1] if len(row) > 1 else ""
        color = status_color(status, c)
        d.ellipse((PAD, y + 22, PAD + 36, y + 58), fill=color, outline=c["ink"], width=4)
        name, fnt = fit(row[0], "bold", 38, WIDTH // 2 - PAD)
        d.text((PAD + 60, y + 40), name, font=fnt, fill=c["ink"], anchor="lm")
        if status:  # широкая плашка статуса — правая половина (как в макете)
            pill_left = 574
            text, sfnt = fit(status, "semi", 30, WIDTH - PAD - pill_left - 40)
            shape(cv, (pill_left, y + 13, WIDTH - PAD, y + 68), 26, color)
            d.text((pill_left + 22, y + 40), text, font=sfnt, fill=INK, anchor="lm")
        y += 80
    cv.caption(data.get("caption"), y + 40)
    return cv.png()


MONTHS = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
          "ноябрь", "декабрь")


def render_calendar(title, data, empty_text="—"):
    today = now()
    month, year = today.month, today.year
    match = re.fullmatch(r"\s*(\d{1,2})(?:\.(\d{4}))?\s*", data.get("month") or "")
    if match and 1 <= int(match[1]) <= 12:
        month, year = int(match[1]), int(match[2] or year)
    weeks = calendar.Calendar(0).monthdayscalendar(year, month)
    events = {}
    for row in data.get("rows") or []:
        day = int(to_number(row[0])) if row else 0
        if day:
            events.setdefault(day, []).append(row[1] if len(row) > 1 else "")
    cell_h = 96
    listed = sorted(events.items())[:6]
    cv = Canvas(title, data, 70 + 60 + cell_h * len(weeks) + 52 * len(listed) + _caption_space(data) + 20,
                accent=False)  # в обоих стилях месяц стоит сразу под заголовком
    c, d = cv.theme, cv.draw
    d.text((PAD, HEAD + 4), f"{MONTHS[month - 1]} {year}", font=font("title", 52), fill=c["ink"], anchor="ls")
    cell_w = (WIDTH - 2 * PAD) // 7
    top = HEAD + 80
    for n, name in enumerate(("пн", "вт", "ср", "чт", "пт", "сб", "вс")):
        d.text((PAD + n * cell_w + cell_w // 2, top + 30), name, font=font("bold", 28),
               fill=c["muted"], anchor="mm")
    top += 60
    for w, week in enumerate(weeks):
        for n, day in enumerate(week):
            if not day:
                continue
            x, y = PAD + n * cell_w, top + w * cell_h
            is_today = (day, month, year) == (today.day, today.month, today.year)
            if day in events:
                accent_fill(cv, (x + 8, y + 6, x + cell_w - 8, y + cell_h - 6), 22, vertical=True)
            elif is_today:
                d.rounded_rectangle((x + 8, y + 6, x + cell_w - 8, y + cell_h - 6), radius=22, outline=c["ink"],
                                    width=4)
            ink = accent_ink(cv, vertical=True) if day in events else c["ink"]
            d.text((x + cell_w // 2, y + cell_h // 2), str(day), font=font("title", 44), fill=ink, anchor="mm")
    y = top + cell_h * len(weeks) + 50
    for day, names in listed:
        text, fnt = fit(f"{day} — " + ", ".join(names), "semi", 32, WIDTH - 2 * PAD)
        d.text((PAD, y), text, font=fnt, fill=c["ink"], anchor="ls")
        y += 52
    cv.caption(data.get("caption"), y + 30)
    return cv.png()


def render_queue(title, data, empty_text="—"):
    rows = data.get("rows") or []
    current = int(to_number(data.get("current")) or 0)
    cv = Canvas(title, data, max(1, len(rows)) * 82 + _caption_space(data))
    c, d = cv.theme, cv.draw
    if not rows:
        cv.caption(empty_text, HEAD + 50)
    y = HEAD + 10
    for n, row in enumerate(rows, 1):
        now_here = n == current
        if now_here:
            accent_fill(cv, (PAD, y + 4, WIDTH - PAD, y + 76), 24)
        ink = accent_ink(cv) if now_here else c["ink"]
        d.text((PAD + ROW_INSET + 22, y + 40), str(n), font=font("title", 48), fill=ink if now_here else c["muted"],
               anchor="mm")
        name, fnt = fit(row[0] if row else "", "bold", 38, WIDTH - 2 * PAD - 300)
        d.text((PAD + ROW_INSET + 76, y + 40), name, font=fnt, fill=ink, anchor="lm")
        if now_here:
            d.text((WIDTH - PAD - ROW_INSET - cv.st["shadow"], y + 40), "сейчас", font=font("bold", 30),
                   fill=accent_ink(cv, end=True), anchor="rm")
        y += 82
    cv.caption(data.get("caption"), y + 40)
    return cv.png()


def next_birthday(text, today):
    match = re.fullmatch(r"\s*(\d{1,2})\.(\d{1,2})(?:\.\d{2,4})?\s*", str(text or ""))
    if not match:
        return None
    for year in (today.year, today.year + 1):
        try:
            day = datetime(year, int(match[2]), int(match[1]))
        except ValueError:
            return None
        if day.date() >= today.date():
            return day
    return None


def render_birthdays(title, data, empty_text="—"):
    today = now()
    rows = []
    for row in data.get("rows") or []:
        when = next_birthday(row[1] if len(row) > 1 else "", today)
        if when:
            rows.append((when, row[0]))
    rows.sort()
    cv = Canvas(title, data, max(1, len(rows)) * 82 + _caption_space(data))
    c, d = cv.theme, cv.draw
    if not rows:
        cv.caption(empty_text, HEAD + 50)
    y = HEAD + 10
    for when, name in rows:
        days = (when.date() - today.date()).days
        is_today = days == 0
        if is_today:
            accent_fill(cv, (PAD, y + 4, WIDTH - PAD, y + 76), 24)
        ink = accent_ink(cv) if is_today else c["ink"]
        d.text((PAD + ROW_INSET, y + 40), when.strftime("%d.%m"), font=font("title", 46), fill=ink, anchor="lm")
        shown, fnt = fit(name, "bold", 38, WIDTH - 2 * PAD - 500)
        d.text((PAD + ROW_INSET + 170, y + 40), shown, font=fnt, fill=ink, anchor="lm")
        note = "сегодня!" if is_today else \
            f"через {days} {plural(days, 'день', 'дня', 'дней')}"
        d.text((WIDTH - PAD - ROW_INSET - cv.st["shadow"], y + 40), note, font=font("semi", 30),
               fill=accent_ink(cv, end=True) if is_today else c["muted"], anchor="rm")
        y += 82
    cv.caption(data.get("caption"), y + 40)
    return cv.png()


def render_streak(title, data):
    cv = Canvas(title, data, 280)
    c, d = cv.theme, cv.draw
    since = parse_when(data.get("since")) if data.get("since") else None
    if since and since.date() > now().date():  # «01.12» без года и ещё не наступило — прошлый год
        since = since.replace(year=since.year - 1)
    days = max(0, (now().date() - since.date()).days) if since else 0
    number = str(days)
    fnt = font("title", 230)
    d.text((PAD - 6, HEAD + 190), number, font=fnt, fill=c["ink"], anchor="ls")
    word_x = PAD + fnt.getlength(number) + 30
    d.text((word_x, HEAD + 190), plural(days, "день", "дня", "дней"), font=font("title", 80), fill=c["muted"],
           anchor="ls")
    cv.caption(data.get("caption") or (f"с {since.strftime('%d.%m.%Y')}" if since else "укажи дату начала"),
               HEAD + 260)
    return cv.png()


def render_prices(title, data, empty_text="—"):
    rows = data.get("rows") or []
    cv = Canvas(title, data, max(1, len(rows)) * 80 + _caption_space(data))
    c, d = cv.theme, cv.draw
    if not rows:
        cv.caption(empty_text, HEAD + 50)
    y = HEAD + 10
    for row in rows:
        price = row[1] if len(row) > 1 else ""
        price = number_text(price) if isinstance(price, (int, float)) else price
        pfnt = font("title", 46)
        price_w = pfnt.getlength(price)
        name, fnt = fit(row[0], "bold", 38, WIDTH - 2 * PAD - price_w - 180)
        d.text((PAD, y + 40), name, font=fnt, fill=c["ink"], anchor="lm")
        dots_from, dots_to = PAD + fnt.getlength(name) + 20, WIDTH - PAD - 100 - price_w - 20
        for x in range(int(dots_from), int(dots_to), 18):
            d.ellipse((x, y + 50, x + 5, y + 55), fill=c["muted"])
        d.text((WIDTH - PAD - 100, y + 40), price, font=pfnt, fill=c["ink"], anchor="rm")
        y += 80
    cv.caption(data.get("caption"), y + 40)
    return cv.png()


def render_chart(title, data, empty_text="—"):
    rows = (data.get("rows") or [])[:10]
    chart_h = 360
    cv = Canvas(title, data, chart_h + 110 + _caption_space(data))
    c, d = cv.theme, cv.draw
    if not rows:
        cv.caption(empty_text, HEAD + 50)
        return cv.png()
    top = HEAD + 40
    base = top + chart_h
    best = max((to_number(row[1]) for row in rows if len(row) > 1), default=0) or 1
    gap = 24
    bar_w = (WIDTH - 2 * PAD - gap * (len(rows) - 1)) // len(rows)
    d.line((PAD, base, WIDTH - PAD, base), fill=c["ink"], width=4)
    for n, row in enumerate(rows):
        value = to_number(row[1]) if len(row) > 1 else 0
        height = int((chart_h - 50) * (value / best)) if best else 0
        x = PAD + n * (bar_w + gap)
        if height > 0:
            if value == best:  # самый высокий — градиент снизу вверх
                accent_fill(cv, (x, base - height, x + bar_w, base), min(20, bar_w // 2), vertical=True)
            else:
                shape(cv, (x, base - height, x + bar_w, base), min(20, bar_w // 2), c["accent2"])
        d.text((x + bar_w // 2, base - height - 14), number_text(value), font=font("bold", 30), fill=c["ink"],
               anchor="ms")
        label, fnt = fit(row[0], "semi", 28, bar_w + gap - 4, 14)
        d.text((x + bar_w // 2, base + 40), label, font=fnt, fill=c["muted"], anchor="ms")
    cv.caption(data.get("caption"), base + 110)
    return cv.png()


RENDERERS = {
    "progress": render_progress, "counter": render_counter, "text": render_text, "table": render_table,
    "countdown": render_countdown, "poll": render_poll, "checklist": render_checklist,
    "timeline": render_timeline, "goals": render_goals, "stats": render_stats, "quote": render_quote,
    "spotlight": render_spotlight, "versus": render_versus, "status": render_status,
    "calendar": render_calendar, "queue": render_queue, "birthdays": render_birthdays,
    "streak": render_streak, "prices": render_prices, "chart": render_chart,
}
_TAKES_EMPTY = {"text", "table", "poll", "checklist", "timeline", "goals", "stats", "quote", "spotlight",
                "status", "calendar", "queue", "birthdays", "prices", "chart"}


def texts_of(card):
    """Все тексты картинки, где могут быть переменные: [(путь, значение)]."""
    data = card["data"]
    found = [("title", card["title"])]
    for n, layer in enumerate((data.get("scene") or {}).get("layers") or []):  # тексты холста с сайта
        if layer.get("type") == "text" and layer.get("text"):
            found.append((f"scene:{n}", layer["text"]))
    for key, _, kind in spec(card["template"]).get("fields", []):
        if (kind == T or isinstance(data.get(key), str)) and data.get(key):
            found.append((key, data[key]))  # и число, ставшее живым: «{online}»
    for r, row in enumerate(data.get("rows") or []):
        if isinstance(row, list):
            found += [(f"cell:{r}:{c}", cell) for c, cell in enumerate(row) if isinstance(cell, str)]
    return found


def is_dynamic(template):
    return bool(spec(template).get("dynamic"))


def render(card, title, data, empty_text="—"):
    template = card["template"]
    renderer = RENDERERS.get(template)
    if renderer is None:
        import cards
        rows = sorted(data.get("rows") or [], key=lambda row: (-row["score"], row["name"].lower()))
        return cards.render_rating(title, rows, empty_text)
    if template in _TAKES_EMPTY:
        return renderer(title, data, empty_text)
    return renderer(title, data)


# ============================================================
# БАННЕР СЕРВЕРА ИЗ ЖИВОЙ КАРТИНКИ
# ============================================================

BANNER_SIZE = (960, 540)


DEFAULT_LINE_COLORS = {"#201C1E", "#5A5254"}  # стандартные цвета строк — под светлый фон, берём цвета темы
LINE_SIZES = {"S": 0.065, "M": 0.095, "L": 0.15, "XL": 0.22}  # как у строк баннера сервера: доля высоты


def _hex(color, default):
    text = str(color or "").lstrip("#")
    try:
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4)) if len(text) == 6 else default
    except ValueError:
        return default


def render_banner(card, title, data, empty_text="—", size=BANNER_SIZE, lines=()):
    """
    Живая картинка в формате баннера (16:9): скруглённая карточка в зоне,
    которую Discord показывает и на телефоне, вокруг — рамка в цвете темы
    (_framed). Строки баннера сервера (lines: [{text, size, pos, font, color}]
    с уже подставленными значениями) — не поверх картинки, а под ней, внутри
    карточки, цветом, который читается на фоне темы. -> PNG.
    """
    import types
    import banner_render
    ratio = size[0] / size[1]
    data = dict(data, _no_fx=True)
    first = data.pop("_png", None) or Image.open(io.BytesIO(render(card, title, data, empty_text))).convert("RGB")
    height = first.height
    lines = [line for line in lines or () if str(line.get("text") or "").strip()]
    plain = card["template"] not in RENDERERS  # рейтинг рисуется сам — фон у него ровный
    _, st, theme = look_of(data) if not plain else (None, STYLE_DEFS[DEFAULT_STYLE], THEMES["cream"])

    def layout(full_h):
        """[(текст, шрифт, цвет, выравнивание)] и высота блока строк (размер — от ширины картинки)."""
        placed, block = [], 0
        for n, line in enumerate(lines):
            text = clean(line["text"])
            px = max(18, int(full_h * LINE_SIZES.get(line.get("size"), 0.065) * 0.8))
            kind = banner_render.kind_for(line.get("font") or "coolvetica", text)
            fnt = banner_render._font(kind, px)
            while fnt.getlength(text) > WIDTH - 2 * PAD and px > 16:
                px = int(px * 0.92)
                fnt = banner_render._font(kind, px)
            fallback = theme["ink"] if n == 0 else theme["muted"]
            own = str(line.get("color") or "").upper() not in DEFAULT_LINE_COLORS  # цвет выбран вручную
            color = _hex(line.get("color"), fallback) if own else fallback
            if readable(color, theme["bg"]) != color:
                color = fallback  # выбранный цвет не читается на фоне картинки — цвет темы
            placed.append((text, fnt, color, (line.get("pos") or "bl")[1:2] or "l"))
            block += int(fnt.size * 1.15)
        return placed, block

    # строки встают под данными картинки (вместо её нижнего отступа) и со своим отступом снизу
    cw = first.width
    placed, block = layout(round(WIDTH / ratio))
    content_h = height + (block + PAD // 2 if block else 0)
    if plain:
        content = Image.new("RGB", (cw, content_h), first.getpixel((4, 4)))
        picture = first
    else:  # фон картинки — на всю карточку вместе со строками, стык не виден
        holder = types.SimpleNamespace(st=st, theme=theme, image=Image.new("RGB", (cw, content_h), theme["bg"]))
        holder.draw = ImageDraw.Draw(holder.image)
        background(holder)
        content = holder.image
        data["_backdrop"] = content.crop((0, 0, cw, height))
        picture = Image.open(io.BytesIO(render(card, title, data, empty_text))).convert("RGB")
    content.paste(picture, (0, 0))
    draw = ImageDraw.Draw(content)
    y = height - PAD // 2
    for text, fnt, color, align in placed:
        x, anchor = {"l": (PAD, "la"), "c": (cw // 2, "ma"), "r": (cw - PAD, "ra")}[align]
        draw.text((x, y), text, font=fnt, fill=color, anchor=anchor)
        y += int(fnt.size * 1.15)
    if not plain and st.get("fx") == "glitch":
        content = glitch(content, colors=theme["grad"])
    return _framed(content, DISCORD_UI, size)


# Discord на телефоне показывает баннер не целиком: срезает ~16% сверху и снизу
# (видно примерно 2.6:1) и скругляет только верхний левый угол. Поэтому картинка
# лежит скруглённой карточкой в той зоне, что видна везде, а вокруг — рамка в
# цвет интерфейса Discord (тёмная тема, взят со скриншота приложения): рамка
# сливается со списком каналов, и кажется, что карточка лежит прямо на нём.
DISCORD_UI = (31, 31, 40)
BANNER_SAFE_Y = 0.19  # чуть больше обреза (~16%) — карточка не касается его края
BANNER_SAFE_X = 0.035
BANNER_RADIUS = 0.05  # доля высоты баннера


def _framed(content, frame, size):
    """Карточка (content) в рамке цвета frame, в безопасной зоне баннера. Рисуем вдвое крупнее — края гладкие."""
    k = 2
    W, H = size[0] * k, size[1] * k
    mx, my = round(W * BANNER_SAFE_X), round(H * BANNER_SAFE_Y)
    scale = min((W - 2 * mx) / content.width, (H - 2 * my) / content.height)
    pw, ph = max(1, round(content.width * scale)), max(1, round(content.height * scale))
    x, y = (W - pw) // 2, (H - ph) // 2
    radius = round(H * BANNER_RADIUS)
    canvas = Image.new("RGB", (W, H), frame)
    # мягкая тень под карточкой — она «лежит» на рамке
    from PIL import ImageFilter
    shade = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shade).rounded_rectangle((x, y + 10 * k, x + pw, y + ph + 10 * k), radius=radius, fill=110)
    shade = shade.filter(ImageFilter.GaussianBlur(14 * k))
    canvas.paste(Image.new("RGB", (W, H), mix(frame, (0, 0, 0), 0.55)), (0, 0), shade)
    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, pw - 1, ph - 1), radius=radius, fill=255)
    canvas.paste(content.resize((pw, ph), Image.LANCZOS), (x, y), mask)
    output = io.BytesIO()
    canvas.resize(size, Image.LANCZOS).save(output, "PNG", optimize=True)
    return output.getvalue()


def banner_from_png(png, lines=(), size=BANNER_SIZE):
    """Готовая картинка (холст с сайта) -> баннер 16:9: по краям — цвет её угла, строки — под ней. -> PNG."""
    first = Image.open(io.BytesIO(png)).convert("RGB")
    fake = {"template": "__png__"}
    return render_banner(fake, "", {"_png": first}, "", size, lines)


# ============================================================
# ПРИМЕРЫ (для «Все шаблоны» и макетов в Figma)
# ============================================================

SAMPLES = {
    "progress": ("Казна клана", {"value": 340, "goal": 500, "unit": "золота", "note": "собираем на замок до пятницы"}),
    "counter": ("Побед в турнире", {"value": 12, "caption": "с начала сезона"}),
    "text": ("Статус дня", {"text": "Сегодня в 20:00 общий сбор в войсе «Клан». Разберём планы на неделю."}),
    "table": ("Кто что делает", {"columns": ["Кто", "Что", "Когда"],
                                 "rows": [["Аня", "баннеры", "пт"], ["Кот", "музыка", "сб"]]}),
    "countdown": ("До ивента", {"when": "", "caption": "хэллоуин-ивент", "done": "начинаем!"}),
    "poll": ("Голосование", {"question": "Какой ивент в субботу?",
                             "rows": [["Мафия", 14], ["Кино", 9], ["Караоке", 5]]}),
    "checklist": ("Подготовка", {"rows": [["Баннер", "да"], ["Музыка", "да"], ["Призы", "нет"]]}),
    "timeline": ("Суббота", {"rows": [["18:00", "сбор в войсе"], ["19:00", "мафия"], ["21:00", "кино"]]}),
    "goals": ("Цели месяца", {"rows": [["Участники", 820, 1000], ["Бусты", 9, 14], ["Ивенты", 3, 4]]}),
    "stats": ("Сервер", {"rows": [["участников", "1 234"], ["в войсе", "17"], ["бустов", "9"]]}),
    "quote": ("Цитата дня", {"text": "Кто рано встаёт, тот первым занимает войс", "author": "Кот"}),
    "spotlight": ("Звезда недели", {"who": "Аня", "reason": "нарисовала все дудлы бота",
                                    "badge": "участник недели"}),
    "versus": ("Финал", {"left": "Совы", "left_score": 3, "right": "Лисы", "right_score": 2,
                         "caption": "до 5 побед"}),
    "status": ("Модераторы", {"rows": [["Аня", "онлайн"], ["Кот", "занят"], ["Пёс", "оффлайн"]]}),
    "calendar": ("Ивенты", {"rows": [[5, "мафия"], [12, "кино"], [31, "хэллоуин"]]}),
    "queue": ("Очередь на собес", {"current": 2, "rows": [["Аня"], ["Кот"], ["Пёс"]]}),
    "birthdays": ("Дни рождения", {"rows": [["Аня", "03.11"], ["Кот", "15.12"], ["Пёс", "01.02"]]}),
    "streak": ("Без ссор", {"since": "", "caption": "держимся!"}),
    "prices": ("Магазин клана", {"rows": [["Своя роль", "500 золота"], ["Цвет ника", "200 золота"],
                                          ["Буст", "50 золота"]]}),
    "chart": ("Активность за неделю", {"rows": [["пн", 12], ["вт", 18], ["ср", 9], ["чт", 22], ["пт", 30],
                                                ["сб", 41], ["вс", 27]]}),
}


# Ещё два примера к SAMPLES: один тип — разные задачи (предпросмотр при выборе типа).
# (для чего, заголовок, значения)
USES = {
    "progress": [("сбор на цель", None, None),
                 ("набор в клан", "Набор в клан", {"value": 7, "goal": 10, "unit": "человек",
                                                   "note": "осталось 3 места"}),
                 ("прогресс ивента", "Хэллоуин-квест", {"value": 64, "goal": 100, "unit": "%",
                                                       "note": "найдено тыкв на сервере"})],
    "counter": [("победы и рекорды", None, None),
                ("сколько нас", "Нас уже", {"value": 1234, "caption": "участников сервера"}),
                ("заявки за день", "Заявок сегодня", {"value": 8, "caption": "ждут проверки"})],
    "text": [("статус дня", None, None),
             ("объявление", "Внимание", {"text": "Сервер на техработах с 02:00 до 03:00 — боты могут молчать."}),
             ("правило недели", "Правило недели", {"text": "Не спамим в общем чате — для мемов есть #мемы."})],
    "table": [("кто что делает", None, None),
              ("состав команды", "Состав клана", {"columns": ["Ник", "Роль", "С нами"],
                                                  "rows": [["Аня", "глава", "март"], ["Кот", "зам", "май"]]}),
              ("график дежурств", "Дежурства модеров", {"columns": ["День", "Кто", "Время"],
                                                        "rows": [["пн", "Аня", "18–22"], ["вт", "Кот", "20–24"]]})],
    "countdown": [("до ивента", None, None),
                  ("до конца набора", "Набор закрывается", {"caption": "успей подать заявку", "done": "набор закрыт"}),
                  ("до релиза", "До обновления", {"caption": "новый сезон кланов", "done": "уже вышло!"})],
    "poll": [("выбрать ивент", None, None),
             ("выбрать название", "Название клана", {"question": "Как назовём клан?",
                                                     "rows": [["Совы", 11], ["Лисы", 7], ["Кофе", 4]]}),
             ("оценка", "Как вам ивент?", {"question": "Оцените вчерашнюю мафию",
                                           "rows": [["огонь", 20], ["норм", 6], ["скучно", 2]]})],
    "checklist": [("подготовка ивента", None, None),
                  ("задачи недели", "Задачи клана", {"rows": [["Квест", "да"], ["Сбор в войсе", "нет"],
                                                              ["Отчёт", "нет"]]}),
                  ("онбординг", "Новичку", {"rows": [["Прочитать правила", "да"], ["Выбрать роли", "да"],
                                                     ["Представиться", "нет"]]})],
    "timeline": [("программа вечера", None, None),
                 ("расписание недели", "Неделя", {"rows": [["пн", "кино"], ["ср", "мафия"], ["пт", "караоке"]]}),
                 ("план стрима", "Стрим", {"rows": [["20:00", "старт"], ["20:30", "розыгрыш"], ["22:00", "финал"]]})],
    "goals": [("цели месяца", None, None),
              ("цели клана", "Цели клана", {"rows": [["Казна", 3400, 5000], ["Квесты", 2, 3], ["Состав", 22, 30]]}),
              ("личные цели", "Мои цели", {"rows": [["Часы в войсе", 14, 20], ["Посты", 40, 50]]})],
    "stats": [("цифры сервера", None, None),
              ("итоги ивента", "Итоги турнира", {"rows": [["команд", "12"], ["матчей", "31"], ["зрителей", "86"]]}),
              ("клан в цифрах", "Клан в цифрах", {"rows": [["участников", "24"], ["респект", "18,4"],
                                                           ["огонёк", "×1.3"]]})],
    "quote": [("цитата дня", None, None),
              ("слова главы", "Слово главы", {"text": "Мы не самые большие, зато самые дружные", "author": "Аня"}),
              ("перл из чата", "Перл недели", {"text": "я не опоздал, я пришёл эффектно", "author": "Кот"})],
    "spotlight": [("участник недели", None, None),
                  ("новичок месяца", "Новичок месяца", {"who": "Кот", "reason": "за месяц стал душой войса",
                                                        "badge": "новичок месяца"}),
                  ("победитель конкурса", "Победитель", {"who": "Аня", "reason": "лучший арт на конкурсе",
                                                         "badge": "1 место"})],
    "versus": [("счёт матча", None, None),
               ("клан против клана", "Битва кланов", {"left": "LINKO", "left_score": 120, "right": "ANH",
                                                      "right_score": 98, "caption": "баллы биллборда"}),
               ("спор", "Кошки vs собаки", {"left": "Кошки", "left_score": 41, "right": "Собаки",
                                            "right_score": 39, "caption": "голосование чата"})],
    "status": [("кто на месте", None, None),
               ("статус серверов", "Игровые сервера", {"rows": [["Minecraft", "онлайн"], ["CS", "занят"],
                                                               ["Terraria", "оффлайн"]]}),
               ("заявки", "Заявки", {"rows": [["Аня", "онлайн"], ["Кот", "занят"]]})],
    "calendar": [("ивенты месяца", None, None),
                 ("дежурства", "Дежурства", {"rows": [[3, "Аня"], [10, "Кот"], [17, "Пёс"]]}),
                 ("важные даты", "Важное", {"rows": [[1, "новый сезон"], [15, "итоги"], [28, "аукцион"]]})],
    "queue": [("очередь на собес", None, None),
              ("очередь в караоке", "Караоке", {"current": 1, "rows": [["Кот"], ["Аня"], ["Пёс"]]}),
              ("очередь на арт", "Заказы артов", {"current": 3, "rows": [["Аня"], ["Кот"], ["Лис"], ["Пёс"]]})],
    "birthdays": [("дни рождения", None, None),
                  ("годовщины", "С нами уже год", {"rows": [["Аня", "12.11"], ["Кот", "02.12"]]}),
                  ("праздники", "Праздники", {"rows": [["Хэллоуин", "31.10"], ["Новый год", "31.12"]]})],
    "streak": [("дней без ссор", None, None),
               ("дней с открытия", "Сервер живёт", {"caption": "дней с открытия"}),
               ("дней подряд в топе", "В топе биллборда", {"caption": "дней подряд на 1 месте"})],
    "prices": [("магазин", None, None),
               ("прайс на арты", "Арты на заказ", {"rows": [["Аватарка", "300"], ["Баннер", "500"],
                                                           ["Эмодзи", "150"]]}),
               ("награды", "Награды за актив", {"rows": [["Своя роль", "10 уровень"], ["Цвет ника", "5 уровень"]]})],
    "chart": [("активность по дням", None, None),
              ("кто больше писал", "Топ болтунов", {"rows": [["Аня", 340], ["Кот", 280], ["Пёс", 120]]}),
              ("часы в войсе", "Войс за неделю", {"rows": [["пн", 3], ["ср", 8], ["пт", 12], ["вс", 9]]})],
}
_STREAK_DAYS = {"дней с открытия": 340, "дней подряд в топе": 5}


def sample_uses(template, style=DEFAULT_STYLE, theme="cream"):
    """Тип на трёх примерах с разными задачами. -> [(для чего, PNG)]."""
    out = []
    for purpose, title, values in USES.get(template, [("пример", None, None)]):
        if title is None:
            out.append((purpose, sample(template, theme, style)))
            continue
        data = default_data(template)
        data.update(values)
        data["theme"], data["style"] = theme, style
        if template == "countdown":
            days = 6 if "набор" in purpose else 14
            data["when"] = (now() + timedelta(days=days, hours=3)).strftime("%d.%m.%Y %H:%M")
        if template == "streak":
            data["since"] = (now() - timedelta(days=_STREAK_DAYS.get(purpose, 12))).strftime("%d.%m.%Y")
        out.append((purpose, render({"template": template}, title, data)))
    return out


def sample(template, theme="cream", style=DEFAULT_STYLE):
    """Пример картинки шаблона. -> PNG."""
    title, values = SAMPLES.get(template, ("Пример", {}))
    data = default_data(template)
    data.update(values)
    data["theme"], data["style"] = theme, style
    if template == "countdown" and not data.get("when"):
        data["when"] = (now() + timedelta(days=2, hours=5, minutes=30)).strftime("%d.%m.%Y %H:%M")
    if template == "streak" and not data.get("since"):
        data["since"] = (now() - timedelta(days=12)).strftime("%d.%m.%Y")
    return render({"template": template}, title, data)
