"""
scene.py
========
«Холст» — свободный макет из слоёв поверх фона, как в Figma: его собирают в
редакторе на сайте (scene_editor.py + scene_editor.html), а бот рисует так же
(Pillow) — для живой картинки в канале и для баннера сервера.

Холст: {"w", "h", "bg": {...}, "layers": [...]} — координаты в пикселях холста.
Фон bg: color (#RRGGBB) и по желанию поверх — style (готовый стиль баннера),
image (своя картинка из /assets), card (фон стиля живой картинки по её id).
Слой: общие поля id, type, name, x, y, w, h, rot (градусы по часовой),
opacity (0–1), hidden, locked; по типу:
- picture — живая картинка: card ("self" — эта же картинка, иначе id), radius, shadow;
- text — text (с переменными {online}…), font, size, color, align, lh, stroke, stroke_color;
- sticker — set + name (дудлы бота), shadow;
- image — asset (имя файла в /assets), radius, shadow;
- shape — shape (rect / ellipse), fill, stroke, stroke_color, radius.
Слои рисуются по порядку: первый — самый нижний.
"""

import asyncio
import io
import math
import pathlib
import re

from PIL import Image, ImageDraw, ImageFilter

ROOT = pathlib.Path(__file__).resolve().parent
STICKER_DIR = ROOT / "panel_art" / "emoji"
# наборы стикеров: ключ -> (подпись, папка, префикс файла)
STICKER_SETS = {
    "doodle": ("Дудлы", STICKER_DIR, "doodle_"),
    "doodle3d": ("3D-дудлы", STICKER_DIR / "doodle3d", "d3_"),
    "smiley": ("Смайлики", STICKER_DIR / "smiley", ""),
    "ui3d": ("3D-значки", STICKER_DIR / "ui3d", "u3_"),
}
# шрифты: ключ -> (подпись, файл в fonts/ или fonts/banner/, запасной без кириллицы)
FONTS = {
    "coolvetica": ("Coolvetica", "banner/Coolvetica-Regular.otf", "Inter-Bold.otf"),
    "dunkin": ("Dunkin Sans", "banner/DunkinSans-Regular.otf", "Inter-SemiBold.otf"),
    "dunkin_bold": ("Dunkin Sans Bold", "banner/DunkinSans-Bold.otf", "Inter-Bold.otf"),
    "drunk": ("Drunk", "banner/DrunkFonts-Regular.otf", "Inter-Bold.otf"),
    "inter": ("Inter", "Inter-Regular.otf", "Inter-Regular.otf"),
    "inter_semi": ("Inter SemiBold", "Inter-SemiBold.otf", "Inter-SemiBold.otf"),
    "inter_bold": ("Inter Bold", "Inter-Bold.otf", "Inter-Bold.otf"),
}
FONT_DIR = ROOT / "fonts"
LAYER_TYPES = ("picture", "text", "sticker", "image", "shape")
MAX_LAYERS = 40
MAX_SIDE, MIN_SIDE = 2400, 64
BANNER = (960, 540)
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_ASSET = re.compile(r"^[0-9a-f]{20}\.(png|jpg|gif|webp)$")
_NAME = re.compile(r"^[\w-]{1,40}$")


# ============================================================
# ПРОВЕРКА (всё, что пришло с сайта, — чистим и ограничиваем)
# ============================================================

