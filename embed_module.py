"""
embed_module.py
================
/embed и /messages — Message Build: сообщение из embed'ов + интерактив.

Поток: интро -> видимость -> редактор embed'ов -> интерактив (кнопки,
список, выбор, кнопка формы — можно сочетать) -> сохранение -> действия
(предпросмотр, отправка, редактирование, обновление отправленных, шаблон,
удаление).

Рендер и выполнение действий — в actions.py (единый путь). Message Build —
"живой объект": отправленные сообщения записываются в sent_instances, их
можно обновить после правки, а кнопки в них постоянные и берут данные из БД.
"""

import json

import discord

import core
from core import PanelView, Modal, t, panel_embed, get_user_level, actions_for_level
from actions import (
    MAX_EMBEDS, MAX_BUTTONS, BUTTON_STYLES,
    say, normalize_url, valid_emoji, build_discord_embed, render_source, load_source,
    validate_action_value, build_visible, template_visible, form_visible,
    check_url, embed_has_content, EMBED_TOTAL_LIMIT, message_parts,
)
from database import (
    save_message_build, update_message_build, get_message_build, get_message_builds,
    delete_message_build, save_sent_instance, get_sent_instances, delete_sent_instance,
    save_template, get_templates, get_template, get_forms, get_form,
)


def default_embed_data():
    return {"title": "", "description": "", "color": core.embed_color().value, "fields": []}


def _json(value, default):
    try:
        result = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default
    return default if result is None else result


def can_manage_build(interaction, row):
    """Править/удалять build может его создатель, админ или владелец бота."""
    return (
        row[2] == interaction.user.id
        or core.is_owner(interaction)
        or core.level_value(get_user_level(interaction)) >= core.level_value("admin")
    )


# ============================================================
# STATE
# ============================================================

class EmbedState:
    def __init__(self, guild_id, owner_id):
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.build_id = None  # не None — редактируем существующий build
        self.name = t("embed.default_name")
        self.content = ""
        self.embeds = [default_embed_data()]
        self.active_index = 0
        self.buttons = []
        self.interactive = None  # {"type": "list", "options": [...]} | {"type": "native_select", "kind": ...}
        self.visibility = "private"
        self.visibility_roles = []
        self.visibility_levels = []
        self.category = "general"
        self.editor_back = None  # куда ведёт «Назад» из редактора

    @property
    def active_embed(self):
        return self.embeds[self.active_index]

    @property
    def list_options(self):
        if not self.interactive or self.interactive.get("type") != "list":
            self.interactive = {"type": "list", "options": []}
        return self.interactive["options"]

    @classmethod
    def from_build(cls, row):
        state = cls(row[1], row[2])
        state.build_id = row[0]
        state.name = row[3] or state.name
        state.content = row[4] or ""
        state.embeds = [e for e in _json(row[5], []) if isinstance(e, dict)] or [default_embed_data()]
        state.buttons = _json(row[6], [])
        state.visibility = row[7]
        state.category = row[8]
        state.visibility_roles = _json(row[9], [])
        state.visibility_levels = _json(row[10], [])
        state.interactive = _json(row[11], None)
        return state

    def load_payload(self, payload):
        if not isinstance(payload, dict):
            payload = {}
        self.content = payload.get("content") or ""
        self.embeds = [e for e in (payload.get("embeds") or []) if isinstance(e, dict)][:MAX_EMBEDS] or [default_embed_data()]
        self.buttons = list(payload.get("buttons") or [])[:MAX_BUTTONS]
        self.interactive = payload.get("interactive")


def render_active_preview(state):
    """Предпросмотр активного embed'а. Служебная пометка дописывается к футеру,
    а не заменяет его — иначе свой футер в редакторе не увидеть."""
    data = state.active_embed
    embed = build_discord_embed(data)
    marker = t("embed.preview_footer", n=state.active_index + 1, total=len(state.embeds), visibility=state.visibility)
    if not embed_has_content(data):
        marker = f"{marker} · {t('embed.preview_empty')}"
    footer = data.get("footer_text")
    embed.set_footer(
        text=(f"{footer} · {marker}" if footer else marker)[:2048],
        icon_url=embed.footer.icon_url if footer else None,
    )
    return embed


def interactive_summary(state):
    interactive = state.interactive or {}
    if interactive.get("type") == "list":
        extra = t("embed.interactive.summary_list", count=len(interactive.get("options") or []))
    elif interactive.get("type") == "native_select":
        extra = t("embed.interactive.summary_native", kind=interactive.get("kind"))
    else:
        extra = t("embed.interactive.summary_none")
    return t("embed.interactive.text", buttons=len(state.buttons), max=MAX_BUTTONS, extra=extra)


def buttons_text(state):
    lines = []
    for index, raw in enumerate(state.buttons, start=1):
        label = raw.get("label") or t("common.default_button_label")
        action = raw.get("action_key") or ("link" if raw.get("style") == "link" else raw.get("action", "?"))
        if action in ("build.trigger", "build.goto", "build.refresh") and raw.get("value"):
            build = get_message_build(int(raw["value"])) if str(raw["value"]).isdigit() else None
            action = f"{action} → {(build[3] if build else '?')} #{raw['value']}"
        lines.append(t("buttons.builder.line", n=index, emoji=raw.get("emoji") or "", label=label, action=action))
    return "\n".join(lines) or t("buttons.builder.empty")


