"""Ролик /forms — проверка заявок."""

import sys

from PIL import Image, ImageDraw, ImageFont

import config
import engine
import kit
from kit import btn, u, dd

STEPS = [
    ("Новая заявка", "участник заполнил анкету — бот сразу присылает её в канал проверки"),
    ("Все формы", "/forms: формы, которые ты проверяешь, и сколько заявок ждут"),
    ("Ответы", "открываешь заявку — видно, кто подал, когда и что ответил"),
    ("Одобрить", "одна кнопка: роль выдаётся сама, заявителю — баннер в личку"),
    ("Отклонить", "с причиной: человек узнает, что не так"),
    ("Порядок", "статус, кто проверил, ничего не теряется"),
]


def answer_banner(kind, role):
    """Баннер-ответ как у бота (banner_render): фон из Figma + должность по-английски."""
    spec = {
        "approved": ("your {role} form   has  . been", 40, 40),
        "rejected": ("      an answer to your {role} form", -27, 158),
    }[kind]
    base = Image.open(engine.ROOT / "panel_art" / f"forms.{kind}.base.png").convert("RGBA")
    d = ImageDraw.Draw(base)
    f = ImageFont.truetype(str(engine.ROOT / "fonts" / "Inter-SemiBold.otf"), 22)
    d.text((spec[1], spec[2]), spec[0].format(role=role), font=f, fill=(90, 82, 84), anchor="lm")
    config.CACHE.mkdir(exist_ok=True)
    path = config.CACHE / f"forms_{kind}.png"
    base.save(path)
    return str(path.relative_to(engine.ROOT))


def submission(n, who, answers):
    return [{"image": "panel_art/forms.submission.banner.png", "image_ratio": 200 / 1200, "color": "#B6EC34"},
            {"title": "НОВАЯ ЗАЯВКА", "color": "#B6EC34",
             "desc": f"Форма: **Модератор**\nЗаявитель: **@{who}**\nID: `#{n}`",
             "fields": answers, "thumb": "panel_art/thumbnail.forms.png"}]


