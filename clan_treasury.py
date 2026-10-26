"""
clan_treasury.py
================
Казна кланов в Purr. Голда и `.награда` живут в боте сервера; когда участник
отправляет награду в казну клана и создатель бота подтверждает перевод, бот сервера
меняет своё сообщение на «✅ Перевод в казну подтверждён». Создатель бота отвечает на
это сообщение «@Purr зафиксируй» — Purr читает карточку и записывает в казну
клана сумму «В казну дойдёт» и «Респект клану» (как посчитал бот сервера).

Каждое сообщение фиксируется один раз (ключ — его ID), всё — в журнал.
`@Purr отмени` ответом на ту же карточку — отменить ошибочную фиксацию.
`@Purr казна` / `@Purr казна ТЕГ` — сводка / журнал клана.

Настройки (.env):
  CLAN_SOURCE_BOT_ID   — ID бота сервера (если пусто — принимается любой бот)
  CLAN_SOURCE_CHANNEL_ID — канал подтверждений (если пусто — любой)
Фиксировать могут владелец бота и уровень admin+ в Purr.
Нужен Message Content Intent: без него Discord не отдаёт Purr карточку чужого бота.
"""

import os
import re
import time

import discord

import core
import ecosystem
from core import t
from database import get_connection

TRIGGERS_FIX = ("зафиксируй", "зафиксировать", "фиксируй", "fix")
TRIGGERS_UNDO = ("отмени", "отменить", "undo")
TRIGGERS_SHOW = ("казна", "казну", "казны")
APPROVED = re.compile(r"подтвержд[её]н(?![а-яё])", re.I)  # не «ждёт подтверждения»
REJECTED = re.compile(r"отклон[её]н(?![а-яё])|отказан|аннулир|верн[уё]т|возвращ", re.I)
MENTION = re.compile(r"<@!?(\d{15,25})>")
TAG = re.compile(r"\[([^\[\]`]{1,12})\]")
NUMBER = re.compile(r"[-+]?\d[\d\s]*(?:[.,]\d+)?")


# ============================================================
# ХРАНИЛИЩЕ
# ============================================================

def ensure_tables():
    connection = get_connection()
    connection.execute("""CREATE TABLE IF NOT EXISTS clan_treasury (
        guild_id INTEGER NOT NULL, tag TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
        treasury INTEGER NOT NULL DEFAULT 0, respect REAL NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL, PRIMARY KEY (guild_id, tag)
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS clan_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, tag TEXT NOT NULL,
        amount INTEGER NOT NULL, respect REAL NOT NULL DEFAULT 0, reason TEXT NOT NULL,
        source_key TEXT UNIQUE, member_id INTEGER, giver_id INTEGER, by_user INTEGER,
        gross INTEGER, created_at INTEGER NOT NULL
    )""")
    if "direction" not in {row[1] for row in connection.execute("PRAGMA table_info(clan_ledger)")}:
        # направление респекта (п. 11): help / events / art / dev / compete / community
        connection.execute("ALTER TABLE clan_ledger ADD COLUMN direction TEXT")
    connection.commit()
    connection.close()


DIRECTIONS = {"help": "🤝 Помощь", "events": "🎉 Ивенты", "art": "🎨 Творчество", "dev": "🛠 Развитие сервера",
              "compete": "⚔️ Соревнования", "community": "👥 Сообщество"}


def direction_of(text):
    """«🎉 Ивенты» / «ивенты» / «events» -> ключ направления или None."""
    low = str(text or "").lower()
    for key, label in DIRECTIONS.items():
        if key == low or label.lower() in low or label.split(" ", 1)[1].lower().split()[0] in low:
            return key
    return None


def giver_direction(guild_id, giver_id):
    """Направление респекта за .награду — по главе категории, который её выдал (задают админы)."""
    import clan_life
    mapping = clan_life.setting(guild_id, "giver_directions", {})
    return mapping.get(str(giver_id)) or "community"


