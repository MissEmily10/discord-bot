"""
core.py
========
Общий фундамент РЕПЛИКА ОС.

Здесь живёт всё, что раньше было размазано по bot.py и extended_modules.py
и дублировалось:
- уровни доступа и их подписи
- PanelView — базовый класс с автоматической кнопкой "Назад"
- t()/panel_embed() — тексты, цвета и thumbnails из каталога texts.py
  (значения владельца хранятся в bot_settings и перекрывают дефолты)
- action_registry — реестр действий для кнопок/select с проверкой по уровню
- fetch_bytes — асинхронная загрузка файлов (замена блокирующего urllib)
"""

import logging
import os
import time

import aiohttp
import discord

from database import (
    log_audit as _db_log_audit,
    GLOBAL_SETTINGS_ID,
    get_all_settings as _db_get_all_settings,
    set_setting as _db_set_setting,
    delete_setting as _db_delete_setting,
    get_action,
    get_actions,
    upsert_action,
    get_access_level,
    get_command_access,
    get_all_role_access,
    is_user_denied,
)
from texts import CATALOG

_log = logging.getLogger(__name__)

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

def level_label(level):
    """Подпись уровня доступа из каталога (level.<уровень>)."""
    return t(f"level.{level}") if has_text(f"level.{level}") else str(level)


def level_value(level):
    return ACCESS_LEVELS.get(level, 0)


# =========================
# PERMISSION CHECKS
# ЕДИНСТВЕННОЕ место, где это должно проверяться. Раньше admin() в
# extended_modules.py дублировал эту же логику отдельно — если бы кто-то
# поменял правило в одном месте и забыл про второе, права бы разъехались.
# =========================

def is_owner_id(user_id):
    return bool(OWNER_ID) and user_id == OWNER_ID


def is_owner(interaction):
    return is_owner_id(interaction.user.id)


def member_level(guild, user):
    """
    Уровень любого участника (не только нажавшего): нужен, чтобы перепроверять
    создателя кнопки в момент клика и сравнивать уровни при управлении доступом.
    user — Member/User или просто id.
    """
    user_id = user if isinstance(user, int) else user.id
    if is_owner_id(user_id):
        return "owner"
    if guild is None:
        return "member"
    if is_user_denied(guild.id, user_id) is not None:
        return "limited"

    levels = [get_access_level(guild.id, user_id)]
    member = user if isinstance(user, discord.Member) else guild.get_member(user_id)
    if member is not None:
        role_levels = {
            role_id: (access_level, expires_at)
            for role_id, access_level, expires_at in get_all_role_access(guild.id)
        }
        now = int(time.time())
        for role in member.roles:
            role_entry = role_levels.get(role.id)
            if role_entry is None:
                continue
            access_level, expires_at = role_entry
            if expires_at is None or expires_at > now:
                levels.append(access_level)

    return max(levels, key=level_value)


def get_user_level(interaction):
    return member_level(interaction.guild, interaction.user)


def can_manage_access(interaction):
    return level_value(get_user_level(interaction)) >= level_value("admin")


def can_view_access(interaction):
    return level_value(get_user_level(interaction)) >= level_value("staff")


def can_assign_level(interaction, target_level):
    actor_level = get_user_level(interaction)
    if target_level in ("owner", "admin"):
        return is_owner(interaction)
    if target_level in ("staff", "member", "limited"):
        return level_value(actor_level) >= level_value("admin")
    return False


def can_manage_user(interaction, target_id):
    """
    Менять уровень, срок или запрет можно только тем, кто СТРОГО ниже тебя.
    Себя и владельца не трогает никто, кроме владельца (а себя — никто).
    """
    if target_id == interaction.user.id:
        return False
    if is_owner_id(target_id):
        return False
    if is_owner(interaction):
        return True
    actor = level_value(get_user_level(interaction))
    target = level_value(member_level(interaction.guild, target_id))
    return actor >= level_value("admin") and actor > target