def build():
    v = kit.video("forms", "/forms", "проверка заявок", STEPS, channel="заявки", channels=channels())
    v.tempo = 1.3
    kit.intro(v, "/forms", "заявки приходят сами — тебе остаётся решить")
    v.wait(0.3)

    answers = [("Сколько тебе лет?", "17"),
               ("Почему хочешь в команду?", "Слежу за чатом каждый день и люблю помогать новичкам")]

    # 1. новая заявка в канале проверки
    v.step(0)
    v.wait(0.4)
    v.message({"kind": "bot", "embeds": submission(12, "Лиса", answers),
               "rows": [[btn("q:ok", "Одобрить", u("confirm"), 3), btn("q:no", "Отклонить", u("cancel"), 4)]]},
              sound="notify")
    v.wait(2.6)

    # 2. /forms
    v.step(1)
    kit.slash(v, "/forms", "Проверка заявок по формам")
    v.bot_typing(0.4)
    embeds, rows = kit.panel("ЗАЯВКИ", "Формы, заявки по которым ты можешь проверять. В скобках — сколько ждут решения.",
                             "panel_art/thumbnail.forms.png", [], banner="panel_art/forms.review_home.banner.gif",
                             extra_rows=[[{"type": "select", "id": "r:form", "placeholder": "Выбери форму"}]])
    pid = v.message({"kind": "bot", "reply": "/forms", "embeds": embeds, "rows": rows, "ephemeral": True})
    v.wait(1.4)
    kit.pick(v, "r:form", [{"label": "Модератор (3)", "emoji": dd("shield")},
                           {"label": "Ивентер (1)", "emoji": dd("balloon")},
                           {"label": "Дизайнер (0)", "emoji": dd("palette")}], 0)
    lst = {"title": "ЗАЯВКИ: Модератор", "color": "#B6EC34", "thumb": "panel_art/thumbnail.forms.png",
           "desc": "Ждут решения (новые сверху):\n`#12` Лиса · 2 мин назад\n`#11` Кот · час назад\n`#9` Ёжик · вчера"}
    v.edit(pid, lambda m: m.update(embeds=[dict(lst)], rows=[[{"type": "select", "id": "r:open",
                                                                "placeholder": "Открыть заявку"}]]), sound="pop")
    v.wait(1.6)

    # 3. ответы
    v.step(2)
    kit.pick(v, "r:open", [{"label": "#12 · Лиса"}, {"label": "#11 · Кот"}, {"label": "#9 · Ёжик"}], 0)
    card = {"title": "ЗАЯВКА #12", "color": "#B6EC34", "thumb": "panel_art/thumbnail.forms.png",
            "desc": "Форма: **Модератор** · Заявитель: **@Лиса** · 2 мин назад", "fields": answers}
    v.edit(pid, lambda m: m.update(embeds=[dict(card)], rows=[[btn("a:ok", "Одобрить", u("confirm"), 3),
                                                                btn("a:no", "Отклонить", u("cancel"), 4),
                                                                btn("a:back", "Назад", u("back"))]]), sound="pop")
    v.wait(2.4)

    # 4. одобрить
    v.step(3)
    v.click("a:ok")
    done = {"title": "ЗАЯВКА ОБРАБОТАНА", "color": "#248046", "thumb": "panel_art/thumbnail.forms.png",
            "desc": f"Статус: **одобрена**\nReviewer: **@{config.USER_NAME}**"}
    v.edit(pid, lambda m: m.update(embeds=[dict(done), kit.toast("Роль **@модератор** выдана Лисе")], rows=[]),
           sound="success")
    v.wait(1.5)
    approved = answer_banner("approved", "moderator")
    v.message({"kind": "bot", "channel": "лс-лисы", "embeds": [
        {"image": approved, "image_ratio": 200 / 1200, "color": "#B6EC34"},
        {"title": "Заявка одобрена!", "desc": "Форма **Модератор** на сервере **eminemovna**. Добро пожаловать в команду!",
         "color": "#248046"}]}, sound=None)
    v.click("ch:лс-лисы")
    v.do(lambda s: s.update(channel="лс-лисы"))
    v.sound("notify")
    v.wait(3.0)

    # 5. отклонить
    v.step(4)
    v.click("ch:заявки")
    v.do(lambda s: s.update(channel="заявки"))
    v.edit(pid, lambda m: m.update(embeds=[dict(lst, desc="Ждут решения (новые сверху):\n`#11` Кот · час назад\n`#9` Ёжик · вчера")],
                                   rows=[[{"type": "select", "id": "r:open2", "placeholder": "Открыть заявку"}]]))
    v.wait(0.4)
    kit.pick(v, "r:open2", [{"label": "#11 · Кот"}, {"label": "#9 · Ёжик"}], 0)
    card2 = dict(card, title="ЗАЯВКА #11", desc="Форма: **Модератор** · Заявитель: **@Кот** · час назад",
                 fields=[("Сколько тебе лет?", "13"), ("Почему хочешь в команду?", "хочу банить всех")])
    v.edit(pid, lambda m: m.update(embeds=[card2], rows=[[btn("b:ok", "Одобрить", u("confirm"), 3),
                                                          btn("b:no", "Отклонить", u("cancel"), 4)]]), sound="pop")
    v.wait(1.4)
    v.click("b:no")
    kit.modal(v, "Причина отказа", [{"id": "why", "label": "Причина (увидит заявитель)", "placeholder": "необязательно",
                                    "tall": True}])
    kit.fill(v, {"why": "Пока маловато опыта — попробуй через пару месяцев :smile_wink:"}, speed=1.3)
    kit.close_modal(v)
    v.edit(pid, lambda m: m.update(embeds=[dict(done, color="#DA373C",
                                                 desc=f"Статус: **отклонена**\nReviewer: **@{config.USER_NAME}**\n"
                                                      "Причина: Пока маловато опыта — попробуй через пару месяцев")],
                                   rows=[]), sound="success")
    v.wait(1.6)
    rejected = answer_banner("rejected", "moderator")
    v.message({"kind": "bot", "channel": "лс-кота", "embeds": [
        {"image": rejected, "image_ratio": 200 / 1200, "color": "#DA373C"},
        {"title": "Заявка отклонена", "desc": "Причина: Пока маловато опыта — попробуй через пару месяцев :smile_wink:",
         "color": "#DA373C"}]}, sound=None)
    v.click("ch:лс-кота")
    v.do(lambda s: s.update(channel="лс-кота"))
    v.sound("notify")
    v.wait(3.0)

    # 6. порядок
    v.step(5)
    v.click("ch:заявки")
    v.do(lambda s: s.update(channel="заявки"))
    v.edit(pid, lambda m: m.update(embeds=[dict(lst, desc="Ждут решения (новые сверху):\n`#9` Ёжик · вчера\n\n"
                                                         "Обработано сегодня: **2** · одобрено 1, отклонено 1")],
                                   rows=[[{"type": "select", "id": "r:open3", "placeholder": "Открыть заявку"}]]),
           sound="pop")
    v.wait(2.6)

    kit.outro(v, "Максимум с /forms", [
        "Анкеты-заявки на любые роли, приватно для каждого",
        "Проверяющие по ролям — каждый видит только свои формы",
        "Одобрил — роль выдаётся сама, без ручной возни",
        "Ответ в личку с баннером и названием должности",
        "Причина отказа, статус и проверяющий у каждой заявки",
    ])
    return v


def channels():
    return ["общий", "анонсы", "заявки", "лс-лисы", "лс-кота"]


if __name__ == "__main__":
    v = build()
    print(f"длина: {v.duration():.1f} с")
    out = engine.ROOT / "promo" / "forms.mp4"
    if len(sys.argv) > 1 and sys.argv[1] == "frames":
        times = [float(x) for x in sys.argv[2:]]
        for t, im in zip(sorted(times), engine.build(v, out, preview_frames=times)):
            im.convert("RGB").save(config.CACHE / f"forms_{t:05.1f}.png")
    else:
        engine.build(v, out)
