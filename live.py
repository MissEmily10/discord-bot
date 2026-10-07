"""
live.py
=======
Как Message Build попадает в Discord и остаётся актуальным:
- переменные {member_count}, {online}, {counter:имя}, {last_submission}, …;
- deliver — единая отправка в канал, ветку или форум (новый пост) с
  проверкой прав автора; ей пользуются ручная отправка, расписание и триггеры;
- resync_build — привести уже отправленные сообщения к текущему виду
  (вместе с «детьми», которые наследуют стиль);
- request_refresh — отложенное обновление build'ов, которые используют
  изменившиеся данные (счётчик, заявки): несколько событий подряд
  склеиваются в одно обновление.
"""

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone

import discord

import core
from core import t
from actions import (
    load_source, message_parts, used_variables, member_can_view, _json,
)
from database import (
    get_message_build, get_message_builds, get_sent_instances, save_sent_instance,
    delete_sent_instance, get_counters, get_submission_stats, get_child_builds,
)

_log = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
    TIMEZONE = ZoneInfo(os.getenv("TIMEZONE") or "Europe/Moscow")
except Exception:  # нет tzdata или неверное имя — работаем в UTC
    TIMEZONE = timezone.utc

ONLINE_CACHE_SECONDS = 300
# Переменные, которые зависят от того, КТО нажал или на ком сработал триггер:
# такие сообщения не обновляются потом «для всех».
PERSONAL_VARIABLES = {"user", "user_name"}
SUBMISSION_VARIABLES = {"submissions", "pending", "approved", "rejected", "last_submission"}

VARIABLE_HELP = (
    "{server} {member_count} {online} {boosts} {boost_level} {date} {time} "
    "{role:ID} {counter:имя} {submissions} {pending} {approved} {rejected} "
    "{submissions:ID_формы} {last_submission} {user} {user_name}"
)

_online_cache = {}  # guild_id -> (ts, value)


def now_local():
    return datetime.now(TIMEZONE)


async def _online(guild):
    cached = _online_cache.get(guild.id)
    if cached and time.time() - cached[0] < ONLINE_CACHE_SECONDS:
        return cached[1]
    value = "?"
    try:
        # без privileged presences intent: Discord сам считает онлайн
        if _bot is not None:
            full = await _bot.fetch_guild(guild.id, with_counts=True)
            if full.approximate_presence_count is not None:
                value = full.approximate_presence_count
    except discord.HTTPException:
        pass
    _online_cache[guild.id] = (time.time(), value)
    return value


_MENTION = re.compile(r"<(@[!&]?|#)(\d+)>")


def safe_text(text):
    """Текст от участника без рабочих упоминаний: ни @everyone/@here, ни <@&роль>, ни <@id>."""
    text = discord.utils.escape_mentions(str(text))  # @everyone / @here
    return _MENTION.sub(lambda m: f"<\u200b{m[1]}{m[2]}>", text)


async def collect(guild, needed, user=None, extra=None):
    """Значения только для тех переменных, что реально используются (needed — {(имя, арг)})."""
    names = {name for name, _ in needed}
    variables = {}
    if not names:
        return variables
    variables["server"] = guild.name
    variables["member_count"] = guild.member_count or len(getattr(guild, "members", []))
    variables["boosts"] = guild.premium_subscription_count or 0
    variables["boost_level"] = guild.premium_tier
    current = now_local()
    variables["date"] = current.strftime("%d.%m.%Y")
    variables["time"] = current.strftime("%H:%M")
    if "online" in names:
        variables["online"] = await _online(guild)

    def role_count(arg):
        role = guild.get_role(int(arg)) if arg and arg.isdigit() else None
        return len(role.members) if role else None

    variables["role"] = role_count

    if "counter" in names:
        counters = get_counters(guild.id)
        variables["counter"] = lambda arg: counters.get(arg, 0) if arg else None

    if names & SUBMISSION_VARIABLES:
        cache = {}

        def stats(arg):
            key = int(arg) if arg and arg.isdigit() else None
            if key not in cache:
                cache[key] = get_submission_stats(guild.id, key)
            return cache[key]

        for name in ("submissions", "pending", "approved", "rejected"):
            field = "total" if name == "submissions" else name
            variables[name] = (lambda field: lambda arg: stats(arg)[field])(field)

        def last(arg):
            row = stats(arg)["last"]
            if not row:
                return t("live.no_submissions")
            form_name, applicant_id, created = row
            return t("live.last_submission", form=form_name or "—", user=f"<@{applicant_id}>", when=f"<t:{created}:R>")

        variables["last_submission"] = last

    if user is not None:
        variables["user"] = user.mention
        # Имя задаёт сам участник: без экранирования «<@&роль>» в нике пинговал бы
        # роль, если у автора расписания/триггера есть право на массовые упоминания.
        variables["user_name"] = safe_text(getattr(user, "display_name", None) or user.name)
    variables.update(extra or {})
    return variables


