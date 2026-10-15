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
    "coolvetica": (("Coolvetica-Regular", "Coolvetica Rg", "coolvetica rg", "Coolvetica"), "Inter-Bold.otf"),
    "dunkin": (("DunkinSans-Regular", "Dunkin Sans", "DunkinSans", "dunkin"), "Inter-SemiBold.otf"),
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
    "forms.public_card": {
        "text": "{name}", "font": "coolvetica", "size": 166, "min_size": 56,
        "x": 40, "y": 36 + 58, "two_line_y": 116, "max_width": 670, "color": (32, 28, 30), "lower": True,
    },
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


def _font(kind, size):
    key = (kind, size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(font_path(kind), size)
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
    width, size, kind = spec["max_width"], spec["size"], spec["font"]
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


def render(screen, **values):
    """-> PNG (bytes) баннера с подставленным текстом."""
    spec = TEMPLATES[screen]
    picture = Image.open(base_path(screen)).convert("RGBA")
    draw = ImageDraw.Draw(picture)
    text = spec["text"].format_map({k: str(v) for k, v in values.items()})
    if spec.get("lower"):
        text = text.lower()
    lines, font = fit_text(spec, text)
    step = font.size * 0.8
    center = spec.get("two_line_y", spec["y"]) if len(lines) > 1 else spec["y"]
    top = center - step * (len(lines) - 1) / 2
    for number, line in enumerate(lines):
        draw.text((spec["x"], top + number * step), line, font=font, fill=spec["color"], anchor="lm")
    output = io.BytesIO()
    picture.save(output, "PNG", optimize=True)
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