# ============================================================
# 1. ИНТРО И СПИСКИ
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
            view=SavedBuildsListView(interaction, back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="Из сообщения", emoji="📥", style=discord.ButtonStyle.secondary)
    async def from_message(self, interaction, button):
        from build_tools import ImportMessageModal
        await interaction.response.send_modal(ImportMessageModal())

    @discord.ui.button(label="Использовать шаблон", emoji="📁", style=discord.ButtonStyle.secondary)
    async def use_template(self, interaction, button):
        rows = [
            row for row in get_templates(self.guild_id, template_type="message")
            if template_visible(interaction, get_template(row[0]))
        ]
        view = PanelView(back_target=(interaction.message.embeds[0], self))
        for tid, owner, name, *_ in rows[:20]:
            btn = discord.ui.Button(label=f"{(name or t('embed.default_name'))[:60]} · #{tid}", style=discord.ButtonStyle.secondary)

            async def cb(i, tid=tid):
                row = get_template(tid)
                if not row or not template_visible(i, row):
                    await say(i, "templates.unavailable")
                    return
                state = EmbedState(self.guild_id, i.user.id)
                state.load_payload(_json(row[5], {}))
                await i.response.edit_message(
                    embed=render_active_preview(state),
                    view=EmbedEditorView(state, back_target=(i.message.embeds[0], view)),
                )

            btn.callback = cb
            view.add_item(btn)
        if not rows:
            view.add_item(discord.ui.Button(label=t("embed.templates.empty")[:80], disabled=True))
        await interaction.response.edit_message(embed=panel_embed(interaction, "embed.templates"), view=view)


class SavedBuildsListView(PanelView):
    """Все build'ы сервера, которые этот пользователь может видеть."""

    def __init__(self, interaction, back_target=None):
        super().__init__(back_target=back_target)
        rows = [
            row for row in get_message_builds(interaction.guild.id)
            if build_visible(interaction, get_message_build(row[0]))
        ]
        for bid, owner, name, vis, cat, updated in rows[:20]:
            btn = discord.ui.Button(label=f"{(name or t('embed.default_name'))[:60]} · #{bid}", style=discord.ButtonStyle.secondary)

            async def cb(i, bid=bid):
                row = get_message_build(bid)
                if not row or not build_visible(i, row):
                    await say(i, "embed.build_not_found")
                    return
                await i.response.edit_message(
                    embed=build_card_embed(i, row),
                    view=MessageBuildFinalView(bid, back_target=(i.message.embeds[0], self))
                )

            btn.callback = cb
            self.add_item(btn)
        if not rows:
            self.add_item(discord.ui.Button(label=t("embed.saved.empty")[:80], disabled=True))


def build_card_embed(interaction, row):
    return panel_embed(
        interaction, "embed.build_card", title=row[3] or t("embed.default_name"),
        visibility=row[7], id=row[0], owner=f"<@{row[2]}>",
        embeds=len(_json(row[5], [])), buttons=len(_json(row[6], [])),
        sent=len(get_sent_instances(row[0])),
    )


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
        await interaction.response.edit_message(
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
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "embed.restriction_level"),
            view=LevelRestrictionView(self.state, back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="По роли(ям)", emoji="🎭", style=discord.ButtonStyle.secondary)
    async def by_role(self, interaction, button):
        view = PanelView(back_target=(interaction.message.embeds[0], self))
        select = discord.ui.RoleSelect(placeholder=t("embed.restriction_roles.placeholder")[:150], min_values=1, max_values=5)

        async def selected(i):
            self.state.visibility_roles = [r.id for r in select.values]
            self.state.visibility_levels = []
            await go_to_editor(i, self.state, back_target=(i.message.embeds[0], view))

        select.callback = selected
        view.add_item(select)
        await interaction.response.edit_message(embed=panel_embed(interaction, "embed.restriction_roles"), view=view)


class LevelRestrictionView(PanelView):
    texts = "embed.restriction_level"

    def __init__(self, state, back_target):
        super().__init__(back_target=back_target)
        self.state = state

    async def pick(self, interaction, level):
        self.state.visibility_levels = [level]
        self.state.visibility_roles = []
        await go_to_editor(interaction, self.state, back_target=(interaction.message.embeds[0], self))

    @discord.ui.button(label="Member", style=discord.ButtonStyle.secondary)
    async def member(self, interaction, button):
        await self.pick(interaction, "member")

    @discord.ui.button(label="Staff", style=discord.ButtonStyle.primary)
    async def staff(self, interaction, button):
        await self.pick(interaction, "staff")

    @discord.ui.button(label="Admin", style=discord.ButtonStyle.success)
    async def admin(self, interaction, button):
        await self.pick(interaction, "admin")


async def go_to_editor(interaction, state, back_target):
    # Видимость можно сменить и из редактора: тогда «Назад» в редакторе
    # ведёт туда же, куда вёл изначально, а не обратно в выбор видимости.
    back_target = state.editor_back or back_target
    state.editor_back = back_target
    await interaction.response.edit_message(
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


class EmbedFieldSelect(discord.ui.Select):
    """Правка или удаление любого поля активного embed'а."""

    def __init__(self, state):
        self.state = state
        fields = state.active_embed.get("fields") or []
        options = [
            discord.SelectOption(
                label=t("embed.editor.field_option", n=n, name=str(field.get("name") or "—"))[:100],
                description=str(field.get("value") or "")[:100] or None,
                value=str(n - 1),
            )
            for n, field in enumerate(fields[:25], 1)
        ]
        super().__init__(placeholder=t("embed.editor.fields_placeholder")[:150], options=options, row=3)

    async def callback(self, interaction):
        await interaction.response.send_modal(EmbedFieldModal(self.state, self.view, index=int(self.values[0])))


class EmbedEditorView(PanelView):
    texts = "embed.editor"

    def __init__(self, state, back_target):
        super().__init__(back_target=back_target)
        self.state = state
        state.editor_back = state.editor_back or back_target
        self.add_item(EmbedSwitchSelect(state))
        if state.active_embed.get("fields"):
            self.add_item(EmbedFieldSelect(state))

    async def refresh(self, interaction):
        await interaction.response.edit_message(
            embed=render_active_preview(self.state),
            view=EmbedEditorView(self.state, back_target=self.back_target),
        )

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
        if len(self.state.active_embed.get("fields") or []) >= 25:
            await say(interaction, "embed.editor.too_many_fields")
            return
        await interaction.response.send_modal(EmbedFieldModal(self.state, self))

    @discord.ui.button(label="Видимость", emoji="👁️", style=discord.ButtonStyle.secondary, row=1)
    async def visibility(self, interaction, button):
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "embed.visibility"),
            view=VisibilityView(self.state, back_target=(interaction.message.embeds[0], self)),
        )

    @discord.ui.button(label="Название и текст", emoji="📝", style=discord.ButtonStyle.primary, row=2)
    async def meta(self, interaction, button):
        await interaction.response.send_modal(BuildMetaModal(self.state, self))

    @discord.ui.button(label="Время", emoji="🕒", style=discord.ButtonStyle.secondary, row=2)
    async def timestamp(self, interaction, button):
        self.state.active_embed["timestamp"] = not self.state.active_embed.get("timestamp")
        await self.refresh(interaction)

    @discord.ui.button(label="Удалить embed", emoji="🗑️", style=discord.ButtonStyle.danger, row=2)
    async def delete_embed(self, interaction, button):
        if len(self.state.embeds) <= 1:
            await say(interaction, "embed.editor.last_embed")
            return
        self.state.embeds.pop(self.state.active_index)
        self.state.active_index = max(0, self.state.active_index - 1)
        await self.refresh(interaction)

    @discord.ui.button(label="Подтвердить дизайн", emoji="✅", style=discord.ButtonStyle.success, row=2)
    async def confirm(self, interaction, button):
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "embed.interactive", description=interactive_summary(self.state)),
            view=InteractiveHubView(self.state, back_target=(interaction.message.embeds[0], self))
        )