def spend(guild_id, tag, amount, reason, source_key=None, by_user=None):
    """
    Списать из казны, только если хватает (п. 0): проверка и списание — одной
    операцией в базе, два одновременных покупателя не уведут казну в минус.
    -> (True, остаток) | (False, остаток) | (None, None), если ключ уже был.
    """
    ensure_tables()
    tag = tag.upper()
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if source_key and connection.execute("SELECT 1 FROM clan_ledger WHERE source_key = ?",
                                             (source_key,)).fetchone():
            connection.rollback()
            return None, None
        cursor = connection.execute("UPDATE clan_treasury SET treasury = treasury - ? WHERE guild_id = ? AND tag = ? "
                                    "AND treasury >= ?", (amount, guild_id, tag, amount))
        if cursor.rowcount == 0:
            row = connection.execute("SELECT treasury FROM clan_treasury WHERE guild_id = ? AND tag = ?",
                                     (guild_id, tag)).fetchone()
            connection.rollback()
            return False, row[0] if row else 0
        connection.execute(
            "INSERT INTO clan_ledger (guild_id, tag, amount, respect, reason, source_key, by_user, created_at) "
            "VALUES (?, ?, ?, 0, ?, ?, ?, ?)", (guild_id, tag, -amount, reason, source_key, by_user, int(time.time())))
        left = connection.execute("SELECT treasury FROM clan_treasury WHERE guild_id = ? AND tag = ?",
                                  (guild_id, tag)).fetchone()[0]
        connection.commit()
        return True, left
    finally:
        connection.close()


def balance(guild_id, tag):
    row = get_clan(guild_id, tag)
    return (row[2], row[3]) if row else (0, 0.0)


def respect_since(guild_id, since, until=None):
    """{тег: респект} за период (для биллборда: 1 респект = 5 баллов)."""
    ensure_tables()
    connection = get_connection()
    rows = connection.execute("SELECT tag, SUM(respect) FROM clan_ledger WHERE guild_id = ? AND created_at >= ? "
                              "AND created_at < ? GROUP BY tag", (guild_id, int(since), int(until or 2 ** 40))).fetchall()
    connection.close()
    return {tag: float(total or 0) for tag, total in rows}


def respect_by_direction(guild_id, tag=None, since=0, until=None):
    """{(тег, направление): респект} — для «чем известен» и значков месяца."""
    ensure_tables()
    connection = get_connection()
    sql = ("SELECT tag, COALESCE(direction, 'community'), SUM(respect) FROM clan_ledger WHERE guild_id = ? "
           "AND respect != 0 AND created_at >= ? AND created_at < ?")
    params = [guild_id, int(since), int(until or 2 ** 40)]
    if tag:
        sql += " AND tag = ?"
        params.append(tag.upper())
    rows = connection.execute(sql + " GROUP BY tag, COALESCE(direction, 'community')", params).fetchall()
    connection.close()
    return {(row[0], row[1]): float(row[2] or 0) for row in rows}


def get_clan(guild_id, tag):
    ensure_tables()
    connection = get_connection()
    row = connection.execute("SELECT tag, name, treasury, respect FROM clan_treasury WHERE guild_id = ? AND tag = ?",
                             (guild_id, tag.upper())).fetchone()
    connection.close()
    return row


def get_clans(guild_id):
    ensure_tables()
    connection = get_connection()
    rows = connection.execute("SELECT tag, name, treasury, respect FROM clan_treasury WHERE guild_id = ? "
                              "ORDER BY treasury DESC, tag", (guild_id,)).fetchall()
    connection.close()
    return rows


def ledger(guild_id, tag, limit=10):
    ensure_tables()
    connection = get_connection()
    rows = connection.execute("SELECT amount, respect, reason, member_id, created_at FROM clan_ledger "
                              "WHERE guild_id = ? AND tag = ? ORDER BY id DESC LIMIT ?",
                              (guild_id, tag.upper(), limit)).fetchall()
    connection.close()
    return rows


def record(guild_id, tag, name, amount, respect, reason, source_key=None, member_id=None, giver_id=None,
           by_user=None, gross=None, direction=None):
    """
    Одна операция с казной: запись в журнал и изменение казны — в одной
    транзакции. Повтор с тем же source_key ничего не меняет.
    -> (True, казна, респект) | (False, None, None), если такой ключ уже был.
    """
    ensure_tables()
    tag = tag.upper()
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if source_key and connection.execute("SELECT 1 FROM clan_ledger WHERE source_key = ?",
                                             (source_key,)).fetchone():
            connection.rollback()
            return False, None, None
        connection.execute(
            "INSERT INTO clan_treasury (guild_id, tag, name, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(guild_id, tag) DO UPDATE SET name = CASE WHEN excluded.name != '' "
            "THEN excluded.name ELSE clan_treasury.name END",
            (guild_id, tag, name or "", int(time.time())))
        connection.execute("UPDATE clan_treasury SET treasury = treasury + ?, respect = ROUND(respect + ?, 4) "
                           "WHERE guild_id = ? AND tag = ?", (amount, respect, guild_id, tag))
        connection.execute(
            "INSERT INTO clan_ledger (guild_id, tag, amount, respect, reason, source_key, member_id, giver_id, "
            "by_user, gross, created_at, direction) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, tag, amount, respect, reason, source_key, member_id, giver_id, by_user, gross,
             int(time.time()), direction))
        treasury, total_respect = connection.execute(
            "SELECT treasury, respect FROM clan_treasury WHERE guild_id = ? AND tag = ?", (guild_id, tag)).fetchone()
        connection.commit()
        return True, treasury, total_respect
    finally:
        connection.close()


