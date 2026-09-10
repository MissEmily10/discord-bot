import json
import os
from datetime import datetime

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

from database import (
    init_database,
    save_message_build,
    get_message_build,
    get_message_builds,
    delete_message_build,
    save_button_set,
    get_button_set,
    get_button_sets,
    delete_button_set,
)

import core
from core import require_command_access
import access_module
import embed_module
from extended_modules import register_extended

TOKEN = os.getenv("DISCORD_TOKEN")

# =========================
# CONSTANTS
# =========================
# ВАЖНО: цвет/тексты постепенно переезжают в core.get_setting(), чтобы
# потом /design мог менять их без правки кода. Embed-билдер пока не
# трогаем в этом проходе — доберёмся до него отдельным модулем.

EMBED_COLOR = discord.Color.blurple()

MAX_EMBEDS = 10
MAX_BUTTONS = 5

BUTTON_STYLES = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
    "link": discord.ButtonStyle.link,
}


# =========================
# DISCORD
# =========================

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# =========================
# HELPERS
# =========================

def safe_json_loads(value, fallback):
    try:
        result = json.loads(value)
        return result
    except (TypeError, json.JSONDecodeError):
        return fallback


def normalize_url(url):
    url = (url or "").strip()
    if not url:
        return None

    if not url.startswith(("http://", "https://")):
        return "https://" + url

    return url


def build_discord_embed(data):
    embed = discord.Embed(
        title=data.get("title") or None,
        description=data.get("description") or "\u200b",
        url=normalize_url(data.get("url")),
        color=int(data.get("color", 0x5865F2))
    )

    author_name = data.get("author_name")
    if author_name:
        embed.set_author(
            name=author_name,
            url=normalize_url(data.get("author_url")),
            icon_url=normalize_url(data.get("author_icon"))
        )

    thumbnail = normalize_url(data.get("thumbnail"))
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)

    image = normalize_url(data.get("image"))
    if image:
        embed.set_image(url=image)

    footer_text = data.get("footer_text")
    if footer_text:
        embed.set_footer(
            text=footer_text,
            icon_url=normalize_url(data.get("footer_icon"))
        )

    if data.get("timestamp"):
        embed.timestamp = datetime.now()

    for field in data.get("fields", []):
        name = str(field.get("name", "‎"))[:256]
        value = str(field.get("value", "‎"))[:1024]
        embed.add_field(
            name=name,
            value=value,
            inline=bool(field.get("inline", False))
        )

    return embed


def build_button_view(buttons):
    if not buttons:
        return None

    view = discord.ui.View(timeout=None)

    for item in buttons[:MAX_BUTTONS]:
        label = str(item.get("label", "Кнопка"))[:80]
        emoji = item.get("emoji")
        style_name = item.get("style", "primary")
        action = item.get("action", "link")
        value = item.get("value")

        style = BUTTON_STYLES.get(
            style_name,
            discord.ButtonStyle.primary
        )

        if action == "link":
            url = normalize_url(value)
            if not url:
                continue

            button = discord.ui.Button(
                label=label,
                emoji=emoji,
                style=discord.ButtonStyle.link,
                url=url
            )
            view.add_item(button)
            continue

        button = discord.ui.Button(
            label=label,
            emoji=emoji,
            style=style
        )

        async def callback(
            interaction,
            action=action,
            value=value
        ):
            from embed_module import dispatch_action

            action_key = {
                "message": "message.send",
                "confirm": "message.confirm",
            }.get(action, action)
            await dispatch_action(interaction, action_key, value)

        button.callback = callback
        view.add_item(button)

    return view


# =========================
# READY
# =========================

@bot.event
async def on_ready():
    init_database()
    core.ensure_default_actions()

    print(f"Бот запущен: {bot.user}")

    try:
        synced = await bot.tree.sync()
        print(f"Синхронизировано команд: {len(synced)}")
    except Exception as error:
        print(f"Ошибка синхронизации: {error}")


# =========================
# PING
# =========================

@bot.tree.command(
    name="ping",
    description="Проверка работы бота"
)
async def ping(interaction):
    if not await require_command_access(interaction, "ping"):
        return

    await interaction.response.send_message(
        "🏓 Pong!",
        ephemeral=True
    )


# ============================================================
# MESSAGE BUILD / EMBED BUILDER
# (без изменений в этом проходе — дорабатываем отдельным модулем следующим шагом)
# ============================================================

