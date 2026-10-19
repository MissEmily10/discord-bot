"""Ролик /library — сохранённые сообщения и шаблоны."""

import sys

import config
import engine
import kit
from kit import btn, u, dd

STEPS = [
    ("Всё в одном месте", "/library: сохранённые сообщения и шаблоны"),
    ("Сообщения", "каждый пост с кнопками и настройками — отправить снова в один клик"),
    ("Где отправлено", "бот помнит все копии: правка обновит их разом"),
    ("Шаблоны", "заготовки: использовать, открыть в редакторе, в избранное"),
    ("Общий доступ", "шаблоны можно открыть ролям — вся команда работает с одним набором"),
]
LIME = "#B6EC34"
PINK = "#E4AEC4"


def build():
    v = kit.video("library", "/library", "сохранённые сообщения и шаблоны", STEPS, channel="общий",
                  channels=["общий", "новости", "правила", "ивенты"])
    v.tempo = 1.5
    kit.intro(v, "/library", "собрал один раз — пользуешься сколько угодно")

    v.step(0)
    kit.slash(v, "/library", "Сохранённые сообщения и шаблоны",
              others=[("/embed", "Создать Message Build"), ("/interactive", "Кнопки, меню ролей и формы")])
    v.bot_typing(0.3)
    embeds, rows = kit.panel(
        "БИБЛИОТЕКА", "Всё сохранённое: готовые сообщения (отправить снова, править, расписание) и шаблоны для новых.",
        "panel_art/thumbnail.library.png",
        [btn("l:msgs", "Сообщения", u("folder"), 1), btn("l:tpls", "Шаблоны", u("library"))],
        banner="panel_art/library.home.banner.gif")
    pid = v.message({"kind": "bot", "reply": "/library", "embeds": embeds, "rows": rows, "ephemeral": True})
    v.wait(2.0)

    v.step(1)
    v.click("l:msgs")
    v.edit(pid, lambda m: m.update(embeds=[{"title": "МОИ СООБЩЕНИЯ", "color": LIME, "thumb": "panel_art/thumbnail.library.png",
                                            "desc": "▸ **Приветствие** — 3 копии в каналах\n"
                                                    "▸ **Набор в команду** — по расписанию, пт 18:00\n"
                                                    "▸ **Голосование** — счётчик голосов\n"
                                                    "▸ **Правила** — закреплено в #правила"}],
                                   rows=[[{"type": "select", "id": "l:open", "placeholder": "Открыть сообщение…"}]]),
           sound="pop")
    v.wait(0.8)
    kit.pick(v, "l:open", [{"label": "Приветствие", "emoji": dd("sparkles")}, {"label": "Набор в команду", "emoji": dd("crown")},
                           {"label": "Голосование", "emoji": dd("trophy")}, {"label": "Правила", "emoji": dd("book")}], 0)
    welcome = {"title": "Добро пожаловать в eminemovna :d3_sparkles:", "color": PINK, "thumb": "panel_art/thumbnail.default.png",
               "desc": "Нас уже **1 251**, в сети **327** :d3_heart:\nЗагляни в #правила и выбери роли."}
    final = [[btn("f:send", "Отправить", u("send"), 3), btn("f:edit", "Редактировать", u("edit")),
              btn("f:resync", "Обновить отправленные", u("refresh"), 1)],
             [btn("f:sched", "Расписание", u("time")), btn("f:hist", "История", u("list")),
              btn("f:json", "Экспорт JSON", u("box")), btn("f:tpl", "Шаблон", u("save"))]]
    v.edit(pid, lambda m: m.update(embeds=[dict(welcome)], rows=final), sound="pop")
    v.wait(1.2)
    v.click("f:send")
    v.message({"kind": "bot", "channel": "новости", "embeds": [dict(welcome)]}, sound=None)
    v.edit(pid, lambda m: m["embeds"].append(kit.toast("Отправлено в #новости")), sound="success")
    v.wait(1.0)

    v.step(2)
    v.edit(pid, lambda m: m.update(embeds=[dict(welcome), {"title": "Где отправлено", "color": LIME, "width": 380,
                                                           "desc": "#общий · #правила · форум «вопросы» · #новости"}]))
    v.wait(1.0)
    v.click("f:edit")
    kit.modal(v, "Основное", [{"id": "title", "label": "Заголовок", "placeholder": ""},
                              {"id": "desc", "label": "Описание", "placeholder": "", "tall": True}])
    v.do(lambda s: s["modal"]["fields"][0].update(value="Добро пожаловать в eminemovna :d3_sparkles:"))
    kit.fill(v, {"desc": "Нас уже **1 251**! В пятницу — большой ивент :d3_balloon:"}, speed=1.8)
    kit.close_modal(v)
    new_desc = "Нас уже **1 251**! В пятницу — большой ивент :d3_balloon:"
    v.edit(pid, lambda m: m["embeds"][0].update(desc=new_desc), sound="pop")
    v.click("f:resync")
    v.edit(pid, lambda m: m["embeds"].append(kit.toast("Обновлено сообщений: 4 — во всех каналах")), sound="success")
    v.click("ch:новости")
    v.do(lambda s: s.update(channel="новости"))
    v.do(lambda s: [m["embeds"][0].update(desc=new_desc) for m in s["messages"] if m.get("channel") == "новости"])
    v.sound("pop")
    v.wait(1.6)

    v.step(3)
    v.click("ch:общий")
    v.do(lambda s: s.update(channel="общий"))
    v.edit(pid, lambda m: m.update(embeds=[{"title": "БИБЛИОТЕКА", "color": LIME, "thumb": "panel_art/thumbnail.library.png",
                                            "desc": "Сообщения и шаблоны."}],
                                   rows=[[btn("l:msgs2", "Сообщения", u("folder")), btn("l:tpls2", "Шаблоны", u("library"), 1)]]))
    v.wait(0.3)
    v.click("l:tpls2")
    v.edit(pid, lambda m: m.update(embeds=[{"title": "ШАБЛОНЫ", "color": LIME, "thumb": "panel_art/thumbnail.templates.png",
                                            "desc": "Доступные тебе заготовки."}],
                                   rows=[[{"type": "select", "id": "t:open", "placeholder": "Открыть шаблон…"}]]), sound="pop")
    kit.pick(v, "t:open", [{"label": "Анонс ивента", "emoji": dd("balloon")}, {"label": "Итоги недели", "emoji": dd("trophy")},
                           {"label": "Пост-набор", "emoji": dd("crown")}], 0)
    v.edit(pid, lambda m: m.update(embeds=[{"title": "ШАБЛОН · Анонс ивента", "color": LIME, "thumb": "panel_art/thumbnail.templates.png",
                                            "desc": "Тип: **сообщение** · Категория: **ивенты**\nВидимость: **@ивентеры, @модераторы**"}],
                                   rows=[[btn("t:use", "Использовать", u("send"), 3), btn("t:edit", "В редактор", u("edit")),
                                          btn("t:fav", "Избранное", u("star"))]]), sound="pop")
    v.wait(0.8)
    v.click("t:fav")
    v.edit(pid, lambda m: m["embeds"].append(kit.toast("Добавлено в избранное — только для тебя")), sound="success")
    v.wait(0.6)
    v.click("t:use")
    v.message({"kind": "bot", "embeds": [{"title": "Ивент «Мафия» :d3_dice:", "color": "#C0D8E8",
                                          "desc": "Пятница, 19:00 · #ивенты\nЗапись — кнопкой ниже!",
                                          "thumb": "panel_art/thumbnail.interactive.png"}],
               "rows": [[btn("ev:go", "Я иду", u("confirm"), 3)]]}, sound="pop")
    v.wait(1.4)

    v.step(4)
    v.wait(2.6)

    kit.outro(v, "Максимум с /library", [
        "Все посты сервера в одном списке — с кнопками и настройками",
        "Отправить снова, по расписанию или в новые каналы",
        "Правка один раз — обновляются все отправленные копии",
        "Шаблоны для команды: по ролям, с избранным",
    ])
    return v


if __name__ == "__main__":
    v = build()
    print(f"длина: {v.duration():.1f} с")
    out = engine.ROOT / "promo" / "library.mp4"
    if len(sys.argv) > 1 and sys.argv[1] == "frames":
        times = [float(x) for x in sys.argv[2:]]
        for t, im in zip(sorted(times), engine.build(v, out, preview_frames=times)):
            im.convert("RGB").save(config.CACHE / f"lib_{t:05.1f}.png")
    else:
        engine.build(v, out)