def fixation_of(guild_id, source_key):
    """Запись фиксации по ключу (для отмены). -> (tag, amount, respect) | None."""
    ensure_tables()
    connection = get_connection()
    row = connection.execute("SELECT tag, amount, respect FROM clan_ledger WHERE guild_id = ? AND source_key = ?",
                             (guild_id, source_key)).fetchone()
    connection.close()
    return row


# ============================================================
# РАЗБОР КАРТОЧКИ БОТА СЕРВЕРА
# ============================================================

def _number(text):
    match = NUMBER.search(text or "")
    if not match:
        return None
    raw = match.group(0).replace(" ", "").replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        return None
    return int(value) if value.is_integer() and "." not in raw else value


def _id(text):
    match = MENTION.search(text or "")
    return int(match.group(1)) if match else None


def parse_card(embed):
    """
    Карточка «✅ Перевод в казну подтверждён» -> dict или (None, ключ ошибки).
    Поля: Переводит, Клан, Сумма, В казну дойдёт, Респект клану, Голду выдал.
    """
    data = embed if isinstance(embed, dict) else embed.to_dict()
    title = data.get("title") or ""
    fields = {(f.get("name") or "").strip().lower(): (f.get("value") or "").strip() for f in data.get("fields") or []}
    if REJECTED.search(title):
        return None, "clan_treasury.rejected"
    if not APPROVED.search(title):
        return None, "clan_treasury.not_approved"

    def field(*names):
        for name in names:
            for key, value in fields.items():
                if key.startswith(name):
                    return value
        return ""

    clan = field("клан")
    tag_match = TAG.search(clan)
    to_treasury = _number(field("в казну"))
    respect = _number(field("респект")) or 0
    if not tag_match or to_treasury is None:
        return None, "clan_treasury.unknown_format"
    name = re.sub(r"`?\[[^\]]*\]`?", "", clan).strip(" `*_")
    return {
        "tag": tag_match.group(1).strip().upper(),
        "name": name[:60],
        "to_treasury": int(to_treasury),
        "gross": int(_number(field("сумма")) or 0) or None,
        "respect": float(respect),
        "member_id": _id(field("переводит")),
        "giver_id": _id(field("голду выдал", "выдал")),
        "approver_id": _id(data.get("description") or ""),
    }, None


# ============================================================
# КОМАНДЫ ЧЕРЕЗ УПОМИНАНИЕ
# ============================================================

def _words(message):
    me = message.guild.me.id
    text = (message.content or "").replace(f"<@{me}>", " ").replace(f"<@!{me}>", " ")
    own_role = message.guild.self_role
    if own_role is not None:
        text = text.replace(f"<@&{own_role.id}>", " ")
    return text.strip().lower()


def wants(message):
    """
    Это обращение к казне? (чтобы другие обработчики упоминаний не мешали)
    «казна» / «казна ТЕГ» — команда; длинный вопрос «как пополнить казну
    клана?» — это вопрос про кланы (clan_info), а не команда.
    """
    words = _words(message)
    if any(w in words for w in TRIGGERS_FIX + TRIGGERS_UNDO):
        return True
    parts = words.split()
    return bool(parts) and parts[0] in TRIGGERS_SHOW and len(parts) <= 2


def can_fix(guild, member):
    if core.is_owner_id(member.id):
        return True
    return core.level_value(core.member_level(guild, member)) >= core.level_value("admin")


def _source_ok(source):
    bot_id = (os.getenv("CLAN_SOURCE_BOT_ID") or "").strip()
    channel_id = (os.getenv("CLAN_SOURCE_CHANNEL_ID") or "").strip()
    if bot_id.isdigit() and source.author.id != int(bot_id):
        return "clan_treasury.wrong_bot"
    if not bot_id.isdigit() and not source.author.bot:
        return "clan_treasury.wrong_bot"
    if channel_id.isdigit() and source.channel.id != int(channel_id):
        return "clan_treasury.wrong_channel"
    return None


