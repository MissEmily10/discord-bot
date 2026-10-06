"""
build_tools.py
==============
Инструменты Message Build:
- сборка build'а из уже существующего сообщения (ссылка или ID);
- экспорт/импорт build'а в JSON-файл (перенос между серверами и ботами);
- история версий: снимок перед каждым сохранением и откат.

Всё, что приходит снаружи (чужое сообщение, JSON с другого сервера),
проходит через sanitize_payload: кнопки с ролями/формами/build'ами,
которых здесь нет или которые создателю недоступны, остаются без действия,
а не исполняют что попало.
"""

import io
import json
import re
from datetime import datetime, timezone

import discord

import core
from core import PanelView, Modal, t, panel_embed
from actions import (
    MAX_EMBEDS, MAX_BUTTONS, NATIVE_SELECT_CLASSES,
    say, reply, normalize_button, validate_action_value, load_source, build_visible,
    render_source, check_url,
)
from database import (
    get_message_build, update_message_build, save_build_version,
    get_build_versions, get_build_version, build_snapshot,
)

EXPORT_FORMAT = "discord-bot/message-build"
EXPORT_VERSION = 1
MAX_IMPORT_BYTES = 512 * 1024

_MESSAGE_LINK = re.compile(r"discord(?:app)?\.com/channels/(\d+|@me)/(\d+)/(\d+)")
_CHANNEL_MESSAGE = re.compile(r"^(\d{15,25})[-/ ](\d{15,25})$")
_OUR_BUTTON = re.compile(r"^rb:a:([bst]):(\d+):(\d+)$")
_OUR_LIST = re.compile(r"^rb:l:([bst]):(\d+)$")
_OUR_NATIVE = re.compile(r"^rb:n:(role|user|channel|mentionable)$")

_STYLE_NAMES = {
    discord.ButtonStyle.primary: "blue",
    discord.ButtonStyle.secondary: "grey",
    discord.ButtonStyle.success: "green",
    discord.ButtonStyle.danger: "red",
    discord.ButtonStyle.link: "link",
}
_NATIVE_TYPES = {
    discord.ComponentType.role_select: "role",
    discord.ComponentType.user_select: "user",
    discord.ComponentType.channel_select: "channel",
    discord.ComponentType.mentionable_select: "mentionable",
}
_EMBED_KEYS = (
    "title", "description", "url", "color", "author_name", "author_url", "author_icon",
    "thumbnail", "image", "footer_text", "footer_icon", "timestamp", "fields",
)
_URL_KEYS = ("url", "author_url", "author_icon", "thumbnail", "image", "footer_icon")


# ============================================================
# СООБЩЕНИЕ -> PAYLOAD
# ============================================================

def parse_message_ref(text, default_channel_id=None):
    """Ссылка / «канал-сообщение» / просто ID -> (guild_id|None, channel_id, message_id) или None."""
    text = (text or "").strip()
    match = _MESSAGE_LINK.search(text)
    if match:
        guild = None if match[1] == "@me" else int(match[1])
        return guild, int(match[2]), int(match[3])
    match = _CHANNEL_MESSAGE.match(text)
    if match:
        return None, int(match[1]), int(match[2])
    if text.isdigit() and default_channel_id:
        return None, default_channel_id, int(text)
    return None


def embed_to_data(embed):
    """discord.Embed -> наш формат embed'а."""
    data = {
        "title": embed.title or "",
        "description": embed.description or "",
        "url": embed.url,
        "color": embed.color.value if embed.color else core.embed_color().value,
        "fields": [
            {"name": field.name or "​", "value": field.value or "​", "inline": bool(field.inline)}
            for field in embed.fields
        ],
    }
    if embed.author and embed.author.name:
        data.update(author_name=embed.author.name, author_url=embed.author.url, author_icon=embed.author.icon_url)
    if embed.footer and embed.footer.text:
        data.update(footer_text=embed.footer.text, footer_icon=embed.footer.icon_url)
    if embed.thumbnail and embed.thumbnail.url:
        data["thumbnail"] = embed.thumbnail.url
    if embed.image and embed.image.url:
        data["image"] = embed.image.url
    if embed.timestamp:
        data["timestamp"] = True
    return data


