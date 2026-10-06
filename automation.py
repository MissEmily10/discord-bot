"""
automation.py
=============
Message Build без ручной отправки:
- расписание: build уходит в канал/ветку/форум в заданное время, один раз
  или с повтором;
- триггеры: событие на сервере -> build уходит туда, где событие случилось
  (новая ветка/пост форума, сообщение с ключевым словом) или в заданный
  канал (новый участник);
- живое обновление по таймеру: отправленные сообщения с переменными
  ({online}, {member_count}, …) переписываются раз в N минут;
- настройки build'а: таймер обновления, родитель стиля, варианты по ролям.

Всё работает от имени того, кто настроил расписание/триггер: при каждом
запуске проверяется, что он ещё на сервере, видит build и может писать в
канал. Иначе запуск не происходит, а причина видна в списке.
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta

import discord
from discord.ext import tasks

import core
import live
from core import PanelView, Modal, t, panel_embed, get_user_level
from actions import say, build_visible
from database import (
    get_message_build, get_message_builds, get_build_settings, set_build_settings,
    add_schedule, get_schedules, get_schedule, get_due_schedules, update_schedule, delete_schedule,
    add_trigger, get_triggers, get_trigger, update_trigger, delete_trigger,
    get_builds_with_settings,
)

_log = logging.getLogger(__name__)

MIN_INTERVAL_MINUTES = 10
MIN_LIVE_MINUTES = 5
MAX_SCHEDULES_PER_BUILD = 10
MAX_TRIGGERS_PER_BUILD = 10
THREAD_DELAY = 2  # форум не даёт писать в пост, пока автор не создал первое сообщение
FAILURES_BEFORE_PAUSE = 3

EVENTS = ("member_join", "thread_create", "keyword")

_bot = None


# ============================================================
# ВРЕМЯ
# ============================================================

_UNITS = {
    "m": 1, "min": 1, "мин": 1, "м": 1,
    "h": 60, "ч": 60, "час": 60,
    "d": 1440, "д": 1440, "дн": 1440, "день": 1440, "дня": 1440, "дней": 1440,
    "w": 10080, "н": 10080, "нед": 10080, "неделя": 10080, "недели": 10080,
}
_DURATION = re.compile(r"^(\d{1,4})\s*([a-zа-яё]+)$")


def parse_duration(text):
    """«30m», «2 ч», «1д», «1 неделя» -> минуты или None."""
    match = _DURATION.match((text or "").strip().lower())
    if not match:
        return None
    unit = match[2]
    for name in sorted(_UNITS, key=len, reverse=True):
        if unit.startswith(name):
            return int(match[1]) * _UNITS[name]
    return None


def parse_when(text, now=None):
    """
    Когда отправить -> datetime (в TIMEZONE) или None.
    «18:30» — сегодня (или завтра, если уже прошло), «25.12 18:30»,
    «25.12.2026 18:30», «+30m», «через 2 ч».
    """
    now = now or live.now_local()
    text = (text or "").strip().lower()
    relative = re.sub(r"^(\+|через)\s*", "", text)
    if relative != text:
        minutes = parse_duration(relative)
        return now + timedelta(minutes=minutes) if minutes else None
    match = re.match(r"^(?:(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\s+)?(\d{1,2})[:.](\d{2})$", text)
    if not match:
        return None
    day, month, year, hour, minute = match.groups()
    try:
        if day:
            result = now.replace(year=int(year or now.year), month=int(month), day=int(day),
                                 hour=int(hour), minute=int(minute), second=0, microsecond=0)
            if not year and result <= now:
                result = result.replace(year=now.year + 1)
        else:
            result = now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
            if result <= now:
                result += timedelta(days=1)
    except ValueError:
        return None
    return result if result > now else None


def parse_interval(text):
    """Повтор: пусто/«нет»/0 -> 0, иначе минуты (не меньше MIN_INTERVAL_MINUTES) или None."""
    text = (text or "").strip().lower()
    if text in ("", "0", "нет", "no", "-", "никогда", "один раз"):
        return 0
    minutes = parse_duration(text)
    if minutes is None or minutes < MIN_INTERVAL_MINUTES:
        return None
    return minutes


def interval_text(minutes):
    if not minutes:
        return t("automation.once")
    for size, key in ((10080, "automation.every_weeks"), (1440, "automation.every_days"), (60, "automation.every_hours")):
        if minutes % size == 0:
            return t(key, n=minutes // size)
    return t("automation.every_minutes", n=minutes)


# ============================================================
# ПРАВА
# ============================================================

def can_automate(interaction, row):
    """Настраивать автоматику build'а: уровень staff+ и право править этот build."""
    from embed_module import can_manage_build
    level_ok = core.level_value(get_user_level(interaction)) >= core.level_value("staff")
    return level_ok and can_manage_build(interaction, row)