def _num(value, low, high, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return max(low, min(high, number))


def _color(value, default):
    return value if isinstance(value, str) and _HEX.match(value) else default


def stickers():
    """{набор: [имена]} — какие стикеры есть."""
    found = {}
    for key, (_, folder, prefix) in STICKER_SETS.items():
        if folder.is_dir():
            found[key] = sorted(p.stem[len(prefix):] for p in folder.glob(f"{prefix}*.png")
                                if p.stem.startswith(prefix) and _NAME.match(p.stem[len(prefix):]))
    return found


def sticker_path(set_key, name):
    if set_key not in STICKER_SETS or not isinstance(name, str) or not _NAME.match(name):
        return None
    _, folder, prefix = STICKER_SETS[set_key]
    path = folder / f"{prefix}{name}.png"
    return path if path.is_file() else None


def asset_path(name):
    import web_panel
    if not isinstance(name, str) or not _ASSET.match(name):
        return None
    path = web_panel.ASSETS_DIR / name
    return path if path.is_file() else None


def clean(raw, card_ids=(), self_card=False):
    """
    Холст с сайта -> (холст, None) или (None, текст ошибки).
    card_ids — какие живые картинки можно ставить слоем (картинки этого сервера);
    self_card — холст самой живой картинки (можно слой «эта картинка»).
    """
    if not isinstance(raw, dict):
        return None, "Пустой холст."
    w = int(_num(raw.get("w"), MIN_SIDE, MAX_SIDE, BANNER[0]))
    h = int(_num(raw.get("h"), MIN_SIDE, MAX_SIDE, BANNER[1]))
    bg_raw = raw.get("bg") if isinstance(raw.get("bg"), dict) else {}
    bg = {"color": _color(bg_raw.get("color"), "#F1EEEC")}
    import banner_styles
    if bg_raw.get("style") in banner_styles.STYLES:
        bg["style"] = bg_raw["style"]
    if asset_path(bg_raw.get("image")):
        bg["image"] = bg_raw["image"]
    if bg_raw.get("card") == "self" and self_card:
        bg["card"] = "self"
    elif str(bg_raw.get("card") or "").isdigit() and int(bg_raw["card"]) in card_ids:
        bg["card"] = int(bg_raw["card"])
    layers = []
    raw_layers = raw.get("layers") if isinstance(raw.get("layers"), list) else []
    if len(raw_layers) > MAX_LAYERS:
        return None, f"Слоёв больше {MAX_LAYERS} — убери лишние."
    for n, item in enumerate(raw_layers):
        if not isinstance(item, dict) or item.get("type") not in LAYER_TYPES:
            continue
        kind = item["type"]
        layer = {
            "id": str(item.get("id") or f"l{n}")[:24], "type": kind, "name": str(item.get("name") or "")[:40],
            "x": _num(item.get("x"), -MAX_SIDE, 2 * MAX_SIDE, 0), "y": _num(item.get("y"), -MAX_SIDE, 2 * MAX_SIDE, 0),
            "w": _num(item.get("w"), 4, 2 * MAX_SIDE, 100), "h": _num(item.get("h"), 4, 2 * MAX_SIDE, 100),
            "rot": _num(item.get("rot"), -360, 360, 0), "opacity": _num(item.get("opacity"), 0, 1, 1),
            "hidden": bool(item.get("hidden")), "locked": bool(item.get("locked")),
        }
        if kind == "picture":
            card = item.get("card")
            if card == "self" and self_card:
                layer["card"] = "self"
            elif str(card or "").isdigit() and int(card) in card_ids:
                layer["card"] = int(card)
            else:
                continue
            layer["radius"] = _num(item.get("radius"), 0, 400, 0)
            layer["shadow"] = _num(item.get("shadow"), 0, 80, 0)
        elif kind == "text":
            layer.update(text=str(item.get("text") or "")[:500],
                         font=item.get("font") if item.get("font") in FONTS else "coolvetica",
                         size=_num(item.get("size"), 6, 600, 48), color=_color(item.get("color"), "#201C1E"),
                         align=item.get("align") if item.get("align") in ("left", "center", "right") else "left",
                         lh=_num(item.get("lh"), 0.7, 3, 1.15), stroke=_num(item.get("stroke"), 0, 40, 0),
                         stroke_color=_color(item.get("stroke_color"), "#FFFFFF"))
        elif kind == "sticker":
            if sticker_path(item.get("set"), item.get("sticker")) is None:
                continue
            layer.update(set=item["set"], sticker=item["sticker"], shadow=_num(item.get("shadow"), 0, 80, 0))
        elif kind == "image":
            if asset_path(item.get("asset")) is None:
                continue
            layer.update(asset=item["asset"], radius=_num(item.get("radius"), 0, 400, 0),
                         shadow=_num(item.get("shadow"), 0, 80, 0))
        elif kind == "shape":
            layer.update(shape=item.get("shape") if item.get("shape") in ("rect", "ellipse") else "rect",
                         fill=_color(item.get("fill"), "#C6F135"), stroke=_num(item.get("stroke"), 0, 60, 0),
                         stroke_color=_color(item.get("stroke_color"), "#201C1E"),
                         radius=_num(item.get("radius"), 0, 400, 0),
                         no_fill=bool(item.get("no_fill")))
        layers.append(layer)
    return {"w": w, "h": h, "bg": bg, "layers": layers}, None


def texts(scene):
    """Тексты холста, где могут быть переменные."""
    return [layer["text"] for layer in (scene or {}).get("layers", []) if layer.get("type") == "text"]


def card_refs(scene):
    """Какие живые картинки нужны холсту: {"self" | id}."""
    refs = {layer["card"] for layer in (scene or {}).get("layers", []) if layer.get("type") == "picture"}
    if (scene or {}).get("bg", {}).get("card") is not None:
        refs.add(scene["bg"]["card"])
    return refs


# ============================================================
# ПОДГОТОВКА (async): переменные и живые картинки
# ============================================================

async def prepare(scene, guild, self_card=None):
    """-> (холст с подставленными значениями, {ref: PNG картинки}, {ref: данные стиля для фона})."""
    import cards
    from actions import VARIABLE, substitute
    needed = {(m[1], m[2]) for text in texts(scene) for m in VARIABLE.finditer(text)}
    variables = {}
    if needed and guild is not None:
        import live
        variables = await live.collect(guild, needed)
    ready = dict(scene, layers=[dict(layer, text=substitute(layer["text"], variables))
                                if layer.get("type") == "text" else layer for layer in scene["layers"]])
    pictures, looks = {}, {}
    for ref in card_refs(scene):
        card = self_card if ref == "self" else cards.get_card(ref)
        if card is None or (guild is not None and card["guild_id"] != guild.id):
            continue
        plain = dict(card, data={k: v for k, v in card["data"].items() if k != "scene"})  # без своего холста
        pictures[ref] = await cards.render_card(plain, guild)
        looks[ref] = plain["data"]
    return ready, pictures, looks


# ============================================================
# РИСОВАНИЕ
# ============================================================

def _rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def font_for(key, size, text=""):
    """Шрифт слоя; если в нём нет букв текста (у Dunkin нет кириллицы) — запасной Inter."""
    from PIL import ImageFont
    import banner_render
    _, file, fallback = FONTS.get(key, FONTS["coolvetica"])
    path = FONT_DIR / file
    if not path.is_file() or not banner_render.covers(str(path), text):
        path = FONT_DIR / fallback
    return ImageFont.truetype(str(path), max(4, int(round(size))))


def font_has_cyrillic(key):
    import banner_render
    path = FONT_DIR / FONTS[key][1]
    return path.is_file() and banner_render.covers(str(path), "абвгдеёжзийклмнопрстуфхцчшщъыьэюя")


def wrap_lines(text, fnt, width):
    """Перенос по словам в ширину (как white-space: pre-wrap на сайте)."""
    lines = []
    for paragraph in str(text).split("\n"):
        line = ""
        for word in paragraph.split(" "):
            trial = f"{line} {word}" if line else word
            if fnt.getlength(trial) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def text_height(layer):
    fnt = font_for(layer["font"], layer["size"], layer["text"])
    return max(1, len(wrap_lines(layer["text"], fnt, layer["w"]))) * layer["size"] * layer["lh"]


def _rounded(image, radius):
    if radius <= 0:
        return image
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=int(radius), fill=255)
    out = image.convert("RGBA")
    out.putalpha(Image.composite(out.getchannel("A"), Image.new("L", image.size, 0), mask))
    return out


