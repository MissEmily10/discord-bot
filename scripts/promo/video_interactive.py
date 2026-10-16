"""Ролик /interactive — кнопки, меню ролей, формы, паки форм, живые картинки."""

import sys

from PIL import Image, ImageDraw

import config
import engine
import kit
from kit import btn, u, dd

STEPS = [
    ("Пять инструментов", "наборы кнопок, меню ролей, формы, паки форм и живые картинки"),
    ("Меню ролей", "участники сами выбирают роли из списка — до 25 ролей"),
    ("Формы-заявки", "анкета, кто проверяет и какую роль выдать после одобрения"),
    ("Паки форм", "все формы в одном меню: выбрал — видит описание и «Подать заявку»"),
    ("Живые картинки", "рейтинг-картинка: поменял очки — бот перерисовал её в канале"),
]
LIME = "#B6EC34"


def public_card(name, tagline="набор! набор! набор!"):
    """Карточка формы как у бота (banner_render: шаблон + название строчными)."""
    sys.path.insert(0, str(engine.ROOT))
    import banner_render as br
    base = Image.open(engine.ROOT / "panel_art" / "forms.public_card.base.png").convert("RGBA")
    d = ImageDraw.Draw(base)
    for spec in br.TEMPLATES["forms.public_card"]:
        text = spec["text"].format(tagline=tagline, name=name)
        if spec.get("lower"):
            text = text.lower()
        lines, font = br.fit_text(spec, text)
        step = font.size * 0.8
        center = spec.get("two_line_y", spec["y"]) if len(lines) > 1 else spec["y"]
        top = center - step * (len(lines) - 1) / 2
        for n, line in enumerate(lines):
            d.text((spec["x"], top + n * step), line, font=font, fill=spec["color"], anchor="lm")
    config.CACHE.mkdir(exist_ok=True)
    path = config.CACHE / f"card_{name}.png"
    base.save(path)
    return str(path.relative_to(engine.ROOT)), base.height / base.width


def rating(rows, name):
    sys.path.insert(0, str(engine.ROOT))
    import cards
    png = cards.render_rating("топ ивентеров", rows)
    config.CACHE.mkdir(exist_ok=True)
    path = config.CACHE / f"rating_{name}.png"
    path.write_bytes(png)
    im = Image.open(path)
    return str(path.relative_to(engine.ROOT)), im.height / im.width