def _parse_color(text):
    text = (text or "").strip().lower().replace("#", "")
    if text.startswith("0x"):
        text = text[2:]
    if not text:
        return core.embed_color().value
    if len(text) != 6:
        return None
    try:
        return int(text, 16)
    except ValueError:
        return None


async def apply_embed_changes(interaction, state, editor_view, changes):
    """
    Применить правку активного embed'а, только если Discord её примет.
    При ошибке состояние не меняется — иначе каждое обновление редактора
    падало бы на том же embed'е.
    """
    candidate = {**state.active_embed, **changes}
    length = len(build_discord_embed(candidate))
    if length > EMBED_TOTAL_LIMIT:
        await say(interaction, "embed.too_long", length=length, max=EMBED_TOTAL_LIMIT)
        return
    state.embeds[state.active_index] = candidate
    await editor_view.refresh(interaction)


async def parse_urls(interaction, inputs):
    """inputs: {ключ: TextInput} -> {ключ: url} или None (ошибка уже показана)."""
    result = {}
    for key, text_input in inputs.items():
        url, ok = check_url(text_input.value)
        if not ok:
            await say(interaction, "embed.bad_url", field=text_input.label, value=text_input.value.strip()[:100])
            return None
        result[key] = url
    return result


class EmbedBasicModal(Modal, title="ОСНОВНОЕ"):
    texts = "embed.basic_modal"

    title_input = discord.ui.TextInput(label="Title", required=False, max_length=256)
    description_input = discord.ui.TextInput(label="Description", required=False, style=discord.TextStyle.paragraph, max_length=4000)
    url_input = discord.ui.TextInput(label="URL заголовка", required=False, max_length=1000)
    color_input = discord.ui.TextInput(label="Цвет HEX", required=False, max_length=8, placeholder="#5865F2")

    def __init__(self, state, editor_view):
        super().__init__()
        self.state = state
        self.editor_view = editor_view
        data = state.active_embed
        self.title_input.default = data.get("title") or ""
        self.description_input.default = data.get("description") or ""
        self.url_input.default = data.get("url") or ""
        try:
            self.color_input.default = f"#{int(data.get('color', core.embed_color().value)):06X}"
        except (TypeError, ValueError):
            self.color_input.default = ""

    async def on_submit(self, interaction):
        color_value = _parse_color(self.color_input.value)
        if color_value is None:
            await say(interaction, "embed.basic_modal.bad_color")
            return
        urls = await parse_urls(interaction, {"url": self.url_input})
        if urls is None:
            return
        await apply_embed_changes(interaction, self.state, self.editor_view, {
            "title": self.title_input.value.strip(),
            "description": self.description_input.value.strip(),
            "color": color_value,
            **urls,
        })


class EmbedMediaModal(Modal, title="ИЗОБРАЖЕНИЯ"):
    texts = "embed.media_modal"

    thumbnail_input = discord.ui.TextInput(label="Thumbnail URL", required=False, max_length=1000)
    image_input = discord.ui.TextInput(label="Image URL", required=False, max_length=1000)

    def __init__(self, state, editor_view):
        super().__init__()
        self.state = state
        self.editor_view = editor_view
        data = state.active_embed
        self.thumbnail_input.default = data.get("thumbnail") or ""
        self.image_input.default = data.get("image") or ""

    async def on_submit(self, interaction):
        urls = await parse_urls(interaction, {"thumbnail": self.thumbnail_input, "image": self.image_input})
        if urls is None:
            return
        await apply_embed_changes(interaction, self.state, self.editor_view, urls)


class EmbedAuthorFooterModal(Modal, title="AUTHOR / FOOTER"):
    texts = "embed.author_modal"

    author_input = discord.ui.TextInput(label="Author name", required=False, max_length=256)
    author_url_input = discord.ui.TextInput(label="Author URL", required=False, max_length=1000)
    author_icon_input = discord.ui.TextInput(label="Author icon URL", required=False, max_length=1000)
    footer_input = discord.ui.TextInput(label="Footer", required=False, max_length=2048)
    footer_icon_input = discord.ui.TextInput(label="Footer icon URL", required=False, max_length=1000)

    def __init__(self, state, editor_view):
        super().__init__()
        self.state = state
        self.editor_view = editor_view
        data = state.active_embed
        self.author_input.default = data.get("author_name") or ""
        self.author_url_input.default = data.get("author_url") or ""
        self.author_icon_input.default = data.get("author_icon") or ""
        self.footer_input.default = data.get("footer_text") or ""
        self.footer_icon_input.default = data.get("footer_icon") or ""

    async def on_submit(self, interaction):
        urls = await parse_urls(interaction, {
            "author_url": self.author_url_input,
            "author_icon": self.author_icon_input,
            "footer_icon": self.footer_icon_input,
        })
        if urls is None:
            return
        await apply_embed_changes(interaction, self.state, self.editor_view, {
            "author_name": self.author_input.value.strip(),
            "footer_text": self.footer_input.value.strip(),
            **urls,
        })


