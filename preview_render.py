"""
Картинка-предпросмотр сообщения: бот рисует PNG так, как сообщение будет
выглядеть в Discord (как превью на сайте Carl-bot), — все части build'а
сразу, со всеми embed'ами, полями, картинками и кнопками.

Устроено в два слоя:
- render_parts() — асинхронный: собирает части сообщения (actions.message_parts),
  скачивает картинки и эмодзи, имена упоминаний берёт с сервера;
- render() — синхронный, только Pillow: раскладывает и рисует. Запускается
  в отдельном потоке, чтобы не держать бота.

Размеры ниже — в «пикселях Discord»; картинка рисуется в SCALE раз крупнее,
чтобы текст был чётким при открытии.
"""

import asyncio
import io
import os
import re
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont

SCALE = 2
WIDTH = 620              # ширина картинки
GUTTER = 72              # отступ текста сообщения слева (аватарка 40 + поля)
EMBED_MAX = 520          # максимальная ширина embed'а вместе с полоской
MAX_HEIGHT = 6000        # длиннее — обрезаем
MAX_IMAGE_SIDE = 2048    # большие картинки уменьшаем до разбора

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONT_FILES = {
    "regular": "Inter-Regular.otf",
    "italic": "Inter-Italic.otf",
    "semibold": "Inter-SemiBold.otf",
    "semibold_italic": "Inter-SemiBoldItalic.otf",
    "bold": "Inter-Bold.otf",
    "bold_italic": "Inter-SemiBoldItalic.otf",
    "mono": "DejaVuSansMono.ttf",
}

TWEMOJI_URL = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72/{code}.png"
CUSTOM_EMOJI_URL = "https://cdn.discordapp.com/emojis/{id}.png?size=96"

THEMES = {
    "dark": {
        "bg": "#313338", "embed_bg": "#2B2D31", "text": "#DBDEE1", "header": "#F2F3F5",
        "muted": "#949BA4", "link": "#00A8FC", "code_bg": "#1E1F22", "code_border": "#1E1F22",
        "quote": "#4E5058", "mention_bg": "#3C4270", "mention_fg": "#C9CDFB",
        "spoiler": "#1E1F22", "placeholder": "#404249", "select_bg": "#1E1F22", "select_border": "#1E1F22",
        "buttons": {1: ("#5865F2", "#FFFFFF"), 2: ("#4E5058", "#FFFFFF"), 3: ("#248046", "#FFFFFF"),
                    4: ("#DA373C", "#FFFFFF"), 5: ("#4E5058", "#FFFFFF")},
    },
    "light": {
        "bg": "#FFFFFF", "embed_bg": "#F2F3F5", "text": "#313338", "header": "#060607",
        "muted": "#5C5E66", "link": "#006CE7", "code_bg": "#EBEDEF", "code_border": "#E3E5E8",
        "quote": "#C4C9CE", "mention_bg": "#E6E8FD", "mention_fg": "#505CDC",
        "spoiler": "#E3E5E8", "placeholder": "#E3E5E8", "select_bg": "#EBEDEF", "select_border": "#E3E5E8",
        "buttons": {1: ("#5865F2", "#FFFFFF"), 2: ("#6D6F78", "#FFFFFF"), 3: ("#248046", "#FFFFFF"),
                    4: ("#DA373C", "#FFFFFF"), 5: ("#6D6F78", "#FFFFFF")},
    },
}

# Подписи — из каталога texts.py (их передаёт render_parts); здесь — запасные.
DEFAULT_LABELS = {
    "bot_tag": "БОТ", "time": "Сегодня, в {time}", "image_missing": "картинка не загрузилась",
    "select": "Сделайте выбор", "unknown_user": "неизвестный", "unknown_role": "неизвестная роль",
    "unknown_channel": "неизвестный-канал", "truncated": "…сообщение длиннее, дальше не показано",
}


# ============================================================
# ЭМОДЗИ
# ============================================================

_EMOJI_CHAR = "\U0001F000-\U0001FAFF☀-➿⬀-⯿⌀-⏿←-⇿▪-◾" \
              "⤴⤵〰〽㊗㊙©®‼⁉™ℹⓂ"
EMOJI_RE = re.compile(
    "[\U0001F1E6-\U0001F1FF]{2}"
    "|[0-9#*]️?⃣"
    f"|[{_EMOJI_CHAR}][︎️]?[\U0001F3FB-\U0001F3FF]?"
    f"(?:‍[{_EMOJI_CHAR}][️]?[\U0001F3FB-\U0001F3FF]?)*"
)
# Символы, которые и без U+FE0F показываются эмодзи (остальные из диапазонов
# выше без FE0F — обычный текст, например ★ или ✓).
EMOJI_PRESENTATION = set(
    "⌚⌛⏩⏪⏫⏬⏰⏳◽◾☔☕♈♉♊♋♌♍♎♏♐♑♒♓♿⚓⚡⚪⚫⚽⚾⛄⛅⛎⛔⛪⛲⛳⛵⛺⛽"
    "✅✊✋✨❌❎❓❔❕❗➕➖➗➰➿⬛⬜⭐⭕"
)
CUSTOM_EMOJI_RE = re.compile(r"<(a?):(\w{2,32}):(\d{15,25})>")


