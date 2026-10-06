"""
embed_module.py
================
/embed — переработанный Message Build / Embed Builder.

Ключевые изменения относительно старой версии:
- Интро-экран перед билдером: создать новый / сохранённые / шаблон.
- Видимость спрашивается сразу: публичный / приватный / ограниченный
  (по уровню доступа ИЛИ по конкретным ролям).
- Multi-embed теперь через select-меню "Embed 1/2/3.../+ Добавить" —
  можно редактировать любой embed, не только первый.
- Библиотека изображений — пока просто помеченная заглушка (по договорённости).
- Интерактив после дизайна — один select (форма/список/выбор/кнопки) +
  отдельные кнопки Назад/Далее под ним, не внутри select.
- Кнопки теперь создаются через action_registry — какие действия видит
  создатель, зависит от его уровня доступа.
- Message Build — "живой объект": при отправке каждое сообщение
  записывается в sent_instances (build_id/message_id/channel_id),
  это основа под живое редактирование в будущем.
- "Отправить" реально работает: ChannelSelect -> шлёт все embeds
  отдельными сообщениями одним пакетом, кнопки — одним набором в конце.
- Добавлена "Сохранить как шаблон".
"""

import json
import time
from datetime import datetime

import discord

import core
from core import (
    PanelView, Modal, t, panel_embed,
    get_user_level, actions_for_level,
)
from database import (
    save_message_build, get_message_build, get_message_builds,
    delete_message_build, save_sent_instance, save_template,
)

MAX_EMBEDS = 10
MAX_BUTTONS = 5

# Подписи стилей — в каталоге: embed.button_color.<ключ>
BUTTON_STYLES = {
    "blue": discord.ButtonStyle.primary,
    "grey": discord.ButtonStyle.secondary,
    "green": discord.ButtonStyle.success,
    "red": discord.ButtonStyle.danger,
    "link": discord.ButtonStyle.link,
}


def say(interaction, key, **params):
    return interaction.response.send_message(t(key, **params), ephemeral=True)


def normalize_url(url):
    url = (url or "").strip()
    if not url:
        return None
    return url if url.startswith(("http://", "https://")) else "https://" + url


def default_embed_data():
    return {"title": "", "description": "", "color": 0x5865F2, "fields": []}


def build_discord_embed(data):
    embed = discord.Embed(
        title=data.get("title") or None,
        description=data.get("description") or "\u200b",
        url=normalize_url(data.get("url")),
        color=int(data.get("color", 0x5865F2))
    )
    author_name = data.get("author_name")
    if author_name:
        embed.set_author(name=author_name, url=normalize_url(data.get("author_url")), icon_url=normalize_url(data.get("author_icon")))
    thumbnail = normalize_url(data.get("thumbnail"))
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    image = normalize_url(data.get("image"))
    if image:
        embed.set_image(url=image)
    footer_text = data.get("footer_text")
    if footer_text:
        embed.set_footer(text=footer_text, icon_url=normalize_url(data.get("footer_icon")))
    if data.get("timestamp"):
        embed.timestamp = datetime.now()
    for field in data.get("fields", []):
        embed.add_field(
            name=str(field.get("name", "‎"))[:256],
            value=str(field.get("value", "‎"))[:1024],
            inline=bool(field.get("inline", False)),
        )
    return embed


def build_custom_list_view(options):
    """Реальный discord.ui.Select из твоих опций списка, с действием на каждую."""
    if not options:
        return None
    view = discord.ui.View(timeout=None)
    default_label = t("embed.list.default_option")
    select = discord.ui.Select(
        placeholder=t("embed.list.placeholder")[:150],
        options=[
            discord.SelectOption(
                label=str(o.get("label") or default_label)[:100],
                description=(o.get("description") or None),
                emoji=o.get("emoji") or None,
            )
            for o in options[:25]
        ],
    )
    by_label = {str(o.get("label") or default_label)[:100]: o for o in options}

    async def callback(interaction):
        chosen = by_label.get(select.values[0])
        if not chosen:
            await say(interaction, "embed.list.option_missing")
            return
        action_key = chosen.get("action_key")
        value = chosen.get("value")
        await dispatch_action(interaction, action_key, value)

    select.callback = callback
    view.add_item(select)
    return view


NATIVE_SELECT_CLASSES = {
    "role": discord.ui.RoleSelect,
    "user": discord.ui.UserSelect,
    "channel": discord.ui.ChannelSelect,
    "mentionable": discord.ui.MentionableSelect,
}


class MessageEditModal(Modal, title="РЕДАКТИРОВАТЬ СООБЩЕНИЕ"):
    texts = "actions.edit_modal"

    content_input = discord.ui.TextInput(
        label="Новый текст сообщения",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=4000,
    )

    def __init__(self, target_message):
        super().__init__()
        self.target_message = target_message
        self.content_input.default = target_message.content[:4000]

    async def on_submit(self, interaction):
        content = self.content_input.value.strip() or None
        embeds = list(self.target_message.embeds)
        if content is None and not embeds:
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

        await say(interaction, "actions.edit_modal.done")


