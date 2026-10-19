"""Ролик /webhook — вебхуки: своё имя и аватарка, ссылка для сервисов, управление."""

import sys

import config
import engine
import kit
from kit import btn, u, dd

STEPS = [
    ("Одной командой", "/webhook канал, имя и аватарка — вебхук готов"),
    ("Секретная ссылка", "только тебе: для сайта, GitHub, Twitch и других сервисов"),
    ("Пишет от своего имени", "сообщения приходят со своим именем и аватаркой"),
    ("Список", "/webhook без параметров — все вебхуки сервера"),
    ("Управление", "отправить, переименовать, сменить аватар, тест, история"),
    ("Безопасность", "ссылка утекла — «Пересоздать URL», старая перестанет работать"),
]
LIME = "#B6EC34"


def build():
    v = kit.video("webhooks", "/webhook", "сообщения от любого имени", STEPS, channel="общий",
                  channels=["общий", "новости", "стримы"])
    v.tempo = 1.45
    kit.intro(v, "/webhook", "свой «бот» для новостей, сайта и стримов — за секунду")
    news = config.webhook_author("Новости сервера", "panel_art/thumbnail.webhooks.png")

    v.step(0)
    v.click("input", move=0.6, after=0.15)
    v.type_input("/webhook channel: #новости name: Новости сервера avatar: news.png", 2.2)
    v.sound("send")
    v.do(lambda s: s.update(input=""))
    v.bot_typing(0.4)
    v.message({"kind": "bot", "reply": "/webhook", "ephemeral": True,
               "text": ":u3_send: Вебхук **Новости сервера** создан в #новости.\n\n"
                       "Ссылка (держи в секрете — кто её знает, пишет от этого имени):\n"
                       "`https://discord.com/api/webhooks/1290…/████████████`"}, sound="success")
    v.wait(1.4)

    v.step(1)
    v.wait(2.4)

    v.step(2)
    v.message({"kind": "user", "author": news, "channel": "новости",
               "embeds": [{"title": "Вышло обновление 2.0 :d3_rocket:", "color": LIME,
                           "desc": "Новые ивенты, магазин ролей и живые рейтинги. Подробности — на сайте.",
                           "thumb": "panel_art/thumbnail.webhooks.png"}]}, sound=None)
    v.click("ch:новости")
    v.do(lambda s: s.update(channel="новости"))
    v.sound("notify")
    v.wait(1.2)
    v.message({"kind": "user", "author": config.webhook_author("Twitch · эфир", "panel_art/thumbnail.interactive.png"),
               "channel": "новости", "embeds": [{"title": "Стрим уже идёт! :d3_camera:", "color": "#9146FF",
                                                 "desc": "Рисуем эмодзи для сервера — залетай!"}]}, sound="notify")
    v.wait(1.8)

    v.step(3)
    v.click("ch:общий")
    v.do(lambda s: s.update(channel="общий"))
    kit.slash(v, "/webhook", "Создать вебхук и получить его ссылку (или открыть список)")
    v.bot_typing(0.3)
    embeds, rows = kit.panel(
        "WEBHOOK MANAGER", "Вебхуки — сообщения от имени «другого бота» со своим именем и аватаркой.",
        "panel_art/thumbnail.webhooks.png",
        [btn("w:create", "Создать", u("add"), 1), btn("w:list", "Список", u("list")), btn("w:hist", "История", u("time"))],
        banner="panel_art/webhooks.home.banner.gif")
    pid = v.message({"kind": "bot", "reply": "/webhook", "embeds": embeds, "rows": rows, "ephemeral": True})
    v.wait(1.2)
    v.click("w:list")
    v.edit(pid, lambda m: m.update(embeds=[{"title": "WEBHOOKS", "color": LIME, "thumb": "panel_art/thumbnail.webhooks.png",
                                            "desc": "Сохранённые вебхуки. Ссылки скрыты."}],
                                   rows=[[{"type": "select", "id": "w:pick", "placeholder": "Открыть вебхук…"}]]), sound="pop")
    kit.pick(v, "w:pick", [{"label": "Новости сервера · #новости", "emoji": u("announce")},
                           {"label": "Twitch · эфир · #новости", "emoji": dd("camera")},
                           {"label": "Отдел кадров · #заявки", "emoji": u("people")}], 0)

    v.step(4)
    card_rows = [[btn("c:send", "Отправить", u("send"), 3), btn("c:show", "Показать URL", u("preview")),
                  btn("c:rename", "Переименовать", u("edit")), btn("c:avatar", "Аватар", u("image"))],
                 [btn("c:test", "Тест", u("play")), btn("c:hist", "История", u("time")),
                  btn("c:regen", "Пересоздать URL", u("refresh"), 4), btn("c:del", "Удалить", u("delete"), 4)]]
    v.edit(pid, lambda m: m.update(embeds=[{"title": "WEBHOOK", "color": LIME, "thumb": "panel_art/thumbnail.webhooks.png",
                                            "desc": "Имя: **Новости сервера**\nКанал: #новости\nURL скрыт :u3_lock:"}],
                                   rows=card_rows), sound="pop")
    v.wait(1.0)
    v.click("c:send")
    kit.modal(v, "Сообщение вебхука", [{"id": "title", "label": "Embed title", "placeholder": ""},
                                       {"id": "desc", "label": "Embed description", "placeholder": "", "tall": True}])
    kit.fill(v, {"title": "Ивент в пятницу :d3_balloon:", "desc": "Мафия в 19:00 — записывайся в #ивенты!"}, speed=1.8)
    kit.close_modal(v)
    v.message({"kind": "user", "author": news, "channel": "новости",
               "embeds": [{"title": "Ивент в пятницу :d3_balloon:", "color": LIME,
                           "desc": "Мафия в 19:00 — записывайся в #ивенты!"}]}, sound=None)
    v.edit(pid, lambda m: m["embeds"].append(kit.toast("Отправлено через вебхук")), sound="success")
    v.wait(0.8)
    v.click("c:rename")
    kit.modal(v, "Переименовать вебхук", [{"id": "name", "label": "Новое имя", "placeholder": ""}])
    kit.fill(v, {"name": "Новости eminemovna"}, speed=2)
    kit.close_modal(v)
    v.edit(pid, lambda m: m.update(embeds=[m["embeds"][0], kit.toast("Имя изменено")]), sound="success")
    v.wait(0.8)

    v.step(5)
    v.click("c:regen")
    v.edit(pid, lambda m: m.update(embeds=[m["embeds"][0], kit.toast("URL пересоздан. Старый больше не работает.")]),
           sound="success")
    v.wait(2.0)

    kit.outro(v, "Максимум с /webhook", [
        "Сколько угодно «ботов» со своими именами и аватарками",
        "Ссылка для сайта, GitHub, Twitch и любых сервисов",
        "Отправка, переименование, аватар и история в одной карточке",
        "Пересоздание ссылки, если она утекла",
    ])
    return v


if __name__ == "__main__":
    v = build()
    print(f"длина: {v.duration():.1f} с")
    out = engine.ROOT / "promo" / "webhook.mp4"
    if len(sys.argv) > 1 and sys.argv[1] == "frames":
        times = [float(x) for x in sys.argv[2:]]
        for t, im in zip(sorted(times), engine.build(v, out, preview_frames=times)):
            im.convert("RGB").save(config.CACHE / f"hook_{t:05.1f}.png")
    else:
        engine.build(v, out)