async def _referenced(message):
    reference = message.reference
    if reference is None or reference.message_id is None:
        return None
    resolved = reference.resolved
    if resolved is not None and not isinstance(resolved, discord.DeletedReferencedMessage):
        return resolved
    try:
        return await message.channel.fetch_message(reference.message_id)
    except discord.HTTPException:
        return None


async def _say(message, key, **params):
    try:
        await message.reply(t(key, **params), mention_author=False)
    except discord.HTTPException:
        pass


def summary_text(guild_id):
    rows = get_clans(guild_id)
    if not rows:
        return t("clan_treasury.empty")
    lines = [t("clan_treasury.line", tag=tag, name=name or "—", treasury=treasury, respect=f"{respect:g}")
             for tag, name, treasury, respect in rows]
    return t("clan_treasury.summary", list="\n".join(lines))


def clan_text(guild_id, tag):
    row = get_clan(guild_id, tag)
    if row is None:
        return t("clan_treasury.no_clan", tag=tag.upper())
    entries = [t("clan_treasury.entry", when=f"<t:{created}:d>", amount=f"{amount:+d}", respect=f"{respect:+g}",
                 reason=reason, who=f"<@{member}>" if member else "—")
               for amount, respect, reason, member, created in ledger(guild_id, tag)]
    return t("clan_treasury.clan", tag=row[0], name=row[1] or "—", treasury=row[2], respect=f"{row[3]:g}",
             entries="\n".join(entries) or "—")


async def on_message(message):
    if message.author.bot or message.guild is None or message.guild.me is None:
        return
    from emoji_export import pinged
    if not pinged(message) or not wants(message):
        return
    if not can_fix(message.guild, message.author):
        await _say(message, "clan_treasury.denied")
        return
    words = _words(message)
    if any(w in words for w in TRIGGERS_SHOW) and message.reference is None:
        tag = next((w for w in words.split() if w not in TRIGGERS_SHOW), None)
        area = ecosystem.scope(message.guild.id)  # связка серверов — одна казна на всех
        text = clan_text(area, tag.strip("[]`")) if tag else summary_text(area)
        try:
            await message.reply(text[:2000], mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass
        return
    source = await _referenced(message)
    if source is None:
        await _say(message, "clan_treasury.reply_needed")
        return
    problem = _source_ok(source)
    if problem:
        await _say(message, problem)
        return
    key = f"msg:{source.id}"
    if any(w in words for w in TRIGGERS_UNDO):
        await undo(message, key)
        return
    if not source.embeds:
        await _say(message, "clan_treasury.no_content")  # нет Message Content Intent или карточки
        return
    card, problem = parse_card(source.embeds[0])
    if problem:
        await _say(message, problem)
        return
    area = ecosystem.scope(message.guild.id)
    done, treasury, respect = record(
        area, card["tag"], card["name"], card["to_treasury"], card["respect"],
        "награда в казну",
        source_key=key, member_id=card["member_id"], giver_id=card["giver_id"], by_user=message.author.id,
        gross=card["gross"], direction=giver_direction(area, card["giver_id"]))
    if not done:
        await _say(message, "clan_treasury.already")
        return
    import clan_life
    bonus = clan_life.reward_bonus(area, card["tag"], card["gross"], key)  # топ-1 месяца: 85% вместо 75%
    if bonus:
        treasury += bonus
    core._log.info("clan treasury %s +%s (msg %s) by %s", card["tag"], card["to_treasury"], source.id,
                   message.author.id)
    await _say(message, "clan_treasury.fixed", tag=card["tag"], amount=card["to_treasury"],
               respect=f"{card['respect']:g}", treasury=treasury, total=f"{respect:g}")


async def undo(message, key):
    row = fixation_of(ecosystem.scope(message.guild.id), key)
    if row is None:
        await _say(message, "clan_treasury.nothing_to_undo")
        return
    tag, amount, respect = row
    done, treasury, total = record(ecosystem.scope(message.guild.id), tag, "", -amount, -respect, "отмена фиксации",
                                   source_key=f"undo:{key}", by_user=message.author.id)
    if not done:
        await _say(message, "clan_treasury.already_undone")
        return
    await _say(message, "clan_treasury.undone", tag=tag, amount=amount, treasury=treasury, total=f"{total:g}")


def register(bot):
    ensure_tables()
    bot.add_listener(on_message, "on_message")