class WebhookSendModal(Modal, title="ОТПРАВИТЬ ЧЕРЕЗ WEBHOOK"):
    texts = "actions.webhook_modal"

    url_input = discord.ui.TextInput(label="Webhook URL", max_length=1000)
    content_input = discord.ui.TextInput(
        label="Текст сообщения",
        style=discord.TextStyle.paragraph,
        max_length=2000,
    )

    def __init__(self, default_url=""):
        super().__init__()
        self.url_input.default = default_url

    async def on_submit(self, interaction):
        webhook_url = normalize_url(self.url_input.value)
        if not webhook_url:
            await say(interaction, "actions.webhook_modal.no_url")
            return
        try:
            webhook = discord.Webhook.from_url(webhook_url, client=interaction.client)
            await webhook.send(self.content_input.value, username=interaction.client.user.name)
        except (discord.HTTPException, ValueError):
            await say(interaction, "actions.webhook_modal.failed")
            return
        await say(interaction, "actions.webhook_modal.sent")


async def open_message_edit_modal(interaction):
    if not core.is_action_allowed(core.get_user_level(interaction), "message.edit"):
        await say(interaction, "actions.edit_denied")
        return
    await interaction.response.send_modal(MessageEditModal(interaction.message))


class DangerousActionView(PanelView):
    texts = "actions.dangerous"

    def __init__(self, action_key, value, target_message=None):
        super().__init__(timeout=300)
        self.action_key = action_key
        self.value = value
        self.target_message = target_message

    @discord.ui.button(label="Подтвердить", emoji="⚠️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await dispatch_action(
            interaction,
            self.action_key,
            self.value,
            skip_confirmation=True,
            target_message=self.target_message,
        )

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content=t("actions.cancelled"), view=None)


async def dispatch_action(interaction, action_key, value, skip_confirmation=False, target_message=None):
    """Единая маршрутизация действий кнопок и пользовательских списков."""
    if not action_key:
        await say(interaction, "actions.not_configured")
        return
    if not core.is_action_allowed(core.get_user_level(interaction), action_key):
        await say(interaction, "actions.no_access")
        return

    action_row = core.get_action(action_key)
    if action_row and action_row[2] and not skip_confirmation:
        await interaction.response.send_message(
            t("actions.dangerous.text", action=action_key),
            view=DangerousActionView(action_key, value, target_message=interaction.message),
            ephemeral=True,
        )
        return

    if action_key == "message.send":
        await interaction.response.send_message(value or t("actions.default.message"), ephemeral=True)
    elif action_key == "message.confirm":
        await interaction.response.send_message(value or t("actions.default.confirm"), ephemeral=True)
    elif action_key == "message.edit":
        if target_message is None:
            target_message = interaction.message
        if target_message is None:
            await say(interaction, "actions.edit_no_message")
            return
        await interaction.response.send_modal(MessageEditModal(target_message))
    elif action_key == "form.trigger":
        from database import get_form
        from extended_modules import SubmissionModal

        try:
            form_id = int((value or "").strip())
        except ValueError:
            await say(interaction, "actions.form_trigger.bad_value")
            return
        row = get_form(form_id)
        if not row:
            await say(interaction, "forms.not_found")
            return
        await interaction.response.send_modal(SubmissionModal(row))
    elif action_key == "select.trigger":
        kind = (value or "role").strip().lower()
        if kind not in NATIVE_SELECT_CLASSES:
            await say(interaction, "actions.select_trigger.bad_value")
            return
        await interaction.response.send_message(
            t("actions.select_trigger.prompt"),
            view=build_native_select_view(kind),
            ephemeral=True,
        )
    elif action_key in {"role.assign", "role.remove"}:
        try:
            role_id = int((value or "").strip())
        except ValueError:
            await say(interaction, "actions.role.bad_value")
            return
        if interaction.guild is None or not interaction.guild.me.guild_permissions.manage_roles:
            await say(interaction, "actions.role.no_manage_roles")
            return
        role = interaction.guild.get_role(role_id)
        member = interaction.guild.get_member(interaction.user.id)
        bot_member = interaction.guild.me
        if not role or not member or role >= bot_member.top_role:
            await say(interaction, "actions.role.unreachable")
            return
        try:
            if action_key == "role.assign":
                await member.add_roles(role, reason="Message action role.assign")
                result = t("actions.role.assigned")
            else:
                await member.remove_roles(role, reason="Message action role.remove")
                result = t("actions.role.removed")
        except discord.Forbidden:
            await say(interaction, "actions.role.forbidden")
            return
        await interaction.response.send_message(result, ephemeral=True)
    elif action_key == "webhook.send":
        await interaction.response.send_modal(WebhookSendModal(value or ""))
    elif action_key == "build.trigger":
        try:
            build_id = int((value or "").strip())
        except ValueError:
            await say(interaction, "actions.build_trigger.bad_value")
            return
        row = get_message_build(build_id)
        if not row:
            await say(interaction, "embed.build_not_found")
            return
        embeds_data = json.loads(row[5])
        buttons_data = json.loads(row[6])
        interactive_data = json.loads(row[11]) if row[11] else None
        content = row[4] or None
        embeds = [build_discord_embed(item) for item in embeds_data[:MAX_EMBEDS]]
        view = build_final_view(buttons_data, interactive_data)
        if not content and not embeds and view is None:
            await say(interaction, "embed.build_empty")
            return
        await interaction.response.send_message(content=content, embeds=embeds, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(t("actions.wip", action=action_key), ephemeral=True)


def build_native_select_view(kind):
    """Discord-нативный select (роль/участник/канал/mentionable)."""
    view = discord.ui.View(timeout=None)
    select_cls = NATIVE_SELECT_CLASSES.get(kind, discord.ui.RoleSelect)
    select = select_cls(placeholder=t("embed.native_select.placeholder")[:150])

    async def callback(interaction):
        chosen = select.values[0]
        label = getattr(chosen, "mention", str(chosen))
        await say(interaction, "select.picked", value=label)

    select.callback = callback
    view.add_item(select)
    return view


def build_button_view(buttons):
    """Рендер кнопок из сохранённых данных для превью и отправки."""
    if not buttons:
        return None

    view = discord.ui.View(timeout=None)
    for item in buttons[:MAX_BUTTONS]:
        label = str(item.get("label") or t("common.default_button_label"))[:80]
        emoji = item.get("emoji") or None
        style_key = item.get("style", "blue")
        style = BUTTON_STYLES.get(style_key, discord.ButtonStyle.primary)
        action_key = item.get("action_key")
        value = item.get("value")

        if style_key == "link":
            url = normalize_url(value)
            if not url:
                continue
            view.add_item(discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.link, url=url))
            continue

        button = discord.ui.Button(label=label, emoji=emoji, style=style)

        async def callback(interaction, action_key=action_key, value=value):
            await dispatch_action(interaction, action_key, value)

        button.callback = callback
        view.add_item(button)

    return view


