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
import time
from datetime import datetime

import discord

import core
from core import t, Modal, PanelView, get_user_level, member_level, role_problem
from database import get_message_build, get_button_set, get_template, get_form, get_webhook, get_build_settings

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


def say(interaction, key, /, **params):
    return interaction.response.send_message(ephemeral=True, **core.message_for(interaction, key, **params))


async def reply(interaction, key, /, **params):
    """Ответ, даже если на interaction уже ответили (после defer/модалки)."""
    if interaction.response.is_done():
        await interaction.followup.send(ephemeral=True, **core.message_for(interaction, key, **params))
    else:
        await say(interaction, key, **params)


def normalize_url(url):
    url = (url or "").strip()
    if not url:
        return None
    # attachment:// — файл, приложенный к самому сообщению (анимированный баннер пака)
    return url if url.startswith(("http://", "https://", "attachment://")) else "https://" + url


_URL = re.compile(r"^https?://[^\s/<>\"']+\.[^\s<>\"']+$", re.IGNORECASE)
# Лимит Discord на сумму всех текстов одного embed'а.
EMBED_TOTAL_LIMIT = 6000


def check_url(value):
    """-> (url или None, ok). Пустое — ok; мусор вроде «abc def» — не ok:
    иначе Discord отклонит весь embed и редактор «зависнет» на ошибке."""
    url = normalize_url(value)
    if url is None:
        return None, True
    if url.startswith("attachment://"):
        return url, True
    return (url, True) if _URL.match(url) else (None, False)


def embed_has_content(data):
    """Есть ли в embed'е хоть что-то видимое (пустые не отправляем)."""
    return any(
        str(data.get(key) or "").strip()
        for key in ("title", "description", "image", "thumbnail", "author_name", "footer_text")
    ) or bool(data.get("fields"))


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
    if visibility != "restricted":
        return False  # private: старые списки ролей и уровней доступа не дают
    if isinstance(user, int):
        user = guild.get_member(user) if guild is not None else None
    member_roles = {role.id for role in getattr(user, "roles", [])}
    if role_ids and member_roles & set(role_ids):
        return True
    if levels and level >= min(core.level_value(lvl) for lvl in levels):
        return True
    return False