async def parts_for(guild, build_id, user=None, extra=None, data=None):
    """Части сообщения build'а с подставленными переменными. None — build'а нет."""
    data = data or load_source("b", build_id)
    if not data:
        return None
    needed = used_variables(data)
    variables = await collect(guild, needed, user=user, extra=extra) if needed else None
    return message_parts(build_id, variables, data)


def is_personal(data):
    return any(name in PERSONAL_VARIABLES for name, _ in used_variables(data))


# ============================================================
# ДОСТАВКА
# ============================================================

def allowed_mentions_for(member, channel):
    """@everyone/@here и роли — только если у отправителя есть на это право в канале."""
    can_mass = core.is_owner_id(member.id) or channel.permissions_for(member).mention_everyone
    return discord.AllowedMentions(everyone=can_mass, roles=can_mass, users=True)


def can_post(member, channel):
    """-> None или ключ ошибки. Канал, ветка или форум; проверяются и автор, и бот."""
    if member is None:
        return "live.error.author_gone"
    me = channel.guild.me
    is_thread = isinstance(channel, discord.Thread)
    for who, key in ((member, "embed.send.no_perm_user"), (me, "embed.send.no_perm_bot")):
        if who is None:
            return "embed.send.no_perm_bot"
        if who is member and core.is_owner_id(member.id):
            continue
        perms = channel.permissions_for(who)
        can_send = perms.send_messages_in_threads if is_thread else perms.send_messages
        if not (perms.view_channel and can_send):
            return key
        if who is me and not perms.embed_links:
            return key
    if is_thread and channel.archived and channel.locked:
        return "live.error.thread_locked"
    return None


async def deliver(guild, author, build_id, channel, *, user=None, extra=None, track=True, post_title=None):
    """
    Отправить build в канал/ветку/форум от имени author (Member).
    -> (первое сообщение, None) или (None, текст ошибки).
    В форуме создаётся новый пост (post_title — его название).
    """
    error = can_post(author, channel)
    if error:
        return None, t(error, channel=channel.mention)
    row = get_message_build(build_id)
    if not row or row[1] != guild.id:
        return None, t("embed.build_not_found")
    data = load_source("b", build_id)
    parts = await parts_for(guild, build_id, user=user, extra=extra, data=data)
    if not parts:
        return None, t("embed.build_empty")
    # Сообщения, где подставлен конкретный человек, нельзя потом «обновить для всех».
    track = track and not is_personal(data)
    mentions = allowed_mentions_for(author, channel)
    first, target = None, channel
    try:
        for index, part in enumerate(parts):
            if index == 0 and isinstance(channel, discord.ForumChannel):
                title = (post_title or row[3] or t("embed.default_name"))[:100]
                created = await channel.create_thread(name=title, allowed_mentions=mentions, **part)
                msg, target = created.message, created.thread
            else:
                msg = await target.send(allowed_mentions=mentions, **part)
            first = first or msg
            if track:
                save_sent_instance(build_id, msg.id, target.id, guild.id, index)
    except discord.Forbidden:
        return first, t("embed.send.forbidden", channel=channel.mention)
    except discord.HTTPException as error:
        return first, t("embed.send.http_error", channel=channel.mention, error=error)
    return first, None


# ============================================================
# ОБНОВЛЕНИЕ ОТПРАВЛЕННЫХ
# ============================================================

def _group_sends(instances):
    """
    Разбить отправленные сообщения канала на отдельные отправки.
    part_index == 0 — начало отправки; у старых записей (NULL) каждая
    запись считается отдельной отправкой из одного сообщения.
    """
    groups = []
    for message_id, part_index in instances:
        if part_index is None or part_index == 0 or not groups:
            groups.append([message_id])
        else:
            groups[-1].append(message_id)
    return groups


_locks = {}


async def resync_build(guild, build_id, with_children=True):
    """
    Привести отправленные сообщения к текущему виду build'а: в каждой отправке
    сообщения правятся по порядку, лишние удаляются, недостающие досылаются.
    -> (обновлено, удалено, дослано, пропало)
    """
    lock = _locks.setdefault(build_id, asyncio.Lock())
    async with lock:
        totals = await _resync_one(guild, build_id)
    if with_children:
        for child_id in get_child_builds(build_id):
            child = await resync_build(guild, child_id, with_children=False)
            totals = tuple(a + b for a, b in zip(totals, child))
    return totals