def build_final_view(buttons_data, interactive_data):
    """
    Собирает реальный компонент для финального сообщения:
    кнопки, ИЛИ пользовательский список, ИЛИ нативный select —
    ровно то, что выбрано в InteractiveMenuView.
    """
    if interactive_data:
        itype = interactive_data.get("type")
        if itype == "list":
            return build_custom_list_view(interactive_data.get("options", []))
        if itype == "native_select":
            return build_native_select_view(interactive_data.get("kind"))
    return build_button_view(buttons_data)


# ============================================================
# STATE
# ============================================================

class EmbedState:
    def __init__(self, guild_id, owner_id):
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.name = t("embed.default_name")
        self.content = ""
        self.embeds = [default_embed_data()]
        self.active_index = 0
        self.buttons = []
        self.interactive_type = None
        self.visibility = "private"
        self.visibility_roles = []
        self.visibility_levels = []
        self.category = "general"

    @property
    def active_embed(self):
        return self.embeds[self.active_index]


def render_active_preview(state):
    embed = build_discord_embed(state.active_embed)
    embed.set_footer(text=t("embed.preview_footer", n=state.active_index + 1, total=len(state.embeds), visibility=state.visibility))
    return embed


# ============================================================
# 1. ИНТРО
# ============================================================

class EmbedHomeView(PanelView):
    texts = "embed.home"


    def __init__(self, guild_id, owner_id):
        super().__init__(back_target=None)
        self.guild_id = guild_id
        self.owner_id = owner_id

    @discord.ui.button(label="Создать новый", emoji="➕", style=discord.ButtonStyle.success)
    async def create(self, interaction, button):
        state = EmbedState(self.guild_id, self.owner_id)
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "embed.visibility"),
            view=VisibilityView(state, back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="Мои сохранённые", emoji="📦", style=discord.ButtonStyle.secondary)
    async def saved(self, interaction, button):
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "embed.saved"),
            view=SavedBuildsListView(self.guild_id, self.owner_id, back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="Использовать шаблон", emoji="📁", style=discord.ButtonStyle.secondary)
    async def use_template(self, interaction, button):
        from database import get_templates
        rows = get_templates(self.guild_id, self.owner_id, template_type="message", include_public=True)
        view = PanelView(back_target=(interaction.message.embeds[0], self))
        for tid, owner, name, typ, payload, vis, cat, roles, logo, fav, updated in rows[:20]:
            btn = discord.ui.Button(label=name[:70], style=discord.ButtonStyle.secondary)

            async def cb(i, tid=tid):
                from database import get_template
                row = get_template(tid)
                payload = json.loads(row[4] or "{}")
                state = EmbedState(self.guild_id, self.owner_id)
                state.embeds = payload.get("embeds") or [default_embed_data()]
                state.buttons = payload.get("buttons", [])
                await i.response.edit_message(embed=render_active_preview(state), view=EmbedEditorView(state, back_target=(i.message.embeds[0], self)))

            btn.callback = cb
            view.add_item(btn)
        if not rows:
            view.add_item(discord.ui.Button(label=t("embed.templates.empty")[:80], disabled=True))
        await interaction.response.edit_message(embed=panel_embed(interaction, "embed.templates"), view=view)


class SavedBuildsListView(PanelView):
    def __init__(self, guild_id, owner_id, back_target):
        super().__init__(back_target=back_target)
        rows = get_message_builds(guild_id, owner_id, include_public=True)
        for bid, owner, name, vis, cat, updated in rows[:20]:
            btn = discord.ui.Button(label=name[:70], style=discord.ButtonStyle.secondary)

            async def cb(interaction, bid=bid):
                row = get_message_build(bid)
                if not row:
                    await say(interaction, "messages.not_found")
                    return
                await interaction.response.edit_message(
                    embed=panel_embed(interaction, "embed.build_card", title=row[3], visibility=row[7]),
                    view=MessageBuildFinalView(bid, back_target=(interaction.message.embeds[0], self))
                )

            btn.callback = cb
            self.add_item(btn)
        if not rows:
            self.add_item(discord.ui.Button(label=t("embed.saved.empty")[:80], disabled=True))


