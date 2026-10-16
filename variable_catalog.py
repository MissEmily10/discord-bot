"""
Каталог переменных: всё, что бот умеет подставлять в тексты, — с разделами,
описаниями, аргументами, примерами и словами для поиска.

Каждая переменная: Var(имя, раздел, название, описание, аргументы, примеры,
ключевые слова, вычисление, требования). Вычисление получает Ctx (сервер,
дата, кто нажал…) и аргумент (то, что после двоеточия) и возвращает значение
или None (тогда {переменная} остаётся в тексте как есть — видно, что не так).

Общие аргументы (через запятую, в любом порядке):
  число 1–25 — сколько мест в топе:      {messages_top:5}
  период — сегодня, вчера, неделя, месяц, всё (по умолчанию — всё):  {messages_top:неделя}
  каналы — имя/ID/#упоминание, «-» — кроме, категория — все её каналы:  {messages:Флуд,-Спам}
  «строка» — топ в одну строку через «·» вместо столбика: {messages_top:3,строка}
У слов первое — само слово: {word_top:какашка,5,неделя}.
"""

import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import discord

import activity
import variables as v

TEXT_KINDS = (discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel)
VOICE_KINDS = (discord.VoiceChannel, discord.StageChannel)
MONTHS = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
          "ноябрь", "декабрь")
MONTHS_GEN = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря")


@dataclass
class Ctx:
    guild: object
    today: datetime
    user: object = None
    bot: object = None
    cache: dict = field(default_factory=dict)


@dataclass
class Var:
    name: str
    section: str
    title: str
    about: str
    args: str
    examples: list
    keywords: str
    compute: object
    needs: str = ""          # "" / "content" (текст сообщений) / "presence" (статусы) / "button" (кто нажал)
    builder: str = ""        # конструктор: channels / voice / roles / word / period


@dataclass
class Args:
    top: int = 10
    period: object = None
    period_name: str = "всё"
    items: list = field(default_factory=list)   # остальное — каналы/роли/слова
    inline: bool = False


def parse(arg, default_top=10):
    result = Args(top=default_top)
    for raw in (arg or "").split(","):
        token = raw.strip()
        low = token.lower()
        if not token:
            continue
        if low.isdigit() and 1 <= int(low) <= 25:
            result.top = int(low)
        elif low in activity.PERIODS:
            result.period, result.period_name = activity.PERIODS[low], low
        elif low in ("строка", "в строку", "inline"):
            result.inline = True
        else:
            result.items.append(token)
    return result


def channel_ids(ctx, items, kinds=TEXT_KINDS):
    """Каналы из аргументов; None — без ограничения (все каналы)."""
    if not items:
        return None
    return [channel.id for channel in v.pick_channels(ctx.guild, ",".join(items), kinds=kinds)]


def name_of(ctx, user_id):
    member = ctx.guild.get_member(user_id)
    return member.display_name if member else "ушёл с сервера"


def fmt_top(ctx, rows, args, value=lambda n: str(n)):
    rows = [row for row in rows if row[1]][:args.top]
    if not rows:
        return "—"
    lines = [f"{n}. {name_of(ctx, user_id)} — {value(count)}" for n, (user_id, count) in enumerate(rows, 1)]
    return " · ".join(lines) if args.inline else "\n".join(lines)


def duration(seconds):
    seconds = int(seconds or 0)
    hours, minutes = seconds // 3600, seconds % 3600 // 60
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    return f"{hours} ч" if hours else f"{minutes} мин"


def humans(guild):
    return [member for member in getattr(guild, "members", []) if not member.bot]


def plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def parse_moment(text, today):
    """«25.12 20:00», «25.12.2026», «20:00» -> datetime или None."""
    import card_templates
    return card_templates.parse_when(text, base=today)


VARS = []


def var(name, section, title, about, compute, args="", examples=None, keywords="", needs="", builder=""):
    VARS.append(Var(name, section, title, about, args, examples or ["{" + name + "}"], keywords, compute, needs,
                    builder))


# ============================================================
# СЕРВЕР
# ============================================================

S = "Сервер"
var("server", S, "Название сервера", "как называется сервер", lambda c, a: c.guild.name, keywords="имя название")
var("server_id", S, "ID сервера", "числовой ID сервера", lambda c, a: c.guild.id, keywords="айди")
var("member_count", S, "Участников всего", "все участники вместе с ботами", lambda c, a: c.guild.member_count,
    keywords="люди количество сколько")
