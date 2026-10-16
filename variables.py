"""
Библиотека переменных: что можно писать в текстах ({voice}, {role:…}, …),
как задаются аргументы и как бот их считает.

Запись: {имя} или {имя:аргумент}. Аргументы-списки — через запятую.
Каналы и роли можно указывать как угодно: ID, <#упоминание>, имя («Клан»).
Минус перед элементом — исключить: {voice:-AFK} — все войсы, кроме AFK.
Категория = все её каналы: {voice:Игры,-Игры-тихая}.

Значения считает live.collect (подставляет actions.substitute); здесь —
список переменных для «Библиотеки переменных» и разбор аргументов.
"""

import random
import re
from datetime import datetime

import discord

# (имя, группа, описание, пример, вид аргумента: None / channels / roles / text / date)
VARIABLES = [
    ("server", "Сервер", "название сервера", "{server}", None),
    ("member_count", "Сервер", "сколько всего участников", "{member_count}", None),
    ("members", "Сервер", "участники с выбором: люди, боты", "{members:люди} · {members:боты}", "text"),
    ("online", "Сервер", "сколько сейчас онлайн (считает Discord)", "{online}", None),
    ("boosts", "Сервер", "сколько бустов", "{boosts}", None),
    ("boost_level", "Сервер", "уровень буста (0–3)", "{boost_level}", None),
    ("voice", "Голосовые", "сколько людей в войсе; можно выбрать каналы, категории и исключения",
     "{voice} · {voice:Клан,Музыка} · {voice:-AFK} · {voice:Игры,-Тихая}", "channels"),
    ("voice_list", "Голосовые", "кто сейчас в войсе — имена через запятую (до 15)",
     "{voice_list:Клан}", "channels"),
    ("streaming", "Голосовые", "сколько людей сейчас стримят (демонстрация экрана)",
     "{streaming} · {streaming:Кино}", "channels"),
    ("role", "Роли", "сколько участников с ролью; несколько ролей — без повторов",
     "{role:Модератор} · {role:Модератор,Хелпер}", "roles"),
    ("role_online", "Роли", "сколько участников с ролью сейчас не офлайн (нужен доступ к статусам)",
     "{role_online:Модератор}", "roles"),
    ("counter", "Счётчики", "значение счётчика (кнопки «Счётчик» в /embed)", "{counter:очки}", "text"),
    ("date", "Время", "сегодняшняя дата", "{date}", None),
    ("time", "Время", "время сейчас", "{time}", None),
    ("weekday", "Время", "день недели", "{weekday}", None),
    ("days_until", "Время", "сколько дней до даты", "{days_until:25.12}", "date"),
    ("days_since", "Время", "сколько дней прошло с даты", "{days_since:01.09.2026}", "date"),
    ("submissions", "Заявки", "сколько заявок (можно по форме: ID формы)", "{submissions} · {submissions:12}", "text"),
    ("pending", "Заявки", "заявок ждут проверки", "{pending}", "text"),
    ("approved", "Заявки", "принято заявок", "{approved}", "text"),
    ("rejected", "Заявки", "отклонено заявок", "{rejected}", "text"),
    ("last_submission", "Заявки", "последняя заявка: форма, кто, когда", "{last_submission}", "text"),
    ("random", "Разное", "случайный вариант из списка через |", "{random:привет|хай|йоу}", "text"),
    ("user", "Разное", "упоминание того, кто нажал кнопку (только в кнопках и триггерах)", "{user}", None),
    ("user_name", "Разное", "имя того, кто нажал кнопку", "{user_name}", None),
]
NAMES = {name for name, *_ in VARIABLES}
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


def help_line():
    """Короткая строка со всеми переменными — для подсказок в панелях."""
    return " ".join("{" + name + "}" for name, *_ in VARIABLES if name not in ("user", "user_name"))


def split_items(arg):
    """«Клан, -AFK , 123» -> [(включить?, элемент)]; минус в начале — исключить."""
    items = []
    for raw in (arg or "").split(","):
        raw = raw.strip()
        if raw:
            items.append((not raw.startswith("-"), raw.lstrip("-").strip()))
    return items


