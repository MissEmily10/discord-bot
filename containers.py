"""
containers.py
=============
Контейнеры — новый вид сообщений Discord (Components V2) вместо embed'ов.

Что это. Сообщение собирается не из embed'ов, а из блоков-компонентов:
- Container — карточка с цветной полосой слева (accent), как embed, но внутри
  можно смешивать любые блоки в любом порядке; можно скрыть целиком под спойлер;
- TextDisplay — текст с Markdown (заголовки #, ##, ###, мелкий текст -#, списки,
  цитаты, ссылки, упоминания, время <t:…>);
- Section — текст с «аксессуаром» справа: миниатюрой (Thumbnail) или кнопкой;
- MediaGallery — галерея до 10 картинок/гифок/видео сеткой (не одна картинка,
  как в embed'е), у каждой — подпись и спойлер;
- Separator — разделитель: линия или просто отступ, маленький или большой;
- ActionRow — ряд кнопок или меню прямо внутри карточки.
Лимиты Discord: до 40 компонентов на сообщение (вместе с вложенными), весь
текст — до 4000 символов; content и embed'ы в таком сообщении не бывают.
Сообщение, раз отправленное контейнерами, обратно в embed'ы не переводится.

Как у нас. В /embed в начале выбирают «Обычный embed» или «Контейнер».
Контейнер собирается из блоков (container_editor.py): build с layout =
"container", каждый элемент embeds — карточка {color, spoiler, blocks}, а блок —
- {"type": "text", "title", "text"} — текст, заголовок крупно (##);
- {"type": "section", "title", "text", "thumbnail"} — текст и картинка справа;
- {"type": "gallery", "images": [...]} — галерея до 10 картинок;
- {"type": "separator", "line": bool, "big": bool} — линия или пустой отступ.
Кнопки и меню сообщения встают внутрь последней карточки. Старые build'ы
с layout = "container" без блоков собираются из полей embed'а (автор → мелкий
текст, заголовок → ##, миниатюра справа, поля, картинка + галерея, подпись).
"""

import discord

MAX_COMPONENTS = 40
MAX_TEXT = 4000
MAX_GALLERY = 10
ZERO = "​"


def _clean(text):
    text = str(text or "").strip()
    return "" if text in ("", ZERO) else text


def extras_of(item):
    """Настройки контейнера у embed'а (из build'а): галерея, спойлер, разделители."""
    item = item if isinstance(item, dict) else {}
    gallery = [str(u).strip() for u in (item.get("gallery") or []) if str(u or "").strip().startswith(("http", "attachment://"))]
    return {"gallery": gallery[:MAX_GALLERY - 1], "spoiler": bool(item.get("spoiler")),
            "dividers": item.get("dividers", True) is not False}


BLOCK_TYPES = ("text", "section", "gallery", "separator")


def block_text(block):
    title, text = _clean(block.get("title")), _clean(block.get("text"))
    return "\n".join(part for part in (f"## {title}" if title else "", text) if part)


def _from_blocks(card):
    """Карточка из блоков (собранная в редакторе контейнеров) -> Container."""
    children = []
    for block in card.get("blocks") or []:
        kind = block.get("type")
        if kind == "text" and block_text(block):
            children.append(discord.ui.TextDisplay(block_text(block)))
        elif kind == "section":
            text, thumb = block_text(block), str(block.get("thumbnail") or "")
            if text and thumb.startswith(("http", "attachment://")):
                children.append(discord.ui.Section(discord.ui.TextDisplay(text), accessory=discord.ui.Thumbnail(thumb)))
            elif text:
                children.append(discord.ui.TextDisplay(text))
        elif kind == "gallery":
            images = [str(u) for u in block.get("images") or [] if str(u).startswith(("http", "attachment://"))]
            if images:
                children.append(discord.ui.MediaGallery(*(discord.MediaGalleryItem(u) for u in images[:MAX_GALLERY])))
        elif kind == "separator":
            children.append(discord.ui.Separator(
                visible=block.get("line", True) is not False,
                spacing=discord.SeparatorSpacing.large if block.get("big") else discord.SeparatorSpacing.small))
    if not children:
        children.append(discord.ui.TextDisplay(ZERO))
    try:
        colour = int(card.get("color")) if card.get("color") is not None else None
    except (TypeError, ValueError):
        colour = None
    return discord.ui.Container(*children, accent_colour=colour, spoiler=bool(card.get("spoiler")))


def blocks_from_embed(item):
    """Старый embed -> блоки (открыли в редакторе контейнеров build, собранный из embed'а)."""
    blocks = []
    head = "\n".join(part for part in (f"-# {item['author_name']}" if item.get("author_name") else "",
                                         _clean(item.get("description"))) if part)
    if item.get("title") or head:
        kind = "section" if item.get("thumbnail") else "text"
        block = {"type": kind, "title": _clean(item.get("title")), "text": head}
        if kind == "section":
            block["thumbnail"] = item["thumbnail"]
        blocks.append(block)
    for field in item.get("fields") or []:
        blocks.append({"type": "text", "title": "", "text": f"**{_clean(field.get('name'))}**\n{_clean(field.get('value'))}"})
    images = ([item["image"]] if item.get("image") else []) + list(item.get("gallery") or [])
    if images:
        blocks.append({"type": "gallery", "images": images[:MAX_GALLERY]})
    if item.get("footer_text"):
        blocks.append({"type": "separator", "line": True, "big": False})
        blocks.append({"type": "text", "title": "", "text": f"-# {item['footer_text']}"})
    return blocks


