"""Ролик /logo — иконки ролей в стиле сервера."""

import sys

from PIL import Image, ImageDraw

import config
import engine
import kit
from kit import btn, u, dd

STEPS = [
    ("Иконки ролей", "/logo рисует иконку роли в стиле твоего сервера"),
    ("Словами", "опиши, что нарисовать — нейросеть нарисует в стиле"),
    ("Варианты", "«Ещё вариант» и «Убрать фон» — пока не понравится"),
    ("На роль", "одна кнопка — иконка стоит у роли рядом с ником"),
    ("Из наброска", "прикрепи свой макет — бот перерисует его в стиле сервера"),
    ("Свои стили", "стили по твоим референсам — админ добавляет в /eminemovna"),
]
LIME = "#B6EC34"


def _icon(fn_name, size=512):
    """Большая 3D-иконка из render_doodles3d (в ролике — «результат генерации»)."""
    sys.path.insert(0, str(engine.ROOT / "scripts"))
    import render_doodles3d as d3
    d3.small = lambda img, s=128: img
    return getattr(d3, fn_name)().resize((size, size), Image.LANCZOS)


def _save(im, name, bg=None):
    config.CACHE.mkdir(exist_ok=True)
    if bg:
        base = Image.new("RGBA", im.size, bg + (255,))
        base.alpha_composite(im)
        im = base
    path = config.CACHE / name
    im.save(path)
    return str(path.relative_to(engine.ROOT))


def images():
    shield = _icon("d3_shield")
    r, g, b, a = shield.split()
    pink = Image.merge("RGBA", (b, r, g, a))              # «ещё вариант» — другой цвет
    dark = (49, 51, 56)
    out = {"shield": _save(shield, "logo_shield.png", dark), "shield2": _save(pink, "logo_shield2.png", dark),
           "shield_nobg": _save(shield, "logo_shield_nobg.png")}
    # набросок -> результат
    sketch = Image.open(engine.ROOT / "panel_art/emoji/doodle_rocket.png").convert("RGBA").resize((360, 360))
    rocket = _icon("d3_rocket", 360)
    pair = Image.new("RGBA", (900, 420), dark + (255,))
    pair.alpha_composite(sketch, (20, 30))
    pair.alpha_composite(rocket, (520, 30))
    d = ImageDraw.Draw(pair)
    d.polygon([(400, 190), (470, 210), (400, 230)], fill=(182, 236, 52))
    d.line((380, 210, 420, 210), fill=(182, 236, 52), width=10)
    out["pair"] = _save(pair, "logo_pair.png")
    # стили
    styles = Image.new("RGBA", (900, 330), dark + (255,))
    for n, path in enumerate(("panel_art/emoji/doodle_star.png", "panel_art/emoji/doodle3d/d3_star.png",
                              "panel_art/emoji/ui3d/u3_star.png")):
        im = Image.open(engine.ROOT / path).convert("RGBA").resize((250, 250), Image.LANCZOS)
        styles.alpha_composite(im, (25 + n * 300, 30))
    out["styles"] = _save(styles, "logo_styles.png")
    return out


