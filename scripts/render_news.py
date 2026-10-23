"""
Баннеры для новостей 1200×600 — 20 шаблонов разного типа и композиции в
фирменном стиле (как баннеры экранов): размытый винный/ледяной/лаймовый фон,
стеклянные плашки с тремя точками, жирный строчный текст с растянутыми
буквами, 3D-смайлики и 3D-иконки, плашки-«кнопки» с толстым боком.

Запуск из корня проекта:
    python3 scripts/render_news.py                       # все 20 → PNG
    python3 scripts/render_news.py event poll            # выборочно
    python3 scripts/render_news.py --figma <папка>       # + слои для Figma

Каждый шаблон собирается из слоёв (сцена): фон, стекло, плашки, тексты,
картинки. Из одной сцены рисуется PNG (panel_art/news/news.<шаблон>.png,
общий лист _all.png) и описание для Figma (--figma: <папка>/<шаблон>.json +
картинки слоёв) — в Figma тексты остаются текстом, плашки — фигурами,
смайлики и иконки — отдельными картинками, всё можно двигать и править.

Тексты — параметры: render("event", title="…", day="31"). Каждый шаблон
проверяет раскладку: ничего не выходит за баннер, текст, картинки и плашки не
наезжают друг на друга; длинный текст уменьшается, а если не влезает и
так — ошибка, файл не пишется.
"""

import hashlib
import io
import json
import math
import pathlib
import random
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import render_banners as rb  # noqa: E402
import render_profile as rp  # noqa: E402
from art3d import ICE, INK, LIME, MAROON, MILK, PINK, WINE, dark  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "panel_art" / "news"
W, H = 1200, 600
FONTS = ROOT / "fonts"
SOFT = (226, 218, 216)          # молочный потише — для обычного текста
GREY = (70, 60, 66)             # текст на светлом фоне
LIME_WAVE, ICE_WAVE, PINK_WAVE = rb.LIME_WAVE, rb.ICE_WAVE, rb.PINK_WAVE
INK_WAVE = (INK, (90, 30, 50), MAROON, WINE)     # для светлого фона
TRACK = -0.075                  # трекинг фирменных надписей (доля кегля)


# ============================================================
# раскладка (проверка наложений)
# ============================================================

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


# ============================================================
# сцена: слои шаблона (общие для PNG и Figma)
# ============================================================
# Узел — dict: type (image / box / ellipse / text), name, x, y, w, h (без
# поворота; у вложенных — от левого верхнего угла родителя), angle (градусы
# против часовой, вокруг центра), effects (тени), children (у box).

def shadow_fx(dx, dy, blur, color, alpha, spread=0):
    """Тень как в Figma: blur — радиус размытия, spread — расширение формы."""
    return {"dx": dx, "dy": dy, "blur": blur, "color": color, "alpha": alpha, "spread": spread}


def soft_shadow(dy=14, blur=36, alpha=0.35):
    return shadow_fx(0, dy, blur, (10, 6, 8), alpha)


def side_fx(fill, depth, width):
    """Толстый бок плашки: обводка-тень и тёмная заливка под плашкой."""
    return [shadow_fx(0, depth, 0, INK, 1.0), shadow_fx(0, depth, 0, dark(fill, 0.62), 1.0, spread=-width)]


def text_shadow(size):
    return shadow_fx(0, size * 0.03, size * 0.12, (0, 0, 0), 0.35)


class Scene:
    def __init__(self):
        self.nodes = []

    def add(self, node, parent=None):
        (parent["children"] if parent else self.nodes).append(node)
        return node


def n_image(name, img, x, y, w, h, angle=0, effects=None):
    return {"type": "image", "name": name, "img": img, "x": x, "y": y, "w": w, "h": h, "angle": angle,
            "effects": effects or []}


def n_box(name, x, y, w, h, fill=None, alpha=1.0, radius=0, stroke=None, stroke_w=0, stroke_alpha=1.0,
          effects=None, clip=False, angle=0, layout=None):
    """layout: None (дети по координатам) или dict(dir, pad, gap, align, hug) — авто-раскладка Figma."""
    return {"type": "box", "name": name, "x": x, "y": y, "w": w, "h": h, "fill": fill, "alpha": alpha,
            "radius": radius, "stroke": stroke, "stroke_w": stroke_w, "stroke_alpha": stroke_alpha,
            "effects": effects or [], "clip": clip, "angle": angle, "layout": layout, "children": []}


def n_ellipse(name, x, y, w, h, fill=None, stroke=None, stroke_w=0, img=None, effects=None):
    return {"type": "ellipse", "name": name, "x": x, "y": y, "w": w, "h": h, "fill": fill, "stroke": stroke,
            "stroke_w": stroke_w, "img": img, "effects": effects or [], "angle": 0}


def line_width(f, text, track, size):
    if not track:
        return f.getlength(text)
    return sum(f.getlength(ch) for ch in text) + track * size * max(0, len(text) - 1)


def wrap(text, f, max_w, track=0, size=0):
    lines = []
    for para_ in text.split("\n"):
        cur = ""
        for w in para_.split(" "):
            test = (cur + " " + w).strip()
            if line_width(f, test, track, size) <= max_w or not cur:
                cur = test
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def n_text(name, text, x, y, size, weight="Bold", color=MILK, runs=None, track=0.0, width=None, align="left",
           leading=None, effects=None, stroke=None, angle=0):
    """
    Текст. x, y — левый верхний угол рамки (как в Figma: строка высотой
    leading·кегль, или «авто» — ascent+descent шрифта). width — ширина рамки
    с переносом слов (None — по тексту). runs — [(начало, конец, цвет)].
    """
    f = font(size, weight)
    asc, desc = f.getmetrics()
    lh = size * leading if leading else asc + desc
    lines = wrap(text, f, width, track, size) if width else text.split("\n")
    widths = [line_width(f, ln, track, size) for ln in lines]
    w = width if width else max(widths)
    return {"type": "text", "name": name, "text": text, "x": x, "y": y, "w": w, "h": lh * len(lines), "size": size,
            "weight": weight, "color": color, "runs": runs or [], "track": track, "fixed_w": bool(width),
            "align": align, "leading": leading, "effects": effects or [], "stroke": stroke, "angle": angle,
            "_lines": lines, "_widths": widths, "_lh": lh}


def text_ink_box(node, ox=0, oy=0):
    """Рамка самих букв текстового узла (без межстрочных полей) — для проверки наложений."""
    f = font(node["size"], node["weight"])
    asc, desc = f.getmetrics()
    lh = node["_lh"]
    top = oy + node["y"] + (lh - asc - desc) / 2
    xs = []
    for wd in node["_widths"]:
        off = 0 if node["align"] == "left" else ((node["w"] - wd) / 2 if node["align"] == "center" else node["w"] - wd)
        xs.append((ox + node["x"] + off, ox + node["x"] + off + wd))
    first_base = top + asc
    last_base = first_base + lh * (len(node["_lines"]) - 1)
    return (min(a for a, _ in xs), first_base - asc * 0.78, max(b for _, b in xs), last_base + desc * 0.9)


def at_baseline(f, text, x, y, anchor):
    """Координаты (левый край, базовая линия) текста, нарисованного с якорем anchor в (x, y)."""
    a = f.getbbox(text, anchor=anchor)
    b = f.getbbox(text, anchor="ls")
    return x + a[0] - b[0], y + a[1] - b[1]


def top_from_baseline(size, weight, baseline):
    return baseline - font(size, weight).getmetrics()[0]


# ============================================================
# рисование сцены в PNG
# ============================================================