class EmbedFieldModal(Modal, title="ПОЛЕ"):
    """Новое поле или правка существующего (index). Пустые название и значение
    при правке — удалить поле."""

    texts = "embed.field_modal"

    name_input = discord.ui.TextInput(label="Название", required=False, max_length=256)
    value_input = discord.ui.TextInput(label="Значение", required=False, style=discord.TextStyle.paragraph, max_length=1024)
    inline_input = discord.ui.TextInput(label="Inline? yes/no", required=False, max_length=3)

    def __init__(self, state, editor_view, index=None):
        super().__init__()
        self.state = state
        self.editor_view = editor_view
        self.index = index
        if index is not None:
            field = (state.active_embed.get("fields") or [])[index]
            self.name_input.default = field.get("name") or ""
            self.value_input.default = field.get("value") or ""
            self.inline_input.default = "yes" if field.get("inline") else "no"
            self.name_input.placeholder = t("embed.field_modal.delete_hint")[:100]

    async def on_submit(self, interaction):
        name = self.name_input.value.strip()
        value = self.value_input.value.strip()
        fields = list(self.state.active_embed.get("fields") or [])
        editing = self.index is not None and self.index < len(fields)
        if not name and not value:
            if not editing:
                await say(interaction, "embed.field_modal.empty")
                return
            fields.pop(self.index)
        else:
            inline = self.inline_input.value.strip().lower() in {"yes", "y", "да", "д"}
            field = {"name": name or "\u200b", "value": value or "\u200b", "inline": inline}
            if editing:
                fields[self.index] = field
            else:
                fields.append(field)
        await apply_embed_changes(interaction, self.state, self.editor_view, {"fields": fields})


class BuildMetaModal(Modal, title="НАЗВАНИЕ И ТЕКСТ"):
    texts = "embed.meta_modal"

    name_input = discord.ui.TextInput(label="Название build'а", max_length=100)
    content_input = discord.ui.TextInput(label="Текст над embed'ами", required=False, style=discord.TextStyle.paragraph, max_length=2000)
    category_input = discord.ui.TextInput(label="Категория", required=False, max_length=50)

    def __init__(self, state, editor_view):
        super().__init__()
        self.state = state
        self.editor_view = editor_view
        self.name_input.default = state.name
        self.content_input.default = state.content
        self.category_input.default = state.category

    async def on_submit(self, interaction):
        self.state.name = self.name_input.value.strip() or t("embed.default_name")
        self.state.content = self.content_input.value
        self.state.category = self.category_input.value.strip() or "general"
        await self.editor_view.refresh(interaction)


# ============================================================
# 4. ИНТЕРАКТИВ: хаб — кнопки, список/выбор и кнопка формы сочетаются
# ============================================================

class InteractiveHubView(PanelView):
    texts = "embed.interactive"

    def __init__(self, state, back_target):
        super().__init__(back_target=back_target)
        self.state = state

    def hub_target(self, interaction):
        return (interaction.message.embeds[0], self)

    @discord.ui.button(label="Кнопки", emoji="🔘", style=discord.ButtonStyle.primary)
    async def buttons(self, interaction, button):
        await interaction.response.edit_message(
            embed=buttons_embed(interaction, self.state),
            view=ButtonBuilderView(self.state, hub=self, back_target=self.hub_target(interaction))
        )

    @discord.ui.button(label="Список", emoji="📋", style=discord.ButtonStyle.secondary)
    async def list(self, interaction, button):
        await interaction.response.edit_message(
            embed=list_embed(interaction, self.state),
            view=CustomListBuilderView(self.state, hub=self, back_target=self.hub_target(interaction))
        )

    @discord.ui.button(label="Выбор роли/участника", emoji="🎯", style=discord.ButtonStyle.secondary)
    async def native(self, interaction, button):
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "embed.native_select"),
            view=NativeSelectTypeView(self.state, hub=self, back_target=self.hub_target(interaction))
        )

    @discord.ui.button(label="Кнопка формы", emoji="📝", style=discord.ButtonStyle.secondary)
    async def form(self, interaction, button):
        if not core.is_action_allowed(get_user_level(interaction), "form.trigger"):
            await say(interaction, "embed.no_actions_allowed")
            return
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "embed.form_attach"),
            view=FormAttachView(interaction, self.state, hub=self, back_target=self.hub_target(interaction))
        )

    @discord.ui.button(label="Сохранить", emoji="💾", style=discord.ButtonStyle.success, row=1)
    async def save(self, interaction, button):
        await finish_message_build(interaction, self.state)

    @discord.ui.button(label="Убрать список", emoji="🧹", style=discord.ButtonStyle.secondary, row=1)
    async def clear_interactive(self, interaction, button):
        self.state.interactive = None
        await back_to_hub(interaction, self)


async def back_to_hub(interaction, hub):
    """Вернуться в хаб интерактива с актуальной сводкой."""
    await interaction.response.edit_message(
        embed=panel_embed(interaction, "embed.interactive", description=interactive_summary(hub.state)),
        view=InteractiveHubView(hub.state, back_target=hub.back_target),
    )


# ---------- общий выбор «убрать любой элемент» ----------

class RemoveItemSelect(discord.ui.Select):
    """Убрать конкретную кнопку/опцию, а не только последнюю."""

    def __init__(self, items, placeholder_key):
        self.items = items
        options = [
            discord.SelectOption(label=f"{n}. {str(item.get('label') or '—')}"[:100], value=str(n - 1))
            for n, item in enumerate(items[:25], 1)
        ]
        super().__init__(placeholder=t(placeholder_key)[:150], options=options, row=1)

    async def callback(self, interaction):
        index = int(self.values[0])
        if index < len(self.items):
            self.items.pop(index)
        await self.view.show(interaction)


