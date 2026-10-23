"""Ролик /embed — конструктор сообщений: шесть частей, все ключевые и нетипичные функции."""

import sys

import config
import engine
import kit
from kit import btn, u, dd

TOTAL = 6
LIME, PINK, ICE = "#B6EC34", "#E4AEC4", "#C0D8E8"

CH1 = [
    ("Конструктор", "с нуля, из шаблона, из сохранённого или из любого уже отправленного сообщения"),
    ("Текст и цвет", "заголовок, описание, цвет — в окошках, без кода и JSON"),
    ("Картинки и поля", "большая картинка, иконка, автор, подпись, поля «название — значение»"),
    ("Несколько embed'ов", "до 10 карточек в одном сообщении, переключаешься между ними"),
    ("Переменные", "{member_count}, {online}, {user}, {counter:…} — бот подставит живые числа"),
]
CH2 = [
    ("12 действий", "кнопка может: роль, форму, ответ, ссылку, вебхук, embed, шаг, счётчик…"),
    ("Embed на кнопке", "нажал — увидел другое сообщение, только для себя"),
    ("Шаги-страницы", "кнопка «Дальше» заменяет сообщение следующим: гиды, меню, квесты"),
    ("Роли по кнопке", "выдать, снять или переключить роль одним нажатием"),
    ("Списки", "выпадающий список вариантов и выбор роли или участника из Discord"),
]
CH3 = [
    ("Счётчик", "кнопка «+1» меняет число, и все сообщения с ним обновляются сами"),
    ("Живое обновление", "онлайн и участники в посте переписываются каждые N минут"),
    ("Гид и список", "участник листает шаги «Дальше» и выбирает тему из списка"),
    ("Разным ролям — разное", "одна кнопка: участнику — меню, модератору — панель"),
    ("Родитель стиля", "поменял цвет у главного — обновились все «дети»"),
]
CH4 = [
    ("Превью", "настоящая картинка поста — тёмная и светлая тема"),
    ("До 5 мест сразу", "каналы, ветки и форумы (в форуме — новым постом)"),
    ("Через вебхук", "от любого имени с любой аватаркой"),
    ("Видимость", "публичный, приватный или только для выбранных ролей"),
]
CH5 = [
    ("Расписание", "один раз или с повтором: каждый день, неделю, N часов"),
    ("Новый участник", "приветствие с {user} — само, в нужный канал"),
    ("Ключевое слово", "кто-то спросил «как подать заявку?» — бот ответил постом"),
    ("Новая ветка", "ответ в каждую новую ветку или пост форума"),
]
CH6 = [
    ("Из сообщения", "вставь ссылку на любое сообщение — бот соберёт его в конструктор"),
    ("Экспорт JSON", "файл, чтобы перенести пост на другой сервер"),
    ("История версий", "25 последних версий и откат в один клик"),
    ("Обновить отправленные", "поправил один раз — изменилось во всех каналах"),
]

WELCOME_T = "Добро пожаловать в eminemovna :d3_sparkles:"
WELCOME_D = "Нас уже **{member_count}**, в сети **{online}** :d3_heart:\nВыбери, с чего начать:"
WELCOME_LIVE = "Нас уже **1 248**, в сети **312** :d3_heart:\nВыбери, с чего начать:"
ACTIONS = [
    {"label": "Отправить сообщение", "emoji": dd("chat")},
    {"label": "Показать подтверждение", "emoji": u("confirm")},
    {"label": "Открыть форму", "emoji": u("text")},
    {"label": "Выдать / снять роль", "emoji": u("roles")},
    {"label": "Показать связанный embed", "emoji": u("preview")},
    {"label": "Шаг: заменить сообщение", "emoji": u("next")},
    {"label": "Изменить счётчик", "emoji": dd("dice")},
    {"label": "Через вебхук", "emoji": u("send")},
    {"label": "Ссылка", "emoji": u("web")},
]


