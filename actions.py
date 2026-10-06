"""
actions.py
==========
Единственный путь для всего интерактива в сообщениях бота:
- нормализация кнопок (старый формат /buttons и новый формат /embed);
- рендер embed'ов и компонентов сохранённых сообщений;
- постоянные (persistent) кнопки и списки: custom_id хранит, ОТКУДА взять
  кнопку (build / набор / шаблон + индекс), поэтому они работают после
  перезапуска бота и сразу подхватывают правки источника;
- dispatch_action — выполнение действия с двумя проверками прав:
  может ли НАЖАВШИЙ (use_level) и сохранил ли права СОЗДАТЕЛЬ (min_level);
- validate_action_value — проверка значения при создании кнопки.

Формат кнопки после normalize_button:
    {"label", "emoji", "style": blue/grey/green/red/link, "action_key", "value"}
"""

import json
import re
from datetime import datetime

import discord

import core
from core import t, Modal, PanelView, get_user_level, member_level, role_problem
from database import get_message_build, get_button_set, get_template, get_form, get_webhook

MAX_EMBEDS = 10
# 5 рядов по 5 кнопок; один ряд может занять список — остаётся 20 кнопок.
MAX_BUTTONS = 20

BUTTON_STYLES = {
    "blue": discord.ButtonStyle.primary,
    "grey": discord.ButtonStyle.secondary,
    "green": discord.ButtonStyle.success,
    "red": discord.ButtonStyle.danger,
    "link": discord.ButtonStyle.link,
}
_LEGACY_STYLES = {"primary": "blue", "secondary": "grey", "success": "green", "danger": "red", "link": "link"}
_LEGACY_ACTIONS = {"message": "message.send", "confirm": "message.confirm", "ephemeral": "message.send"}

NATIVE_SELECT_CLASSES = {
    "role": discord.ui.RoleSelect,
    "user": discord.ui.UserSelect,
    "channel": discord.ui.ChannelSelect,
    "mentionable": discord.ui.MentionableSelect,
}

_CUSTOM_EMOJI = re.compile(r"^<a?:\w{2,32}:\d{15,25}>$")
_WEBHOOK_URL = re.compile(r"^https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$")
_ROLE_MENTION = re.compile(r"^<@&(\d+)>$")


def say(interaction, key, **params):
    return interaction.response.send_message(t(key, **params), ephemeral=True)


async def reply(interaction, key, **params):
    """Ответ, даже если на interaction уже ответили (после defer/модалки)."""
    if interaction.response.is_done():
        await interaction.followup.send(t(key, **params), ephemeral=True)
    else:
        await say(interaction, key, **params)


def normalize_url(url):
    url = (url or "").strip()
    if not url:
        return None
    return url if url.startswith(("http://", "https://")) else "https://" + url


def valid_emoji(value):
    """Пустое, кастомное <:name:id> или короткая unicode-строка без латиницы/цифр."""
    value = (value or "").strip()
    if not value:
        return True
    if _CUSTOM_EMOJI.match(value):
        return True
    return len(value) <= 8 and not any(ch.isascii() and ch.isalnum() for ch in value)


def parse_id(value):
    value = (value or "").strip()
    match = _ROLE_MENTION.match(value)
    if match:
        value = match.group(1)
    return int(value) if value.isdigit() else None


# ============================================================
# НОРМАЛИЗАЦИЯ И ВИДИМОСТЬ
# ============================================================

def normalize_button(item):
    """Любой сохранённый формат кнопки -> единый словарь."""
    label = str(item.get("label") or t("common.default_button_label"))[:80]
    emoji = item.get("emoji") or None
    if "action_key" in item or ("action" not in item and item.get("style") in BUTTON_STYLES):
        style = item.get("style") if item.get("style") in BUTTON_STYLES else "blue"
        action_key = item.get("action_key")
    else:
        action = item.get("action", "message")
        if action == "link":
            style, action_key = "link", None
        else:
            style = _LEGACY_STYLES.get(item.get("style"), "blue")
            action_key = _LEGACY_ACTIONS.get(action, action)
    return {"label": label, "emoji": emoji, "style": style, "action_key": action_key, "value": item.get("value")}


