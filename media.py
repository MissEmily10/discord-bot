"""
media.py
========
Чужие эмодзи и файлы в Message Build.

Эмодзи. Бот может показать кастомный эмодзи (на кнопке, в списке, в тексте),
только если «видит» его: эмодзи с сервера, где он есть, или эмодзи из
собственного хранилища приложения (до 2000 штук, работают на любом сервере
и не занимают слоты сервера). Эмодзи с чужого сервера бот один раз скачивает
и кладёт в хранилище приложения; дальше подставляется копия. Копию не
удаляем — иначе эмодзи пропал бы с уже отправленных кнопок.

Файлы. Ссылки на вложения Discord (cdn.discordapp.com/attachments/…) живут
около суток: в них подпись, которая истекает, и картинка в embed'е пропадает.
Такие картинки бот скачивает, пока ссылка жива, и хранит у себя
(веб-панель, /assets/…) — получается постоянная ссылка.
"""

import logging
import re
import time

import discord

import core
from database import get_connection

_log = logging.getLogger(__name__)

EMOJI = re.compile(r"<(a?):(\w{2,32}):(\d{15,25})>")
DISCORD_FILE = re.compile(
    r"^https?://(?:cdn\.discordapp\.com|media\.discordapp\.net)/(?:ephemeral-)?attachments/", re.IGNORECASE,
)
EMOJI_MAX_BYTES = 256 * 1024
FILE_MAX_BYTES = 8 * 1024 * 1024

_app_emoji_ids = set()
_app_emoji_names = set()


# ============================================================
# ХРАНИЛИЩЕ
# ============================================================

def ensure_table():
    connection = get_connection()
    connection.execute("""CREATE TABLE IF NOT EXISTS emoji_mirrors (
        source_id INTEGER PRIMARY KEY, app_emoji_id INTEGER NOT NULL, name TEXT NOT NULL,
        animated INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL
    )""")
    connection.commit()
    connection.close()


def _mirror_row(source_id):
    connection = get_connection()
    row = connection.execute(
        "SELECT app_emoji_id, name, animated FROM emoji_mirrors WHERE source_id = ?", (source_id,)
    ).fetchone()
    connection.close()
    return row


def _save_mirror(source_id, emoji):
    connection = get_connection()
    connection.execute(
        "INSERT OR REPLACE INTO emoji_mirrors (source_id, app_emoji_id, name, animated, created_at) VALUES (?, ?, ?, ?, ?)",
        (source_id, emoji.id, emoji.name, int(emoji.animated), int(time.time())),
    )
    connection.commit()
    connection.close()


async def load(bot):
    """Из setup_hook: какие эмодзи уже лежат в хранилище приложения."""
    ensure_table()
    try:
        emojis = await bot.fetch_application_emojis()
    except (discord.HTTPException, AttributeError) as error:
        _log.warning("Не удалось загрузить эмодзи приложения: %s", error)
        return
    _app_emoji_ids.clear()
    _app_emoji_names.clear()
    for emoji in emojis:
        _app_emoji_ids.add(emoji.id)
        _app_emoji_names.add(emoji.name)


# ============================================================
# ЭМОДЗИ
# ============================================================

def usable(bot, emoji_id):
    """Видит ли бот этот эмодзи: с одного из его серверов или из его хранилища."""
    return emoji_id in _app_emoji_ids or bot.get_emoji(emoji_id) is not None


def _free_name(name, emoji_id):
    base = re.sub(r"\W", "_", name)[:24].strip("_") or "emoji"
    candidate = base if len(base) >= 2 else f"{base}_e"
    if candidate in _app_emoji_names:
        candidate = f"{base[:24]}_{str(emoji_id)[-6:]}"
    return candidate[:32]


async def mirror_emoji(bot, text):
    """
    «<:name:id>» -> строка эмодзи, которую бот может показать, или None.
    Unicode-эмодзи и пустое значение возвращаются как есть.
    """
    match = EMOJI.fullmatch((text or "").strip())
    if not match:
        return text
    animated, name, source_id = match[1] == "a", match[2], int(match[3])
    if usable(bot, source_id):
        return text
    row = _mirror_row(source_id)
    if row and row[0] in _app_emoji_ids:
        return f"<{'a' if row[2] else ''}:{row[1]}:{row[0]}>"
    ext = "gif" if animated else "png"
    data = await core.fetch_bytes(f"https://cdn.discordapp.com/emojis/{source_id}.{ext}?size=128",
                                  max_bytes=EMOJI_MAX_BYTES)
    if data is None:
        return None
    try:
        emoji = await bot.create_application_emoji(name=_free_name(name, source_id), image=data)
    except discord.HTTPException as error:  # лимит 2000, битая картинка
        _log.warning("Эмодзи %s не скопирован: %s", source_id, error)
        return None
    _app_emoji_ids.add(emoji.id)
    _app_emoji_names.add(emoji.name)
    _save_mirror(source_id, emoji)
    _log.info("Эмодзи %s скопирован в хранилище приложения как %s", source_id, emoji.id)
    return str(emoji)


