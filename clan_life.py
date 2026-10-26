"""
clan_life.py
============
Жизнь клана по сроку (CLANS_TODO п. 3, 3б, 4а): продление раз в 30 дней,
долг, роспуск, «Надёжный», награды месяца, уведомления главе и замам.

Как идёт срок (всё — по таймерам в базе; после перезапуска пропущенное
выполняется один раз — у каждого шага свой ключ события):
1. За 3 дня и за 1 день до продления — ЛС главе и замам: казна и сколько не хватает.
2. За сутки — стипендия за квесты срока (1 → 1000, 2 → 2500, 3 → 5000), один раз на срок.
3. В день продления: казна × огонёк (если серия не сгорела) → −5000
   (бесплатно, если есть награда месяца «бесплатное продление»).
   Хватило — новый срок, +1 к серии «Надёжный» (3 подряд — значок).
   Не хватило — долг на 7 дней: огонёк сгорает, магазин и приём новых закрыты,
   нет наград месяца и места в Совете; ЛС каждый день и за сутки до роспуска.
4. Долг гасится сам, как только в казне набралось 5000. Новый срок — от
   прежней даты продления, не от дня погашения.
5. Не погасили за 7 дней — роспуск: роль, каналы и категория клана
   удаляются, казна аннулируется (запись в журнал), тег и название свободны.
   Распустить сам клан может глава — с подтверждением зама.

Повторный долг (2 срока подряд) — клан скрыт из «Всё о кланах» месяц.

**Выключено, пока админ не включит** (/clan → «Управление кланами» →
«Жизнь кланов: вкл.») — роспуск удаляет роль и каналы, это нельзя включать
случайно. Пока выключено: сроки идут и видны, но продление, долг и
роспуск не выполняются.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import discord

import ecosystem
from core import t
from database import get_connection

_log = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))
DAY = 24 * 3600
TERM_DAYS = 30
RENEW_COST = 5000
DEBT_DAYS = 7
STIPEND = {0: 0, 1: 1000, 2: 2500, 3: 5000}
FIRE = {0: 1.0, 1: 1.1, 2: 1.2, 3: 1.3, 4: 1.4}  # 5 и больше недель — ×1.5
RELIABLE_AFTER = 3
CHECK_EVERY = 60
_bot = None


# ============================================================
# ХРАНИЛИЩЕ
# ============================================================

def ensure_tables():
    connection = get_connection()
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS clan_settings (scope_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT,
            PRIMARY KEY (scope_id, key));
        CREATE TABLE IF NOT EXISTS clan_state (scope_id INTEGER NOT NULL, tag TEXT NOT NULL,
            term_no INTEGER NOT NULL DEFAULT 1, term_start INTEGER NOT NULL, due_at INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'active', debt_until INTEGER, reliable INTEGER NOT NULL DEFAULT 0,
            missed INTEGER NOT NULL DEFAULT 0, free_renewals INTEGER NOT NULL DEFAULT 0, fire_reset_at INTEGER,
            hidden_until INTEGER, notified TEXT NOT NULL DEFAULT '[]', PRIMARY KEY (scope_id, tag));
        CREATE TABLE IF NOT EXISTS clan_awards (scope_id INTEGER NOT NULL, month TEXT NOT NULL, tag TEXT NOT NULL,
            place INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'place', PRIMARY KEY (scope_id, month, tag, kind));
    """)
    connection.commit()
    connection.close()


def setting(scope_id, key, default=None):
    ensure_tables()
    connection = get_connection()
    row = connection.execute("SELECT value FROM clan_settings WHERE scope_id = ? AND key = ?",
                             (scope_id, key)).fetchone()
    connection.close()
    if row is None or row[0] is None:
        return default
    try:
        return json.loads(row[0])
    except ValueError:
        return default


def set_setting(scope_id, key, value):
    ensure_tables()
    connection = get_connection()
    connection.execute("INSERT INTO clan_settings (scope_id, key, value) VALUES (?, ?, ?) ON CONFLICT(scope_id, key) "
                       "DO UPDATE SET value = excluded.value", (scope_id, key, json.dumps(value, ensure_ascii=False)))
    connection.commit()
    connection.close()