def emoji_matches(text):
    """[(start, end, sequence)] — юникодные эмодзи, которые Discord рисует картинкой."""
    found = []
    for match in EMOJI_RE.finditer(text or ""):
        seq = match.group(0)
        if len(seq) == 1 and ord(seq) < 0x1F000 and seq not in EMOJI_PRESENTATION:
            continue
        if seq.endswith("︎"):
            continue
        found.append((match.start(), match.end(), seq))
    return found


def twemoji_code(seq):
    keep_fe0f = "‍" in seq
    return "-".join(f"{ord(c):x}" for c in seq if c != "︎" and (keep_fe0f or c != "️"))


# ============================================================
# РАЗМЕТКА DISCORD → КУСОЧКИ ТЕКСТА
# ============================================================

INLINE_RE = re.compile(r"""
    (?P<code>`+)(?P<codetext>.+?)(?P=code)
  | \[(?P<ltext>[^\]\n]+)\]\(<?(?P<lurl>https?://[^)\s>]+)>?\)
  | <(?P<anim>a?):(?P<ename>\w{2,32}):(?P<eid>\d{15,25})>
  | <(?P<mkind>@!?|@&|\#)(?P<mid>\d{15,25})>
  | <t:(?P<tsv>-?\d{1,13})(?::(?P<tsf>[tTdDfFR]))?>
  | (?P<url>https?://[^\s<]+[^\s<.,:;"')\]!?])
  | (?P<everyone>@(?:everyone|here))
  | (?P<marker>\*\*\*|\*\*|\*|__|_|~~|\|\|)
  | \\(?P<esc>[\\*_~`|<>\[\]\#\-.])
""", re.X)

MARKER_FLAGS = {"***": ("bold", "italic"), "**": ("bold",), "*": ("italic",), "_": ("italic",),
                "__": ("underline",), "~~": ("strike",), "||": ("spoiler",)}

TS_FORMATS = {"t": "%H:%M", "T": "%H:%M:%S", "d": "%d.%m.%Y", "D": "%d %b %Y",
              "f": "%d %b %Y %H:%M", "F": "%A, %d %b %Y %H:%M", "R": "%d.%m.%Y %H:%M"}


def parse_inline(text, mentions, labels, markdown=True):
    """Строка -> [кусочек]; кусочек: {"kind": text|code|pill|emoji|cemoji, "text", "style", ...}."""
    pieces = []
    style = set()

    def add_text(value, extra=None):
        if not value:
            return
        last = 0
        for start, end, seq in emoji_matches(value):
            if start > last:
                pieces.append({"kind": "text", "text": value[last:start], "style": frozenset(style), **(extra or {})})
            pieces.append({"kind": "emoji", "code": twemoji_code(seq), "text": seq, "style": frozenset(style)})
            last = end
        if last < len(value):
            pieces.append({"kind": "text", "text": value[last:], "style": frozenset(style), **(extra or {})})

    if not markdown:
        position = 0
        for match in CUSTOM_EMOJI_RE.finditer(text or ""):
            add_text(text[position:match.start()])
            pieces.append({"kind": "cemoji", "id": match.group(3), "text": match.group(2), "style": frozenset()})
            position = match.end()
        add_text((text or "")[position:])
        return pieces

    position = 0
    for match in INLINE_RE.finditer(text):
        add_text(text[position:match.start()])
        position = match.end()
        group = match.lastgroup
        if match.group("code"):
            pieces.append({"kind": "code", "text": match.group("codetext"), "style": frozenset(style)})
        elif match.group("ltext"):
            add_text(match.group("ltext"), {"link": True})
        elif match.group("eid"):
            pieces.append({"kind": "cemoji", "id": match.group("eid"), "text": match.group("ename"),
                           "style": frozenset(style)})
        elif match.group("mid"):
            kind = {"@": "user", "@!": "user", "@&": "role", "#": "channel"}[match.group("mkind")]
            name, color = mention_name(mentions, labels, kind, match.group("mid"))
            pieces.append({"kind": "pill", "text": name, "color": color, "style": frozenset(style)})
        elif match.group("tsv"):
            try:
                moment = datetime.fromtimestamp(int(match.group("tsv")), tz=timezone.utc)
                shown = moment.strftime(TS_FORMATS[match.group("tsf") or "f"])
            except (ValueError, OverflowError, OSError):
                shown = match.group(0)
            pieces.append({"kind": "code", "text": shown, "style": frozenset(style), "soft": True})
        elif match.group("url"):
            add_text(match.group("url"), {"link": True})
        elif match.group("everyone"):
            pieces.append({"kind": "pill", "text": match.group("everyone"), "color": None, "style": frozenset(style)})
        elif match.group("esc"):
            add_text(match.group("esc"))
        elif group == "marker" or match.group("marker"):
            marker = match.group("marker")
            flags = MARKER_FLAGS[marker]
            if all(flag in style for flag in flags):
                style.difference_update(flags)
            elif marker in text[match.end():] and not (
                    marker == "_" and match.start() > 0 and text[match.start() - 1].isalnum()):
                style.update(flags)
            else:
                add_text(marker)
    add_text(text[position:])
    return pieces