def can_view(interaction, owner_id, visibility, role_ids=(), levels=()):
    """
    Общее правило видимости для build'ов, шаблонов и форм:
    создатель, владелец бота и админы видят всё; public — все;
    иначе — по разрешённым ролям или минимальному уровню.
    """
    return member_can_view(interaction.guild, interaction.user, owner_id, visibility, role_ids, levels)


def member_can_view(guild, user, owner_id, visibility, role_ids=(), levels=()):
    """То же правило для любого участника (user — Member или id): нужно, чтобы
    в момент клика перепроверить СОЗДАТЕЛЯ кнопки, а не только нажавшего."""
    user_id = user if isinstance(user, int) else user.id
    if user_id == owner_id or core.is_owner_id(user_id):
        return True
    level = core.level_value(member_level(guild, user))
    if level >= core.level_value("admin"):
        return True
    if visibility == "public":
        return True
    if isinstance(user, int):
        user = guild.get_member(user) if guild is not None else None
    member_roles = {role.id for role in getattr(user, "roles", [])}
    if role_ids and member_roles & set(role_ids):
        return True
    if levels and level >= min(core.level_value(lvl) for lvl in levels):
        return True
    return False


def _json(value, default):
    try:
        result = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default
    return default if result is None else result


def build_visible(interaction, row):
    return can_view(interaction, row[2], row[7], _json(row[9], []), _json(row[10], []))


def template_visible(interaction, row):
    return can_view(interaction, row[2], row[6], _json(row[8], []))


def form_visible(interaction, row):
    return can_view(interaction, row[2], row[6], _json(row[8], []))


# ============================================================
# ИСТОЧНИКИ КНОПОК
# ============================================================

def load_source(src, source_id):
    """
    src: b — Message Build, s — набор кнопок /buttons, t — шаблон.
    -> {"guild_id", "owner_id", "content", "embeds", "buttons", "interactive", "row"} или None
    """
    if src == "b":
        row = get_message_build(source_id)
        if not row:
            return None
        return {
            "guild_id": row[1], "owner_id": row[2], "content": row[4] or "",
            "embeds": _json(row[5], []), "buttons": _json(row[6], []),
            "interactive": _json(row[11], None), "row": row,
        }
    if src == "s":
        row = get_button_set(source_id)
        if not row:
            return None
        return {
            "guild_id": row[1], "owner_id": row[2], "content": "", "embeds": [],
            "buttons": _json(row[4], []), "interactive": None, "row": row,
        }
    if src == "t":
        row = get_template(source_id)
        if not row:
            return None
        payload = _json(row[5], {})
        if not isinstance(payload, dict):
            payload = {}
        return {
            "guild_id": row[1], "owner_id": row[2], "content": payload.get("content") or "",
            "embeds": payload.get("embeds") or [], "buttons": payload.get("buttons") or [],
            "interactive": payload.get("interactive"), "row": row,
        }
    return None


# ============================================================
# РЕНДЕР
# ============================================================

def _color(value):
    try:
        color = int(value)
    except (TypeError, ValueError):
        return core.embed_color().value
    return color if 0 <= color <= 0xFFFFFF else core.embed_color().value


def build_discord_embed(data):
    embed = discord.Embed(
        title=(data.get("title") or None) and str(data["title"])[:256],
        description=str(data.get("description") or "​")[:4096],
        url=normalize_url(data.get("url")),
        color=_color(data.get("color", core.embed_color().value)),
    )
    author_name = data.get("author_name")
    if author_name:
        embed.set_author(name=str(author_name)[:256], url=normalize_url(data.get("author_url")), icon_url=normalize_url(data.get("author_icon")))
    thumbnail = normalize_url(data.get("thumbnail"))
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    image = normalize_url(data.get("image"))
    if image:
        embed.set_image(url=image)
    footer_text = data.get("footer_text")
    if footer_text:
        embed.set_footer(text=str(footer_text)[:2048], icon_url=normalize_url(data.get("footer_icon")))
    if data.get("timestamp"):
        embed.timestamp = datetime.now()
    for field in (data.get("fields") or [])[:25]:
        embed.add_field(
            name=str(field.get("name") or "​")[:256],
            value=str(field.get("value") or "​")[:1024],
            inline=bool(field.get("inline", False)),
        )
    return embed


def _safe_emoji(value):
    if not value:
        return None
    try:
        return discord.PartialEmoji.from_str(value) if valid_emoji(value) else None
    except (TypeError, ValueError):
        return None