def _apply_effects(canvas, layer, effects):
    """Тени из альфы слоя (как drop shadow в Figma), потом сам слой."""
    if effects:
        box = layer.getchannel("A").getbbox()
        if box:
            alpha_full = layer.getchannel("A")
            for e in effects:
                pad = int(e["blur"] * 1.5 + abs(e["spread"]) + 4)
                x0, y0 = max(0, box[0] - pad), max(0, box[1] - pad)
                x1, y1 = min(canvas.width, box[2] + pad), min(canvas.height, box[3] + pad)
                a = alpha_full.crop((x0, y0, x1, y1))
                sp = int(round(e["spread"]))
                if sp > 0:
                    a = a.filter(ImageFilter.MaxFilter(2 * sp + 1))
                elif sp < 0:
                    a = a.filter(ImageFilter.MinFilter(2 * -sp + 1))
                if e["blur"]:
                    a = a.filter(ImageFilter.GaussianBlur(e["blur"] / 2))
                a = a.point(lambda v, k=e["alpha"]: int(v * k))
                sh = Image.new("RGBA", a.size, tuple(e["color"]) + (255,))
                sh.putalpha(a)
                full = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
                full.alpha_composite(sh, (int(round(x0 + e["dx"])), int(round(y0 + e["dy"]))))
                canvas.alpha_composite(full)
    canvas.alpha_composite(layer)


def _radius_args(r):
    if isinstance(r, (list, tuple)):
        rad = max(r)
        return rad, tuple(v > 0 for v in r)       # tl, tr, br, bl
    return r, None


def _draw_text(layer, node, ax, ay):
    f = font(node["size"], node["weight"])
    asc, desc = f.getmetrics()
    lh = node["_lh"]
    d = ImageDraw.Draw(layer)
    colors = [node["color"]] * len(node["text"])
    for s, e, col in node["runs"]:
        for i in range(s, e):
            colors[i] = col
    stroke = node["stroke"]
    kw = {"stroke_width": stroke[1], "stroke_fill": stroke[0]} if stroke else {}
    idx = 0
    for li, (ln, wd) in enumerate(zip(node["_lines"], node["_widths"])):
        pos = node["text"].find(ln, idx)
        if pos >= 0:
            idx = pos
        off = 0 if node["align"] == "left" else ((node["w"] - wd) / 2 if node["align"] == "center" else node["w"] - wd)
        base = ay + (lh - asc - desc) / 2 + asc + li * lh
        x = ax + off
        uniform = len(set(colors[idx:idx + len(ln)])) <= 1
        if not node["track"] and uniform:
            d.text((x, base), ln, font=f, fill=colors[idx] if ln else node["color"], anchor="ls", **kw)
        else:
            for k, ch in enumerate(ln):
                d.text((x, base), ch, font=f, fill=colors[idx + k], anchor="ls", **kw)
                x += f.getlength(ch) + node["track"] * node["size"]
        idx += len(ln)


def draw(node, canvas, ox=0, oy=0):
    if node.get("angle"):
        # рисуем на своём холсте (узел может выходить за баннер до поворота), поворачиваем вокруг центра
        p = 80
        tmp = Image.new("RGBA", (int(node["w"] + 2 * p), int(node["h"] + 2 * p)), (0, 0, 0, 0))
        draw(dict(node, angle=0, x=p, y=p), tmp)
        rot = tmp.rotate(node["angle"], expand=True, resample=Image.BICUBIC)
        cx, cy = ox + node["x"] + node["w"] / 2, oy + node["y"] + node["h"] / 2
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        layer.paste(rot, (int(round(cx - rot.width / 2)), int(round(cy - rot.height / 2))))
        canvas.alpha_composite(layer)
        return
    ax, ay = ox + node["x"], oy + node["y"]
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    t = node["type"]
    if t == "image":
        img = node["img"].resize((max(1, int(round(node["w"]))), max(1, int(round(node["h"])))), Image.LANCZOS)
        layer.alpha_composite(img, (int(round(ax)), int(round(ay))))
    elif t == "text":
        _draw_text(layer, node, ax, ay)
    elif t in ("box", "ellipse"):
        box = (ax, ay, ax + node["w"] - 1, ay + node["h"] - 1)
        d = ImageDraw.Draw(layer)
        fill = node["fill"] + (int(255 * node.get("alpha", 1.0)),) if node["fill"] else None
        if t == "box":
            r, corners = _radius_args(node["radius"])
            if fill:
                d.rounded_rectangle(box, radius=r, fill=fill, corners=corners)
        else:
            if node.get("img") is not None:
                img = node["img"].resize((int(node["w"]), int(node["h"])), Image.LANCZOS)
                m = Image.new("L", img.size, 0)
                ImageDraw.Draw(m).ellipse((0, 0, img.width - 1, img.height - 1), fill=255)
                img.putalpha(ImageChops.multiply(img.getchannel("A"), m))
                layer.alpha_composite(img, (int(ax), int(ay)))
            elif fill:
                d.ellipse(box, fill=fill)
        if node["stroke"] and node["stroke_w"]:
            ol = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            od = ImageDraw.Draw(ol)
            col = node["stroke"] + (int(255 * node.get("stroke_alpha", 1.0)),)
            if t == "box":
                r, corners = _radius_args(node["radius"])
                od.rounded_rectangle(box, radius=r, outline=col, width=int(node["stroke_w"]), corners=corners)
            else:
                od.ellipse(box, outline=col, width=int(node["stroke_w"]))
            layer.alpha_composite(ol)
    _apply_effects(canvas, layer, node["effects"])
    kids = node.get("children") or []
    if kids:
        if node.get("clip"):
            sub = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            for ch in kids:
                draw(ch, sub, ax, ay)
            m = Image.new("L", canvas.size, 0)
            r, corners = _radius_args(node["radius"])
            ImageDraw.Draw(m).rounded_rectangle((ax, ay, ax + node["w"] - 1, ay + node["h"] - 1), radius=r,
                                                fill=255, corners=corners)
            sub.putalpha(ImageChops.multiply(sub.getchannel("A"), m))
            canvas.alpha_composite(sub)
        else:
            for ch in kids:
                draw(ch, canvas, ax, ay)


def to_png(scene):
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    for n in scene.nodes:
        draw(n, canvas)
    return canvas.convert("RGB")


# ============================================================
# элементы шаблонов
# ============================================================