async def _build_row(interaction, build_id):
    row = get_message_build(build_id)
    if not row or row[1] != interaction.guild.id:
        await say(interaction, "embed.build_not_found")
        return None
    if not can_automate(interaction, row):
        await say(interaction, "automation.denied")
        return None
    return row


# ============================================================
# ЦИКЛ: расписание и таймеры живых сообщений
# ============================================================

_live_last = {}  # build_id -> ts последнего обновления по таймеру


@tasks.loop(seconds=30)
async def _tick():
    now = int(time.time())
    for schedule in get_due_schedules(now):
        try:
            await run_schedule(schedule, now)
        except Exception:
            _log.exception("schedule %s failed", schedule[0])
    for build_id, guild_id, settings in get_builds_with_settings():
        minutes = settings.get("live_minutes") or 0
        if minutes < MIN_LIVE_MINUTES or now - _live_last.get(build_id, 0) < minutes * 60:
            continue
        _live_last[build_id] = now
        guild = _bot.get_guild(guild_id)
        if guild is not None:
            try:
                await live.resync_build(guild, build_id, with_children=False)
            except Exception:
                _log.exception("live timer failed for build %s", build_id)


@_tick.before_loop
async def _wait_ready():
    await _bot.wait_until_ready()


def _next_run(schedule, now):
    interval = schedule[6]
    if not interval:
        return None
    next_run = schedule[5]
    while next_run <= now:  # бот был выключен — пропущенные запуски не досылаем пачкой
        next_run += interval * 60
    return next_run


async def run_schedule(schedule, now):
    schedule_id, guild_id, build_id, owner_id, channel_id = schedule[:5]
    guild = _bot.get_guild(guild_id)
    if guild is None:
        return  # бот не видит сервер (ещё не загрузился или удалён) — попробуем позже
    row = get_message_build(build_id)
    channel = guild.get_channel_or_thread(channel_id)
    author = live.automation_author(guild, owner_id, row)
    error = None
    if row is None:
        error = t("embed.build_not_found")
    elif channel is None:
        error = t("embed.send.channel_missing", channel=f"<#{channel_id}>")
    elif author is None:
        error = t("automation.error.author")
    else:
        _, error = await live.deliver(guild, author, build_id, channel)
    next_run = _next_run(schedule, now)
    fields = {"last_run": now, "last_error": error}
    if next_run is None:
        fields["enabled"] = 0
    else:
        fields["next_run"] = next_run
    if error and (row is None or channel is None):
        fields["enabled"] = 0  # чинить нечего — выключаем
    update_schedule(schedule_id, **fields)


# ============================================================
# ТРИГГЕРЫ
# ============================================================

_cooldowns = {}  # (trigger_id, channel_id) -> ts


async def fire_trigger(trigger, guild, channel, user=None):
    trigger_id, _, build_id, owner_id = trigger[:4]
    cooldown = trigger[8] or 0
    key = (trigger_id, channel.id)
    now = time.time()
    if now - _cooldowns.get(key, 0) < cooldown:
        return
    _cooldowns[key] = now
    row = get_message_build(build_id)
    author = live.automation_author(guild, owner_id, row)
    if row is None or author is None:
        update_trigger(trigger_id, last_error=t("automation.error.author") if row else t("embed.build_not_found"))
        return
    _, error = await live.deliver(guild, author, build_id, channel, user=user)
    update_trigger(trigger_id, last_fired=int(now), last_error=error)