var("members", S, "Участники: люди или боты", "люди — без ботов, боты — только боты",
    lambda c, a: len(humans(c.guild)) if (a or "").strip().lower() in ("люди", "humans", "")
    else sum(1 for m in c.guild.members if m.bot),
    args="люди / боты", examples=["{members:люди}", "{members:боты}"], keywords="боты люди без ботов")
var("bots", S, "Ботов", "сколько ботов на сервере", lambda c, a: sum(1 for m in c.guild.members if m.bot))
var("online", S, "Онлайн", "сколько сейчас онлайн — считает сам Discord (обновляется раз в 5 минут)",
    None, keywords="в сети активны")
var("boosts", S, "Бусты", "сколько бустов у сервера", lambda c, a: c.guild.premium_subscription_count or 0,
    keywords="нитро буст")
var("boost_level", S, "Уровень буста", "0–3", lambda c, a: c.guild.premium_tier, keywords="нитро уровень")
var("boosters", S, "Бустеров", "сколько людей бустят сервер", lambda c, a: len(c.guild.premium_subscribers),
    keywords="нитро бустеры")
var("booster_list", S, "Кто бустит", "имена бустеров через запятую (до 15)",
    lambda c, a: ", ".join(m.display_name for m in c.guild.premium_subscribers[:15]) or "—", keywords="нитро")
var("server_age", S, "Возраст сервера", "сколько дней серверу",
    lambda c, a: (c.today.date() - c.guild.created_at.date()).days, keywords="дней создан")
var("server_created", S, "Дата создания", "когда создан сервер",
    lambda c, a: c.guild.created_at.strftime("%d.%m.%Y"), keywords="создан день рождения сервера")
var("owner", S, "Владелец", "ник владельца сервера",
    lambda c, a: c.guild.owner.display_name if c.guild.owner else "—", keywords="создатель")
var("channels", S, "Каналов", "всего каналов (без категорий)",
    lambda c, a: sum(1 for ch in c.guild.channels if not isinstance(ch, discord.CategoryChannel)))
var("text_channels", S, "Текстовых каналов", "сколько текстовых каналов", lambda c, a: len(c.guild.text_channels))
var("voice_channels", S, "Голосовых каналов", "сколько голосовых каналов",
    lambda c, a: len(c.guild.voice_channels) + len(c.guild.stage_channels))
var("roles_count", S, "Ролей", "сколько ролей (без @everyone)", lambda c, a: len(c.guild.roles) - 1)
var("emoji_count", S, "Эмодзи", "сколько своих эмодзи на сервере", lambda c, a: len(c.guild.emojis),
    keywords="смайлики")
var("sticker_count", S, "Стикеров", "сколько своих стикеров", lambda c, a: len(c.guild.stickers))

# ============================================================
# УЧАСТНИКИ
# ============================================================

U = "Участники"
var("newest_member", U, "Новенький", "кто зашёл последним",
    lambda c, a: (max(humans(c.guild), key=lambda m: m.joined_at or c.guild.created_at).display_name
                  if humans(c.guild) else "—"), keywords="последний новый зашёл")
var("newest_members", U, "Последние новенькие", "последние N зашедших (по умолчанию 5)",
    lambda c, a: ", ".join(m.display_name for m in sorted(humans(c.guild), key=lambda m: m.joined_at or
                                                            c.guild.created_at, reverse=True)[:parse(a, 5).top]),
    args="сколько", examples=["{newest_members:5}"], keywords="новые зашли")
var("joined", U, "Зашло", "сколько людей зашло за период (считается с запуска счётчика)",
    lambda c, a: activity.members_moved(c.guild.id, "joined", parse(a).period, c.today), args="период",
    examples=["{joined:сегодня}", "{joined:неделя}"], keywords="пришли новенькие зашли прирост", builder="period")
var("left", U, "Ушло", "сколько людей ушло за период",
    lambda c, a: activity.members_moved(c.guild.id, "left", parse(a).period, c.today), args="период",
    examples=["{left:сегодня}", "{left:неделя}"], keywords="ушли вышли кикнули", builder="period")
var("growth", U, "Прирост", "зашло минус ушло за период",
    lambda c, a: activity.members_moved(c.guild.id, "joined", parse(a).period, c.today)
    - activity.members_moved(c.guild.id, "left", parse(a).period, c.today),
    args="период", examples=["{growth:неделя}"], keywords="рост прирост", builder="period")