def enabled(scope_id):
    return bool(setting(scope_id, "life", False))


_STATE = ("scope_id", "tag", "term_no", "term_start", "due_at", "state", "debt_until", "reliable", "missed",
          "free_renewals", "fire_reset_at", "hidden_until", "notified")


def state_of(scope_id, tag, now=None):
    """Срок клана; первый раз — новый 30-дневный срок с этого момента (старым кланам — тоже заново)."""
    ensure_tables()
    tag = tag.upper()
    connection = get_connection()
    row = connection.execute(f"SELECT {', '.join(_STATE)} FROM clan_state WHERE scope_id = ? AND tag = ?",
                             (scope_id, tag)).fetchone()
    if row is None:
        start = int(now or time.time())
        connection.execute("INSERT OR IGNORE INTO clan_state (scope_id, tag, term_start, due_at) VALUES (?, ?, ?, ?)",
                           (scope_id, tag, start, start + TERM_DAYS * DAY))
        connection.commit()
        row = connection.execute(f"SELECT {', '.join(_STATE)} FROM clan_state WHERE scope_id = ? AND tag = ?",
                                 (scope_id, tag)).fetchone()
    connection.close()
    state = dict(zip(_STATE, row))
    state["notified"] = set(json.loads(state["notified"] or "[]"))
    return state


def save_state(state):
    connection = get_connection()
    notified = sorted(state["notified"])[-60:]
    connection.execute(
        "UPDATE clan_state SET term_no = ?, term_start = ?, due_at = ?, state = ?, debt_until = ?, reliable = ?, "
        "missed = ?, free_renewals = ?, fire_reset_at = ?, hidden_until = ?, notified = ? WHERE scope_id = ? AND tag = ?",
        (state["term_no"], state["term_start"], state["due_at"], state["state"], state["debt_until"], state["reliable"],
         state["missed"], state["free_renewals"], state["fire_reset_at"], state["hidden_until"],
         json.dumps(notified), state["scope_id"], state["tag"]))
    connection.commit()
    connection.close()


def rename_tag(scope_id, old, new):
    """Смена тега (магазин): срок, награды — за новым тегом."""
    ensure_tables()
    connection = get_connection()
    for table in ("clan_state", "clan_awards"):
        connection.execute(f"UPDATE {table} SET tag = ? WHERE scope_id = ? AND tag = ?", (new.upper(), scope_id,
                                                                                         old.upper()))
    connection.commit()
    connection.close()


# ============================================================
# ПРОВЕРКИ ДЛЯ ДРУГИХ МОДУЛЕЙ (санкции долга)
# ============================================================

def in_debt(scope_id, tag):
    return state_of(scope_id, tag)["state"] == "debt"


def can_accept(scope_id, tag):
    """В долгу нельзя принимать новых участников (п. 3б)."""
    return not in_debt(scope_id, tag)


def shop_open(scope_id, tag):
    return not in_debt(scope_id, tag)


def reliable(scope_id, tag):
    return state_of(scope_id, tag)["reliable"] >= RELIABLE_AFTER


def hidden(scope_id, tag, now=None):
    until = state_of(scope_id, tag)["hidden_until"]
    return bool(until and until > (now or time.time()))


def month_key(moment=None):
    return datetime.fromtimestamp(moment or time.time(), MSK).strftime("%Y-%m")


def award_place(scope_id, tag, moment=None):
    """Место клана в итогах прошлого месяца — награды действуют весь текущий. -> 1/2/3 | None."""
    now = datetime.fromtimestamp(moment or time.time(), MSK)
    previous = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    ensure_tables()
    connection = get_connection()
    row = connection.execute("SELECT place FROM clan_awards WHERE scope_id = ? AND month = ? AND tag = ? AND "
                             "kind = 'place'", (scope_id, previous, tag.upper())).fetchone()
    connection.close()
    return row[0] if row else None


