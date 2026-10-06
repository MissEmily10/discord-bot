import json
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

from database import (
    init_database,
    save_button_set,
    get_button_set,
    get_button_sets,
)

import core
from core import require_command_access, PanelView, Modal, t, panel_embed
import access_module
import embed_module
import web_panel
from extended_modules import register_extended

TOKEN = os.getenv("DISCORD_TOKEN")

# =========================
# CONSTANTS
# =========================
# Тексты, цвета и thumbnails — в texts.py (core.t / core.panel_embed).

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

# Бот работает только на slash-командах. Префикс-команд нет, поэтому
# message content intent не нужен; when_mentioned вместо "!" убирает
# предупреждение "Privileged message content intent is missing".
bot = commands.Bot(
    command_prefix=commands.when_mentioned,
    intents=intents
)


# =========================
# HELPERS
# =========================

def safe_json_loads(value, fallback):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def normalize_url(url):
    url = (url or "").strip()
    if not url:
        return None

    if not url.startswith(("http://", "https://")):
        return "https://" + url

    return url


def build_button_view(buttons):
    """Кнопки наборов /buttons (старый формат: style primary/..., action link/message/confirm)."""
    if not buttons:
        return None

    view = discord.ui.View(timeout=None)

    for item in buttons[:MAX_BUTTONS]:
        label = str(item.get("label") or t("common.default_button_label"))[:80]
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
# STARTUP
# =========================

async def setup_hook():
    # Один раз до подключения к Discord: БД и веб-панель должны быть готовы
    # раньше первого interaction. on_ready может срабатывать повторно при
    # переподключениях — туда это не кладём.
    init_database()
    core.reload_settings()
    core.ensure_default_actions()
    await web_panel.start(bot)

bot.setup_hook = setup_hook


@bot.event
async def on_ready():
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

    await interaction.response.send_message(t("ping.reply"), ephemeral=True)


# ============================================================
# BUTTON BUILDER (/buttons)
# ============================================================

class ButtonSetState:

    def __init__(self):
        self.name = t("buttons.default_name")
        self.buttons = []
        self.visibility = "private"
        self.category = "general"


def render_button_builder(interaction, state):
    lines = [
        t(
            "buttons.builder.line",
            n=index,
            emoji=item.get("emoji") or "",
            label=item.get("label") or t("common.default_button_label"),
            action=item.get("action", "action"),
        )
        for index, item in enumerate(state.buttons, start=1)
    ]
    return panel_embed(
        interaction, "buttons.builder",
        max=MAX_BUTTONS,
        buttons="\n".join(lines) or t("buttons.builder.empty"),
    )


class InlineButtonModal(Modal, title="НОВАЯ КНОПКА"):

    texts = "buttons.modal"

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

    def __init__(self, state, back_target):
        super().__init__()
        self.state = state
        self.back_target = back_target

    async def on_submit(self, interaction):
        if len(self.state.buttons) >= MAX_BUTTONS:
            await interaction.response.send_message(t("buttons.limit", max=MAX_BUTTONS), ephemeral=True)
            return

        style = self.style_input.value.strip().lower()
        action = self.action_input.value.strip().lower()

        if style not in {"primary", "secondary", "success", "danger"}:
            await interaction.response.send_message(t("buttons.modal.bad_style"), ephemeral=True)
            return

        if action not in {"link", "message", "confirm"}:
            await interaction.response.send_message(t("buttons.modal.bad_action"), ephemeral=True)
            return

        if action == "link" and not normalize_url(self.value_input.value):
            await interaction.response.send_message(t("buttons.modal.need_url"), ephemeral=True)
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
            embed=render_button_builder(interaction, self.state),
            view=InlineButtonBuilderView(self.state, back_target=self.back_target)
        )


class InlineButtonBuilderView(PanelView):

    texts = "buttons.builder"

    def __init__(self, state, back_target=None):
        super().__init__(back_target=back_target)
        self.state = state

    @discord.ui.button(
        label="Добавить кнопку",
        emoji="➕",
        style=discord.ButtonStyle.success
    )
    async def add(self, interaction, button):
        if len(self.state.buttons) >= MAX_BUTTONS:
            await interaction.response.send_message(t("buttons.limit", max=MAX_BUTTONS), ephemeral=True)
            return

        await interaction.response.send_modal(
            InlineButtonModal(self.state, self.back_target)
        )

    @discord.ui.button(
        label="Готово",
        emoji="✅",
        style=discord.ButtonStyle.primary
    )
    async def done(self, interaction, button):
        if not self.state.buttons:
            await interaction.response.send_message(t("buttons.builder.need_button"), ephemeral=True)
            return

        set_id = save_button_set(
            guild_id=interaction.guild.id,
            owner_id=interaction.user.id,
            name=self.state.name,
            buttons_json=json.dumps(self.state.buttons, ensure_ascii=False),
            visibility=self.state.visibility,
            category=self.state.category,
        )
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "buttons.saved_set", id=set_id, count=len(self.state.buttons)),
            view=ButtonSetListView(
                interaction.guild.id,
                interaction.user.id,
            ),
        )

    @discord.ui.button(
        label="Очистить",
        emoji="🧹",
        style=discord.ButtonStyle.secondary
    )
    async def clear(self, interaction, button):
        self.state.buttons = []
        await interaction.response.edit_message(
            embed=render_button_builder(interaction, self.state),
            view=InlineButtonBuilderView(self.state, back_target=self.back_target)
        )


class StandaloneButtonStartView(PanelView):

    texts = "buttons.home"

    def __init__(self, guild_id, owner_id):
        super().__init__()
        self.guild_id = guild_id
        self.owner_id = owner_id

    @discord.ui.button(
        label="Создать набор",
        emoji="➕",
        style=discord.ButtonStyle.success
    )
    async def create(self, interaction, button):
        state = ButtonSetState()
        await interaction.response.edit_message(
            embed=render_button_builder(interaction, state),
            view=InlineButtonBuilderView(state, back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(
        label="Сохранённые наборы",
        emoji="📦",
        style=discord.ButtonStyle.secondary
    )
    async def saved(self, interaction, button):
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "buttons.list"),
            view=ButtonSetListView(
                self.guild_id,
                self.owner_id,
                back_target=(interaction.message.embeds[0], self),
            )
        )


class ButtonSetListView(PanelView):

    def __init__(self, guild_id, owner_id, back_target=None):
        super().__init__(back_target=back_target)
        self.guild_id = guild_id
        self.owner_id = owner_id

        rows = get_button_sets(
            guild_id,
            owner_id,
            include_public=True
        )

        for set_id, creator_id, name, visibility, category, updated_at in rows[:20]:
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
                    await interaction.response.send_message(t("buttons.not_found"), ephemeral=True)
                    return

                buttons = safe_json_loads(row[4], [])

                await interaction.response.send_message(
                    embed=panel_embed(
                        interaction, "buttons.card", title=row[3],
                        category=row[6], visibility=row[5], count=len(buttons),
                    ),
                    view=build_button_view(buttons),
                    ephemeral=True
                )

            button.callback = callback
            self.add_item(button)

        if not rows:
            self.add_item(
                discord.ui.Button(
                    label=t("buttons.list.empty")[:80],
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
        embed=panel_embed(interaction, "buttons.home", max=MAX_BUTTONS),
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
web_panel.register_panel(bot)


# =========================
# START
# =========================

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError(
            "Токен не найден. Проверь файл .env"
        )

    bot.run(TOKEN)
