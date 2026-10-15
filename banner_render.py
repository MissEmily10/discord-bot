"""
Баннеры-шаблоны: фон из Figma (panel_art/<экран>.base.png) + текст, который
бот дописывает сам — название должности по-английски («your moderator form
has been accepted!») или по-русски крупно на карточке формы.

Шрифты — как в макете: Coolvetica и Dunkin Sans. Их файлы нужно положить в
fonts/banner/ (см. fonts/README.md); пока их нет — рисуем похожим Inter.

Шаблон работает, пока в веб-панели у баннера стоит картинка по умолчанию.
Загрузишь туда свою — бот покажет её как есть, без текста.
"""

import glob
import io
import os

from PIL import Image, ImageDraw, ImageFont

import core
from texts import CATALOG

ROOT = os.path.dirname(os.path.abspath(__file__))
ART_DIR = os.path.join(ROOT, "panel_art")
FONT_DIR = os.path.join(ROOT, "fonts")
BANNER_FONT_DIR = os.path.join(FONT_DIR, "banner")

# Файл шрифта ищется по началу имени (любое расширение .otf/.ttf), потом — запасной.
FONTS = {
    "coolvetica": (("Coolvetica-Regular", "Coolvetica Rg"), "Inter-Bold.otf"),
    "dunkin": (("DunkinSans-Regular", "Dunkin Sans Regular"), "Inter-SemiBold.otf"),
    "drunk": (("DrunkFonts-Regular", "DRUNK FONTS", "Drunk Fonts", "DrunkFonts", "drunk"), "Inter-Bold.otf"),
}

# Координаты — из макета (1200×200): левый край текста, центр строки по высоте.
TEMPLATES = {
    "forms.approved": {
        "text": "your {role} form   has  . been", "font": "dunkin", "size": 22,
        "x": 40, "y": 29 + 11, "max_width": 640, "color": (90, 82, 84),
    },
    "forms.rejected": {
        "text": "      an answer to your {role} form", "font": "dunkin", "size": 22,
        "x": -27, "y": 147 + 11, "max_width": 700, "color": (90, 82, 84),
    },
    "forms.public_card": [
        # надпись сверху — своя у пака («набор! набор!…», «напиши нам!…»), у формы можно задать свою
        {"text": "{tagline}", "font": "drunk", "size": 22, "x": 48, "y": 31 + 11, "max_width": 660,
         "color": (90, 82, 84)},
        # below_previous: значки над буквами (й, ё) и сами буквы не касаются надписи сверху
        {"text": "{name}", "font": "coolvetica", "size": 166, "min_size": 56,
         "x": 40, "y": 36 + 58, "two_line_y": 116, "max_width": 670, "color": (32, 28, 30), "lower": True,
         "below_previous": True},
    ],
}

_font_cache = {}


def font_path(kind):
    names, fallback = FONTS[kind]
    for name in names:
        for path in sorted(glob.glob(os.path.join(BANNER_FONT_DIR, name + "*"))):
            if path.lower().endswith((".otf", ".ttf")):
                return path
    return os.path.join(FONT_DIR, fallback)


def has_design_fonts():
    return all(os.path.dirname(font_path(kind)) == BANNER_FONT_DIR for kind in FONTS)


_cmap_cache = {}


def covers(path, text):
    """Есть ли в файле шрифта все буквы текста (у Dunkin, например, нет кириллицы)."""
    if path not in _cmap_cache:
        try:
            from fontTools.ttLib import TTFont
            _cmap_cache[path] = set(TTFont(path, lazy=True).getBestCmap() or {})
        except Exception:  # нет fontTools или битый файл — считаем, что всё есть
            _cmap_cache[path] = None
    cmap = _cmap_cache[path]
    return cmap is None or all(ord(ch) in cmap for ch in text if not ch.isspace())


def kind_for(kind, text):
    """Шрифт макета, а если в нём нет нужных букв — запасной Inter того же веса."""
    path = font_path(kind)
    if os.path.dirname(path) == BANNER_FONT_DIR and not covers(path, text):
        return "fallback:" + FONTS[kind][1]
    return kind


def _font(kind, size):
    key = (kind, size)
    if key not in _font_cache:
        path = os.path.join(FONT_DIR, kind[9:]) if kind.startswith("fallback:") else font_path(kind)
        _font_cache[key] = ImageFont.truetype(path, size)
    return _font_cache[key]