def strip_secrets(buttons):
    """Кнопки для экспорта и шаблонов: без вебхуков (ссылка — секрет, запись — только этого сервера)."""
    cleaned = []
    for button in buttons or []:
        if isinstance(button, dict) and button.get("action_key") == "webhook.send":
            button = {**button, "action_key": None, "value": None}
        cleaned.append(button)
    return cleaned


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
        settings = get_build_settings(source_id)
        embeds = _json(row[5], [])
        parent_id = settings.get("parent_id")
        if parent_id and parent_id != source_id:
            parent = get_message_build(parent_id)
            if parent and parent[1] == row[1]:
                embeds = inherit_style(embeds, _json(parent[5], []))
        return {
            "guild_id": row[1], "owner_id": row[2], "content": row[4] or "",
            "embeds": embeds, "buttons": _json(row[6], []),
            "interactive": _json(row[11], None), "row": row, "settings": settings,
            "layout": row[14] if len(row) > 14 and row[14] in ("single", "split") else "split",
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
# НАСЛЕДОВАНИЕ СТИЛЯ И ПЕРЕМЕННЫЕ
# ============================================================

# Что «ребёнок» берёт у родителя. Цвет — всегда родительский (в этом смысл
# семейства), остальное — только если у ребёнка своё не задано.
STYLE_KEYS = ("author_name", "author_url", "author_icon", "footer_text", "footer_icon", "thumbnail")


def inherit_style(embeds, parent_embeds):
    base = next((e for e in parent_embeds if isinstance(e, dict) and embed_has_content(e)), None)
    if base is None:
        base = next((e for e in parent_embeds if isinstance(e, dict)), None)
    if base is None:
        return embeds
    result = []
    for embed in embeds:
        if not isinstance(embed, dict):
            continue
        merged = dict(embed)
        if "color" in base:
            merged["color"] = base["color"]
        for key in STYLE_KEYS:
            if not merged.get(key) and base.get(key):
                merged[key] = base[key]
        result.append(merged)
    return result


# {имя} или {имя:аргумент}; аргумент — до 300 символов без фигурных скобок и переносов
# (списки каналов/ролей с пробелами в именах: {voice:Клан общий, -AFK}). См. variables.py.
VARIABLE = re.compile(r"\{([a-z_]+)(?::([^{}\n]{1,300}))?\}")
_TEXT_KEYS = ("title", "description", "author_name", "footer_text")


def _texts(data):
    yield data.get("content") or ""
    for embed in data.get("embeds") or []:
        if not isinstance(embed, dict):
            continue
        for key in _TEXT_KEYS:
            yield str(embed.get(key) or "")
        for field in embed.get("fields") or []:
            yield str(field.get("name") or "")
            yield str(field.get("value") or "")


def used_variables(data):
    """-> {(имя, аргумент|None)} — какие переменные встречаются в build'е."""
    found = set()
    for text in _texts(data):
        for match in VARIABLE.finditer(text):
            found.add((match[1], match[2]))
    return found


def substitute(text, variables):
    """{имя} и {имя:аргумент}; неизвестные остаются как есть."""
    if not text or not variables:
        return text

    def one(match):
        value = variables.get(match[1])
        if value is None:
            return match[0]
        if callable(value):
            value = value(match[2])
            if value is None:
                return match[0]
        return str(value)

    return VARIABLE.sub(one, text)


def apply_variables(data, variables):
    if not variables:
        return data
    result = dict(data)
    result["content"] = substitute(data.get("content") or "", variables)
    embeds = []
    for embed in data.get("embeds") or []:
        if not isinstance(embed, dict):
            continue
        embed = dict(embed)
        for key in _TEXT_KEYS:
            if embed.get(key):
                embed[key] = substitute(str(embed[key]), variables)
        embed["fields"] = [
            {**field, "name": substitute(str(field.get("name") or ""), variables),
             "value": substitute(str(field.get("value") or ""), variables)}
            for field in embed.get("fields") or []
        ]
        embeds.append(embed)
    result["embeds"] = embeds
    return result


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
        elif itype == "role_menu" and interactive.get("roles"):
            view.add_item(BuildRoleMenuSelect(src, source_id, interactive, row=0))
            first_row = 1
        elif itype == "native_select" and interactive.get("kind") in NATIVE_SELECT_CLASSES:
            view.add_item(NativeSelect(interactive["kind"], row=0))
            first_row = 1
        elif itype == "form_packs":
            # меню на каждый пак форм (form_packs.py), кнопки — в рядах ниже
            from form_packs import pack_select, MAX_PACKS_IN_MESSAGE
            for pack_id in (interactive.get("packs") or [])[:MAX_PACKS_IN_MESSAGE]:
                select = pack_select(pack_id, row=first_row)
                if select is not None:
                    view.add_item(select)
                    first_row += 1
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


def render_source(src, source_id, data=None, variables=None):
    """-> (content, [Embed], View|None) для предпросмотра и отправки."""
    data = data or load_source(src, source_id)
    if not data:
        return None, [], None
    data = apply_variables(data, variables)
    items = [item for item in data["embeds"][:MAX_EMBEDS] if isinstance(item, dict)]
    view = build_components(src, source_id, data["buttons"], data["interactive"])
    # Пустые embed'ы не отправляем — так можно собрать сообщение из одного
    # текста с кнопками. Но сообщение только из кнопок Discord не примет:
    # тогда оставляем один (пустой) embed как основу.
    visible = [item for item in items if embed_has_content(item)]
    if not visible and not data["content"] and view is not None and items:
        visible = items[:1]
    embeds = [build_discord_embed(item) for item in visible]
    return (data["content"] or None), embeds, view


def message_parts(build_id, variables=None, data=None):
    """Сообщения для отправки build'а: каждый embed отдельно, текст — с первым,
    компоненты — с последним. None — build'а нет, [] — он пустой."""
    data = data or load_source("b", build_id)
    if not data:
        return None
    content, embeds, view = render_source("b", build_id, data, variables)
    if data.get("layout") == "single" and len(embeds) > 1 \
            and sum(len(embed) for embed in embeds) <= EMBED_TOTAL_LIMIT:
        # все embed'ы одним сообщением (Discord: до 10 штук и 6000 символов на всё сообщение)
        parts = [{"embeds": embeds}]
    else:
        parts = [{"embed": embed} for embed in embeds] or [{}]
    if content:
        parts[0]["content"] = content
    if view is not None:
        parts[-1]["view"] = view
    return [] if parts == [{}] else parts


def part_embeds(part):
    """Embed'ы одной части сообщения: {"embed": …} или {"embeds": […]}."""
    if part.get("embeds"):
        return list(part["embeds"])
    return [part["embed"]] if part.get("embed") else []


def single_message(parts):
    """Склеить части в одно сообщение (для правки на месте). None — не влезает."""
    if not parts:
        return None
    embeds = [embed for part in parts for embed in part_embeds(part)]
    if sum(len(embed) for embed in embeds) > EMBED_TOTAL_LIMIT:
        return None
    message = {"content": parts[0].get("content"), "embeds": embeds, "view": parts[-1].get("view")}
    files = [f for part in parts for f in part.get("files") or []]
    if files:
        message["files"] = files
    return message


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


class BuildRoleMenuSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"rb:bm:(?P<src>[bst]):(?P<sid>\d+)"):
    """Меню ролей прямо в сообщении: выбрал роль — выдана, выбрал ещё раз — снята."""

    def __init__(self, src, source_id, interactive=None, *, row=None, select=None):
        if select is None:
            interactive = interactive or {}
            roles = [int(r) for r in interactive.get("roles") or []][:25]
            names = list(interactive.get("names") or [])
            options = [discord.SelectOption(label=(names[n] if n < len(names) else f"#{r}")[:100] or f"#{r}", value=str(r))
                       for n, r in enumerate(roles)] or [discord.SelectOption(label="—", value="0")]
            select = discord.ui.Select(
                custom_id=f"rb:bm:{src}:{source_id}", options=options, min_values=1,
                max_values=1 if interactive.get("single") else len(options),
                placeholder=t("action_wizard.role_menu.public_placeholder")[:150])
        super().__init__(select, row=row)
        self.src, self.source_id = src, source_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["src"], int(match["sid"]), select=item)

    async def callback(self, interaction):
        import action_wizard
        data = await _source_for(interaction, self.src, self.source_id)
        if data is None:
            return
        menu = data["interactive"] or {}
        if menu.get("type") != "role_menu":
            await say(interaction, "actions.source_gone")
            return
        chosen = [int(v) for v in self.item.values if str(v).isdigit()]
        result = await action_wizard.apply_role_menu(interaction, menu.get("roles") or [], chosen,
                                                     bool(menu.get("single")), data["owner_id"], exact=False)
        await interaction.response.send_message(action_wizard.role_menu_result(result), ephemeral=True)