def _watches(trigger, channel):
    watch = trigger[6]
    return watch is None or watch in (channel.id, getattr(channel, "parent_id", None))


async def on_member_join(member):
    for trigger in get_triggers(guild_id=member.guild.id, event="member_join"):
        channel = member.guild.get_channel_or_thread(trigger[7] or 0)
        if channel is not None:
            await fire_trigger(trigger, member.guild, channel, user=member)


async def on_thread_create(thread):
    triggers = [tr for tr in get_triggers(guild_id=thread.guild.id, event="thread_create") if _watches(tr, thread)]
    if not triggers:
        return
    await asyncio.sleep(THREAD_DELAY)
    owner = thread.guild.get_member(thread.owner_id) if thread.owner_id else None
    if owner is not None and owner.bot:
        return  # посты самого бота (расписание в форум) не должны запускать триггеры
    for trigger in triggers:
        await fire_trigger(trigger, thread.guild, thread, user=owner)


def keyword_matches(pattern, content):
    content = (content or "").lower()
    words = [word.strip().lower() for word in (pattern or "").split(",") if word.strip()]
    return any(word in content for word in words)


async def on_message(message):
    if message.guild is None or message.author.bot or message.webhook_id:
        return
    for trigger in get_triggers(guild_id=message.guild.id, event="keyword"):
        if _watches(trigger, message.channel) and keyword_matches(trigger[5], message.content):
            await fire_trigger(trigger, message.guild, message.channel, user=message.author)


# ============================================================
# UI: общий выбор build'а
# ============================================================

def build_options(interaction, exclude=()):
    rows = [
        row for row in get_message_builds(interaction.guild.id)
        if row[0] not in exclude and build_visible(interaction, get_message_build(row[0]))
    ][:25]
    return [
        discord.SelectOption(label=f"{(name or t('embed.default_name'))[:90]} · #{bid}", value=str(bid))
        for bid, owner, name, *_ in rows
    ]


def _when(ts):
    return f"<t:{ts}:f>" if ts else "—"


def _status(enabled, error):
    if error:
        return t("automation.status_error", error=str(error)[:150])
    return t("automation.status_on") if enabled else t("automation.status_off")


# ============================================================
# UI: расписание
# ============================================================

def schedules_embed(interaction, build_id):
    rows = get_schedules(build_id=build_id)
    lines = [
        t("automation.schedule.line", id=row[0], channel=f"<#{row[4]}>", when=_when(row[5]) if row[7] else "—",
          repeat=interval_text(row[6]), status=_status(row[7], row[9]))
        for row in rows
    ]
    return panel_embed(interaction, "automation.schedules", list="\n".join(lines) or t("automation.schedules.empty"),
                       tz=str(live.TIMEZONE))


class SchedulesView(PanelView):
    texts = "automation.schedules"

    def __init__(self, build_id, back_target):
        super().__init__(back_target=back_target)
        self.build_id = build_id
        rows = get_schedules(build_id=build_id)
        if rows:
            select = discord.ui.Select(
                placeholder=t("automation.schedules.manage")[:150], row=1,
                options=[discord.SelectOption(label=t("automation.schedule.option", id=r[0], repeat=interval_text(r[6]))[:100],
                                              value=str(r[0])) for r in rows[:25]],
            )

            async def picked(i):
                if not await _build_row(i, self.build_id):
                    return
                await i.response.edit_message(
                    embed=schedules_embed(i, self.build_id),
                    view=ScheduleItemView(self.build_id, int(select.values[0]), back_target=self.back_target),
                )

            select.callback = picked
            self.add_item(select)

    @discord.ui.button(label="Добавить", emoji="➕", style=discord.ButtonStyle.success)
    async def add(self, interaction, button):
        if not await _build_row(interaction, self.build_id):
            return
        if len(get_schedules(build_id=self.build_id)) >= MAX_SCHEDULES_PER_BUILD:
            await say(interaction, "automation.too_many", max=MAX_SCHEDULES_PER_BUILD)
            return
        from embed_module import SEND_CHANNEL_TYPES
        view = PanelView(back_target=(interaction.message.embeds[0], self), timeout=300)
        select = discord.ui.ChannelSelect(placeholder=t("automation.pick_channel")[:150], channel_types=SEND_CHANNEL_TYPES)

        async def picked(i):
            await i.response.send_modal(ScheduleModal(self.build_id, select.values[0].id, self))

        select.callback = picked
        view.add_item(select)
        await interaction.response.edit_message(embed=panel_embed(interaction, "automation.schedule_channel"), view=view)