async def mirror_in_text(bot, text, failed):
    """Все кастомные эмодзи в тексте -> видимые боту; не вышло — остаётся :name:."""
    if not text or "<" not in text:
        return text
    result, last = [], 0
    for match in EMOJI.finditer(text):
        result.append(text[last:match.start()])
        mirrored = await mirror_emoji(bot, match[0])
        if mirrored is None:
            failed.append(match[0])
            mirrored = f":{match[2]}:"
        result.append(mirrored)
        last = match.end()
    result.append(text[last:])
    return "".join(result)


# ============================================================
# ФАЙЛЫ
# ============================================================

async def rehost_url(url):
    """
    Ссылка на вложение Discord -> постоянная ссылка у бота.
    -> (ссылка, None) или (None, ключ ошибки). Прочие ссылки не трогаем.
    """
    if not url or not DISCORD_FILE.match(url):
        return url, None
    import web_panel
    if not web_panel.panel_url():
        return url, "media.no_panel"
    data = await core.fetch_bytes(url, timeout=15, max_bytes=FILE_MAX_BYTES)
    if data is None:
        return None, "media.expired"
    import asyncio
    name, error = await asyncio.to_thread(web_panel.save_asset, data)
    if error:
        return None, "media.bad_file"
    return f"{web_panel.panel_url()}/assets/{name}", None


IMAGE_KEYS = ("image", "thumbnail", "author_icon", "footer_icon")
TEXT_KEYS = ("title", "description", "author_name", "footer_text")


def needs_work(bot, payload):
    """Есть ли что копировать (чтобы заранее отложить ответ Discord'у)."""
    texts = [payload.get("content") or ""]
    urls = []
    for embed in payload.get("embeds") or []:
        texts += [str(embed.get(key) or "") for key in TEXT_KEYS]
        texts += [str(f.get("name") or "") + str(f.get("value") or "") for f in embed.get("fields") or []]
        urls += [embed.get(key) for key in IMAGE_KEYS]
    items = list(payload.get("buttons") or []) + list(((payload.get("interactive") or {}).get("options")) or [])
    texts += [str(item.get("emoji") or "") for item in items]
    foreign = any(not usable(bot, int(m[3])) for text in texts for m in EMOJI.finditer(text))
    return foreign or any(url and DISCORD_FILE.match(url) for url in urls)


async def prepare(bot, payload):
    """
    Привести payload (content / embeds / buttons / interactive) к виду, который
    переживёт отправку и время: чужие эмодзи -> копии бота, вложения Discord ->
    постоянные ссылки. Меняет payload на месте.
    -> список предупреждений (ключи текстов с параметрами).
    """
    failed_emojis, warnings = [], []
    payload["content"] = await mirror_in_text(bot, payload.get("content") or "", failed_emojis)
    for embed in payload.get("embeds") or []:
        for key in TEXT_KEYS:
            if embed.get(key):
                embed[key] = await mirror_in_text(bot, str(embed[key]), failed_emojis)
        for field in embed.get("fields") or []:
            field["name"] = await mirror_in_text(bot, str(field.get("name") or ""), failed_emojis)
            field["value"] = await mirror_in_text(bot, str(field.get("value") or ""), failed_emojis)
        for key in IMAGE_KEYS:
            url, error = await rehost_url(embed.get(key))
            if error:
                warnings.append((error, {"field": key}))
            if url != embed.get(key) and not (error and url):
                embed[key] = url
    items = list(payload.get("buttons") or []) + list(((payload.get("interactive") or {}).get("options")) or [])
    for item in items:
        if not item.get("emoji"):
            continue
        mirrored = await mirror_emoji(bot, item["emoji"])
        if mirrored is None:
            failed_emojis.append(item["emoji"])
            item["emoji"] = None  # с недоступным эмодзи Discord не примет кнопку целиком
        else:
            item["emoji"] = mirrored
    if failed_emojis:
        warnings.append(("media.emoji_failed", {"count": len(failed_emojis)}))
    return warnings
