import json
import logging
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
    delete_button_set,
)

import core
from core import require_command_access, PanelView, Modal, t, panel_embed
import actions
import access_module
import embed_module
import extended_modules
import logo_module
import web_panel
from extended_modules import register_extended

TOKEN = os.getenv("DISCORD_TOKEN")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# Тексты, цвета и thumbnails — в texts.py (core.t / core.panel_embed).
# Рендер кнопок и действия — в actions.py.


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
# STARTUP
# =========================

async def setup_hook():
    # Один раз до подключения к Discord: БД, постоянные компоненты и веб-панель
    # должны быть готовы раньше первого interaction. on_ready может срабатывать
    # повторно при переподключениях — туда это не кладём.
    init_database()
    core.reload_settings()
    core.ensure_default_actions()
    # кнопки/списки в уже отправленных сообщениях работают после рестарта
    bot.add_dynamic_items(*actions.DYNAMIC_ITEMS, *extended_modules.DYNAMIC_ITEMS, *logo_module.DYNAMIC_ITEMS)
    await web_panel.start(bot)

bot.setup_hook = setup_hook


async def on_app_command_error(interaction, error):
    await core.report_error(interaction, error)

bot.tree.on_error = on_app_command_error

_synced = False


@bot.event
async def on_ready():
    global _synced
    print(f"Бот запущен: {bot.user}")
    if _synced:
        return

    try:
        synced = await bot.tree.sync()
        _synced = True
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
        t("ping.reply_latency", ms=round(bot.latency * 1000)), ephemeral=True
    )


# ============================================================
# BUTTON BUILDER (/buttons)
# ============================================================
# Наборы кнопок хранятся в общем формате actions.normalize_button и
# отправляются постоянными кнопками (actions.ActionButton, источник "s").

# старые слова из модалки -> ключ реестра действий
LEGACY_ACTION_WORDS = {"message": "message.send", "confirm": "message.confirm"}
STYLE_WORDS = {"primary": "blue", "secondary": "grey", "success": "green", "danger": "red",
               "blue": "blue", "grey": "grey", "green": "green", "red": "red"}


class ButtonSetState:

    def __init__(self):
        self.name = t("buttons.default_name")
        self.buttons = []
        self.visibility = "private"
        self.category = "general"


def render_button_builder(interaction, state):
    return panel_embed(
        interaction, "buttons.builder",
        max=actions.MAX_BUTTONS,
        name=state.name,
        buttons=embed_module.buttons_text(state),
    )


class InlineButtonModal(Modal, title="НОВАЯ КНОПКА"):

    texts = "buttons.modal"

    label_input = discord.ui.TextInput(label="Текст кнопки", required=True, max_length=80)
    emoji_input = discord.ui.TextInput(label="Emoji", placeholder="Например: ✨ или :my_emoji:", required=False, max_length=100)
    style_input = discord.ui.TextInput(label="Стиль: primary / secondary / success / danger", required=True, max_length=10)
    action_input = discord.ui.TextInput(label="Действие: link / message / confirm", required=True, max_length=40)
    value_input = discord.ui.TextInput(label="URL или текст действия", required=False, style=discord.TextStyle.paragraph, max_length=1000)

    def __init__(self, state, back_target):
        super().__init__()
        self.state = state
        self.back_target = back_target

    async def on_submit(self, interaction):
        if len(self.state.buttons) >= actions.MAX_BUTTONS:
            await interaction.response.send_message(t("buttons.limit", max=actions.MAX_BUTTONS), ephemeral=True)
            return

        style_word = self.style_input.value.strip().lower()
        action_word = self.action_input.value.strip().lower()

        if style_word not in STYLE_WORDS:
            await interaction.response.send_message(t("buttons.modal.bad_style"), ephemeral=True)
            return
        if not actions.valid_emoji(self.emoji_input.value):
            await interaction.response.send_message(t("embed.bad_emoji"), ephemeral=True)
            return

        if action_word == "link":
            style, action_key = "link", None
        else:
            style = STYLE_WORDS[style_word]
            action_key = LEGACY_ACTION_WORDS.get(action_word, action_word)
            # то же правило, что и в /embed: только действия, разрешённые уровню создателя
            if not core.is_action_allowed(core.get_user_level(interaction), action_key):
                await interaction.response.send_message(t("buttons.modal.bad_action"), ephemeral=True)
                return

        value, error = actions.validate_action_value(interaction, action_key, self.value_input.value, style=style)
        if error:
            await interaction.response.send_message(t(error), ephemeral=True)
            return

        self.state.buttons.append({
            "label": self.label_input.value,
            "emoji": self.emoji_input.value.strip() or None,
            "style": style,
            "action_key": action_key,
            "value": value,
        })

        await interaction.response.edit_message(
            embed=render_button_builder(interaction, self.state),
            view=InlineButtonBuilderView(self.state, back_target=self.back_target)
        )


