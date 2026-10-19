"""Ролик /pictures — живые картинки, своя команда, баннер сервера, библиотека картинок.

Картинки — настоящим кодом бота: scripts/promo/make_pictures_assets.py (из ветки main).
"""

import sys

from PIL import Image

import config
import engine
import kit
from kit import btn, u, dd

STEPS = [
    ("Всё, что рисует бот", "/pictures: живые картинки, баннер сервера и библиотека картинок"),
    ("21 шаблон", "рейтинг, прогресс, отсчёт, голосование, календарь, прайс, диаграмма…"),
    ("Своя картинка", "выбрал шаблон, вписал поля, выбрал одну из 7 тем"),
    ("Кнопка «Изменить»", "меняют ты, админы и те, кого ты выберешь"),
    ("Своя команда", "/казна значение:+60 — картинка в канале перерисуется сама"),
    ("Живут сами", "отсчёт, календарь и цифры сервера бот обновляет сам"),
    ("Баннер сервера", "20 стилей и живые цифры: участники, войс, бусты"),
    ("Библиотека", "все картинки бота и свои — забрать файлом или поделиться"),
]
LIME = "#B6EC34"
A = "scripts/promo/assets/pictures/"


def pic(name):
    path = A + name + ".png"
    im = Image.open(engine.ROOT / path)
    return path, im.height / im.width