class Builder:
    """Сцена + раскладка одного шаблона и фирменные элементы."""

    def __init__(self, name):
        self.lay = Layout(name)
        self.scene = Scene()

    def add(self, node, parent=None):
        return self.scene.add(node, parent)

    # --- фон и декор (картинкой) ---
    def background(self, img, name="фон"):
        return self.add(n_image(name, img.convert("RGBA"), 0, 0, W, H))

    def decor(self, name, img, x, y, angle=0):
        """Картинка-декор (полосы, звезда, искры): отдельный слой, без проверки наложений."""
        return self.add(n_image(name, img, x, y, img.width, img.height, angle))

    # --- стекло ---
    def glass(self, box, radius=30, alpha=34, dots=True, name="стекло", effects=None, parent=None, check=False):
        x0, y0, x1, y1 = box
        g = n_box(name, x0, y0, x1 - x0, y1 - y0, fill=(255, 255, 255), alpha=alpha / 255, radius=radius,
                  stroke=(255, 255, 255), stroke_w=1, stroke_alpha=70 / 255, effects=effects)
        if dots:
            for i, col in enumerate((WINE, LIME, ICE)):
                cx, cy, r = (x1 - x0) - 46 + i * 16, 18, 4
                g["children"].append(n_ellipse(f"точка {i + 1}", cx - r, cy - r, 2 * r, 2 * r, fill=col))
        if check:
            self.lay.add(name, box)
        return self.add(g, parent)

    # --- плашка-кнопка ---
    def plate(self, name, box, fill, radius=28, depth=12, width=5, effects=(), parent=None, check=True, layout=None):
        x0, y0, x1, y1 = box
        node = n_box(name, x0, y0, x1 - x0, y1 - y0, fill=fill, radius=radius, stroke=INK, stroke_w=width,
                     effects=list(effects) + side_fx(fill, depth, width), layout=layout)
        if check:
            self.lay.add(name, (x0, y0, x1, y1 + depth))
        return self.add(node, parent)

    # --- картинки ---
    def sprite(self, kind, im, cx, cy, size, angle=0, pad=0.1, parent=None, check=True):
        node = n_image(kind, im, cx - size / 2, cy - size / 2, size, size, angle)
        self.add(node, parent)
        if check:
            p = size * pad
            self.lay.add(kind, (cx - size / 2 + p, cy - size / 2 + p, cx + size / 2 - p, cy + size / 2 - p))
        return node

    # --- текст ---
    def label(self, name, text, x, y, size, weight="Bold", color=INK, anchor="la", parent=None, **kw):
        """Одна строка, нарисованная с якорем PIL (mm, rm, ls…) — без проверки наложений.
        width=… (с align) — рамка фиксированной ширины от x-width/2 (для текста по центру плашки)."""
        f = font(size, weight)
        lx, base = at_baseline(f, text, x, y, anchor)
        if kw.get("track"):
            w = line_width(f, text, kw["track"], size)
            if anchor[0] == "m":
                lx = x - w / 2
            elif anchor[0] == "r":
                lx = x - w
        if kw.get("width"):
            lx = x - kw["width"] / 2 if anchor[0] == "m" else lx
        node = n_text(name, text, lx, top_from_baseline(size, weight, base), size, weight, color, **kw)
        return self.add(node, parent)

    def word(self, kind, text, x, base, size, limit, letter=None, palette=PINK_WAVE, phase=0.3,
             smiley_at=(), smiley=None, color=MILK, center=False, min_size=40, check=True):
        """Фирменная надпись (растянутые буквы letter — волной цвета). x — левый край или центр."""
        slots = set(smiley_at) if smiley is not None else set()
        max_w = limit - x if not center else limit
        size = rp.fit_size(text, max_w, size, slots=slots)
        if size < min_size:
            raise ValueError(f"{self.lay.name}: «{kind}» не влезает даже кеглем {size}")
        if center:
            x = x - rp.text_width(text, size, slots=slots) / 2
        box = rb.text_box(text, x, base, size, slots=slots)
        if letter and letter not in text:
            letter = max(set(text), key=lambda ch: (lambda r: r[1] - r[0])(rb.run(text, ch)))
        if letter:
            a, b = rb.run(text, letter)
            cols = rp.stretch_colors(text, a, b, phase=phase, palette=palette)
            cols = [c if a <= i < b else color for i, c in enumerate(cols)]
        else:
            cols = [color] * len(text)
        fx = [text_shadow(size)] if color != INK else []
        top = top_from_baseline(size, "Bold", base)
        chars = rp.char_layout(text, size, TRACK, slots)
        if not slots:
            runs = [(i, i + 1, c) for i, c in enumerate(cols) if c != color]
            self.add(n_text(kind, text, x, top, size, color=color, runs=runs, track=TRACK, effects=fx))
        else:
            # надпись со смайликом вместо буквы: части текста + картинка, одной группой
            gx = x
            group = n_box(kind, gx, top, 1, 1)
            pieces, start = [], 0
            for i, (ch, off, width, sprite) in enumerate(chars):
                if sprite:
                    pieces.append((start, i))
                    cx, cy = x + off + width / 2, base - size * 0.27
                    group["children"].append(n_image("смайлик", smiley, cx - sprite / 2 - gx, cy - sprite / 2 - top,
                                                     sprite, sprite))
                    vis = sprite * rp.SLOT_VISIBLE / 2
                    box = (box[0], min(box[1], int(cy - vis)), box[2], max(box[3], int(cy + vis)))
                    start = i + 1
            pieces.append((start, len(text)))
            for s, e in pieces:
                if s >= e:
                    continue
                part = text[s:e]
                runs = [(i - s, i - s + 1, cols[i]) for i in range(s, e) if cols[i] != color]
                group["children"].append(n_text(f"текст {part}", part, chars[s][1], 0, size,
                                                color=color, runs=runs, track=TRACK, effects=fx))
            group["w"] = max(c["x"] + c["w"] for c in group["children"])
            group["h"] = max(c["y"] + c["h"] for c in group["children"])
            self.add(group)
        if check:
            self.lay.add(kind, box)
        return box

    def para(self, kind, text, box, size, color=SOFT, weight="SemiBold", max_lines=3, align="left",
             leading=1.22, min_size=22, valign="top", parent=None, ox=0, oy=0, check=True, layout=None):
        """Абзац в рамке box (абсолютные координаты): перенос по словам, кегль уменьшается, пока не влезет."""
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
                raise ValueError(f"{(layout or self.lay).name}: «{kind}» не влезает в {box}")
        total = lh * len(lines)
        y = y0 - (lh - size) / 2 if valign == "top" else (
            (y0 + y1 - total) / 2 if valign == "middle" else y1 - total + (lh - size) / 2)
        node = n_text(kind, text, x0 - ox, y - oy, size, weight, color, width=x1 - x0, align=align, leading=leading)
        self.add(node, parent)
        real = text_ink_box(node, ox, oy)
        if check:
            (layout or self.lay).add(kind, real, inside=(x0 - 1, y0 - size * 0.5, x1 + 1, y1 + size * 0.4))
        return real

    def chip(self, kind, text, x, y, fill=MILK, fg=INK, size=26, icon=None, align="left", depth=6, dot=None,
             parent=None, check=True):
        """Плашка-пилюля с текстом (дата, тег, время): в Figma — авто-раскладка, растёт под текст."""
        f = font(size, "Bold")
        tw = f.getlength(text)
        ih = int(size * 1.25) if icon or dot else 0
        gap = size * 0.35
        pad = size * 0.7
        w = tw + 2 * pad + (ih + gap if ih else 0)
        h = size * 1.85
        if align == "right":
            x -= w
        elif align == "center":
            x -= w / 2
        node = self.plate(kind, (x, y, x + w, y + h), fill, radius=h / 2, depth=depth, width=3, check=False,
                          parent=parent, layout={"dir": "H", "pad": pad, "gap": gap, "hug": True})
        if check:
            self.lay.add(kind, (x, y, x + w, y + h + depth))
        if icon:
            node["children"].append(n_image("иконка", rp.icon(icon), pad, (h - ih) / 2, ih, ih))
        elif dot:
            r = size * 0.32
            node["children"].append(n_ellipse("точка", pad + ih / 2 - r, h / 2 - r, 2 * r, 2 * r, fill=dot))
        asc, desc = f.getmetrics()
        node["children"].append(n_text("текст", text, pad + (ih + gap if ih else 0), (h - asc - desc) / 2, size,
                                       color=fg))
        return (x, y, x + w, y + h)


def scatter_doodles(b, names, count, seed, sizes=(56, 96), margin=14):
    """
    Дудлы в свободных местах: случайные места, размеры и наклоны, только там,
    где они не задевают текст, картинки и плашки (по раскладке b.lay) и друг
    друга. Слои — сразу над фоном (под стеклом). -> сколько поставили.
    """
    rng = random.Random(seed)
    placed = []
    tries = 0
    while len(placed) < count and tries < 1500:
        tries += 1
        size = rng.randint(*sizes)
        cx, cy = rng.uniform(24 + size / 2, W - 24 - size / 2), rng.uniform(24 + size / 2, H - 24 - size / 2)
        p = size * 0.12
        box = (cx - size / 2 + p - margin, cy - size / 2 + p - margin, cx + size / 2 - p + margin, cy + size / 2 - p + margin)
        if any(box[0] < o[2] and o[0] < box[2] and box[1] < o[3] and o[1] < box[3] for _, o, _ in b.lay.boxes):
            continue
        if any(box[0] < o[2] and o[0] < box[2] and box[1] < o[3] and o[1] < box[3] for o in placed):
            continue
        placed.append(box)
        name = names[len(placed) % len(names)]
        path = name if "/" in name else f"doodle_{name}.png"
        node = n_image("дудл", rp.icon(path), cx - size / 2, cy - size / 2, size, size, rng.uniform(-18, 18))
        b.scene.nodes.insert(1, node)
    for box in placed:
        b.lay.add("дудл", (box[0] + margin, box[1] + margin, box[2] - margin, box[3] - margin))
    return len(placed)