DYNAMIC_ITEMS = [ActionButton, ListSelect, NativeSelect, BuildRoleMenuSelect]


def build_native_select_view(kind):
    view = discord.ui.View(timeout=None)
    view.add_item(NativeSelect(kind))
    return view


# ============================================================
# МОДАЛКИ ДЕЙСТВИЙ
# ============================================================

class MessageEditModal(Modal, title="РЕДАКТИРОВАТЬ СООБЩЕНИЕ"):
    """
    Сообщение не из Message Build: текст над ним и (если есть) заголовок и
    текст первого embed'а. Сообщения из Message Build правятся целиком —
    в конструкторе (см. _message_edit).
    """
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
        self.embed = target_message.embeds[0] if target_message.embeds else None
        if self.embed is not None:
            self.title_input = discord.ui.TextInput(label=t("actions.edit_modal.embed_title")[:45], required=False,
                                                    max_length=256, default=(self.embed.title or "")[:256])
            self.description_input = discord.ui.TextInput(
                label=t("actions.edit_modal.embed_description")[:45], required=False,
                style=discord.TextStyle.paragraph, max_length=4000, default=(self.embed.description or "")[:4000])
            self.add_item(self.title_input)
            self.add_item(self.description_input)

    async def on_submit(self, interaction):
        content = self.content_input.value.strip() or None
        embeds = list(self.target_message.embeds)
        if self.embed is not None:
            changed = self.embed.copy()
            changed.title = self.title_input.value.strip() or None
            changed.description = self.description_input.value.strip() or None
            embeds[0] = changed
        if content is None and not any(e.title or e.description or e.fields or e.image for e in embeds):
            await say(interaction, "actions.edit_modal.empty")
            return
        try:
            await self.target_message.edit(content=content, embeds=embeds,
                                           allowed_mentions=discord.AllowedMentions.none())
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
        # URL вебхука — секрет: никогда не показываем его нажавшему. Старые
        # кнопки со ссылкой внутри работают, но ссылку берём на сервере.
        self.secret_url = default_url or None
        if record_id is not None or self.secret_url:
            self.remove_item(self.url_input)

    async def on_submit(self, interaction):
        if self.record_id is not None:
            await say(interaction, await send_via_webhook(interaction, self.record_id, self.content_input.value))
            return
        else:
            webhook_url = normalize_url(self.secret_url or self.url_input.value)
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


