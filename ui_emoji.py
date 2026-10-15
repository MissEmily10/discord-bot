"""
Эмодзи кнопок бота в стиле дудлов (panel_art/emoji/ui, Figma: «Эмодзи для
кнопок»). При запуске бот заливает их в свои эмодзи и на кнопках панелей
меняет обычные эмодзи на свои: ◀️ ▶️ → стрелки, «Назад», ➕, 🗑️, 💾…

Выключить — в веб-панели ui_emoji_style = standard. Свои стрелки
перелистывания (nav_prev_emoji / nav_next_emoji) не трогаются.
Кнопки, которые собирают участники в /embed, не трогаются никогда.
"""

import asyncio
import os

import discord

import core

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel_art", "emoji", "ui")

# обычное эмодзи -> имя дудла
MAP = {
    "◀": "ui_prev", "⏪": "ui_prev", "▶": "ui_next", "↩": "ui_back",
    "❌": "ui_cancel", "✅": "ui_confirm", "➕": "ui_add", "➖": "ui_remove",
    "🗑": "ui_delete", "✏": "ui_edit", "💾": "ui_save", "📤": "ui_send",
    "👁": "ui_preview", "🖼": "ui_image", "📝": "ui_text", "📦": "ui_box",
    "📚": "ui_library", "🎭": "ui_roles", "🏷": "ui_roles", "🔄": "ui_refresh",
    "♻": "ui_refresh", "⚙": "ui_settings", "🧹": "ui_clear", "🚫": "ui_block",
    "📨": "ui_mail", "✉": "ui_mail", "📣": "ui_announce", "📜": "ui_list",
    "📋": "ui_list", "📁": "ui_folder", "🗂": "ui_folder", "🌐": "ui_web",
    "⭐": "ui_star", "✨": "ui_star", "⚠": "ui_warn", "🔒": "ui_lock",
    "🔑": "ui_lock", "👥": "ui_people", "👤": "ui_people", "🕒": "ui_time",
    "🕘": "ui_time", "⏱": "ui_time", "🗓": "ui_time", "🔍": "ui_search",
    "🔘": "ui_button", "⏯": "ui_play", "🏆": "ui_trophy", "🎨": "ui_palette",
    "🖌": "ui_palette",
}

_loaded = {}  # имя -> PartialEmoji из хранилища приложения


def _key(text):
    return (text or "").replace("️", "").strip()


async def upload_folder(bot, folder, store):
    """Залить недостающие PNG из папки в эмодзи приложения; store[имя] = эмодзи."""
    if not os.path.isdir(folder):
        return
    try:
        existing = {emoji.name: emoji for emoji in await bot.fetch_application_emojis()}
    except (discord.HTTPException, AttributeError) as error:
        core._log.warning("Эмодзи приложения недоступны: %s", error)
        return
    for file in sorted(os.listdir(folder)):
        name, ext = os.path.splitext(file)
        if ext.lower() != ".png":
            continue
        emoji = existing.get(name)
        if emoji is None:
            try:
                with open(os.path.join(folder, file), "rb") as handle:
                    emoji = await bot.create_application_emoji(name=name, image=handle.read())
            except discord.HTTPException as error:
                core._log.warning("Эмодзи %s не загружен: %s", name, error)
                continue
        store[name] = emoji


async def load(bot):
    store = {}
    await upload_folder(bot, UI_DIR, store)
    _loaded.update({name: discord.PartialEmoji(name=name, id=emoji.id) for name, emoji in store.items()})


def start(bot):
    """Из setup_hook: загрузка в фоне — первая заливка может упереться в лимиты Discord."""
    return asyncio.create_task(load(bot))


def enabled():
    return (core.get_setting("ui_emoji_style") or "doodle").strip().lower() != "standard"


def swap(emoji):
    """Обычное эмодзи -> дудл (если загружен и включён), иначе как было."""
    if not enabled() or emoji is None:
        return emoji
    name = MAP.get(_key(str(emoji)))
    return _loaded.get(name, emoji) if name else emoji


def apply(view):
    """Заменить эмодзи на кнопках панели. «Назад» и «◀️ Отмена» — эмодзи из подписи."""
    if not enabled() or not _loaded:
        return
    for child in view.children:
        if not isinstance(child, discord.ui.Button):
            continue
        if child.emoji is not None:
            if child.emoji.is_unicode_emoji():
                child.emoji = swap(child.emoji.name)
            continue
        label = child.label or ""
        head, _, rest = label.partition(" ")
        name = MAP.get(_key(head))
        if name in _loaded and rest:
            child.emoji = _loaded[name]
            child.label = rest
