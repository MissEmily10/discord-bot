"""
clans.py
========
Реестр кланов в Purr. Клан **создаёт бот сервера** (сбор впятером, взнос),
всё остальное внутри клана ведёт Purr: состав, казна, дальше — продление,
биллборд, квесты, магазин. Данные — на связку серверов (ecosystem.scope).

Как клан попадает в Purr:
1. Сам: бот сервера пишет сообщение о создании клана (embed «Клан … создан»
   с тегом [ТЕГ], главой, замами, представителями) — Purr его читает.
   Нужны CLAN_SOURCE_BOT_ID (и, если хочется, CLAN_CREATE_CHANNEL_ID) в .env
   и Message Content Intent.
2. Ответом на такое сообщение: «@Purr зарегистрируй» — если сам не понял
   или Purr был выключен.
3. Вручную (пробный сервер без бота сервера):
   «@Purr новый клан [ТЕГ] Название @глава @зам @зам @пред @пред».
Посмотреть: «@Purr реестр кланов», «@Purr клан ТЕГ».

Регистрируют и смотрят реестр — админы Purr и создатель бота. Одно сообщение
бота сервера регистрируется один раз (ключ — его ID).

Полный перенос (создание клана и голда в коде Purr) — в плане, потом:
docs/CLANS_PURR_PLAN.md.
"""

import json
import os
import re
import time

import discord

import core
import ecosystem
from core import t
from database import get_connection

TAG = re.compile(r"\[([^\[\]`]{2,5})\]")
MENTION = re.compile(r"<@!?(\d+)>")
CREATED = re.compile(r"клан\w*[^\n]{0,60}?(созда|основ|зарегистр)|(созда|основ)\w*[^\n]{0,60}?клан", re.I)
TRIGGERS_REGISTER = ("зарегистрируй", "зарегистрировать", "регистрируй")
TRIGGERS_NEW = ("новый клан",)
TRIGGERS_LIST = ("реестр кланов", "кланы реестр", "список кланов")
TRIGGERS_SYNC = ("синхронизируй кланы", "синхронизировать кланы", "найди кланы")
TRIGGERS_SHOW = ("клан ",)


# ============================================================
# ХРАНИЛИЩЕ
# ============================================================

def ensure_tables():
    connection = get_connection()
    connection.execute("""CREATE TABLE IF NOT EXISTS clans (
        scope_id INTEGER NOT NULL, tag TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
        guild_id INTEGER, role_id INTEGER, leader_id INTEGER,
        deputies_json TEXT NOT NULL DEFAULT '[]', reps_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'active', source_key TEXT UNIQUE, registered_by INTEGER,
        created_at INTEGER NOT NULL, PRIMARY KEY (scope_id, tag)
    )""")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(clans)")}
    if "category_id" not in columns:  # категория клана (её создаёт бот сервера)
        connection.execute("ALTER TABLE clans ADD COLUMN category_id INTEGER")
    connection.commit()
    connection.close()


COLUMNS = ("scope_id", "tag", "name", "guild_id", "role_id", "leader_id", "deputies_json", "reps_json", "status",
           "source_key", "registered_by", "created_at", "category_id")


def _row(row):
    if row is None:
        return None
    clan = dict(zip(COLUMNS, row))
    clan["deputies"] = json.loads(clan.pop("deputies_json") or "[]")
    clan["reps"] = json.loads(clan.pop("reps_json") or "[]")
    return clan


def get_clan(scope_id, tag):
    ensure_tables()
    connection = get_connection()
    row = connection.execute(f"SELECT {', '.join(COLUMNS)} FROM clans WHERE scope_id = ? AND tag = ?",
                             (scope_id, (tag or "").upper())).fetchone()
    connection.close()
    return _row(row)


def get_clans(scope_id):
    ensure_tables()
    connection = get_connection()
    rows = connection.execute(f"SELECT {', '.join(COLUMNS)} FROM clans WHERE scope_id = ? AND status != 'dissolved' "
                              "ORDER BY created_at", (scope_id,)).fetchall()
    connection.close()
    return [_row(row) for row in rows]