def welcome_embed(live=False, color=PINK, fields=True):
    e = {"title": WELCOME_T, "desc": WELCOME_LIVE if live else WELCOME_D, "color": color,
         "thumb": "panel_art/thumbnail.default.png", "author": ("eminemovna", config.BOT_AVATAR_STILL),
         "footer": "eminemovna · с нами с 2024"}
    if fields:
        e["fields"] = [("Правила", "коротко и по делу — читай #правила"), ("Ивенты", "каждую пятницу в 19:00")]
    return e


def guide_rows():
    return [[btn("g:rules", "Правила", dd("book"), 1), btn("g:next", "Дальше", u("next"), 2),
             btn("g:role", "Хочу на ивенты", dd("balloon"), 3)],
            [{"type": "select", "id": "g:topic", "placeholder": "Выбери тему…"}]]


def build():
    v = kit.video("embed", "/embed", "конструктор сообщений", CH1, channel="общий",
                  channels=["общий", "правила", "ивенты", "вопросы", "модерация"])
    kit.intro(v, "/embed", "собери пост любой сложности — и пусть он работает сам")

    # ============ 1. ОСНОВА ============
    kit.chapter(v, 1, TOTAL, "Основа", CH1, "panel_art/emoji/ui3d/u3_edit.png")
    v.step(0)
    kit.slash(v, "/embed", "Создать Message Build с embed'ами и интерактивом",
              others=[("/library", "Сохранённые сообщения и шаблоны"), ("/interactive", "Кнопки, меню ролей и формы")])
    v.bot_typing(0.3)
    embeds, rows = kit.panel(
        "MESSAGE BUILD",
        "Конструктор сообщений: собери пост из embed'ов, добавь кнопки, список или форму и отправь в каналы.",
        "panel_art/thumbnail.embed.png",
        [btn("b:create", "Создать новый", u("add"), 1), btn("b:saved", "Мои сохранённые", u("folder")),
         btn("b:tpl", "Шаблон", u("library")), btn("b:from", "Из сообщения", u("text"))],
        banner="panel_art/embed.home.banner.gif")
    pid = v.message({"kind": "bot", "reply": "/embed", "embeds": embeds, "rows": rows, "ephemeral": True})
    v.wait(1.3)
    v.click("b:create")
    editor_rows = [[btn("e:basic", "Основное", u("edit")), btn("e:media", "Изображения", u("image")),
                    btn("e:af", "Author/Footer", u("text")), btn("e:field", "Поле", u("add"))],
                   [{"type": "select", "id": "e:switch", "placeholder": "Embed 1"}],
                   [btn("e:confirm", "Подтвердить дизайн", u("confirm"), 3), btn("e:time", "Время", u("time")),
                    btn("e:preview", "Превью", u("preview"))]]
    v.edit(pid, lambda m: m.update(embeds=[{"title": "Новый embed", "desc": "Нажми «Основное», чтобы задать текст.",
                                            "color": "#5865F2"}], rows=editor_rows), sound="pop")
    v.wait(0.5)

    v.step(1)
    v.click("e:basic")
    kit.modal(v, "Основное", [{"id": "title", "label": "Заголовок", "placeholder": "Заголовок"},
                              {"id": "desc", "label": "Описание", "placeholder": "Текст", "tall": True},
                              {"id": "color", "label": "Цвет (HEX)", "placeholder": "#5865F2"}])
    kit.fill(v, {"title": WELCOME_T, "desc": WELCOME_D, "color": PINK}, speed=1.6)
    kit.close_modal(v)
    v.edit(pid, lambda m, t=v.t: m["embeds"][0].update(title=WELCOME_T, desc=WELCOME_D, color=PINK,
                                                        old_color="#5865F2", t_color=t), sound="pop")
    v.wait(0.6)

    v.step(2)
    v.click("e:media")
    kit.modal(v, "Изображения", [{"id": "image", "label": "Большая картинка", "placeholder": "https://"},
                                 {"id": "thumb", "label": "Иконка справа", "placeholder": "https://"}])
    kit.fill(v, {"image": "https://…/welcome.png", "thumb": "https://…/smile.png"}, speed=2.2)
    kit.close_modal(v)
    v.edit(pid, lambda m: m["embeds"][0].update(thumb="panel_art/thumbnail.default.png",
                                                author=("eminemovna", config.BOT_AVATAR_STILL),
                                                footer="eminemovna · с нами с 2024"), sound="pop")
    v.wait(0.3)
    v.click("e:field")
    kit.modal(v, "Поле", [{"id": "name", "label": "Название", "placeholder": "Название"},
                          {"id": "value", "label": "Значение", "placeholder": "Текст", "tall": True}])
    kit.fill(v, {"name": "Правила", "value": "коротко и по делу — читай #правила"}, speed=2)
    kit.close_modal(v)
    v.edit(pid, lambda m: m["embeds"][0].update(fields=[("Правила", "коротко и по делу — читай #правила"),
                                                        ("Ивенты", "каждую пятницу в 19:00")]), sound="pop")
    v.wait(0.6)

    v.step(3)
    kit.pick(v, "e:switch", [{"label": "Embed 1", "emoji": u("text")}, {"label": "Добавить embed", "emoji": u("add")}], 1)
    second = {"title": "Полезное :u3_star:", "desc": "Гайды, FAQ и всё, что спрашивают чаще всего.", "color": ICE}
    v.edit(pid, lambda m: m["embeds"].append(dict(second)), sound="pop")
    v.wait(1.4)

    v.step(4)
    v.wait(2.2)

    # ============ 2. КНОПКИ ============
    kit.chapter(v, 2, TOTAL, "Кнопки, которые умеют всё", CH2, "panel_art/emoji/ui3d/u3_button.png")
    v.click("e:confirm")
    inter_rows = [[btn("i:buttons", "Кнопки", u("button"), 1), btn("i:list", "Список", u("list")),
                   btn("i:native", "Выбор роли/участника", u("people")), btn("i:form", "Кнопка формы", u("text")),
                   btn("i:packs", "Паки форм", dd("book"))],
                  [btn("i:save", "Сохранить", u("save"), 3), btn("i:send", "Отправить", u("send"), 3),
                   btn("i:preview", "Превью", u("preview"))]]
    v.edit(pid, lambda m: m.update(embeds=[welcome_embed()], rows=inter_rows), sound="pop")
    v.wait(0.5)

    v.step(0)
    v.click("i:buttons")
    add_rows = [[btn("k:add", "Добавить кнопку", u("add"), 1), btn("k:done", "Готово", u("confirm"), 3)],
                [{"type": "select", "id": "k:action", "placeholder": "Что должна делать кнопка?"}]]
    v.edit(pid, lambda m: m.update(rows=add_rows), sound="pop")
    v.click("k:add")
    kit.modal(v, "Текст кнопки", [{"id": "label", "label": "Текст кнопки", "placeholder": "Подать заявку"},
                                  {"id": "emoji", "label": "Emoji", "placeholder": ":smile:"}])
    kit.fill(v, {"label": "Правила", "emoji": ":d3_book:"}, speed=2)
    kit.close_modal(v)

    v.step(1)
    kit.pick(v, "k:action", ACTIONS, 4)
    v.edit(pid, lambda m: m["embeds"].append(kit.toast("Кнопка «Правила» → покажет embed «Правила сервера»")),
           sound="success")
    v.wait(1.0)

    v.step(2)
    v.edit(pid, lambda m: m.update(embeds=m["embeds"][:1]))
    v.click("k:add")
    kit.modal(v, "Текст кнопки", [{"id": "label", "label": "Текст кнопки", "placeholder": ""},
                                  {"id": "emoji", "label": "Emoji", "placeholder": ""}])
    kit.fill(v, {"label": "Дальше", "emoji": ":u3_next:"}, speed=2.4)
    kit.close_modal(v)
    kit.pick(v, "k:action", ACTIONS, 5)
    v.edit(pid, lambda m: m["embeds"].append(kit.toast("«Дальше» → заменит сообщение на «Гид · шаг 2»")),
           sound="success")
    v.wait(0.9)

    v.step(3)
    v.edit(pid, lambda m: m.update(embeds=m["embeds"][:1]))
    v.click("k:add")
    kit.modal(v, "Текст кнопки", [{"id": "label", "label": "Текст кнопки", "placeholder": ""},
                                  {"id": "emoji", "label": "Emoji", "placeholder": ""}])
    kit.fill(v, {"label": "Хочу на ивенты", "emoji": ":d3_balloon:"}, speed=2.4)
    kit.close_modal(v)
    kit.pick(v, "k:action", ACTIONS, 3)
    v.edit(pid, lambda m: m["embeds"].append(kit.toast("«Хочу на ивенты» → выдаст или снимет роль @ивенты")),
           sound="success")
    v.wait(0.9)

    v.step(4)
    v.click("k:done")
    v.edit(pid, lambda m: m.update(embeds=[welcome_embed()], rows=inter_rows), sound="pop")
    v.click("i:list")
    kit.modal(v, "Опция списка", [{"id": "label", "label": "Текст опции", "placeholder": ""},
                                  {"id": "answer", "label": "Ответ при выборе", "placeholder": "", "tall": True}])
    kit.fill(v, {"label": "Когда ивенты?", "answer": "Каждую пятницу в 19:00 — заходи в #ивенты :d3_balloon:"}, speed=2)
    kit.close_modal(v)
    v.edit(pid, lambda m: m.update(embeds=[welcome_embed(), kit.toast("Список: 3 опции, у каждой свой ответ")],
                                   rows=guide_rows() + inter_rows[1:]), sound="success")
    v.wait(1.6)

    # ============ 3. ЖИВОЕ ============
    kit.chapter(v, 3, TOTAL, "Живые сообщения", CH3, "panel_art/emoji/doodle3d/d3_bolt.png")
    v.step(0)
    vote = {"title": "Лучший ивент октября :d3_trophy:", "desc": "Голосов: **{counter:ивент}**", "color": LIME,
            "thumb": "panel_art/thumbnail.select.png"}
    vote_id = v.message({"kind": "bot", "channel": "ивенты", "embeds": [dict(vote, desc="Голосов: **12**")],
                         "rows": [[btn("v:plus", "+1", dd("heart"), 1), btn("v:reset", "Сбросить", u("refresh"), 2)]]},
                        sound=None)
    v.click("ch:ивенты")
    v.do(lambda s: s.update(channel="ивенты"))
    v.wait(0.5)
    for n, who in enumerate(("Лиса · участница", "Кот · участник", "Ёжик · участник")):
        v.do(lambda s, who=who: s.update(cursor_label=who))
        v.click("v:plus", move=0.45, after=0.1)
        v.edit(vote_id, lambda m, k=13 + n: m["embeds"][0].update(desc=f"Голосов: **{k}**"), sound="pop")
        v.wait(0.35)
    v.do(lambda s: s.update(cursor_label=None))
    v.wait(0.6)

    v.step(1)
    v.click("ch:общий")
    v.do(lambda s: s.update(channel="общий"))
    v.edit(pid, lambda m: m.update(embeds=[welcome_embed()],
                                   rows=[[btn("o:live", "Живое обновление", dd("bolt")),
                                          btn("o:variant", "Вариант по роли", u("people")),
                                          btn("o:parent", "Родитель стиля", u("palette"))]]), sound="pop")
    v.click("o:live")
    kit.modal(v, "Живое обновление", [{"id": "min", "label": "Раз в сколько минут", "placeholder": "0 — выключить"}])
    kit.fill(v, {"min": "5"}, speed=1.5)
    kit.close_modal(v)
    post_id = v.message({"kind": "bot", "embeds": [welcome_embed(live=True)], "rows": guide_rows(),
                         "time": "Сегодня, в 18:45"}, sound="pop")
    v.wait(1.0)
    v.edit(post_id, lambda m: m["embeds"][0].update(desc="Нас уже **1 251**, в сети **327** :d3_heart:\n"
                                                         "Выбери, с чего начать:"), sound="tick")
    v.wait(1.2)

    v.step(2)
    v.do(lambda s: s.update(cursor_label="Лиса · участница"))
    v.click("g:next")
    guide = v.message({"kind": "bot", "embeds": [{"title": "Гид · шаг 2 из 3", "color": PINK,
                                                  "desc": "Выбери роли в #роли — так ты увидишь нужные каналы.",
                                                  "thumb": "panel_art/thumbnail.select.png"}],
                       "rows": [[btn("gd:back", "Назад", u("back")), btn("gd:next", "Дальше", u("next"), 1)]],
                       "ephemeral": True}, sound="pop")
    v.wait(1.0)
    v.click("gd:next")
    v.edit(guide, lambda m: m.update(embeds=[{"title": "Гид · шаг 3 из 3", "color": LIME,
                                              "desc": "Готово! Заглядывай на ивенты по пятницам :d3_balloon:",
                                              "thumb": "panel_art/thumbnail.interactive.png"}],
                                     rows=[[btn("gd:back2", "Назад", u("back")), btn("gd:done", "Готово", u("confirm"), 3)]]),
           sound="pop")
    v.wait(1.0)
    v.remove(guide)
    kit.pick(v, "g:topic", [{"label": "Когда ивенты?", "emoji": dd("balloon")}, {"label": "Как стать модером?", "emoji": dd("shield")},
                            {"label": "Партнёрство", "emoji": dd("gift")}], 0)
    ans = v.message({"kind": "bot", "embeds": [{"desc": "Каждую пятницу в 19:00 — заходи в #ивенты :d3_balloon:",
                                                "color": PINK, "width": 400}], "ephemeral": True}, sound="pop")
    v.wait(1.2)
    v.remove(ans)
    v.do(lambda s: s.update(cursor_label=None))

    v.step(3)
    v.click("o:variant", move=0.5)
    v.edit(pid, lambda m: m["embeds"].append(kit.toast("Staff и выше → вместо «Правил» видят «Панель модерации»")),
           sound="success")
    v.wait(0.8)
    v.do(lambda s: s.update(cursor_label="Лиса · участница"))
    v.click("g:rules")
    v.message({"kind": "bot", "embeds": [{"title": "Правила сервера :d3_book:", "color": PINK,
                                          "desc": "1. Уважай других\n2. Без спама\n3. Флуд — в #оффтоп"}],
               "ephemeral": True}, sound="pop")
    v.wait(1.0)
    v.do(lambda s: s.update(cursor_label="Кот · модератор"))
    v.click("g:rules")
    v.message({"kind": "bot", "embeds": [{"title": "Панель модерации :d3_shield:", "color": "#DA373C",
                                          "desc": "Жалоб сегодня: **3** · заявок ждут: **{pending}**".replace("{pending}", "5")}],
               "rows": [[btn("mod:warn", "Предупредить", u("warn"), 4), btn("mod:log", "Журнал", u("list"))]],
               "ephemeral": True}, sound="pop")
    v.wait(1.4)
    v.do(lambda s: s.update(cursor_label=None))

    v.step(4)
    v.click("o:parent")
    v.edit(pid, lambda m: m["embeds"].append(kit.toast("Родитель «Фирменный стиль»: цвет, автор, подпись")),
           sound="success")
    v.wait(0.5)
    v.edit(post_id, lambda m, t=v.t: m["embeds"][0].update(color=LIME, old_color=PINK, t_color=t), sound="tick")
    v.edit(pid, lambda m, t=v.t: m["embeds"][0].update(color=LIME, old_color=PINK, t_color=t))
    v.wait(1.6)

    # ============ 4. ОТПРАВКА ============
    kit.chapter(v, 4, TOTAL, "Отправка", CH4, "panel_art/emoji/ui3d/u3_send.png")
    v.remove(post_id)
    v.edit(pid, lambda m: m.update(embeds=[welcome_embed(color=LIME)], rows=inter_rows))
    v.step(0)
    v.click("i:preview")
    dark, ratio = preview_image("dark")
    light, _ = preview_image("light")
    prev = v.message({"kind": "bot", "embeds": [{"image": dark, "image_ratio": ratio, "color": "#2B2D31", "width": 380}],
                      "rows": [[btn("pv:light", "Светлая тема", dd("sun")), btn("pv:real", "Настоящими", dd("chat"))]],
                      "ephemeral": True})
    v.wait(1.0)
    v.click("pv:light")
    v.edit(prev, lambda m: m["embeds"][0].update(image=light), sound="pop")
    v.wait(1.0)
    v.remove(prev)

    v.step(1)
    v.click("i:send")
    send_rows = [[{"type": "select", "id": "s:ch", "placeholder": "Куда отправить (до 5)"}],
                 [btn("s:hook", "Через вебхук", u("send"), 1), btn("s:vis", "Видимость", u("lock"))]]
    v.edit(pid, lambda m: m.update(embeds=[{"title": "КУДА ОТПРАВИТЬ", "color": LIME, "thumb": "panel_art/thumbnail.embed.png",
                                            "desc": "До 5 каналов, веток или форумов — или через вебхук."}],
                                   rows=send_rows), sound="pop")
    kit.pick(v, "s:ch", [{"label": "#общий", "emoji": dd("chat")}, {"label": "#правила", "emoji": dd("book")},
                         {"label": "ветка «гайды»", "emoji": u("list")}, {"label": "форум «вопросы»", "emoji": u("folder")}], 3)
    v.edit(pid, lambda m: m.update(embeds=[kit.toast("Отправлено: #общий, #правила, ветка «гайды», форум «вопросы»")]),
           sound="success")
    v.wait(1.1)

    v.step(2)
    v.edit(pid, lambda m: m.update(embeds=[{"title": "КУДА ОТПРАВИТЬ", "color": LIME, "thumb": "panel_art/thumbnail.embed.png",
                                            "desc": "До 5 каналов, веток или форумов — или через вебхук."}]))
    v.click("s:hook")
    kit.modal(v, "Через вебхук", [{"id": "name", "label": "Имя", "placeholder": ""},
                                  {"id": "avatar", "label": "Аватарка", "placeholder": "https://"}])
    kit.fill(v, {"name": "Совет сервера", "avatar": "https://…/council.png"}, speed=2)
    kit.close_modal(v)
    council = config.webhook_author("Совет сервера", "panel_art/thumbnail.select.png")
    v.message({"kind": "user", "author": council, "embeds": [welcome_embed(live=True, color=LIME, fields=False)],
               "rows": guide_rows()[:1]}, sound="pop")
    v.wait(1.3)

    v.step(3)
    v.click("s:vis")
    v.edit(pid, lambda m: m.update(embeds=[{"title": "ВИДИМОСТЬ", "color": LIME, "desc": "Кто сможет видеть и использовать?"}],
                                   rows=[[btn("vi:pub", "Публичный", u("web")), btn("vi:priv", "Приватный", u("lock")),
                                          btn("vi:res", "Ограниченный", u("roles"), 1)]]), sound="pop")
    v.click("vi:res")
    v.edit(pid, lambda m: m["embeds"].append(kit.toast("Видят и пользуются только @модераторы")), sound="success")
    v.wait(1.1)

    # ============ 5. САМО ПО СЕБЕ ============
    kit.chapter(v, 5, TOTAL, "Работает само", CH5, "panel_art/emoji/ui3d/u3_time.png")
    v.edit(pid, lambda m: m.update(embeds=[{"title": "АВТОМАТИКА", "color": LIME, "thumb": "panel_art/thumbnail.embed.png",
                                            "desc": "Пост уйдёт сам — по времени или когда что-то случится."}],
                                   rows=[[btn("a:sched", "Расписание", u("time")), btn("a:join", "Новый участник", u("people")),
                                          btn("a:kw", "Ключевое слово", dd("chat")), btn("a:thread", "Новая ветка", u("list"))]]),
           sound="pop")
    v.step(0)
    v.click("a:sched")
    kit.modal(v, "Расписание", [{"id": "when", "label": "Когда", "placeholder": "18:30, 25.12 18:30, через 2ч"},
                                {"id": "repeat", "label": "Повтор (пусто — один раз)", "placeholder": "1д, 12ч, 1 неделя"}])
    kit.fill(v, {"when": "пятница 18:00", "repeat": "1 неделя"}, speed=2)
    kit.close_modal(v)
    v.edit(pid, lambda m: m["embeds"].append(kit.toast("Каждую пятницу в 18:00 → #ивенты")), sound="success")
    v.wait(0.8)

    v.step(1)
    v.edit(pid, lambda m: m.update(embeds=m["embeds"][:1]))
    v.click("a:join")
    v.edit(pid, lambda m: m["embeds"].append(kit.toast("Новый участник → приветствие в #общий")), sound="success")
    v.wait(0.5)
    v.message({"kind": "system", "text": "**Ёжик** присоединился к серверу. Скорее поздоровайся!"}, sound="notify")
    v.wait(0.5)
    v.message({"kind": "bot", "embeds": [{"title": "Привет, @Ёжик! :smile_laugh:", "color": LIME,
                                          "desc": "Ты **1 249**-й участник. Загляни в #правила и выбери роли.",
                                          "thumb": "panel_art/thumbnail.default.png"}]}, sound="pop")
    v.wait(1.4)

    v.step(2)
    v.click("a:kw")
    kit.modal(v, "Ключевое слово", [{"id": "words", "label": "Слова или фразы через запятую", "placeholder": ""},
                                    {"id": "cd", "label": "Пауза между ответами, сек", "placeholder": "от 10"}])
    kit.fill(v, {"words": "как подать заявку, анкета", "cd": "60"}, speed=2)
    kit.close_modal(v)
    lisa = config.member("Лиса", "#F0B232", (210, 140, 60))
    v.message({"kind": "user", "author": lisa, "text": "а как подать заявку в модеры?"}, sound="pop")
    v.wait(0.5)
    v.bot_typing(0.4)
    v.message({"kind": "bot", "embeds": [{"title": "Набор в команду :d3_crown:", "color": LIME,
                                          "desc": "Жми кнопку — анкета откроется прямо здесь.",
                                          "thumb": "panel_art/thumbnail.forms.png"}],
               "rows": [[btn("kw:apply", "Подать заявку", u("text"), 1)]]}, sound="pop")
    v.wait(1.4)

    v.step(3)
    v.bump(pid)
    v.wait(0.3)
    v.click("a:thread")
    v.edit(pid, lambda m: m["embeds"].append(kit.toast("Новая ветка в форуме «вопросы» → ответ прямо в ней")),
           sound="success")
    v.wait(1.4)

    # ============ 6. УПРАВЛЕНИЕ ============
    kit.chapter(v, 6, TOTAL, "Полный контроль", CH6, "panel_art/emoji/ui3d/u3_settings.png")
    v.bump(pid)
    v.edit(pid, lambda m: m.update(embeds=[welcome_embed(color=LIME, fields=False)],
                                   rows=[[btn("c:from", "Из сообщения", u("text")), btn("c:json", "Экспорт JSON", u("box")),
                                          btn("c:hist", "История", u("time")), btn("c:resync", "Обновить отправленные", u("refresh"), 1)]]),
           sound="pop")
    v.step(0)
    v.click("c:from")
    kit.modal(v, "Из сообщения", [{"id": "link", "label": "Ссылка на сообщение или его ID", "placeholder": ""}])
    kit.fill(v, {"link": "https://discord.com/channels/…/…/1290…"}, speed=2.4)
    kit.close_modal(v)
    v.edit(pid, lambda m: m["embeds"].append(kit.toast("Собрано из сообщения: embed'ов 2, кнопок 3 — даже от другого бота")),
           sound="success")
    v.wait(1.0)

    v.step(1)
    v.edit(pid, lambda m: m.update(embeds=m["embeds"][:1]))
    v.click("c:json")
    v.message({"kind": "bot", "text": "Готово. Этот файл можно загрузить на любом сервере с ботом: `/embed file:`",
               "file": ("welcome_build.json", "3,4 КБ"), "ephemeral": True}, sound="pop")
    v.wait(1.4)

    v.step(2)
    v.click("c:hist")
    v.edit(pid, lambda m: m.update(embeds=[{"title": "ИСТОРИЯ ВЕРСИЙ", "color": LIME,
                                            "desc": "Перед каждым сохранением бот запоминает, как build выглядел (последние 25)."}],
                                   rows=[[{"type": "select", "id": "h:ver", "placeholder": "Выбери версию"}],
                                         [btn("h:restore", "Откатить к этой версии", u("back"), 4)]]), sound="pop")
    kit.pick(v, "h:ver", [{"label": "сегодня 18:40 · до правки emily", "emoji": u("time")},
                          {"label": "вчера 21:05 · до правки Кот", "emoji": u("time")},
                          {"label": "12.10 · до правки emily", "emoji": u("time")}], 1)
    v.click("h:restore")
    v.edit(pid, lambda m: m["embeds"].append(kit.toast("Откат выполнен — его тоже можно отменить")), sound="success")
    v.wait(1.0)

    v.step(3)
    v.edit(pid, lambda m: m.update(embeds=[welcome_embed(color=LIME, fields=False)],
                                   rows=[[btn("c:resync", "Обновить отправленные", u("refresh"), 1)]]))
    v.click("c:resync")
    v.edit(pid, lambda m: m["embeds"].append(kit.toast("Обновлено сообщений: 6 — во всех каналах")), sound="success")
    v.wait(1.6)

    kit.outro(v, "Максимум с /embed", [
        "Посты из нескольких embed'ов с живыми числами и переменными",
        "Кнопки на 12 действий: embed'ы, шаги-страницы, роли, формы, счётчики",
        "Разным ролям — разное, общий стиль у десятков сообщений",
        "До 5 мест сразу или от любого имени через вебхук",
        "Расписание, приветствия, ответы на слова и новые ветки",
        "Импорт, экспорт, история версий и правки везде сразу",
    ], dur=10)
    return v