class EmbedBuilderState:

    def __init__(self):
        self.content = ""
        self.embeds = []
        self.buttons = []
        self.name = "Новый Message Build"
        self.category = "general"
        self.visibility = "private"
        self.channel_id = None
        self.standalone_button_set = False

    def preview_embed(self):
        if self.embeds:
            return build_discord_embed(self.embeds[0])

        return discord.Embed(
            title="ПРЕДПРОСМОТР",
            description="Embed пока пуст.",
            color=EMBED_COLOR
        )


class EmbedBasicModal(discord.ui.Modal, title="ОСНОВНОЙ EMBED"):

    title_input = discord.ui.TextInput(
        label="Title",
        placeholder="Заголовок embed",
        required=False,
        max_length=256
    )

    description_input = discord.ui.TextInput(
        label="Description",
        placeholder="Основной текст",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=4000
    )

    url_input = discord.ui.TextInput(
        label="URL",
        placeholder="https://...",
        required=False,
        max_length=1000
    )

    color_input = discord.ui.TextInput(
        label="Цвет HEX",
        placeholder="#5865F2",
        required=False,
        max_length=7
    )

    def __init__(self, state):
        super().__init__()
        self.state = state

        existing = state.embeds[0] if state.embeds else {}

        self.title_input.default = existing.get("title", "")
        self.description_input.default = existing.get("description", "")
        self.url_input.default = existing.get("url", "")
        self.color_input.default = existing.get("color_hex", "#5865F2")

    async def on_submit(self, interaction):
        color_text = self.color_input.value.strip().replace("#", "")

        try:
            color_value = int(color_text, 16) if color_text else 0x5865F2
            if not 0 <= color_value <= 0xFFFFFF:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "HEX-цвет указан некорректно. Используй формат вроде `#5865F2`.",
                ephemeral=True
            )
            return

        data = {
            "title": self.title_input.value.strip(),
            "description": self.description_input.value.strip(),
            "url": normalize_url(self.url_input.value),
            "color": color_value,
            "color_hex": f"#{color_value:06X}",
            "fields": [],
        }

        if self.state.embeds:
            old = self.state.embeds[0]
            for key in (
                "author_name", "author_url", "author_icon",
                "thumbnail", "image", "footer_text", "footer_icon",
                "timestamp", "fields"
            ):
                if key in old:
                    data[key] = old[key]

        self.state.embeds = [data]

        await interaction.response.edit_message(
            embed=render_build_preview(self.state),
            view=EmbedDesignView(self.state)
        )


class EmbedMediaModal(discord.ui.Modal, title="ИЗОБРАЖЕНИЯ EMBED"):

    thumbnail_input = discord.ui.TextInput(
        label="Thumbnail URL",
        placeholder="Прямая HTTPS-ссылка на изображение",
        required=False,
        max_length=1000
    )

    image_input = discord.ui.TextInput(
        label="Image URL",
        placeholder="Прямая HTTPS-ссылка на изображение",
        required=False,
        max_length=1000
    )

    def __init__(self, state):
        super().__init__()
        self.state = state
        existing = state.embeds[0] if state.embeds else {}

        self.thumbnail_input.default = existing.get("thumbnail", "")
        self.image_input.default = existing.get("image", "")

    async def on_submit(self, interaction):
        if not self.state.embeds:
            self.state.embeds = [{
                "title": "",
                "description": "",
                "color": 0x5865F2,
                "fields": []
            }]

        self.state.embeds[0]["thumbnail"] = normalize_url(
            self.thumbnail_input.value
        )
        self.state.embeds[0]["image"] = normalize_url(
            self.image_input.value
        )

        await interaction.response.edit_message(
            embed=render_build_preview(self.state),
            view=EmbedDesignView(self.state)
        )