def has_command_access(interaction, command_name):
    if is_owner(interaction):
        return True
    if interaction.guild is None:
        return False
    # запрет сильнее индивидуального доступа к команде
    if is_user_denied(interaction.guild.id, interaction.user.id) is not None:
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
    if interaction.guild is None:
        await interaction.response.send_message(t("common.only_in_guild"), ephemeral=True)
        return False
    if has_command_access(interaction, command_name):
        return True
    await interaction.response.send_message(t("text_access_denied"), ephemeral=True)
    return False


# =========================
# РОЛИ И КАНАЛЫ: защита от эскалации прав
# =========================

# Роли с такими правами нельзя выдавать через кнопки, меню ролей, формы и
# /logo — иначе staff может собрать кнопку "выдай мне админку". Только владелец.
DANGEROUS_PERMISSIONS = (
    "administrator", "manage_guild", "manage_roles", "manage_channels", "manage_webhooks",
    "ban_members", "kick_members", "moderate_members", "manage_messages", "manage_nicknames",
    "manage_expressions", "manage_threads", "manage_events", "mention_everyone", "view_audit_log",
)


def role_dangerous_permissions(role):
    return [name for name in DANGEROUS_PERMISSIONS if getattr(role.permissions, name, False)]


def role_problem(guild, role, actor=None, *, allow_dangerous=None):
    """
    Почему эту роль нельзя выдать/менять через бота — ключ каталога, или None.
    actor — Member, от чьего имени это делается (создатель кнопки, ревьюер...);
    allow_dangerous=None — разрешено только владельцу.
    """
    if role is None or guild is None:
        return "roles.not_found"
    if role.is_default():
        return "roles.everyone"
    if role.managed:
        return "roles.managed"
    me = guild.me
    if me is None or role >= me.top_role:
        return "roles.above_bot"
    actor_is_owner = actor is not None and is_owner_id(actor.id)
    if allow_dangerous is None:
        allow_dangerous = actor_is_owner
    if role_dangerous_permissions(role) and not allow_dangerous:
        return "roles.dangerous"
    if actor is not None and not actor_is_owner and hasattr(actor, "top_role"):
        if actor.id != guild.owner_id and role >= actor.top_role:
            return "roles.above_actor"
    return None


def can_post_in(member, channel):
    """Может ли участник сам писать в канал — бот не должен постить туда, куда человеку нельзя."""
    if member is None or channel is None:
        return False
    if is_owner_id(member.id):
        return True
    perms = channel.permissions_for(member)
    return perms.view_channel and perms.send_messages


def bot_can_post(channel):
    me = channel.guild.me if channel is not None and channel.guild else None
    if me is None:
        return False
    perms = channel.permissions_for(me)
    return perms.view_channel and perms.send_messages and perms.embed_links


# =========================
# AUDIT
# =========================

def audit(interaction, action, target_type=None, target_id=None, details=None):
    """Журнал действий (/access → Журнал). Никогда не роняет основной сценарий."""
    if interaction is None or interaction.guild is None:
        return
    try:
        _db_log_audit(interaction.guild.id, interaction.user.id, action, target_type, target_id, details)
    except Exception:  # noqa: BLE001 — журнал вторичен
        _log.exception("audit failed: %s", action)


# =========================
# ОШИБКИ
# =========================

async def report_error(interaction, error):
    """Логирует исключение и показывает пользователю понятное сообщение вместо 'Interaction failed'."""
    _log.error("Ошибка в interaction", exc_info=error)
    text = t("common.error")
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        pass


# =========================
# ТЕКСТЫ, ЦВЕТА, THUMBNAILS (каталог — texts.py)
# =========================
# Настройки общие для всего бота (guild_id = 0 в bot_settings): один владелец,
# один бренд, одна веб-панель. Значение из БД перекрывает дефолт из каталога.

_settings_cache = None


def _settings():
    global _settings_cache
    if _settings_cache is None:
        _settings_cache = _db_get_all_settings(GLOBAL_SETTINGS_ID)
    return _settings_cache


def overrides():
    """Значения, заданные владельцем поверх каталога (копия)."""
    return dict(_settings())


def reload_settings():
    global _settings_cache
    _settings_cache = None


def get_setting(key, default=None):
    value = _settings().get(key)
    if value is not None:
        return value
    return CATALOG.get(key, default)