async def send_via_webhook(interaction, record_id, text):
    """Отправить через сохранённый вебхук сервера. -> ключ итога для нажавшего."""
    row = get_webhook(record_id)
    if not row or row[1] != interaction.guild.id:
        return "actions.webhook_record_missing"
    try:
        webhook = discord.Webhook.from_url(row[6], client=interaction.client)
        await webhook.send(text, username=row[5] or interaction.client.user.name,
                           avatar_url=row[7] or discord.utils.MISSING,
                           allowed_mentions=discord.AllowedMentions(everyone=False, roles=False))
    except (discord.HTTPException, ValueError):
        return "actions.webhook_modal.failed"
    core.audit(interaction, "webhook.sent", "webhook", record_id)
    return "actions.webhook_modal.sent"


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

def validate_action_value(interaction, action_key, value, style=None, actor=None):
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
        import action_wizard
        options = action_wizard.reply_options(value)
        if options["to"] == "me":
            return options["text"][:2000], None
        return action_wizard.dump({"to": options["to"], "text": options["text"][:2000]}), None
    if action_key == "message.edit":
        return "", None
    if action_key == "select.trigger":
        import action_wizard
        menu = action_wizard.role_menu_options(value)
        if menu is not None:
            # меню ролей раздаёт роли — собирать его может тот, кому можно кнопку «Роль»
            if not core.is_action_allowed(member_level(guild, actor or interaction.user), "role.toggle"):
                return None, "actions.no_access"
            roles = [guild.get_role(r) for r in menu["roles"]]
            if not roles or None in roles:
                return None, "actions.role.bad_value"
            for role in roles:
                problem = role_problem(guild, role, actor or interaction.user)
                if problem:
                    return None, problem
            return action_wizard.dump(menu), None
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
        # роль проверяется для создателя кнопки: при клике права берутся его
        problem = role_problem(guild, role, actor or interaction.user)
        return (None, problem) if problem else (str(role_id), None)
    if action_key == "webhook.send":
        # только сохранённый вебхук этого сервера, которым ты управляешь (/webhook);
        # ссылку-секрет в кнопку больше не кладём: её видели бы нажавшие и экспорт
        import action_wizard
        from extended_modules import can_manage_webhook
        options = action_wizard.webhook_options(value)
        record_id = options["id"]
        row = get_webhook(record_id) if record_id is not None else None
        if row and row[1] == guild.id and can_manage_webhook(interaction, row):
            if options["text"]:
                return action_wizard.dump({"id": record_id, "text": options["text"][:2000]}), None
            return str(record_id), None
        return None, "actions.webhook_record_missing"
    if action_key == "counter.change":
        parsed = parse_counter(value)
        if parsed is None:
            return None, "actions.counter.bad_value"
        name, op, number = parsed
        return f"{name} {op}{number}", None
    if action_key in BUILD_ACTIONS:
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
    if core.needs_confirmation(action_key) and not skip_confirmation:
        await interaction.response.send_message(
            t("actions.dangerous.text", action=(action_row[3] or action_key) if action_row else action_key),
            view=DangerousActionView(action_key, value, creator_id=creator_id, target_message=interaction.message),
            ephemeral=True,
        )
        return

    handler = _HANDLERS.get(action_key)
    if handler is None:
        await reply(interaction, "actions.wip", action=action_key)
        return
    await handler(interaction, value, creator_id=creator_id, target_message=target_message)


