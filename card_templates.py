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
    "streak": dict(name="Дней без…", about="«12 дней без ссор» — считает сам",
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
    data = {"rows": [], "theme": "cream", "bang": True}
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


def set_field(template, data, key, raw):
    """Поле по ключу: число принимает +/-, остальное — текст. -> None или ключ ошибки."""
    kind = next((k for f, _, k in spec(template).get("fields", []) if f == key), T)
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


class Canvas:
    """Картинка шаблона: шапка (заголовок, черта, «!») и тема."""

    def __init__(self, title, data, body_height):
        self.theme = THEMES.get(data.get("theme"), THEMES["cream"])
        self.height = HEAD + body_height + PAD
        self.image = Image.new("RGB", (WIDTH, self.height), self.theme["bg"])
        self.draw = ImageDraw.Draw(self.image)
        c = self.theme
        right = WIDTH - PAD - (140 if data.get("bang", True) else 0)
        shown, title_font = fit((title or "").lower(), "title", 104, right - PAD, 40)
        self.draw.text((PAD, 112), shown, font=title_font, fill=c["ink"], anchor="ls")
        self.draw.rounded_rectangle((PAD, 136, PAD + 120, 148), radius=6, fill=c["accent"], outline=c["ink"], width=3)
        if data.get("bang", True):
            self.draw.text((WIDTH - PAD, 154), "!", font=font("bold", 170), fill=c["accent2"], anchor="rs")

    def caption(self, text, y):
        if text:
            shown, fnt = fit(text, "semi", 34, WIDTH - 2 * PAD)
            self.draw.text((PAD, y), shown, font=fnt, fill=self.theme["muted"], anchor="ls")

    def png(self):
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
    d.rounded_rectangle((PAD, top, WIDTH - PAD, bottom), radius=30, fill=c["soft"], outline=c["ink"], width=5)
    if share > 0:
        right = PAD + max(60, int((WIDTH - 2 * PAD) * share))
        d.rounded_rectangle((PAD, top, right, bottom), radius=30, fill=c["accent"], outline=c["ink"], width=5)
    cv.caption(data.get("note"), bottom + 64)
    return cv.png()


def render_counter(title, data):
    cv = Canvas(title, data, 270)
    number, fnt = fit(number_text(to_number(data.get("value"))), "title", 230, WIDTH - 2 * PAD, 60)
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
    d.rounded_rectangle((PAD, top, WIDTH - PAD, top + row_h), radius=18, fill=c["head"])
    x = PAD
    for (name, _), width in zip(columns, widths):
        shown, fnt = fit(name.split(" (")[0], "bold", 32, width - 30)
        d.text((x + 22, top + row_h // 2), shown, font=fnt, fill=c["head_ink"], anchor="lm")
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
            d.rounded_rectangle((x, HEAD + 20, x + box_w, HEAD + 200), radius=28,
                                fill=c["accent"] if n == 0 else c["soft"], outline=c["ink"], width=4)
            d.text((x + box_w // 2, HEAD + 120), str(number), font=font("title", 110),
                   fill=c["ink"] if n else contrast(c["accent"]), anchor="ms")
            d.text((x + box_w // 2, HEAD + 170), word, font=font("semi", 32),
                   fill=c["muted"] if n else contrast(c["accent"]), anchor="ms")
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
        d.rounded_rectangle((PAD, y + 10, WIDTH - PAD, y + 80), radius=20, fill=c["soft"])
        bar_fill = c["accent"] if votes == best else c["accent2"]
        bar_right = PAD + max(40, int((WIDTH - 2 * PAD) * share)) if share else PAD
        if share:
            d.rounded_rectangle((PAD, y + 10, bar_right, y + 80), radius=20, fill=bar_fill)
        name, fnt = fit(row[0], "bold", 34, WIDTH - 2 * PAD - 260)
        on_bar = PAD + 24 + fnt.getlength(name) <= bar_right
        d.text((PAD + 24, y + 45), name, font=fnt, fill=contrast(bar_fill if on_bar else c["soft"]), anchor="lm")
        d.text((WIDTH - PAD - 24, y + 45), f"{int(round(share * 100))}% · {number_text(votes)}",
               font=font("semi", 30), fill=contrast(bar_fill if share > 0.85 else c["soft"]), anchor="rm")
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
        d.rounded_rectangle((PAD, y + 12, PAD + 50, y + 62), radius=12, fill=c["accent"] if done else c["soft"],
                            outline=c["ink"], width=4)
        if done:
            d.line([(PAD + 12, y + 37), (PAD + 22, y + 49), (PAD + 40, y + 24)], fill=contrast(c["accent"]), width=6)
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
        d.line((x_line, HEAD + 30, x_line, HEAD + 30 + 90 * (len(rows) - 1)), fill=c["ink"], width=5)
    y = HEAD + 30
    for row in rows:
        when, fnt = fit(row[0], "title", 44, 190)
        d.text((x_line - 30, y), when, font=fnt, fill=c["ink"], anchor="rm")
        d.ellipse((x_line - 16, y - 16, x_line + 16, y + 16), fill=c["accent"], outline=c["ink"], width=5)
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
        d.rounded_rectangle((PAD, y + 48, WIDTH - PAD, y + 88), radius=20, fill=c["soft"], outline=c["ink"], width=4)
        if share:
            d.rounded_rectangle((PAD, y + 48, PAD + max(40, int((WIDTH - 2 * PAD) * share)), y + 88), radius=20,
                                fill=c["accent"], outline=c["ink"], width=4)
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
        fill = c["accent"] if n == 0 else c["soft"]
        ink = contrast(fill) if n == 0 else c["ink"]
        d.rounded_rectangle((x, y, x + tile_w, y + tile_h), radius=28, fill=fill, outline=c["ink"], width=4)
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
    d.rounded_rectangle((PAD, HEAD + 10, PAD + 12, HEAD + 20 + 76 * len(lines)), radius=6, fill=c["accent"])
    for n, line in enumerate(lines):
        d.text((PAD + 50, HEAD + 70 + 76 * n), line, font=fnt, fill=c["ink"], anchor="ls")
    if data.get("author"):
        author, afnt = fit(f"— {data['author']}", "semi", 36, WIDTH - 2 * PAD)
        d.text((PAD + 50, HEAD + 70 + 76 * len(lines) + 30), author, font=afnt, fill=c["muted"], anchor="ls")
    return cv.png()


def render_spotlight(title, data, empty_text="—"):
    cv = Canvas(title, data, 320)
    c, d = cv.theme, cv.draw
    size = 260
    x, y = PAD, HEAD + 20
    avatar = data.get("_avatar")
    d.ellipse((x - 8, y - 8, x + size + 8, y + size + 8), fill=c["accent"], outline=c["ink"], width=5)
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
        d.text((x + size // 2, y + size // 2), letter, font=font("title", 150), fill=contrast(c["accent"]),
               anchor="mm")
    left = x + size + 60
    badge = data.get("badge") or ""
    if badge:
        text, bfnt = fit(badge, "bold", 30, WIDTH - left - PAD - 40)
        width = int(bfnt.getlength(text)) + 44
        d.rounded_rectangle((left, y + 10, left + width, y + 62), radius=26, fill=c["head"])
        d.text((left + 22, y + 37), text, font=bfnt, fill=c["head_ink"], anchor="lm")
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
        d.rounded_rectangle((x, HEAD + 20, x + half, HEAD + 280), radius=32,
                            fill=c["accent"] if leader else c["soft"], outline=c["ink"], width=5)
        ink = contrast(c["accent"]) if leader else c["ink"]
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
        if status:
            text, sfnt = fit(status, "semi", 30, WIDTH // 2 - PAD - 40)
            width = int(sfnt.getlength(text)) + 48
            d.rounded_rectangle((WIDTH - PAD - width, y + 14, WIDTH - PAD, y + 66), radius=26, fill=color,
                                outline=c["ink"], width=3)
            d.text((WIDTH - PAD - width // 2, y + 40), text, font=sfnt, fill=INK, anchor="mm")
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
    cv = Canvas(title, data, 70 + 60 + cell_h * len(weeks) + 52 * len(listed) + _caption_space(data) + 20)
    c, d = cv.theme, cv.draw
    d.text((PAD, HEAD + 50), f"{MONTHS[month - 1]} {year}", font=font("title", 52), fill=c["ink"], anchor="ls")
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
                d.rounded_rectangle((x + 8, y + 6, x + cell_w - 8, y + cell_h - 6), radius=22, fill=c["accent"],
                                    outline=c["ink"], width=4)
            elif is_today:
                d.rounded_rectangle((x + 8, y + 6, x + cell_w - 8, y + cell_h - 6), radius=22, outline=c["ink"],
                                    width=4)
            ink = contrast(c["accent"]) if day in events else c["ink"]
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
            d.rounded_rectangle((PAD - 14, y + 4, WIDTH - PAD + 14, y + 76), radius=24, fill=c["accent"],
                                outline=c["ink"], width=4)
        ink = contrast(c["accent"]) if now_here else c["ink"]
        d.text((PAD + 30, y + 40), str(n), font=font("title", 48), fill=ink if now_here else c["muted"], anchor="mm")
        name, fnt = fit(row[0] if row else "", "bold", 38, WIDTH - 2 * PAD - 260)
        d.text((PAD + 90, y + 40), name, font=fnt, fill=ink, anchor="lm")
        if now_here:
            d.text((WIDTH - PAD, y + 40), "сейчас", font=font("bold", 30), fill=ink, anchor="rm")
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
            d.rounded_rectangle((PAD - 14, y + 4, WIDTH - PAD + 14, y + 76), radius=24, fill=c["accent"],
                                outline=c["ink"], width=4)
        ink = contrast(c["accent"]) if is_today else c["ink"]
        d.text((PAD, y + 40), when.strftime("%d.%m"), font=font("title", 46), fill=ink, anchor="lm")
        shown, fnt = fit(name, "bold", 38, WIDTH - 2 * PAD - 460)
        d.text((PAD + 170, y + 40), shown, font=fnt, fill=ink, anchor="lm")
        note = "сегодня!" if is_today else \
            f"через {days} {plural(days, 'день', 'дня', 'дней')}"
        d.text((WIDTH - PAD, y + 40), note, font=font("semi", 30), fill=ink if is_today else c["muted"], anchor="rm")
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
        name, fnt = fit(row[0], "bold", 38, WIDTH - 2 * PAD - price_w - 80)
        d.text((PAD, y + 40), name, font=fnt, fill=c["ink"], anchor="lm")
        dots_from, dots_to = PAD + fnt.getlength(name) + 20, WIDTH - PAD - price_w - 20
        for x in range(int(dots_from), int(dots_to), 18):
            d.ellipse((x, y + 50, x + 5, y + 55), fill=c["muted"])
        d.text((WIDTH - PAD, y + 40), price, font=pfnt, fill=c["ink"], anchor="rm")
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
            d.rounded_rectangle((x, base - height, x + bar_w, base), radius=min(20, bar_w // 2),
                                fill=c["accent"] if value == best else c["accent2"], outline=c["ink"], width=4)
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
    for key, _, kind in spec(card["template"]).get("fields", []):
        if kind == T and data.get(key):
            found.append((key, data[key]))
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
    "spotlight": ("Звезда недели", {"who": "Эмилия", "reason": "нарисовала все дудлы бота",
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


def sample(template, theme="cream"):
    """Пример картинки шаблона. -> PNG."""
    title, values = SAMPLES.get(template, ("Пример", {}))
    data = default_data(template)
    data.update(values)
    data["theme"] = theme
    if template == "countdown" and not data.get("when"):
        data["when"] = (now() + timedelta(days=2, hours=5, minutes=30)).strftime("%d.%m.%Y %H:%M")
    if template == "streak" and not data.get("since"):
        data["since"] = (now() - timedelta(days=12)).strftime("%d.%m.%Y")
    return render({"template": template}, title, data)