def _emoji_text(emoji):
    return str(emoji) if emoji else None


def _copied_button(custom_id):
    """Кнопка нашего бота — берём её настоящее определение из источника."""
    match = _OUR_BUTTON.match(custom_id or "")
    if not match:
        return None
    data = load_source(match[1], int(match[2]))
    index = int(match[3])
    if not data or index >= len(data["buttons"]):
        return None
    return dict(normalize_button(data["buttons"][index]))


def _copied_list(custom_id):
    match = _OUR_LIST.match(custom_id or "")
    if not match:
        return None
    data = load_source(match[1], int(match[2]))
    interactive = (data or {}).get("interactive") or {}
    return interactive if interactive.get("type") == "list" else None


def message_to_payload(message):
    """
    discord.Message -> payload в формате шаблонов/экспорта.
    Кнопки и списки нашего бота копируются вместе с действиями;
    чужие — только внешний вид (действие нужно назначить заново).
    """
    buttons, interactive = [], None
    for row in message.components:
        for item in getattr(row, "children", [row]):
            if isinstance(item, discord.components.Button):
                button = _copied_button(item.custom_id)
                if button is None:
                    style = _STYLE_NAMES.get(item.style, "grey")
                    button = {
                        "label": item.label or "—", "emoji": _emoji_text(item.emoji), "style": style,
                        "action_key": None, "value": item.url if style == "link" else None,
                    }
                buttons.append(button)
            elif isinstance(item, discord.components.SelectMenu) and interactive is None:
                native = _NATIVE_TYPES.get(item.type)
                ours = _OUR_NATIVE.match(item.custom_id or "")
                if native or ours:
                    interactive = {"type": "native_select", "kind": native or ours[1]}
                    continue
                interactive = _copied_list(item.custom_id) or {"type": "list", "options": [
                    {"label": option.label, "description": option.description or "",
                     "emoji": _emoji_text(option.emoji), "action_key": None, "value": None}
                    for option in item.options
                ]}
    return {
        "content": message.content or "",
        "embeds": [embed_to_data(embed) for embed in message.embeds if embed.type == "rich"],
        "buttons": buttons,
        "interactive": interactive,
    }


async def fetch_message_for(interaction, text):
    """-> (message, None) или (None, ключ ошибки). Проверяет права и пользователя, и бота."""
    ref = parse_message_ref(text, getattr(interaction.channel, "id", None))
    if ref is None:
        return None, "build_tools.import.bad_ref"
    guild_id, channel_id, message_id = ref
    if guild_id is not None and guild_id != interaction.guild.id:
        return None, "build_tools.import.other_guild"
    channel = interaction.guild.get_channel_or_thread(channel_id)
    if channel is None:
        return None, "build_tools.import.no_channel"
    perms = channel.permissions_for(interaction.user)
    if not (perms.view_channel and perms.read_message_history):
        return None, "build_tools.import.no_access"
    try:
        return await channel.fetch_message(message_id), None
    except discord.NotFound:
        return None, "build_tools.import.not_found"
    except discord.Forbidden:
        return None, "build_tools.import.bot_no_access"


# ============================================================
# ОЧИСТКА ПРИШЕДШИХ СНАРУЖИ ДАННЫХ
# ============================================================