def face(name, phase=0.15, amp=0.6):
    return rp.solid(name, phase, amp=amp)


def ic(path):
    return rp.icon(path)


def spark_img(r, col=MILK):
    s = int(r * 2 + 6)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    rp._spark(img, s / 2, s / 2, r, col)
    return img


def sparkle(b, cx, cy, r, col=MILK):
    im = spark_img(r, col)
    b.decor("искра", im, cx - im.width / 2, cy - im.height / 2)



def slot_index(word):
    """Буква, вместо которой встаёт смайлик: первая «o», иначе «a» или «e»."""
    for ch in "oae":
        if ch in word:
            return word.index(ch)
    raise ValueError(f"в надписи «{word}» нет буквы o/a/e для смайлика")


def bg(kind="wine", seed=3):
    return rp.brand_bg(W, H, kind, seed=seed)


def glow(img, box, col, alpha, blur):
    g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(g).ellipse(box, fill=col + (alpha,))
    img.alpha_composite(g.filter(ImageFilter.GaussianBlur(blur)))
    return img


# ============================================================
# ШАБЛОНЫ. У каждого — тексты по умолчанию (пример) и функция сборки.
# ============================================================

T = {}


def template(key, label, **defaults):
    def deco(fn):
        T[key] = (label, defaults, fn)
        return fn
    return deco


@template("news", "Новость — классика", tag="#новости", date="19.10", title="Открыли новый канал для артов",
          text="Кидайте свои рисунки в #арты — лучшие попадут в баннер сервера каждую пятницу.")
def t_news(b, d):
    b.background(bg(d.get("bg", "wine"), d.get("seed", 2)))
    b.glass((40, 40, 1160, 560))
    c = b.chip("тег", d["tag"], 84, 78, fill=LIME)
    b.chip("дата", d["date"], c[2] + 16, 78, fill=MILK)
    b.word("надпись", d.get("word", "neeeeews"), 80, 300, 190, 790, letter="e", palette=d.get("wave", PINK_WAVE))
    b.sprite("смайлик", face(d.get("mood", "stars")), 975, 230, 270)
    b.para("заголовок", d["title"], (84, 340, 1100, 420), 52, color=MILK, weight="Bold", max_lines=1)
    b.para("текст", d["text"], (84, 436, 1100, 530), 30, max_lines=2)


@template("update", "Обновление бота", version="v2.4", title="Что нового в Purr",
          items="Стили живых картинок: стекло, 3D и глитч\nКнопка «Изменить» прямо под картинкой\nБаннер сервера обновляется сам",
          date="19.10.2026")
def t_update(b, d):
    b.background(bg(d.get("bg", "ice"), d.get("seed", 7)))
    b.glass((40, 40, 1160, 560))
    b.word("надпись", d.get("word", "updaaaate"), 80, 196, 160, 760, letter="a", palette=d.get("wave", ICE_WAVE))
    box = (850, 86, 1110, 216)
    p = b.plate("версия", box, LIME, radius=34)
    b.label("номер версии", d["version"], 130, 65, 78, anchor="mm", parent=p, width=260, align="center")
    b.sprite("искры", ic(d.get("ic_искры", "doodle3d/d3_sparkles.png")), 1070, 470, 110, angle=-10)
    b.para("заголовок", d["title"], (84, 236, 800, 296), 46, color=MILK, weight="Bold", max_lines=1)
    icons = ["ui3d/u3_add.png", "ui3d/u3_edit.png", "ui3d/u3_refresh.png", "ui3d/u3_star.png"]
    items = d["items"].split("\n")[:4]
    for i, line in enumerate(items):
        y = 330 + i * 64
        b.sprite(f"иконка {i + 1}", ic(icons[i]), 112, y + 22, 62)
        b.para(f"пункт {i + 1}", line, (160, y, 1010, y + 46), 32, color=MILK, max_lines=1)
    b.chip("дата", d["date"], 1110, 250, fill=MILK, align="right", size=22)


@template("event", "Ивент с датой", day="31", month="ОКТ", weekday="пятница", title="Хэллоуин-вечеринка",
          text="Костюмы, мафия и страшилки в войсе. Лучший костюм получит роль на месяц.", time="20:00 МСК",
          place="#войс-ивенты")
def t_event(b, d):
    b.background(bg(d.get("bg", "wine"), d.get("seed", 4)))
    cal = (70, 70, 410, 500)
    cw = cal[2] - cal[0]
    c = b.plate("календарь", cal, MILK, radius=36, depth=14, effects=[soft_shadow()])
    head = n_box("шапка", 0, 0, cw, 110, fill=MAROON, radius=[36, 36, 0, 0], stroke=INK, stroke_w=5)
    c["children"].append(head)
    b.label("месяц", d["month"], cw / 2, 55, 54, color=MILK, anchor="mm", parent=head, width=cw, align="center")
    for k, nm in ((0.3, "кольцо 1"), (0.7, "кольцо 2")):
        x = cw * k
        c["children"].append(n_box(nm, x - 12, -26, 24, 52, fill=SOFT, radius=12, stroke=INK, stroke_w=4))
    b.label("число", d["day"], cw / 2, 255, 200, anchor="mm", parent=c, width=cw, align="center")
    b.label("день недели", d["weekday"], cw / 2, cal[3] - cal[1] - 52, 34, "SemiBold", MAROON, anchor="mm",
            parent=c, width=cw, align="center")
    b.glass((460, 40, 1160, 560))
    b.word("надпись", d.get("word", "eveeeent"), 500, 210, 160, 1010, letter="e", palette=d.get("wave", PINK_WAVE))
    b.sprite("шарик", ic(d.get("ic_шарик", "doodle3d/d3_balloon.png")), 1080, 150, 120, angle=8)
    b.para("заголовок", d["title"], (504, 250, 1120, 310), 48, color=MILK, weight="Bold", max_lines=1)
    b.para("текст", d["text"], (504, 326, 1120, 430), 29, max_lines=3)
    t = b.chip("время", d["time"], 504, 456, fill=LIME, icon="ui3d/u3_time.png")
    b.chip("место", d["place"], t[2] + 18, 456, fill=PINK, icon="ui3d/u3_announce.png")


@template("giveaway", "Розыгрыш", prize="Nitro на месяц", text="Нажми на подарок под постом — победителя выберет бот.",
          until="до 25.10", winners="3 победителя")
def t_giveaway(b, d):
    b.background(bg(d.get("bg", "lime"), d.get("seed", 4)))
    b.glass((40, 40, 1160, 560), alpha=24)
    b.word("надпись", d.get("word", "giveaaaaway"), 600, 200, 170, 1000, letter="a", palette=d.get("wave", LIME_WAVE), center=True)
    b.sprite("подарок", ic(d.get("ic_подарок", "doodle3d/d3_gift.png")), 230, 390, 260, angle=-8)
    sparkle(b, 105, 270, 16, LIME)
    sparkle(b, 360, 280, 12, MILK)
    b.para("приз", d["prize"], (390, 260, 1000, 336), 64, color=LIME, weight="Bold", max_lines=1)
    b.para("текст", d["text"], (392, 350, 960, 430), 30, color=MILK, max_lines=2)
    c = b.chip("срок", d["until"], 392, 456, fill=MILK, icon="ui3d/u3_time.png")
    b.chip("победители", d["winners"], c[2] + 18, 456, fill=LIME, icon="ui3d/u3_trophy.png")
    b.sprite("смайлик", face(d.get("mood", "love")), 1060, 420, 170)