def set_setting(key, value):
    _db_set_setting(GLOBAL_SETTINGS_ID, key, value)
    _settings()[key] = str(value)


def reset_setting(key):
    _db_delete_setting(GLOBAL_SETTINGS_ID, key)
    _settings().pop(key, None)


class _KeepMissing(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def t(key, **params):
    """
    Текст из каталога. Подстановки — {name}. Если владелец сломал фигурные
    скобки в своей фразе, показываем фразу как есть, а не роняем бота.
    """
    template = get_setting(key)
    if template is None:
        _log.warning("Нет ключа в texts.CATALOG: %s", key)
        return key
    if not params:
        return template
    try:
        return template.format_map(_KeepMissing(params))
    except (ValueError, IndexError, AttributeError):
        return template


def has_text(key):
    return key in CATALOG or key in _settings()


def _parse_color(raw, fallback):
    try:
        return discord.Color(int(raw, 16) if isinstance(raw, str) else int(raw))
    except (TypeError, ValueError):
        return fallback


def embed_color(guild_id=None):
    return _parse_color(get_setting("embed_color"), discord.Color.blurple())


def danger_color(guild_id=None):
    return _parse_color(get_setting("danger_color"), discord.Color.red())


def resolve_thumbnail(interaction, context):
    """
    Thumbnail контекста: <context>.thumbnail -> thumbnail.default.
    "none" — явно без картинки; {bot_avatar} — аватар бота.
    """
    value = (get_setting(f"{context}.thumbnail") or "").strip() if context else ""
    if not value:
        value = (get_setting("thumbnail.default") or "").strip()
    if not value or value.lower() == "none":
        return None
    if value == "{bot_avatar}":
        user = interaction.client.user if interaction is not None else None
        return user.display_avatar.url if user else None
    return value if value.startswith(("http://", "https://")) else "https://" + value


def panel_embed(interaction, context, description=None, *, title=None, danger=False, **params):
    """
    Единый конструктор служебных embed'ов бота.
    context — ключ экрана, например "forms.home": берутся
    "forms.home.title", "forms.home.text" (если есть) и thumbnail контекста.
    """
    if title is None:
        title = t(f"{context}.title", **params)
    if description is None:
        description = t(f"{context}.text", **params) if has_text(f"{context}.text") else ""
    embed = discord.Embed(
        title=str(title)[:256] or None,
        description=str(description)[:4096] or None,
        color=danger_color() if danger else embed_color(),
    )
    thumbnail = resolve_thumbnail(interaction, context)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    return embed


def apply_texts(component, prefix):
    """
    Подписи декорированных кнопок/полей из каталога: ключ <prefix>.<имя метода
    или атрибута>. Если ключа нет — остаётся подпись из кода. Кнопка-метод
    cancel без своего ключа получает общую подпись nav_cancel.
    """
    if isinstance(component, discord.ui.Modal):
        if not prefix:
            return
        if has_text(f"{prefix}.title"):
            component.title = t(f"{prefix}.title")[:45]
        for name in component.__modal_children_items__:
            item = getattr(component, name, None)
            if not isinstance(item, discord.ui.TextInput):
                continue
            if has_text(f"{prefix}.{name}"):
                item.label = t(f"{prefix}.{name}")[:45]
            if has_text(f"{prefix}.{name}.placeholder"):
                item.placeholder = t(f"{prefix}.{name}.placeholder")[:100]
        return
    for name, raw in component.__view_children_items__.items():
        attr = getattr(raw, "__name__", name)
        item = getattr(component, attr, None)
        if not isinstance(item, discord.ui.Button):
            continue
        if prefix and has_text(f"{prefix}.{attr}"):
            item.label = t(f"{prefix}.{attr}")[:80]
        elif attr == "cancel":
            item.label = t("nav_cancel")[:80]


class Modal(discord.ui.Modal):
    """Базовая модалка: texts = "forms.basic_modal" — заголовок и поля из каталога."""

    texts = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        apply_texts(self, self.texts)

    async def on_error(self, interaction, error):
        await report_error(interaction, error)


# =========================
# PANEL VIEW — базовый класс с "Назад"
# =========================

class PanelView(discord.ui.View):
    """
    Базовый класс для всех панелей бота.

    back_target: кортеж (embed, view) — куда вернуться. Если передан,
    кнопка "Назад" добавляется автоматически, первой в ряду.

    texts: префикс каталога для подписей кнопок, например "access.home" —
    кнопка-метод users_button получит подпись "access.home.users_button".

    Использование:
        view = SomePanel(..., back_target=(previous_embed, previous_view))
    """

    texts = None

    def __init__(self, back_target=None, timeout=900):
        super().__init__(timeout=timeout)
        self.back_target = back_target
        apply_texts(self, self.texts)
        if back_target is not None:
            self._insert_back_button()

    def _insert_back_button(self):
        button = discord.ui.Button(
            label=t("nav_back")[:80],
            style=discord.ButtonStyle.secondary,
            row=4,
        )

        async def callback(interaction):
            embed, view = self.back_target
            await interaction.response.edit_message(embed=embed, view=view)

        button.callback = callback
        self.add_item(button)

    async def on_error(self, interaction, error, item):
        await report_error(interaction, error)


# =========================
# ASYNC FILE FETCH (замена блокирующего urllib)
# =========================

MAX_FETCH_BYTES = 8 * 1024 * 1024


async def fetch_bytes(url, timeout=8, max_bytes=MAX_FETCH_BYTES):
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
                if not (response.content_type or "").startswith("image/"):
                    return None
                if response.content_length and response.content_length > max_bytes:
                    return None
                data = await response.content.read(max_bytes + 1)
                return None if len(data) > max_bytes else data
    except (aiohttp.ClientError, TimeoutError):
        return None


# =========================
# ACTION REGISTRY
# =========================

# Дефолтный набор действий — заполняется при первом старте, если ключа нет.
# min_level — кто может СОЗДАТЬ кнопку с действием; use_level — кто может НАЖАТЬ.
# Например, кнопку "выдать роль" собирает staff, а нажимает любой участник.
DEFAULT_ACTIONS = [
    # action_key, min_level, dangerous, description, use_level
    ("message.send", "member", False, "Отправить текстовое сообщение", "member"),
    ("message.confirm", "member", False, "Показать подтверждение (ephemeral)", "member"),
    ("form.trigger", "member", False, "Открыть форму по клику", "member"),
    ("select.trigger", "member", False, "Открыть select-меню по клику", "member"),
    ("role.assign", "staff", False, "Выдать роль нажавшему", "member"),
    ("role.remove", "staff", False, "Снять роль с нажавшего", "member"),
    ("role.toggle", "staff", False, "Выдать или снять роль у нажавшего", "member"),
    ("webhook.send", "admin", True, "Отправить сообщение через webhook", "admin"),
    ("message.edit", "admin", True, "Редактировать сообщение с этой кнопкой", "admin"),
    ("build.trigger", "staff", False, "Показать связанный Message Build", "member"),
    ("build.goto", "staff", False, "Шаг: заменить сообщение другим Message Build", "member"),
    ("build.refresh", "staff", False, "Обновить отправленные сообщения другого Message Build", "member"),
    ("counter.change", "staff", False, "Изменить счётчик {counter:имя} и обновить связанные сообщения", "member"),
]


def ensure_default_actions():
    for key, min_level, dangerous, description, use_level in DEFAULT_ACTIONS:
        if get_action(key) is None:
            upsert_action(key, min_level, dangerous, description, use_level=use_level)


def actions_for_level(level):
    """Действия, которые создатель с этим уровнем может повесить на кнопку."""
    return [
        row for row in get_actions(enabled_only=True)
        if level_value(level) >= level_value(row[1])  # row: (key, min_level, dangerous, description, enabled, use_level)
    ]


def is_action_allowed(level, action_key):
    """Может ли создатель с этим уровнем повесить действие на кнопку."""
    row = get_action(action_key)
    if row is None or not row[4]:  # not enabled
        return False
    return level_value(level) >= level_value(row[1])


def is_action_usable(level, action_key):
    """Может ли нажавший с этим уровнем выполнить действие."""
    row = get_action(action_key)
    if row is None or not row[4]:
        return False
    return level_value(level) >= level_value(row[5] or "member")