class EmbedAuthorFooterModal(discord.ui.Modal, title="AUTHOR / FOOTER"):

    author_input = discord.ui.TextInput(
        label="Author name",
        required=False,
        max_length=256
    )

    author_url_input = discord.ui.TextInput(
        label="Author URL",
        required=False,
        max_length=1000
    )

    author_icon_input = discord.ui.TextInput(
        label="Author icon URL",
        required=False,
        max_length=1000
    )

    footer_input = discord.ui.TextInput(
        label="Footer",
        required=False,
        max_length=2048
    )

    footer_icon_input = discord.ui.TextInput(
        label="Footer icon URL",
        required=False,
        max_length=1000
    )

    def __init__(self, state):
        super().__init__()
        self.state = state
        existing = state.embeds[0] if state.embeds else {}

        self.author_input.default = existing.get("author_name", "")
        self.author_url_input.default = existing.get("author_url", "")
        self.author_icon_input.default = existing.get("author_icon", "")
        self.footer_input.default = existing.get("footer_text", "")
        self.footer_icon_input.default = existing.get("footer_icon", "")

    async def on_submit(self, interaction):
        if not self.state.embeds:
            self.state.embeds = [{
                "title": "",
                "description": "",
                "color": 0x5865F2,
                "fields": []
            }]

        data = self.state.embeds[0]
        data["author_name"] = self.author_input.value.strip()
        data["author_url"] = normalize_url(self.author_url_input.value)
        data["author_icon"] = normalize_url(self.author_icon_input.value)
        data["footer_text"] = self.footer_input.value.strip()
        data["footer_icon"] = normalize_url(self.footer_icon_input.value)

        await interaction.response.edit_message(
            embed=render_build_preview(self.state),
            view=EmbedDesignView(self.state)
        )


class EmbedFieldModal(discord.ui.Modal, title="FIELD"):

    name_input = discord.ui.TextInput(
        label="Название поля",
        required=True,
        max_length=256
    )

    value_input = discord.ui.TextInput(
        label="Значение",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1024
    )

    inline_input = discord.ui.TextInput(
        label="Inline? yes / no",
        required=False,
        max_length=3
    )

    def __init__(self, state):
        super().__init__()
        self.state = state

    async def on_submit(self, interaction):
        if not self.state.embeds:
            self.state.embeds = [{
                "title": "",
                "description": "",
                "color": 0x5865F2,
                "fields": []
            }]

        inline = self.inline_input.value.strip().lower() in {
            "yes", "y", "да", "д"
        }

        self.state.embeds[0].setdefault("fields", []).append({
            "name": self.name_input.value,
            "value": self.value_input.value,
            "inline": inline
        })

        await interaction.response.edit_message(
            embed=render_build_preview(self.state),
            view=EmbedDesignView(self.state)
        )


class EmbedDesignView(discord.ui.View):

    def __init__(self, state):
        super().__init__(timeout=900)
        self.state = state

    @discord.ui.button(
        label="Основное",
        emoji="✏️",
        style=discord.ButtonStyle.primary
    )
    async def basic(self, interaction, button):
        await interaction.response.send_modal(
            EmbedBasicModal(self.state)
        )

    @discord.ui.button(
        label="Изображения",
        emoji="🖼️",
        style=discord.ButtonStyle.secondary
    )
    async def media(self, interaction, button):
        await interaction.response.send_modal(
            EmbedMediaModal(self.state)
        )

    @discord.ui.button(
        label="Author / Footer",
        emoji="🏷️",
        style=discord.ButtonStyle.secondary
    )
    async def author_footer(self, interaction, button):
        await interaction.response.send_modal(
            EmbedAuthorFooterModal(self.state)
        )

    @discord.ui.button(
        label="Добавить поле",
        emoji="➕",
        style=discord.ButtonStyle.secondary
    )
    async def add_field(self, interaction, button):
        if self.state.embeds and len(
            self.state.embeds[0].get("fields", [])
        ) >= 25:
            await interaction.response.send_message(
                "В одном embed максимум 25 полей.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(
            EmbedFieldModal(self.state)
        )

    @discord.ui.button(
        label="Добавить embed",
        emoji="🧩",
        style=discord.ButtonStyle.secondary
    )
    async def add_embed(self, interaction, button):
        if len(self.state.embeds) >= MAX_EMBEDS:
            await interaction.response.send_message(
                "Discord позволяет максимум 10 embed'ов в одном сообщении.",
                ephemeral=True
            )
            return

        self.state.embeds.append({
            "title": f"Embed {len(self.state.embeds) + 1}",
            "description": "",
            "color": 0x5865F2,
            "fields": []
        })

        await interaction.response.edit_message(
            embed=render_build_preview(self.state),
            view=self
        )

    @discord.ui.button(
        label="Подтвердить дизайн",
        emoji="✅",
        style=discord.ButtonStyle.success
    )
    async def confirm(self, interaction, button):
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="ПОНАДОБЯТСЯ КНОПКИ?",
                description=(
                    "Дизайн сообщения подтверждён.\n\n"
                    "Теперь отдельно соберём интерактив."
                ),
                color=EMBED_COLOR
            ),
            view=ButtonDecisionView(self.state)
        )