def is_template(screen):
    """Шаблон действует, пока баннер в веб-панели не заменён своей картинкой."""
    if screen not in TEMPLATES:
        return False
    key = f"{screen}.banner"
    return core.get_setting(key) == CATALOG.get(key) and os.path.exists(base_path(screen))


def base_path(screen):
    value = CATALOG.get(f"{screen}.banner") or ""
    return os.path.join(ART_DIR, value[4:]) if value.startswith("art:") else ""


def fit_text(spec, text):
    """
    Длинное название: сначала уменьшаем шрифт в одну строку, потом — в две
    строки (крупно читается лучше мелкого), и только в крайнем случае «…».
    -> ([строки], шрифт)
    """
    width, size, kind = spec["max_width"], spec["size"], kind_for(spec["font"], text)
    min_size = spec.get("min_size", size)
    fits = lambda line, font: font.getlength(line) <= width
    two_lines_from = max(min_size, int(size * 0.6))
    while size > two_lines_from and not fits(text, _font(kind, size)):
        size = int(size * 0.92)
    font = _font(kind, size)
    if fits(text, font) or " " not in text or min_size == spec["size"]:
        return [_cut(text, font, width)], font
    words = text.split(" ")
    size = int(spec["size"] * 0.44)
    while size >= min_size:
        font = _font(kind, size)
        splits = [(" ".join(words[:k]), " ".join(words[k:])) for k in range(1, len(words))]
        splits.sort(key=lambda pair: max(font.getlength(pair[0]), font.getlength(pair[1])))
        best = splits[0]
        if fits(best[0], font) and fits(best[1], font):
            return list(best), font
        size = int(size * 0.92)
    font = _font(kind, min_size)
    return [_cut(text, font, width)], font


def _cut(text, font, width):
    while font.getlength(text) > width and len(text) > 4:
        text = text[:-2].rstrip() + "…"
    return text


GAP = 7  # px между надписью сверху и заголовком (и между строками заголовка)


def _box(draw, piece, font):
    (x, y), text = piece
    return draw.textbbox((x, y), text, font=font, anchor="lm")


def _ink(draw, pieces, font):
    boxes = [_box(draw, piece, font) for piece in pieces]
    return min(b[1] for b in boxes), max(b[3] for b in boxes)


def _layout(draw, spec, lines, font):
    """Строки как в макете; вторая строка не налезает на хвосты первой (д, щ, у)."""
    step = font.size * 0.8
    center = spec.get("two_line_y", spec["y"]) if len(lines) > 1 else spec["y"]
    ys = [center - step * (len(lines) - 1) / 2]
    for number in range(1, len(lines)):
        y = ys[-1] + step
        above = draw.textbbox((spec["x"], ys[-1]), lines[number - 1], font=font, anchor="lm")[3]
        top = draw.textbbox((spec["x"], y), lines[number], font=font, anchor="lm")[1]
        ys.append(y + max(0, above + GAP / 2 - top))
    return [((spec["x"], y), line) for y, line in zip(ys, lines)]


def _sticking_up(draw, piece, font, limit):
    """Горизонтальные отрезки букв первой строки, которые поднимаются выше limit (значок й, ё…)."""
    (x, y), line = piece
    spans = []
    for index, char in enumerate(line):
        if char.isspace():
            continue
        left = x + font.getlength(line[:index])
        box = draw.textbbox((left, y), char, font=font, anchor="lm")
        if box[1] < limit:
            spans.append((box[0] - GAP, box[2] + GAP, box[1]))
    return spans


def _words(draw, piece, font):
    """Надпись сверху -> слова с их x (лишние пробелы макета сохраняются)."""
    (x, y), line = piece
    words, index = [], 0
    for word in line.split(" "):
        if word:
            words.append([x + font.getlength(line[:index]), word])
        index += len(word) + 1
    return words, y


def respace_tagline(draw, tagline, font, spans, max_right):
    """
    Раздвинуть пробелы надписи сверху так, чтобы торчащие вверх части заголовка
    (значок над «й» и т. п.) встали в просветы между словами. -> куски | None.
    """
    if len(tagline) != 1:
        return None
    words, y = _words(draw, tagline[0], font)
    shift = 0
    for word in words:
        word[0] += shift
        moved = True
        while moved:
            moved = False
            right = word[0] + font.getlength(word[1])
            for left_edge, right_edge, _ in spans:
                if word[0] < right_edge and right > left_edge:
                    shift += right_edge - word[0]
                    word[0] = right_edge
                    moved = True
                    break
    if not words or words[-1][0] + font.getlength(words[-1][1]) > max_right:
        return None
    return [((x, y), word) for x, word in words]