class ScheduleModal(Modal, title="РАСПИСАНИЕ"):
    texts = "automation.schedule_modal"

    when_input = discord.ui.TextInput(label="Когда", max_length=40, placeholder="18:30 · 25.12 18:30 · +2ч")
    repeat_input = discord.ui.TextInput(label="Повтор", required=False, max_length=20, placeholder="нет · 1д · 1 неделя")

    def __init__(self, build_id, channel_id, schedules_view):
        super().__init__()
        self.build_id = build_id
        self.channel_id = channel_id
        self.schedules_view = schedules_view

    async def on_submit(self, interaction):
        row = await _build_row(interaction, self.build_id)
        if not row:
            return
        when = parse_when(self.when_input.value)
        if when is None:
            await say(interaction, "automation.bad_when")
            return
        interval = parse_interval(self.repeat_input.value)
        if interval is None:
            await say(interaction, "automation.bad_interval", min=MIN_INTERVAL_MINUTES)
            return
        channel = interaction.guild.get_channel_or_thread(self.channel_id)
        if channel is None:
            await say(interaction, "embed.send.channel_missing", channel=f"<#{self.channel_id}>")
            return
        problem = live.can_post(interaction.user, channel)
        if problem:
            await say(interaction, problem, channel=channel.mention)
            return
        schedule_id = add_schedule(interaction.guild.id, self.build_id, interaction.user.id, channel.id,
                                   int(when.timestamp()), interval)
        core.audit(interaction, "schedule.created", "build", self.build_id,
                   f"schedule={schedule_id} channel={channel.id} at={int(when.timestamp())} every={interval}")
        await interaction.response.edit_message(
            embed=schedules_embed(interaction, self.build_id),
            view=SchedulesView(self.build_id, back_target=self.schedules_view.back_target),
        )