def stripes_img(w, h, a=LIME, c=INK, step=46):
    img = Image.new("RGBA", (w, h), c + (255,))
    d = ImageDraw.Draw(img)
    for x in range(-h, w + h, step * 2):
        d.polygon([(x, h), (x + step, h), (x + step + h, 0), (x + h, 0)], fill=a)
    return img


@template("alert", "Важное объявление", title="Сервер переезжает на новые роли",
          text="С понедельника старые роли перестанут выдаваться. Загляни в #роли и выбери новые.")
def t_alert(b, d):
    b.background(bg(d.get("bg", "dark"), d.get("seed", 6)))
    b.decor("полосы сверху", stripes_img(W, 54), 0, 0)
    b.decor("полосы снизу", stripes_img(W, 54), 0, H - 54)
    b.lay.add("полосы сверху", (0, 0, W, 54))
    b.lay.add("полосы снизу", (0, H - 54, W, H))
    b.glass((40, 90, 1160, 510), dots=False)
    b.sprite("мегафон", ic(d.get("ic_мегафон", "doodle3d/d3_megaphone.png")), 175, 300, 220, angle=-12)
    i = slot_index(d.get("word", "attention!"))
    b.word("надпись", d.get("word", "attention!"), 320, 250, 150, 1120, smiley_at=(i,), smiley=face(d.get("mood", "shock"), amp=0.8))
    b.para("заголовок", d["title"], (324, 280, 1120, 344), 46, color=LIME, weight="Bold", max_lines=1)
    b.para("текст", d["text"], (324, 360, 1120, 470), 30, max_lines=3)


def starburst_img(r, fill, n=14, angle=0):
    s = int(2 * r + 40)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    cx = cy = s / 2 - 6
    pts = []
    for i in range(n * 2):
        rr = r if i % 2 == 0 else r * 0.8
        a = math.radians(angle + 180 * i / n)
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    d = ImageDraw.Draw(img)
    d.polygon([(x + 6, y + 12) for x, y in pts], fill=dark(fill, 0.62), outline=INK, width=5)
    d.polygon(pts, fill=fill, outline=INK, width=5)
    return img, cx, cy


@template("feature", "Новая функция (светлый)", name="Своя слэш-команда",
          text="Создай команду для любой живой картинки — /казна +60, и картинка в канале перерисуется сама.",
          how="/pictures → Живые картинки → Своя команда", badge="new!")
def t_feature(b, d):
    b.background(bg(d.get("bg", "milk"), d.get("seed", 3)))
    sb = (1000, 150)
    star, scx, scy = starburst_img(120, LIME, angle=8)
    b.decor("звезда", star, sb[0] - scx, sb[1] - scy)
    b.lay.add("значок new", (sb[0] - 132, sb[1] - 132, sb[0] + 132, sb[1] + 144))
    f = font(74)
    tw = f.getlength(d["badge"])
    asc, desc = f.getmetrics()
    b.add(n_text("надпись на значке", d["badge"], sb[0] - tw / 2, sb[1] - (asc + desc) / 2, 74, color=INK, angle=14))
    b.word("надпись", d.get("word", "feeeature"), 80, 210, 170, 840, letter="e", palette=d.get("wave", INK_WAVE), color=INK)
    b.para("название", d["name"], (84, 250, 860, 316), 54, color=MAROON, weight="Bold", max_lines=1)
    b.para("текст", d["text"], (84, 334, 1110, 420), 30, color=GREY, max_lines=2)
    box = (80, 452, 1120, 530)
    p = b.plate("подсказка", box, MILK, radius=26, depth=8, width=4,
                layout={"dir": "H", "pad": 22, "gap": 16, "hug": False})
    p["children"].append(n_image("иконка", ic("ui3d/u3_play.png"), 22, (box[3] - box[1] - 56) / 2, 56, 56))
    size = 30
    while font(size, "SemiBold").getlength(d["how"]) > box[2] - box[0] - 120 and size > 18:
        size -= 2
    asc, desc = font(size, "SemiBold").getmetrics()
    p["children"].append(n_text("текст", d["how"], 94, (box[3] - box[1] - asc - desc) / 2, size, "SemiBold", INK))


@template("poll", "Голосование", question="Какой ивент в субботу?",
          options="Мафия:46\nКино-вечер:31\nКараоке:23", hint="голосуй кнопками под постом")
def t_poll(b, d):
    b.background(bg(d.get("bg", "wine"), d.get("seed", 8)))
    b.word("надпись", d.get("word", "voooote"), 80, 190, 170, 560, letter="o", palette=d.get("wave", PINK_WAVE))
    b.para("вопрос", d["question"], (84, 236, 520, 380), 50, color=MILK, weight="Bold", max_lines=3)
    b.sprite("смайлик", face(d.get("mood", "think")), 180, 470, 150)
    b.para("подсказка", d["hint"], (270, 440, 520, 520), 24, color=SOFT, max_lines=2, valign="middle")
    card = (580, 60, 1150, 540)
    g = b.glass(card, alpha=40, name="карточка", check=True)
    opts = [o.rsplit(":", 1) for o in d["options"].split("\n")][:4]
    cols = [LIME, PINK, ICE, MILK]
    top = 130
    step = (card[3] - 40 - top) / len(opts)
    for i, (name, pct) in enumerate(opts):
        y = top + i * step - card[1]
        pct = int(pct)
        row = n_box(f"вариант {i + 1}", 36, y - 10, 496, 104)
        g["children"].append(row)
        b.label("название", name, 0, 20, 34, color=MILK, anchor="lm", parent=row)
        b.label("процент", f"{pct}%", 496, 20, 34, color=cols[i], anchor="rm", parent=row)
        row["children"].append(n_box("шкала", 0, 54, 496, 40, fill=(255, 255, 255), alpha=40 / 255, radius=20,
                                     stroke=(255, 255, 255), stroke_w=2, stroke_alpha=80 / 255))
        fw = max(44, 496 * pct / 100)
        b.plate("заполнение", (0, 54, fw, 94), cols[i], radius=20, depth=5, width=3, parent=row, check=False)


@template("winners", "Победители", title="Итоги конкурса артов", first="Лиса", second="Кот", third="Ёжик")
def t_winners(b, d):
    b.background(bg(d.get("bg", "wine"), d.get("seed", 11)))
    b.glass((40, 40, 1160, 560))
    b.word("надпись", d.get("word", "winneeeers"), 600, 150, 128, 900, letter="e", palette=d.get("wave", PINK_WAVE), center=True)
    b.para("заголовок", d["title"], (240, 180, 960, 222), 34, color=SOFT, max_lines=1, align="center")
    blocks = [(2, d["second"], PINK, 300, 380, "laugh"), (1, d["first"], LIME, 600, 330, "cool"),
              (3, d["third"], ICE, 900, 400, "wink")]
    for place, name, col, cx, top, mood in blocks:
        box = (cx - 130, top, cx + 130, 526)
        p = b.plate(f"пьедестал {place}", box, col, radius=22, depth=14)
        b.label("место", str(place), 130, 36, 56, anchor="mm", parent=p, width=260, align="center")
        size = 34
        while font(size).getlength(name) > 230:
            size -= 2
        if 86 + size * 0.6 > box[3] - box[1] - 8:
            raise ValueError(f"winners: имя «{name}» вылезает из пьедестала")
        b.label("имя", name, 130, 86, size, anchor="mm", parent=p, width=260, align="center")
        b.sprite(f"смайлик {place}", face(mood), cx, top - 54, 120)
    b.sprite("кубок", ic(d.get("ic_кубок", "doodle3d/d3_trophy.png")), 1080, 300, 110, angle=10)
    b.sprite("звезда", ic(d.get("ic_звезда", "doodle3d/d3_star.png")), 120, 330, 90, angle=-12)