_REPLY_COOLDOWN = 10
_last_reply = {}  # (канал/сообщение, user_id) -> ts последнего ответа в канал


async def _send_reply(interaction, value, default_key):
    """
    «Ответить текстом»: нажавшему (скрыто), в канал всем или в личку.
    Переменные подставляются для нажавшего: {user}, {counter:очки}…
    """
    import action_wizard
    options = action_wizard.reply_options(value)
    text = await action_wizard.with_variables(interaction, options["text"] or t(default_key))
    text = text[:2000]
    if options["to"] == "channel":
        key = (getattr(interaction.message, "id", None) or getattr(interaction, "channel_id", None), interaction.user.id)
        now = time.monotonic()
        if key in _last_reply and now - _last_reply[key] < _REPLY_COOLDOWN:
            await reply(interaction, "actions.reply.cooldown",
                        seconds=int(_REPLY_COOLDOWN - (now - _last_reply[key])) + 1)
            return
        _last_reply[key] = now
        try:
            await interaction.channel.send(content=text,
                                           allowed_mentions=discord.AllowedMentions(everyone=False, roles=False))
        except (discord.HTTPException, AttributeError):
            await reply(interaction, "actions.reply.channel_failed")
            return
        await reply(interaction, "actions.reply.posted")
        return
    if options["to"] == "dm":
        try:
            await interaction.user.send(text)
        except discord.HTTPException:
            # личка закрыта — показываем здесь же, чтобы текст не потерялся
            await interaction.response.send_message(f"{t('actions.reply.dm_closed')}\n\n{text}"[:2000], ephemeral=True)
            return
        await reply(interaction, "actions.reply.dm_sent")
        return
    await interaction.response.send_message(text, ephemeral=True)


async def _message_send(interaction, value, **_):
    await _send_reply(interaction, value, "actions.default.message")


async def _message_confirm(interaction, value, **_):
    await _send_reply(interaction, value, "actions.default.confirm")


async def _message_edit(interaction, value, target_message=None, **_):
    target_message = target_message or interaction.message
    if target_message is None:
        await reply(interaction, "actions.edit_no_message")
        return
    if target_message.author.id != interaction.client.user.id:
        await reply(interaction, "actions.edit_not_bot")
        return
    from database import get_instance_build
    build_id = get_instance_build(target_message.id)
    row = get_message_build(build_id) if build_id else None
    if row and interaction.guild is not None and row[1] == interaction.guild.id:
        # сообщение из Message Build: правим сам build (весь вид, кнопки, картинки),
        # потом «Обновить отправленные» меняет это и остальные копии
        import embed_module
        await embed_module.open_build_panel(interaction, build_id)
        return
    await interaction.response.send_modal(MessageEditModal(target_message))


async def _form_trigger(interaction, value, **_):
    from extended_modules import open_form

    form_id = parse_id(value)
    if form_id is None:
        await reply(interaction, "actions.form_trigger.bad_value")
        return
    await open_form(interaction, form_id)


async def _select_trigger(interaction, value, creator_id=None, **_):
    import action_wizard
    menu = action_wizard.role_menu_options(value)
    if menu is not None:
        await action_wizard.open_role_menu(interaction, menu, creator_id)
        return
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
        # при нажатии проверяется создатель кнопки, а не нажавший — так и пишем
        await reply(interaction, "actions.role.creator_" + problem.split(".")[-1]
                    if core.has_text("actions.role.creator_" + problem.split(".")[-1]) else problem)
        return
    member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
    if member is None:
        await reply(interaction, "actions.role.no_member")
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
    import action_wizard
    options = action_wizard.webhook_options(value)
    if options["id"] is not None and options["text"]:
        # текст задан заранее — отправляем сразу (подтверждение уже было)
        text = await action_wizard.with_variables(interaction, options["text"])
        await interaction.response.defer(ephemeral=True, thinking=True)
        await reply(interaction, await send_via_webhook(interaction, options["id"], text[:2000]))
        return
    record_id = options["id"]
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