var("oldest_member", U, "Старожил", "кто дольше всех на сервере (дольше всех с нами)",
    lambda c, a: (min(humans(c.guild), key=lambda m: m.joined_at or c.today).display_name
                  if humans(c.guild) else "—"), keywords="старый давно первый")
var("random_member", U, "Случайный участник", "ник случайного человека (без ботов)",
    lambda c, a: random.choice(humans(c.guild)).display_name if humans(c.guild) else "—",
    keywords="рандом случайный выбрать")
var("random_member_role", U, "Случайный с ролью", "случайный человек с ролью",
    lambda c, a: (lambda people: random.choice(people).display_name if people else "—")(
        [m for r in v.pick_roles(c.guild, a) for m in r.members if not m.bot]),
    args="роли", examples=["{random_member_role:Модератор}"], keywords="рандом роль", builder="roles")

# ============================================================
# РОЛИ
# ============================================================

R = "Роли"
var("role", R, "Участников с ролью", "сколько людей с ролью; несколько ролей — каждый один раз",
    lambda c, a: len({m.id for r in v.pick_roles(c.guild, a) for m in r.members}) if v.pick_roles(c.guild, a) else None,
    args="роли через запятую", examples=["{role:Модератор}", "{role:Модератор,Хелпер}"], keywords="роль сколько",
    builder="roles")
var("role_list", R, "Кто с ролью", "имена людей с ролью (до 15)",
    lambda c, a: ", ".join(m.display_name for m in list({m.id: m for r in v.pick_roles(c.guild, a)
                                                          for m in r.members}.values())[:15]) or "—",
    args="роли", examples=["{role_list:Модератор}"], keywords="список состав", builder="roles")
var("role_online", R, "С ролью в сети", "сколько людей с ролью не офлайн",
    lambda c, a: len({m.id for r in v.pick_roles(c.guild, a) for m in r.members
                      if m.status != discord.Status.offline}) if v.pick_roles(c.guild, a) else None,
    args="роли", examples=["{role_online:Модератор}"], keywords="онлайн статус", needs="presence", builder="roles")
var("role_in_voice", R, "С ролью в войсе", "сколько людей с ролью сейчас в голосовых",
    lambda c, a: len({m.id for r in v.pick_roles(c.guild, a) for m in r.members if m.voice and m.voice.channel})
    if v.pick_roles(c.guild, a) else None, args="роли", examples=["{role_in_voice:Модератор}"],
    keywords="войс голос", builder="roles")
var("top_role_count", R, "Самая большая роль", "роль, у которой больше всего людей",
    lambda c, a: max((r for r in c.guild.roles if not r.is_default()), key=lambda r: len(r.members)).name
    if len(c.guild.roles) > 1 else "—", keywords="популярная")

# ============================================================
# ГОЛОСОВЫЕ
# ============================================================

VO = "Голосовые"


def _voice_people(c, a):
    return v.people_in(v.pick_channels(c.guild, ",".join(parse(a).items) or None))


var("voice", VO, "В войсе сейчас", "сколько людей в голосовых; можно выбрать каналы, категории и исключения",
    lambda c, a: len(_voice_people(c, a)), args="каналы, «-» — кроме",
    examples=["{voice}", "{voice:Клан,Музыка}", "{voice:-AFK}", "{voice:Игры,-Тихая}"],
    keywords="голос войс сидят разговаривают", builder="voice")
var("voice_list", VO, "Кто в войсе", "имена тех, кто сейчас в голосовых (до 15)",
    lambda c, a: (lambda p: ", ".join(m.display_name for m in p[:15]) + (" …" if len(p) > 15 else "") or "—")(
        _voice_people(c, a)), args="каналы", examples=["{voice_list}", "{voice_list:Клан}"],
    keywords="голос список имена", builder="voice")
var("streaming", VO, "Стримят", "сколько людей сейчас показывают экран",
    lambda c, a: sum(1 for m in _voice_people(c, a) if m.voice and m.voice.self_stream), args="каналы",
    examples=["{streaming}", "{streaming:Кино}"], keywords="стрим демонстрация экран", builder="voice")
var("cameras", VO, "С камерой", "сколько людей с включённой камерой",
    lambda c, a: sum(1 for m in _voice_people(c, a) if m.voice and m.voice.self_video), args="каналы",
    examples=["{cameras}"], keywords="камера видео вебка", builder="voice")