# ============================================================
# 2. ВИДИМОСТЬ
# ============================================================

class VisibilityView(PanelView):
    texts = "embed.visibility"

    def __init__(self, state, back_target):
        super().__init__(back_target=back_target)
        self.state = state

    @discord.ui.button(label="Публичный", emoji="🌐", style=discord.ButtonStyle.success)
    async def public(self, interaction, button):
        self.state.visibility = "public"
        await go_to_editor(interaction, self.state, back_target=(interaction.message.embeds[0], self))

    @discord.ui.button(label="Приватный", emoji="🔒", style=discord.ButtonStyle.secondary)
    async def private(self, interaction, button):
        self.state.visibility = "private"
        await go_to_editor(interaction, self.state, back_target=(interaction.message.embeds[0], self))

    @discord.ui.button(label="Ограниченный", emoji="🎭", style=discord.ButtonStyle.primary)
    async def restricted(self, interaction, button):
        self.state.visibility = "restricted"
        await interaction.response.defer()
        await interaction.edit_original_response(
            embed=panel_embed(interaction, "embed.restriction"),
            view=RestrictionModeView(self.state, back_target=(interaction.message.embeds[0], self))
        )


class RestrictionModeView(PanelView):
    texts = "embed.restriction"

    def __init__(self, state, back_target):
        super().__init__(back_target=back_target)
        self.state = state

    @discord.ui.button(label="По уровню доступа", emoji="🛡️", style=discord.ButtonStyle.primary)
    async def by_level(self, interaction, button):
        await interaction.response.defer()
        await interaction.edit_original_response(
            embed=panel_embed(interaction, "embed.restriction_level"),
            view=LevelRestrictionView(self.state, back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="По роли(ям)", emoji="🎭", style=discord.ButtonStyle.secondary)
    async def by_role(self, interaction, button):
        view = PanelView(back_target=(interaction.message.embeds[0], self))
        select = discord.ui.RoleSelect(placeholder=t("embed.restriction_roles.placeholder")[:150], min_values=1, max_values=5)

        async def selected(i):
            self.state.visibility_roles = [r.id for r in select.values]
            await go_to_editor(i, self.state, back_target=(i.message.embeds[0], view))

        select.callback = selected
        view.add_item(select)
        await interaction.response.defer()
        await interaction.edit_original_response(embed=panel_embed(interaction, "embed.restriction_roles"), view=view)


class LevelRestrictionView(PanelView):
    texts = "embed.restriction_level"

    def __init__(self, state, back_target):
        super().__init__(back_target=back_target)
        self.state = state

    async def pick(self, interaction, level):
        self.state.visibility_levels = [level]
        await go_to_editor(interaction, self.state, back_target=(interaction.message.embeds[0], self))

    @discord.ui.button(label="Staff", style=discord.ButtonStyle.primary)
    async def staff(self, interaction, button):
        await self.pick(interaction, "staff")

    @discord.ui.button(label="Admin", style=discord.ButtonStyle.success)
    async def admin(self, interaction, button):
        await self.pick(interaction, "admin")


async def go_to_editor(interaction, state, back_target):
    await interaction.response.defer()
    await interaction.edit_original_response(
        embed=render_active_preview(state),
        view=EmbedEditorView(state, back_target=back_target)
    )


# ============================================================
# 3. РЕДАКТОР (multi-embed через select)
# ============================================================

class EmbedSwitchSelect(discord.ui.Select):
    def __init__(self, state):
        self.state = state
        options = [
            discord.SelectOption(label=t("embed.switch.option", n=i + 1)[:100], value=str(i), default=(i == state.active_index))
            for i in range(len(state.embeds))
        ]
        if len(state.embeds) < MAX_EMBEDS:
            options.append(discord.SelectOption(label=t("embed.switch.add")[:100], value="__add__"))
        super().__init__(placeholder=t("embed.switch.placeholder")[:150], options=options, row=0)

    async def callback(self, interaction):
        if self.values[0] == "__add__":
            self.state.embeds.append(default_embed_data())
            self.state.active_index = len(self.state.embeds) - 1
        else:
            self.state.active_index = int(self.values[0])
        await interaction.response.edit_message(
            embed=render_active_preview(self.state),
            view=EmbedEditorView(self.state, back_target=self.view.back_target)
        )


class EmbedEditorView(PanelView):
    texts = "embed.editor"

    def __init__(self, state, back_target):
        super().__init__(back_target=back_target)
        self.state = state
        self.add_item(EmbedSwitchSelect(state))

    @discord.ui.button(label="Основное", emoji="✏️", style=discord.ButtonStyle.primary, row=1)
    async def basic(self, interaction, button):
        await interaction.response.send_modal(EmbedBasicModal(self.state, self))

    @discord.ui.button(label="Изображения", emoji="🖼️", style=discord.ButtonStyle.secondary, row=1)
    async def media(self, interaction, button):
        await interaction.response.send_modal(EmbedMediaModal(self.state, self))

    @discord.ui.button(label="Author/Footer", emoji="🏷️", style=discord.ButtonStyle.secondary, row=1)
    async def author_footer(self, interaction, button):
        await interaction.response.send_modal(EmbedAuthorFooterModal(self.state, self))

    @discord.ui.button(label="Добавить поле", emoji="➕", style=discord.ButtonStyle.secondary, row=1)
    async def add_field(self, interaction, button):
        if len(self.state.active_embed.get("fields", [])) >= 25:
            await say(interaction, "embed.editor.too_many_fields")
            return
        await interaction.response.send_modal(EmbedFieldModal(self.state, self))

    @discord.ui.button(label="Библиотека изображений", emoji="🖼️", style=discord.ButtonStyle.secondary, row=2, disabled=True)
    async def image_library(self, interaction, button):
        await say(interaction, "embed.editor.library_wip")

    @discord.ui.button(label="Подтвердить дизайн", emoji="✅", style=discord.ButtonStyle.success, row=2)
    async def confirm(self, interaction, button):
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "embed.interactive"),
            view=InteractiveMenuView(self.state, back_target=(interaction.message.embeds[0], self))
        )