class ScheduleItemView(PanelView):
    texts = "automation.schedule_item"

    def __init__(self, build_id, schedule_id, back_target):
        super().__init__(back_target=None)
        self.build_id = build_id
        self.schedule_id = schedule_id
        self.list_back = back_target

    async def _back(self, interaction):
        await interaction.response.edit_message(
            embed=schedules_embed(interaction, self.build_id),
            view=SchedulesView(self.build_id, back_target=self.list_back),
        )

    def _row(self):
        row = get_schedule(self.schedule_id)
        return row if row and row[2] == self.build_id else None

    @discord.ui.button(label="Пауза / включить", emoji="⏯️", style=discord.ButtonStyle.secondary)
    async def toggle(self, interaction, button):
        if not await _build_row(interaction, self.build_id):
            return
        row = self._row()
        if row:
            fields = {"enabled": 0 if row[7] else 1, "last_error": None}
            if not row[7] and row[5] <= int(time.time()):
                # включаем просроченное: следующий повтор, а разовое — через минуту
                fields["next_run"] = _next_run(row, int(time.time())) or int(time.time()) + 60
            update_schedule(self.schedule_id, **fields)
        await self._back(interaction)

    @discord.ui.button(label="Удалить", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def remove(self, interaction, button):
        if not await _build_row(interaction, self.build_id):
            return
        if self._row():
            delete_schedule(self.schedule_id)
            core.audit(interaction, "schedule.deleted", "build", self.build_id, f"schedule={self.schedule_id}")
        await self._back(interaction)

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        await self._back(interaction)


# ============================================================
# UI: триггеры
# ============================================================

def _trigger_text(row):
    event, pattern, watch, target = row[4], row[5], row[6], row[7]
    if event == "member_join":
        where = t("automation.trigger.member_join", channel=f"<#{target}>")
    elif event == "thread_create":
        where = t("automation.trigger.thread_create", channel=f"<#{watch}>" if watch else t("automation.anywhere"))
    else:
        where = t("automation.trigger.keyword", words=pattern or "—",
                  channel=f"<#{watch}>" if watch else t("automation.anywhere"))
    return t("automation.trigger.line", id=row[0], what=where, status=_status(row[9], row[11]))


def triggers_embed(interaction, build_id):
    rows = get_triggers(build_id=build_id)
    text = "\n".join(_trigger_text(row) for row in rows) or t("automation.triggers.empty")
    return panel_embed(interaction, "automation.triggers", list=text)


class TriggersView(PanelView):
    texts = "automation.triggers"

    def __init__(self, build_id, back_target):
        super().__init__(back_target=back_target)
        self.build_id = build_id
        rows = get_triggers(build_id=build_id)
        if rows:
            select = discord.ui.Select(
                placeholder=t("automation.triggers.manage")[:150], row=1,
                options=[discord.SelectOption(label=t("automation.trigger.option", id=r[0], event=t(f"automation.event.{r[4]}"))[:100],
                                              value=str(r[0])) for r in rows[:25]],
            )

            async def picked(i):
                if not await _build_row(i, self.build_id):
                    return
                await i.response.edit_message(
                    embed=triggers_embed(i, self.build_id),
                    view=TriggerItemView(self.build_id, int(select.values[0]), back_target=self.back_target),
                )

            select.callback = picked
            self.add_item(select)

    async def _start(self, interaction):
        if not await _build_row(interaction, self.build_id):
            return False
        if len(get_triggers(build_id=self.build_id)) >= MAX_TRIGGERS_PER_BUILD:
            await say(interaction, "automation.too_many", max=MAX_TRIGGERS_PER_BUILD)
            return False
        return True

    async def _show_list(self, interaction):
        await interaction.response.edit_message(
            embed=triggers_embed(interaction, self.build_id),
            view=TriggersView(self.build_id, back_target=self.back_target),
        )

    async def _channel_step(self, interaction, screen, channel_types, on_pick, here_label=None):
        view = PanelView(back_target=(interaction.message.embeds[0], self), timeout=300)
        select = discord.ui.ChannelSelect(placeholder=t("automation.pick_channel")[:150], channel_types=channel_types)

        async def picked(i):
            await on_pick(i, i.guild.get_channel_or_thread(select.values[0].id))

        select.callback = picked
        view.add_item(select)
        if here_label:
            # контекст: канал/ветка, где открыта панель
            here = discord.ui.Button(label=t(here_label)[:80], style=discord.ButtonStyle.primary)

            async def pick_here(i):
                await on_pick(i, i.channel)

            here.callback = pick_here
            view.add_item(here)
        await interaction.response.edit_message(embed=panel_embed(interaction, screen), view=view)

    @discord.ui.button(label="Новый участник", emoji="👋", style=discord.ButtonStyle.success)
    async def member_join(self, interaction, button):
        if not await self._start(interaction):
            return
        from embed_module import SEND_CHANNEL_TYPES

        async def on_pick(i, channel):
            if not await self._create(i, "member_join", target=channel):
                return
            await self._show_list(i)

        await self._channel_step(interaction, "automation.trigger_target", SEND_CHANNEL_TYPES, on_pick, "automation.here")

    @discord.ui.button(label="Новая ветка / пост", emoji="🧵", style=discord.ButtonStyle.success)
    async def thread_create(self, interaction, button):
        if not await self._start(interaction):
            return
        types = [discord.ChannelType.forum, discord.ChannelType.text, discord.ChannelType.news]

        async def on_pick(i, channel):
            if not await self._create(i, "thread_create", watch=channel):
                return
            await self._show_list(i)

        await self._channel_step(interaction, "automation.trigger_watch_threads", types, on_pick)

    @discord.ui.button(label="Ключевое слово", emoji="💬", style=discord.ButtonStyle.success)
    async def keyword(self, interaction, button):
        if not await self._start(interaction):
            return
        await interaction.response.send_modal(KeywordModal(self))

    async def _create(self, interaction, event, *, target=None, watch=None, pattern=None, cooldown=30):
        row = await _build_row(interaction, self.build_id)
        if not row:
            return False
        check = target or watch
        if check is None:
            await say(interaction, "embed.send.channel_missing", channel="?")
            return False
        # Проверяем право писать туда, куда build будет уходить (для веток — в родителя).
        problem = live.can_post(interaction.user, check) if not isinstance(check, discord.ForumChannel) else None
        if problem:
            await say(interaction, problem, channel=check.mention)
            return False
        trigger_id = add_trigger(
            interaction.guild.id, self.build_id, interaction.user.id, event, pattern=pattern,
            watch_channel_id=watch.id if watch else None, target_channel_id=target.id if target else None,
            cooldown_seconds=cooldown,
        )
        core.audit(interaction, "trigger.created", "build", self.build_id, f"trigger={trigger_id} event={event}")
        return True


class KeywordModal(Modal, title="КЛЮЧЕВОЕ СЛОВО"):
    texts = "automation.keyword_modal"

    words_input = discord.ui.TextInput(label="Слова через запятую", max_length=200)
    cooldown_input = discord.ui.TextInput(label="Пауза между ответами, сек", required=False, max_length=5, default="60")

    def __init__(self, triggers_view):
        super().__init__()
        self.triggers_view = triggers_view

    async def on_submit(self, interaction):
        words = ", ".join(w.strip() for w in self.words_input.value.split(",") if w.strip())
        if not words:
            await say(interaction, "automation.keyword.empty")
            return
        raw = self.cooldown_input.value.strip() or "60"
        if not raw.isdigit() or int(raw) < 10:
            await say(interaction, "automation.keyword.bad_cooldown")
            return
        view = KeywordScopeView(self.triggers_view, words, int(raw))
        await interaction.response.edit_message(embed=panel_embed(interaction, "automation.keyword_scope", words=words), view=view)


class KeywordScopeView(PanelView):
    """Где слушать: только там, где открыта панель (контекст), или весь сервер."""

    texts = "automation.keyword_scope"

    def __init__(self, triggers_view, words, cooldown):
        super().__init__(back_target=None)
        self.triggers_view = triggers_view
        self.words = words
        self.cooldown = cooldown

    @discord.ui.button(label="Только этот канал", emoji="📍", style=discord.ButtonStyle.primary)
    async def here(self, interaction, button):
        if await self.triggers_view._create(interaction, "keyword", watch=interaction.channel,
                                            pattern=self.words, cooldown=self.cooldown):
            await self.triggers_view._show_list(interaction)

    @discord.ui.button(label="Весь сервер", emoji="🌐", style=discord.ButtonStyle.secondary)
    async def everywhere(self, interaction, button):
        view = self.triggers_view
        row = await _build_row(interaction, view.build_id)
        if not row:
            return
        trigger_id = add_trigger(interaction.guild.id, view.build_id, interaction.user.id, "keyword",
                                 pattern=self.words, cooldown_seconds=self.cooldown)
        core.audit(interaction, "trigger.created", "build", view.build_id, f"trigger={trigger_id} event=keyword")
        await view._show_list(interaction)


class TriggerItemView(PanelView):
    texts = "automation.trigger_item"

    def __init__(self, build_id, trigger_id, back_target):
        super().__init__(back_target=None)
        self.build_id = build_id
        self.trigger_id = trigger_id
        self.list_back = back_target

    async def _back(self, interaction):
        await interaction.response.edit_message(
            embed=triggers_embed(interaction, self.build_id),
            view=TriggersView(self.build_id, back_target=self.list_back),
        )

    def _row(self):
        row = get_trigger(self.trigger_id)
        return row if row and row[2] == self.build_id else None

    @discord.ui.button(label="Пауза / включить", emoji="⏯️", style=discord.ButtonStyle.secondary)
    async def toggle(self, interaction, button):
        if not await _build_row(interaction, self.build_id):
            return
        row = self._row()
        if row:
            update_trigger(self.trigger_id, enabled=0 if row[9] else 1, last_error=None)
        await self._back(interaction)

    @discord.ui.button(label="Удалить", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def remove(self, interaction, button):
        if not await _build_row(interaction, self.build_id):
            return
        if self._row():
            delete_trigger(self.trigger_id)
            core.audit(interaction, "trigger.deleted", "build", self.build_id, f"trigger={self.trigger_id}")
        await self._back(interaction)

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        await self._back(interaction)


# ============================================================
# UI: настройки build'а (живое обновление, родитель, варианты)
# ============================================================

def settings_embed(interaction, build_id):
    settings = get_build_settings(build_id)
    minutes = settings.get("live_minutes") or 0
    parent = get_message_build(settings.get("parent_id") or 0)
    variants = []
    for variant in settings.get("variants") or []:
        target = get_message_build(variant.get("build_id") or 0)
        who = t("automation.variant.level", level=variant["level"]) if variant.get("level") else \
            ", ".join(f"<@&{rid}>" for rid in variant.get("roles") or [])
        variants.append(t("automation.variant.line", who=who, build=f"{target[3] if target else '?'} #{variant.get('build_id')}"))
    return panel_embed(
        interaction, "automation.settings",
        live=t("automation.settings.live_on", n=minutes) if minutes else t("automation.settings.live_off"),
        parent=f"{parent[3]} #{parent[0]}" if parent else t("automation.settings.no_parent"),
        variants="\n".join(variants) or t("automation.settings.no_variants"),
        variables=live.VARIABLE_HELP,
    )


class BuildSettingsView(PanelView):
    texts = "automation.settings"

    def __init__(self, build_id, back_target):
        super().__init__(back_target=back_target)
        self.build_id = build_id

    async def show(self, interaction):
        await interaction.response.edit_message(
            embed=settings_embed(interaction, self.build_id),
            view=BuildSettingsView(self.build_id, back_target=self.back_target),
        )

    def _update(self, **changes):
        settings = get_build_settings(self.build_id)
        for key, value in changes.items():
            if value in (None, 0, [], ""):
                settings.pop(key, None)
            else:
                settings[key] = value
        set_build_settings(self.build_id, settings)

    @discord.ui.button(label="Живое обновление", emoji="⏱️", style=discord.ButtonStyle.secondary)
    async def live_timer(self, interaction, button):
        if not await _build_row(interaction, self.build_id):
            return
        await interaction.response.send_modal(LiveTimerModal(self))

    @discord.ui.button(label="Родитель стиля", emoji="🧬", style=discord.ButtonStyle.secondary)
    async def parent(self, interaction, button):
        if not await _build_row(interaction, self.build_id):
            return
        from database import get_child_builds
        exclude = {self.build_id, *get_child_builds(self.build_id)}
        view = PanelView(back_target=(interaction.message.embeds[0], self), timeout=300)
        options = [discord.SelectOption(label=t("automation.settings.no_parent")[:100], value="0")] + build_options(interaction, exclude)[:24]
        select = discord.ui.Select(placeholder=t("automation.settings.pick_parent")[:150], options=options)

        async def picked(i):
            if not await _build_row(i, self.build_id):
                return
            parent_id = int(select.values[0])
            parent = get_message_build(parent_id) if parent_id else None
            if parent_id and (not parent or parent[1] != i.guild.id or not build_visible(i, parent)):
                await say(i, "embed.build_not_found")
                return
            self._update(parent_id=parent_id or None)
            core.audit(i, "build.parent_set", "build", self.build_id, f"parent={parent_id}")
            await self.show(i)

        select.callback = picked
        view.add_item(select)
        await interaction.response.edit_message(embed=panel_embed(interaction, "automation.parent"), view=view)

    @discord.ui.button(label="Вариант по роли", emoji="🎭", style=discord.ButtonStyle.secondary)
    async def add_variant(self, interaction, button):
        if not await _build_row(interaction, self.build_id):
            return
        if len(get_build_settings(self.build_id).get("variants") or []) >= 10:
            await say(interaction, "automation.too_many", max=10)
            return
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "automation.variant"),
            view=VariantConditionView(self, back_target=(interaction.message.embeds[0], self)),
        )

    @discord.ui.button(label="Убрать варианты", emoji="🧹", style=discord.ButtonStyle.danger)
    async def clear_variants(self, interaction, button):
        if not await _build_row(interaction, self.build_id):
            return
        self._update(variants=None)
        await self.show(interaction)