var("muted", VO, "С выключенным микрофоном", "сколько людей в войсе с мьютом",
    lambda c, a: sum(1 for m in _voice_people(c, a) if m.voice and (m.voice.self_mute or m.voice.mute)),
    args="каналы", examples=["{muted}"], keywords="мут микрофон", builder="voice")
var("deafened", VO, "С выключенным звуком", "сколько людей в войсе с выключенными наушниками",
    lambda c, a: sum(1 for m in _voice_people(c, a) if m.voice and (m.voice.self_deaf or m.voice.deaf)),
    args="каналы", examples=["{deafened}"], keywords="наушники звук", builder="voice")
var("voice_busiest", VO, "Самый людный войс", "название голосового канала, где больше всего людей",
    lambda c, a: (lambda chs: max(chs, key=lambda ch: len(v.people_in([ch]))).name
                  if chs and v.people_in(chs) else "—")(v.pick_channels(c.guild, ",".join(parse(a).items) or None)),
    args="каналы", examples=["{voice_busiest}"], keywords="популярный канал", builder="voice")
var("voice_active_channels", VO, "Занятых войсов", "в скольких голосовых сейчас кто-то есть",
    lambda c, a: sum(1 for ch in v.pick_channels(c.guild, ",".join(parse(a).items) or None) if v.people_in([ch])),
    args="каналы", examples=["{voice_active_channels}"], keywords="занято", builder="voice")
var("voice_time_top", VO, "Топ по времени в войсе", "кто дольше всех сидел в голосовых за период",
    lambda c, a: fmt_top(c, activity.voice_by_user(c.guild.id, parse(a).period, c.today,
                                                   channel_ids(c, parse(a).items, VOICE_KINDS)), parse(a), duration),
    args="число мест, период, каналы", examples=["{voice_time_top:5,неделя}", "{voice_time_top:3,сегодня,строка}"],
    keywords="время войс топ рейтинг часы сидит", builder="voice")
var("voice_time_leader", VO, "Король войса", "кто дольше всех в голосовых за период",
    lambda c, a: (lambda rows: name_of(c, rows[0][0]) if rows else "—")(
        activity.voice_by_user(c.guild.id, parse(a).period, c.today, channel_ids(c, parse(a).items, VOICE_KINDS))),
    args="период, каналы", examples=["{voice_time_leader:неделя}"], keywords="лидер войс время", builder="voice")
var("voice_time_total", VO, "Всего часов в войсе", "сколько времени сервер провёл в голосовых за период",
    lambda c, a: duration(sum(n for _, n in activity.voice_by_user(c.guild.id, parse(a).period, c.today,
                                                                   channel_ids(c, parse(a).items, VOICE_KINDS)))),
    args="период, каналы", examples=["{voice_time_total:неделя}"], keywords="время часы", builder="voice")

# ============================================================
# СООБЩЕНИЯ
# ============================================================

M = "Сообщения"


def _msg_rows(c, a):
    args = parse(a)
    return activity.messages_by_user(c.guild.id, args.period, c.today, channel_ids(c, args.items))


var("messages", M, "Сообщений", "сколько сообщений написали за период (с момента запуска счётчика)",
    lambda c, a: sum(n for _, n in _msg_rows(c, a)), args="период, каналы",
    examples=["{messages:сегодня}", "{messages:неделя,Флуд}", "{messages:-Спам}"],
    keywords="сообщения написали чат активность количество", builder="channels")
var("messages_top", M, "Топ болтунов", "кто больше всех написал сообщений",
    lambda c, a: fmt_top(c, _msg_rows(c, a), parse(a)), args="число мест, период, каналы",
    examples=["{messages_top:5,неделя}", "{messages_top:3,сегодня,строка}", "{messages_top:10,Флуд}"],
    keywords="топ рейтинг активные болтун флудер сообщения пишет больше всех чат", builder="channels")
var("messages_leader", M, "Самый активный", "ник того, кто больше всех написал",
    lambda c, a: (lambda rows: name_of(c, rows[0][0]) if rows else "—")(_msg_rows(c, a)),
    args="период, каналы", examples=["{messages_leader:сегодня}"], keywords="лидер активный болтун пишет больше всех",
    builder="channels")