def reward_bonus(scope_id, tag, gross, key):
    """Топ-1 месяца: +10% к наградам в казну (85% вместо 75%). -> сколько добавлено."""
    import clan_treasury
    if not gross or award_place(scope_id, tag) != 1:
        return 0
    bonus = int(round(gross * 0.10))
    done, _, _ = clan_treasury.record(scope_id, tag, "", bonus, 0, "бонус топ-1 месяца (+10%)",
                                      source_key=f"bonus:{key}")
    return bonus if done else 0


def fire_multiplier(scope_id, tag):
    import clan_billboard
    state = state_of(scope_id, tag)
    weeks = clan_billboard.fire_streak(scope_id, tag, since=state["fire_reset_at"])
    return FIRE.get(weeks, 1.5), weeks


# ============================================================
# УВЕДОМЛЕНИЯ
# ============================================================

async def tell_team(guild, clan, text):
    """ЛС главе и замам (не дошло — в канал клана)."""
    import clan_members
    delivered = False
    for user_id in [clan["leader_id"], *clan["deputies"]]:
        member = await clan_members.fetch_member(guild, user_id)
        if member is not None and await clan_members.dm(member, text[:2000]) is not None:
            delivered = True
    if not delivered:
        await clan_members.tell_clan(guild, clan, text)