class LiveTimerModal(Modal, title="ЖИВОЕ ОБНОВЛЕНИЕ"):
    texts = "automation.live_modal"

    minutes_input = discord.ui.TextInput(label="Раз в сколько минут (0 — выключить)", max_length=5)

    def __init__(self, settings_view):
        super().__init__()
        self.settings_view = settings_view
        self.minutes_input.default = str(get_build_settings(settings_view.build_id).get("live_minutes") or 0)

    async def on_submit(self, interaction):
        if not await _build_row(interaction, self.settings_view.build_id):
            return
        raw = self.minutes_input.value.strip()
        if not raw.isdigit() or (0 < int(raw) < MIN_LIVE_MINUTES):
            await say(interaction, "automation.live.bad", min=MIN_LIVE_MINUTES)
            return
        self.settings_view._update(live_minutes=int(raw))
        await self.settings_view.show(interaction)


class VariantConditionView(PanelView):
    """Кому показывать вариант: уровень доступа или роли."""

    texts = "automation.variant"

    def __init__(self, settings_view, back_target):
        super().__init__(back_target=back_target)
        self.settings_view = settings_view
        roles = discord.ui.RoleSelect(placeholder=t("automation.variant.roles")[:150], min_values=1, max_values=10, row=1)

        async def picked(i):
            await self._pick_build(i, {"roles": [role.id for role in roles.values]})

        roles.callback = picked
        self.add_item(roles)

    async def _pick_build(self, interaction, condition):
        build_id = self.settings_view.build_id
        view = PanelView(back_target=(interaction.message.embeds[0], self), timeout=300)
        options = build_options(interaction, exclude={build_id})
        if not options:
            await say(interaction, "embed.build_pick.none")
            return
        select = discord.ui.Select(placeholder=t("automation.variant.pick_build")[:150], options=options)

        async def picked(i):
            if not await _build_row(i, build_id):
                return
            target = get_message_build(int(select.values[0]))
            if not target or target[1] != i.guild.id or not build_visible(i, target):
                await say(i, "embed.build_not_found")
                return
            variants = list(get_build_settings(build_id).get("variants") or [])
            variants.append({**condition, "build_id": target[0]})
            self.settings_view._update(variants=variants)
            core.audit(i, "build.variant_added", "build", build_id, json_safe(condition))
            await self.settings_view.show(i)

        select.callback = picked
        view.add_item(select)
        await interaction.response.edit_message(embed=panel_embed(interaction, "automation.variant_build"), view=view)

    @discord.ui.button(label="Staff и выше", style=discord.ButtonStyle.primary)
    async def staff(self, interaction, button):
        await self._pick_build(interaction, {"level": "staff"})

    @discord.ui.button(label="Admin и выше", style=discord.ButtonStyle.primary)
    async def admin(self, interaction, button):
        await self._pick_build(interaction, {"level": "admin"})


def json_safe(value):
    import json
    return json.dumps(value, ensure_ascii=False)


# ============================================================
# РЕГИСТРАЦИЯ
# ============================================================

def setup(bot):
    """Из setup_hook: слушатели событий и цикл расписания."""
    global _bot
    _bot = bot
    live.setup(bot)
    bot.add_listener(on_member_join, "on_member_join")
    bot.add_listener(on_thread_create, "on_thread_create")
    bot.add_listener(on_message, "on_message")
    if not _tick.is_running():
        _tick.start()