class ButtonDecisionView(discord.ui.View):

    def __init__(self, state):
        super().__init__(timeout=900)
        self.state = state

    @discord.ui.button(
        label="Назад",
        emoji="↩️",
        style=discord.ButtonStyle.secondary
    )
    async def back(self, interaction, button):
        await interaction.response.edit_message(
            embed=render_build_preview(self.state),
            view=EmbedDesignView(self.state)
        )

    @discord.ui.button(
        label="Да, создать форму",
        emoji="📝",
        style=discord.ButtonStyle.primary
    )
    async def form(self, interaction, button):
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="FORM BUILDER",
                description="Настройка формы, вопросов, канала заявок, reviewers и действия после решения.",
                color=EMBED_COLOR
            ),
            view=__import__("extended_modules").FormStartView(interaction.guild.id, interaction.user.id)
        )

    @discord.ui.button(
        label="Да, создать список",
        emoji="📋",
        style=discord.ButtonStyle.primary
    )
    async def select_menu(self, interaction, button):
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="SELECT MENU",
                description="Выберите тип интерактивного списка.",
                color=EMBED_COLOR
            ),
            view=__import__("extended_modules").SelectHome()
        )

    @discord.ui.button(
        label="Да, обычная кнопка",
        emoji="🔘",
        style=discord.ButtonStyle.primary
    )
    async def ordinary(self, interaction, button):
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="BUTTON BUILDER",
                description="Создай до 5 кнопок для этого Message Build.",
                color=EMBED_COLOR
            ),
            view=InlineButtonBuilderView(self.state)
        )

    @discord.ui.button(
        label="Нет, кнопки не нужны",
        emoji="❌",
        style=discord.ButtonStyle.secondary
    )
    async def none(self, interaction, button):
        await finish_message_build(interaction, self.state)


class InlineButtonModal(discord.ui.Modal, title="НОВАЯ КНОПКА"):

    label_input = discord.ui.TextInput(
        label="Текст кнопки",
        required=True,
        max_length=80
    )

    emoji_input = discord.ui.TextInput(
        label="Emoji",
        placeholder="Например: ✨ или :my_emoji:",
        required=False,
        max_length=100
    )

    style_input = discord.ui.TextInput(
        label="Стиль: primary / secondary / success / danger",
        required=True,
        max_length=10
    )

    action_input = discord.ui.TextInput(
        label="Действие: link / message / confirm",
        required=True,
        max_length=20
    )

    value_input = discord.ui.TextInput(
        label="URL или текст действия",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=1000
    )

    def __init__(self, state):
        super().__init__()
        self.state = state

    async def on_submit(self, interaction):
        if len(self.state.buttons) >= MAX_BUTTONS:
            await interaction.response.send_message(
                "В одном Message Build сейчас максимум 5 кнопок.",
                ephemeral=True
            )
            return

        style = self.style_input.value.strip().lower()
        action = self.action_input.value.strip().lower()

        if style not in {"primary", "secondary", "success", "danger"}:
            await interaction.response.send_message(
                "Стиль должен быть: primary, secondary, success или danger.",
                ephemeral=True
            )
            return

        if action not in {"link", "message", "confirm"}:
            await interaction.response.send_message(
                "Действие должно быть: link, message или confirm.",
                ephemeral=True
            )
            return

        if action == "link" and not normalize_url(self.value_input.value):
            await interaction.response.send_message(
                "Для link нужен URL.",
                ephemeral=True
            )
            return

        self.state.buttons.append({
            "label": self.label_input.value,
            "emoji": self.emoji_input.value.strip() or None,
            "style": style,
            "action": action,
            "value": (
                normalize_url(self.value_input.value)
                if action == "link"
                else self.value_input.value
            )
        })

        await interaction.response.edit_message(
            embed=render_button_preview(self.state),
            view=InlineButtonBuilderView(self.state)
        )