def _clean_embed(raw):
    data = {key: raw.get(key) for key in _EMBED_KEYS if key in raw}
    for key in _URL_KEYS:
        url, ok = check_url(data.get(key))
        data[key] = url if ok else None
    for key in ("title", "description", "author_name", "footer_text"):
        data[key] = str(data.get(key) or "")
    try:
        color = int(data.get("color", core.embed_color().value))
        data["color"] = color if 0 <= color <= 0xFFFFFF else core.embed_color().value
    except (TypeError, ValueError):
        data["color"] = core.embed_color().value
    data["timestamp"] = bool(data.get("timestamp"))
    data["fields"] = [
        {"name": str(f.get("name") or "​")[:256], "value": str(f.get("value") or "​")[:1024],
         "inline": bool(f.get("inline"))}
        for f in (data.get("fields") or [])[:25] if isinstance(f, dict)
    ]
    return data


def _clean_action(interaction, item, style=None):
    """-> (элемент, сброшено ли действие). Действие проверяется как при ручном создании."""
    action_key = item.get("action_key")
    if style == "link":
        value, error = validate_action_value(interaction, None, item.get("value"), style="link")
        return ({**item, "value": value}, False) if not error else (None, True)
    if not action_key:
        return {**item, "action_key": None, "value": None}, False
    allowed = core.is_action_allowed(core.get_user_level(interaction), action_key)
    value, error = validate_action_value(interaction, action_key, item.get("value"), style=style)
    if error or not allowed:
        return {**item, "action_key": None, "value": None}, True
    return {**item, "value": value}, False


def sanitize_payload(interaction, payload):
    """
    Payload из чужого сообщения или JSON -> (безопасный payload, сколько действий сброшено).
    Роли, формы, вебхуки и build'ы проверяются для ЭТОГО сервера и ЭТОГО создателя.
    """
    if not isinstance(payload, dict):
        payload = {}
    dropped = 0
    embeds = [_clean_embed(e) for e in (payload.get("embeds") or [])[:MAX_EMBEDS] if isinstance(e, dict)]

    buttons = []
    for raw in (payload.get("buttons") or [])[:MAX_BUTTONS]:
        if not isinstance(raw, dict):
            continue
        button = normalize_button(raw)
        cleaned, reset = _clean_action(interaction, button, style=button["style"])
        dropped += reset
        if cleaned is not None:
            buttons.append(cleaned)

    interactive = payload.get("interactive")
    if isinstance(interactive, dict) and interactive.get("type") == "list":
        options = []
        for raw in (interactive.get("options") or [])[:25]:
            if not isinstance(raw, dict):
                continue
            option = {
                "label": str(raw.get("label") or t("embed.list.default_option"))[:100],
                "description": str(raw.get("description") or "")[:100],
                "emoji": raw.get("emoji") or None,
                "action_key": raw.get("action_key"), "value": raw.get("value"),
            }
            cleaned, reset = _clean_action(interaction, option)
            dropped += reset
            options.append(cleaned)
        interactive = {"type": "list", "options": options} if options else None
    elif isinstance(interactive, dict) and interactive.get("type") == "native_select" \
            and interactive.get("kind") in NATIVE_SELECT_CLASSES:
        interactive = {"type": "native_select", "kind": interactive["kind"]}
    else:
        interactive = None

    return {
        "content": str(payload.get("content") or "")[:2000],
        "embeds": embeds,
        "buttons": buttons,
        "interactive": interactive,
    }, dropped


def unconfigured_count(payload):
    items = list(payload["buttons"]) + list((payload.get("interactive") or {}).get("options") or [])
    return sum(1 for item in items if item.get("style") != "link" and not item.get("action_key"))


# ============================================================
# ЭКСПОРТ / ИМПОРТ JSON
# ============================================================

def export_build(row):
    """build -> (имя файла, байты JSON)."""
    payload = {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "name": row[3] or "",
        "category": row[8] or "general",
        "content": row[4] or "",
        "embeds": json.loads(row[5] or "[]"),
        "buttons": json.loads(row[6] or "[]"),
        "interactive": json.loads(row[11] or "null"),
    }
    safe_name = re.sub(r"[^\w-]+", "_", payload["name"], flags=re.UNICODE).strip("_")[:40] or "build"
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return f"{safe_name}_{row[0]}.json", data


