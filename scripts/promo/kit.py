"""Общие кусочки сценариев: заставка, вызов команды, модалки, финальная карточка."""

import math

from PIL import Image, ImageDraw, ImageFont

import config
import engine
from engine import ROOT, FONTS, img, ease_out

U = "panel_art/emoji/ui3d/u3_{}.png"
D = "panel_art/emoji/doodle3d/d3_{}.png"


def u(name):
    return U.format(name)


def dd(name):
    return D.format(name)


def F(size, weight="bold"):
    return ImageFont.truetype(str(ROOT / "fonts" / FONTS[weight]), size)


def video(name, title, subtitle, steps, **kw):
    return engine.Video(name, title, subtitle, steps, config.USER, config.BOT, **kw)


def chapter(v, n, total, title, steps, icon, dur=1.8):
    """Заставка части + новый список шагов справа."""
    pic = img(icon, 220)
    label = f"Часть {n} из {total}"

    def draw(layer, p, now):
        d = ImageDraw.Draw(layer)
        d.rectangle((0, 0, engine.W, engine.H), fill=(40, 22, 30, 246))
        k = ease_out(p / 0.3)
        layer.alpha_composite(pic, (int(engine.W / 2 - 110), int(300 - (1 - k) * 40 + 8 * math.sin(now * 3))))
        d.text((engine.W / 2, 600), label, font=F(40, "semibold"), fill=(228, 174, 196), anchor="mm")
        d.text((engine.W / 2, 690), title, font=F(92), fill=(242, 238, 234), anchor="mm")
    v.sound("whoosh")
    v.do(lambda s: s.update(steps=steps, chapter=f"{label} · {title}", step=-1))
    v.overlay("card", dur, draw=draw)


# ---------- заставка и финал ----------

def intro(v, command, tagline, dur=3.4):
    thumb = img(getattr(v, "thumb", f"panel_art/thumbnail.{v.name}.png"), 300)

    def draw(layer, p, now):
        d = ImageDraw.Draw(layer)
        d.rectangle((0, 0, engine.W, engine.H), fill=(40, 22, 30, 250))
        k = ease_out(p / 0.25)
        bob = 10 * math.sin(now * 3)
        layer.alpha_composite(thumb, (int(engine.W / 2 - 150), int(250 - (1 - k) * 60 + bob)))
        d.text((engine.W / 2, 640), command, font=F(120), fill=(242, 238, 234), anchor="mm")
        d.text((engine.W / 2, 740), tagline, font=F(40, "semibold"), fill=(228, 174, 196), anchor="mm")
    v.sound("whoosh")
    v.overlay("card", dur, draw=draw)


def outro(v, title, items, dur=9.0):
    check = img(u("confirm"), 56)
    thumb = img(getattr(v, "thumb", f"panel_art/thumbnail.{v.name}.png"), 170)

    def draw(layer, p, now):
        d = ImageDraw.Draw(layer)
        d.rectangle((0, 0, engine.W, engine.H), fill=(40, 22, 30, 252))
        layer.alpha_composite(thumb, (150, 110 + int(8 * math.sin(now * 3))))
        d.text((350, 195), title, font=F(76), fill=(242, 238, 234), anchor="lm")
        for n, text in enumerate(items):
            appear = 0.1 + n * 0.08
            k = ease_out((p - appear) / 0.06)
            if k <= 0:
                continue
            y = 360 + n * 112
            x = 170 + (1 - k) * 40
            card = Image.new("RGBA", layer.size, (0, 0, 0, 0))
            ImageDraw.Draw(card).rounded_rectangle((x - 20, y - 46, x + 1580, y + 46), radius=26,
                                                   fill=(255, 255, 255, int(30 * k)), outline=(255, 255, 255, int(60 * k)))
            layer.alpha_composite(card)
            layer.alpha_composite(check, (int(x), y - 28))
            d.text((x + 80, y), text, font=F(40, "semibold"), fill=(242, 238, 234, int(255 * k)), anchor="lm")
    v.sound("whoosh")
    for n in range(len(items)):
        v.sound("tick", offset=dur * (0.1 + n * 0.08))
    v.overlay("card", dur, draw=draw)


# ---------- команда и ответы ----------

def slash(v, command, desc, others=()):
    """Курсор в поле ввода, печать «/», меню команд, выбор, Enter."""
    v.click("input", move=0.6, after=0.15)
    items = [{"name": command, "desc": desc}] + [{"name": n, "desc": d_} for n, d_ in others]
    v.type_input(command[:3], 0.35)
    v.do(lambda s: s.update(popup={"items": items, "active": 0}))
    v.sound("whoosh")
    v.wait(0.5)
    v.click(f"cmd:{command}", move=0.45, after=0.1)
    v.do(lambda s: s.update(popup=None, input=command + " "))
    v.wait(0.35)
    v.do(lambda s: s.update(input=""))
    v.sound("send")


def panel(title, text, thumb, buttons, banner=None, extra_rows=None):
    """Ответ бота «панелью»: (баннер), embed с иконкой, кнопки."""
    embeds = []
    if banner:
        embeds.append({"banner": banner, "color": "#B6EC34"})
    embeds.append({"title": title, "desc": text, "thumb": thumb, "color": "#B6EC34"})
    rows = [buttons[i:i + 5] for i in range(0, len(buttons), 5)] + (extra_rows or [])
    return embeds, rows


def btn(id_, label, emoji=None, style=2):
    return {"type": "button", "id": id_, "label": label, "emoji": emoji, "style": style}


def modal(v, title, fields, submit="modal:ok"):
    t = v.t
    v.do(lambda s: s.update(modal={"title": title, "fields": [dict(f) for f in fields], "submit": submit, "t": t}))
    v.sound("whoosh")
    v.wait(0.35)


def close_modal(v, submit="modal:ok"):
    v.click(submit, move=0.45, after=0.1)
    v.do(lambda s: s.update(modal=None))
    v.sound("send")


def fill(v, values, speed=1.0):
    """Заполнить поля модалки по очереди: {id: текст}."""
    for fid, text in values.items():
        v.click(f"field:{fid}", move=0.4, after=0.05, sound="click")
        v.type_field(fid, text, dur=max(0.35, min(1.6, len(text) * 0.035)) / speed)
        v.wait(0.12)


def pick(v, select_id, options, index, hover_path=True):
    """Открыть меню выбора и выбрать пункт."""
    v.click(select_id, move=0.5, after=0.1)
    v.do(lambda s: s.update(dropdown={"select": select_id, "options": options, "hover": None, "up": True}))
    v.sound("whoosh")
    v.wait(0.35)
    v.move(f"{select_id}:{index}", 0.4)
    v.do(lambda s: s["dropdown"].update(hover=index) if s["dropdown"] else None)
    v.wait(0.15)
    v.click(None, after=0.1)
    v.do(lambda s: s.update(dropdown=None))


def toast(text, ok=True):
    """Короткий ответ бота «только тебе»."""
    return {"title": None, "desc": (":u3_confirm: " if ok else ":u3_warn: ") + text,
            "color": "#248046" if ok else "#DA373C", "width": 400}