class InlineButtonBuilderView(discord.ui.View):

    def __init__(self, state):
        super().__init__(timeout=900)
        self.state = state

    @discord.ui.button(
        label="Добавить кнопку",
        emoji="➕",
        style=discord.ButtonStyle.success
    )
    async def add(self, interaction, button):
        if len(self.state.buttons) >= MAX_BUTTONS:
            await interaction.response.send_message(
                "Лимит достигнут: максимум 5 кнопок.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(
            InlineButtonModal(self.state)
        )

    @discord.ui.button(
        label="Готово",
        emoji="✅",
        style=discord.ButtonStyle.primary
    )
    async def done(self, interaction, button):
        if self.state.standalone_button_set:
            set_id = save_button_set(
                guild_id=interaction.guild.id,
                owner_id=interaction.user.id,
                name=self.state.name,
                buttons_json=json.dumps(self.state.buttons, ensure_ascii=False),
                visibility=self.state.visibility,
                category=self.state.category,
            )
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="BUTTON SET СОХРАНЁН",
                    description=f"ID: `{set_id}`\nКнопок: **{len(self.state.buttons)}**",
                    color=EMBED_COLOR,
                ),
                view=ButtonSetListView(
                    self.state.guild_id,
                    interaction.user.id,
                ),
            )
            return
        await finish_message_build(interaction, self.state)

    @discord.ui.button(
        label="Без кнопок",
        emoji="❌",
        style=discord.ButtonStyle.secondary
    )
    async def clear(self, interaction, button):
        self.state.buttons = []
        await finish_message_build(interaction, self.state)


def render_build_preview(state):
    if not state.embeds:
        return discord.Embed(
            title="MESSAGE BUILD",
            description="Добавь первый embed.",
            color=EMBED_COLOR
        )

    embed = build_discord_embed(state.embeds[0])

    if len(state.embeds) > 1:
        embed.set_footer(
            text=f"Предпросмотр: {len(state.embeds)} embed'ов"
        )

    return embed


def render_button_preview(state):
    embed = render_build_preview(state)
    embed.title = "BUTTON BUILDER"

    button_lines = []
    for index, item in enumerate(state.buttons, start=1):
        button_lines.append(
            f"{index}. {item.get('emoji') or ''} "
            f"{item.get('label', 'Кнопка')} · "
            f"{item.get('action', 'action')}"
        )

    embed.description = (
        "\n".join(button_lines)
        if button_lines
        else "Кнопок пока нет."
    )

    return embed


async def finish_message_build(interaction, state):
    if interaction.guild is None:
        await interaction.response.send_message(
            "Эта функция работает только на сервере.",
            ephemeral=True
        )
        return

    build_id = save_message_build(
        guild_id=interaction.guild.id,
        owner_id=interaction.user.id,
        name=state.name,
        content=state.content,
        embeds_json=json.dumps(state.embeds, ensure_ascii=False),
        buttons_json=json.dumps(state.buttons, ensure_ascii=False),
        visibility=state.visibility,
        category=state.category,
    )

    await interaction.response.edit_message(
        content=None,
        embed=discord.Embed(
            title="MESSAGE BUILD СОХРАНЁН",
            description=(
                f"ID: `{build_id}`\n\n"
                f"Embed'ов: **{len(state.embeds)}**\n"
                f"Кнопок: **{len(state.buttons)}**\n"
                f"Видимость: **{state.visibility}**\n\n"
                "Само сохранение уже объединяет дизайн и кнопки."
            ),
            color=EMBED_COLOR
        ),
        view=MessageBuildActionsView(build_id)
    )


class MessageBuildActionsView(discord.ui.View):

    def __init__(self, build_id):
        super().__init__(timeout=900)
        self.build_id = build_id

    @discord.ui.button(
        label="Предпросмотр",
        emoji="👁️",
        style=discord.ButtonStyle.secondary
    )
    async def preview(self, interaction, button):
        row = get_message_build(self.build_id)

        if not row:
            await interaction.response.send_message(
                "Message Build не найден.",
                ephemeral=True
            )
            return

        content = row[4] or None
        embeds_data = safe_json_loads(row[5], [])
        buttons_data = safe_json_loads(row[6], [])

        embeds = [
            build_discord_embed(item)
            for item in embeds_data[:MAX_EMBEDS]
        ]

        await interaction.response.send_message(
            content=content,
            embeds=embeds,
            view=build_button_view(buttons_data),
            ephemeral=True
        )

    @discord.ui.button(
        label="Отправить",
        emoji="📤",
        style=discord.ButtonStyle.success
    )
    async def send(self, interaction, button):
        await interaction.response.send_message(
            "Отправку в выбранный канал подключим поверх Message Build "
            "следующим шагом.",
            ephemeral=True
        )

    @discord.ui.button(
        label="Удалить",
        emoji="🗑️",
        style=discord.ButtonStyle.danger
    )
    async def delete(self, interaction, button):
        if delete_message_build(
            self.build_id,
            interaction.user.id
        ):
            await interaction.response.edit_message(
                content=None,
                embed=discord.Embed(
                    title="MESSAGE BUILD УДАЛЁН",
                    color=EMBED_COLOR
                ),
                view=None
            )
        else:
            await interaction.response.send_message(
                "Удалить этот Message Build может только его создатель.",
                ephemeral=True
            )