def builder_screen(interaction, builder_view):
    """(embed, свежий view) экрана, где собираются кнопки или опции списка."""
    fresh = builder_view.rebuilt()
    return fresh.screen_embed(interaction), fresh


def action_pick_view(interaction, state, pending, target, builder_view):
    """Экран выбора действия с «Назад» к списку кнопок/опций."""
    view = PanelView(back_target=builder_screen(interaction, builder_view), timeout=300)
    view.add_item(ActionPickSelect(interaction, state, pending, target=target, builder_view=builder_view))
    return view


# ---------- список ----------

def list_embed(interaction, state):
    interactive = state.interactive or {}
    options = (interactive.get("options") or []) if interactive.get("type") == "list" else []
    lines = "\n".join(f"{n}. {o.get('label')} · {o.get('action_key')}" for n, o in enumerate(options, 1))
    return panel_embed(interaction, "embed.list", options=lines or t("embed.list.empty"), count=len(options))


class CustomListBuilderView(PanelView):
    """Пользовательский select с опциями, которые сама задаёшь."""

    texts = "embed.list"

    def __init__(self, state, hub, back_target):
        super().__init__(back_target=back_target)
        self.state = state
        self.hub = hub
        options = (state.interactive or {}).get("options") if (state.interactive or {}).get("type") == "list" else None
        if options:
            self.add_item(RemoveItemSelect(options, "embed.list.remove_placeholder"))

    def rebuilt(self):
        return CustomListBuilderView(self.state, self.hub, self.back_target)

    def screen_embed(self, interaction):
        return list_embed(interaction, self.state)

    async def show(self, interaction):
        embed, view = builder_screen(interaction, self)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Добавить опцию", emoji="➕", style=discord.ButtonStyle.success)
    async def add_option(self, interaction, button):
        if len(self.state.list_options) >= 25:
            await say(interaction, "embed.list.too_many")
            return
        await interaction.response.send_modal(ListOptionModal(self.state, self))

    @discord.ui.button(label="Убрать последнюю", emoji="➖", style=discord.ButtonStyle.secondary)
    async def remove_last(self, interaction, button):
        if self.state.list_options:
            self.state.list_options.pop()
        await self.show(interaction)

    @discord.ui.button(label="Готово", emoji="✅", style=discord.ButtonStyle.primary)
    async def done(self, interaction, button):
        if self.state.interactive and not self.state.interactive.get("options"):
            self.state.interactive = None
        await back_to_hub(interaction, self.hub)


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
        if not valid_emoji(self.emoji_input.value):
            await say(interaction, "embed.bad_emoji")
            return
        pending = {
            "label": self.label_input.value,
            "description": self.description_input.value,
            "emoji": self.emoji_input.value.strip() or None,
        }
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "embed.button_action"),
            view=action_pick_view(interaction, self.state, pending, "list", self.list_view),
        )


# ---------- выбор действия (общий для кнопок и опций списка) ----------

class ActionPickSelect(discord.ui.Select):
    def __init__(self, interaction, state, pending, target, builder_view):
        allowed = actions_for_level(get_user_level(interaction))
        options = [
            discord.SelectOption(label=row[0], description=(row[3] or "")[:100], value=row[0])
            for row in allowed[:25]
        ] or [discord.SelectOption(label=t("embed.no_actions")[:100], value="__none__")]
        super().__init__(placeholder=t("embed.pick_action")[:150], options=options, row=0)
        self.state = state
        self.pending = pending
        self.target = target
        self.builder_view = builder_view

    async def callback(self, interaction):
        action_key = self.values[0]
        if action_key == "__none__":
            await say(interaction, "embed.no_actions_allowed")
            return
        self.pending["action_key"] = action_key
        if action_key == "message.edit":
            # значение не нужно — сразу добавляем
            await commit_pending(interaction, self.state, self.pending, self.target, self.builder_view, "")
            return
        if action_key in ("build.trigger", "build.goto", "build.refresh"):
            # ID никто не помнит — даём выбрать из сохранённых сообщений
            view = PanelView(back_target=builder_screen(interaction, self.builder_view), timeout=300)
            view.add_item(BuildPickSelect(interaction, self.state, self.pending, self.target, self.builder_view))
            await interaction.response.edit_message(embed=panel_embed(interaction, "embed.build_pick"), view=view)
            return
        await interaction.response.send_modal(ActionValueModal(self.state, self.pending, self.target, self.builder_view))


class BuildPickSelect(discord.ui.Select):
    """Выбор сохранённого Message Build, который кнопка будет показывать."""

    def __init__(self, interaction, state, pending, target, builder_view):
        rows = [
            row for row in get_message_builds(interaction.guild.id)
            if row[0] != state.build_id and build_visible(interaction, get_message_build(row[0]))
        ][:25]
        options = [
            discord.SelectOption(
                label=f"{(name or t('embed.default_name'))[:90]} · #{bid}",
                description=t("embed.build_pick.option", visibility=vis)[:100],
                value=str(bid),
            )
            for bid, owner, name, vis, cat, updated in rows
        ] or [discord.SelectOption(label=t("embed.saved.empty")[:100], value="__none__")]
        super().__init__(placeholder=t("embed.build_pick.placeholder")[:150], options=options, row=0)
        self.state = state
        self.pending = pending
        self.target = target
        self.builder_view = builder_view

    async def callback(self, interaction):
        if self.values[0] == "__none__":
            await say(interaction, "embed.build_pick.none")
            return
        await commit_pending(interaction, self.state, self.pending, self.target, self.builder_view, self.values[0])


class ActionValueModal(Modal, title="ЗНАЧЕНИЕ"):
    texts = "embed.button_value_modal"

    value_input = discord.ui.TextInput(label="URL или текст действия", required=False, style=discord.TextStyle.paragraph, max_length=1000)

    def __init__(self, state, pending, target, builder_view):
        super().__init__()
        self.state = state
        self.pending = pending
        self.target = target
        self.builder_view = builder_view
        hint_key = "embed.value_hint.link" if pending.get("style") == "link" else f"embed.value_hint.{pending.get('action_key')}"
        if core.has_text(hint_key):
            self.value_input.placeholder = t(hint_key)[:100]

    async def on_submit(self, interaction):
        await commit_pending(interaction, self.state, self.pending, self.target, self.builder_view, self.value_input.value)