def build():
    pics = images()
    v = kit.video("logo", "/logo", "иконки ролей в стиле сервера", STEPS, channel="общий",
                  channels=["общий", "модерация", "ивенты"])
    v.tempo = 1.45
    kit.intro(v, "/logo", "иконка для любой роли — в стиле твоего сервера")

    v.step(0)
    kit.slash(v, "/logo", "Иконки для ролей в стиле сервера",
              others=[("/embed", "Создать Message Build"), ("/interactive", "Кнопки, меню ролей и формы")])
    v.bot_typing(0.3)
    embeds, rows = kit.panel(
        "ИКОНКИ ДЛЯ РОЛЕЙ", "Иконка в стиле сервера.\n• С нуля — кнопка ниже или `/logo brief:`\n"
                            "• Из своего макета — `/logo image:`", "panel_art/thumbnail.logo.png",
        [btn("g:gen", "Сгенерировать", dd("brush"), 1)], banner="panel_art/logo.home.banner.gif")
    pid = v.message({"kind": "bot", "reply": "/logo", "embeds": embeds, "rows": rows, "ephemeral": True})
    v.wait(1.8)

    v.step(1)
    v.click("g:gen")
    kit.modal(v, "Что нарисовать", [{"id": "brief", "label": "Описание", "placeholder": "", "tall": True},
                                    {"id": "style", "label": "Стиль", "placeholder": "основной"}])
    kit.fill(v, {"brief": "щит с галочкой для модераторов", "style": "3D, как эмодзи сервера"}, speed=1.6)
    kit.close_modal(v)
    v.edit(pid, lambda m: m.update(embeds=[{"title": "Рисую… :d3_brush:", "color": LIME,
                                            "desc": "Нейросеть рисует иконку в стиле сервера."}], rows=[]), sound="pop")
    v.wait(1.6)
    res_rows = [[btn("r:apply", "Поставить на роль", u("roles"), 1), btn("r:again", "Ещё вариант", u("refresh")),
                 btn("r:nobg", "Убрать фон", u("clear"))]]
    result = {"title": "ИКОНКА ГОТОВА", "color": LIME, "image": pics["shield"], "image_ratio": 1.0, "width": 300,
              "desc": "Стиль: **3D** · режим: с нуля\nТЗ: щит с галочкой для модераторов"}
    v.edit(pid, lambda m: m.update(embeds=[dict(result)], rows=res_rows), sound="success")
    v.wait(1.6)

    v.step(2)
    v.click("r:again")
    v.edit(pid, lambda m: m["embeds"][0].update(image=pics["shield2"]), sound="pop")
    v.wait(1.0)
    v.click("r:again")
    v.edit(pid, lambda m: m["embeds"][0].update(image=pics["shield"]), sound="pop")
    v.wait(0.6)
    v.click("r:nobg")
    v.edit(pid, lambda m: m["embeds"][0].update(image=pics["shield_nobg"]), sound="success")
    v.wait(1.0)

    v.step(3)
    v.edit(pid, lambda m: m.update(rows=res_rows + [[{"type": "select", "id": "r:role", "placeholder": "Какой роли?"}]]))
    v.click("r:apply")
    kit.pick(v, "r:role", [{"label": "@модератор", "emoji": dd("shield")}, {"label": "@ивентер", "emoji": dd("balloon")},
                           {"label": "@дизайнер", "emoji": dd("palette")}], 0)
    v.edit(pid, lambda m: m.update(embeds=[kit.toast("Иконка стоит у роли **@модератор**")], rows=[]), sound="success")
    v.wait(0.8)
    cat = config.member("Кот", "#7DB4FF", (90, 140, 220))
    cat["role_icon"] = pics["shield_nobg"]
    v.message({"kind": "user", "author": cat, "text": "о, у меня теперь значок модератора рядом с ником :smile_cool:"})
    v.wait(1.8)

    v.step(4)
    v.type_input("/logo image: rocket_sketch.png brief: сделай объёмной", 1.6)
    v.sound("send")
    v.do(lambda s: s.update(input=""))
    v.bot_typing(1.2)
    v.message({"kind": "bot", "reply": "/logo", "embeds": [
        {"title": "ИКОНКА ГОТОВА", "color": LIME, "image": pics["pair"], "image_ratio": 420 / 900, "width": 420,
         "desc": "Стиль: **3D** · режим: из макета\nТЗ: сделай объёмной"}],
        "rows": res_rows, "ephemeral": True}, sound="success")
    v.wait(2.4)

    v.step(5)
    v.message({"kind": "bot", "embeds": [
        {"title": "Один рисунок — разные стили", "color": LIME, "image": pics["styles"], "image_ratio": 330 / 900,
         "width": 420, "desc": "«дудл» · «3D» · «объём» — стили по твоим референсам"}], "ephemeral": True}, sound="pop")
    v.wait(3.0)

    kit.outro(v, "Максимум с /logo", [
        "Иконка для любой роли за минуту — словами",
        "Перерисовка твоих набросков в едином стиле",
        "Варианты, фон, и сразу на роль — одной кнопкой",
        "Свои стили по референсам: весь сервер в одном визуале",
    ])
    return v


if __name__ == "__main__":
    v = build()
    print(f"длина: {v.duration():.1f} с")
    out = engine.ROOT / "promo" / "logo.mp4"
    if len(sys.argv) > 1 and sys.argv[1] == "frames":
        times = [float(x) for x in sys.argv[2:]]
        for t, im in zip(sorted(times), engine.build(v, out, preview_frames=times)):
            im.convert("RGB").save(config.CACHE / f"logo_{t:05.1f}.png")
    else:
        engine.build(v, out)