class EmbedBasicModal(Modal, title="ОСНОВНОЕ"):
    texts = "embed.basic_modal"

    title_input = discord.ui.TextInput(label="Title", required=False, max_length=256)
    description_input = discord.ui.TextInput(label="Description", required=False, style=discord.TextStyle.paragraph, max_length=4000)
    color_input = discord.ui.TextInput(label="Цвет HEX", required=False, max_length=7, placeholder="#5865F2")

    def __init__(self, state, editor_view):
        super().__init__()
        self.state = state
        self.editor_view = editor_view
        data = state.active_embed
        self.title_input.default = data.get("title", "")
        self.description_input.default = data.get("description", "")
        self.color_input.default = f"#{data.get('color', 0x5865F2):06X}"

    async def on_submit(self, interaction):
        color_text = self.color_input.value.strip().replace("#", "")
        try:
            color_value = int(color_text, 16) if color_text else 0x5865F2
        except ValueError:
            await say(interaction, "embed.basic_modal.bad_color")
            return
        data = self.state.active_embed
        data["title"] = self.title_input.value.strip()
        data["description"] = self.description_input.value.strip()
        data["color"] = color_value
        await interaction.response.edit_message(embed=render_active_preview(self.state), view=self.editor_view)


class EmbedMediaModal(Modal, title="ИЗОБРАЖЕНИЯ"):
    texts = "embed.media_modal"

    thumbnail_input = discord.ui.TextInput(label="Thumbnail URL", required=False, max_length=1000)
    image_input = discord.ui.TextInput(label="Image URL", required=False, max_length=1000)

    def __init__(self, state, editor_view):
        super().__init__()
        self.state = state
        self.editor_view = editor_view
        data = state.active_embed
        self.thumbnail_input.default = data.get("thumbnail", "")
        self.image_input.default = data.get("image", "")

    async def on_submit(self, interaction):
        data = self.state.active_embed
        data["thumbnail"] = normalize_url(self.thumbnail_input.value)
        data["image"] = normalize_url(self.image_input.value)
        await interaction.response.edit_message(embed=render_active_preview(self.state), view=self.editor_view)


class EmbedAuthorFooterModal(Modal, title="AUTHOR / FOOTER"):
    texts = "embed.author_modal"

    author_input = discord.ui.TextInput(label="Author name", required=False, max_length=256)
    footer_input = discord.ui.TextInput(label="Footer", required=False, max_length=2048)

    def __init__(self, state, editor_view):
        super().__init__()
        self.state = state
        self.editor_view = editor_view
        data = state.active_embed
        self.author_input.default = data.get("author_name", "")
        self.footer_input.default = data.get("footer_text", "")

    async def on_submit(self, interaction):
        data = self.state.active_embed
        data["author_name"] = self.author_input.value.strip()
        data["footer_text"] = self.footer_input.value.strip()
        await interaction.response.edit_message(embed=render_active_preview(self.state), view=self.editor_view)


class EmbedFieldModal(Modal, title="ПОЛЕ"):
    texts = "embed.field_modal"

    name_input = discord.ui.TextInput(label="Название", max_length=256)
    value_input = discord.ui.TextInput(label="Значение", style=discord.TextStyle.paragraph, max_length=1024)
    inline_input = discord.ui.TextInput(label="Inline? yes/no", required=False, max_length=3)

    def __init__(self, state, editor_view):
        super().__init__()
        self.state = state
        self.editor_view = editor_view

    async def on_submit(self, interaction):
        inline = self.inline_input.value.strip().lower() in {"yes", "y", "да", "д"}
        self.state.active_embed.setdefault("fields", []).append({
            "name": self.name_input.value, "value": self.value_input.value, "inline": inline
        })
        await interaction.response.edit_message(embed=render_active_preview(self.state), view=self.editor_view)


# ============================================================
# 4. ИНТЕРАКТИВ: select + отдельные Назад/Далее
# ============================================================

class InteractiveTypeSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=t("embed.interactive.option_form")[:100], emoji="📝", value="form"),
            discord.SelectOption(label=t("embed.interactive.option_list")[:100], emoji="📋", value="list"),
            discord.SelectOption(label=t("embed.interactive.option_select")[:100], emoji="🎯", value="select"),
            discord.SelectOption(label=t("embed.interactive.option_buttons")[:100], emoji="🔘", value="buttons"),
        ]
        super().__init__(placeholder=t("embed.interactive.placeholder")[:150], options=options, row=0)

    async def callback(self, interaction):
        self.view.chosen_type = self.values[0]
        await interaction.response.defer()