async def commit_pending(interaction, state, pending, target, builder_view, raw_value):
    value, error = validate_action_value(interaction, pending.get("action_key"), raw_value, style=pending.get("style"))
    if error:
        await say(interaction, error)
        return
    pending["value"] = value
    if target == "list":
        if len(state.list_options) >= 25:
            await say(interaction, "embed.list.too_many")
            return
        state.list_options.append(pending)
    else:
        if len(state.buttons) >= MAX_BUTTONS:
            await say(interaction, "embed.buttons.too_many", max=MAX_BUTTONS)
            return
        state.buttons.append(pending)
    await builder_view.show(interaction)


# ---------- нативный выбор ----------

class NativeSelectTypeView(PanelView):
    texts = "embed.native_select"

    def __init__(self, state, hub, back_target):
        super().__init__(back_target=back_target)
        self.state = state
        self.hub = hub

    async def pick(self, interaction, kind):
        self.state.interactive = {"type": "native_select", "kind": kind}
        await back_to_hub(interaction, self.hub)

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


# ---------- кнопка формы ----------

class FormAttachView(PanelView):
    def __init__(self, interaction, state, hub, back_target):
        super().__init__(back_target=back_target)
        self.state = state
        self.hub = hub
        rows = [row for row in get_forms(interaction.guild.id) if form_visible(interaction, get_form(row[0]))]
        if not rows:
            self.add_item(discord.ui.Button(label=t("embed.form_attach.empty")[:80], disabled=True))
            return
        select = discord.ui.Select(
            placeholder=t("embed.form_attach.placeholder")[:150],
            options=[discord.SelectOption(label=f"{(row[2] or '')[:90]} · #{row[0]}", value=str(row[0])) for row in rows[:25]],
        )

        async def picked(i):
            if len(self.state.buttons) >= MAX_BUTTONS:
                await say(i, "embed.buttons.too_many", max=MAX_BUTTONS)
                return
            self.state.buttons.append({
                "label": t("forms.use.apply")[:80], "emoji": "📝", "style": "blue",
                "action_key": "form.trigger", "value": select.values[0],
            })
            await back_to_hub(i, self.hub)

        select.callback = picked
        self.add_item(select)


# ============================================================
# КНОПКИ через action_registry
# ============================================================

def buttons_embed(interaction, state):
    return panel_embed(interaction, "embed.buttons", max=MAX_BUTTONS, count=len(state.buttons), buttons=buttons_text(state))


class ButtonBuilderView(PanelView):
    texts = "embed.buttons"

    def __init__(self, state, hub, back_target):
        super().__init__(back_target=back_target)
        self.state = state
        self.hub = hub
        if state.buttons:
            self.add_item(RemoveItemSelect(state.buttons, "embed.buttons.remove_placeholder"))

    def rebuilt(self):
        return ButtonBuilderView(self.state, self.hub, self.back_target)

    def screen_embed(self, interaction):
        return buttons_embed(interaction, self.state)

    async def show(self, interaction):
        embed, view = builder_screen(interaction, self)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Добавить кнопку", emoji="➕", style=discord.ButtonStyle.success)
    async def add(self, interaction, button):
        if len(self.state.buttons) >= MAX_BUTTONS:
            await say(interaction, "embed.buttons.too_many", max=MAX_BUTTONS)
            return
        await interaction.response.send_modal(ButtonLabelModal(self.state, self))

    @discord.ui.button(label="Убрать последнюю", emoji="➖", style=discord.ButtonStyle.secondary)
    async def remove_last(self, interaction, button):
        if self.state.buttons:
            self.state.buttons.pop()
        await self.show(interaction)

    @discord.ui.button(label="Готово", emoji="✅", style=discord.ButtonStyle.primary)
    async def done(self, interaction, button):
        await back_to_hub(interaction, self.hub)


class ButtonLabelModal(Modal, title="ТЕКСТ КНОПКИ"):
    texts = "embed.button_label_modal"

    label_input = discord.ui.TextInput(label="Текст кнопки", max_length=80)
    emoji_input = discord.ui.TextInput(label="Emoji", required=False, max_length=100)

    def __init__(self, state, builder_view):
        super().__init__()
        self.state = state
        self.builder_view = builder_view

    async def on_submit(self, interaction):
        if not valid_emoji(self.emoji_input.value):
            await say(interaction, "embed.bad_emoji")
            return
        pending = {"label": self.label_input.value, "emoji": self.emoji_input.value.strip() or None}
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "embed.button_color"),
            view=ButtonColorView(self.state, pending, self.builder_view,
                                 back_target=builder_screen(interaction, self.builder_view)),
        )


class ButtonColorView(PanelView):
    def __init__(self, state, pending, builder_view, back_target=None):
        super().__init__(back_target=back_target)
        self.state = state
        self.pending = pending
        self.builder_view = builder_view
        for key, style in BUTTON_STYLES.items():
            btn = discord.ui.Button(
                label=t(f"embed.button_color.{key}")[:80],
                style=style if key != "link" else discord.ButtonStyle.secondary,
            )

            async def cb(interaction, key=key):
                self.pending["style"] = key
                if key == "link":
                    # ссылке действие не нужно — сразу спрашиваем URL
                    self.pending["action_key"] = None
                    await interaction.response.send_modal(ActionValueModal(self.state, self.pending, "buttons", self.builder_view))
                    return
                await interaction.response.edit_message(
                    embed=panel_embed(interaction, "embed.button_action"),
                    view=action_pick_view(interaction, self.state, self.pending, "buttons", self.builder_view),
                )

            btn.callback = cb
            self.add_item(btn)