var("messages_leader_count", M, "Сколько написал лидер", "число сообщений у самого активного",
    lambda c, a: (lambda rows: rows[0][1] if rows else 0)(_msg_rows(c, a)), args="период, каналы",
    examples=["{messages_leader_count:сегодня}"], keywords="лидер количество", builder="channels")
var("active_users", M, "Сколько людей писали", "уникальные авторы за период",
    lambda c, a: len(_msg_rows(c, a)), args="период, каналы", examples=["{active_users:сегодня}"],
    keywords="активные писали уникальные", builder="channels")
var("busiest_channel", M, "Самый живой канал", "где больше всего сообщений за период",
    lambda c, a: (lambda rows: (c.guild.get_channel(rows[0][0]).name if c.guild.get_channel(rows[0][0])
                                else "—") if rows else "—")(
        activity.messages_by_channel(c.guild.id, parse(a).period, c.today)),
    args="период", examples=["{busiest_channel:сегодня}"], keywords="канал активный популярный", builder="period")
var("channels_top", M, "Топ каналов", "каналы по числу сообщений",
    lambda c, a: (lambda args, rows: ("\n" if not args.inline else " · ").join(
        f"{n}. #{c.guild.get_channel(cid).name if c.guild.get_channel(cid) else '—'} — {cnt}"
        for n, (cid, cnt) in enumerate(rows[:args.top], 1)) or "—")(
        parse(a, 5), activity.messages_by_channel(c.guild.id, parse(a).period, c.today)),
    args="число мест, период", examples=["{channels_top:5,неделя}"], keywords="каналы рейтинг", builder="period")
var("user_messages", M, "Мои сообщения", "сколько написал тот, кто нажал кнопку / сработал триггер",
    lambda c, a: sum(n for uid, n in _msg_rows(c, a) if c.user and uid == c.user.id) if c.user else None,
    args="период, каналы", examples=["{user_messages:неделя}"], keywords="я мои личные", needs="button")
var("user_rank", M, "Моё место", "место нажавшего в топе болтунов",
    lambda c, a: (lambda rows: next((n for n, (uid, _) in enumerate(rows, 1) if uid == c.user.id), "—"))(
        _msg_rows(c, a)) if c.user else None,
    args="период, каналы", examples=["{user_rank:неделя}"], keywords="место позиция я", needs="button")

# ============================================================
# СЛОВА
# ============================================================

W = "Слова"


def _word(c, a):
    args = parse(a)
    word = args.items[0] if args.items else ""
    if word:
        activity.track_word(c.guild.id, word)  # с этого момента слово начинают считать
    return word, args


def _word_rows(c, a):
    word, args = _word(c, a)
    return activity.words_by_user(c.guild.id, word, args.period, c.today) if word else None


var("word_count", W, "Сколько раз написали слово", "сколько раз слово встретилось в чате (по корню)",
    lambda c, a: (lambda rows: sum(n for _, n in rows) if rows is not None else None)(_word_rows(c, a)),
    args="слово, период", examples=["{word_count:какашка}", "{word_count:спасибо,сегодня}"],
    keywords="слово фраза считать упоминания", needs="content", builder="word")
var("word_top", W, "Топ по слову", "кто чаще всех пишет это слово",
    lambda c, a: (lambda rows: fmt_top(c, rows, _word(c, a)[1]) if rows is not None else None)(_word_rows(c, a)),
    args="слово, число мест, период", examples=["{word_top:какашка,5}", "{word_top:лол,3,неделя,строка}"],
    keywords="слово топ рейтинг больше всех пишет чаще", needs="content", builder="word")
var("word_leader", W, "Чемпион по слову", "кто чаще всех пишет слово",
    lambda c, a: (lambda rows: (name_of(c, rows[0][0]) if rows else "—") if rows is not None else None)(
        _word_rows(c, a)), args="слово, период", examples=["{word_leader:какашка}"],
    keywords="слово лидер чемпион", needs="content", builder="word")
var("word_leader_count", W, "Сколько раз у чемпиона", "сколько раз чемпион написал слово",
    lambda c, a: (lambda rows: (rows[0][1] if rows else 0) if rows is not None else None)(_word_rows(c, a)),
    args="слово, период", examples=["{word_leader_count:какашка}"], keywords="слово количество лидер",
    needs="content", builder="word")
