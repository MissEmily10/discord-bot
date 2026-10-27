"""
clan_billboard.py
=================
Биллборд кланов (CLANS_TODO п. 4, 4а, 5, 11, 13).

Баллы (с десятыми, остатки не сгорают — минута войса и каждое сообщение
идут в счёт сразу, без округления):
- войс: час в голосовом канале клана = 5 баллов, если в канале ≥3 человек и
  хотя бы у одного включён микрофон (не self-mute / self-deaf / server-mute);
  все замьючены — время не идёт (анти-АФК). Бот смотрит войсы раз в минуту,
  после перезапуска теряется только время, пока он был выключен;
- сообщения: 900 сообщений в текстовых каналах категории клана = 5 баллов;
- респект: 1 респект = 5 баллов (из журнала казны: награды, квесты, ивенты).

Неделя закрывается в воскресенье в 20:00 МСК: снимок мест хранится навсегда,
в канал биллборда — отчёт недели (места, кто поднялся / упал). Огонёк —
недели подряд в топ-3 по снимкам (сгорает при долге): 1 → ×1.1 … 5+ → ×1.5.

Итоги месяца — сумма баллов за месяц. Топ-3 (кроме кланов в долгу) получают
награды на весь следующий месяц: 3 — роль выделена в списке участников;
2 — + бесплатное продление; 1 — + градиентный цвет роли и +10% к наградам в
казну. Лидер каждого направления респекта за месяц — значок навсегда.

Живая карточка биллборда — в канале из настроек (/clan → «Управление
кланами»), обновляется раз в 10 минут; кнопка «Лидеры направлений».
"""

import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta

import discord

import ecosystem
from core import t
from database import get_connection

_log = logging.getLogger(__name__)

VOICE_POINTS_PER_HOUR = 5
MESSAGES_PER_5 = 900
RESPECT_POINTS = 5
MIN_VOICE = 3
CARD_EVERY = 10 * 60
_messages = defaultdict(int)   # (scope, tag) -> сообщений с прошлой записи
_categories = {}                # category_id -> (scope, tag)
_categories_at = 0
_bot = None