def _layer_image(layer, pictures, scale):
    """Слой -> RGBA картинка размера w×h (в пикселях холста × scale)."""
    w, h = max(1, int(round(layer["w"] * scale))), max(1, int(round(layer["h"] * scale)))
    kind = layer["type"]
    if kind == "picture":
        png = pictures.get(layer["card"])
        if png is None:
            return None
        image = Image.open(io.BytesIO(png)).convert("RGBA").resize((w, h), Image.LANCZOS)
        return _rounded(image, layer["radius"] * scale)
    if kind == "sticker":
        path = sticker_path(layer["set"], layer["sticker"])
        return Image.open(path).convert("RGBA").resize((w, h), Image.LANCZOS) if path else None
    if kind == "image":
        path = asset_path(layer["asset"])
        if path is None:
            return None
        with Image.open(path) as source:
            image = source.convert("RGBA").resize((w, h), Image.LANCZOS)
        return _rounded(image, layer["radius"] * scale)
    if kind == "shape":
        image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        stroke = int(round(layer["stroke"] * scale))
        fill = None if layer["no_fill"] else _rgb(layer["fill"]) + (255,)
        outline = _rgb(layer["stroke_color"]) + (255,) if stroke else None
        box = (stroke // 2, stroke // 2, w - 1 - stroke // 2, h - 1 - stroke // 2)
        if layer["shape"] == "ellipse":
            draw.ellipse(box, fill=fill, outline=outline, width=stroke)
        else:
            draw.rounded_rectangle(box, radius=int(layer["radius"] * scale), fill=fill, outline=outline, width=stroke)
        return image
    if kind == "text":
        size = layer["size"] * scale
        fnt = font_for(layer["font"], size, layer["text"])
        lines = wrap_lines(layer["text"], fnt, w)
        line_h = size * layer["lh"]
        h = max(1, int(math.ceil(len(lines) * line_h)))
        image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        stroke = int(round(layer["stroke"] * scale))
        x, anchor = {"left": (0, "lm"), "center": (w / 2, "mm"), "right": (w, "rm")}[layer["align"]]
        for n, line in enumerate(lines):
            draw.text((x, (n + 0.5) * line_h), line, font=fnt, fill=_rgb(layer["color"]) + (255,), anchor=anchor,
                      stroke_width=stroke, stroke_fill=_rgb(layer["stroke_color"]) + (255,) if stroke else None)
        return image
    return None


def _background(scene, pictures, looks, size):
    w, h = size
    bg = scene.get("bg") or {}
    image = Image.new("RGBA", (w, h), _rgb(bg.get("color") or "#F1EEEC") + (255,))
    if bg.get("style"):
        import banner_styles
        base = banner_styles.render_base(bg["style"]).convert("RGBA")
        image = _cover(base, size)
    if bg.get("card") is not None and bg["card"] in looks:
        import types
        import card_templates as ct
        _, st, theme = ct.look_of(looks[bg["card"]])
        holder = types.SimpleNamespace(st=st, theme=theme, image=Image.new("RGB", (w, h), theme["bg"]))
        holder.draw = ImageDraw.Draw(holder.image)
        ct.background(holder)
        image = holder.image.convert("RGBA")
    if bg.get("image"):
        path = asset_path(bg["image"])
        if path:
            with Image.open(path) as source:
                image = _cover(source.convert("RGBA"), size)
    return image


def _cover(image, size):
    w, h = size
    scale = max(w / image.width, h / image.height)
    image = image.resize((max(1, int(image.width * scale + 0.5)), max(1, int(image.height * scale + 0.5))),
                         Image.LANCZOS)
    left, top = (image.width - w) // 2, (image.height - h) // 2
    return image.crop((left, top, left + w, top + h))


def render(scene, pictures=None, looks=None, size=None):
    """Холст (значения уже подставлены) -> PNG. size — итоговый размер (баннер 960×540), по умолчанию w×h."""
    pictures, looks = pictures or {}, looks or {}
    size = size or (scene["w"], scene["h"])
    scale = min(size[0] / scene["w"], size[1] / scene["h"])
    canvas = _background(scene, pictures, looks, size)
    off_x = (size[0] - scene["w"] * scale) / 2
    off_y = (size[1] - scene["h"] * scale) / 2
    for layer in scene["layers"]:
        if layer.get("hidden") or layer["opacity"] <= 0:
            continue
        image = _layer_image(layer, pictures, scale)
        if image is None:
            continue
        if layer["opacity"] < 1:
            image.putalpha(image.getchannel("A").point(lambda v, k=layer["opacity"]: int(v * k)))
        cx = off_x + (layer["x"] + layer["w"] / 2) * scale
        cy = off_y + (layer["y"] + layer["h"] / 2) * scale
        if layer["type"] == "text":  # высота текста — по строкам, рамка слоя растёт вниз от верха
            cy = off_y + layer["y"] * scale + image.height / 2
        shadow = layer.get("shadow") or 0
        if layer["rot"]:
            image = image.rotate(-layer["rot"], resample=Image.BICUBIC, expand=True)
        x, y = int(round(cx - image.width / 2)), int(round(cy - image.height / 2))
        if shadow:  # мягкая тень вниз
            blur = max(1, int(shadow * scale))
            shade = Image.new("RGBA", (image.width + 4 * blur, image.height + 4 * blur), (0, 0, 0, 0))
            alpha = image.getchannel("A").point(lambda v: int(v * 0.45))
            shade.paste((0, 0, 0, 255), (2 * blur, 2 * blur), alpha)
            shade = shade.filter(ImageFilter.GaussianBlur(blur))
            _paste(canvas, shade, x - 2 * blur, y - 2 * blur + blur // 2)
        _paste(canvas, image, x, y)
    output = io.BytesIO()
    canvas.convert("RGB").save(output, "PNG", optimize=True)
    return output.getvalue()


def _paste(canvas, image, x, y):
    """alpha_composite, которому можно отдать слой, вылезающий за край холста."""
    left, top = max(0, -x), max(0, -y)
    right, bottom = min(image.width, canvas.width - x), min(image.height, canvas.height - y)
    if right <= left or bottom <= top:
        return
    canvas.alpha_composite(image.crop((left, top, right, bottom)), (x + left, y + top))


async def render_live(scene, guild, self_card=None, size=None):
    """Холст со свежими значениями сервера -> PNG."""
    ready, pictures, looks = await prepare(scene, guild, self_card)
    return await asyncio.to_thread(render, ready, pictures, looks, size)


# ============================================================
# НАЧАЛЬНЫЕ ХОЛСТЫ
# ============================================================

_LINE_PX = {"S": 0.065, "M": 0.095, "L": 0.15, "XL": 0.22}
_FONT_OF_LINE = {"coolvetica": "coolvetica", "dunkin": "dunkin", "drunk": "drunk"}


def from_banner(banner):
    """Холст из обычного баннера сервера: фон и строки текста становятся слоями — дальше двигай как хочешь."""
    w, h = BANNER
    bg = {"color": "#F1EEEC"}
    layers = []
    base = banner.get("base") or ""
    if base.startswith("style:"):
        bg["style"] = base[6:]
    elif base.startswith("card:") and base[5:].isdigit():
        layers.append({"id": "pic", "type": "picture", "name": "Живая картинка", "card": int(base[5:]),
                       "x": 0, "y": 0, "w": w, "h": h, "rot": 0, "opacity": 1, "radius": 0, "shadow": 0})
    elif base:
        bg["image"] = base
    margin = int(w * 0.06)
    groups = {}
    for line in banner.get("lines") or []:
        groups.setdefault(line.get("pos") or "bl", []).append(line)
    for pos, lines in groups.items():
        sizes = [h * _LINE_PX.get(line.get("size"), 0.065) for line in lines]
        block = sum(size * 1.12 for size in sizes)
        y = {"t": margin, "m": (h - block) / 2, "b": h - margin - block}[pos[0]]
        align = {"l": "left", "c": "center", "r": "right"}[pos[1]]
        for n, (line, size) in enumerate(zip(lines, sizes)):
            layers.append({"id": f"t{len(layers)}", "type": "text", "name": line.get("text", "")[:30],
                           "text": line.get("text", ""), "font": _FONT_OF_LINE.get(line.get("font"), "coolvetica"),
                           "size": round(size), "color": line.get("color") or "#201C1E", "align": align, "lh": 1.12,
                           "x": margin, "y": round(y), "w": w - 2 * margin, "h": round(size * 1.12), "rot": 0,
                           "opacity": 1, "stroke": 0, "stroke_color": "#FFFFFF"})
            y += size * 1.12
    return clean({"w": w, "h": h, "bg": bg, "layers": layers}, card_ids={
        layer["card"] for layer in layers if layer["type"] == "picture"})[0]


def for_card(card, png_size):
    """Холст живой картинки: её же картинка слоем на весь холст и её фон — дальше добавляй стикеры, тексты."""
    w, h = png_size
    return {"w": w, "h": h, "bg": {"color": "#F1EEEC", "card": "self"},
            "layers": [{"id": "self", "type": "picture", "name": card["title"] or "Живая картинка", "card": "self",
                        "x": 0, "y": 0, "w": w, "h": h, "rot": 0, "opacity": 1, "hidden": False, "locked": False,
                        "radius": 0, "shadow": 0}]}


def size_presets(card_size=None):
    presets = [("banner", "Баннер сервера 16:9", 960, 540), ("square", "Квадрат", 1080, 1080),
               ("wide", "Широкая полоса", 1200, 400), ("tall", "Вертикальная", 1080, 1350)]
    if card_size:
        presets.insert(0, ("card", "Как живая картинка", card_size[0], card_size[1]))
    return [{"key": k, "label": label, "w": w, "h": h} for k, label, w, h in presets]