@template("maintenance", "Технические работы", title="Бот уходит на техработы",
          text="Обновляем базу и картинки. Команды временно не отвечают — всё вернётся само.",
          window="сегодня 03:00–05:00")
def t_maintenance(b, d):
    b.background(rp.glitch(bg(d.get("bg", "ice"), d.get("seed", 9)), 0.5, random.Random(5)), "фон (глитч)")
    b.glass((40, 40, 1160, 560), alpha=30)
    i = slot_index(d.get("word", "ooooops"))
    b.word("надпись", d.get("word", "ooooops"), 80, 220, 190, 720, letter="o", palette=d.get("wave", ICE_WAVE), phase=0.4,
           smiley_at=(i,), smiley=face(d.get("mood", "cracked"), amp=0.8))
    b.sprite("ключ", ic(d.get("ic_ключ", "doodle3d/d3_wrench.png")), 900, 170, 170, angle=-16)
    b.sprite("шестерёнка", ic(d.get("ic_шестерёнка", "doodle3d/d3_gear.png")), 1060, 230, 130, angle=12)
    b.para("заголовок", d["title"], (84, 270, 990, 336), 52, color=MILK, weight="Bold", max_lines=1)
    b.para("текст", d["text"], (84, 350, 1110, 440), 30, color=MILK, max_lines=2)
    b.chip("время", d["window"], 84, 464, fill=MILK, icon="ui3d/u3_time.png")


@template("welcome", "Добро пожаловать", count="+128", text="новых участников за неделю — привет, рады вам!")
def t_welcome(b, d):
    b.background(glow(bg(d.get("bg", "wine"), d.get("seed", 13)), (250, 120, 950, 600), PINK, 70, 90))
    i = slot_index(d.get("word", "welcooome"))
    b.word("надпись", d.get("word", "welcooome"), 600, 210, 190, 1080, letter="o", palette=d.get("wave", PINK_WAVE), center=True,
           smiley_at=(i,), smiley=face(d.get("mood", "smile_lime")))
    moods = ["wink", "love", "laugh", "cool", "stars", "kiss", "smile_pink"]
    n = len(moods)
    for k, m in enumerate(moods):
        x = 150 + k * 900 / (n - 1)
        y = 364 - 34 * math.cos(math.pi * (k - (n - 1) / 2) / (n - 1) * 1.6)
        b.sprite(f"смайлик {k + 1}", face(m, phase=k / n), x, y, 124)
    w = font(40).getlength(d["count"]) + 40 * 1.4
    tw = min(font(30, "SemiBold").getlength(d["text"]), 1000 - w)
    x = 600 - (w + 20 + tw) / 2
    b.chip("число", d["count"], x, 470, fill=LIME, size=40, depth=8)
    b.para("текст", d["text"], (x + w + 20, 470, 1140, 545), 30, color=MILK, max_lines=2, valign="middle")


@template("rules", "Правила", title="Обновили правила сервера",
          items="Уважай всех в чате и войсе\nБез рекламы и спама в личку\nАрты — только с подписью автора\nСпорные вопросы — в #поддержка")
def t_rules(b, d):
    b.background(bg(d.get("bg", "dark"), d.get("seed", 12)))
    b.add(n_box("полоса", 0, 0, 210, H, fill=WINE, alpha=210 / 255))
    # надпись снизу вверх вдоль левого края
    text = d.get("word", "ruuuules")
    size = rp.fit_size(text, H - 60, 150)
    a, e = rb.run(text, max(set(text), key=lambda ch: (lambda r: r[1] - r[0])(rb.run(text, ch))))
    cols = rp.stretch_colors(text, a, e, phase=0.3, palette=d.get("wave", LIME_WAVE))
    asc, desc = font(size).getmetrics()
    bx = rb.text_box(text, 0, 0, size)          # верх/низ букв от базовой линии
    node = n_text("надпись", text, 0, 0, size, runs=[(i, i + 1, c) for i, c in enumerate(cols) if c != MILK],
                  track=TRACK, effects=[text_shadow(size)], angle=90)
    tw = node["w"]
    # после поворота на 90°: строка идёт снизу вверх, верх букв смотрит влево
    left = 40 - (asc + bx[1])                   # левый край рамки (верх строки) — буквы начинаются с 40 px
    node["x"] = left + (asc + desc) / 2 - tw / 2
    node["y"] = H - 30 - tw / 2 - node["h"] / 2
    b.add(node)
    b.lay.add("надпись", (40, H - 30 - tw, 40 + bx[3] - bx[1], H - 30))
    b.sprite("щит", ic(d.get("ic_щит", "doodle3d/d3_shield.png")), 1080, 100, 120, angle=10)
    b.para("заголовок", d["title"], (260, 54, 1000, 130), 52, color=MILK, weight="Bold", max_lines=1,
           valign="middle")
    items = d["items"].split("\n")[:5]
    top, bottom = 170, 560
    step = (bottom - top) / len(items)
    for i, it in enumerate(items):
        y = top + i * step
        h = step - 18
        card = b.glass((260, y, 1140, y + h), radius=20, alpha=26, dots=False, name=f"правило {i + 1}", check=True)
        nb = b.plate("номер", (16, h / 2 - 26, 68, h / 2 + 22), LIME if i % 2 == 0 else PINK, radius=14, depth=4,
                     width=3, parent=card, check=False)
        b.label("цифра", str(i + 1), 26, 24, 30, anchor="mm", parent=nb, width=52, align="center")
        b.para("текст", it, (350, y + 8, 1120, y + h - 8), 32, color=MILK, max_lines=1, valign="middle",
               parent=card, ox=260, oy=y, check=False)


@template("live", "Стрим / эфир", title="Рисуем баннер сервера вместе", time="сегодня в 19:00",
          where="#стрим", badge="В ЭФИРЕ")
def t_live(b, d):
    b.background(glow(bg(d.get("bg", "dark"), d.get("seed", 14)), (-100, 200, 600, 700), MAROON, 120, 100))
    b.word("надпись", d.get("word", "liiive"), 70, 300, 200, 560, letter="i", palette=d.get("wave", PINK_WAVE))
    b.chip("в эфире", d["badge"], 76, 50, fill=MAROON, fg=MILK, size=28, dot=LIME)
    b.para("заголовок", d["title"], (76, 330, 560, 440), 46, color=MILK, weight="Bold", max_lines=2)
    t = b.chip("время", d["time"], 76, 470, fill=MILK, icon="ui3d/u3_time.png", size=24)
    b.chip("где", d["where"], t[2] + 16, 470, fill=PINK, size=24)
    scr = (610, 70, 1150, 470)
    g = b.glass(scr, alpha=44, name="экран", effects=[soft_shadow()])
    b.lay.add("экран", (scr[0], scr[1], scr[2], 530))
    b.sprite("смайлик", face(d.get("mood", "cool")), 270, 180, 230, parent=g, check=False)
    b.sprite("кнопка play", ic("ui3d/u3_play.png"), 470, 340, 80, parent=g, check=False)
    b.add(n_box("шкала", 660, 496, 440, 16, fill=(255, 255, 255), alpha=60 / 255, radius=8))
    b.plate("прогресс", (660, 496, 930, 512), LIME, radius=8, depth=3, width=2, check=False)