# Старая команда /embed удалена — теперь её регистрирует embed_module.py
# (register_embed(bot) ниже). Классы выше (EmbedBuilderState, модалки,
# EmbedDesignView, finish_message_build и тд) оставлены как есть —
# от них по цепочке всё ещё зависит /buttons (StandaloneButtonStartView),
# который мы договорились пока не трогать глубоко.


# ============================================================
# BUTTON BUILDER
# (без изменений в этом проходе)
# ============================================================

class StandaloneButtonStartView(discord.ui.View):

    def __init__(self, guild_id, owner_id):
        super().__init__(timeout=900)
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.state = EmbedBuilderState()

    @discord.ui.button(
        label="Создать набор",
        emoji="➕",
        style=discord.ButtonStyle.success
    )
    async def create(self, interaction, button):
        self.state.standalone_button_set = True
        self.state.name = "Новый набор кнопок"
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="BUTTON BUILDER",
                description="Можно добавить до 5 кнопок.",
                color=EMBED_COLOR
            ),
            view=InlineButtonBuilderView(self.state)
        )

    @discord.ui.button(
        label="Сохранённые наборы",
        emoji="📦",
        style=discord.ButtonStyle.secondary
    )
    async def saved(self, interaction, button):
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="BUTTON SETS",
                description="Выберите сохранённый набор.",
                color=EMBED_COLOR
            ),
            view=ButtonSetListView(
                self.guild_id,
                self.owner_id
            )
        )


class ButtonSetListView(discord.ui.View):

    def __init__(self, guild_id, owner_id):
        super().__init__(timeout=900)
        self.guild_id = guild_id
        self.owner_id = owner_id

        rows = get_button_sets(
            guild_id,
            owner_id,
            include_public=True
        )

        for set_id, creator_id, name, visibility, category, updated_at in rows[:25]:
            button = discord.ui.Button(
                label=f"{name[:70]}",
                style=discord.ButtonStyle.secondary
            )

            async def callback(
                interaction,
                set_id=set_id
            ):
                row = get_button_set(set_id)
                if not row:
                    await interaction.response.send_message(
                        "Набор не найден.",
                        ephemeral=True
                    )
                    return

                buttons = safe_json_loads(row[4], [])

                await interaction.response.send_message(
                    embed=discord.Embed(
                        title=row[2],
                        description=(
                            f"Категория: `{row[6]}`\n"
                            f"Видимость: `{row[5]}`\n"
                            f"Кнопок: `{len(buttons)}`"
                        ),
                        color=EMBED_COLOR
                    ),
                    view=build_button_view(buttons),
                    ephemeral=True
                )

            button.callback = callback
            self.add_item(button)

        if not rows:
            self.add_item(
                discord.ui.Button(
                    label="Сохранённых наборов нет",
                    disabled=True
                )
            )


@bot.tree.command(
    name="buttons",
    description="Создать и управлять наборами кнопок"
)
async def buttons_command(interaction):
    if not await require_command_access(interaction, "buttons"):
        return

    await interaction.response.send_message(
        embed=discord.Embed(
            title="BUTTON BUILDER",
            description=(
                "Отдельный конструктор кнопок.\n\n"
                "Максимум **5 кнопок** в одном наборе.\n"
                "Набор можно будет подключать к Message Build."
            ),
            color=EMBED_COLOR
        ),
        view=StandaloneButtonStartView(
            interaction.guild.id,
            interaction.user.id
        ),
        ephemeral=True
    )


# ============================================================
# РЕГИСТРАЦИЯ МОДУЛЕЙ
# ============================================================

access_module.register_access(bot)
embed_module.register_embed(bot)
register_extended(bot, require_command_access)


# =========================
# START
# =========================

if not TOKEN:
    raise RuntimeError(
        "Токен не найден. Проверь файл .env"
    )

bot.run(TOKEN)