BUILD_ACTIONS = ("build.trigger", "build.goto", "build.refresh")
_COUNTER_NAME = re.compile(r"^[\w-]{1,32}$", re.UNICODE)


def parse_counter(value):
    """«очки +1», «очки -5», «очки =0», просто «очки» (= +1) -> (имя, оп, число) или None."""
    parts = (value or "").split()
    if not parts or len(parts) > 2 or not _COUNTER_NAME.match(parts[0]):
        return None
    if len(parts) == 1:
        return parts[0].lower(), "+", 1
    match = re.match(r"^([+=-])?(\d{1,9})$", parts[1])
    if not match:
        return None
    return parts[0].lower(), match[1] or "+", int(match[2])


def variant_for(interaction, row):
    """
    Условный контент: build может показывать нажавшему другой build в
    зависимости от его уровня доступа или ролей. Первый подходящий вариант.
    """
    from database import get_build_settings

    level = core.level_value(get_user_level(interaction))
    roles = {role.id for role in getattr(interaction.user, "roles", [])}
    for variant in get_build_settings(row[0]).get("variants") or []:
        target = get_message_build(variant.get("build_id") or 0)
        if not target or target[1] != row[1] or not build_attach_allowed(interaction, target, row[2]):
            continue
        if variant.get("level") and level >= core.level_value(variant["level"]):
            return target[0]
        if set(variant.get("roles") or []) & roles:
            return target[0]
    return row[0]


async def _resolve_build(interaction, value, creator_id):
    """Общая проверка для build-действий -> id build'а для показа или None."""
    build_id = parse_id(value)
    row = get_message_build(build_id) if build_id else None
    if not row or row[1] != interaction.guild.id:
        await reply(interaction, "embed.build_not_found")
        return None
    if not build_attach_allowed(interaction, row, creator_id):
        await reply(interaction, "actions.build_no_access")
        return None
    return variant_for(interaction, row)


def button_message(parts):
    """
    По кнопке build показывается ОДНИМ сообщением: все embed'ы вместе, если
    влезают в лимиты Discord, иначе только первая часть. Об этом предупреждаем
    при выборе «разными сообщениями» в конструкторе.
    """
    return single_message(parts) or dict(parts[0])


async def _send_parts(interaction, parts):
    # Как при обычной отправке: каждый embed отдельным сообщением, иначе
    # большой build упрётся в лимит Discord 6000 символов на сообщение.
    for index, part in enumerate(parts):
        if index == 0 and not interaction.response.is_done():
            await interaction.response.send_message(ephemeral=True, **part)
        else:
            await interaction.followup.send(ephemeral=True, **part)


async def _build_trigger(interaction, value, creator_id=None, **_):
    import live

    build_id = await _resolve_build(interaction, value, creator_id)
    if build_id is None:
        return
    parts = await live.parts_for(interaction.guild, build_id, user=interaction.user)
    if not parts:
        await reply(interaction, "embed.build_empty")
        return
    await _send_parts(interaction, [button_message(parts)])


async def _build_goto(interaction, value, creator_id=None, **_):
    """
    Шаг мастера: сообщение переписывает само себя. Если кнопка нажата в
    личном (ephemeral) сообщении — оно меняется на месте; в общем сообщении
    менять его для всех нельзя, поэтому шаги начинаются в личном сообщении.
    """
    import live

    build_id = await _resolve_build(interaction, value, creator_id)
    if build_id is None:
        return
    parts = await live.parts_for(interaction.guild, build_id, user=interaction.user)
    if not parts:
        await reply(interaction, "embed.build_empty")
        return
    message = interaction.message
    single = button_message(parts)
    if message is not None and message.flags.ephemeral:
        single = dict(single)
        single["attachments"] = single.pop("files", [])  # правка сообщения: вложения заменяются целиком
        await interaction.response.edit_message(**{"content": None, "embeds": [], "view": None, **single})
        return
    await _send_parts(interaction, [single])