def preview_image(theme):
    embed = {"title": "Добро пожаловать в eminemovna", "description": WELCOME_LIVE.replace(":d3_heart:", ""),
             "color": 0xB6EC34, "author": {"name": "eminemovna", "icon_url": config.BOT_AVATAR_STILL},
             "thumbnail": {"url": "panel_art/thumbnail.default.png"},
             "fields": [{"name": "Правила", "value": "коротко и по делу — читай #правила", "inline": False},
                        {"name": "Ивенты", "value": "каждую пятницу в 19:00", "inline": False}],
             "footer": {"text": "eminemovna · с нами с 2024"}}
    rows = [{"type": 1, "components": [{"type": 2, "style": 1, "label": "Правила", "custom_id": "a"},
                                       {"type": 2, "style": 2, "label": "Дальше", "custom_id": "b"},
                                       {"type": 2, "style": 3, "label": "Хочу на ивенты", "custom_id": "c"}]}]
    return config.preview_png([embed], rows, f"embed_preview_{theme}", theme=theme)


if __name__ == "__main__":
    v = build()
    print(f"длина: {v.duration():.1f} с")
    out = engine.ROOT / "promo" / "embed.mp4"
    if len(sys.argv) > 1 and sys.argv[1] == "frames":
        times = [float(x) for x in sys.argv[2:]]
        for t, im in zip(sorted(times), engine.build(v, out, preview_frames=times)):
            im.convert("RGB").save(config.CACHE / f"frame_{t:05.1f}.png")
        print("кадры в", config.CACHE)
    else:
        out.parent.mkdir(exist_ok=True)
        engine.build(v, out)