def register(scope_id, clan, guild_id=None, source_key=None, by_user=None):
    """
    Записать клан в реестр. -> ("created" | "updated" | "duplicate", клан).
    Тот же тег — обновляем команду (повторное создание после роспуска, правка);
    то же сообщение бота сервера — второй раз не регистрируем.
    """
    ensure_tables()
    tag = clan["tag"].upper()
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if source_key and connection.execute("SELECT 1 FROM clans WHERE source_key = ?", (source_key,)).fetchone():
            connection.rollback()
            return "duplicate", get_clan(scope_id, tag)
        exists = connection.execute("SELECT 1 FROM clans WHERE scope_id = ? AND tag = ?", (scope_id, tag)).fetchone()
        values = (clan.get("name") or "", guild_id, clan.get("role_id"), clan.get("leader_id"))
        team = (json.dumps(clan["deputies"]) if clan.get("deputies") else None,
                json.dumps(clan["reps"]) if clan.get("reps") else None)
        if exists:
            # пустая команда (клан подхвачен по категории) не стирает уже записанную
            connection.execute(
                "UPDATE clans SET name = CASE WHEN ? != '' THEN ? ELSE name END, guild_id = COALESCE(?, guild_id), "
                "role_id = COALESCE(?, role_id), leader_id = COALESCE(?, leader_id), "
                "deputies_json = COALESCE(?, deputies_json), reps_json = COALESCE(?, reps_json), status = 'active', "
                "source_key = COALESCE(source_key, ?), category_id = COALESCE(?, category_id) "
                "WHERE scope_id = ? AND tag = ?",
                (values[0], *values, *team, source_key, clan.get("category_id"), scope_id, tag))
        else:
            connection.execute(
                f"INSERT INTO clans ({', '.join(COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
                (scope_id, tag, *values, team[0] or "[]", team[1] or "[]", source_key, by_user, int(time.time()),
                 clan.get("category_id")))
        connection.commit()
    finally:
        connection.close()
    return ("updated" if exists else "created"), get_clan(scope_id, tag)


# ============================================================
# РАЗБОР
# ============================================================

def _ids(text):
    seen = []
    for match in MENTION.finditer(text or ""):
        if int(match.group(1)) not in seen:
            seen.append(int(match.group(1)))
    return seen


def parse_creation(embed):
    """
    Сообщение бота сервера о создании клана -> (клан, None) или (None, ключ ошибки).
    Ищем: тег [ТЕГ], название, главу, замов, представителей — в полях или тексте.
    """
    data = embed if isinstance(embed, dict) else embed.to_dict()
    title, description = data.get("title") or "", data.get("description") or ""
    fields = [((f.get("name") or "").strip().lower(), (f.get("value") or "").strip()) for f in data.get("fields") or []]
    everything = "\n".join([title, description] + [f"{n}: {v}" for n, v in fields])
    if not CREATED.search(f"{title}\n{description}"):
        return None, "clans.not_creation"

    def field(*names):
        return next((value for key, value in fields for name in names if key.startswith(name)), "")

    def near(*names):
        """«Глава: @кто» в тексте, если полей нет."""
        for line in description.split("\n"):
            if any(name in line.lower() for name in names):
                return line
        return ""

    tag_match = TAG.search(field("тег", "клан") or title) or TAG.search(everything)
    if not tag_match:
        return None, "clans.no_tag"
    name = field("название", "имя") or re.sub(r"`?\[[^\]]*\]`?", "", field("клан") or "").strip(" `*_")
    if not name:
        after = title[tag_match.end():] if tag_match.group(0) in title else ""
        name = re.sub(r"(?i)клан|создан\w*|основан\w*|[!✅🎉:«»\"*_`]", " ", after).strip(" -—·")
    leader = (_ids(field("глав", "лидер", "основател")) or _ids(near("глав", "лидер")))[:1]
    deputies = _ids(field("зам")) or _ids(near("зам"))
    reps = _ids(field("представ")) or _ids(near("представ"))
    return {
        "tag": tag_match.group(1).strip().upper(),
        "name": " ".join(name.split())[:40],
        "leader_id": leader[0] if leader else None,
        "deputies": [i for i in deputies if i not in leader][:2],
        "reps": [i for i in reps if i not in leader and i not in deputies][:2],
    }, None


def parse_manual(text, author_id):
    """«новый клан [ТЕГ] Название @глава @зам @зам @пред @пред» -> (клан, None) | (None, ошибка)."""
    tag_match = TAG.search(text)
    if not tag_match:
        return None, "clans.manual_help"
    rest = text[tag_match.end():]
    ids = _ids(rest)
    name = MENTION.sub(" ", rest).strip(" -—·")
    leader = ids[:1] or [author_id]
    return {"tag": tag_match.group(1).strip().upper(), "name": " ".join(name.split())[:40],
            "leader_id": leader[0], "deputies": ids[1:3], "reps": ids[3:5]}, None


# Бот сервера создаёт клану категорию «ТЕГ · название», в ней текстовый
# «тег-название» и голосовой «ТЕГ · название» — по этому Purr и узнаёт клан.
CATEGORY = re.compile(r"^\s*([A-ZА-ЯЁ0-9]{2,5})\s*[·•|]\s*(\S.*?)\s*$")
CATEGORY_WAIT = 20  # каналы в категории появляются через пару секунд после неё


