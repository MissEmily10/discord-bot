"""
Счётчик активности сервера — данные для переменных ({messages_top}, {word_leader:…},
{voice_time_top}, {reactions_top}…).

Бот хранит только ЧИСЛА по дням: сколько сообщений написал человек в канале,
сколько реакций поставил/получил, сколько минут просидел в войсе, сколько раз
написал отслеживаемое слово. Тексты сообщений не сохраняются.

Слова: бот считает только те слова, которые встречаются в переменных
({word_count:какашка} и т. п.) — слово начинают считать, как только переменная
появилась в каком-нибудь тексте. Совпадение — по началу слова без учёта
регистра: «какашк» найдёт «какашка», «какашку», «какашками».
Чтобы бот видел текст сообщений, нужен Message Content Intent
(Developer Portal → Bot → Message Content Intent и MESSAGE_CONTENT_INTENT=1).
Без него считаются сообщения, реакции и войс, но не слова.

Всё копится в памяти и раз в минуту пишется в базу. Старше 120 дней — удаляется.
"""

import asyncio
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta

import discord

from database import get_connection

_log = logging.getLogger(__name__)
FLUSH_EVERY = 60
KEEP_DAYS = 120
MAX_TRACKED_WORDS = 50

_messages = defaultdict(int)     # (guild, day, user, channel) -> n
_words = defaultdict(int)        # (guild, word, day, user) -> n
_reactions = defaultdict(int)    # (guild, day, user, kind) -> n; kind: given / received
_emoji = defaultdict(int)        # (guild, day, emoji) -> n
_voice = defaultdict(int)        # (guild, day, user, channel) -> секунды
_members = defaultdict(int)      # (guild, day, kind) -> n; kind: joined / left
_in_voice = {}                   # (guild, user) -> (channel_id, с какого момента)
_tracked = {}                    # guild -> {слово}
_started = time.time()
content_seen = False             # бот хоть раз видел текст чужого сообщения (Message Content Intent работает)