def build_components(src, source_id, buttons, interactive):
    """Реальные компоненты сообщения: список (если есть) в первом ряду + кнопки."""
    view = discord.ui.View(timeout=None)
    first_row = 0
    if interactive and isinstance(interactive, dict):
        itype = interactive.get("type")
        if itype == "list" and interactive.get("options"):
            view.add_item(ListSelect(src, source_id, interactive["options"], row=0))
            first_row = 1
        elif itype == "native_select" and interactive.get("kind") in NATIVE_SELECT_CLASSES:
            view.add_item(NativeSelect(interactive["kind"], row=0))
            first_row = 1
    slots = (5 - first_row) * 5
    for index, raw in enumerate((buttons or [])[:min(MAX_BUTTONS, slots)]):
        button = normalize_button(raw)
        row = first_row + index // 5
        if button["style"] == "link":
            url = normalize_url(button["value"])
            if not url:
                continue
            view.add_item(discord.ui.Button(
                label=button["label"], emoji=_safe_emoji(button["emoji"]),
                style=discord.ButtonStyle.link, url=url, row=row,
            ))
            continue
        view.add_item(ActionButton(
            src, source_id, index, label=button["label"],
            style=BUTTON_STYLES[button["style"]], emoji=_safe_emoji(button["emoji"]), row=row,
        ))
    return view if view.children else None


def render_source(src, source_id, data=None):
    """-> (content, [Embed], View|None) для предпросмотра и отправки."""
    data = data or load_source(src, source_id)
    if not data:
        return None, [], None
    embeds = [build_discord_embed(item) for item in data["embeds"][:MAX_EMBEDS] if isinstance(item, dict)]
    view = build_components(src, source_id, data["buttons"], data["interactive"])
    return (data["content"] or None), embeds, view


# ============================================================
# ПОСТОЯННЫЕ КОМПОНЕНТЫ
# ============================================================

async def _source_for(interaction, src, source_id):
    data = load_source(src, source_id)
    if not data or interaction.guild is None or data["guild_id"] != interaction.guild.id:
        await say(interaction, "actions.source_gone")
        return None
    return data


class ActionButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rb:a:(?P<src>[bst]):(?P<sid>\d+):(?P<idx>\d+)"):
    def __init__(self, src, source_id, index, *, label=None, style=discord.ButtonStyle.primary, emoji=None, row=None):
        super().__init__(
            discord.ui.Button(
                label=label, style=style, emoji=emoji,
                custom_id=f"rb:a:{src}:{source_id}:{index}",
            ),
            row=row,
        )
        self.src, self.source_id, self.index = src, source_id, index

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["src"], int(match["sid"]), int(match["idx"]), label=item.label, style=item.style, emoji=item.emoji)

    async def callback(self, interaction):
        data = await _source_for(interaction, self.src, self.source_id)
        if data is None:
            return
        if self.index >= len(data["buttons"]):
            await say(interaction, "actions.source_gone")
            return
        button = normalize_button(data["buttons"][self.index])
        await dispatch_action(interaction, button["action_key"], button["value"], creator_id=data["owner_id"])


class ListSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"rb:l:(?P<src>[bst]):(?P<sid>\d+)"):
    def __init__(self, src, source_id, options=None, *, row=None, select=None):
        if select is None:
            default_label = t("embed.list.default_option")
            select = discord.ui.Select(
                custom_id=f"rb:l:{src}:{source_id}",
                placeholder=t("embed.list.placeholder")[:150],
                options=[
                    discord.SelectOption(
                        label=str(option.get("label") or default_label)[:100],
                        description=(str(option.get("description"))[:100] if option.get("description") else None),
                        emoji=_safe_emoji(option.get("emoji")),
                        value=str(index),
                    )
                    for index, option in enumerate((options or [])[:25])
                ],
            )
        super().__init__(select, row=row)
        self.src, self.source_id = src, source_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["src"], int(match["sid"]), select=item)

    async def callback(self, interaction):
        data = await _source_for(interaction, self.src, self.source_id)
        if data is None:
            return
        options = (data["interactive"] or {}).get("options") or []
        try:
            option = options[int(self.item.values[0])]
        except (ValueError, IndexError):
            await say(interaction, "embed.list.option_missing")
            return
        await dispatch_action(interaction, option.get("action_key"), option.get("value"), creator_id=data["owner_id"])


class NativeSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"rb:n:(?P<kind>role|user|channel|mentionable)"):
    def __init__(self, kind, *, row=None, select=None):
        if select is None:
            select = NATIVE_SELECT_CLASSES[kind](custom_id=f"rb:n:{kind}", placeholder=t("embed.native_select.placeholder")[:150])
        super().__init__(select, row=row)
        self.kind = kind

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["kind"], select=item)

    async def callback(self, interaction):
        chosen = self.item.values[0] if self.item.values else None
        label = getattr(chosen, "mention", str(chosen))
        await say(interaction, "select.picked", value=label)


DYNAMIC_ITEMS = [ActionButton, ListSelect, NativeSelect]


def build_native_select_view(kind):
    view = discord.ui.View(timeout=None)
    view.add_item(NativeSelect(kind))
    return view


# ============================================================
# МОДАЛКИ ДЕЙСТВИЙ
# ============================================================

class MessageEditModal(Modal, title="РЕДАКТИРОВАТЬ СООБЩЕНИЕ"):
    texts = "actions.edit_modal"

    content_input = discord.ui.TextInput(
        label="Новый текст сообщения",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=2000,
    )

    def __init__(self, target_message):
        super().__init__()
        self.target_message = target_message
        self.content_input.default = (target_message.content or "")[:2000]

    async def on_submit(self, interaction):
        content = self.content_input.value.strip() or None
        if content is None and not self.target_message.embeds:
            await say(interaction, "actions.edit_modal.empty")
            return
        try:
            await self.target_message.edit(content=content)
        except discord.NotFound:
            await say(interaction, "actions.edit_modal.gone")
            return
        except discord.Forbidden:
            await say(interaction, "actions.edit_modal.forbidden")
            return
        core.audit(interaction, "message.edited", "message", self.target_message.id)
        await say(interaction, "actions.edit_modal.done")


class WebhookSendModal(Modal, title="ОТПРАВИТЬ ЧЕРЕЗ WEBHOOK"):
    """webhook_id — запись из /webhooks (URL не показывается); иначе URL задаётся в поле."""

    texts = "actions.webhook_modal"

    url_input = discord.ui.TextInput(label="Webhook URL", max_length=1000, required=False)
    content_input = discord.ui.TextInput(
        label="Текст сообщения",
        style=discord.TextStyle.paragraph,
        max_length=2000,
    )

    def __init__(self, default_url="", record_id=None):
        super().__init__()
        self.record_id = record_id
        if record_id is not None:
            # URL сохранённого вебхука — секрет, не показываем его нажавшему
            self.remove_item(self.url_input)
        else:
            self.url_input.default = default_url

    async def on_submit(self, interaction):
        if self.record_id is not None:
            row = get_webhook(self.record_id)
            if not row or row[1] != interaction.guild.id:
                await say(interaction, "actions.webhook_record_missing")
                return
            webhook_url = row[6]
        else:
            webhook_url = normalize_url(self.url_input.value)
            if not webhook_url or not _WEBHOOK_URL.match(webhook_url):
                await say(interaction, "actions.webhook_modal.no_url")
                return
        try:
            webhook = discord.Webhook.from_url(webhook_url, client=interaction.client)
            await webhook.send(self.content_input.value, username=interaction.client.user.name,
                               allowed_mentions=discord.AllowedMentions(everyone=False, roles=False))
        except (discord.HTTPException, ValueError):
            await say(interaction, "actions.webhook_modal.failed")
            return
        core.audit(interaction, "webhook.sent", "webhook", self.record_id)
        await say(interaction, "actions.webhook_modal.sent")


class DangerousActionView(PanelView):
    texts = "actions.dangerous"

    def __init__(self, action_key, value, creator_id=None, target_message=None):
        super().__init__(timeout=300)
        self.action_key = action_key
        self.value = value
        self.creator_id = creator_id
        self.target_message = target_message

    @discord.ui.button(label="Подтвердить", emoji="⚠️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await dispatch_action(
            interaction, self.action_key, self.value,
            creator_id=self.creator_id, skip_confirmation=True, target_message=self.target_message,
        )

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content=t("actions.cancelled"), view=None)


# ============================================================
# ПРОВЕРКА ЗНАЧЕНИЯ ПРИ СОЗДАНИИ
# ============================================================

