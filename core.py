"""
core.py
========
Общий фундамент РЕПЛИКА ОС.

Здесь живёт всё, что раньше было размазано по bot.py и extended_modules.py
и дублировалось:
- уровни доступа и их подписи
- PanelView — базовый класс с автоматической кнопкой "Назад"
- EmbedPaginator — переиспользуемая пагинация списков
- get_setting/set_setting — рантайм-настройки без хардкода (бэкенд под /design)
- action_registry — реестр действий для кнопок/select с проверкой по уровню
- fetch_bytes — асинхронная загрузка файлов (замена блокирующего urllib)
"""

import json
import os
import time

import aiohttp
import discord

from database import (
    get_setting as _db_get_setting,
    set_setting as _db_set_setting,
    get_action,
    get_actions,
    upsert_action,
    set_action_enabled,
    get_access_level,
    get_command_access,
    get_member_info,
    get_all_role_access,
    is_user_denied,
)

OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# Минимальный уровень для встроенных команд. Команды, добавленные модулями,
# могут дополнять этот словарь через core.COMMAND_MIN_LEVELS[name] = level.
COMMAND_MIN_LEVELS = {
    "ping": "staff",
    "access": "staff",
    "embed": "staff",
    "buttons": "staff",
    "forms": "staff",
    "select": "staff",
    "templates": "staff",
    "messages": "staff",
    "webhooks": "admin",
}


# =========================
# ACCESS LEVELS
# =========================

ACCESS_LEVELS = {
    "limited": 0,
    "member": 1,
    "staff": 2,
    "admin": 3,
    "owner": 4,
}

ACCESS_LABELS = {
    "limited": "◽ Ограниченный пользователь",
    "member": "👤 Пользователь",
    "staff": "🔧 Staff",
    "admin": "🛡️ Администратор бота",
    "owner": "👑 Владелец",
}


def level_value(level):
    return ACCESS_LEVELS.get(level, 0)


# =========================
# PERMISSION CHECKS
# ЕДИНСТВЕННОЕ место, где это должно проверяться. Раньше admin() в
# extended_modules.py дублировал эту же логику отдельно — если бы кто-то
# поменял правило в одном месте и забыл про второе, права бы разъехались.
# =========================

def is_owner(interaction):
    return interaction.user.id == OWNER_ID


def get_user_level(interaction):
    if is_owner(interaction):
        return "owner"
    if interaction.guild is None:
        return "member"

    if is_user_denied(interaction.guild.id, interaction.user.id) is not None:
        return "limited"

    guild_id = interaction.guild.id
    levels = [get_access_level(guild_id, interaction.user.id)]

    role_levels = {
        role_id: (access_level, expires_at)
        for role_id, access_level, expires_at in get_all_role_access(guild_id)
    }
    now = int(time.time())
    for role in getattr(interaction.user, "roles", []):
        role_entry = role_levels.get(role.id)
        if role_entry is None:
            continue
        access_level, expires_at = role_entry
        if expires_at is None or expires_at > now:
            levels.append(access_level)

    return max(levels, key=level_value)


def can_manage_access(interaction):
    return level_value(get_user_level(interaction)) >= level_value("admin")


def can_view_access(interaction):
    return level_value(get_user_level(interaction)) >= level_value("staff")


def can_assign_level(interaction, target_level):
    actor_level = get_user_level(interaction)
    if target_level == "owner":
        return is_owner(interaction)
    if target_level in ("staff", "member", "limited"):
        return level_value(actor_level) >= level_value("admin")
    if target_level == "admin":
        return is_owner(interaction)
    return False


def has_command_access(interaction, command_name):
    if is_owner(interaction):
        return True
    if interaction.guild is None:
        return False

    users = get_command_access(interaction.guild.id, command_name)
    if interaction.user.id in users:
        return True

    current_level = get_user_level(interaction)
    required_level = COMMAND_MIN_LEVELS.get(command_name)
    if required_level is None:
        return False
    return level_value(current_level) >= level_value(required_level)


async def require_command_access(interaction, command_name):
    if has_command_access(interaction, command_name):
        return True
    await interaction.response.send_message(
        get_setting(interaction.guild.id if interaction.guild else None, "text_access_denied"),
        ephemeral=True,
    )
    return False


# =========================
# RUNTIME SETTINGS (бэкенд под /design, наполнение UI — в самом конце проекта)
# =========================

# Фразы/цвета по умолчанию, пока UI настроек ещё не собран.
_DEFAULTS = {
    "embed_color": "0x5865F2",
    "danger_color": "0xED4245",
    "text_access_denied": "РЕПЛИКА ОС — ДОСТУП ЗАПРЕЩЁН",
    "nav_back": "◀️ Назад",
    "nav_next": "▶️ Далее",
    "nav_cancel": "❌ Отмена",
}


def get_setting(guild_id, key, default=None):
    """
    Синхронная обёртка над БД. Всегда используем эту функцию вместо
    хардкода констант — когда появится UI /design, менять нужно будет
    только сами значения в БД, а не код.
    """
    value = _db_get_setting(guild_id, key)
    if value is not None:
        return value
    return _DEFAULTS.get(key, default)


def set_setting(guild_id, key, value):
    _db_set_setting(guild_id, key, value)


def embed_color(guild_id=None):
    raw = get_setting(guild_id, "embed_color")
    try:
        return discord.Color(int(raw, 16) if isinstance(raw, str) else int(raw))
    except (TypeError, ValueError):
        return discord.Color.blurple()


def danger_color(guild_id=None):
    raw = get_setting(guild_id, "danger_color")
    try:
        return discord.Color(int(raw, 16) if isinstance(raw, str) else int(raw))
    except (TypeError, ValueError):
        return discord.Color.red()