def parse_export(raw_bytes):
    """-> (payload, None) или (None, ключ ошибки)."""
    if len(raw_bytes) > MAX_IMPORT_BYTES:
        return None, "build_tools.json.too_big"
    try:
        data = json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "build_tools.json.bad_json"
    if not isinstance(data, dict):
        return None, "build_tools.json.bad_format"
    # Принимаем и наш экспорт, и «голый» payload (например, из шаблона).
    if data.get("format") not in (None, EXPORT_FORMAT):
        return None, "build_tools.json.bad_format"
    if not any(key in data for key in ("content", "embeds", "buttons")):
        return None, "build_tools.json.bad_format"
    return data, None


async def open_imported(interaction, payload, name=None, source_key="build_tools.import.done"):
    """Открыть редактор с импортированным содержимым (ещё не сохранено)."""
    from embed_module import EmbedState, EmbedEditorView, editor_embeds

    clean, dropped = sanitize_payload(interaction, payload)
    state = EmbedState(interaction.guild.id, interaction.user.id)
    state.load_payload(clean)
    if name:
        state.name = str(name)[:100]
    if isinstance(payload.get("category"), str) and payload["category"].strip():
        state.category = payload["category"].strip()[:50]
    note = t(source_key, embeds=len(clean["embeds"]), buttons=len(clean["buttons"]))
    unconfigured = unconfigured_count(clean)
    if dropped:
        note += "\n" + t("build_tools.import.dropped", count=dropped)
    if unconfigured:
        note += "\n" + t("build_tools.import.unconfigured", count=unconfigured)
    note += "\n" + t("build_tools.import.save_hint")
    kwargs = dict(content=note, embeds=editor_embeds(state), view=EmbedEditorView(state, back_target=None))
    if interaction.response.is_done():
        await interaction.followup.send(ephemeral=True, **kwargs)
    else:
        await interaction.response.send_message(ephemeral=True, **kwargs)


async def import_from_message(interaction, text):
    message, error = await fetch_message_for(interaction, text)
    if error:
        await reply(interaction, error)
        return
    payload = message_to_payload(message)
    if not payload["content"] and not payload["embeds"] and not payload["buttons"] and not payload["interactive"]:
        await reply(interaction, "build_tools.import.empty")
        return
    core.audit(interaction, "build.imported", "message", message.id, f"channel={message.channel.id}")
    await open_imported(interaction, payload, name=t("build_tools.import.name", id=message.id))


async def import_from_file(interaction, attachment):
    if attachment.size > MAX_IMPORT_BYTES:
        await reply(interaction, "build_tools.json.too_big")
        return
    try:
        raw = await attachment.read()
    except discord.HTTPException:
        await reply(interaction, "build_tools.json.read_error")
        return
    payload, error = parse_export(raw)
    if error:
        await reply(interaction, error)
        return
    core.audit(interaction, "build.imported", "file", attachment.filename)
    await open_imported(interaction, payload, name=payload.get("name"), source_key="build_tools.json.done")


class ImportMessageModal(Modal, title="ИЗ СООБЩЕНИЯ"):
    texts = "build_tools.import_modal"

    link_input = discord.ui.TextInput(label="Ссылка на сообщение или его ID", max_length=200)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await import_from_message(interaction, self.link_input.value)


# ============================================================
# ВЕРСИИ
# ============================================================