def clan_from_category(category):
    """Категория клана -> {tag, name, category_id} или None (не по шаблону или нет каналов клана)."""
    match = CATEGORY.match(getattr(category, "name", "") or "")
    if not match:
        return None
    tag, name = match.group(1), match.group(2)
    prefix = tag.lower()
    channels = list(getattr(category, "channels", None) or [])
    has_text = any(isinstance(c, discord.TextChannel) and c.name.lower().startswith(prefix) for c in channels)
    has_voice = any(isinstance(c, (discord.VoiceChannel, discord.StageChannel))
                    and c.name.upper().replace(" ", "").startswith(f"{tag}·") for c in channels)
    if not (has_text and has_voice):
        return None
    return {"tag": tag, "name": name[:40], "category_id": category.id, "deputies": [], "reps": []}


def find_role(guild, clan):
    """Роль клана на сервере: в названии [ТЕГ] или название клана."""
    if guild is None:
        return None
    tag, name = f"[{clan['tag']}]".lower(), (clan.get("name") or "").lower()
    plain = clan["tag"].lower()
    for role in guild.roles:
        low = role.name.lower()
        if tag in low or (name and low.strip() == name) or re.match(rf"^{re.escape(plain)}\s*[·•|]", low):
            return role
    # роли нет — та, что открывает категорию клана (кроме @everyone и ролей персонала)
    category = guild.get_channel(clan.get("category_id") or 0)
    for target, overwrite in (getattr(category, "overwrites", None) or {}).items():
        if isinstance(target, discord.Role) and not target.is_default() and overwrite.view_channel \
                and not target.permissions.administrator:
            return target
    return None


# ============================================================
# ТЕКСТЫ
# ============================================================

def mention(user_id):
    return f"<@{user_id}>" if user_id else "—"


def clan_text(guild, clan):
    import clan_treasury
    role = guild.get_role(clan["role_id"]) if guild and clan.get("role_id") else None
    money = clan_treasury.get_clan(clan["scope_id"], clan["tag"])
    return t("clans.card", tag=clan["tag"], name=clan["name"] or "—", leader=mention(clan["leader_id"]),
             deputies=", ".join(mention(i) for i in clan["deputies"]) or "—",
             reps=", ".join(mention(i) for i in clan["reps"]) or "—",
             members=len(role.members) if role else "—", role=role.mention if role else t("clans.no_role"),
             treasury=money[2] if money else 0, respect=f"{money[3]:g}" if money else "0",
             since=f"<t:{clan['created_at']}:d>")


def list_text(scope_id):
    clans = get_clans(scope_id)
    if not clans:
        return t("clans.empty")
    lines = [t("clans.line", tag=c["tag"], name=c["name"] or "—", leader=mention(c["leader_id"])) for c in clans]
    return t("clans.list", count=len(clans), list="\n".join(lines))


# ============================================================
# СООБЩЕНИЯ
# ============================================================

def _source_bot():
    raw = os.getenv("CLAN_SOURCE_BOT_ID", "").strip()
    return int(raw) if raw.isdigit() else None


def _create_channel():
    raw = os.getenv("CLAN_CREATE_CHANNEL_ID", "").strip()
    return int(raw) if raw.isdigit() else None


def from_server_bot(message):
    """Сообщение бота сервера (тот же ID, что для казны) в канале создания кланов (если задан)."""
    source = _source_bot()
    if source is None or getattr(message.author, "id", None) != source:
        return False
    channel = _create_channel()
    return channel is None or getattr(message.channel, "id", None) == channel


def _words(message):
    import clan_treasury
    return clan_treasury._words(message)


def wants(message):
    """Команда реестра? (чтобы вопросы к нейросети и перенос эмодзи не мешали)"""
    from emoji_export import pinged
    if message.guild is None or message.guild.me is None or not pinged(message):
        return False
    words = _words(message)
    return (words.startswith(TRIGGERS_NEW) or words.startswith(TRIGGERS_LIST) or words.startswith(TRIGGERS_SYNC)
            or words in TRIGGERS_REGISTER
            or any(words.startswith(w) for w in TRIGGERS_REGISTER)
            or bool(re.fullmatch(r"клан\s+\[?[^\s\[\]]{2,5}\]?", words)))


def can_manage(guild, member):
    if core.is_owner_id(member.id):
        return True
    return core.level_value(core.member_level(guild, member)) >= core.level_value("admin")