def ensure_tables():
    connection = get_connection()
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS clan_activity (scope_id INTEGER NOT NULL, tag TEXT NOT NULL, period TEXT NOT NULL,
            voice_sec INTEGER NOT NULL DEFAULT 0, messages INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (scope_id, tag, period));
        CREATE TABLE IF NOT EXISTS clan_snapshots (scope_id INTEGER NOT NULL, kind TEXT NOT NULL, period TEXT NOT NULL,
            tag TEXT NOT NULL, points REAL NOT NULL, place INTEGER NOT NULL, PRIMARY KEY (scope_id, kind, period, tag));
    """)
    connection.commit()
    connection.close()


# ============================================================
# ПЕРИОДЫ (МСК)
# ============================================================

def _msk(moment):
    import clan_life
    return datetime.fromtimestamp(moment, clan_life.MSK)


def week_end(moment=None):
    """Конец недели, в которую попадает момент: ближайшее воскресенье 20:00 МСК после него. -> datetime."""
    now = _msk(moment or time.time())
    end = (now + timedelta(days=(6 - now.weekday()) % 7)).replace(hour=20, minute=0, second=0, microsecond=0)
    if end <= now:
        end += timedelta(days=7)
    return end


def week_key(moment=None):
    return "w" + week_end(moment).strftime("%Y-%m-%d")


def week_range(key):
    import clan_life
    end = datetime.strptime(key[1:], "%Y-%m-%d").replace(hour=20, tzinfo=clan_life.MSK)
    return int((end - timedelta(days=7)).timestamp()), int(end.timestamp())


def month_key(moment=None):
    return "m" + _msk(moment or time.time()).strftime("%Y-%m")


def month_range(key):
    import clan_life
    start = datetime.strptime(key[1:], "%Y-%m").replace(tzinfo=clan_life.MSK)
    after = (start + timedelta(days=32)).replace(day=1)
    return int(start.timestamp()), int(after.timestamp())


# ============================================================
# УЧЁТ
# ============================================================

def add(scope_id, tag, voice_sec=0, messages=0, moment=None):
    """Начислить активность сразу в неделю и в месяц."""
    ensure_tables()
    connection = get_connection()
    for period in (week_key(moment), month_key(moment)):
        connection.execute(
            "INSERT INTO clan_activity (scope_id, tag, period, voice_sec, messages) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(scope_id, tag, period) DO UPDATE SET voice_sec = voice_sec + excluded.voice_sec, "
            "messages = messages + excluded.messages", (scope_id, tag.upper(), period, int(voice_sec), int(messages)))
    connection.commit()
    connection.close()


def activity(scope_id, period):
    """{тег: (секунды войса, сообщения)} за период."""
    ensure_tables()
    connection = get_connection()
    rows = connection.execute("SELECT tag, voice_sec, messages FROM clan_activity WHERE scope_id = ? AND period = ?",
                              (scope_id, period)).fetchall()
    connection.close()
    return {tag: (voice, messages) for tag, voice, messages in rows}


def standings(scope_id, period):
    """[(тег, баллы, войс_ч, сообщения, респект)] по убыванию баллов — для активных кланов."""
    import clan_treasury
    import clans
    start, end = week_range(period) if period.startswith("w") else month_range(period)
    acts = activity(scope_id, period)
    respect = clan_treasury.respect_since(scope_id, start, end)
    rows = []
    for clan in clans.get_clans(scope_id):
        voice, messages = acts.get(clan["tag"], (0, 0))
        resp = max(0.0, respect.get(clan["tag"], 0.0))
        points = voice / 3600 * VOICE_POINTS_PER_HOUR + messages / MESSAGES_PER_5 * 5 + resp * RESPECT_POINTS
        rows.append((clan["tag"], round(points, 1), round(voice / 3600, 1), messages, round(resp, 2)))
    rows.sort(key=lambda row: (-row[1], row[0]))
    return rows


def snapshot(scope_id, kind, period, rows):
    ensure_tables()
    connection = get_connection()
    for place, (tag, points, *_rest) in enumerate(rows, 1):
        connection.execute("INSERT OR IGNORE INTO clan_snapshots (scope_id, kind, period, tag, points, place) "
                           "VALUES (?, ?, ?, ?, ?, ?)", (scope_id, kind, period, tag, points, place))
    connection.commit()
    connection.close()


def snapshots(scope_id, kind, limit=None):
    """{период: {тег: место}} — последние закрытые, свежие первыми."""
    ensure_tables()
    connection = get_connection()
    rows = connection.execute("SELECT period, tag, place FROM clan_snapshots WHERE scope_id = ? AND kind = ? "
                              "ORDER BY period DESC", (scope_id, kind)).fetchall()
    connection.close()
    found = {}
    for period, tag, place in rows:
        found.setdefault(period, {})[tag] = place
    if limit:
        found = dict(list(found.items())[:limit])
    return found


def fire_streak(scope_id, tag, since=None):
    """Недели подряд в топ-3 по снимкам (свежие первыми); since — сгорело при долге."""
    streak = 0
    for period, places in snapshots(scope_id, "week").items():
        if since and week_range(period)[1] <= since:
            break
        if places.get(tag.upper(), 99) <= 3:
            streak += 1
        else:
            break
    return streak


# ============================================================
# СОБЫТИЯ: сообщения и войс
# ============================================================

def _refresh_categories(bot):
    """category_id -> (scope, tag) — раз в минуту."""
    global _categories_at
    import clans
    if time.time() - _categories_at < 60:
        return
    _categories_at = time.time()
    _categories.clear()
    seen = set()
    for guild in bot.guilds:
        scope_id = ecosystem.scope(guild.id)
        if scope_id in seen:
            continue
        seen.add(scope_id)
        for clan in clans.get_clans(scope_id):
            if clan.get("category_id"):
                _categories[clan["category_id"]] = (scope_id, clan["tag"])


async def on_message(message):
    if message.author.bot or message.guild is None or _bot is None:
        return
    _refresh_categories(_bot)
    channel = message.channel
    category_id = getattr(channel, "category_id", None) or getattr(getattr(channel, "parent", None), "category_id",
                                                                    None)
    found = _categories.get(category_id)
    if found:
        _messages[found] += 1


def voice_ok(channel):
    """≥3 человек и хотя бы у одного включён микрофон."""
    people = [m for m in getattr(channel, "members", []) if not m.bot]
    if len(people) < MIN_VOICE:
        return False
    for member in people:
        state = member.voice
        if state is not None and not (state.self_mute or state.mute or state.self_deaf or state.deaf):
            return True
    return False


def voice_tick(bot, seconds=60):
    """Раз в минуту: клану — минута, если хоть один его голосовой канал «живой»."""
    _refresh_categories(bot)
    for category_id, (scope_id, tag) in list(_categories.items()):
        category = bot.get_channel(category_id)
        voices = [c for c in getattr(category, "channels", None) or []
                  if isinstance(c, (discord.VoiceChannel, discord.StageChannel))]
        if any(voice_ok(channel) for channel in voices):
            add(scope_id, tag, voice_sec=seconds)


def flush_messages():
    for (scope_id, tag), count in list(_messages.items()):
        if count:
            add(scope_id, tag, messages=count)
    _messages.clear()


# ============================================================
# ЗАКРЫТИЕ НЕДЕЛИ И МЕСЯЦА
# ============================================================

def place_emoji(place):
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(place, f"{place}.")


def week_report(scope_id, period):
    rows = standings(scope_id, period)
    before = snapshots(scope_id, "week", limit=2)
    previous = next((places for key, places in before.items() if key != period), {})
    lines = []
    for place, (tag, points, voice, messages, respect) in enumerate(rows, 1):
        old = previous.get(tag)
        move = "" if old is None or old == place else (f" ▲{old - place}" if old > place else f" ▼{place - old}")
        lines.append(t("clan_billboard.report_line", place=place_emoji(place), tag=tag, points=f"{points:g}",
                       move=move))
    return t("clan_billboard.report", week=period[1:], lines="\n".join(lines) or "—")


async def close_week(bot, scope_id, period):
    rows = standings(scope_id, period)
    snapshot(scope_id, "week", period, rows)
    channel = _channel(bot, scope_id, "billboard_channel")
    if channel is not None:
        try:
            await channel.send(embed=discord.Embed(description=week_report(scope_id, period)[:4096],
                                                   title=t("clan_billboard.report_title"), color=0x2B2D31))
        except discord.HTTPException:
            pass


async def close_month(bot, scope_id, period, guild=None):
    """Итоги месяца: места, награды на следующий месяц, значки направлений."""
    import clan_life
    import clan_treasury
    rows = standings(scope_id, period)
    snapshot(scope_id, "month", period, rows)
    month = period[1:]
    winners = [row for row in rows if row[1] > 0 and not clan_life.in_debt(scope_id, row[0])][:3]
    connection = get_connection()
    for place, row in enumerate(winners, 1):
        connection.execute("INSERT OR IGNORE INTO clan_awards (scope_id, month, tag, place, kind) "
                           "VALUES (?, ?, ?, ?, 'place')", (scope_id, month, row[0], place))
    start, end = month_range(period)
    by_direction = clan_treasury.respect_by_direction(scope_id, since=start, until=end)
    leaders = {}
    for (tag, direction), amount in by_direction.items():
        if amount > 0 and amount > leaders.get(direction, ("", 0))[1]:
            leaders[direction] = (tag, amount)
    for direction, (tag, _) in leaders.items():
        connection.execute("INSERT OR IGNORE INTO clan_awards (scope_id, month, tag, place, kind) "
                           "VALUES (?, ?, ?, 1, ?)", (scope_id, month, tag, f"dir:{direction}"))
    connection.commit()
    connection.close()
    live = clan_life.enabled(scope_id)  # роли и канал Совета — только когда админ включил жизнь кланов
    for place, row in enumerate(winners, 1):
        if place <= 2 and live:
            state = clan_life.state_of(scope_id, row[0])
            state["free_renewals"] += 1
            clan_life.save_state(state)
    guild = guild or bot.get_guild(scope_id)
    if guild is not None and live:
        await apply_role_awards(guild, scope_id, [row[0] for row in winners])
    channel = _channel(bot, scope_id, "billboard_channel")
    if channel is not None:
        lines = [t("clan_billboard.month_line", place=place_emoji(n), tag=row[0], points=f"{row[1]:g}",
                   prize=t(f"clan_billboard.prize.{n}")) for n, row in enumerate(winners, 1)]
        dirs = [t("clan_billboard.dir_line", direction=clan_treasury.DIRECTIONS.get(d, d), tag=tag)
                for d, (tag, _) in leaders.items()]
        try:
            await channel.send(embed=discord.Embed(
                title=t("clan_billboard.month_title", month=month),
                description=t("clan_billboard.month_text", lines="\n".join(lines) or "—",
                              dirs="\n".join(dirs) or "—")[:4096], color=0x2B2D31))
        except discord.HTTPException:
            pass
    if not live:
        return
    try:
        import clan_hub
        dev_order = [tag for (tag, direction), amount in sorted(by_direction.items(), key=lambda kv: -kv[1])
                     if direction == "dev" and amount > 0]
        await clan_hub.refresh_council(bot, scope_id, month, [row[0] for row in winners], dev_order)
    except Exception:  # совет — не повод ломать итоги месяца
        _log.exception("council refresh")


async def apply_role_awards(guild, scope_id, top_tags):
    """Выделение роли в списке (топ-3) и градиент (топ-1); у прошлых — снять."""
    import clans
    for clan in clans.get_clans(scope_id):
        role = guild.get_role(clan.get("role_id") or 0)
        if role is None:
            continue
        place = top_tags.index(clan["tag"]) + 1 if clan["tag"] in top_tags else None
        changes = {}
        if bool(role.hoist) != bool(place):
            changes["hoist"] = bool(place)
        try:
            if changes:
                await role.edit(reason="Purr: награды месяца кланов", **changes)
            if place == 1:  # градиент — если у сервера есть улучшенные цвета ролей
                try:
                    await role.edit(secondary_colour=discord.Colour(0xC6F135), reason="Purr: топ-1 месяца")
                except (TypeError, discord.HTTPException):
                    pass
            elif getattr(role, "secondary_colour", None):
                try:
                    await role.edit(secondary_colour=None, reason="Purr: награда месяца закончилась")
                except (TypeError, discord.HTTPException):
                    pass
        except discord.HTTPException as error:
            _log.warning("clan award role %s: %s", clan["tag"], error)


def _channel(bot, scope_id, key):
    import clan_life
    channel_id = clan_life.setting(scope_id, key)
    return bot.get_channel(int(channel_id)) if channel_id and bot else None


async def close_due(bot, scope_id, now=None):
    """Закрыть все прошедшие недели и месяцы, что ещё не закрыты (после перезапуска — тоже)."""
    import clan_life
    now = now or time.time()
    current_week, current_month = week_key(now), month_key(now)
    last_week = clan_life.setting(scope_id, "last_week") or current_week
    last_month = clan_life.setting(scope_id, "last_month") or current_month
    closed = []
    if last_week != current_week:
        await close_week(bot, scope_id, last_week)
        closed.append(last_week)
    if last_month != current_month:
        await close_month(bot, scope_id, last_month)
        closed.append(last_month)
    clan_life.set_setting(scope_id, "last_week", current_week)
    clan_life.set_setting(scope_id, "last_month", current_month)
    return closed


# ============================================================
# ЖИВАЯ КАРТОЧКА
# ============================================================

def card_embed(scope_id):
    import clan_life
    period = week_key()
    rows = standings(scope_id, period)
    lines = []
    for place, (tag, points, voice, messages, respect) in enumerate(rows, 1):
        mult, weeks = clan_life.fire_multiplier(scope_id, tag)
        marks = []
        if weeks:
            marks.append(f"🔥{weeks}")
        if clan_life.in_debt(scope_id, tag):
            marks.append(t("clan_billboard.in_debt"))
        lines.append(t("clan_billboard.line", place=place_emoji(place), tag=tag, points=f"{points:g}", voice=f"{voice:g}",
                       messages=messages, respect=f"{respect:g}", marks=" ".join(marks)))
    end = int(week_end().timestamp())
    embed = discord.Embed(title=t("clan_billboard.title"), color=0x2B2D31,
                          description=t("clan_billboard.text", lines="\n".join(lines) or t("clan_billboard.empty"),
                                        end=f"<t:{end}:R>")[:4096])
    return embed


class LeadersButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rb:clb:lead"):
    def __init__(self, *, item=None):
        super().__init__(item or discord.ui.Button(label=t("clan_billboard.leaders")[:80], emoji="🏆",
                                                   style=discord.ButtonStyle.secondary, custom_id="rb:clb:lead"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(item=item)

    async def callback(self, interaction):
        import clan_treasury
        scope_id = ecosystem.scope(interaction.guild.id)
        start, end = month_range(month_key())
        by_direction = clan_treasury.respect_by_direction(scope_id, since=start, until=end)
        leaders = {}
        for (tag, direction), amount in by_direction.items():
            if amount > leaders.get(direction, ("", 0))[1]:
                leaders[direction] = (tag, amount)
        lines = [t("clan_billboard.dir_line", direction=label, tag=leaders[key][0])
                 for key, label in clan_treasury.DIRECTIONS.items() if key in leaders]
        await interaction.response.send_message(
            embed=discord.Embed(title=t("clan_billboard.leaders_title"), color=0x2B2D31,
                                description="\n".join(lines) or t("clan_billboard.no_leaders")), ephemeral=True)


def card_view():
    view = discord.ui.View(timeout=None)
    view.add_item(LeadersButton())
    return view


async def publish(channel, scope_id):
    """Поставить живую карточку биллборда в канал (и запомнить, чтобы обновлять)."""
    import clan_life
    message = await channel.send(embed=card_embed(scope_id), view=card_view())
    clan_life.set_setting(scope_id, "billboard_channel", channel.id)
    clan_life.set_setting(scope_id, "billboard_message", message.id)
    return message


async def refresh_card(bot, scope_id):
    import clan_life
    channel = _channel(bot, scope_id, "billboard_channel")
    message_id = clan_life.setting(scope_id, "billboard_message")
    if channel is None or not message_id:
        return
    try:
        message = await channel.fetch_message(int(message_id))
        await message.edit(embed=card_embed(scope_id), view=card_view())
    except discord.HTTPException:
        pass


# ============================================================
# ФОН
# ============================================================

async def loop(bot):
    import clan_life
    await bot.wait_until_ready()
    last_card = 0
    while not bot.is_closed():
        try:
            voice_tick(bot)
            flush_messages()
            import clan_events
            clan_events.tick(bot)  # клановые ивенты: кто в войсе ивента
            for scope_id in clan_life.scopes(bot):
                await close_due(bot, scope_id)
                if time.time() - last_card >= CARD_EVERY:
                    await refresh_card(bot, scope_id)
            if time.time() - last_card >= CARD_EVERY:
                last_card = time.time()
        except Exception:  # учёт не должен падать насовсем
            _log.exception("clan billboard loop")
        await asyncio.sleep(60)


DYNAMIC_ITEMS = [LeadersButton]


def start(bot):
    global _bot
    _bot = bot
    ensure_tables()
    bot.add_listener(on_message, "on_message")
    return asyncio.create_task(loop(bot))
