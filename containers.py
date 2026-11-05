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

Как у нас. Build остаётся тем же (embed'ы в редакторе /embed и на сайте), а вид
«Контейнеры» (layout = "container") при отправке собирает из каждого embed'а
карточку: автор → мелкий текст, заголовок → ## (со ссылкой), описание,
миниатюра — справа от текста, поля — блоком, картинка + галерея, подпись и
время — мелким текстом под разделителем; кнопки и меню — внутри последней
карточки. У embed'а есть настройки контейнера: галерея (ещё картинки),
спойлер, разделители (кнопка «Контейнер» в редакторе).
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
            container = _container(embed, extras_of(extras[index] if index < len(extras) else {}))
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