# ============================================================
# ФИНАЛ — живой Message Build
# ============================================================

async def finish_message_build(interaction, state):
    if interaction.guild is None:
        await say(interaction, "common.only_in_guild")
        return
    if not state.content.strip() and not state.buttons and not state.interactive \
            and not any(embed_has_content(e) for e in state.embeds):
        await say(interaction, "embed.save_empty")
        return
    for n, data in enumerate(state.embeds, 1):
        length = len(build_discord_embed(data))
        if length > EMBED_TOTAL_LIMIT:
            await say(interaction, "embed.too_long_n", n=n, length=length, max=EMBED_TOTAL_LIMIT)
            return

    fields = dict(
        name=state.name,
        content=state.content,
        embeds_json=json.dumps(state.embeds, ensure_ascii=False),
        buttons_json=json.dumps(state.buttons, ensure_ascii=False),
        visibility=state.visibility,
        category=state.category,
        allowed_role_ids_json=json.dumps(state.visibility_roles),
        visibility_levels_json=json.dumps(state.visibility_levels),
        interactive_json=json.dumps(state.interactive, ensure_ascii=False),
    )
    if state.build_id is not None:
        row = get_message_build(state.build_id)
        if not row or not can_manage_build(interaction, row):
            await say(interaction, "embed.edit_denied")
            return
        from build_tools import remember_version
        remember_version(state.build_id, interaction.user.id)  # прежний вид — в историю
        update_message_build(state.build_id, **fields)
        build_id = state.build_id
        core.audit(interaction, "build.updated", "build", build_id)
        key = "embed.saved_build.updated"
    else:
        build_id = save_message_build(guild_id=interaction.guild.id, owner_id=interaction.user.id, **fields)
        core.audit(interaction, "build.created", "build", build_id)
        key = "embed.saved_build.text"

    await interaction.response.edit_message(
        content=None,  # убираем подсказку импорта, если была
        embed=panel_embed(interaction, "embed.saved_build", description=t(
            key, id=build_id, embeds=len(state.embeds), buttons=len(state.buttons),
            sent=len(get_sent_instances(build_id)),
        )),
        view=MessageBuildFinalView(build_id, back_target=None)
    )


SEND_CHANNEL_TYPES = [
    discord.ChannelType.text, discord.ChannelType.news, discord.ChannelType.forum,
    discord.ChannelType.public_thread, discord.ChannelType.private_thread, discord.ChannelType.news_thread,
]


async def send_build(interaction, build_id, channels):
    """-> (отправлено в, ошибки). Канал, ветка или форум (в форуме — новый пост)."""
    import live

    sent, failed = [], []
    for channel in channels:
        real_channel = interaction.guild.get_channel_or_thread(channel.id)
        if real_channel is None:
            failed.append(t("embed.send.channel_missing", channel=f"<#{channel.id}>"))
            continue
        message, error = await live.deliver(interaction.guild, interaction.user, build_id, real_channel)
        if error:
            failed.append(error)
        if message is not None:
            sent.append(message.channel.mention)
            core.audit(interaction, "build.sent", "build", build_id, f"channel={message.channel.id}")
    return sent, failed