class InteractiveMenuView(PanelView):
    texts = "embed.interactive"

    def __init__(self, state, back_target):
        super().__init__(back_target=back_target)
        self.state = state
        self.chosen_type = None
        self.add_item(InteractiveTypeSelect())

    @discord.ui.button(label="Далее", emoji="▶️", style=discord.ButtonStyle.success, row=1)
    async def next_step(self, interaction, button):
        if self.chosen_type == "form":
            import extended_modules
            await interaction.response.edit_message(
                embed=panel_embed(interaction, "embed.form_step"),
                view=extended_modules.FormStartView(self.state.guild_id, self.state.owner_id)
            )
        elif self.chosen_type == "list":
            await interaction.response.edit_message(
                embed=panel_embed(interaction, "embed.list"),
                view=CustomListBuilderView(self.state, back_target=(interaction.message.embeds[0], self))
            )
        elif self.chosen_type == "select":
            await interaction.response.edit_message(
                embed=panel_embed(interaction, "embed.native_select"),
                view=NativeSelectTypeView(self.state, back_target=(interaction.message.embeds[0], self))
            )
        elif self.chosen_type == "buttons":
            await interaction.response.edit_message(
                embed=panel_embed(interaction, "embed.buttons", max=MAX_BUTTONS),
                view=ButtonBuilderView(self.state, back_target=(interaction.message.embeds[0], self))
            )
        else:
            await finish_message_build(interaction, self.state)


class CustomListBuilderView(PanelView):
    """Пользовательский select с опциями, которые сама задаёшь."""

    texts = "embed.list"
    def __init__(self, state, back_target):
        super().__init__(back_target=back_target)
        self.state = state
        self.state.interactive_type = "list"
        self.state.__dict__.setdefault("list_options", [])

    @discord.ui.button(label="Добавить опцию", emoji="➕", style=discord.ButtonStyle.success)
    async def add_option(self, interaction, button):
        if len(self.state.list_options) >= 25:
            await say(interaction, "embed.list.too_many")
            return
        await interaction.response.send_modal(ListOptionModal(self.state, self))

    @discord.ui.button(label="Готово", emoji="✅", style=discord.ButtonStyle.primary)
    async def done(self, interaction, button):
        await finish_message_build(interaction, self.state)


class ListOptionModal(Modal, title="ОПЦИЯ СПИСКА"):
    texts = "embed.list_option_modal"

    label_input = discord.ui.TextInput(label="Текст опции", max_length=100)
    description_input = discord.ui.TextInput(label="Описание", required=False, max_length=100)
    emoji_input = discord.ui.TextInput(label="Emoji", required=False, max_length=100)

    def __init__(self, state, list_view):
        super().__init__()
        self.state = state
        self.list_view = list_view

    async def on_submit(self, interaction):
        pending = {
            "label": self.label_input.value,
            "description": self.description_input.value,
            "emoji": self.emoji_input.value or None,
        }
        view = discord.ui.View(timeout=300)
        view.add_item(ListOptionActionSelect(interaction, self.state, pending))
        await interaction.response.send_message(t("embed.list.pick_action"), view=view, ephemeral=True)


class ListOptionActionSelect(discord.ui.Select):
    def __init__(self, interaction, state, pending):
        level = get_user_level(interaction)
        allowed = actions_for_level(level)
        options = [
            discord.SelectOption(label=row[0], description=(row[3] or "")[:100], value=row[0])
            for row in allowed
        ] or [discord.SelectOption(label=t("embed.no_actions")[:100], value="__none__")]
        super().__init__(placeholder=t("embed.pick_action")[:150], options=options)
        self.state = state
        self.pending = pending

    async def callback(self, interaction):
        if self.values[0] == "__none__":
            await say(interaction, "embed.no_actions_allowed")
            return
        self.pending["action_key"] = self.values[0]
        await interaction.response.send_modal(ListOptionValueModal(self.state, self.pending))


class ListOptionValueModal(Modal, title="ЗНАЧЕНИЕ ОПЦИИ"):
    texts = "embed.list_value_modal"

    value_input = discord.ui.TextInput(label="Текст ответа при выборе", required=False, style=discord.TextStyle.paragraph, max_length=1000)

    def __init__(self, state, pending):
        super().__init__()
        self.state = state
        self.pending = pending

    async def on_submit(self, interaction):
        self.pending["value"] = self.value_input.value
        self.state.list_options.append(self.pending)
        await say(interaction, "embed.list.option_added", label=self.pending["label"], total=len(self.state.list_options))


class NativeSelectTypeView(PanelView):
    texts = "embed.native_select"

    def __init__(self, state, back_target):
        super().__init__(back_target=back_target)
        self.state = state

    async def pick(self, interaction, kind):
        self.state.interactive_type = f"native_select:{kind}"
        await finish_message_build(interaction, self.state)

    @discord.ui.button(label="Роль", emoji="🎭", style=discord.ButtonStyle.secondary)
    async def role(self, interaction, button):
        await self.pick(interaction, "role")

    @discord.ui.button(label="Участник", emoji="👤", style=discord.ButtonStyle.secondary)
    async def user(self, interaction, button):
        await self.pick(interaction, "user")

    @discord.ui.button(label="Канал", emoji="📁", style=discord.ButtonStyle.secondary)
    async def channel(self, interaction, button):
        await self.pick(interaction, "channel")

    @discord.ui.button(label="Mentionable", emoji="🔀", style=discord.ButtonStyle.secondary)
    async def mentionable(self, interaction, button):
        await self.pick(interaction, "mentionable")


