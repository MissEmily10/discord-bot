"""
Библиотека переменных: что можно писать в текстах ({voice}, {role:…}, …),
как задаются аргументы и как бот их считает.

Запись: {имя} или {имя:аргумент}. Аргументы-списки — через запятую.
Каналы и роли можно указывать как угодно: ID, <#упоминание>, имя («Клан»).
Минус перед элементом — исключить: {voice:-AFK} — все войсы, кроме AFK.
Категория = все её каналы: {voice:Игры,-Игры-тихая}.

Здесь — разбор аргументов (каналы, роли, даты) и панель «Переменные»
(поиск, разделы, карточка, конструктор). Сам каталог — variable_catalog.py,
значения считает live.collect, подставляет actions.substitute.
"""

import random
import re
from datetime import datetime

import discord

WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


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


# ============================================================
# ПАНЕЛЬ: «Переменные» — разделы, поиск, карточка, конструктор
# ============================================================

import core  # noqa: E402
from core import PanelView, Modal, t, panel_embed  # noqa: E402


def _catalog():
    import variable_catalog
    return variable_catalog


def library_embed(interaction):
    vc = _catalog()
    import activity
    sections = "\n".join(f"▸ **{name}** — {sum(1 for item in vc.VARS if item.section == name)}"
                          for name in vc.SECTIONS)
    content = t("variables.content_on") if (interaction.client.intents.message_content or activity.content_seen) \
        else t("variables.content_off")
    return panel_embed(interaction, "variables.home", count=len(vc.VARS), sections=sections, content=content)


def list_lines(items):
    return "\n".join(f"`{{{item.name}}}` — {item.title}" for item in items)[:3800]


def var_options(items):
    return [discord.SelectOption(label=("{" + item.name + "}")[:100], description=item.title[:100], value=item.name)
            for item in items]


class VariablesView(PanelView):
    texts = "variables.home"

    def __init__(self, back_target=None):
        super().__init__(back_target=back_target)
        vc = _catalog()
        select = discord.ui.Select(placeholder=t("variables.pick_section")[:150], row=0, options=[
            discord.SelectOption(label=name[:100], value=name,
                                 description=t("variables.section_count",
                                               n=sum(1 for item in vc.VARS if item.section == name))[:100])
            for name in vc.SECTIONS])

        async def picked(i):
            items = [item for item in vc.VARS if item.section == select.values[0]]
            await show_list(i, select.values[0], items, back_target=(core.screen(i), self))

        select.callback = picked
        self.add_item(select)

    @discord.ui.button(label="Найти", emoji="🔍", style=discord.ButtonStyle.primary, row=1)
    async def find(self, interaction, button):
        await interaction.response.send_modal(SearchModal(back_target=(core.screen(interaction), self)))


async def show_list(interaction, title, items, back_target):
    embed = panel_embed(interaction, "variables.list", hints=False, title=title,
                        rows=list_lines(items) or t("variables.nothing_found"), count=len(items))
    view = PanelView(back_target=back_target, timeout=900)
    if items:
        select = core.PagedSelect(placeholder=t("variables.open")[:150], options=var_options(items))

        async def picked(i):
            await open_card(i, select.values[0], back_target=(core.screen(i), view))

        select.on_pick = picked
        view.add_item(select)
    if interaction.response.is_done():
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        await interaction.response.edit_message(embed=embed, view=view)


class SearchModal(Modal, title="ПОИСК"):
    def __init__(self, back_target):
        super().__init__(title=t("variables.search_modal.title")[:45])
        self.back_target = back_target
        self.query = discord.ui.TextInput(label=t("variables.search_modal.query")[:45], max_length=60,
                                          placeholder=t("variables.search_modal.placeholder")[:100])
        self.add_item(self.query)

    async def on_submit(self, interaction):
        items = _catalog().search(self.query.value)
        await show_list(interaction, t("variables.found", query=self.query.value[:40]), items, self.back_target)


async def preview(interaction, text):
    """Значение примера прямо сейчас на этом сервере."""
    import live
    from actions import VARIABLE, substitute
    needed = {(m[1], m[2]) for m in VARIABLE.finditer(text)}
    values = await live.collect(interaction.guild, needed, user=interaction.user)
    return substitute(text, values)


def card_embed(interaction, item, previews):
    vc = _catalog()
    examples = "\n".join(f"`{example}` → {value}" for example, value in previews)[:1800]
    needs = f"\n\n⚠️ {vc.NEEDS_TEXT[item.needs]}" if item.needs else ""
    return panel_embed(interaction, "variables.card", hints=False, title=item.title,
                       name=item.name, about=item.about, args=item.args or t("variables.no_args"),
                       examples=examples, needs=needs, section=item.section)


async def open_card(interaction, name, back_target=None, new_message=False):
    vc = _catalog()
    item = vc.BY_NAME.get(name)
    if item is None:
        await interaction.response.send_message(t("variables.nothing_found"), ephemeral=True)
        return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True) if new_message else await interaction.response.defer()
    previews = []
    for example in item.examples[:4]:
        try:
            value = await preview(interaction, example)
        except Exception:
            value = "—"
        value = str(value).replace("\n", " · ")
        previews.append((example, (value[:150] + "…") if len(value) > 150 else value))
    view = CardView(item, back_target=back_target)
    embed = card_embed(interaction, item, previews)
    if new_message:
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.edit_original_response(embed=embed, view=view)


