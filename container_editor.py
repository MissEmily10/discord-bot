"""
container_editor.py
===================
Конструктор контейнера в /embed — сообщение нового вида Discord (Components V2)
собирается из блоков, по одному, сверху вниз:

- 📝 Текст — заголовок (крупно) и текст с разметкой;
- 🖼️ Текст и картинка справа — то же, плюс миниатюра справа от текста;
- 🎞️ Галерея — до 10 картинок сеткой;
- ➖ Разделитель — линия или пустой отступ, маленький или большой;
- 🎨 Оформление карточки — цвет полосы слева и «скрыть под спойлер»;
- ➕ Ещё карточка — несколько карточек в одном сообщении.

Блок можно выбрать в меню и изменить, сдвинуть выше / ниже или удалить.
«👁 Как выглядит» присылает настоящее сообщение-контейнер (видно только тебе).
«✅ Готово» — дальше как у embed'а: кнопки, меню, сохранить, отправить
(кнопки встают внутрь последней карточки).

Хранится как обычный Message Build: layout = "container", embeds — карточки
{color, spoiler, blocks} (формат блоков — в containers.py).
"""

import discord

import core
from core import Modal, PanelView, t, panel_embed

MAX_CARDS = 10
ICONS = {"text": "📝", "section": "🖼️", "gallery": "🎞️", "separator": "➖"}


def new_card():
    return {"color": core.embed_color().value, "spoiler": False, "blocks": []}


def is_container(state):
    return getattr(state, "layout", None) == "container"


def prepare(state):
    """Карточки из блоков: старые embed'ы (без блоков) переводятся в блоки один раз."""
    import containers
    cards = []
    for item in state.embeds or []:
        if not isinstance(item, dict):
            continue
        if "blocks" not in item:
            item = {"color": item.get("color", core.embed_color().value), "spoiler": bool(item.get("spoiler")),
                    "blocks": containers.blocks_from_embed(item)}
        cards.append(item)
    state.embeds = cards or [new_card()]
    state.active_index = min(state.active_index, len(state.embeds) - 1)


def _short(text, size=50):
    text = " ".join(str(text or "").split())
    return text if len(text) <= size else text[:size - 1] + "…"


def block_line(block):
    kind = block.get("type")
    if kind in ("text", "section"):
        label = _short(block.get("title") or block.get("text")) or "—"
        name = t(f"container.block.{kind}")
        return f"{ICONS[kind]} {name} — «{label}»"
    if kind == "gallery":
        return f"{ICONS[kind]} {t('container.block.gallery')} — {len(block.get('images') or [])} шт."
    if kind == "separator":
        look = t("container.sep.line" if block.get("line", True) is not False else "container.sep.space")
        size = t("container.sep.big" if block.get("big") else "container.sep.small")
        return f"{ICONS[kind]} {t('container.block.separator')} — {look}, {size}"
    return "—"


def editor_embed(state):
    """Панель конструктора: карточка как список блоков, цвет — полосой, первая картинка — для наглядности."""
    card = state.active_embed
    blocks = card.get("blocks") or []
    lines = [f"**{n}.** {block_line(block)}" for n, block in enumerate(blocks, 1)]
    title = t("container.editor.title", n=state.active_index + 1, total=len(state.embeds))
    description = "\n".join(lines) if lines else t("container.editor.empty")
    if card.get("spoiler"):
        description += "\n\n" + t("container.editor.spoiler_on")
    embed = panel_embed(None, "container.editor", title=title, description=description[:3000],
                        hide=("delete_card",) if len(state.embeds) <= 1 else ())
    try:
        embed.colour = discord.Colour(int(card.get("color")))
    except (TypeError, ValueError):
        pass
    thumb = next((b.get("thumbnail") for b in blocks if b.get("type") == "section" and b.get("thumbnail")), None)
    if thumb:
        embed.set_thumbnail(url=thumb)
    image = next((b["images"][0] for b in blocks if b.get("type") == "gallery" and b.get("images")), None)
    if image:
        embed.set_image(url=image)
    return embed


class BlockSelect(discord.ui.Select):
    """Выбрать блок: изменить, сдвинуть, удалить."""

    def __init__(self, state):
        blocks = state.active_embed.get("blocks") or []
        super().__init__(placeholder=t("container.editor.pick_block")[:150], row=0, options=[
            discord.SelectOption(label=f"{n}. {block_line(block)}"[:100], value=str(n - 1))
            for n, block in enumerate(blocks[:25], 1)])
        self.state = state

    async def callback(self, interaction):
        index = int(self.values[0])
        await interaction.response.edit_message(
            embed=editor_embed(self.state),
            view=BlockActionsView(self.state, index, back_target=self.view.back_target))