def _when(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d.%m %H:%M UTC")


def _who(guild, user_id):
    member = guild.get_member(user_id) if guild else None
    return member.display_name if member else str(user_id)


def remember_version(build_id, user_id):
    """Вызывать ПЕРЕД изменением build'а."""
    save_build_version(build_id, user_id)


class VersionsView(PanelView):
    """Список версий build'а: выбрать -> предпросмотр / откат."""

    def __init__(self, interaction, build_id, back_target):
        super().__init__(back_target=back_target)
        self.build_id = build_id
        versions = get_build_versions(build_id)[:25]
        if not versions:
            self.add_item(discord.ui.Button(label=t("build_tools.versions.empty")[:80], disabled=True))
            return
        select = discord.ui.Select(
            placeholder=t("build_tools.versions.placeholder")[:150],
            options=[
                discord.SelectOption(
                    label=t("build_tools.versions.option", when=_when(created), who=_who(interaction.guild, user_id))[:100],
                    value=str(version_id),
                )
                for version_id, user_id, created in versions
            ],
        )

        async def picked(i):
            version = get_build_version(int(select.values[0]))
            if not version or version[1] != self.build_id:
                await say(i, "build_tools.versions.missing")
                return
            await i.response.edit_message(
                embed=panel_embed(i, "build_tools.version", when=f"<t:{version[4]}:f>", who=f"<@{version[2]}>",
                                  name=version[3].get("name") or "—"),
                view=VersionActionsView(self.build_id, version[0], back_target=(i.message.embeds[0], self)),
            )

        select.callback = picked
        self.add_item(select)


class VersionActionsView(PanelView):
    texts = "build_tools.version"

    def __init__(self, build_id, version_id, back_target):
        super().__init__(back_target=back_target)
        self.build_id = build_id
        self.version_id = version_id

    def _load(self, interaction):
        row = get_message_build(self.build_id)
        version = get_build_version(self.version_id)
        if not row or row[1] != interaction.guild.id or not version or version[1] != self.build_id:
            return None, None
        return row, version

    @discord.ui.button(label="Предпросмотр", emoji="👁️", style=discord.ButtonStyle.secondary)
    async def preview(self, interaction, button):
        row, version = self._load(interaction)
        if not row or not build_visible(interaction, row):
            await say(interaction, "embed.build_not_found")
            return
        snap = version[3]
        data = {
            "content": snap.get("content") or "",
            "embeds": json.loads(snap.get("embeds_json") or "[]"),
            "buttons": [], "interactive": None,
        }
        content, embeds, _ = render_source("b", self.build_id, data)
        if not content and not embeds:
            await say(interaction, "embed.build_empty")
            return
        buttons = len(json.loads(snap.get("buttons_json") or "[]"))
        note = t("build_tools.version.preview_note", buttons=buttons)
        await interaction.response.send_message(
            content=f"{note}\n{content or ''}"[:2000], embeds=embeds[:10], ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Откатить к этой версии", emoji="⏪", style=discord.ButtonStyle.danger)
    async def restore(self, interaction, button):
        from embed_module import can_manage_build, MessageBuildFinalView, build_card_embed

        row, version = self._load(interaction)
        if not row:
            await say(interaction, "embed.build_not_found")
            return
        if not can_manage_build(interaction, row):
            await say(interaction, "embed.edit_denied")
            return
        # Текущее состояние тоже уходит в историю — откат можно отменить.
        remember_version(self.build_id, interaction.user.id)
        fields = {**build_snapshot(row), **{k: v for k, v in version[3].items() if v is not None}}
        update_message_build(self.build_id, **fields)
        core.audit(interaction, "build.restored", "build", self.build_id, f"version={self.version_id}")
        row = get_message_build(self.build_id)
        await interaction.response.edit_message(
            content=t("build_tools.version.restored"),
            embed=build_card_embed(interaction, row),
            view=MessageBuildFinalView(self.build_id, back_target=None),
        )


# ============================================================
# ЭКСПОРТ (кнопка в карточке build'а)
# ============================================================

async def send_export(interaction, row):
    filename, data = export_build(row)
    core.audit(interaction, "build.exported", "build", row[0])
    await interaction.response.send_message(
        t("build_tools.json.exported"), file=discord.File(io.BytesIO(data), filename=filename), ephemeral=True,
    )