async def log(bot, scope_id, text):
    """Журнал кланов для админов (канал из настроек)."""
    channel_id = setting(scope_id, "log_channel")
    channel = bot.get_channel(int(channel_id)) if channel_id and bot else None
    if channel is not None:
        try:
            await channel.send(text[:2000], allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass


def _money(value):
    return f"{int(value):,}".replace(",", " ")


# ============================================================
# ШАГИ СРОКА
# ============================================================

async def pay_stipend(scope_id, clan, state):
    import clan_quests
    import clan_treasury
    done = clan_quests.done_count(scope_id, clan["tag"], state["term_no"])
    amount = STIPEND.get(min(done, 3), 0)
    if not amount:
        return 0
    ok, _, _ = clan_treasury.record(scope_id, clan["tag"], clan["name"], amount, 0,
                                    f"стипендия за квесты: {done} из 3",
                                    source_key=f"stipend:{clan['tag']}:{state['term_no']}")
    return amount if ok else 0


async def renew(guild, clan, state, now):
    """День продления: огонёк → −5000 → новый срок или долг."""
    import clan_treasury
    scope_id, tag = state["scope_id"], state["tag"]
    treasury, _ = clan_treasury.balance(scope_id, tag)
    mult, weeks = fire_multiplier(scope_id, tag)
    if mult > 1 and treasury > 0:
        bonus = int(treasury * (mult - 1))
        clan_treasury.record(scope_id, tag, clan["name"], bonus, 0, f"огонёк ×{mult:g} ({weeks} нед.)",
                             source_key=f"fire:{tag}:{state['term_no']}")
    cost = RENEW_COST
    if state["free_renewals"] > 0:
        cost = 0
    paid, left = True, None
    if cost:
        paid, left = clan_treasury.spend(scope_id, tag, cost, f"продление, срок {state['term_no'] + 1}",
                                         source_key=f"renew:{tag}:{state['term_no']}")
        paid = paid is not False  # None — уже списано раньше (перезапуск посреди шага)
    else:
        state["free_renewals"] -= 1
        clan_treasury.record(scope_id, tag, clan["name"], 0, 0, "бесплатное продление (награда месяца)",
                             source_key=f"renew:{tag}:{state['term_no']}")
    if paid:
        state.update(term_no=state["term_no"] + 1, term_start=state["due_at"], due_at=state["due_at"] + TERM_DAYS * DAY,
                     reliable=state["reliable"] + 1, missed=0)
        save_state(state)
        await tell_team(guild, clan, t("clan_life.renewed", tag=tag, due=f"<t:{state['due_at']}:D>",
                                       left=_money(clan_treasury.balance(scope_id, tag)[0]),
                                       free=t("clan_life.free") if not cost else ""))
        return True
    state.update(state="debt", debt_until=state["due_at"] + DEBT_DAYS * DAY, missed=state["missed"] + 1, reliable=0,
                 fire_reset_at=int(now))
    save_state(state)
    await tell_team(guild, clan, t("clan_life.debt_started", tag=tag, need=_money(RENEW_COST - (left or 0)),
                                   until=f"<t:{state['debt_until']}:R>"))
    await log(_bot, scope_id, t("clan_life.log_debt", tag=tag))
    return False


async def settle_debt(guild, clan, state):
    """Долг гасится сам, как только в казне набралось 5000. Срок — от прежней даты."""
    import clan_treasury
    scope_id, tag = state["scope_id"], state["tag"]
    paid, _ = clan_treasury.spend(scope_id, tag, RENEW_COST, f"погашение долга, срок {state['term_no'] + 1}",
                                  source_key=f"renew:{tag}:{state['term_no']}")
    if paid is False:
        return False
    hidden_until = int(time.time()) + TERM_DAYS * DAY if state["missed"] >= 2 else state["hidden_until"]
    state.update(state="active", debt_until=None, term_no=state["term_no"] + 1, term_start=state["due_at"],
                 due_at=state["due_at"] + TERM_DAYS * DAY, hidden_until=hidden_until)
    save_state(state)
    await tell_team(guild, clan, t("clan_life.debt_paid", tag=tag, due=f"<t:{state['due_at']}:D>"))
    return True


async def dissolve(guild, clan, reason, by_user=None):
    """Роспуск: роль, каналы, категория — удалить; казна — аннулировать; тег и название — свободны."""
    import clan_members
    import clan_treasury
    import clans
    scope_id, tag = clan["scope_id"], clan["tag"]
    members = clan_members.member_ids(scope_id, tag)
    if guild is not None and clan.get("guild_id") in (None, guild.id):
        category = guild.get_channel(clan.get("category_id") or 0)
        for channel in list(getattr(category, "channels", None) or []):
            try:
                await channel.delete(reason=f"Purr: роспуск клана [{tag}]")
            except discord.HTTPException:
                pass
        if category is not None:
            try:
                await category.delete(reason=f"Purr: роспуск клана [{tag}]")
            except discord.HTTPException:
                pass
        role = guild.get_role(clan.get("role_id") or 0) or clans.find_role(guild, clan)
        if role is not None:
            try:
                await role.delete(reason=f"Purr: роспуск клана [{tag}]")
            except discord.HTTPException:
                pass
    treasury, _ = clan_treasury.balance(scope_id, tag)
    if treasury:
        clan_treasury.record(scope_id, tag, "", -treasury, 0, "аннулировано при роспуске",
                             source_key=f"dissolve:{tag}:{int(time.time())}", by_user=by_user)
    connection = get_connection()
    connection.execute("UPDATE clans SET status = 'dissolved' WHERE scope_id = ? AND tag = ?", (scope_id, tag))
    connection.execute("DELETE FROM clan_members WHERE scope_id = ? AND tag = ?", (scope_id, tag))
    connection.execute("UPDATE clan_state SET state = 'dissolved' WHERE scope_id = ? AND tag = ?", (scope_id, tag))
    connection.execute("DELETE FROM clan_seeded WHERE scope_id = ? AND tag = ?", (scope_id, tag))
    connection.commit()
    connection.close()
    text = t(f"clan_life.dissolved.{reason}", tag=tag, name=clan["name"] or "—")
    for user_id in members:
        member = await clan_members.fetch_member(guild, user_id)
        if member is not None:
            await clan_members.dm(member, text)
    _log.info("clan %s dissolved (%s)", tag, reason)
    await log(_bot, scope_id, t("clan_life.log_dissolved", tag=tag, reason=t(f"clan_life.reason.{reason}"),
                               by=f"<@{by_user}>" if by_user else "—"))
    return True


async def process(guild, clan, now=None):
    """Один шаг таймеров клана. -> что сделано (для журнала и тестов)."""
    import clan_treasury
    now = int(now or time.time())
    state = state_of(clan["scope_id"], clan["tag"], now)
    tag, term = state["tag"], state["term_no"]
    done = []
    if state["state"] == "active":
        left = state["due_at"] - now
        treasury, _ = clan_treasury.balance(state["scope_id"], tag)
        for days in (3, 1):
            key = f"pre{days}:{term}"
            if left <= days * DAY and key not in state["notified"] and left > 0:
                state["notified"].add(key)
                save_state(state)
                need = max(0, RENEW_COST - treasury)
                await tell_team(guild, clan, t("clan_life.soon", tag=tag, when=f"<t:{state['due_at']}:R>",
                                               treasury=_money(treasury),
                                               need=t("clan_life.need", need=_money(need)) if need else
                                               t("clan_life.enough")))
                done.append(key)
        if left <= DAY and f"stipend:{term}" not in state["notified"]:
            amount = await pay_stipend(state["scope_id"], clan, state)
            state["notified"].add(f"stipend:{term}")
            save_state(state)
            if amount:
                await tell_team(guild, clan, t("clan_life.stipend", tag=tag, amount=_money(amount)))
            done.append("stipend")
        if now >= state["due_at"]:
            done.append("renewed" if await renew(guild, clan, state, now) else "debt")
    elif state["state"] == "debt":
        if await settle_debt(guild, clan, state):
            return done + ["debt_paid"]
        day = (now - (state["debt_until"] - DEBT_DAYS * DAY)) // DAY
        key = f"debt_day:{term}:{day}"
        if key not in state["notified"]:
            state["notified"].add(key)
            save_state(state)
            treasury, _ = clan_treasury.balance(state["scope_id"], tag)
            await tell_team(guild, clan, t("clan_life.debt_day", tag=tag, need=_money(RENEW_COST - treasury),
                                           until=f"<t:{state['debt_until']}:R>"))
            done.append("debt_day")
        if now >= state["debt_until"]:
            await dissolve(guild, clan, "debt")
            done.append("dissolved")
    return done


# ============================================================
# ФОНОВАЯ ЗАДАЧА
# ============================================================

def scopes(bot):
    seen = {}
    for guild in bot.guilds:
        seen.setdefault(ecosystem.scope(guild.id), guild)
    return seen


async def tick(bot):
    import clans
    for scope_id, any_guild in scopes(bot).items():
        for clan in clans.get_clans(scope_id):
            state_of(scope_id, clan["tag"])  # срок идёт и виден, даже если автоматика выключена
            if not enabled(scope_id):
                continue
            guild = bot.get_guild(clan.get("guild_id") or 0) or any_guild
            try:
                await process(guild, clan)
            except Exception:  # один клан не должен останавливать остальные
                _log.exception("clan life %s", clan["tag"])


async def loop(bot):
    await bot.wait_until_ready()
    while not bot.is_closed():
        await tick(bot)
        await asyncio.sleep(CHECK_EVERY)


def start(bot):
    global _bot
    _bot = bot
    ensure_tables()
    return asyncio.create_task(loop(bot))


# ============================================================
# ТЕКСТ ДЛЯ КАРТОЧЕК
# ============================================================

def status_line(scope_id, tag):
    """«Срок до …, огонёк, надёжный, в долгу» — одной строкой для карточек клана."""
    import clan_treasury
    state = state_of(scope_id, tag)
    treasury, respect = clan_treasury.balance(scope_id, tag)
    mult, weeks = fire_multiplier(scope_id, tag)
    parts = [t("clan_life.line.treasury", treasury=_money(treasury), respect=f"{respect:g}")]
    if state["state"] == "debt":
        parts.append(t("clan_life.line.debt", until=f"<t:{state['debt_until']}:R>"))
    else:
        parts.append(t("clan_life.line.due", due=f"<t:{state['due_at']}:R>"))
    if weeks:
        parts.append(t("clan_life.line.fire", weeks=weeks, mult=f"{mult:g}"))
    if state["reliable"] >= RELIABLE_AFTER:
        parts.append(t("clan_life.line.reliable"))
    place = award_place(scope_id, tag)
    if place:
        parts.append(t("clan_life.line.award", place=place))
    if not enabled(scope_id):
        parts.append(t("clan_life.line.off"))
    return " · ".join(parts)