class CardView(PanelView):
    texts = "variables.card"

    def __init__(self, item, back_target=None):
        super().__init__(back_target=back_target, timeout=900)
        self.item = item
        if not item.builder:
            self.remove_item(self.build)

    @discord.ui.button(label="Собрать", emoji="🧩", style=discord.ButtonStyle.primary)
    async def build(self, interaction, button):
        kind = self.item.builder
        back = (core.screen(interaction), self)
        if kind == "word":
            await interaction.response.send_modal(WordModal(self.item, back))
            return
        view = BuildView(self.item, back_target=back)
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "variables.build", hints=False, name=self.item.name,
                              help=t(f"variables.build_help.{kind}")), view=view)


PERIOD_OPTIONS = (("всё", "за всё время"), ("сегодня", "сегодня"), ("вчера", "вчера"), ("неделя", "за 7 дней"),
                  ("месяц", "за 30 дней"))


class BuildView(PanelView):
    """Конструктор: каналы/роли — списками Discord (бот ставит ID — не ломаются при переименовании), период, топ."""

    def __init__(self, item, back_target=None):
        super().__init__(back_target=back_target, timeout=900)
        self.item = item
        self.include, self.exclude, self.period, self.top = [], [], "всё", None
        kind = item.builder
        if kind in ("channels", "voice"):
            types = ([discord.ChannelType.voice, discord.ChannelType.stage_voice, discord.ChannelType.category]
                     if kind == "voice" else
                     [discord.ChannelType.text, discord.ChannelType.news, discord.ChannelType.category,
                      discord.ChannelType.voice])
            first = discord.ui.ChannelSelect(placeholder=t("variables.include_channels")[:150], channel_types=types,
                                             min_values=0, max_values=25, row=0)
            second = discord.ui.ChannelSelect(placeholder=t("variables.exclude_channels")[:150], channel_types=types,
                                              min_values=0, max_values=25, row=1)
            first.callback = self._set("include", first)
            second.callback = self._set("exclude", second)
            self.add_item(first)
            self.add_item(second)
        if kind == "roles":
            roles = discord.ui.RoleSelect(placeholder=t("variables.include_roles")[:150], min_values=0,
                                          max_values=25, row=0)
            roles.callback = self._set("include", roles)
            self.add_item(roles)
        if "период" in item.args:
            period = discord.ui.Select(placeholder=t("variables.pick_period")[:150], row=2, options=[
                discord.SelectOption(label=label, value=value) for value, label in PERIOD_OPTIONS])

            async def chose_period(i):
                self.period = period.values[0]
                await self.show(i)

            period.callback = chose_period
            self.add_item(period)
        if "число мест" in item.args:
            top = discord.ui.Select(placeholder=t("variables.pick_top")[:150], row=3, options=[
                discord.SelectOption(label=t("variables.top_n", n=n), value=str(n)) for n in (1, 3, 5, 10, 15, 25)])

            async def chose_top(i):
                self.top = int(top.values[0])
                await self.show(i)

            top.callback = chose_top
            self.add_item(top)

    def _set(self, field, select):
        async def callback(i):
            setattr(self, field, [item.id for item in select.values])
            await self.show(i)
        return callback

    def result(self):
        parts = [str(i) for i in self.include] + [f"-{i}" for i in self.exclude]
        if self.top:
            parts.insert(0, str(self.top))
        if self.period != "всё":
            parts.append(self.period)
        return "{" + self.item.name + (":" + ",".join(parts) if parts else "") + "}"

    async def show(self, interaction):
        await interaction.response.defer()
        code = self.result()
        try:
            value = str(await preview(interaction, code))
        except Exception:
            value = "—"
        await interaction.edit_original_response(embed=panel_embed(
            interaction, "variables.result", hints=False, code=code, value=value[:1500]), view=self)


class WordModal(Modal, title="СЛОВО"):
    def __init__(self, item, back_target):
        super().__init__(title=t("variables.word_modal.title")[:45])
        self.item, self.back_target = item, back_target
        self.word = discord.ui.TextInput(label=t("variables.word_modal.word")[:45], max_length=40,
                                         placeholder=t("variables.word_modal.placeholder")[:100])
        self.top = discord.ui.TextInput(label=t("variables.word_modal.top")[:45], max_length=2, required=False)
        self.period = discord.ui.TextInput(label=t("variables.word_modal.period")[:45], max_length=10,
                                           required=False, placeholder="всё / сегодня / неделя / месяц")
        self.add_item(self.word)
        if "число мест" in item.args:
            self.add_item(self.top)
        self.add_item(self.period)

    async def on_submit(self, interaction):
        parts = [self.word.value.strip().replace(",", " ")]
        if self.top.value.strip().isdigit():
            parts.append(self.top.value.strip())
        if self.period.value.strip():
            parts.append(self.period.value.strip().lower())
        code = "{" + self.item.name + ":" + ",".join(parts) + "}"
        await interaction.response.defer()
        value = str(await preview(interaction, code))
        view = PanelView(back_target=self.back_target, timeout=600)
        await interaction.edit_original_response(embed=panel_embed(
            interaction, "variables.result", hints=False, code=code, value=value[:1500] + "\n\n" +
            t("variables.word_note")), view=view)


async def autocomplete(interaction, current):
    """Подсказки для /pictures переменная: — поиск по каталогу на лету."""
    from discord import app_commands
    return [app_commands.Choice(name=f"{{{item.name}}} — {item.title}"[:100], value=item.name)
            for item in _catalog().search(current)[:25]]