def _match_id(text):
    match = re.fullmatch(r"<[#@]&?!?(\d+)>|(\d{15,25})", text)
    return int(match[1] or match[2]) if match else None


def find_channels(guild, item, kinds):
    """Элемент списка -> каналы нужного вида (категория раскрывается в свои каналы)."""
    found = []
    target_id = _match_id(item)
    low = item.lower().lstrip("#")
    for channel in guild.channels:
        if target_id is not None:
            hit = channel.id == target_id
        else:
            hit = channel.name.lower() == low
        if not hit:
            continue
        if isinstance(channel, discord.CategoryChannel):
            found += [c for c in channel.channels if isinstance(c, kinds)]
        elif isinstance(channel, kinds):
            found.append(channel)
    return found


def pick_channels(guild, arg, kinds=(discord.VoiceChannel, discord.StageChannel)):
    """
    {voice} — все; {voice:A,B} — только A и B; {voice:-C} — все, кроме C;
    {voice:A,-C} — A без C (C может быть каналом внутри категории A).
    """
    every = [c for c in guild.channels if isinstance(c, kinds)]
    items = split_items(arg)
    include = [c for keep, item in items if keep for c in find_channels(guild, item, kinds)]
    exclude = {c.id for keep, item in items if not keep for c in find_channels(guild, item, kinds)}
    has_include = any(keep for keep, _ in items)
    base = include if has_include else every
    seen, result = set(), []
    for channel in base:
        if channel.id not in exclude and channel.id not in seen:
            seen.add(channel.id)
            result.append(channel)
    return result


def pick_roles(guild, arg):
    roles = []
    for keep, item in split_items(arg):
        if not keep:
            continue
        role_id = _match_id(item)
        role = guild.get_role(role_id) if role_id else discord.utils.find(
            lambda r: r.name.lower() == item.lower().lstrip("@"), guild.roles)
        if role is not None:
            roles.append(role)
    return roles


def people_in(channels, bots=False):
    return [member for channel in channels for member in channel.members if bots or not member.bot]


def parse_day(text):
    match = re.fullmatch(r"\s*(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\s*", text or "")
    if not match:
        return None
    try:
        year = int(match[3]) if match[3] else None
        if year is not None and year < 100:
            year += 2000
        return int(match[1]), int(match[2]), year
    except ValueError:
        return None


def days_until(arg, today):
    parsed = parse_day(arg)
    if not parsed:
        return None
    day, month, year = parsed
    try:
        target = datetime(year or today.year, month, day)
        if year is None and target.date() < today.date():
            target = target.replace(year=today.year + 1)
    except ValueError:
        return None
    return (target.date() - today.date()).days


def days_since(arg, today):
    parsed = parse_day(arg)
    if not parsed:
        return None
    day, month, year = parsed
    try:
        target = datetime(year or today.year, month, day)
        if year is None and target.date() > today.date():
            target = target.replace(year=today.year - 1)
    except ValueError:
        return None
    return (today.date() - target.date()).days


def fill(variables, guild, names, today):
    """Добавить в variables значения переменных этого модуля (те, что используются)."""
    if names & {"voice", "voice_list", "streaming"}:
        variables["voice"] = lambda arg: len(people_in(pick_channels(guild, arg)))

        def voice_list(arg):
            people = people_in(pick_channels(guild, arg))
            names_ = [member.display_name for member in people[:15]]
            return (", ".join(names_) + (" …" if len(people) > 15 else "")) or "—"

        variables["voice_list"] = voice_list
        variables["streaming"] = lambda arg: sum(
            1 for member in people_in(pick_channels(guild, arg)) if member.voice and member.voice.self_stream)

    def role_count(arg):
        roles = pick_roles(guild, arg)
        if not roles:
            return None
        return len({member.id for role in roles for member in role.members})

    variables["role"] = role_count
    variables["role_online"] = lambda arg: len({
        member.id for role in pick_roles(guild, arg) for member in role.members
        if member.status != discord.Status.offline}) if pick_roles(guild, arg) else None

    def members(arg):
        everyone = getattr(guild, "members", [])
        kind = (arg or "").strip().lower()
        if kind in ("люди", "humans", "people"):
            return sum(1 for member in everyone if not member.bot)
        if kind in ("боты", "bots"):
            return sum(1 for member in everyone if member.bot)
        return guild.member_count or len(everyone)

    variables["members"] = members
    variables["weekday"] = WEEKDAYS[today.weekday()]
    variables["days_until"] = lambda arg: days_until(arg, today)
    variables["days_since"] = lambda arg: days_since(arg, today)
    variables["random"] = lambda arg: random.choice([part.strip() for part in arg.split("|")]) if arg else None
    return variables