class ButtonSetNameModal(Modal, title="НАЗВАНИЕ НАБОРА"):
    texts = "buttons.name_modal"

    name_input = discord.ui.TextInput(label="Название", max_length=80)
    visibility_input = discord.ui.TextInput(label="private / public", max_length=7, default="private")

    def __init__(self, state, back_target):
        super().__init__()
        self.state = state
        self.back_target = back_target
        self.name_input.default = state.name
        self.visibility_input.default = state.visibility

    async def on_submit(self, interaction):
        visibility = self.visibility_input.value.strip().lower()
        if visibility not in ("private", "public"):
            await interaction.response.send_message(t("forms.basic_modal.bad_visibility"), ephemeral=True)
            return
        self.state.name = self.name_input.value.strip() or t("buttons.default_name")
        self.state.visibility = visibility
        await interaction.response.edit_message(
            embed=render_button_builder(interaction, self.state),
            view=InlineButtonBuilderView(self.state, back_target=self.back_target)
        )


class InlineButtonBuilderView(PanelView):

    texts = "buttons.builder"

    def __init__(self, state, back_target=None):
        super().__init__(back_target=back_target)
        self.state = state

    @discord.ui.button(label="Добавить кнопку", emoji="➕", style=discord.ButtonStyle.success)
    async def add(self, interaction, button):
        if len(self.state.buttons) >= actions.MAX_BUTTONS:
            await interaction.response.send_message(t("buttons.limit", max=actions.MAX_BUTTONS), ephemeral=True)
            return
        await interaction.response.send_modal(InlineButtonModal(self.state, self.back_target))

    @discord.ui.button(label="Название", emoji="🏷️", style=discord.ButtonStyle.secondary)
    async def rename(self, interaction, button):
        await interaction.response.send_modal(ButtonSetNameModal(self.state, self.back_target))

    @discord.ui.button(label="Готово", emoji="✅", style=discord.ButtonStyle.primary)
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
        core.audit(interaction, "button_set.created", "button_set", set_id, self.state.name)
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "buttons.saved_set", id=set_id, count=len(self.state.buttons)),
            view=ButtonSetActions(set_id),
        )

    @discord.ui.button(label="Очистить", emoji="🧹", style=discord.ButtonStyle.secondary)
    async def clear(self, interaction, button):
        self.state.buttons = []
        await interaction.response.edit_message(
            embed=render_button_builder(interaction, self.state),
            view=InlineButtonBuilderView(self.state, back_target=self.back_target)
        )


def button_set_visible(interaction, row):
    return actions.can_view(interaction, row[2], row[5])