var("word_user", W, "Моё слово", "сколько раз слово написал тот, кто нажал кнопку",
    lambda c, a: (lambda rows: sum(n for uid, n in rows if uid == c.user.id) if rows is not None else None)(
        _word_rows(c, a)) if c.user else None,
    args="слово, период", examples=["{word_user:спасибо}"], keywords="слово я мои", needs="content", builder="word")
var("word_since", W, "С какого дня считаем слово", "дата, с которой бот считает это слово",
    lambda c, a: (lambda ts: datetime.fromtimestamp(ts).strftime("%d.%m.%Y") if ts else "—")(
        activity.track_word(c.guild.id, parse(a).items[0]) if parse(a).items else None),
    args="слово", examples=["{word_since:какашка}"], keywords="слово с какого дня начало", builder="word")

# ============================================================
# РЕАКЦИИ
# ============================================================

RE = "Реакции"
var("reactions", RE, "Реакций поставлено", "сколько реакций поставили за период",
    lambda c, a: sum(n for _, n in activity.reactions_by_user(c.guild.id, "given", parse(a).period, c.today)),
    args="период", examples=["{reactions:сегодня}"], keywords="реакции лайки эмодзи", builder="period")
var("reactions_top", RE, "Кто больше всех реагирует", "топ ставящих реакции",
    lambda c, a: fmt_top(c, activity.reactions_by_user(c.guild.id, "given", parse(a).period, c.today), parse(a)),
    args="число мест, период", examples=["{reactions_top:5,неделя}"], keywords="реакции топ лайкают",
    builder="period")
var("reacted_top", RE, "Чьи сообщения любят", "топ тех, кто получил больше всего реакций",
    lambda c, a: fmt_top(c, activity.reactions_by_user(c.guild.id, "received", parse(a).period, c.today), parse(a)),
    args="число мест, период", examples=["{reacted_top:5,неделя}"], keywords="популярные лайки получил топ",
    builder="period")
var("reacted_leader", RE, "Звезда реакций", "кто получил больше всего реакций",
    lambda c, a: (lambda rows: name_of(c, rows[0][0]) if rows else "—")(
        activity.reactions_by_user(c.guild.id, "received", parse(a).period, c.today)),
    args="период", examples=["{reacted_leader:неделя}"], keywords="звезда лайки лидер", builder="period")
var("emoji_top", RE, "Любимые реакции", "какие эмодзи ставят чаще всего",
    lambda c, a: (lambda args, rows: " ".join(f"{e}×{n}" for e, n in rows[:args.top]) or "—")(
        parse(a, 5), activity.emoji_top(c.guild.id, parse(a).period, c.today)),
    args="число, период", examples=["{emoji_top:5,неделя}"], keywords="эмодзи смайлы популярные", builder="period")

# ============================================================
# ВРЕМЯ И ДАТЫ
# ============================================================

T = "Время"
var("date", T, "Дата", "сегодняшняя дата (дд.мм.гггг)", lambda c, a: c.today.strftime("%d.%m.%Y"),
    keywords="сегодня число")
var("date_text", T, "Дата словами", "«16 октября»", lambda c, a: f"{c.today.day} {MONTHS_GEN[c.today.month - 1]}",
    keywords="сегодня число месяц")
var("time", T, "Время", "часы и минуты сейчас (по Москве)", lambda c, a: c.today.strftime("%H:%M"),
    keywords="часы сейчас")
var("weekday", T, "День недели", "понедельник, вторник…", lambda c, a: v.WEEKDAYS[c.today.weekday()],
    keywords="неделя день")
var("month", T, "Месяц", "название месяца", lambda c, a: MONTHS[c.today.month - 1])
var("year", T, "Год", "текущий год", lambda c, a: c.today.year)
var("days_until", T, "Дней до", "сколько дней до даты", lambda c, a: v.days_until(a, c.today),
    args="дата", examples=["{days_until:25.12}", "{days_until:01.01}"], keywords="осталось отсчёт до")
var("days_since", T, "Дней после", "сколько дней прошло с даты", lambda c, a: v.days_since(a, c.today),
    args="дата", examples=["{days_since:01.09.2026}"], keywords="прошло с начала")
var("time_until", T, "Осталось до", "«2 д 5 ч 10 мин» до даты и времени",
    lambda c, a: (lambda when: (lambda left: "уже!" if left.total_seconds() <= 0 else
                                f"{left.days} д {left.seconds // 3600} ч {left.seconds % 3600 // 60} мин")(
        when - c.today) if when else None)(parse_moment(a, c.today)),
    args="дата и время", examples=["{time_until:25.12 20:00}", "{time_until:20:00}"], keywords="отсчёт таймер")