@template("soon", "Скоро / отсчёт", caption="до старта нового сезона", days="02", hours="14", minutes="37")
def t_soon(b, d):
    b.background(bg(d.get("bg", "wine"), d.get("seed", 16)))
    b.glass((40, 40, 1160, 560))
    b.word("надпись", d.get("word", "sooooon"), 600, 200, 176, 900, letter="o", palette=d.get("wave", PINK_WAVE), center=True)
    b.sprite("часы", ic(d.get("ic_часы", "doodle3d/d3_hourglass.png")), 1040, 150, 140, angle=12)
    b.sprite("ракета", ic(d.get("ic_ракета", "doodle3d/d3_rocket.png")), 160, 150, 130, angle=-14)
    b.para("подпись", d["caption"], (200, 246, 1000, 298), 38, color=SOFT, max_lines=1, align="center")
    parts = [(d["days"], "дней", LIME), (d["hours"], "часов", PINK), (d["minutes"], "минут", ICE)]
    bw, gap = 250, 50
    x0 = 600 - (3 * bw + 2 * gap) / 2
    for k, (num, lab, col) in enumerate(parts):
        box = (x0 + k * (bw + gap), 320, x0 + k * (bw + gap) + bw, 500)
        p = b.plate(f"ячейка {lab}", box, col, radius=30, depth=14)
        b.label("число", num, bw / 2, 76, 110, anchor="mm", parent=p, width=bw, align="center")
        b.label("подпись", lab, bw / 2, 146, 30, "SemiBold", dark(col, 0.4), anchor="mm", parent=p, width=bw,
                align="center")
        if k < 2:
            b.label("двоеточие", ":", box[2] + gap / 2, 400, 90, color=MILK, anchor="mm")


@template("breaking", "Срочно (бегущая строка)", title="Purr научился сам обновлять баннер сервера",
          text="Участники, войс и бусты — теперь цифры на баннере живые.", top_word="breaking",
          bottom_line="срочно · новости сервера")
def t_breaking(b, d):
    b.background(bg(d.get("bg", "wine"), d.get("seed", 17)))
    top = b.add(n_box("лента сверху", 0, 0, W, 86, fill=INK, clip=True))
    b.lay.add("лента сверху", (0, 0, W, 86))
    rep = (d["top_word"] + "  •  ") * 8
    runs = [(i, i + 1, PINK) for i, ch in enumerate(rep) if ch == "•"]
    b.label("бегущая строка", rep, -40, 43, 46, color=LIME, anchor="lm", parent=top, runs=runs)
    bot = b.add(n_box("лента снизу", 0, H - 70, W, 70, fill=LIME, clip=True))
    b.lay.add("лента снизу", (0, H - 70, W, H))
    b.label("бегущая строка", (d["bottom_line"] + " · ") * 6, 20, 35, 30, color=INK, anchor="lm", parent=bot)
    b.glass((40, 120, 1160, 500))
    b.sprite("смайлик", face(d.get("mood", "shock"), amp=0.8), 1020, 250, 220)
    b.para("заголовок", d["title"], (84, 160, 880, 350), 64, color=MILK, weight="Bold", max_lines=3)
    b.para("текст", d["text"], (84, 372, 900, 470), 30, max_lines=2)


@template("split", "Сплит: текст + смайлик", title="Горячая новость недели",
          text="Клановые карточки уже в Figma — скоро их будет рисовать бот.", tag="#кланы")
def t_split(b, d):
    b.background(bg(d.get("bg", "wine"), d.get("seed", 18)))
    b.add(n_box("белая половина", -40, -40, 720, H + 80, fill=MILK, radius=60,
                effects=[shadow_fx(0, 0, 48, (10, 6, 8), 0.35)]))
    b.chip("тег", d["tag"], 70, 60, fill=LIME, size=26)
    b.word("надпись", d.get("word", "hoooot"), 66, 290, 200, 640, letter="o", palette=d.get("wave", INK_WAVE), color=INK)
    b.para("заголовок", d["title"], (70, 336, 630, 404), 46, color=MAROON, weight="Bold", max_lines=1)
    b.para("текст", d["text"], (70, 418, 630, 548), 30, color=GREY, max_lines=3)
    b.lay.add("край белой половины", (680, 0, 700, H))
    b.sprite("смайлик", face(d.get("mood", "cool"), amp=0.9), 945, 300, 360)
    b.sprite("огонь", ic(d.get("ic_огонь", "doodle3d/d3_fire.png")), 1110, 96, 120, angle=12)
    sparkle(b, 760, 120, 18, LIME)
    sparkle(b, 1120, 520, 14, PINK)
    sparkle(b, 780, 500, 10, MILK)