def build():
    v = kit.video("pictures", "/pictures", "картинки, которые живут сами", STEPS, channel="общий",
                  channels=["общий", "клан", "ивенты"])
    v.thumb = "scripts/promo/assets/thumb_pictures.png"
    v.tempo = 1.2
    kit.intro(v, "/pictures", "рейтинги, отсчёты, голосования и баннер — бот рисует и обновляет сам")

    # 1. панель
    v.step(0)
    kit.slash(v, "/pictures", "Живые картинки, баннер сервера и библиотека картинок",
              others=[("/interactive", "Кнопки, меню ролей и формы"), ("/library", "Сохранённые сообщения и шаблоны")])
    v.bot_typing(0.3)
    embeds, rows = kit.panel(
        "КАРТИНКИ", "Всё, что бот рисует сам: живые картинки (21 шаблон), баннер сервера с живыми цифрами "
                    "и библиотека готовых картинок.", v.thumb,
        [btn("p:live", "Живые картинки", dd("trophy"), 1), btn("p:sheet", "Все шаблоны", u("image")),
         btn("p:banner", "Баннер сервера", dd("sparkles")), btn("p:lib", "Библиотека картинок", u("folder")),
         btn("p:vars", "Переменные", u("list"))])
    pid = v.message({"kind": "bot", "reply": "/pictures", "embeds": embeds, "rows": rows, "ephemeral": True})
    v.wait(1.8)

    # 2. все шаблоны
    v.step(1)
    v.click("p:sheet")
    sheet, ratio = pic("templates_sheet")
    sid = v.message({"kind": "bot", "text": "Все шаблоны живых картинок с примерами.",
                     "embeds": [{"image": sheet, "image_ratio": ratio, "color": LIME, "width": 520}],
                     "ephemeral": True})
    v.wait(3.2)
    v.remove(sid)

    # 3. своя картинка
    v.step(2)
    v.click("p:live")
    v.edit(pid, lambda m: m.update(embeds=[{"title": "ЖИВЫЕ КАРТИНКИ", "color": LIME, "thumb": v.thumb,
                                            "desc": "Картинка в канале, которую бот перерисовывает, когда меняются значения."}],
                                   rows=[[btn("l:new", "Новая картинка", u("add"), 1)],
                                         [{"type": "select", "id": "l:tpl", "placeholder": "Какая картинка?"}]]), sound="pop")
    v.click("l:new")
    kit.pick(v, "l:tpl", [{"label": "Рейтинг", "desc": "место — имя — очки", "emoji": dd("trophy")},
                          {"label": "Прогресс", "desc": "шкала: 340 / 500 золота", "emoji": dd("hourglass")},
                          {"label": "Обратный отсчёт", "desc": "до ивента 2 д 5 ч", "emoji": u("time")},
                          {"label": "Голосование", "desc": "варианты с полосками", "emoji": dd("star")},
                          {"label": "Календарь", "desc": "месяц с отмеченными днями", "emoji": u("list")}], 1)
    kit.modal(v, "Поля картинки", [{"id": "title", "label": "Название (крупно на картинке)", "placeholder": ""},
                                   {"id": "value", "label": "Сейчас", "placeholder": ""},
                                   {"id": "goal", "label": "Цель", "placeholder": ""},
                                   {"id": "unit", "label": "Единица", "placeholder": ""}])
    kit.fill(v, {"title": "Казна клана", "value": "340", "goal": "500", "unit": "золота"}, speed=2.2)
    kit.close_modal(v)
    p_cream, r = pic("progress_340_cream")
    p_night, _ = pic("progress_340_night")
    editor_rows = [[btn("e:value", "Значение", dd("dice")), btn("e:fields", "Поля", u("edit")),
                    btn("e:theme", "Тема", u("palette")), btn("e:cmd", "Своя команда", u("send"))],
                   [btn("e:access", "Кто меняет", u("people")), btn("e:pub", "Опубликовать", u("send"), 3),
                    btn("e:lib", "В библиотеку", u("folder"))]]
    v.edit(pid, lambda m: m.update(embeds=[{"title": "ПРОГРЕСС · Казна клана", "color": LIME,
                                            "image": p_cream, "image_ratio": r, "width": 440}],
                                   rows=editor_rows), sound="pop")
    v.wait(1.4)
    v.edit(pid, lambda m: m.update(rows=editor_rows + [[{"type": "select", "id": "e:themes", "placeholder": "Цвета картинки…"}]]))
    v.click("e:theme")
    kit.pick(v, "e:themes", [{"label": "Сливочная"}, {"label": "Ночная"}, {"label": "Розовая"}, {"label": "Голубая"},
                             {"label": "Салатовая"}, {"label": "Бордовая"}, {"label": "Чёрно-белая"}], 1)
    v.edit(pid, lambda m: m.update(embeds=[dict(m["embeds"][0], image=p_night)], rows=editor_rows), sound="pop")
    v.wait(1.2)

    # 4. кнопка «Изменить»
    v.step(3)
    v.click("e:access")
    v.edit(pid, lambda m: m.update(embeds=[m["embeds"][0], kit.toast("Меняют: ты, админы и @казначей")]), sound="success")
    v.wait(0.6)
    v.click("e:pub")
    v.edit(pid, lambda m: m.update(embeds=[m["embeds"][0], kit.toast("Опубликовано в #клан")]), sound="success")
    card = v.message({"kind": "bot", "channel": "клан", "embeds": [{"image": p_night, "image_ratio": r, "color": LIME,
                                                                  "width": 440}],
                      "rows": [[btn("c:edit", "Изменить", u("edit"))]]}, sound=None)
    v.click("ch:клан")
    v.do(lambda s: s.update(channel="клан"))
    v.wait(1.0)
    v.do(lambda s: s.update(cursor_label="Кот · казначей"))
    v.click("c:edit")
    kit.modal(v, "Значение", [{"id": "val", "label": "340 — поставить, +50 / -10, цель 500", "placeholder": ""}])
    kit.fill(v, {"val": "+50"}, speed=1.5)
    kit.close_modal(v)
    p390, _ = pic("progress_390_night")
    v.edit(card, lambda m: m["embeds"][0].update(image=p390), sound="success")
    v.wait(1.4)
    v.do(lambda s: s.update(cursor_label=None))

    # 5. своя команда
    v.step(4)
    v.click("ch:общий")
    v.do(lambda s: s.update(channel="общий"))
    v.click("e:cmd")
    kit.modal(v, "Своя команда", [{"id": "name", "label": "Имя команды (без /)", "placeholder": "очки_клана"}])
    kit.fill(v, {"name": "казна"}, speed=1.5)
    kit.close_modal(v)
    v.edit(pid, lambda m: m.update(embeds=[m["embeds"][0], kit.toast("Команда **/казна** готова на этом сервере")]),
           sound="success")
    v.wait(1.0)
    v.click("input", move=0.5, after=0.1)
    v.type_input("/казна значение: +60", 1.0)
    v.sound("send")
    v.do(lambda s: s.update(input=""))
    v.message({"kind": "bot", "reply": "/казна", "embeds": [kit.toast("Готово — картинка в #клан обновлена")],
               "ephemeral": True}, sound="success")
    p450, _ = pic("progress_450_night")
    v.edit(card, lambda m: m["embeds"][0].update(image=p450))
    v.wait(0.6)
    v.click("ch:клан")
    v.do(lambda s: s.update(channel="клан"))
    v.wait(1.6)

    # 6. живут сами
    v.step(5)
    cd, cr = pic("countdown")
    poll0, pr = pic("poll_0")
    poll1, _ = pic("poll_1")
    v.message({"kind": "bot", "channel": "ивенты", "embeds": [{"image": cd, "image_ratio": cr, "color": LIME, "width": 400}]},
              sound=None)
    pl = v.message({"kind": "bot", "channel": "ивенты", "embeds": [{"image": poll0, "image_ratio": pr, "color": LIME,
                                                                  "width": 400}]}, sound=None)
    v.click("ch:ивенты")
    v.do(lambda s: s.update(channel="ивенты"))
    v.wait(1.2)
    v.click("input", move=0.5, after=0.1)
    v.type_input("/голос значение: +1 кто: Мафия", 1.0)
    v.sound("send")
    v.do(lambda s: s.update(input=""))
    v.edit(pl, lambda m: m["embeds"][0].update(image=poll1), sound="success")
    v.wait(1.8)

    # 7. баннер сервера
    v.step(6)
    v.click("ch:общий")
    v.do(lambda s: s.update(channel="общий"))
    v.edit(pid, lambda m: m.update(embeds=[{"title": "БАННЕР СЕРВЕРА", "color": LIME, "thumb": v.thumb,
                                            "desc": "Бот сам ставит баннер сервера и вписывает в него живые цифры.\n"
                                                    "**Строки:** {server} · участников {member_count} · в войсе {voice}"}],
                                   rows=[[btn("b:style", "Стиль", u("palette"), 1), btn("b:lines", "Строки", u("text")),
                                          btn("b:preview", "Предпросмотр", u("preview")),
                                          btn("b:apply", "Поставить сейчас", u("confirm"), 3)],
                                         [{"type": "select", "id": "b:styles", "placeholder": "20 стилей…"}]]), sound="pop")
    kit.pick(v, "b:styles", [{"label": "1. Сливочный"}, {"label": "2. Ночной"}, {"label": "11. Салатовый взрыв"},
                             {"label": "13. Наклейки"}], 2)
    b_lime, br = pic("banner_lime_pop")
    prev = v.message({"kind": "bot", "embeds": [{"image": b_lime, "image_ratio": br, "color": LIME, "width": 420}],
                      "ephemeral": True}, sound="pop")
    v.wait(1.2)
    v.remove(prev)
    kit.pick(v, "b:styles", [{"label": "1. Сливочный"}, {"label": "2. Ночной"}, {"label": "11. Салатовый взрыв"},
                             {"label": "13. Наклейки"}], 3)
    b_st, _ = pic("banner_stickers")
    prev = v.message({"kind": "bot", "embeds": [{"image": b_st, "image_ratio": br, "color": LIME, "width": 420}],
                      "ephemeral": True}, sound="pop")
    v.wait(1.2)
    v.remove(prev)
    v.click("b:apply")
    v.do(lambda s: s.update(server_banner=b_st))
    v.edit(pid, lambda m: m.update(embeds=[m["embeds"][0], kit.toast("Баннер стоит. Цифры обновляются раз в 10 минут")]),
           sound="success")
    v.wait(2.0)

    # 8. библиотека
    v.step(7)
    v.edit(pid, lambda m: m.update(embeds=[{"title": "БИБЛИОТЕКА КАРТИНОК", "color": LIME, "thumb": v.thumb,
                                            "desc": "Твои картинки и те, которыми поделились с сервером. До 200 на человека."}],
                                   rows=[[{"type": "select", "id": "lib:pick", "placeholder": "Открыть картинку…"}]]),
           sound="pop")
    kit.pick(v, "lib:pick", [{"label": "Казна клана", "emoji": dd("hourglass")}, {"label": "Баннер сервера", "emoji": dd("sparkles")},
                             {"label": "Иконка модератора", "emoji": dd("shield")}], 1)
    v.edit(pid, lambda m: m.update(embeds=[{"title": "Баннер сервера", "color": LIME, "image": b_st, "image_ratio": br,
                                            "width": 420}],
                                   rows=[[btn("lib:file", "Прислать файлом", u("box"), 1),
                                          btn("lib:share", "Поделиться с сервером", u("people"))]]), sound="pop")
    v.wait(1.0)
    v.click("lib:file")
    v.message({"kind": "bot", "file": ("server_banner.png", "184 КБ"), "ephemeral": True}, sound="pop")
    v.wait(1.6)

    kit.outro(v, "Максимум с /pictures", [
        "21 шаблон: рейтинги, прогресс, отсчёты, голосования, календари",
        "Меняются кнопкой или своей слэш-командой — сразу в канале",
        "Отсчёты и живые цифры бот перерисовывает сам",
        "Баннер сервера: 20 стилей и цифры, которые обновляются",
        "Библиотека: все картинки под рукой, файлом за секунду",
    ])
    return v


if __name__ == "__main__":
    v = build()
    print(f"длина: {v.duration():.1f} с")
    out = engine.ROOT / "promo" / "pictures.mp4"
    if len(sys.argv) > 1 and sys.argv[1] == "frames":
        times = [float(x) for x in sys.argv[2:]]
        for t, im in zip(sorted(times), engine.build(v, out, preview_frames=times)):
            im.convert("RGB").save(config.CACHE / f"pic_{t:05.1f}.png")
    else:
        engine.build(v, out)