class CardSelect(discord.ui.Select):
    def __init__(self, state):
        super().__init__(placeholder=t("container.editor.pick_card")[:150], row=1, options=[
            discord.SelectOption(label=t("container.editor.card_n", n=n)[:100], value=str(n - 1),
                                 default=n - 1 == state.active_index)
            for n in range(1, len(state.embeds) + 1)])
        self.state = state

    async def callback(self, interaction):
        self.state.active_index = int(self.values[0])
        await refresh(interaction, self.state, self.view.back_target)


async def refresh(interaction, state, back_target, edit=True):
    kwargs = dict(content=None, embeds=[editor_embed(state)], view=ContainerEditorView(state, back_target))
    if interaction.response.is_done():
        await interaction.edit_original_response(**kwargs)
    else:
        await interaction.response.edit_message(**kwargs)


def add_block(state, block, index=None):
    blocks = state.active_embed.setdefault("blocks", [])
    if index is None:
        blocks.append(block)
    else:
        blocks[index] = block


class ContainerEditorView(PanelView):
    texts = "container.editor"
    button_order = ("text", "section", "gallery", "separator", "done", "preview", "style", "add_card",
                    "delete_card", "meta")

    def __init__(self, state, back_target=None):
        super().__init__(back_target=back_target)
        self.state = state
        prepare(state)
        state.editor_back = state.editor_back or back_target
        if state.active_embed.get("blocks"):
            self.add_item(BlockSelect(state))
        if len(state.embeds) > 1:
            self.add_item(CardSelect(state))
        else:
            self.remove_item(self.delete_card)

    @discord.ui.button(label="Текст", emoji="📝", style=discord.ButtonStyle.primary, row=2)
    async def text(self, interaction, button):
        await interaction.response.send_modal(TextBlockModal(self.state, self.back_target))

    @discord.ui.button(label="Текст и картинка справа", emoji="🖼️", style=discord.ButtonStyle.primary, row=2)
    async def section(self, interaction, button):
        await interaction.response.send_modal(TextBlockModal(self.state, self.back_target, section=True))

    @discord.ui.button(label="Галерея", emoji="🎞️", style=discord.ButtonStyle.primary, row=2)
    async def gallery(self, interaction, button):
        await interaction.response.send_modal(GalleryModal(self.state, self.back_target))

    @discord.ui.button(label="Разделитель", emoji="➖", style=discord.ButtonStyle.secondary, row=2)
    async def separator(self, interaction, button):
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "container.separator", hints=False),
            view=SeparatorView(self.state, back_target=self.back_target))

    @discord.ui.button(label="Готово", emoji="✅", style=discord.ButtonStyle.success, row=2)
    async def done(self, interaction, button):
        import embed_module
        problem = embed_module.state_problem(self.state)
        if problem:
            key, params = problem
            await embed_module.say(interaction, key, **params)
            return
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "embed.interactive",
                              description=embed_module.interactive_summary(self.state)),
            view=embed_module.InteractiveHubView(self.state, back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Как выглядит", emoji="👁️", style=discord.ButtonStyle.secondary, row=3)
    async def preview(self, interaction, button):
        await send_preview(interaction, self.state)

    @discord.ui.button(label="Оформление карточки", emoji="🎨", style=discord.ButtonStyle.secondary, row=3)
    async def style(self, interaction, button):
        await interaction.response.send_modal(StyleModal(self.state, self.back_target))

    @discord.ui.button(label="Ещё карточка", emoji="➕", style=discord.ButtonStyle.secondary, row=3)
    async def add_card(self, interaction, button):
        if len(self.state.embeds) >= MAX_CARDS:
            await core_say(interaction, "container.too_many_cards", max=MAX_CARDS)
            return
        self.state.embeds.append(new_card())
        self.state.active_index = len(self.state.embeds) - 1
        await refresh(interaction, self.state, self.back_target)

    @discord.ui.button(label="Удалить карточку", emoji="🗑️", style=discord.ButtonStyle.danger, row=3)
    async def delete_card(self, interaction, button):
        if len(self.state.embeds) <= 1:
            return
        self.state.embeds.pop(self.state.active_index)
        self.state.active_index = max(0, self.state.active_index - 1)
        await refresh(interaction, self.state, self.back_target)

    @discord.ui.button(label="Название и текст сверху", emoji="🏷️", style=discord.ButtonStyle.secondary, row=3)
    async def meta(self, interaction, button):
        import embed_module
        await interaction.response.send_modal(embed_module.BuildMetaModal(self.state, self))

    async def refresh(self, interaction):  # для общих окон редактора (название и текст сверху)
        await refresh(interaction, self.state, self.back_target)


async def core_say(interaction, key, **params):
    from actions import say
    await say(interaction, key, **params)


async def send_preview(interaction, state):
    """Настоящее сообщение-контейнер, видно только тебе (кнопки — для вида, без действий)."""
    import containers
    from actions import build_discord_embed
    import embed_module
    data = embed_module.state_source(state)
    from actions import build_components, visible_items
    view = build_components("b", state.build_id or 0, data["buttons"], data["interactive"])
    items = visible_items(data, view)
    layout = containers.build_layout(data["content"], [build_discord_embed(e) for e in items], view, items)
    problem = containers.problem(layout)
    if problem:
        key, params = problem
        await core_say(interaction, key, **params)
        return
    try:
        await interaction.response.send_message(view=layout, ephemeral=True)
    except discord.HTTPException as error:
        await core_say(interaction, "embed.preview_error", error=error)


class BlockActionsView(PanelView):
    """Выбранный блок: изменить, выше, ниже, удалить."""

    def __init__(self, state, index, back_target=None):
        super().__init__(back_target=None, timeout=900)
        self.state, self.index, self.editor_back = state, index, back_target
        blocks = state.active_embed.get("blocks") or []
        if index == 0:
            self.remove_item(self.up)
        if index >= len(blocks) - 1:
            self.remove_item(self.down)
        if blocks and blocks[index].get("type") == "separator":
            self.edit.label = t("container.block.change_separator")[:80]

    @property
    def blocks(self):
        return self.state.active_embed.setdefault("blocks", [])

    @discord.ui.button(label="Изменить", emoji="✏️", style=discord.ButtonStyle.primary)
    async def edit(self, interaction, button):
        block = self.blocks[self.index]
        kind = block.get("type")
        if kind in ("text", "section"):
            await interaction.response.send_modal(TextBlockModal(self.state, self.editor_back, section=kind == "section",
                                                                 index=self.index))
        elif kind == "gallery":
            await interaction.response.send_modal(GalleryModal(self.state, self.editor_back, index=self.index))
        else:
            await interaction.response.edit_message(
                embed=panel_embed(interaction, "container.separator", hints=False),
                view=SeparatorView(self.state, back_target=self.editor_back, index=self.index))

    @discord.ui.button(label="Выше", emoji="⬆️", style=discord.ButtonStyle.secondary)
    async def up(self, interaction, button):
        blocks = self.blocks
        blocks[self.index - 1], blocks[self.index] = blocks[self.index], blocks[self.index - 1]
        await refresh(interaction, self.state, self.editor_back)

    @discord.ui.button(label="Ниже", emoji="⬇️", style=discord.ButtonStyle.secondary)
    async def down(self, interaction, button):
        blocks = self.blocks
        blocks[self.index + 1], blocks[self.index] = blocks[self.index], blocks[self.index + 1]
        await refresh(interaction, self.state, self.editor_back)

    @discord.ui.button(label="Удалить", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def remove(self, interaction, button):
        self.blocks.pop(self.index)
        await refresh(interaction, self.state, self.editor_back)

    @discord.ui.button(label="Назад", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        await refresh(interaction, self.state, self.editor_back)


class SeparatorView(PanelView):
    """Какой разделитель: линия или пустой отступ, маленький или большой."""

    texts = "container.separator"

    def __init__(self, state, back_target=None, index=None):
        super().__init__(back_target=None, timeout=900)
        self.state, self.editor_back, self.index = state, back_target, index

    async def put(self, interaction, line, big):
        add_block(self.state, {"type": "separator", "line": line, "big": big}, self.index)
        await refresh(interaction, self.state, self.editor_back)

    @discord.ui.button(label="Линия", emoji="➖", style=discord.ButtonStyle.primary)
    async def line_small(self, interaction, button):
        await self.put(interaction, True, False)

    @discord.ui.button(label="Линия с большим отступом", emoji="〰️", style=discord.ButtonStyle.secondary)
    async def line_big(self, interaction, button):
        await self.put(interaction, True, True)

    @discord.ui.button(label="Пустой отступ", emoji="⬜", style=discord.ButtonStyle.secondary)
    async def space_small(self, interaction, button):
        await self.put(interaction, False, False)

    @discord.ui.button(label="Большой пустой отступ", emoji="🔳", style=discord.ButtonStyle.secondary)
    async def space_big(self, interaction, button):
        await self.put(interaction, False, True)

    @discord.ui.button(label="Назад", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        await refresh(interaction, self.state, self.editor_back)


def _check(interaction_value):
    from actions import check_url
    return check_url(interaction_value)


class TextBlockModal(Modal):
    """Блок «Текст» (или «Текст и картинка справа»): заголовок, текст, миниатюра."""

    def __init__(self, state, back_target, section=False, index=None):
        super().__init__(title=t("container.text_modal.section_title" if section else "container.text_modal.title")[:45])
        self.state, self.back_target, self.section, self.index = state, back_target, section, index
        block = (state.active_embed.get("blocks") or [])[index] if index is not None else {}
        self.title_input = discord.ui.TextInput(label=t("container.text_modal.heading")[:45], required=False,
                                                max_length=256, default=block.get("title") or None,
                                                placeholder=t("container.text_modal.heading_hint")[:100])
        self.text_input = discord.ui.TextInput(label=t("container.text_modal.text")[:45], required=not section,
                                               style=discord.TextStyle.paragraph, max_length=4000,
                                               default=block.get("text") or None,
                                               placeholder=t("container.text_modal.text_hint")[:100])
        self.add_item(self.title_input)
        self.add_item(self.text_input)
        if section:
            self.thumb_input = discord.ui.TextInput(label=t("container.text_modal.thumbnail")[:45], max_length=1000,
                                                    default=block.get("thumbnail") or None, placeholder="https://…")
            self.add_item(self.thumb_input)

    async def on_submit(self, interaction):
        block = {"type": "section" if self.section else "text", "title": self.title_input.value.strip(),
                 "text": self.text_input.value.strip()}
        if not block["title"] and not block["text"]:
            await core_say(interaction, "container.text_modal.empty")
            return
        if self.section:
            url, ok = _check(self.thumb_input.value)
            if not ok or not url:
                await core_say(interaction, "embed.bad_url", field=self.thumb_input.label,
                               value=self.thumb_input.value.strip()[:100])
                return
            block["thumbnail"] = url
        add_block(self.state, block, self.index)
        await refresh(interaction, self.state, self.back_target)


class GalleryModal(Modal):
    def __init__(self, state, back_target, index=None):
        super().__init__(title=t("container.gallery_modal.title")[:45])
        self.state, self.back_target, self.index = state, back_target, index
        block = (state.active_embed.get("blocks") or [])[index] if index is not None else {}
        self.images_input = discord.ui.TextInput(label=t("container.gallery_modal.images")[:45],
                                                 style=discord.TextStyle.paragraph, max_length=4000,
                                                 default="\n".join(block.get("images") or []) or None,
                                                 placeholder=t("container.gallery_modal.placeholder")[:100])
        self.add_item(self.images_input)

    async def on_submit(self, interaction):
        images = []
        for raw in self.images_input.value.split():
            url, ok = _check(raw)
            if not ok or not url:
                await core_say(interaction, "embed.bad_url", field=self.images_input.label, value=raw[:100])
                return
            images.append(url)
        if not images or len(images) > 10:
            await core_say(interaction, "container.gallery_modal.count", count=len(images))
            return
        add_block(self.state, {"type": "gallery", "images": images}, self.index)
        await refresh(interaction, self.state, self.back_target)


class StyleModal(Modal):
    """Оформление карточки: цвет полосы слева и «скрыть под спойлер»."""

    def __init__(self, state, back_target):
        super().__init__(title=t("container.style_modal.title")[:45])
        self.state, self.back_target = state, back_target
        card = state.active_embed
        self.color_input = discord.ui.TextInput(label=t("container.style_modal.color")[:45], required=False,
                                                max_length=8, default=f"#{int(card.get('color') or 0):06X}",
                                                placeholder="#B4EE3A")
        self.spoiler_input = discord.ui.TextInput(label=t("container.style_modal.spoiler")[:45], required=False,
                                                  max_length=3, default="да" if card.get("spoiler") else "нет")
        self.add_item(self.color_input)
        self.add_item(self.spoiler_input)

    async def on_submit(self, interaction):
        from embed_module import _parse_color
        card = self.state.active_embed
        raw = self.color_input.value.strip()
        if raw:
            color = _parse_color(raw)
            if color is None:
                await core_say(interaction, "container.style_modal.bad_color", value=raw[:20])
                return
            card["color"] = color
        card["spoiler"] = self.spoiler_input.value.strip().lower() in ("да", "yes", "д", "y", "+")
        await refresh(interaction, self.state, self.back_target)