def validate_action_value(interaction, action_key, value, style=None):
    """
    -> (нормализованное значение, None) или (None, ключ ошибки).
    Вызывается, когда создатель заполняет кнопку/опцию: битые ID, чужие
    формы и опасные роли отсекаются сразу, а не при клике у участников.
    """
    value = (value or "").strip()
    guild = interaction.guild
    if style == "link":
        url = normalize_url(value)
        return (url, None) if url and " " not in url else (None, "actions.bad_value.link")
    if action_key in ("message.send", "message.confirm"):
        return value[:2000], None
    if action_key == "message.edit":
        return "", None
    if action_key == "select.trigger":
        kind = (value or "role").lower()
        return (kind, None) if kind in NATIVE_SELECT_CLASSES else (None, "actions.select_trigger.bad_value")
    if action_key == "form.trigger":
        form_id = parse_id(value)
        row = get_form(form_id) if form_id else None
        if not row or row[1] != guild.id:
            return None, "actions.form_trigger.bad_value"
        return str(form_id), None
    if action_key in ("role.assign", "role.remove", "role.toggle"):
        role_id = parse_id(value)
        role = guild.get_role(role_id) if role_id else None
        if role is None:
            return None, "actions.role.bad_value"
        problem = role_problem(guild, role, interaction.user)
        return (None, problem) if problem else (str(role_id), None)
    if action_key == "webhook.send":
        record_id = parse_id(value)
        if record_id is not None:
            row = get_webhook(record_id)
            return (str(record_id), None) if row and row[1] == guild.id else (None, "actions.webhook_record_missing")
        url = normalize_url(value)
        if url and _WEBHOOK_URL.match(url):
            return url, None
        return None, "actions.webhook_modal.no_url"
    if action_key == "build.trigger":
        build_id = parse_id(value)
        row = get_message_build(build_id) if build_id else None
        if not row or row[1] != guild.id or not build_visible(interaction, row):
            return None, "actions.build_trigger.bad_value"
        return str(build_id), None
    return value, None


# ============================================================
# ВЫПОЛНЕНИЕ ДЕЙСТВИЯ
# ============================================================

async def dispatch_action(interaction, action_key, value, *, creator_id=None, skip_confirmation=False, target_message=None):
    """Единая маршрутизация действий кнопок и пользовательских списков."""
    guild = interaction.guild
    if guild is None:
        await reply(interaction, "common.only_in_guild")
        return
    if not action_key:
        await reply(interaction, "actions.not_configured")
        return
    if not core.is_action_usable(get_user_level(interaction), action_key):
        await reply(interaction, "actions.no_access")
        return
    # Создатель кнопки мог потерять права после того, как её собрал.
    if creator_id is not None and not core.is_action_allowed(member_level(guild, creator_id), action_key):
        await reply(interaction, "actions.creator_revoked")
        return

    action_row = core.get_action(action_key)
    if action_row and action_row[2] and not skip_confirmation:
        await interaction.response.send_message(
            t("actions.dangerous.text", action=action_key),
            view=DangerousActionView(action_key, value, creator_id=creator_id, target_message=interaction.message),
            ephemeral=True,
        )
        return

    handler = _HANDLERS.get(action_key)
    if handler is None:
        await reply(interaction, "actions.wip", action=action_key)
        return
    await handler(interaction, value, creator_id=creator_id, target_message=target_message)


async def _message_send(interaction, value, **_):
    await interaction.response.send_message(value or t("actions.default.message"), ephemeral=True)


async def _message_confirm(interaction, value, **_):
    await interaction.response.send_message(value or t("actions.default.confirm"), ephemeral=True)


async def _message_edit(interaction, value, target_message=None, **_):
    target_message = target_message or interaction.message
    if target_message is None:
        await reply(interaction, "actions.edit_no_message")
        return
    if target_message.author.id != interaction.client.user.id:
        await reply(interaction, "actions.edit_not_bot")
        return
    await interaction.response.send_modal(MessageEditModal(target_message))


async def _form_trigger(interaction, value, **_):
    from extended_modules import open_form

    form_id = parse_id(value)
    if form_id is None:
        await reply(interaction, "actions.form_trigger.bad_value")
        return
    await open_form(interaction, form_id)