# ============================================================
# КНОПКИ через action_registry
# ============================================================

class ButtonBuilderView(PanelView):
    texts = "embed.buttons"

    def __init__(self, state, back_target):
        super().__init__(back_target=back_target)
        self.state = state

    @discord.ui.button(label="Добавить кнопку", emoji="➕", style=discord.ButtonStyle.success)
    async def add(self, interaction, button):
        if len(self.state.buttons) >= MAX_BUTTONS:
            await say(interaction, "embed.buttons.too_many")
            return
        await interaction.response.send_modal(ButtonLabelModal(self.state, self))

    @discord.ui.button(label="Готово", emoji="✅", style=discord.ButtonStyle.primary)
    async def done(self, interaction, button):
        await finish_message_build(interaction, self.state)


class ButtonLabelModal(Modal, title="ТЕКСТ КНОПКИ"):
    texts = "embed.button_label_modal"

    label_input = discord.ui.TextInput(label="Текст кнопки", max_length=80)

    def __init__(self, state, builder_view):
        super().__init__()
        self.state = state
        self.builder_view = builder_view

    async def on_submit(self, interaction):
        pending = {"label": self.label_input.value}
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "embed.button_color"),
            view=ButtonColorView(self.state, pending, self.builder_view)
        )


class ButtonColorView(PanelView):
    def __init__(self, state, pending, builder_view):
        super().__init__(back_target=None)
        self.state = state
        self.pending = pending
        self.builder_view = builder_view
        for key in BUTTON_STYLES:
            btn = discord.ui.Button(label=t(f"embed.button_color.{key}")[:80], style=discord.ButtonStyle.secondary)

            async def cb(interaction, key=key):
                self.pending["style"] = key
                action_view = ButtonActionView(self.state, self.pending, self.builder_view)
                action_view.add_item(ButtonActionSelect(interaction, self.state, self.pending))
                await interaction.response.edit_message(
                    embed=panel_embed(interaction, "embed.button_action"),
                    view=action_view
                )

            btn.callback = cb
            self.add_item(btn)


class ButtonActionSelect(discord.ui.Select):
    def __init__(self, interaction, state, pending):
        level = get_user_level(interaction)
        allowed = actions_for_level(level)
        options = [
            discord.SelectOption(label=row[0], description=(row[3] or "")[:100], value=row[0])
            for row in allowed
        ] or [discord.SelectOption(label=t("embed.no_actions")[:100], value="__none__")]
        super().__init__(placeholder=t("embed.pick_action")[:150], options=options, row=0)
        self.state = state
        self.pending = pending

    async def callback(self, interaction):
        if self.values[0] == "__none__":
            await say(interaction, "embed.no_actions_allowed")
            return
        self.pending["action_key"] = self.values[0]
        await interaction.response.send_modal(ButtonValueModal(self.state, self.pending, self.view.builder_view))


class ButtonActionView(PanelView):
    def __init__(self, state, pending, builder_view):
        super().__init__(back_target=None)
        self.state = state
        self.pending = pending
        self.builder_view = builder_view


class ButtonValueModal(Modal, title="ЗНАЧЕНИЕ"):
    texts = "embed.button_value_modal"

    value_input = discord.ui.TextInput(label="URL или текст действия", required=False, style=discord.TextStyle.paragraph, max_length=1000)

    def __init__(self, state, pending, builder_view):
        super().__init__()
        self.state = state
        self.pending = pending
        self.builder_view = builder_view

    async def on_submit(self, interaction):
        self.pending["value"] = self.value_input.value
        self.state.buttons.append(self.pending)
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "embed.buttons", description=t("embed.buttons.added", count=len(self.state.buttons), max=MAX_BUTTONS)),
            view=self.builder_view
        )


# ============================================================
# ФИНАЛ — живой Message Build
# ============================================================

async def finish_message_build(interaction, state):
    if interaction.guild is None:
        await say(interaction, "common.only_in_guild")
        return

    interactive_payload = None
    if state.interactive_type == "list":
        interactive_payload = {"type": "list", "options": getattr(state, "list_options", [])}
    elif state.interactive_type and state.interactive_type.startswith("native_select:"):
        interactive_payload = {"type": "native_select", "kind": state.interactive_type.split(":", 1)[1]}

    build_id = save_message_build(
        guild_id=interaction.guild.id,
        owner_id=interaction.user.id,
        name=state.name,
        content=state.content,
        embeds_json=json.dumps(state.embeds, ensure_ascii=False),
        buttons_json=json.dumps(state.buttons, ensure_ascii=False),
        visibility=state.visibility,
        category=state.category,
        allowed_role_ids_json=json.dumps(state.visibility_roles),
        visibility_levels_json=json.dumps(state.visibility_levels),
        interactive_json=json.dumps(interactive_payload, ensure_ascii=False),
    )

    await interaction.response.edit_message(
        embed=panel_embed(interaction, "embed.saved_build", id=build_id, embeds=len(state.embeds), buttons=len(state.buttons)),
        view=MessageBuildFinalView(build_id, back_target=None)
    )