def _container(embed, extra):
    children = []
    head = []
    if embed.author and embed.author.name:
        name = embed.author.name
        head.append("-# " + (f"[{name}]({embed.author.url})" if embed.author.url else name))
    title = _clean(embed.title)
    if title:
        head.append("## " + (f"[{title}]({embed.url})" if embed.url else title))
    description = _clean(embed.description)
    if description:
        head.append(description)
    text = "\n".join(head)
    thumb = embed.thumbnail.url if embed.thumbnail else None
    if text and thumb:
        children.append(discord.ui.Section(discord.ui.TextDisplay(text), accessory=discord.ui.Thumbnail(thumb)))
    elif text:
        children.append(discord.ui.TextDisplay(text))
    elif thumb:
        children.append(discord.ui.MediaGallery(discord.MediaGalleryItem(thumb)))
    if embed.fields:
        if extra["dividers"] and children:
            children.append(discord.ui.Separator())
        blocks = []
        for field in embed.fields:
            name, value = _clean(field.name), _clean(field.value)
            blocks.append("\n".join(part for part in (f"**{name}**" if name else "", value) if part))
        children.append(discord.ui.TextDisplay("\n\n".join(b for b in blocks if b) or ZERO))
    media = ([embed.image.url] if embed.image and embed.image.url else []) + extra["gallery"]
    if media:
        children.append(discord.ui.MediaGallery(*(discord.MediaGalleryItem(url) for url in media[:MAX_GALLERY])))
    footer = []
    if embed.footer and embed.footer.text:
        footer.append(embed.footer.text)
    if embed.timestamp:
        footer.append(f"<t:{int(embed.timestamp.timestamp())}:f>")
    if footer:
        if extra["dividers"] and children:
            children.append(discord.ui.Separator())
        children.append(discord.ui.TextDisplay("-# " + " · ".join(footer)))
    if not children:
        children.append(discord.ui.TextDisplay(ZERO))
    colour = embed.colour.value if embed.colour else None
    return discord.ui.Container(*children, accent_colour=colour, spoiler=extra["spoiler"])


def _rows(view):
    """Кнопки и меню обычного View -> ряды ActionRow (как Discord раскладывает их по row)."""
    if view is None:
        return []
    items = list(view.children)
    for item in items:  # из старого View — в новый
        view.remove_item(item)
    rows, current, width = {}, None, 0
    order = []
    for item in items:
        key = item.row
        if key is None:
            size = getattr(item, "width", 1)
            if current is None or width + size > 5:
                current = ("auto", len(order))
                order.append(current)
                width = 0
            width += size
            key = current
        elif key not in order:
            order.append(key)
        rows.setdefault(key, []).append(item)
    built = []
    for key in order:
        row = discord.ui.ActionRow()
        for item in rows[key]:
            item.row = None
            row.add_item(item)
        built.append(row)
    return built


def build_layout(content, embeds, view=None, extras=None):
    """Текст + embed'ы + кнопки -> LayoutView: каждый embed — карточка-контейнер."""
    layout = discord.ui.LayoutView(timeout=None)
    layout._purr_overflow = False
    extras = list(extras or [])
    try:  # discord.py сам не пускает больше 40 блоков — тогда отметка, что не влезло
        if _clean(content):
            layout.add_item(discord.ui.TextDisplay(content))
        containers = []
        for index, embed in enumerate(embeds):
            item = extras[index] if index < len(extras) else {}
            container = _from_blocks(item) if isinstance(item, dict) and "blocks" in item else _container(embed, extras_of(item))
            layout.add_item(container)
            containers.append(container)
        for row in _rows(view):
            (containers[-1] if containers else layout).add_item(row)
    except ValueError:
        layout._purr_overflow = True
    # для картинки-предпросмотра: как выглядело бы теми же embed'ами
    layout._purr_preview = {"content": content or "", "embeds": [e.to_dict() for e in embeds]}
    return layout


def problem(layout):
    """-> None или (ключ ошибки, параметры): лимиты Discord на сообщение контейнерами."""
    count = layout.total_children_count
    if getattr(layout, "_purr_overflow", False) or count > MAX_COMPONENTS:
        return "containers.too_many", {"count": count, "max": MAX_COMPONENTS}
    length = layout.content_length()
    if length > MAX_TEXT:
        return "containers.too_long", {"length": length, "max": MAX_TEXT}
    return None


def is_layout(view):
    return isinstance(view, discord.ui.LayoutView)
