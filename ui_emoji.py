"""
Свои эмодзи кнопок бота. Два набора с одинаковыми именами после префикса:
- 3d — объёмные иконки (panel_art/emoji/ui3d/u3_*.png, scripts/render_ui3d.py);
- doodle — дудлы (panel_art/emoji/ui/ui_*.png, Figma: «Эмодзи для кнопок»).
При запуске бот заливает оба в свои эмодзи и на кнопках панелей меняет
обычные эмодзи на свои: ◀️ ▶️ → стрелки, «Назад», ➕, 🗑️, 💾…

Набор — в веб-панели ui_emoji_style: doodle (по умолчанию — дудлы Эмилии
из Figma), 3d (объёмные иконки ui3d/u3_* и дудлы форм doodle3d/d3_*) или
standard (обычные эмодзи). Стрелки перелистывания, «Назад» и «Отмена» бот
оформляет сам — в настройках их больше нет.
Кнопки, которые собирают участники в /embed, не трогаются никогда.

Перерисованный PNG бот замечает сам (emoji_sync: хэш файла) и заменяет
эмодзи в хранилище приложения при следующем запуске.
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
    # остальные кнопки панелей — дудлы из набора форм (тот же стиль)
    "💬": "d3_chat", "🛡": "d3_shield", "🧩": "ui_settings", "♾": "ui_refresh",
    "🧪": "ui_play", "👋": "d3_heart", "🧵": "ui_list", "📍": "d3_flag",
    "🧬": "d3_gem", "☀": "d3_sun", "📥": "ui_mail", "🎯": "d3_medal",
    "🔀": "ui_refresh", "🪝": "ui_web", "⚡": "d3_bolt", "🔢": "ui_list",
    "🎞": "d3_film", "🩺": "ui_search", "🪪": "ui_people", "🏠": "ui_back",
    "🟢": "ui_confirm", "🔴": "ui_block", "📎": "ui_folder", "🌟": "ui_star",
    # из объёмного набора: своей иконки кнопки нет — дудл форм (d3_ / плоский doodle_)
    "🌙": "d3_moon", "🔁": "ui_refresh", "🔗": "ui_web", "🔧": "d3_wrench", "👑": "d3_crown", "⏳": "d3_hourglass", "⏸": "ui_play", "🎉": "d3_balloon", "🔔": "d3_bell", "❤": "d3_heart", "🎮": "d3_gamepad", "🎵": "d3_music", "🎤": "d3_mic", "📷": "d3_camera", "🎁": "d3_gift",
}

_loaded = {}  # имя -> PartialEmoji из хранилища приложения
# для «Бункер → Диагностика»: что получилось при загрузке (папка -> состояние)
load_status = {}


def _key(text):
    return (text or "").replace("️", "").strip()


async def upload_folder(bot, folder, store):
    """Залить недостающие PNG из папки в эмодзи приложения; store[имя] = эмодзи."""
    state = load_status.setdefault(os.path.basename(folder), {"files": 0, "loaded": 0, "errors": [], "done": False})
    if not os.path.isdir(folder):
        state["errors"].append(f"нет папки {folder}")
        state["done"] = True
        return
    state["files"] = len([f for f in os.listdir(folder) if f.lower().endswith(".png")])
    try:
        existing = {emoji.name: emoji for emoji in await bot.fetch_application_emojis()}
    except Exception as error:  # нет application_id, сеть, права — не роняем бота
        core._log.warning("Эмодзи приложения недоступны: %s", error)
        state["errors"].append(f"список эмодзи приложения: {error}")
        state["done"] = True
        return
    import emoji_sync
    for file in sorted(os.listdir(folder)):
        name, ext = os.path.splitext(file)
        if ext.lower() != ".png":
            continue
        with open(os.path.join(folder, file), "rb") as handle:
            data = handle.read()
        emoji = existing.get(name)
        changed = emoji is not None and emoji_sync.is_changed("app", name, data)
        if emoji is None or changed:
            try:
                if changed:  # картинку перерисовали — старый эмодзи заменяем новым
                    await emoji.delete()
                fresh = await bot.create_application_emoji(name=name, image=data)
            except discord.HTTPException as error:
                core._log.warning("Эмодзи %s не загружен: %s", name, error)
                state["errors"].append(f"{name}: {error}")
                if not changed:
                    continue
                fresh = None
            if fresh is not None:
                if changed:
                    emoji_sync.remap_ids(emoji.id, fresh.id)
                    state["replaced"] = state.get("replaced", 0) + 1
                emoji_sync.remember("app", name, emoji_sync.file_hash(data))
                emoji = fresh
            elif changed:
                continue  # старый уже удалён, новый не залился — пропускаем имя
        store[name] = emoji
        state["loaded"] += 1
    state["done"] = True


async def load(bot):
    # прямо в _loaded: кнопки получают эмодзи по мере загрузки, а не после всех;
    # сначала дудлы Эмилии (набор по умолчанию), потом объёмные наборы
    await upload_folder(bot, UI_DIR, _loaded)
    await upload_folder(bot, UI3D_DIR, _loaded)
    await upload_folder(bot, DOODLE3D_DIR, _loaded)


def start(bot):
    """Из setup_hook: загрузка в фоне — первая заливка может упереться в лимиты Discord."""
    return asyncio.create_task(load(bot))


def style():
    return (core.get_setting("ui_emoji_style") or "doodle").strip().lower()


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