var("timestamp", T, "Время у каждого своё", "Discord сам покажет время в часовом поясе читателя (только в сообщениях)",
    lambda c, a: (lambda when: f"<t:{int(when.replace(tzinfo=c.today.tzinfo).timestamp())}:R>" if when else None)(
        parse_moment(a, c.today)), args="дата и время", examples=["{timestamp:25.12 20:00}"],
    keywords="часовой пояс таймер через")
var("hour_greeting", T, "Приветствие по времени", "доброе утро / день / вечер / ночи",
    lambda c, a: ("доброй ночи" if c.today.hour < 5 else "доброе утро" if c.today.hour < 12 else
                  "добрый день" if c.today.hour < 18 else "добрый вечер"), keywords="привет утро вечер")

# ============================================================
# ЗАЯВКИ, СЧЁТЧИКИ, ЦИТАТЫ, ШЕПТАЛКИ
# ============================================================

F = "Заявки"
for _name, _title, _about in (("submissions", "Заявок всего", "сколько заявок (можно ID формы)"),
                              ("pending", "Ждут проверки", "заявки, которые ещё не проверили"),
                              ("approved", "Принято", "принятые заявки"),
                              ("rejected", "Отклонено", "отклонённые заявки"),
                              ("last_submission", "Последняя заявка", "форма, кто и когда")):
    var(_name, F, _title, _about, None, args="ID формы (необязательно)",
        examples=["{" + _name + "}", "{" + _name + ":12}"], keywords="заявки формы анкеты набор")

C = "Счётчики и бот"
var("counter", C, "Счётчик", "значение счётчика (кнопка «Счётчик» в /embed)", None, args="имя счётчика",
    examples=["{counter:очки}"], keywords="счёт очки кнопка")
var("quote_today", C, "Цитата дня", "текст последней цитаты дня",
    lambda c, a: (lambda q: q["text"] if q else "—")(_last_quote(c)), keywords="цитата дня победитель")
var("quote_author", C, "Автор цитаты дня", "кто автор последней цитаты дня",
    lambda c, a: (lambda q: name_of(c, q["author_id"]) if q else "—")(_last_quote(c)), keywords="цитата автор")
var("quotes_pending", C, "Цитат собрано сегодня", "сколько цитат ждут голосования",
    lambda c, a: len(__import__("quotes").pending_quotes(c.guild.id)), keywords="цитаты")
var("whispers_today", C, "Шепталок сегодня", "сколько шепталок отправили за сутки",
    lambda c, a: __import__("whispers")._q("SELECT COUNT(*) FROM whispers WHERE guild_id = ? AND created_at >= ?",
                                           (c.guild.id, int(time.time()) - 86400), one=True)[0],
    keywords="шепталки анонимки")
var("bot_ping", C, "Пинг бота", "задержка бота в мс",
    lambda c, a: round(c.bot.latency * 1000) if c.bot and c.bot.latency == c.bot.latency else "—",
    keywords="задержка лаг")
var("bot_uptime", C, "Бот работает", "сколько бот работает без перезапуска",
    lambda c, a: duration(time.time() - activity._started), keywords="аптайм работает")

# ============================================================
# КТО НАЖАЛ (кнопки, триггеры)
# ============================================================

ME = "Кто нажал"
var("user", ME, "Упоминание", "упоминание того, кто нажал кнопку / сработал триггер", None, needs="button",
    keywords="я пинг тег")
var("user_name", ME, "Имя", "имя того, кто нажал", None, needs="button", keywords="ник я")
var("user_id", ME, "ID", "ID того, кто нажал", lambda c, a: c.user.id if c.user else None, needs="button")
var("user_joined", ME, "Когда зашёл", "дата, когда нажавший зашёл на сервер",
    lambda c, a: c.user.joined_at.strftime("%d.%m.%Y") if c.user and getattr(c.user, "joined_at", None) else None,
    needs="button", keywords="стаж дата вход")
var("user_days", ME, "Дней на сервере", "сколько дней нажавший на сервере",
    lambda c, a: (c.today.date() - c.user.joined_at.date()).days if c.user and getattr(c.user, "joined_at", None)
    else None, needs="button", keywords="стаж сколько")
var("user_top_role", ME, "Главная роль", "самая высокая роль нажавшего",
    lambda c, a: c.user.top_role.name if c.user and hasattr(c.user, "top_role") else None, needs="button")