def mention_name(mentions, labels, kind, raw_id):
    entry = (mentions or {}).get((kind, int(raw_id)))
    if kind == "user":
        return "@" + (entry or labels["unknown_user"]), None
    if kind == "role":
        name, color = entry if entry else (labels["unknown_role"], None)
        return "@" + name, color
    return "#" + (entry or labels["unknown_channel"]), None


# ============================================================
# РИСОВАНИЕ
# ============================================================

class Painter:
    """Шрифты, картинки и список операций. Координаты — в пикселях Discord."""

    def __init__(self, theme, labels, assets):
        self.theme = THEMES.get(theme, THEMES["dark"])
        self.labels = {**DEFAULT_LABELS, **(labels or {})}
        self.raw_assets = assets or {}
        self.images = {}
        self.fonts = {}

    def font(self, weight, size):
        key = (weight, size)
        if key not in self.fonts:
            path = os.path.join(FONT_DIR, FONT_FILES[weight])
            try:
                self.fonts[key] = ImageFont.truetype(path, int(round(size * SCALE)))
            except OSError:
                self.fonts[key] = ImageFont.load_default(size * SCALE)
        return self.fonts[key]

    def width(self, text, weight, size):
        return self.font(weight, size).getlength(text) / SCALE

    def image(self, key):
        """PIL-картинка по ключу (url / эмодзи) или None."""
        if key not in self.images:
            data = self.raw_assets.get(key)
            picture = None
            if data:
                try:
                    picture = Image.open(io.BytesIO(data))
                    picture.seek(0)
                    picture.draft("RGB", (MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
                    picture = picture.convert("RGBA")
                    picture.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
                except Exception:  # битый файл — рисуем заглушку
                    picture = None
            self.images[key] = picture
        return self.images[key]


def font_weight(style, base="regular"):
    bold = "bold" in style
    italic = "italic" in style
    if bold or base in ("semibold", "bold"):
        weight = "bold" if bold else base
        return f"{weight}_italic" if italic else weight
    return "italic" if italic else "regular"


class Block:
    """Нарисованный кусок: операции + размер. Можно вставить в другой со сдвигом."""

    def __init__(self):
        self.ops = []
        self.w = 0
        self.h = 0

    def add(self, other, dx, dy):
        for op in other.ops:
            self.ops.append(shift(op, dx, dy))
        self.w = max(self.w, dx + other.w)
        self.h = max(self.h, dy + other.h)


def shift(op, dx, dy):
    kind = op[0]
    if kind in ("rect", "line"):
        x0, y0, x1, y1 = op[1]
        return (kind, (x0 + dx, y0 + dy, x1 + dx, y1 + dy)) + op[2:]
    return (kind, op[1] + dx, op[2] + dy) + op[3:]


def line_height(size):
    return round(size * 1.3 + 1)


def layout_line_pieces(painter, pieces, max_width, size, color, base="regular", jumbo=False):
    """Кусочки одной строки разметки -> Block с переносами по словам."""
    theme = painter.theme
    lh = line_height(size)
    emoji_size = 48 if jumbo else round(size * 1.375)
    atoms = []
    for piece in pieces:
        kind = piece["kind"]
        if kind in ("emoji", "cemoji"):
            key = ("emoji", piece["code"]) if kind == "emoji" else ("cemoji", piece["id"])
            atoms.append({"kind": "img", "key": key, "w": emoji_size + 2, "fallback": piece["text"]})
            continue
        if kind == "code":
            mono = round(size * 0.85)
            for word in re.findall(r"\s+|\S+", piece["text"]):
                atoms.append({"kind": "code", "text": word, "w": painter.width(word, "mono", mono) + 4,
                              "size": mono, "soft": piece.get("soft")})
            continue
        weight = font_weight(piece["style"], base)
        fill = color
        if piece.get("link"):
            fill = theme["link"]
        bg = None
        if kind == "pill":
            fill = piece.get("color") or theme["mention_fg"]
            bg = theme["mention_bg"]
        if "spoiler" in piece["style"]:
            bg = theme["spoiler"]
        parts = [piece["text"]] if kind == "pill" else re.findall(r"\s+|\S+", piece["text"])
        for word in parts:
            atoms.append({"kind": "text", "text": word, "weight": weight, "fill": fill, "bg": bg,
                          "w": painter.width(word, weight, size) + (4 if kind == "pill" else 0),
                          "pill": kind == "pill", "style": piece["style"]})

    block = Block()
    lines = [[]]
    x = 0
    for atom in atoms:
        is_space = atom["kind"] in ("text", "code") and not atom["text"].strip()
        if is_space and x == 0:
            continue
        if x + atom["w"] > max_width and x > 0:
            lines.append([])
            x = 0
            if is_space:
                continue
        if atom["w"] > max_width and atom["kind"] == "text":
            # очень длинное слово — режем по буквам
            chunk = ""
            for char in atom["text"]:
                char_w = painter.width(chunk + char, atom["weight"], size)
                if x + char_w > max_width and chunk:
                    lines[-1].append(dict(atom, text=chunk, w=painter.width(chunk, atom["weight"], size)))
                    lines.append([])
                    x = 0
                    chunk = char
                else:
                    chunk += char
            atom = dict(atom, text=chunk, w=painter.width(chunk, atom["weight"], size))
        lines[-1].append(dict(atom, x=x))
        x += atom["w"]

    y = 0
    regular = painter.font(base if base in FONT_FILES else "regular", size)
    ascent, descent = (value / SCALE for value in regular.getmetrics())
    for line in lines:
        height = max([lh] + [emoji_size + 4 for atom in line if atom["kind"] == "img"])
        baseline = y + (height - (ascent + descent)) / 2 + ascent
        middle = y + height / 2
        for atom in line:
            ax = atom["x"]
            if atom["kind"] == "img":
                box = emoji_size
                block.ops.append(("img", ax + 1, middle - box / 2, box, box, atom["key"], 0, atom["fallback"]))
            elif atom["kind"] == "code":
                if atom["text"].strip():
                    block.ops.append(("rect", (ax, middle - lh / 2 + 1, ax + atom["w"], middle + lh / 2 - 1),
                                      theme["code_bg"], 3))
                block.ops.append(("text", ax + 2, baseline, atom["text"], ("mono", atom["size"]), color))
            else:
                if atom["bg"] and atom["text"].strip():
                    block.ops.append(("rect", (ax, middle - lh / 2 + 1, ax + atom["w"], middle + lh / 2 - 1),
                                      atom["bg"], 3))
                text_x = ax + (2 if atom["pill"] else 0)
                block.ops.append(("text", text_x, baseline, atom["text"], (atom["weight"], size), atom["fill"]))
                if atom["text"].strip():
                    if "underline" in atom["style"]:
                        block.ops.append(("line", (ax, baseline + 2, ax + atom["w"], baseline + 2), atom["fill"], 1))
                    if "strike" in atom["style"]:
                        mid = baseline - size * 0.3
                        block.ops.append(("line", (ax, mid, ax + atom["w"], mid), atom["fill"], 1))
        block.w = max(block.w, max((atom["x"] + atom["w"] for atom in line), default=0))
        y += height
    block.h = y
    return block


def layout_rich(painter, text, max_width, size=14, color=None, base="regular", markdown=True, jumbo=False):
    """Текст с разметкой Discord (несколько строк, цитаты, заголовки, код) -> Block."""
    theme = painter.theme
    color = color or theme["text"]
    block = Block()
    y = 0
    lines = (text or "").split("\n")
    quote_all = False
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if markdown and line.lstrip().startswith("```"):
            body = line.lstrip()[3:]
            code_lines = []
            if body.rstrip().endswith("```") and len(body.strip()) > 3:
                code_lines.append(body.rstrip()[:-3])
            else:
                while index < len(lines) and "```" not in lines[index]:
                    code_lines.append(lines[index])
                    index += 1
                if index < len(lines):
                    tail = lines[index].split("```")[0]
                    if tail:
                        code_lines.append(tail)
                    index += 1
            code = layout_code_block(painter, code_lines, max_width, size)
            block.add(code, 0, y + 2)
            y += code.h + 6
            continue
        indent = 0
        quoted = quote_all
        if markdown and line.startswith(">>> "):
            quote_all = quoted = True
            line = line[4:]
        elif markdown and (line.startswith("> ") or line == ">"):
            quoted = True
            line = line[2:]
        line_size, line_color, line_base = size, color, base
        if markdown:
            header = re.match(r"(#{1,3}) (.+)", line)
            subtext = re.match(r"-# (.+)", line)
            bullet = re.match(r"(\s*)([-*]|\d{1,3}\.) (.+)", line)
            if header:
                line_size = {1: 24, 2: 20, 3: 16}[len(header.group(1))]
                line_base, line_color = "bold", theme["header"]
                line = header.group(2)
                y += 4 if y else 0
            elif subtext:
                line_size, line_color = 12, theme["muted"]
                line = subtext.group(1)
            elif bullet:
                depth = min(len(bullet.group(1)) // 2, 3)
                marker = "•" if bullet.group(2) in "-*" else bullet.group(2)
                indent = 16 + depth * 16
                block.ops.append(("text", indent - 12 if marker == "•" else indent - 4 - painter.width(marker, base, size),
                                  y + line_height(size) * 0.76, marker, (base, size), color))
                line = bullet.group(3)
        if quoted:
            indent += 12
        pieces = parse_inline(line, painter.mentions, painter.labels, markdown) if line else []
        part = layout_line_pieces(painter, pieces, max_width - indent, line_size, line_color, line_base, jumbo)
        height = part.h or line_height(line_size)
        if quoted:
            block.ops.append(("rect", (0, y, 4, y + height), theme["quote"], 2))
        block.add(part, indent, y)
        y += height
    block.h = y
    return block


def layout_code_block(painter, code_lines, max_width, size):
    theme = painter.theme
    mono = round(size * 0.85)
    lh = line_height(mono)
    block = Block()
    inner = max_width - 16
    rows = []
    for line in code_lines or [""]:
        chunk = ""
        for char in line.expandtabs(4):
            if painter.width(chunk + char, "mono", mono) > inner and chunk:
                rows.append(chunk)
                chunk = ""
            chunk += char
        rows.append(chunk)
    height = len(rows) * lh + 14
    block.ops.append(("rect", (0, 0, max_width, height), theme["code_bg"], 4))
    ascent, descent = (value / SCALE for value in painter.font("mono", mono).getmetrics())
    for number, row in enumerate(rows):
        baseline = 7 + number * lh + (lh - ascent - descent) / 2 + ascent
        block.ops.append(("text", 8, baseline, row, ("mono", mono), theme["text"]))
    block.w = max_width
    block.h = height
    return block


def fit(size, max_w, max_h):
    w, h = size
    ratio = min(max_w / w, max_h / h, 1)
    return max(1, round(w * ratio)), max(1, round(h * ratio))


def color_hex(value):
    if value is None:
        return None
    return f"#{int(value) & 0xFFFFFF:06X}"


def layout_embed(painter, data, max_width=EMBED_MAX):
    """Один embed (словарь discord.Embed.to_dict()) -> Block."""
    theme = painter.theme
    block = Block()
    inner_max = max_width - 4 - 12 - 16
    thumbnail = (data.get("thumbnail") or {}).get("url")
    text_max = inner_max - (80 + 16 if thumbnail else 0)
    body = Block()
    y = 0

    def gap(amount):
        return amount if y else 0

    author = data.get("author") or {}
    if author.get("name"):
        y += gap(8)
        x = 0
        if author.get("icon_url"):
            body.ops.append(("img", 0, y, 24, 24, author["icon_url"], -1, None))
            x = 32
        name = layout_line_pieces(painter, parse_inline(author["name"], None, painter.labels, False),
                                  text_max - x, 14, theme["header"], "semibold")
        body.add(name, x, y + max(0, (24 - name.h) / 2))
        y += max(24, name.h)
    if data.get("title"):
        y += gap(8)
        title = layout_rich(painter, data["title"], text_max, 16,
                            theme["link"] if data.get("url") else theme["header"], "semibold")
        body.add(title, 0, y)
        y += title.h
    description = data.get("description")
    if description and description.strip("​"):
        y += gap(8)
        text = layout_rich(painter, description, text_max, 14)
        body.add(text, 0, y)
        y += text.h

    fields = data.get("fields") or []
    if fields:
        y += gap(8)
        per_row = 2 if thumbnail else 3
        rows, current = [], []
        for field in fields:
            if field.get("inline") and len(current) < per_row:
                current.append(field)
                continue
            if current:
                rows.append(current)
                current = []
            if field.get("inline"):
                current.append(field)
            else:
                rows.append([field])
        if current:
            rows.append(current)
        for row in rows:
            columns = len(row) if row[0].get("inline") else 1
            column_w = (text_max - 8 * (columns - 1)) / columns
            row_h = 0
            for number, field in enumerate(row):
                cell = Block()
                name = layout_rich(painter, field.get("name") or "", column_w, 14, theme["header"], "semibold")
                cell.add(name, 0, 0)
                value = layout_rich(painter, field.get("value") or "", column_w, 14)
                cell.add(value, 0, name.h + 2)
                body.add(cell, number * (column_w + 8), y)
                row_h = max(row_h, cell.h)
            if columns > 1 or len(row) > 1:
                body.w = max(body.w, text_max)
            y += row_h + 8
        y -= 8

    if thumbnail:
        picture = painter.image(thumbnail)
        tw, th = fit(picture.size, 80, 80) if picture else (80, 80)
        body.ops.append(("img", text_max + 16, 8 if author.get("name") or data.get("title") else 0,
                         tw, th, thumbnail, 3, None))
        body.w = max(body.w, text_max + 16 + tw)
        y = max(y, th + 8)
    image = (data.get("image") or {}).get("url")
    if image:
        y += gap(16)
        picture = painter.image(image)
        iw, ih = fit(picture.size, inner_max, 300) if picture else (inner_max, 120)
        body.ops.append(("img", 0, y, iw, ih, image, 4, None))
        body.w = max(body.w, iw)
        y += ih
    footer = data.get("footer") or {}
    stamp = data.get("timestamp")
    if footer.get("text") or stamp:
        y += gap(8)
        x = 0
        if footer.get("icon_url") and footer.get("text"):
            body.ops.append(("img", 0, y, 20, 20, footer["icon_url"], -1, None))
            x = 28
        text = footer.get("text") or ""
        if stamp:
            try:
                moment = datetime.fromisoformat(stamp).astimezone().strftime("%H:%M")
            except ValueError:
                moment = ""
            shown = painter.labels["time"].format(time=moment)
            text = f"{text} • {shown}" if text else shown
        note = layout_line_pieces(painter, parse_inline(text, None, painter.labels, False),
                                  inner_max - x, 12, theme["muted"])
        body.add(note, x, y + max(0, (20 - note.h) / 2))
        y += max(20, note.h)
    body.h = y

    width = 4 + 12 + max(body.w, 1) + 16
    height = 8 + body.h + 16
    block.ops.append(("rect", (0, 0, width, height), theme["embed_bg"], 4))
    block.ops.append(("rect", (0, 0, 4, height), color_hex(data.get("color")) or "#1E1F22", -2))
    block.add(body, 16, 8)
    block.w, block.h = width, height
    return block


def layout_components(painter, rows):
    """Ряды кнопок и меню (View.to_components()) -> Block."""
    theme = painter.theme
    block = Block()
    y = 0
    for row in rows:
        x = 0
        row_h = 0
        for component in row.get("components") or []:
            kind = component.get("type")
            if kind == 2:
                item = layout_button(painter, component)
                if x and x + item.w > WIDTH - GUTTER - 16:
                    y += row_h + 8
                    x, row_h = 0, 0
                block.add(item, x, y)
                x += item.w + 8
                row_h = max(row_h, item.h)
            elif kind in (3, 5, 6, 7, 8):
                select = Block()
                width = 400
                select.ops.append(("rect", (0, 0, width, 40), theme["select_bg"], 4))
                placeholder = component.get("placeholder") or painter.labels["select"]
                text = layout_line_pieces(painter, parse_inline(placeholder, None, painter.labels, False),
                                          width - 48, 14, theme["muted"])
                select.add(text, 12, (40 - text.h) / 2)
                select.ops.append(("chevron", width - 24, 20, theme["text"]))
                select.w, select.h = width, 40
                block.add(select, 0, y)
                row_h = 40
        y += row_h + 8
    block.h = max(0, y - 8)
    return block


def layout_button(painter, component):
    theme = painter.theme
    style = component.get("style") or 2
    bg, fg = theme["buttons"].get(style, theme["buttons"][2])
    pieces = []
    emoji = component.get("emoji") or {}
    if emoji.get("id"):
        pieces.append({"kind": "cemoji", "id": str(emoji["id"]), "text": emoji.get("name") or "", "style": frozenset()})
    elif emoji.get("name"):
        pieces.extend(parse_inline(emoji["name"], None, painter.labels, False))
    label = component.get("label") or ""
    if label:
        if pieces:
            pieces.append({"kind": "text", "text": " ", "style": frozenset()})
        pieces.extend({**piece, "style": frozenset()} for piece in parse_inline(label, None, painter.labels, False))
    content = layout_line_pieces(painter, pieces, 360, 14, fg, "semibold")
    content.h = max(content.h, 18)
    extra = 14 if style == 5 else 0
    width = content.w + 32 + extra
    block = Block()
    block.ops.append(("rect", (0, 0, width, 32), bg, 8))
    block.add(content, 16, (32 - content.h) / 2)
    if style == 5:
        block.ops.append(("arrow", width - 16 - 10, 16, fg))
    block.w, block.h = width, 32
    return block


def layout_message(painter, message, first, author):
    """Одно сообщение: шапка (у первого), текст, embed'ы, компоненты."""
    theme = painter.theme
    block = Block()
    y = 0
    if first:
        block.ops.append(("img", -GUTTER + 16, 2, 40, 40, author.get("avatar"), -1, None))
        name = author.get("name") or "Bot"
        name_w = painter.width(name, "semibold", 16)
        block.ops.append(("text", 0, 17, name, ("semibold", 16), theme["header"]))
        tag = painter.labels["bot_tag"]
        tag_w = painter.width(tag, "semibold", 10) + 8
        block.ops.append(("rect", (name_w + 6, 4, name_w + 6 + tag_w, 19), "#5865F2", 3))
        block.ops.append(("text", name_w + 10, 15.5, tag, ("semibold", 10), "#FFFFFF"))
        block.ops.append(("text", name_w + 6 + tag_w + 8, 16, painter.labels["time"].format(time=author.get("time", "")),
                          ("regular", 12), theme["muted"]))
        y = 24
    else:
        # продолжение — как в Discord: без шапки, время сбоку
        block.ops.append(("text", -GUTTER + 18, 15, author.get("time", ""), ("regular", 11), theme["muted"]))
    content = message.get("content")
    width = WIDTH - GUTTER - 16
    if content:
        only_emoji = not CUSTOM_EMOJI_RE.sub("", EMOJI_RE.sub("", content)).strip() and \
            len(emoji_matches(content)) + len(CUSTOM_EMOJI_RE.findall(content)) <= 30
        text = layout_rich(painter, content, width, 16, jumbo=only_emoji)
        block.add(text, 0, y)
        y += text.h
    for embed in message.get("embeds") or []:
        y += 4 if y else 2
        item = layout_embed(painter, embed)
        block.add(item, 0, y)
        y += item.h
    rows = message.get("components") or []
    if rows:
        y += 8 if y else 2
        item = layout_components(painter, rows)
        block.add(item, 0, y)
        y += item.h
    block.h = max(y, 22)
    return block


def render(messages, author=None, *, theme="dark", labels=None, assets=None, mentions=None):
    """
    messages: [{"content", "embeds": [Embed.to_dict()], "components": View.to_components()}]
    author: {"name", "avatar" (ключ в assets), "time"}; assets: {ключ: bytes}.
    -> PNG (bytes).
    """
    painter = Painter(theme, labels, assets)
    painter.mentions = mentions or {}
    author = author or {}
    page = Block()
    y = 16
    for number, message in enumerate(messages):
        item = layout_message(painter, message, number == 0, author)
        page.add(item, GUTTER, y)
        y += item.h + (4 if number < len(messages) - 1 else 0)
    height = y + 16
    truncated = height > MAX_HEIGHT
    if truncated:
        height = MAX_HEIGHT
    canvas = Image.new("RGBA", (WIDTH * SCALE, int(height * SCALE)), painter.theme["bg"])
    draw = ImageDraw.Draw(canvas)
    for op in page.ops:
        paint(canvas, draw, painter, op)
    if truncated:
        draw.rectangle((0, (height - 28) * SCALE, WIDTH * SCALE, height * SCALE), fill=painter.theme["embed_bg"])
        draw.text((GUTTER * SCALE, (height - 9) * SCALE), painter.labels["truncated"],
                  font=painter.font("regular", 12), fill=painter.theme["muted"], anchor="ls")
    output = io.BytesIO()
    canvas.convert("RGB").save(output, "PNG", optimize=True)
    return output.getvalue()


def S(value):
    return int(round(value * SCALE))


def paint(canvas, draw, painter, op):
    kind = op[0]
    if kind == "rect":
        (x0, y0, x1, y1), fill, radius = op[1], op[2], op[3]
        box = (S(x0), S(y0), S(x1) - 1, S(y1) - 1)
        if box[2] < box[0] or box[3] < box[1]:
            return
        if radius < 0:  # полоска embed'а: скруглена только слева
            draw.rounded_rectangle(box, radius=S(-radius), fill=fill, corners=(True, False, False, True))
        elif radius:
            draw.rounded_rectangle(box, radius=S(radius), fill=fill)
        else:
            draw.rectangle(box, fill=fill)
    elif kind == "line":
        (x0, y0, x1, y1), fill, width = op[1], op[2], op[3]
        draw.line((S(x0), S(y0), S(x1), S(y1)), fill=fill, width=max(1, S(width)))
    elif kind == "text":
        _, x, y, text, (weight, size), fill = op
        draw.text((S(x), S(y)), text, font=painter.font(weight, size), fill=fill, anchor="ls")
    elif kind == "chevron":
        _, x, y, fill = op
        draw.line((S(x - 5), S(y - 2), S(x), S(y + 3), S(x + 5), S(y - 2)), fill=fill, width=S(2), joint="curve")
    elif kind == "arrow":
        _, x, y, fill = op
        draw.line((S(x + 1), S(y + 4), S(x + 8), S(y - 3)), fill=fill, width=S(1.5))
        draw.line((S(x + 3), S(y - 3), S(x + 8), S(y - 3), S(x + 8), S(y + 2)), fill=fill, width=S(1.5))
    elif kind == "img":
        _, x, y, w, h, key, radius, fallback = op
        picture = painter.image(key) if key else None
        size = (max(1, S(w)), max(1, S(h)))
        if picture is None:
            if fallback:  # эмодзи не скачался — пишем символом
                draw.text((S(x), S(y + h * 0.8)), fallback, font=painter.font("regular", h * 0.8),
                          fill=painter.theme["text"], anchor="ls")
                return
            draw.rounded_rectangle((S(x), S(y), S(x) + size[0], S(y) + size[1]),
                                   radius=S(max(radius, 0)) if radius >= 0 else min(size) // 2,
                                   fill=painter.theme["placeholder"])
            if radius >= 0 and w > 100:
                draw.text((S(x + w / 2), S(y + h / 2)), painter.labels["image_missing"],
                          font=painter.font("regular", 12), fill=painter.theme["muted"], anchor="mm")
            return
        picture = picture.resize(size, Image.LANCZOS)
        mask = Image.new("L", size, 0)
        mask_draw = ImageDraw.Draw(mask)
        if radius < 0:
            mask_draw.ellipse((0, 0, size[0] - 1, size[1] - 1), fill=255)
        else:
            mask_draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=S(radius), fill=255)
        alpha = picture.getchannel("A")
        mask = Image.composite(alpha, Image.new("L", size, 0), mask)
        canvas.paste(picture, (S(x), S(y)), mask)


# ============================================================
# ИЗ DISCORD-ОБЪЕКТОВ И С ЗАГРУЗКОЙ КАРТИНОК
# ============================================================

def messages_from_parts(parts):
    """Части сообщения (actions.message_parts) -> словари для render()."""
    from actions import part_embeds
    messages = []
    for part in parts or []:
        view = part.get("view")
        messages.append({
            "content": part.get("content") or "",
            "embeds": [embed.to_dict() for embed in part_embeds(part)],
            "components": view.to_components() if view is not None else [],
        })
    return messages


def _texts(messages):
    for message in messages:
        yield message.get("content") or ""
        for embed in message.get("embeds") or []:
            yield embed.get("title") or ""
            yield embed.get("description") or ""
            yield (embed.get("author") or {}).get("name") or ""
            yield (embed.get("footer") or {}).get("text") or ""
            for field in embed.get("fields") or []:
                yield field.get("name") or ""
                yield field.get("value") or ""
        for row in message.get("components") or []:
            for component in row.get("components") or []:
                yield component.get("label") or ""
                yield component.get("placeholder") or ""
                emoji = component.get("emoji") or {}
                if emoji.get("id"):
                    yield f"<:{emoji.get('name') or 'e'}:{emoji['id']}>"
                elif emoji.get("name"):
                    yield emoji["name"]


def wanted_assets(messages, author):
    """-> {ключ: url} всего, что надо скачать."""
    urls = {}
    if author.get("avatar"):
        urls[author["avatar"]] = author["avatar"]
    for message in messages:
        for embed in message.get("embeds") or []:
            for key in ("author", "footer"):
                icon = (embed.get(key) or {}).get("icon_url")
                if icon:
                    urls[icon] = icon
            for key in ("thumbnail", "image"):
                url = (embed.get(key) or {}).get("url")
                if url:
                    urls[url] = url
    for text in _texts(messages):
        for match in CUSTOM_EMOJI_RE.finditer(text):
            urls[("cemoji", match.group(3))] = CUSTOM_EMOJI_URL.format(id=match.group(3))
        for _, _, seq in emoji_matches(text):
            code = twemoji_code(seq)
            urls[("emoji", code)] = TWEMOJI_URL.format(code=code)
    return urls


def mentions_for(guild, messages):
    """Имена упоминаний с сервера: {(вид, id): имя | (имя, цвет)}."""
    found = {}
    if guild is None:
        return found
    pattern = re.compile(r"<(@!?|@&|#)(\d{15,25})>")
    for text in _texts(messages):
        for kind, raw in pattern.findall(text):
            ident = int(raw)
            if kind == "@&":
                role = guild.get_role(ident)
                if role:
                    color = color_hex(role.color.value) if role.color.value else None
                    found[("role", ident)] = (role.name, color)
            elif kind == "#":
                channel = guild.get_channel_or_thread(ident) if hasattr(guild, "get_channel_or_thread") \
                    else guild.get_channel(ident)
                if channel:
                    found[("channel", ident)] = channel.name
            else:
                member = guild.get_member(ident)
                if member:
                    found[("user", ident)] = member.display_name
    return found


_emoji_cache = {}
EMOJI_CACHE_LIMIT = 500


async def fetch_assets(urls):
    """Скачать всё параллельно (не больше 8 сразу). Эмодзи кэшируются."""
    from core import fetch_bytes
    limit = asyncio.Semaphore(8)
    result = {}

    async def one(key, url):
        cacheable = isinstance(key, tuple)
        if cacheable and key in _emoji_cache:
            result[key] = _emoji_cache[key]
            return
        async with limit:
            data = await fetch_bytes(url, timeout=6)
        if data:
            result[key] = data
            if cacheable:
                if len(_emoji_cache) >= EMOJI_CACHE_LIMIT:
                    _emoji_cache.pop(next(iter(_emoji_cache)))
                _emoji_cache[key] = data

    await asyncio.gather(*(one(key, url) for key, url in urls.items()))
    return result


async def render_parts(parts, *, guild=None, author=None, theme="dark", labels=None):
    """Части сообщения -> PNG (bytes). author: {"name", "avatar" (url), "time"}."""
    messages = messages_from_parts(parts)
    author = dict(author or {})
    author.setdefault("time", datetime.now().strftime("%H:%M"))
    assets = await fetch_assets(wanted_assets(messages, author))
    mentions = mentions_for(guild, messages)
    return await asyncio.to_thread(render, messages, author, theme=theme, labels=labels,
                                   assets=assets, mentions=mentions)