# ============================================================
# ПАНЕЛЬ: «Переменные» — список и конструктор
# ============================================================

import core  # noqa: E402
from core import PanelView, t, panel_embed  # noqa: E402


def library_embed(interaction):
    groups = {}
    for name, group, about, example, _ in VARIABLES:
        groups.setdefault(group, []).append(f"`{example}` — {about}")
    text = "\n\n".join(f"**{group}**\n" + "\n".join(lines) for group, lines in groups.items())
    return panel_embed(interaction, "variables.home", rows=text[:3500])


class VariablesView(PanelView):
    texts = "variables.home"

    def __init__(self, back_target=None):
        super().__init__(back_target=back_target)
        select = discord.ui.Select(placeholder=t("variables.pick")[:150], options=[
            discord.SelectOption(label="{" + name + "}", description=about[:100], value=name)
            for name, _, about, _, kind in VARIABLES if kind in ("channels", "roles")])

        async def picked(i):
            name = select.values[0]
            kind = next(k for n, *_, k in VARIABLES if n == name)
            await i.response.edit_message(embed=panel_embed(i, f"variables.build_{kind}", name=name),
                                          view=BuildView(name, kind, back_target=(core.screen(i), self)))

        select.callback = picked
        self.add_item(select)


class BuildView(PanelView):
    """Каналы/роли выбираются списками Discord — бот собирает {voice:ID,-ID} сам (ID не ломаются при переименовании)."""

    def __init__(self, name, kind, back_target=None):
        super().__init__(back_target=back_target, timeout=900)
        self.name, self.kind = name, kind
        self.include, self.exclude = [], []
        if kind == "channels":
            types = [discord.ChannelType.voice, discord.ChannelType.stage_voice, discord.ChannelType.category]
            first = discord.ui.ChannelSelect(placeholder=t("variables.include_channels")[:150], channel_types=types,
                                             min_values=0, max_values=25, row=0)
            second = discord.ui.ChannelSelect(placeholder=t("variables.exclude_channels")[:150], channel_types=types,
                                              min_values=0, max_values=25, row=1)
        else:
            first = discord.ui.RoleSelect(placeholder=t("variables.include_roles")[:150], min_values=0,
                                          max_values=25, row=0)
            second = None

        async def chose_first(i):
            self.include = [item.id for item in first.values]
            await self.show(i)

        first.callback = chose_first
        self.add_item(first)
        if second is not None:
            async def chose_second(i):
                self.exclude = [item.id for item in second.values]
                await self.show(i)

            second.callback = chose_second
            self.add_item(second)

    def result(self):
        parts = [str(i) for i in self.include] + [f"-{i}" for i in self.exclude]
        return "{" + self.name + (":" + ",".join(parts) if parts else "") + "}"

    async def show(self, interaction):
        what = [f"<#{i}>" if self.kind == "channels" else f"<@&{i}>" for i in self.include]
        without = [f"<#{i}>" for i in self.exclude]
        await interaction.response.edit_message(embed=panel_embed(
            interaction, "variables.result", hints=False, code=self.result(),
            include=", ".join(what) or t("variables.everything"), exclude=", ".join(without) or "—"), view=self)