# =========================
# PANEL VIEW — базовый класс с "Назад"
# =========================

class PanelView(discord.ui.View):
    """
    Базовый класс для всех панелей бота.

    back_target: кортеж (embed, view) — куда вернуться. Если передан,
    кнопка "Назад" добавляется автоматически, первой в ряду.

    Использование:
        view = SomePanel(..., back_target=(previous_embed, previous_view))

    Кнопки самой панели добавляй через @discord.ui.button как обычно —
    PanelView не мешает декораторам, "Назад" просто довешивается поверх.
    """

    def __init__(self, back_target=None, timeout=900):
        super().__init__(timeout=timeout)
        self.back_target = back_target
        if back_target is not None:
            self._insert_back_button()

    def _insert_back_button(self):
        button = discord.ui.Button(
            label="Назад",
            emoji="◀️",
            style=discord.ButtonStyle.secondary,
            row=4,
        )

        async def callback(interaction):
            embed, view = self.back_target
            await interaction.response.edit_message(embed=embed, view=view)

        button.callback = callback
        self.add_item(button)


# =========================
# EMBED PAGINATOR
# =========================

class EmbedPaginator(discord.ui.View):
    """
    Универсальный листалка: ◀️ N/M ▶️ под одним embed'ом.
    embeds — список discord.Embed. extra_view_factory (опционально) —
    функция(index) -> discord.ui.View с доп. кнопками под конкретную страницу
    (например "Использовать этот шаблон").
    """

    def __init__(self, embeds, back_target=None, extra_buttons_factory=None, timeout=900):
        super().__init__(timeout=timeout)
        self.embeds = embeds
        self.index = 0
        self.extra_buttons_factory = extra_buttons_factory

        self.prev_button = discord.ui.Button(emoji="◀️", style=discord.ButtonStyle.secondary, row=0)
        self.next_button = discord.ui.Button(emoji="▶️", style=discord.ButtonStyle.secondary, row=0)
        self.page_label = discord.ui.Button(
            label=self._page_text(), style=discord.ButtonStyle.grey, disabled=True, row=0
        )

        self.prev_button.callback = self._go_prev
        self.next_button.callback = self._go_next

        self.add_item(self.prev_button)
        self.add_item(self.page_label)
        self.add_item(self.next_button)

        if extra_buttons_factory:
            for item in extra_buttons_factory(self.index):
                self.add_item(item)

        if back_target is not None:
            embed_back, view_back = back_target
            back = discord.ui.Button(label="Назад", emoji="◀️", style=discord.ButtonStyle.secondary, row=4)

            async def back_callback(interaction):
                await interaction.response.edit_message(embed=embed_back, view=view_back)

            back.callback = back_callback
            self.add_item(back)

    def _page_text(self):
        return f"{self.index + 1}/{len(self.embeds)}"

    def _rebuild_extra(self):
        # убираем предыдущие extra-кнопки (всё, что не prev/page/next/назад)
        keep = {self.prev_button, self.page_label, self.next_button}
        for item in list(self.children):
            if item not in keep and getattr(item, "row", None) != 4:
                self.remove_item(item)
        if self.extra_buttons_factory:
            for item in self.extra_buttons_factory(self.index):
                self.add_item(item)

    async def _go_prev(self, interaction):
        self.index = (self.index - 1) % len(self.embeds)
        self.page_label.label = self._page_text()
        self._rebuild_extra()
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)

    async def _go_next(self, interaction):
        self.index = (self.index + 1) % len(self.embeds)
        self.page_label.label = self._page_text()
        self._rebuild_extra()
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)


# =========================
# ASYNC FILE FETCH (замена блокирующего urllib)
# =========================

async def fetch_bytes(url, timeout=8):
    """
    Асинхронная загрузка файла (аватарки вебхука, картинки для эмодзи и тд).
    ВАЖНО: раньше это делалось через urllib.request.urlopen синхронно —
    блокировало весь event loop бота и вызывало "не ответил вовремя"
    буквально на всех кнопках, пока грузилась картинка. Больше так не делаем.
    """
    if not url:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                if response.status != 200:
                    return None
                return await response.read()
    except (aiohttp.ClientError, TimeoutError):
        return None


# =========================
# ACTION REGISTRY
# =========================

# Дефолтный набор действий — заполняется при первом старте, если таблица пуста.
DEFAULT_ACTIONS = [
    # action_key, min_level, dangerous, description
    ("message.send", "member", False, "Отправить текстовое сообщение"),
    ("message.confirm", "member", False, "Показать подтверждение (ephemeral)"),
    ("form.trigger", "member", False, "Открыть форму по клику"),
    ("select.trigger", "member", False, "Открыть select-меню по клику"),
    ("role.assign", "staff", True, "Выдать роль пользователю"),
    ("role.remove", "staff", True, "Снять роль с пользователя"),
    ("webhook.send", "admin", True, "Отправить сообщение через webhook"),
    ("message.edit", "admin", True, "Редактировать ранее отправленное сообщение"),
    ("build.trigger", "admin", True, "Запустить связанный Message Build"),
]


def ensure_default_actions():
    for key, min_level, dangerous, description in DEFAULT_ACTIONS:
        if get_action(key) is None:
            upsert_action(key, min_level, dangerous, description)


def actions_for_level(level):
    """Список действий, доступных создателю кнопки с данным уровнем доступа."""
    all_actions = get_actions(enabled_only=True)
    return [
        row for row in all_actions
        if level_value(level) >= level_value(row[1])  # row: (key, min_level, dangerous, description)
    ]


def is_action_allowed(level, action_key):
    row = get_action(action_key)
    if row is None or not row[4]:  # not enabled
        return False
    return level_value(level) >= level_value(row[1])