def photo_placeholder(w, h, photo=None):
    if photo:
        im = Image.open(photo).convert("RGBA")
        k = max(w / im.width, h / im.height)
        im = im.resize((int(im.width * k) + 1, int(im.height * k) + 1), Image.LANCZOS)
        return im.crop(((im.width - w) // 2, (im.height - h) // 2, (im.width - w) // 2 + w, (im.height - h) // 2 + h))
    im = rp.brand_bg(w, h, "ice", seed=4)
    rp.put(im, ic("ui3d/u3_image.png"), w / 2, h / 2 - 16, 110)
    ImageDraw.Draw(im).text((w / 2, h / 2 + 66), "фото здесь", font=font(26, "SemiBold"), fill=INK, anchor="mm")
    return im


@template("photo", "Фото-отчёт", title="Как прошёл хэллоуин", caption="31.10 · войс-ивенты",
          text="Сорок человек в костюмах, три раунда мафии и победа команды тыкв.", photo=None)
def t_photo(b, d):
    b.background(bg(d.get("bg", "wine"), d.get("seed", 19)))
    b.word("надпись", d.get("word", "recaaaap"), 70, 200, 170, 640, letter="a", palette=d.get("wave", PINK_WAVE))
    b.para("заголовок", d["title"], (74, 240, 640, 310), 50, color=MILK, weight="Bold", max_lines=1)
    b.para("текст", d["text"], (74, 326, 620, 440), 30, max_lines=3)
    b.sprite("камера", ic(d.get("ic_камера", "doodle3d/d3_camera.png")), 140, 500, 110, angle=-10)
    pw, ph = 420, 480
    px, py = 710, (H - ph) / 2
    pol = b.add(n_box("полароид", px, py, pw, ph, fill=MILK, radius=14, stroke=INK, stroke_w=5, angle=-6,
                      effects=[shadow_fx(10, 18, 28, (0, 0, 0), 0.45)]))
    hw, hh = pw - 52, ph - 136
    pol["children"].append(n_image("фото (замени картинку)", photo_placeholder(hw, hh, d.get("photo")), 26, 26, hw, hh))
    pol["children"].append(n_box("рамка фото", 26, 26, hw, hh, stroke=INK, stroke_w=4))
    b.label("подпись", d["caption"], pw / 2, ph - 55, 32, "SemiBoldItalic", MAROON, anchor="mm", parent=pol,
            width=pw, align="center")
    a = math.radians(6)
    rw = pw * math.cos(a) + ph * math.sin(a)
    rh = pw * math.sin(a) + ph * math.cos(a)
    cx, cy = px + pw / 2, py + ph / 2
    b.lay.add("полароид", (cx - rw / 2, max(0, cy - rh / 2), cx + rw / 2, min(H, cy + rh / 2)))
    tape = Image.new("RGBA", (150, 44), PINK + (190,))
    b.decor("скотч", tape, cx - 75, max(0, cy - rh / 2) + 8, angle=8)


@template("quote", "Цитата", quote="Лучший сервер, где даже бот улыбается в ответ.", author="Лиса",
          role="участница с 2024")
def t_quote(b, d):
    b.background(bg(d.get("bg", "ice"), d.get("seed", 20)))
    b.glass((40, 40, 1160, 560), alpha=30)
    b.label("кавычки", "“", 70, 34, 300, color=LIME, anchor="la",
            effects=[shadow_fx(8, 12, 0, dark(LIME, 0.6), 1.0)])
    b.lay.add("кавычки", (70, 70, 230, 210))
    b.para("цитата", d["quote"], (250, 110, 1100, 360), 60, color=MILK, weight="SemiBoldItalic",
           max_lines=3, leading=1.18, valign="middle")
    b.sprite("аватар", face("smile_pink"), 330, 450, 140)
    b.para("автор", d["author"], (410, 400, 900, 450), 44, color=MILK, weight="Bold", max_lines=1)
    b.para("роль", d["role"], (412, 458, 900, 500), 28, color=WINE, weight="SemiBold", max_lines=1)
    b.sprite("сердце", ic(d.get("ic_сердце", "doodle3d/d3_heart.png")), 1060, 460, 110, angle=12)


@template("milestone", "Рекорд / цифра", number="1 000", unit="membeeeers", text="нас уже тысяча — спасибо, что вы с нами!")
def t_milestone(b, d):
    b.background(bg(d.get("bg", "lime"), d.get("seed", 21)))
    b.glass((40, 40, 1160, 560), alpha=22)
    num = d["number"]
    size = 270
    while font(size).getlength(num) > 820 and size > 120:
        size -= 6
    fx = [shadow_fx(k * 0.4, k, 0, dark(LIME, 0.45), 1.0) for k in range(14, 0, -2)]
    b.label("число", num, 600, 330, size, color=LIME, anchor="ms", effects=fx, stroke=(INK, 6))
    bx = font(size).getbbox(num, anchor="ms")
    b.lay.add("число", (600 + bx[0], 330 + bx[1], 600 + bx[2], 330 + bx[3]))
    b.word("надпись", d["unit"], 600, 450, 110, 900, letter="e", palette=d.get("wave", LIME_WAVE), center=True)
    b.para("текст", d["text"], (200, 476, 1000, 530), 32, color=MILK, max_lines=1, align="center")
    b.sprite("смайлик", face(d.get("mood", "stars")), 1050, 160, 150)
    b.sprite("звезда", ic(d.get("ic_звезда", "doodle3d/d3_star.png")), 150, 170, 120, angle=-12)
    b.sprite("медаль", ic(d.get("ic_медаль", "doodle3d/d3_medal.png")), 140, 420, 100, angle=10)
    b.sprite("конфети", ic(d.get("ic_конфети", "doodle3d/d3_sparkles.png")), 1060, 420, 100)


def avatar_img(r, inner, seed):
    im = rp.brand_bg(2 * r, 2 * r, "wine", seed=seed)
    rp.put(im, inner, r, r + 6, int(r * 1.6))
    return im


@template("collab", "Коллаборация", left="Наш сервер", right="Котики RP",
          text="Объединяем ивенты: общие турниры каждую субботу и обмен ролями.")
def t_collab(b, d):
    b.background(bg(d.get("bg", "wine"), d.get("seed", 22)))
    b.glass((40, 40, 1160, 560))
    b.word("надпись", d.get("word", "collaaaab"), 600, 170, 150, 900, letter="a", palette=d.get("wave", PINK_WAVE), center=True)
    for cx, name, inner, col in ((330, d["left"], face("smile_lime"), PINK), (870, d["right"], ic("doodle3d/d3_cat.png"), ICE)):
        r, cy = 100, 290
        b.add(n_ellipse("кольцо аватара", cx - r, cy - r, 2 * r, 2 * r, fill=col, stroke=INK, stroke_w=5,
                        effects=[shadow_fx(6, 14, 0, INK, 1.0), shadow_fx(6, 14, 0, dark(col, 0.55), 1.0, spread=-5)]))
        b.add(n_ellipse("аватар (замени картинку)", cx - r + 16, cy - r + 16, 2 * r - 32, 2 * r - 32, stroke=INK,
                        stroke_w=4, img=avatar_img(r - 16, inner, int(cx))))
        b.lay.add(f"аватар {name}", (cx - r, cy - r, cx + r + 6, cy + r + 14))
        b.para(f"имя {name}", name, (cx - 200, 418, cx + 200, 462), 38, color=MILK, weight="Bold",
               max_lines=1, align="center")
    b.label("×", "×", 600, 290, 140, color=LIME, anchor="mm", effects=[shadow_fx(4, 8, 0, dark(LIME, 0.5), 1.0)])
    b.lay.add("крестик", (550, 240, 650, 340))
    b.para("текст", d["text"], (120, 482, 1080, 540), 28, max_lines=2, align="center")


@template("digest", "Дайджест недели", period="13–19 октября",
          cards="Кланы|Казна и магазин клана уже работают|ui3d/u3_people.png\n"
                "Картинки|Три новых стиля живых картинок|ui3d/u3_image.png\n"
                "Ивенты|Хэллоуин 31.10 в 20:00 в войсе|doodle3d/d3_balloon.png")
def t_digest(b, d):
    b.background(bg(d.get("bg", "dark"), d.get("seed", 23)))
    b.word("надпись", d.get("word", "weeeekly"), 70, 170, 160, 780, letter="e", palette=d.get("wave", LIME_WAVE))
    b.chip("период", d["period"], 1130, 92, fill=LIME, icon="ui3d/u3_time.png", align="right")
    cards = [c.split("|") for c in d["cards"].split("\n")][:3]
    n = len(cards)
    gap = 30
    cw = (1130 - 70 - gap * (n - 1)) / n
    cols = [PINK, LIME, ICE]
    for k, (title, text, icon) in enumerate(cards):
        x0 = 70 + k * (cw + gap)
        box = (x0, 230, x0 + cw, 540)
        g = b.glass(box, alpha=36, dots=k == n - 1, name=f"карточка {k + 1}", effects=[soft_shadow()], check=True)
        inner = Layout(f"карточка {k + 1}")
        b.sprite("иконка", ic(icon), 70, 70, 96, parent=g, check=False)
        b.chip("заголовок", title, 130, 46, fill=cols[k], size=30, parent=g, check=False)
        b.para("текст", text, (x0 + 30, 370, x0 + cw - 30, 520), 34, color=MILK, weight="Bold", max_lines=3,
               parent=g, ox=x0, oy=230, layout=inner)


# ============================================================
# экспорт в Figma: описание слоёв + картинки
# ============================================================

def _png_bytes(img):
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def figma_spec(scene, folder):
    """Сцена → JSON для сборщика в Figma; картинки слоёв — PNG-файлы (одинаковые — один файл)."""
    folder.mkdir(parents=True, exist_ok=True)

    def conv(n):
        out = {k: v for k, v in n.items() if not k.startswith("_") and k not in ("img", "children")}
        for k in ("x", "y", "w", "h"):
            out[k] = round(float(n[k]), 2)
        if n.get("img") is not None:
            data = _png_bytes(n["img"])
            name = hashlib.sha1(data).hexdigest()[:12] + ".png"
            (folder / name).write_bytes(data)
            out["asset"] = name
        if n["type"] == "text":
            out["lh"] = round(n["_lh"], 2)
        if n.get("children"):
            out["children"] = [conv(c) for c in n["children"]]
        return out
    return [conv(n) for n in scene.nodes]


# ============================================================

def build(key, **texts):
    label, defaults, fn = T[key]
    data = dict(defaults, **texts)
    b = Builder(key)
    fn(b, data)
    return b


def render(key, **texts):
    b = build(key, **texts)
    return to_png(b.scene), b.lay


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
    args = sys.argv[1:]
    figma_dir = None
    if "--figma" in args:
        i = args.index("--figma")
        figma_dir = pathlib.Path(args[i + 1]).resolve()
        del args[i:i + 2]
    keys = [k for k in T if not args or k in args]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    done = []
    for key in keys:
        b = build(key)
        img = to_png(b.scene)
        path = OUT_DIR / f"news.{key}.png"
        img.save(path, optimize=True)
        done.append((key, img))
        if figma_dir:
            spec = figma_spec(b.scene, figma_dir / "assets")
            (figma_dir / f"{key}.json").write_text(json.dumps({"key": key, "label": T[key][0], "nodes": spec},
                                                              ensure_ascii=False))
        print(f"{path.relative_to(ROOT)}: ок, {len(b.lay.boxes)} элементов без наложений")
    if not args:
        contact_sheet(done).save(OUT_DIR / "_all.png", optimize=True)


if __name__ == "__main__":
    main()