async def _resync_one(guild, build_id):
    parts = await parts_for(guild, build_id) or []
    by_channel = {}
    for message_id, channel_id, guild_id, sent_at, part_index in get_sent_instances(build_id):
        if guild_id == guild.id:
            by_channel.setdefault(channel_id, []).append((message_id, part_index))

    updated = removed = added = missing = 0
    for channel_id, instances in by_channel.items():
        channel = guild.get_channel_or_thread(channel_id)
        if channel is None:
            # Архивные ветки и посты форума не лежат в кэше — это не значит,
            # что их нет. Забываем сообщения, только если канал правда удалён.
            try:
                channel = await guild.fetch_channel(channel_id)
            except discord.NotFound:
                missing += len(instances)
                for message_id, _ in instances:
                    delete_sent_instance(message_id)
                continue
            except discord.HTTPException:
                missing += len(instances)
                continue
        for group in _group_sends(instances):
            kept = []
            for position, message_id in enumerate(group):
                message = channel.get_partial_message(message_id)
                try:
                    if position >= len(parts):
                        await message.delete()
                        delete_sent_instance(message_id)
                        removed += 1
                        continue
                    part = parts[position]
                    await message.edit(
                        content=part.get("content"), embed=part.get("embed"), view=part.get("view"),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    updated += 1
                except discord.NotFound:
                    delete_sent_instance(message_id)
                    missing += 1
                    continue
                except discord.HTTPException:
                    missing += 1
                kept.append(message_id)
            appended = []
            for position in range(len(group), len(parts)):
                try:
                    msg = await channel.send(allowed_mentions=discord.AllowedMentions.none(), **parts[position])
                except discord.HTTPException:
                    missing += 1
                    continue
                appended.append(msg.id)
                added += 1
            if appended:
                # Записи отправки должны идти подряд (по id), иначе _group_sends
                # в следующий раз припишет досланное сообщение чужой отправке
                # в этом же канале. Поэтому переписываем всю группу заново.
                for message_id in kept:
                    delete_sent_instance(message_id)
                for position, message_id in enumerate(kept + appended):
                    save_sent_instance(build_id, message_id, channel.id, guild.id, position)
    return updated, removed, added, missing


# ============================================================
# ЖИВЫЕ ОБНОВЛЕНИЯ ПО СОБЫТИЮ
# ============================================================

REFRESH_DELAY = 5
_pending = {}  # guild_id -> set(build_id)
_bot = None


def setup(bot):
    global _bot
    _bot = bot


def builds_using(guild_id, predicate):
    """build'ы сервера, которые отправлены и используют переменную, подходящую под predicate(имя, арг)."""
    result = []
    for build_id, *_ in get_message_builds(guild_id, include_hidden=True):
        if not get_sent_instances(build_id):
            continue
        data = load_source("b", build_id)
        if data and any(predicate(name, arg) for name, arg in used_variables(data)):
            result.append(build_id)
    return result


def request_refresh(guild, build_ids):
    """Обновить build'ы чуть позже; повторные запросы за это время склеиваются."""
    if not build_ids or guild is None:
        return
    queued = _pending.setdefault(guild.id, set())
    first = not queued
    queued.update(build_ids)
    if first:
        asyncio.get_running_loop().create_task(_flush(guild))


async def _flush(guild):
    await asyncio.sleep(REFRESH_DELAY)
    build_ids = _pending.pop(guild.id, set())
    for build_id in build_ids:
        try:
            await resync_build(guild, build_id)
        except Exception:  # одно сломанное сообщение не должно останавливать остальные
            _log.exception("live refresh failed for build %s", build_id)


def counter_changed(guild, name):
    request_refresh(guild, builds_using(guild.id, lambda n, arg: n == "counter" and arg == name))


def submissions_changed(guild):
    request_refresh(guild, builds_using(guild.id, lambda n, arg: n in SUBMISSION_VARIABLES))


def automation_author(guild, owner_id, build_row):
    """
    От чьего имени работает расписание/триггер: тот, кто его создал.
    Он должен быть на сервере и по-прежнему видеть build — иначе None.
    """
    member = guild.get_member(owner_id)
    if member is None or build_row is None:
        return None
    settings_roles = _json(build_row[9], [])
    levels = _json(build_row[10], [])
    if not member_can_view(guild, member, build_row[2], build_row[7], settings_roles, levels):
        return None
    return member