class MessageBuildFinalView(PanelView):
    texts = "embed.final"

    def __init__(self, build_id, back_target):
        super().__init__(back_target=back_target)
        self.build_id = build_id

    async def _row(self, interaction, manage=False):
        row = get_message_build(self.build_id)
        if not row or row[1] != interaction.guild.id or not build_visible(interaction, row):
            await say(interaction, "embed.build_not_found")
            return None
        if manage and not can_manage_build(interaction, row):
            await say(interaction, "embed.edit_denied")
            return None
        return row

    @discord.ui.button(label="Предпросмотр", emoji="👁️", style=discord.ButtonStyle.secondary)
    async def preview(self, interaction, button):
        if not await self._row(interaction):
            return
        # Ровно так, как сообщение уйдёт в канал: каждый embed отдельно.
        import live
        parts = await live.parts_for(interaction.guild, self.build_id, user=interaction.user)
        if not parts:
            await say(interaction, "embed.build_empty")
            return
        try:
            for index, part in enumerate(parts):
                send = interaction.response.send_message if index == 0 else interaction.followup.send
                await send(ephemeral=True, allowed_mentions=discord.AllowedMentions.none(), **part)
        except discord.HTTPException as error:
            if interaction.response.is_done():
                await interaction.followup.send(t("embed.preview_error", error=error), ephemeral=True)
            else:
                await say(interaction, "embed.preview_error", error=error)

    @discord.ui.button(label="Отправить", emoji="📤", style=discord.ButtonStyle.success)
    async def send(self, interaction, button):
        if not await self._row(interaction):
            return
        view = PanelView(timeout=300)
        select = discord.ui.ChannelSelect(
            placeholder=t("embed.send.placeholder")[:150],
            channel_types=SEND_CHANNEL_TYPES,
            min_values=1, max_values=5,
        )

        async def selected(i):
            # отправка в несколько каналов может занять дольше 3 секунд
            await i.response.defer(ephemeral=True, thinking=True)
            sent, failed = await send_build(i, self.build_id, select.values)
            lines = []
            if sent:
                lines.append(t("embed.send.sent", channels=", ".join(sent)))
            if failed:
                lines.append(t("embed.send.failed", errors="\n".join(failed)))
            await i.followup.send("\n\n".join(lines), ephemeral=True)

        select.callback = selected
        view.add_item(select)
        await interaction.response.send_message(t("embed.send.prompt"), view=view, ephemeral=True)

    @discord.ui.button(label="Редактировать", emoji="✏️", style=discord.ButtonStyle.primary)
    async def edit(self, interaction, button):
        row = await self._row(interaction, manage=True)
        if not row:
            return
        state = EmbedState.from_build(row)
        await interaction.response.edit_message(
            embed=render_active_preview(state),
            view=EmbedEditorView(state, back_target=(interaction.message.embeds[0], self)),
        )

    @discord.ui.button(label="Обновить отправленные", emoji="🔄", style=discord.ButtonStyle.secondary)
    async def resync(self, interaction, button):
        if not await self._row(interaction, manage=True):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        updated, removed, added, missing = await resync_instances(interaction, self.build_id)
        if not (updated or removed or added or missing):
            await interaction.followup.send(t("embed.resync.none"), ephemeral=True)
            return
        core.audit(interaction, "build.resynced", "build", self.build_id, f"updated={updated}")
        await interaction.followup.send(
            t("embed.resync.done", updated=updated, removed=removed, added=added, missing=missing), ephemeral=True,
        )

    @discord.ui.button(label="Сохранить как шаблон", emoji="💾", style=discord.ButtonStyle.secondary, row=1)
    async def save_template_button(self, interaction, button):
        if not await self._row(interaction):
            return
        await interaction.response.send_modal(SaveAsTemplateModal(self.build_id))

    @discord.ui.button(label="Экспорт JSON", emoji="📤", style=discord.ButtonStyle.secondary, row=1)
    async def export_json(self, interaction, button):
        from build_tools import send_export
        row = await self._row(interaction)
        if row:
            await send_export(interaction, row)

    @discord.ui.button(label="История", emoji="🕘", style=discord.ButtonStyle.secondary, row=1)
    async def history(self, interaction, button):
        from build_tools import VersionsView
        if not await self._row(interaction):
            return
        await interaction.response.edit_message(
            content=None,
            embed=panel_embed(interaction, "build_tools.versions"),
            view=VersionsView(interaction, self.build_id, back_target=(interaction.message.embeds[0], self)),
        )

    async def _automation(self, interaction, screen, view_factory):
        import automation
        row = await self._row(interaction)
        if not row:
            return
        if not automation.can_automate(interaction, row):
            await say(interaction, "automation.denied")
            return
        back = (interaction.message.embeds[0], self)
        await interaction.response.edit_message(content=None, embed=screen(interaction, self.build_id),
                                                view=view_factory(self.build_id, back_target=back))

    @discord.ui.button(label="Расписание", emoji="🗓️", style=discord.ButtonStyle.secondary, row=2)
    async def schedule(self, interaction, button):
        import automation
        await self._automation(interaction, automation.schedules_embed, automation.SchedulesView)

    @discord.ui.button(label="Триггеры", emoji="⚡", style=discord.ButtonStyle.secondary, row=2)
    async def triggers(self, interaction, button):
        import automation
        await self._automation(interaction, automation.triggers_embed, automation.TriggersView)

    @discord.ui.button(label="Настройки", emoji="⚙️", style=discord.ButtonStyle.secondary, row=2)
    async def settings(self, interaction, button):
        import automation
        await self._automation(interaction, automation.settings_embed, automation.BuildSettingsView)

    @discord.ui.button(label="Удалить", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def delete(self, interaction, button):
        row = await self._row(interaction, manage=True)
        if not row:
            return
        if delete_message_build(self.build_id):
            core.audit(interaction, "build.deleted", "build", self.build_id, row[3])
            await interaction.response.edit_message(embed=panel_embed(interaction, "embed.deleted"), view=None)
        else:
            await say(interaction, "embed.build_not_found")


def _group_sends(instances):
    import live
    return live._group_sends(instances)


async def resync_instances(interaction, build_id):
    import live
    return await live.resync_build(interaction.guild, build_id)


class SaveAsTemplateModal(Modal, title="СОХРАНИТЬ КАК ШАБЛОН"):
    texts = "embed.save_template_modal"

    name_input = discord.ui.TextInput(label="Название шаблона", max_length=100)
    category_input = discord.ui.TextInput(label="Категория", required=False, max_length=50)

    def __init__(self, build_id):
        super().__init__()
        self.build_id = build_id

    async def on_submit(self, interaction):
        row = get_message_build(self.build_id)
        if not row or not build_visible(interaction, row):
            await say(interaction, "embed.build_not_found")
            return
        payload = {
            "content": row[4] or "",
            "embeds": _json(row[5], []),
            "buttons": _json(row[6], []),
            "interactive": _json(row[11], None),
        }
        visibility = row[7] if row[7] in ("public", "private") else "private"
        tid = save_template(
            guild_id=interaction.guild.id,
            owner_id=interaction.user.id,
            name=self.name_input.value.strip(),
            template_type="message",
            payload_json=json.dumps(payload, ensure_ascii=False),
            visibility=visibility,
            category=self.category_input.value.strip() or "general",
            allowed_role_ids_json=row[9],
        )
        core.audit(interaction, "template.created", "template", tid, f"from build {self.build_id}")
        await say(interaction, "embed.save_template_modal.done", id=tid)


# ============================================================
# КОМАНДЫ /embed и /messages
# ============================================================

def register_embed(bot):
    @bot.tree.command(name="embed", description="Создать Message Build с embed'ами и интерактивом")
    @discord.app_commands.guild_only()
    @discord.app_commands.describe(
        from_message="Собрать build из готового сообщения: ссылка или ID",
        file="Импорт build'а из JSON-файла (экспорт этого бота)",
    )
    async def embed_command(interaction, from_message: str = None, file: discord.Attachment = None):
        if not await core.require_command_access(interaction, "embed"):
            return
        if from_message or file:
            from build_tools import import_from_message, import_from_file
            await interaction.response.defer(ephemeral=True, thinking=True)
            if file:
                await import_from_file(interaction, file)
            else:
                await import_from_message(interaction, from_message)
            return
        await interaction.response.send_message(
            embed=panel_embed(interaction, "embed.home"),
            view=EmbedHomeView(interaction.guild.id, interaction.user.id),
            ephemeral=True,
        )

    @bot.tree.command(name="messages", description="Управление сохранёнными Message Build")
    @discord.app_commands.guild_only()
    async def messages_command(interaction):
        if not await core.require_command_access(interaction, "messages"):
            return
        await interaction.response.send_message(
            embed=panel_embed(interaction, "messages.home"),
            view=SavedBuildsListView(interaction),
            ephemeral=True,
        )
