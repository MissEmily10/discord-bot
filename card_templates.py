"""
Шаблоны живых картинок (кроме рейтинга — он в cards.py): данные и отрисовка.

- progress — шкала: «340 / 500 золота», полоска, проценты и подпись;
- counter  — большое число и подпись («побед: 12», «до ивента 3 дня»);
- text     — окно с текстом (объявление, «статус дня»);
- table    — таблица: заголовки столбцов и строки.

В текстах можно писать переменные ({member_count}, {voice}, {date}…) —
их подставляет cards.card_message перед отрисовкой.
Оформление — в цветах бота, как у рейтинга. Когда будет макет из Figma,
фон и координаты переедут сюда.
"""

import io
import re

from PIL import Image, ImageDraw

import cards

TEMPLATES = ("rating", "progress", "counter", "text", "table")
MAX_TABLE_ROWS, MAX_TABLE_COLUMNS = 12, 5
MAX_TEXT = 600
WIDTH, PAD, HEAD = 1200, 56, 190


def default_data(template):
    return {
        "rating": {"rows": []},
        "progress": {"value": 0, "goal": 100, "unit": "", "note": ""},
        "counter": {"value": 0, "caption": ""},
        "text": {"text": ""},
        "table": {"columns": ["Кто", "Что", "Когда"], "rows": []},
    }.get(template, {"rows": []})


def number_text(value):
    return cards.score_text(value)


# ---------- данные ----------

def change_number(data, key, raw):
    """«+5», «-3», «42» -> новое значение data[key]; None — не число."""
    value = cards.parse_change(raw, data.get(key) or 0)
    if value is None:
        return "cards.error.bad_value"
    data[key] = value
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
    return change_number(data, "value", text.lstrip("="))


def set_text(data, raw):
    data["text"] = (raw or "").strip()[:MAX_TEXT]
    return None


def table_row_index(data, who):
    """Строка таблицы по номеру («2») или по первой ячейке («Аня»). None — новой строки."""
    who = (who or "").strip()
    if who.isdigit() and 1 <= int(who) <= len(data["rows"]):
        return int(who) - 1
    for index, row in enumerate(data["rows"]):
        if row and row[0].strip().lower() == who.lower():
            return index
    return None


def set_table(data, who, raw, column=None):
    """
    Без столбца: «значение» — вся строка через «|» (Аня | дизайн | пт).
    Со столбцом: одна ячейка. who — номер строки или первая ячейка; нет такой — новая.
    """
    columns = data["columns"]
    index = table_row_index(data, who)
    if index is None:
        if len(data["rows"]) >= MAX_TABLE_ROWS:
            return "cards.error.full"
        row = [""] * len(columns)
        if who and not who.strip().isdigit():
            row[0] = who.strip()[:40]
        data["rows"].append(row)
        index = len(data["rows"]) - 1
    row = data["rows"][index]
    row.extend([""] * (len(columns) - len(row)))
    if raw.strip().lower() in ("убрать", "удалить", "remove", "delete"):
        data["rows"].pop(index)
        return None
    if column:
        if not 1 <= column <= len(columns):
            return "cards.error.bad_column"
        row[column - 1] = raw.strip()[:60]
    else:
        cells = [cell.strip()[:60] for cell in raw.split("|")]
        if len(cells) == 1 and row[0]:  # только одно значение — во второй столбец
            cells = [row[0]] + cells
        for n, cell in enumerate(cells[:len(columns)]):
            row[n] = cell
    return None


def set_columns(data, raw):
    columns = [cell.strip()[:30] for cell in (raw or "").split("|") if cell.strip()][:MAX_TABLE_COLUMNS]
    if not columns:
        return "cards.error.bad_value"
    data["columns"] = columns
    for row in data["rows"]:
        del row[len(columns):]
        row.extend([""] * (len(columns) - len(row)))
    return None


# ---------- картинка ----------

def _head(title, height):
    picture = Image.new("RGB", (WIDTH, height), cards.CREAM)
    draw = ImageDraw.Draw(picture)
    shown, font = cards._fit((title or "").lower(), "Inter-Bold.otf", 88, WIDTH - 2 * PAD - 140)
    draw.text((PAD, 104), shown, font=font, fill=cards.INK, anchor="ls")
    draw.rounded_rectangle((PAD, 132, PAD + 120, 144), radius=6, fill=cards.LIME, outline=cards.INK, width=3)
    draw.text((WIDTH - PAD, 150), "!", font=cards._font("Inter-Bold.otf", 170), fill=cards.BLUE, anchor="rs")
    return picture, draw


def _png(picture):
    output = io.BytesIO()
    picture.save(output, "PNG", optimize=True)
    return output.getvalue()