def place_title(draw, spec, lines, font, tagline, tagline_font, bottom_limit):
    """
    Заголовок под надписью сверху. Если значок над буквой (й, ё) или буква
    задевает надпись:
    1) раздвигаем пробелы надписи, чтобы значок встал между словами;
    2) иначе сдвигаем заголовок вниз, если снизу есть место;
    3) иначе понемногу уменьшаем шрифт, но не мельче min_size — чтобы читалось.
    -> (куски заголовка, шрифт, куски надписи)
    """
    min_size = spec.get("min_size", font.size)
    tag_top, tag_bottom = _ink(draw, tagline, tagline_font)
    limit = tag_bottom + GAP
    max_right = spec["x"] + spec["max_width"]
    while True:
        placed = _layout(draw, spec, lines, font)
        top, bottom = _ink(draw, placed, font)
        if top >= limit:
            return placed, font, tagline
        spans = _sticking_up(draw, placed[0], font, limit)
        if spans and min(s[2] for s in spans) >= GAP:  # торчит, но в картинку влезает
            respaced = respace_tagline(draw, tagline, tagline_font, spans, max_right)
            if respaced:
                return placed, font, respaced
        need = limit - top
        if bottom + need <= bottom_limit:
            return [((x, y + need), line) for (x, y), line in placed], font, tagline
        if font.size <= min_size:
            # мельче нельзя: лучше чуть срезать хвосты снизу, чем налезть на надпись
            return [((x, y + need), line) for (x, y), line in placed], font, tagline
        font = font.font_variant(size=max(min_size, int(font.size * 0.95)))


def render(screen, **values):
    """-> PNG (bytes) баннера с подставленным текстом."""
    layers = TEMPLATES[screen]
    picture = Image.open(base_path(screen)).convert("RGBA")
    draw = ImageDraw.Draw(picture)
    values = {"tagline": core.t("packs.tagline_default"), **values}
    drawn = []  # [(куски, шрифт, цвет)]
    for spec in layers if isinstance(layers, list) else [layers]:
        text = spec["text"].format_map({k: str(v) for k, v in values.items()})
        if spec.get("lower"):
            text = text.lower()
        lines, font = fit_text(spec, text)
        if spec.get("below_previous") and drawn:
            above, above_font, above_color = drawn[-1]
            placed, font, above = place_title(draw, spec, lines, font, above, above_font, picture.height - 4)
            drawn[-1] = (above, above_font, above_color)
        else:
            placed = _layout(draw, spec, lines, font)
        drawn.append((placed, font, spec["color"]))
    for pieces, font, color in drawn:
        for (x, y), text in pieces:
            draw.text((x, y), text, font=font, fill=color, anchor="lm")
    output = io.BytesIO()
    picture.save(output, "PNG", optimize=True)
    return output.getvalue()


FRAME_MS = 2200  # сколько показывается каждая должность в анимированном баннере пака


def pack_gif(cards, frame_ms=FRAME_MS):
    """
    Анимированный баннер пака: карточки форм (название + надпись сверху)
    сменяют друг друга по кругу. cards — [(название, надпись сверху)]. -> GIF (bytes)
    """
    frames = []
    for name, tagline in cards:
        values = {"name": name}
        if tagline:
            values["tagline"] = tagline
        frame = Image.open(io.BytesIO(render("forms.public_card", **values))).convert("RGB")
        frames.append(frame.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
    output = io.BytesIO()
    frames[0].save(output, "GIF", save_all=True, append_images=frames[1:], duration=frame_ms,
                   loop=0, optimize=True, disposal=1)
    return output.getvalue()


def banner_parts(interaction, screen, filename="banner.png", **values):
    """
    Баннер над карточкой: -> (embed с картинкой | None, discord.File | None).
    Шаблон — картинка вложением; своя картинка из веб-панели — ссылкой.
    """
    import discord
    color = core.embed_color()
    if is_template(screen):
        try:
            data = render(screen, **values)
        except (OSError, ValueError) as error:
            core._log.warning("Баннер %s не нарисован: %s", screen, error)
        else:
            embed = discord.Embed(color=color).set_image(url=f"attachment://{filename}")
            return embed, discord.File(io.BytesIO(data), filename=filename)
    url = core.resolve_banner(interaction, screen)
    if url:
        return discord.Embed(color=color).set_image(url=url), None
    return None, None