_REFRESH_COOLDOWN = 30
_last_refresh = {}  # build_id -> ts последнего обновления кнопкой


async def _build_refresh(interaction, value, creator_id=None, **_):
    import live

    build_id = parse_id(value)
    row = get_message_build(build_id) if build_id else None
    if not row or row[1] != interaction.guild.id:
        await reply(interaction, "embed.build_not_found")
        return
    if not build_attach_allowed(interaction, row, creator_id):
        await reply(interaction, "actions.build_no_access")
        return
    # Кнопка доступна всем: без паузы её можно жать без конца, и каждое
    # нажатие правит все отправленные сообщения build'а (лимиты Discord).
    now = time.monotonic()
    wait = _REFRESH_COOLDOWN - (now - _last_refresh[build_id]) if build_id in _last_refresh else 0
    if wait > 0:
        await reply(interaction, "actions.build_refresh.cooldown", seconds=int(wait) + 1)
        return
    _last_refresh[build_id] = now
    await interaction.response.defer(ephemeral=True, thinking=True)
    updated, removed, added, missing = await live.resync_build(interaction.guild, build_id)
    key = "actions.build_refresh.done_missing" if missing else "actions.build_refresh.done"
    await interaction.followup.send(t(key, updated=updated + added, missing=missing), ephemeral=True)


_COUNTER_COOLDOWN = 3
_last_counter = {}  # (guild_id, user_id, имя) -> ts последнего нажатия


def counter_wait(guild_id, user_id, name, now=None):
    """Сколько секунд ещё ждать этому участнику до следующего нажатия (0 — можно)."""
    now = time.monotonic() if now is None else now
    key = (guild_id, user_id, name)
    wait = _COUNTER_COOLDOWN - (now - _last_counter[key]) if key in _last_counter else 0
    if wait > 0:
        return wait
    _last_counter[key] = now
    if len(_last_counter) > 5000:  # не копим старые записи бесконечно
        for old in [k for k, ts in _last_counter.items() if now - ts > _COUNTER_COOLDOWN]:
            _last_counter.pop(old, None)
    return 0


async def _counter_change(interaction, value, **_):
    import live
    from database import change_counter

    parsed = parse_counter(value)
    if parsed is None:
        await reply(interaction, "actions.counter.bad_value")
        return
    name, op, number = parsed
    # кнопка доступна всем: без паузы счётчик накручивают автокликером
    wait = counter_wait(interaction.guild.id, interaction.user.id, name)
    if wait > 0:
        await reply(interaction, "actions.counter.cooldown", seconds=int(wait) + 1)
        return
    if op == "=":
        result = change_counter(interaction.guild.id, name, set_to=number)
    else:
        result = change_counter(interaction.guild.id, name, delta=number if op == "+" else -number)
    core.audit(interaction, "counter.changed", "counter", name, f"{op}{number} -> {result}")
    live.counter_changed(interaction.guild, name)
    import cards
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)  # картинки перерисовываются пару секунд
    pictures = await cards.counter_changed(interaction.guild, name, op, number)
    staff = core.level_value(get_user_level(interaction)) >= core.level_value("staff")
    if not pictures:
        # участнику — просто итог; тем, кто собирает кнопки, — почему картинка не изменилась
        await reply(interaction, "actions.counter.no_pictures" if staff else "actions.counter.done",
                    name=name, value=result)
        return
    if not staff:
        await reply(interaction, "actions.counter.done_pictures", name=name, value=result, count=len(pictures))
        return
    lines = "\n".join(t(f"actions.counter.picture.{status}", title=title) for title, status in pictures)
    await reply(interaction, "actions.counter.done_report", name=name, value=result, pictures=lines)


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
    "build.goto": _build_goto,
    "build.refresh": _build_refresh,
    "counter.change": _counter_change,
}