var("user_voice_time", ME, "Моё время в войсе", "сколько нажавший сидел в голосовых за период",
    lambda c, a: duration(sum(n for uid, n in activity.voice_by_user(c.guild.id, parse(a).period, c.today)
                              if uid == c.user.id)) if c.user else None,
    args="период", examples=["{user_voice_time:неделя}"], needs="button", keywords="я войс время")

# ============================================================
# РАЗНОЕ
# ============================================================

X = "Разное"
var("random", X, "Случайный вариант", "один из вариантов через |",
    lambda c, a: random.choice([p.strip() for p in a.split("|")]) if a else None, args="варианты через |",
    examples=["{random:привет|хай|йоу}"], keywords="рандом случайно выбор")
var("random_number", X, "Случайное число", "число от и до (по умолчанию 1–100)",
    lambda c, a: (lambda m: random.randint(int(m[1]), int(m[2])) if m else random.randint(1, 100))(
        re.fullmatch(r"\s*(-?\d+)\s*[-–]\s*(-?\d+)\s*", a or "")), args="от-до",
    examples=["{random_number:1-6}"], keywords="кубик число рандом")
var("dice", X, "Кубик", "бросок кубика 1–6", lambda c, a: random.randint(1, 6), keywords="кубик игра")
var("coin", X, "Монетка", "орёл или решка", lambda c, a: random.choice(("орёл", "решка")), keywords="монетка")


def _last_quote(c):
    import quotes
    row = quotes._q("SELECT winner_id FROM quote_rounds WHERE guild_id = ? AND winner_id IS NOT NULL "
                    "ORDER BY id DESC LIMIT 1", (c.guild.id,), one=True)
    if not row:
        return None
    found = quotes._quotes("WHERE id = ?", (row[0],))
    return found[0] if found else None


BY_NAME = {item.name: item for item in VARS}
SECTIONS = list(dict.fromkeys(item.section for item in VARS))
NEEDS_TEXT = {"content": "нужен доступ к тексту сообщений (Message Content Intent)",
              "presence": "нужен доступ к статусам (Presence Intent)",
              "button": "работает в кнопках и триггерах — там, где есть «кто нажал»"}


STOP_WORDS = {"кто", "что", "как", "в", "во", "на", "за", "и", "по", "с", "у", "о", "the", "a"}
_TOKEN = re.compile(r"[\wё]+", re.UNICODE)


def _tokens(text):
    return [token for token in _TOKEN.findall((text or "").lower().replace("ё", "е"))]


def _hit(word, tokens):
    """Слово запроса совпадает с началом слова описания (или по корню: «сообщений» ~ «сообщения»)."""
    stem = word[:max(4, len(word) - 2)] if len(word) > 4 else word
    return any(token.startswith(word) or token.startswith(stem) for token in tokens)


def search(query):
    """Поиск по смыслу: каждое слово запроса — по началу слов в имени, названии, описании и ключевых словах.
    Сначала те, где совпало больше слов запроса; имя переменной весит больше. -> [Var]."""
    words = [w for w in _tokens(query.strip("{} ") if query else "") if w not in STOP_WORDS]
    if not words:
        return list(VARS)
    scored = []
    for item in VARS:
        name_tokens = _tokens(item.name.replace("_", " "))
        tokens = _tokens(" ".join((item.title, item.about, item.keywords, item.section, " ".join(item.examples))))
        matched = score = 0
        for word in words:
            if word == item.name.lower():
                matched, score = matched + 1, score + 20
            elif _hit(word, name_tokens):
                matched, score = matched + 1, score + 6
            elif _hit(word, tokens):
                matched, score = matched + 1, score + 2
        if matched:
            scored.append((matched, score, item))
    scored.sort(key=lambda row: (-row[0], -row[1]))
    return [item for _, _, item in scored]


def compute_all(ctx, names):
    """-> {имя: значение или функция(арг)} для переменных из каталога, которые используются."""
    result = {}
    for name in names:
        item = BY_NAME.get(name)
        if item is None or item.compute is None:
            continue
        result[name] = (lambda item: lambda arg: _safe(item, ctx, arg))(item)
    return result


def _safe(item, ctx, arg):
    try:
        return item.compute(ctx, arg)
    except Exception:  # сломанный аргумент не должен ронять всё сообщение
        return None