def _wrap(text, font, width):
    lines = []
    for paragraph in (text or "").split("\n"):
        words, line = paragraph.split(" "), ""
        for word in words:
            trial = f"{line} {word}".strip()
            if font.getlength(trial) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def render_progress(title, data):
    picture, draw = _head(title, 470)
    value, goal = data.get("value") or 0, data.get("goal") or 0
    share = max(0.0, min(1.0, value / goal)) if goal else 0.0
    unit = f" {data['unit']}" if data.get("unit") else ""
    big = f"{number_text(value)} / {number_text(goal)}{unit}"
    big, font = cards._fit(big, "Inter-Bold.otf", 64, WIDTH - 2 * PAD - 200)
    draw.text((PAD, HEAD + 70), big, font=font, fill=cards.INK, anchor="ls")
    draw.text((WIDTH - PAD, HEAD + 70), f"{int(share * 100)}%", font=cards._font("Inter-Bold.otf", 64),
              fill=cards.MAROON if share >= 1 else cards.INK, anchor="rs")
    top, bottom = HEAD + 100, HEAD + 160
    draw.rounded_rectangle((PAD, top, WIDTH - PAD, bottom), radius=30, fill=(255, 255, 255),
                           outline=cards.INK, width=5)
    if share > 0:
        right = PAD + max(60, int((WIDTH - 2 * PAD) * share))
        draw.rounded_rectangle((PAD, top, right, bottom), radius=30, fill=cards.LIME, outline=cards.INK, width=5)
    if data.get("note"):
        note, note_font = cards._fit(data["note"], "Inter-SemiBold.otf", 34, WIDTH - 2 * PAD)
        draw.text((PAD, bottom + 60), note, font=note_font, fill=cards.MUTED, anchor="ls")
    return _png(picture)


def render_counter(title, data):
    picture, draw = _head(title, 470)
    number, font = cards._fit(number_text(data.get("value") or 0), "Inter-Bold.otf", 200, WIDTH - 2 * PAD, 60)
    draw.text((PAD - 6, HEAD + 170), number, font=font, fill=cards.INK, anchor="ls")
    if data.get("caption"):
        caption, caption_font = cards._fit(data["caption"], "Inter-SemiBold.otf", 44, WIDTH - 2 * PAD)
        draw.text((PAD, HEAD + 240), caption, font=caption_font, fill=cards.MUTED, anchor="ls")
    return _png(picture)


def render_text(title, data, empty_text="—"):
    text = data.get("text") or empty_text
    size = 44
    font = cards._font("Inter-SemiBold.otf", size)
    lines = _wrap(text, font, WIDTH - 2 * PAD)
    while len(lines) > 9 and size > 26:
        size -= 4
        font = cards._font("Inter-SemiBold.otf", size)
        lines = _wrap(text, font, WIDTH - 2 * PAD)
    lines = lines[:14]
    step = int(size * 1.35)
    picture, draw = _head(title, HEAD + 40 + step * len(lines) + PAD)
    for n, line in enumerate(lines):
        draw.text((PAD, HEAD + 40 + step * n), line, font=font,
                  fill=cards.INK if data.get("text") else cards.MUTED, anchor="ls")
    return _png(picture)


def render_table(title, data, empty_text="—"):
    columns = data.get("columns") or ["—"]
    rows = data.get("rows") or []
    row_h = 74
    height = HEAD + row_h * (1 + max(1, len(rows))) + PAD
    picture, draw = _head(title, height)
    width = (WIDTH - 2 * PAD) // len(columns)
    top = HEAD
    draw.rounded_rectangle((PAD, top, WIDTH - PAD, top + row_h), radius=18, fill=cards.INK)
    for n, name in enumerate(columns):
        shown, font = cards._fit(name, "Inter-Bold.otf", 32, width - 30, 18)
        draw.text((PAD + n * width + 22, top + row_h // 2), shown, font=font, fill=cards.CREAM, anchor="lm")
    if not rows:
        draw.text((PAD + 22, top + row_h + row_h // 2), empty_text, font=cards._font("Inter-SemiBold.otf", 30),
                  fill=cards.MUTED, anchor="lm")
    for index, row in enumerate(rows):
        y = top + row_h * (index + 1)
        if index % 2:
            draw.rectangle((PAD, y, WIDTH - PAD, y + row_h), fill=(232, 227, 224))
        for n in range(len(columns)):
            cell = row[n] if n < len(row) else ""
            shown, font = cards._fit(cell, "Inter-SemiBold.otf" if n else "Inter-Bold.otf", 32, width - 30, 18)
            draw.text((PAD + n * width + 22, y + row_h // 2), shown, font=font, fill=cards.INK, anchor="lm")
    return _png(picture)


def texts_of(card):
    """Все тексты картинки, где могут быть переменные: [(путь, значение)]."""
    data = card["data"]
    found = [("title", card["title"])]
    for key in ("unit", "note", "caption", "text"):
        if data.get(key):
            found.append((key, data[key]))
    for r, row in enumerate(data.get("rows") or []):
        if isinstance(row, list):
            found += [(f"cell:{r}:{c}", cell) for c, cell in enumerate(row)]
    return found


def render(card, title, data, empty_text="—"):
    template = card["template"]
    if template == "progress":
        return render_progress(title, data)
    if template == "counter":
        return render_counter(title, data)
    if template == "text":
        return render_text(title, data, empty_text)
    if template == "table":
        return render_table(title, data, empty_text)
    return cards.render_rating(title, sorted(data.get("rows") or [], key=lambda row: (-row["score"],
                                                                                       row["name"].lower())),
                               empty_text)
