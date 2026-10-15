"""
Свои эмодзи кнопок бота. Два набора с одинаковыми именами после префикса:
- 3d — объёмные иконки (panel_art/emoji/ui3d/u3_*.png, scripts/render_ui3d.py);
- doodle — дудлы (panel_art/emoji/ui/ui_*.png, Figma: «Эмодзи для кнопок»).
При запуске бот заливает оба в свои эмодзи и на кнопках панелей меняет
обычные эмодзи на свои: ◀️ ▶️ → стрелки, «Назад», ➕, 🗑️, 💾…

Набор — в веб-панели ui_emoji_style: 3d, doodle или standard (обычные).
В наборе 3d эмодзи форм (doodle_*) тоже показываются объёмными (doodle3d/d3_*),
а кнопки без своей иконки получают подходящий 3D-дудл (d3_* в MAP). Свои стрелки
перелистывания (nav_prev_emoji / nav_next_emoji) не трогаются.
Кнопки, которые собирают участники в /embed, не трогаются никогда.
"""

import asyncio
import os

import discord

import core

EMOJI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel_art", "emoji")
UI_DIR = os.path.join(EMOJI_DIR, "ui")
UI3D_DIR = os.path.join(EMOJI_DIR, "ui3d")
DOODLE3D_DIR = os.path.join(EMOJI_DIR, "doodle3d")

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
    # своей иконки кнопки нет — берётся 3D-дудл форм
    "☀": "d3_sun", "🌙": "d3_moon", "💬": "d3_chat", "🧵": "d3_chat", "📥": "ui_box", "🛡": "d3_shield",
    "🧩": "ui_box", "🎯": "ui_star", "🔀": "ui_refresh", "🔁": "ui_refresh", "🪝": "ui_send", "🔗": "ui_web",
    "⚡": "d3_bolt", "👋": "ui_people", "📍": "d3_flag", "🧬": "ui_settings", "🧪": "d3_gem",
    "♾": "d3_sparkles", "🟢": "ui_confirm", "🔴": "ui_cancel", "🔧": "d3_wrench", "👑": "d3_crown",
    "⏳": "d3_hourglass", "⏸": "ui_play", "🎉": "d3_balloon", "🔔": "d3_bell", "❤": "d3_heart",
    "🎮": "d3_gamepad", "🎵": "d3_music", "🎤": "d3_mic", "📷": "d3_camera", "🎁": "d3_gift",
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
    await upload_folder(bot, UI3D_DIR, store)
    await upload_folder(bot, UI_DIR, store)
    await upload_folder(bot, DOODLE3D_DIR, store)
    _loaded.update({name: discord.PartialEmoji(name=name, id=emoji.id) for name, emoji in store.items()})


def start(bot):
    """Из setup_hook: загрузка в фоне — первая заливка может упереться в лимиты Discord."""
    return asyncio.create_task(load(bot))


def style():
    return (core.get_setting("ui_emoji_style") or "3d").strip().lower()


def enabled():
    return style() != "standard"


def _doodle(name):
    """Плоский дудл формы doodle_<имя> (его заливает form_packs)."""
    import form_packs
    value = form_packs._doodles.get(name)
    return discord.PartialEmoji.from_str(value) if value else None


def _pick(name):
    """Эмодзи набора из настройки; не загрузилось — плоский с тем же именем."""
    if name is None:
        return None
    if name.startswith("d3_"):
        found = _loaded.get(name) if style() == "3d" else None
        return found or _doodle("doodle_" + name[len("d3_"):])
    if style() == "3d":
        found = _loaded.get("u3_" + name[len("ui_"):])
        if found is not None:
            return found
    return _loaded.get(name)


def doodle(emoji):
    """Эмодзи формы doodle_* -> объёмное d3_* (в наборе 3d). Строка остаётся строкой."""
    if emoji is None or style() != "3d":
        return emoji
    partial = emoji if isinstance(emoji, discord.PartialEmoji) else discord.PartialEmoji.from_str(str(emoji))
    if not partial.name or not partial.name.startswith("doodle_") or partial.id is None:
        return emoji
    found = _loaded.get("d3_" + partial.name[len("doodle_"):])
    if found is None:
        return emoji
    return found if isinstance(emoji, discord.PartialEmoji) else str(found)


def swap(emoji):
    """Обычное эмодзи -> своё (если загружено и включено), иначе как было."""
    if not enabled() or emoji is None:
        return emoji
    return _pick(MAP.get(_key(str(emoji)))) or emoji


def _swap_any(emoji):
    if emoji is None:
        return None
    if emoji.is_unicode_emoji():
        return swap(emoji.name)
    return doodle(emoji)


def apply(view):
    """Заменить эмодзи на кнопках и в меню панели. «Назад» и «◀️ Отмена» — эмодзи из подписи."""
    if not enabled():
        return
    for child in view.children:
        if isinstance(child, discord.ui.Select):
            for option in getattr(child, "options", []):
                if option.emoji is not None:
                    option.emoji = _swap_any(option.emoji)
            continue
        if not isinstance(child, discord.ui.Button):
            continue
        if child.emoji is not None:
            child.emoji = _swap_any(child.emoji)
            continue
        label = child.label or ""
        head, _, rest = label.partition(" ")
        name = "ui_back" if getattr(child, "_ui_back", False) and _key(head) in MAP else MAP.get(_key(head))
        emoji = _pick(name)
        if emoji is not None and rest:
            child.emoji = emoji
            child.label = rest