def ensure_tables():
    connection = get_connection()
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS act_messages (guild_id INTEGER, day TEXT, user_id INTEGER, channel_id INTEGER,
            count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, day, user_id, channel_id));
        CREATE TABLE IF NOT EXISTS act_words (guild_id INTEGER, word TEXT, day TEXT, user_id INTEGER,
            count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, word, day, user_id));
        CREATE TABLE IF NOT EXISTS act_reactions (guild_id INTEGER, day TEXT, user_id INTEGER, kind TEXT,
            count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, day, user_id, kind));
        CREATE TABLE IF NOT EXISTS act_emoji (guild_id INTEGER, day TEXT, emoji TEXT,
            count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, day, emoji));
        CREATE TABLE IF NOT EXISTS act_voice (guild_id INTEGER, day TEXT, user_id INTEGER, channel_id INTEGER,
            seconds INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, day, user_id, channel_id));
        CREATE TABLE IF NOT EXISTS act_members (guild_id INTEGER, day TEXT, kind TEXT,
            count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, day, kind));
        CREATE TABLE IF NOT EXISTS act_tracked_words (guild_id INTEGER, word TEXT, since INTEGER NOT NULL,
            PRIMARY KEY (guild_id, word));
    """)
    connection.commit()
    connection.close()


def today():
    import live
    return live.now_local().strftime("%Y-%m-%d")


# ============================================================
# ОТСЛЕЖИВАЕМЫЕ СЛОВА
# ============================================================

def normalize_word(word):
    return re.sub(r"\s+", " ", (word or "").strip().lower().replace("ё", "е"))[:40]


def tracked_words(guild_id):
    if guild_id not in _tracked:
        connection = get_connection()
        ensure_tables()
        rows = connection.execute("SELECT word FROM act_tracked_words WHERE guild_id = ?", (guild_id,)).fetchall()
        connection.close()
        _tracked[guild_id] = {row[0] for row in rows}
    return _tracked[guild_id]


def track_word(guild_id, word):
    """Начать считать слово (вызывается, когда слово встретилось в переменной). -> since (ts)."""
    word = normalize_word(word)
    words = tracked_words(guild_id)
    if not word or (word not in words and len(words) >= MAX_TRACKED_WORDS):
        return None
    if word not in words:
        connection = get_connection()
        connection.execute("INSERT OR IGNORE INTO act_tracked_words (guild_id, word, since) VALUES (?, ?, ?)",
                           (guild_id, word, int(time.time())))
        connection.commit()
        connection.close()
        words.add(word)
    return word_since(guild_id, word)


def word_since(guild_id, word):
    connection = get_connection()
    ensure_tables()
    row = connection.execute("SELECT since FROM act_tracked_words WHERE guild_id = ? AND word = ?",
                             (guild_id, normalize_word(word))).fetchone()
    connection.close()
    return row[0] if row else None


_WORD = re.compile(r"[\w-]+", re.UNICODE)


def count_words(text, words):
    """-> {слово: сколько раз} — совпадение по началу слова (корню) или по фразе."""
    low = (text or "").lower().replace("ё", "е")
    tokens = _WORD.findall(low)
    found = {}
    for word in words:
        if " " in word:
            hits = low.count(word)
        else:
            hits = sum(1 for token in tokens if token.startswith(word))
        if hits:
            found[word] = hits
    return found


# ============================================================
# СОБЫТИЯ
# ============================================================

async def on_message(message):
    global content_seen
    if message.guild is None or message.author.bot or message.webhook_id:
        return
    day = today()
    channel_id = getattr(message.channel, "parent_id", None) if isinstance(message.channel, discord.Thread) \
        else message.channel.id
    _messages[(message.guild.id, day, message.author.id, channel_id or message.channel.id)] += 1
    if message.content:
        content_seen = True
        for word, hits in count_words(message.content, tracked_words(message.guild.id)).items():
            _words[(message.guild.id, word, day, message.author.id)] += hits


async def on_raw_reaction_add(payload):
    if payload.guild_id is None or (payload.member and payload.member.bot):
        return
    day = today()
    _reactions[(payload.guild_id, day, payload.user_id, "given")] += 1
    if payload.message_author_id and payload.message_author_id != payload.user_id:
        _reactions[(payload.guild_id, day, payload.message_author_id, "received")] += 1
    _emoji[(payload.guild_id, day, str(payload.emoji))] += 1


def _close_voice(guild_id, user_id, now=None):
    state = _in_voice.pop((guild_id, user_id), None)
    if state:
        channel_id, since = state
        _voice[(guild_id, today(), user_id, channel_id)] += int((now or time.time()) - since)


async def on_voice_state_update(member, before, after):
    if member.bot:
        return
    if before.channel is not None and (after.channel is None or after.channel.id != before.channel.id):
        _close_voice(member.guild.id, member.id)
    if after.channel is not None and (member.guild.id, member.id) not in _in_voice:
        _in_voice[(member.guild.id, member.id)] = (after.channel.id, time.time())


async def on_member_join(member):
    if not member.bot:
        _members[(member.guild.id, today(), "joined")] += 1


async def on_member_remove(member):
    if not member.bot:
        _members[(member.guild.id, today(), "left")] += 1
    _close_voice(member.guild.id, member.id)


# ============================================================
# ЗАПИСЬ В БАЗУ
# ============================================================

def flush():
    """Накопленное — в базу. Войс: идущие сессии засчитываются по сей момент."""
    now = time.time()
    for key, (channel_id, since) in list(_in_voice.items()):
        _voice[(key[0], today(), key[1], channel_id)] += int(now - since)
        _in_voice[key] = (channel_id, now)
    batches = (
        ("act_messages", "guild_id, day, user_id, channel_id", "count", _messages),
        ("act_words", "guild_id, word, day, user_id", "count", _words),
        ("act_reactions", "guild_id, day, user_id, kind", "count", _reactions),
        ("act_emoji", "guild_id, day, emoji", "count", _emoji),
        ("act_voice", "guild_id, day, user_id, channel_id", "seconds", _voice),
        ("act_members", "guild_id, day, kind", "count", _members),
    )
    if not any(store for *_, store in batches):
        return
    ensure_tables()
    connection = get_connection()
    try:
        for table, keys, column, store in batches:
            items = list(store.items())
            store.clear()
            marks = ", ".join("?" * (len(keys.split(",")) + 1))
            connection.executemany(
                f"INSERT INTO {table} ({keys}, {column}) VALUES ({marks}) "
                f"ON CONFLICT ({keys}) DO UPDATE SET {column} = {column} + excluded.{column}",
                [(*key, value) for key, value in items if value])
        connection.commit()
    finally:
        connection.close()


def prune():
    cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    connection = get_connection()
    for table in ("act_messages", "act_words", "act_reactions", "act_emoji", "act_voice", "act_members"):
        connection.execute(f"DELETE FROM {table} WHERE day < ?", (cutoff,))
    connection.commit()
    connection.close()


async def _loop(bot):
    await bot.wait_until_ready()
    # кто уже сидел в войсе, когда бот запустился
    for guild in bot.guilds:
        for channel in [*guild.voice_channels, *guild.stage_channels]:
            for member in channel.members:
                if not member.bot:
                    _in_voice.setdefault((guild.id, member.id), (channel.id, time.time()))
    last_prune = 0
    while not bot.is_closed():
        await asyncio.sleep(FLUSH_EVERY)
        try:
            flush()
            if time.time() - last_prune > 24 * 3600:
                prune()
                last_prune = time.time()
        except Exception:
            _log.exception("activity flush")


def register(bot):
    ensure_tables()
    for name in ("on_message", "on_raw_reaction_add", "on_voice_state_update", "on_member_join",
                 "on_member_remove"):
        bot.add_listener(globals()[name], name)


def start(bot):
    return asyncio.create_task(_loop(bot))


# ============================================================
# ЧТЕНИЕ (для переменных)
# ============================================================

PERIODS = {"сегодня": 0, "today": 0, "вчера": -1, "yesterday": -1, "неделя": 7, "неделю": 7, "week": 7,
           "месяц": 30, "month": 30, "всё": None, "все": None, "всегда": None, "all": None}


def period_range(period, today_date):
    """-> (с какого дня, по какой) строками ГГГГ-ММ-ДД; None — без ограничения."""
    if period is None:
        return None, None
    if period == 0:
        day = today_date.strftime("%Y-%m-%d")
        return day, day
    if period == -1:
        day = (today_date - timedelta(days=1)).strftime("%Y-%m-%d")
        return day, day
    return (today_date - timedelta(days=period - 1)).strftime("%Y-%m-%d"), today_date.strftime("%Y-%m-%d")


def _where(guild_id, period, today_date, channels=None, extra=""):
    start, end = period_range(period, today_date)
    sql, params = "guild_id = ?", [guild_id]
    if start:
        sql += " AND day BETWEEN ? AND ?"
        params += [start, end]
    if channels is not None:
        sql += f" AND channel_id IN ({', '.join('?' * len(channels)) or 'NULL'})"
        params += list(channels)
    return sql + extra, params


def _rows(sql, params):
    flush()  # свежие цифры — с последней минуты тоже
    ensure_tables()
    connection = get_connection()
    rows = connection.execute(sql, params).fetchall()
    connection.close()
    return rows


def messages_by_user(guild_id, period, today_date, channels=None):
    where, params = _where(guild_id, period, today_date, channels)
    return _rows(f"SELECT user_id, SUM(count) AS n FROM act_messages WHERE {where} GROUP BY user_id ORDER BY n DESC",
                 params)


def messages_by_channel(guild_id, period, today_date):
    where, params = _where(guild_id, period, today_date)
    return _rows(f"SELECT channel_id, SUM(count) AS n FROM act_messages WHERE {where} GROUP BY channel_id "
                 "ORDER BY n DESC", params)


def words_by_user(guild_id, word, period, today_date):
    where, params = _where(guild_id, period, today_date, extra=" AND word = ?")
    return _rows(f"SELECT user_id, SUM(count) AS n FROM act_words WHERE {where} GROUP BY user_id ORDER BY n DESC",
                 params + [normalize_word(word)])


def reactions_by_user(guild_id, kind, period, today_date):
    where, params = _where(guild_id, period, today_date, extra=" AND kind = ?")
    return _rows(f"SELECT user_id, SUM(count) AS n FROM act_reactions WHERE {where} GROUP BY user_id "
                 "ORDER BY n DESC", params + [kind])


def emoji_top(guild_id, period, today_date):
    where, params = _where(guild_id, period, today_date)
    return _rows(f"SELECT emoji, SUM(count) AS n FROM act_emoji WHERE {where} GROUP BY emoji ORDER BY n DESC", params)


def voice_by_user(guild_id, period, today_date, channels=None):
    where, params = _where(guild_id, period, today_date, channels)
    return _rows(f"SELECT user_id, SUM(seconds) AS n FROM act_voice WHERE {where} GROUP BY user_id ORDER BY n DESC",
                 params)


def members_moved(guild_id, kind, period, today_date):
    where, params = _where(guild_id, period, today_date, extra=" AND kind = ?")
    row = _rows(f"SELECT SUM(count) FROM act_members WHERE {where}", params + [kind])
    return (row[0][0] or 0) if row else 0


def tracking_since(guild_id):
    """С какого дня бот вообще что-то считает на сервере (самый ранний день в базе)."""
    row = _rows("SELECT MIN(day) FROM act_messages WHERE guild_id = ?", [guild_id])
    return row[0][0] if row and row[0][0] else None