class ButtonSetActions(PanelView):
    texts = "buttons.actions"

    def __init__(self, set_id, back_target=None):
        super().__init__(back_target=back_target)
        self.set_id = set_id

    async def _row(self, interaction, manage=False):
        row = get_button_set(self.set_id)
        if not row or row[1] != interaction.guild.id or not button_set_visible(interaction, row):
            await interaction.response.send_message(t("buttons.not_found"), ephemeral=True)
            return None
        if manage and not extended_modules.owns_or_admin(interaction, row[2]):
            await interaction.response.send_message(t("forms.manage_denied"), ephemeral=True)
            return None
        return row

    @discord.ui.button(label="Предпросмотр", emoji="👁️", style=discord.ButtonStyle.secondary)
    async def preview(self, interaction, button):
        row = await self._row(interaction)
        if not row:
            return
        _, _, view = actions.render_source("s", self.set_id)
        await interaction.response.send_message(
            embed=panel_embed(interaction, "buttons.card", title=row[3], category=row[6], visibility=row[5],
                              count=len(json.loads(row[4] or "[]"))),
            view=view, ephemeral=True,
        )

    @discord.ui.button(label="Опубликовать", emoji="📣", style=discord.ButtonStyle.success)
    async def publish(self, interaction, button):
        row = await self._row(interaction)
        if not row:
            return
        view = PanelView(timeout=300)
        select = discord.ui.ChannelSelect(
            placeholder=t("embed.send.placeholder")[:150],
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
        )

        async def picked(ci):
            channel = ci.guild.get_channel(select.values[0].id)
            if channel is None or not core.can_post_in(ci.user, channel):
                await ci.response.send_message(t("embed.send.no_perm_user", channel=f"<#{select.values[0].id}>"), ephemeral=True)
                return
            if not core.bot_can_post(channel):
                await ci.response.send_message(t("embed.send.no_perm_bot", channel=channel.mention), ephemeral=True)
                return
            _, _, components = actions.render_source("s", self.set_id)
            await channel.send(embed=panel_embed(ci, "buttons.public_card", title=row[3]), view=components)
            core.audit(ci, "button_set.published", "button_set", self.set_id, f"channel={channel.id}")
            await ci.response.send_message(t("forms.published", channel=channel.mention), ephemeral=True)

        select.callback = picked
        view.add_item(select)
        await interaction.response.send_message(t("embed.send.prompt"), view=view, ephemeral=True)

    @discord.ui.button(label="Удалить", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete(self, interaction, button):
        row = await self._row(interaction, manage=True)
        if not row:
            return
        delete_button_set(self.set_id)
        core.audit(interaction, "button_set.deleted", "button_set", self.set_id, row[3])
        await interaction.response.edit_message(embed=panel_embed(interaction, "buttons.deleted"), view=None)


class StandaloneButtonStartView(PanelView):

    texts = "buttons.home"

    def __init__(self, guild_id, owner_id):
        super().__init__()
        self.guild_id = guild_id
        self.owner_id = owner_id

    @discord.ui.button(label="Создать набор", emoji="➕", style=discord.ButtonStyle.success)
    async def create(self, interaction, button):
        state = ButtonSetState()
        await interaction.response.edit_message(
            embed=render_button_builder(interaction, state),
            view=InlineButtonBuilderView(state, back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="Сохранённые наборы", emoji="📦", style=discord.ButtonStyle.secondary)
    async def saved(self, interaction, button):
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "buttons.list"),
            view=ButtonSetListView(interaction, back_target=(interaction.message.embeds[0], self))
        )


class ButtonSetListView(PanelView):

    def __init__(self, interaction, back_target=None):
        super().__init__(back_target=back_target)
        rows = [
            row for row in get_button_sets(interaction.guild.id)
            if button_set_visible(interaction, get_button_set(row[0]))
        ]

        for set_id, creator_id, name, visibility, category, updated_at in rows[:20]:
            button = discord.ui.Button(label=f"{(name or '')[:60]} · #{set_id}", style=discord.ButtonStyle.secondary)

            async def callback(interaction, set_id=set_id):
                row = get_button_set(set_id)
                if not row or not button_set_visible(interaction, row):
                    await interaction.response.send_message(t("buttons.not_found"), ephemeral=True)
                    return
                await interaction.response.edit_message(
                    embed=panel_embed(interaction, "buttons.card", title=row[3], category=row[6],
                                      visibility=row[5], count=len(json.loads(row[4] or "[]"))),
                    view=ButtonSetActions(set_id, back_target=(interaction.message.embeds[0], self)),
                )

            button.callback = callback
            self.add_item(button)

        if not rows:
            self.add_item(discord.ui.Button(label=t("buttons.list.empty")[:80], disabled=True))


@bot.tree.command(
    name="buttons",
    description="Создать и управлять наборами кнопок"
)
@discord.app_commands.guild_only()
async def buttons_command(interaction):
    if not await require_command_access(interaction, "buttons"):
        return

    await interaction.response.send_message(
        embed=panel_embed(interaction, "buttons.home", max=actions.MAX_BUTTONS),
        view=StandaloneButtonStartView(interaction.guild.id, interaction.user.id),
        ephemeral=True
    )


# ============================================================
# РЕГИСТРАЦИЯ МОДУЛЕЙ
# ============================================================

access_module.register_access(bot)
embed_module.register_embed(bot)
register_extended(bot, require_command_access)
logo_module.register_logo(bot)
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