async def _say(message, key, **params):
    try:
        await message.reply(t(key, **params)[:2000], mention_author=False,
                            allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        pass


async def save(message, clan, source_key=None):
    """Записать + найти роль клана; ответить, что получилось."""
    role = find_role(message.guild, clan)
    if role is not None:
        clan["role_id"] = role.id
    result, saved = register(ecosystem.scope(message.guild.id), clan, guild_id=message.guild.id,
                             source_key=source_key, by_user=getattr(message.author, "id", None))
    core._log.info("clan %s %s in scope %s", saved["tag"], result, saved["scope_id"])
    return result, saved


async def on_server_bot(message):
    """Бот сервера написал (или изменил) сообщение — если это создание клана, регистрируем."""
    if message.guild is None or not from_server_bot(message) or not message.embeds:
        return
    clan, problem = parse_creation(message.embeds[0])
    if problem:
        return
    result, saved = await save(message, clan, source_key=f"msg:{message.id}")
    if result == "duplicate":
        return
    try:  # видно, что Purr подхватил клан
        await message.add_reaction("🛡️")
    except discord.HTTPException:
        pass


async def on_message(message):
    if message.author.bot:
        if from_server_bot(message):
            await on_server_bot(message)
        return
    if not wants(message):
        return
    if not can_manage(message.guild, message.author):
        await _say(message, "clans.denied")
        return
    words = _words(message)
    scope_id = ecosystem.scope(message.guild.id)
    if words.startswith(TRIGGERS_LIST):
        await _say(message, "clans.list_wrap", text=list_text(scope_id))
        return
    if words.startswith(TRIGGERS_SYNC):
        found = await sync_categories(message.guild)
        await _say(message, "clans.synced", count=len(found), tags=", ".join(f"[{tag}]" for tag in found) or "—")
        return
    if re.fullmatch(r"клан\s+\[?[^\s\[\]]{2,5}\]?", words):
        tag = words.split()[1].strip("[]")
        clan = get_clan(scope_id, tag)
        await _say(message, "clans.card_wrap", text=clan_text(message.guild, clan)) if clan else \
            await _say(message, "clans.not_found", tag=tag.upper())
        return
    if words.startswith(TRIGGERS_NEW):
        raw = message.content or ""
        clan, problem = parse_manual(raw[raw.lower().find("новый клан") + len("новый клан"):], message.author.id)
        if problem:
            await _say(message, problem)
            return
        result, saved = await save(message, clan)
        await _say(message, f"clans.{result}", text=clan_text(message.guild, saved))
        return
    # «зарегистрируй» ответом на сообщение бота сервера о создании клана
    reference = message.reference
    source = getattr(reference, "resolved", None) if reference else None
    if source is None or isinstance(source, discord.DeletedReferencedMessage):
        await _say(message, "clans.reply_needed")
        return
    if not source.embeds:
        await _say(message, "clans.no_embed")
        return
    clan, problem = parse_creation(source.embeds[0])
    if problem:
        await _say(message, problem)
        return
    result, saved = await save(message, clan, source_key=f"msg:{source.id}")
    if result == "duplicate":
        await _say(message, "clans.duplicate", tag=saved["tag"] if saved else clan["tag"])
        return
    await _say(message, f"clans.{result}", text=clan_text(message.guild, saved))


async def on_message_edit(before, after):
    # бот сервера часто сначала пишет «сбор…», а потом меняет на «клан создан»
    if after.author.bot and from_server_bot(after):
        await on_server_bot(after)


async def save_category_clan(guild, clan):
    role = find_role(guild, clan)
    if role is not None:
        clan["role_id"] = role.id
    return register(ecosystem.scope(guild.id), clan, guild_id=guild.id, source_key=f"cat:{clan['category_id']}")


async def sync_categories(guild):
    """Все категории по шаблону клана на сервере -> в реестр. -> теги новых/обновлённых."""
    found = []
    for category in guild.categories:
        clan = clan_from_category(category)
        if clan is not None:
            await save_category_clan(guild, clan)
            found.append(clan["tag"])
    return found


async def on_category_create(channel):
    if not isinstance(channel, discord.CategoryChannel) or not CATEGORY.match(channel.name or ""):
        return
    import asyncio
    await asyncio.sleep(CATEGORY_WAIT)  # бот сервера создаёт каналы следом за категорией
    category = channel.guild.get_channel(channel.id)
    clan = clan_from_category(category) if category else None
    if clan is not None:
        result, saved = await save_category_clan(channel.guild, clan)
        core._log.info("clan %s %s from category %s", saved["tag"], result, channel.id)


def register_listeners(bot):
    ensure_tables()
    bot.add_listener(on_message, "on_message")
    bot.add_listener(on_category_create, "on_guild_channel_create")
    bot.add_listener(on_message_edit, "on_message_edit")