def build():
    v = kit.video("interactive", "/interactive", "всё, что нажимают участники", STEPS, channel="общий",
                  channels=["общий", "роли", "заявки", "ивенты"])
    v.tempo = 1.25
    kit.intro(v, "/interactive", "кнопки, роли, анкеты и картинки, которые живут сами")
    lisa = config.member("Лиса", "#F0B232", (210, 140, 60))

    v.step(0)
    kit.slash(v, "/interactive", "Кнопки, меню ролей и формы-заявки",
              others=[("/embed", "Создать Message Build"), ("/forms", "Проверка заявок")])
    v.bot_typing(0.3)
    embeds, rows = kit.panel(
        "ИНТЕРАКТИВ", "Всё, что нажимают участники. Готовое можно опубликовать отдельно или прикрепить к сообщению в /embed.",
        "panel_art/thumbnail.interactive.png",
        [btn("h:sets", "Наборы кнопок", u("button")), btn("h:menus", "Меню ролей", u("roles"), 1),
         btn("h:forms", "Формы", u("text")), btn("h:packs", "Паки форм", dd("book")), btn("h:cards", "Живые картинки", dd("trophy"))],
        banner="panel_art/interactive.home.banner.gif")
    pid = v.message({"kind": "bot", "reply": "/interactive", "embeds": embeds, "rows": rows, "ephemeral": True})
    v.wait(2.4)

    # 2. меню ролей
    v.step(1)
    v.click("h:menus")
    v.edit(pid, lambda m: m.update(embeds=[{"title": "НОВОЕ МЕНЮ РОЛЕЙ", "color": LIME, "thumb": "panel_art/thumbnail.select.png",
                                            "desc": "Выбери до 25 ролей, которые участники смогут брать себе сами."}],
                                   rows=[[{"type": "select", "id": "m:roles", "placeholder": "Роли для меню"}]]), sound="pop")
    kit.pick(v, "m:roles", [{"label": "@розовый", "emoji": dd("heart")}, {"label": "@лаймовый", "emoji": dd("clover")},
                            {"label": "@голубой", "emoji": dd("cloud")}, {"label": "@ночной", "emoji": dd("moon")}], 3)
    kit.modal(v, "Меню ролей", [{"id": "name", "label": "Название меню", "placeholder": ""},
                                {"id": "ph", "label": "Подсказка в списке", "placeholder": ""},
                                {"id": "max", "label": "Сколько ролей можно выбрать", "placeholder": "1"}])
    kit.fill(v, {"name": "Цвет ника", "ph": "Выбери цвет :u3_palette:", "max": "1"}, speed=2)
    kit.close_modal(v)
    v.edit(pid, lambda m: m.update(embeds=[{"title": "МЕНЮ РОЛЕЙ", "color": LIME, "thumb": "panel_art/thumbnail.select.png",
                                            "desc": "Роли: @розовый, @лаймовый, @голубой, @ночной\nМожно выбрать: **1**"}],
                                   rows=[[btn("m:pub", "Опубликовать", u("send"), 3), btn("m:del", "Удалить", u("delete"), 4)]]),
           sound="pop")
    v.click("m:pub")
    v.edit(pid, lambda m: m["embeds"].append(kit.toast("Опубликовано в #роли")), sound="success")
    v.message({"kind": "bot", "channel": "роли", "embeds": [
        {"banner": "panel_art/select.public_menu.banner.gif"},
        {"title": "Цвет ника", "desc": "Выбери цвет — роль выдастся сразу. Передумаешь — выбери другой.", "color": LIME}],
        "rows": [[{"type": "select", "id": "pub:color", "placeholder": "Выбери цвет"}]]}, sound=None)
    v.click("ch:роли")
    v.do(lambda s: s.update(channel="роли"))
    v.wait(0.8)
    v.do(lambda s: s.update(cursor_label="Лиса · участница"))
    kit.pick(v, "pub:color", [{"label": "Розовый", "emoji": dd("heart")}, {"label": "Лаймовый", "emoji": dd("clover")},
                              {"label": "Голубой", "emoji": dd("cloud")}, {"label": "Ночной", "emoji": dd("moon")}], 0)
    v.message({"kind": "bot", "channel": "роли", "embeds": [kit.toast("Роль **@розовый** выдана")], "ephemeral": True},
              sound="success")
    v.wait(0.6)
    pink_lisa = dict(lisa, color="#F28DB2")
    v.message({"kind": "user", "author": pink_lisa, "channel": "роли", "text": "ура, теперь я розовая :smile_love:"})
    v.wait(1.2)
    v.do(lambda s: s.update(cursor_label=None))

    # 3. формы
    v.step(2)
    v.click("ch:общий")
    v.do(lambda s: s.update(channel="общий"))
    v.edit(pid, lambda m: m.update(embeds=[{"title": "FORM BUILDER", "color": LIME, "thumb": "panel_art/thumbnail.forms.png",
                                            "desc": "Анкета, кто проверяет и что сделать после решения."}],
                                   rows=[[btn("f:q", "Вопросы", u("edit"), 1), btn("f:rev", "Кто проверяет", u("people")),
                                          btn("f:role", "Роль заявки и выдачи", u("roles")), btn("f:pub", "Опубликовать", u("send"), 3)]]),
           sound="pop")
    v.click("f:q")
    kit.modal(v, "Форма", [{"id": "name", "label": "Название", "placeholder": ""},
                           {"id": "q1", "label": "Вопрос 1", "placeholder": ""},
                           {"id": "q2", "label": "Вопрос 2", "placeholder": ""}])
    kit.fill(v, {"name": "Ивентер", "q1": "Какие ивенты хочешь проводить?", "q2": "Сколько времени готов уделять?"},
             speed=2.2)
    kit.close_modal(v)
    v.click("f:rev")
    v.edit(pid, lambda m: m["embeds"].append(kit.toast("Проверяют: @админы, @старшие модеры")), sound="success")
    v.wait(0.5)
    v.click("f:role")
    v.edit(pid, lambda m: m["embeds"].append(kit.toast("После одобрения выдать @ивентер")), sound="success")
    v.wait(0.6)
    v.click("f:pub")
    card, ratio = public_card("Ивентер")
    v.message({"kind": "bot", "channel": "заявки", "embeds": [
        {"image": card, "image_ratio": ratio, "color": LIME},
        {"title": "Ивентер", "desc": "Придумывай и проводи ивенты для сервера :d3_balloon:", "color": LIME}],
        "rows": [[btn("pc:apply", "Подать заявку", u("text"), 1)]]}, sound=None)
    v.click("ch:заявки")
    v.do(lambda s: s.update(channel="заявки"))
    v.sound("notify")
    v.wait(1.6)

    # 4. паки форм
    v.step(3)
    pack_opts = [{"label": "Модератор", "desc": "следить за порядком в чате", "emoji": dd("shield")},
                 {"label": "Ивентер", "desc": "проводить ивенты", "emoji": dd("balloon")},
                 {"label": "Дизайнер", "desc": "баннеры и эмодзи сервера", "emoji": dd("palette")}]
    v.message({"kind": "bot", "channel": "заявки", "embeds": [
        {"title": "Служебные роли :d3_crown:", "color": LIME, "thumb": "panel_art/thumbnail.forms.png",
         "desc": "Выбери роль в меню — увидишь описание и сможешь подать заявку."}],
        "rows": [[{"type": "select", "id": "pk:pick", "placeholder": "Выбери роль…"}]]}, sound="pop")
    v.wait(1.0)
    v.do(lambda s: s.update(cursor_label="Лиса · участница"))
    kit.pick(v, "pk:pick", pack_opts, 2)
    dcard, dratio = public_card("Дизайнер", tagline="напиши нам! напиши нам!")
    v.message({"kind": "bot", "channel": "заявки", "embeds": [
        {"image": dcard, "image_ratio": dratio, "color": LIME},
        {"title": "Дизайнер", "desc": "Рисуешь баннеры и эмодзи — покажи, что умеешь!", "color": LIME}],
        "rows": [[btn("pk:apply", "Подать заявку", u("text"), 1)]], "ephemeral": True}, sound="pop")
    v.wait(1.0)
    v.click("pk:apply")
    kit.modal(v, "Дизайнер — анкета", [{"id": "port", "label": "Ссылка на работы", "placeholder": ""}])
    kit.fill(v, {"port": "behance.net/lisa_art"}, speed=2)
    kit.close_modal(v)
    v.message({"kind": "bot", "channel": "заявки", "embeds": [kit.toast("Заявка отправлена! Ответ придёт в личку.")],
               "ephemeral": True}, sound="success")
    v.wait(1.0)
    v.do(lambda s: s.update(cursor_label=None))

    # 5. живые картинки
    v.step(4)
    r1, ratio1 = rating([{"name": "Кот", "score": 120}, {"name": "Лиса", "score": 95}, {"name": "Ёжик", "score": 95},
                         {"name": "Сова", "score": 40}], "a")
    r2, ratio2 = rating([{"name": "Лиса", "score": 135}, {"name": "Кот", "score": 120}, {"name": "Ёжик", "score": 95},
                         {"name": "Сова", "score": 40}], "b")
    rid = v.message({"kind": "bot", "channel": "ивенты", "embeds": [{"image": r1, "image_ratio": ratio1, "color": LIME,
                                                                     "width": 360}],
                     "rows": [[btn("rt:edit", "Изменить", u("edit"))]]}, sound=None)
    v.click("ch:ивенты")
    v.do(lambda s: s.update(channel="ивенты"))
    v.wait(1.2)
    v.click("rt:edit")
    kit.modal(v, "Изменить очки", [{"id": "who", "label": "Кто", "placeholder": "имя"},
                                   {"id": "score", "label": "Очки", "placeholder": "42 — поставить, +10 / -5 — изменить"}])
    kit.fill(v, {"who": "Лиса", "score": "+40"}, speed=2)
    kit.close_modal(v)
    v.edit(rid, lambda m: m["embeds"][0].update(image=r2), sound="success")
    v.wait(2.4)

    kit.outro(v, "Максимум с /interactive", [
        "Меню ролей: участники сами берут цвета, пинги и доступы",
        "Анкеты-заявки с проверяющими и выдачей роли после одобрения",
        "Паки форм: десятки ролей в одном аккуратном меню",
        "Живые рейтинги, которые бот перерисовывает сам",
        "Всё можно прикрепить к любому посту в /embed",
    ])
    return v


if __name__ == "__main__":
    v = build()
    print(f"длина: {v.duration():.1f} с")
    out = engine.ROOT / "promo" / "interactive.mp4"
    if len(sys.argv) > 1 and sys.argv[1] == "frames":
        times = [float(x) for x in sys.argv[2:]]
        for t, im in zip(sorted(times), engine.build(v, out, preview_frames=times)):
            im.convert("RGB").save(config.CACHE / f"inter_{t:05.1f}.png")
    else:
        engine.build(v, out)