class MessageBuildFinalView(PanelView):
    texts = "embed.final"

    def __init__(self, build_id, back_target):
        super().__init__(back_target=back_target)
        self.build_id = build_id

    @discord.ui.button(label="Предпросмотр", emoji="👁️", style=discord.ButtonStyle.secondary)
    async def preview(self, interaction, button):
        row = get_message_build(self.build_id)
        if not row:
            await say(interaction, "embed.build_not_found")
            return
        embeds_data = json.loads(row[5])
        buttons_data = json.loads(row[6])
        interactive_data = json.loads(row[11]) if row[11] else None
        await interaction.response.send_message(
            content=row[4] or None,
            embeds=[build_discord_embed(e) for e in embeds_data[:MAX_EMBEDS]],
            view=build_final_view(buttons_data, interactive_data),
            ephemeral=True,
        )

    @discord.ui.button(label="Отправить", emoji="📤", style=discord.ButtonStyle.success)
    async def send(self, interaction, button):
        view = discord.ui.View(timeout=300)
        select = discord.ui.ChannelSelect(placeholder=t("embed.send.placeholder")[:150], channel_types=[discord.ChannelType.text], min_values=1, max_values=5)

        async def selected(i):
            # Отправка в несколько каналов может занять дольше 3 секунд —
            # сначала defer, иначе Discord сочтёт interaction проваленным.
            await i.response.defer(ephemeral=True, thinking=True)

            row = get_message_build(self.build_id)
            if not row:
                await i.followup.send(t("embed.build_not_found"), ephemeral=True)
                return
            content = row[4] or None
            embeds_data = json.loads(row[5])[:MAX_EMBEDS]
            buttons_data = json.loads(row[6])
            interactive_data = json.loads(row[11]) if row[11] else None
            final_view = build_final_view(buttons_data, interactive_data)

            # Каждый embed — отдельным сообщением; content идёт с первым,
            # компоненты — с последним.
            parts = [
                {"embed": build_discord_embed(embed_data)}
                for embed_data in embeds_data
            ] or [{}]
            if content:
                parts[0]["content"] = content
            if final_view is not None:
                parts[-1]["view"] = final_view
            if parts == [{}]:
                await i.followup.send(t("embed.build_empty"), ephemeral=True)
                return

            sent, failed = [], []
            for channel in select.values:
                real_channel = i.guild.get_channel(channel.id)
                if real_channel is None:
                    failed.append(t("embed.send.channel_missing", channel=f"<#{channel.id}>"))
                    continue
                try:
                    for part in parts:
                        msg = await real_channel.send(**part)
                        # Живой объект: помним, откуда родилось каждое сообщение
                        save_sent_instance(self.build_id, msg.id, real_channel.id, i.guild.id)
                except discord.Forbidden:
                    failed.append(t("embed.send.forbidden", channel=real_channel.mention))
                    continue
                except discord.HTTPException as error:
                    failed.append(t("embed.send.http_error", channel=real_channel.mention, error=error))
                    continue
                sent.append(real_channel.mention)

            lines = []
            if sent:
                lines.append(t("embed.send.sent", channels=", ".join(sent)))
            if failed:
                lines.append(t("embed.send.failed", errors="\n".join(failed)))
            await i.followup.send("\n\n".join(lines), ephemeral=True)

        select.callback = selected
        view.add_item(select)
        await interaction.response.send_message(t("embed.send.prompt"), view=view, ephemeral=True)

    @discord.ui.button(label="Сохранить как шаблон", emoji="💾", style=discord.ButtonStyle.primary)
    async def save_template_button(self, interaction, button):
        await interaction.response.send_modal(SaveAsTemplateModal(self.build_id))

    @discord.ui.button(label="Удалить", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete(self, interaction, button):
        if delete_message_build(self.build_id, interaction.user.id):
            await interaction.response.edit_message(embed=panel_embed(interaction, "embed.deleted"), view=None)
        else:
            await say(interaction, "embed.delete_denied")


class SaveAsTemplateModal(Modal, title="СОХРАНИТЬ КАК ШАБЛОН"):
    texts = "embed.save_template_modal"

    name_input = discord.ui.TextInput(label="Название шаблона", max_length=100)
    category_input = discord.ui.TextInput(label="Категория", required=False, max_length=50)

    def __init__(self, build_id):
        super().__init__()
        self.build_id = build_id

    async def on_submit(self, interaction):
        row = get_message_build(self.build_id)
        if not row:
            await say(interaction, "embed.build_not_found")
            return
        payload = {
            "embeds": json.loads(row[5]),
            "buttons": json.loads(row[6]),
            "interactive": json.loads(row[11]) if row[11] else None,
        }
        tid = save_template(
            guild_id=interaction.guild.id,
            owner_id=interaction.user.id,
            name=self.name_input.value.strip(),
            template_type="message",
            payload_json=json.dumps(payload, ensure_ascii=False),
            visibility=row[7],
            category=self.category_input.value.strip() or "general",
            allowed_role_ids_json=row[9],
        )
        await say(interaction, "embed.save_template_modal.done", id=tid)


# ============================================================
# КОМАНДА /embed
# ============================================================

def register_embed(bot):
    @bot.tree.command(name="embed", description="Создать Message Build с embed'ами и интерактивом")
    async def embed_command(interaction):
        if not await core.require_command_access(interaction, "embed"):
            return

        await interaction.response.send_message(
            embed=panel_embed(interaction, "embed.home"),
            view=EmbedHomeView(interaction.guild.id, interaction.user.id),
            ephemeral=True,
        )