async def _select_trigger(interaction, value, **_):
    kind = (value or "role").strip().lower()
    if kind not in NATIVE_SELECT_CLASSES:
        await reply(interaction, "actions.select_trigger.bad_value")
        return
    await interaction.response.send_message(
        t("actions.select_trigger.prompt"), view=build_native_select_view(kind), ephemeral=True,
    )


async def _role_action(interaction, value, action_key, creator_id=None):
    guild = interaction.guild
    role_id = parse_id(value)
    role = guild.get_role(role_id) if role_id else None
    if role is None:
        await reply(interaction, "actions.role.bad_value")
        return
    if not guild.me.guild_permissions.manage_roles:
        await reply(interaction, "actions.role.no_manage_roles")
        return
    # Права роли могли измениться после создания кнопки — проверяем на момент клика
    # от имени СОЗДАТЕЛЯ: опасные роли может раздавать только владелец.
    creator = guild.get_member(creator_id) if creator_id else None
    problem = role_problem(guild, role, creator, allow_dangerous=bool(creator_id and core.is_owner_id(creator_id)))
    if problem:
        await reply(interaction, problem)
        return
    member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
    if member is None:
        await reply(interaction, "actions.role.unreachable")
        return
    has_role = role in member.roles
    add = action_key == "role.assign" or (action_key == "role.toggle" and not has_role)
    try:
        if add:
            await member.add_roles(role, reason=f"Button {action_key} (creator {creator_id})")
        else:
            await member.remove_roles(role, reason=f"Button {action_key} (creator {creator_id})")
    except discord.Forbidden:
        await reply(interaction, "actions.role.forbidden")
        return
    core.audit(interaction, "role.added" if add else "role.removed", "role", role.id, f"via {action_key}")
    await reply(interaction, "actions.role.assigned" if add else "actions.role.removed", role=role.mention)


async def _role_assign(interaction, value, creator_id=None, **_):
    await _role_action(interaction, value, "role.assign", creator_id)


async def _role_remove(interaction, value, creator_id=None, **_):
    await _role_action(interaction, value, "role.remove", creator_id)


async def _role_toggle(interaction, value, creator_id=None, **_):
    await _role_action(interaction, value, "role.toggle", creator_id)


async def _webhook_send(interaction, value, **_):
    record_id = parse_id(value)
    if record_id is not None:
        await interaction.response.send_modal(WebhookSendModal(record_id=record_id))
    else:
        await interaction.response.send_modal(WebhookSendModal(value or ""))


def build_attach_allowed(interaction, row, creator_id):
    """
    Может ли нажавший открыть build, прикреплённый к кнопке.
    Прикрепляя build, создатель сам его публикует: private означает лишь
    «не виден в чужих списках», поэтому нажавшему его показываем. Но только
    пока создатель сам видит этот build (не потерял доступ), а у restricted
    ограничение по ролям/уровням действует и для нажавшего.
    """
    if creator_id is None:
        return build_visible(interaction, row)
    creator_ok = member_can_view(interaction.guild, creator_id, row[2], row[7], _json(row[9], []), _json(row[10], []))
    if not creator_ok:
        return False
    return row[7] != "restricted" or build_visible(interaction, row)


async def _build_trigger(interaction, value, creator_id=None, **_):
    from embed_module import message_parts

    build_id = parse_id(value)
    row = get_message_build(build_id) if build_id else None
    if not row or row[1] != interaction.guild.id:
        await reply(interaction, "embed.build_not_found")
        return
    if not build_attach_allowed(interaction, row, creator_id):
        await reply(interaction, "actions.build_no_access")
        return
    # Как при обычной отправке: каждый embed отдельным сообщением, иначе
    # большой build упрётся в лимит Discord 6000 символов на сообщение.
    parts = message_parts(build_id)
    if not parts:
        await reply(interaction, "embed.build_empty")
        return
    for index, part in enumerate(parts):
        if index == 0 and not interaction.response.is_done():
            await interaction.response.send_message(ephemeral=True, **part)
        else:
            await interaction.followup.send(ephemeral=True, **part)


_HANDLERS = {
    "message.send": _message_send,
    "message.confirm": _message_confirm,
    "message.edit": _message_edit,
    "form.trigger": _form_trigger,
    "select.trigger": _select_trigger,
    "role.assign": _role_assign,
    "role.remove": _role_remove,
    "role.toggle": _role_toggle,
    "webhook.send": _webhook_send,
    "build.trigger": _build_trigger,
}
