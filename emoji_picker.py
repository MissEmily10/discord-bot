"""
Выбор эмодзи для кнопки — экраном, а не полем в окне:
- два списка с дудлами бота (panel_art/emoji, 50 штук);
- список эмодзи этого сервера;
- «Своё…»: обычное эмодзи (😀), эмодзи этого или чужого сервера (<:имя:id>,
  :имя: или ID) или картинка PNG/JPG/GIF.

Чужие эмодзи и картинки бот кладёт в своё хранилище эмодзи (как media.py):
место на сервере они не занимают и не пропадут, если тот сервер удалит эмодзи.
"""

import hashlib
import io
import re

import discord

import core
from core import PanelView, t, panel_embed
from actions import reply, valid_emoji

UPLOAD_MAX_BYTES = 4 * 1024 * 1024   # исходная картинка
EMOJI_MAX_BYTES = 256 * 1024         # лимит Discord на эмодзи
EMOJI_SIDE = 128

NAMES_RU = {
    "shield": "щит", "crown": "корона", "star": "звезда", "heart": "сердце", "bolt": "молния",
    "mic": "микрофон", "headset": "наушники", "palette": "палитра", "gamepad": "геймпад",
    "megaphone": "рупор", "mail": "письмо", "chat": "чат", "sparkles": "искры", "flower": "цветок",
    "gem": "кристалл", "music": "ноты", "book": "книга", "cat": "котик", "camera": "фотик",
    "film": "плёнка", "eye": "глаз", "cherries": "ягоды", "moon": "луна", "sun": "солнце",
    "cloud": "облако", "fire": "огонь", "ghost": "призрак", "rocket": "ракета", "planet": "планета",
    "key": "ключ", "lock": "замок", "bell": "колокольчик", "gift": "подарок", "trophy": "кубок",
    "medal": "медаль", "flag": "флаг", "pencil": "карандаш", "brush": "кисть", "wrench": "гаечный ключ",
    "gear": "шестерёнка", "laptop": "ноутбук", "globe": "глобус", "hourglass": "песочные часы",
    "coffee": "кофе", "cake": "торт", "balloon": "шарик", "clover": "клевер", "mushroom": "гриб",
    "lollipop": "леденец", "dice": "кубик",
}
_CUSTOM = re.compile(r"<(a?):(\w{2,32}):(\d{15,25})>")
_COLON = re.compile(r":?([\w~]{2,32}):?")


def doodles():
    """[(имя, '<:имя:id>')] — дудлы, которые бот уже залил в свои эмодзи."""
    import form_packs
    return list(form_packs._doodles.items())


def _option(value, label, emoji=None):
    try:
        partial = discord.PartialEmoji.from_str(emoji or value)
    except (TypeError, ValueError):
        partial = None
    return discord.SelectOption(label=label[:100], value=value[:100], emoji=partial)


async def open_picker(interaction, on_pick, back_target=None):
    """
    Экран выбора. on_pick(interaction, эмодзи-строка | None) продолжает сценарий;
    отвечать ему лучше через embed_module.edit_panel — ответ может быть уже отложен.
    """
    view = EmojiPickerView(interaction, on_pick, back_target)
    embed = panel_embed(interaction, "emoji_picker")
    if interaction.response.is_done():
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        await interaction.response.edit_message(embed=embed, view=view)


class EmojiPickerView(PanelView):
    texts = "emoji_picker"

    def __init__(self, interaction, on_pick, back_target=None):
        super().__init__(back_target=back_target, timeout=900)
        self.on_pick = on_pick
        items = doodles()
        for row, chunk in enumerate((items[:25], items[25:50])):
            if not chunk:
                continue
            select = discord.ui.Select(
                placeholder=t("emoji_picker.doodles", part=row + 1)[:150], row=row,
                options=[_option(value, NAMES_RU.get(name.removeprefix("doodle_"), name)) for name, value in chunk],
            )
            select.callback = self._picked(select)
            self.add_item(select)
        guild_emojis = [emoji for emoji in getattr(interaction.guild, "emojis", []) if emoji.available]
        if guild_emojis:
            select = core.PagedSelect(
                placeholder=t("emoji_picker.server")[:150], row=2,
                options=[_option(str(emoji), emoji.name) for emoji in guild_emojis],
            )
            select.on_pick = self._picked(select)
            self.add_item(select)

    def _picked(self, select):
        async def callback(interaction):
            await self.on_pick(interaction, select.values[0])
        return callback

    @discord.ui.button(label="Своё…", emoji="✏️", style=discord.ButtonStyle.primary, row=3)
    async def custom(self, interaction, button):
        await interaction.response.send_modal(CustomEmojiModal(self.on_pick))

    @discord.ui.button(label="Без эмодзи", emoji="🚫", style=discord.ButtonStyle.secondary, row=3)
    async def no_emoji(self, interaction, button):
        await self.on_pick(interaction, None)


class CustomEmojiModal(discord.ui.Modal):
    def __init__(self, on_pick):
        super().__init__(title=t("emoji_picker.modal.title")[:45])
        self.on_pick = on_pick
        self.text_input = discord.ui.TextInput(required=False, max_length=100,
                                               placeholder=t("emoji_picker.modal.text.placeholder")[:100])
        self.file_input = discord.ui.FileUpload(required=False, max_values=1)
        self.add_item(discord.ui.Label(text=t("emoji_picker.modal.text")[:45],
                                       description=t("emoji_picker.modal.text_help")[:100], component=self.text_input))
        self.add_item(discord.ui.Label(text=t("emoji_picker.modal.file")[:45],
                                       description=t("emoji_picker.modal.file_help")[:100], component=self.file_input))

    async def on_submit(self, interaction):
        files = list(self.file_input.values or [])
        text = (self.text_input.value or "").strip()
        if files:
            emoji, problem = await from_upload(interaction, files[0])
        elif text:
            emoji, problem = await from_text(interaction, text)
        else:
            emoji, problem = None, "emoji_picker.error.empty"
        if problem:
            await reply(interaction, problem)
            return
        await self.on_pick(interaction, emoji)


async def from_text(interaction, text):
    """Обычное эмодзи, <:имя:id>, :имя: этого сервера или ID. -> (эмодзи, ключ ошибки)."""
    import media
    bot, guild = interaction.client, interaction.guild
    match = _CUSTOM.fullmatch(text)
    if match is None and text.isdigit():
        known = bot.get_emoji(int(text))
        text = str(known) if known else f"<:emoji:{text}>"
        match = _CUSTOM.fullmatch(text)
    if match is None:
        named = _COLON.fullmatch(text)
        if named and guild is not None:
            found = discord.utils.find(lambda e: e.name.lower() == named[1].lower(), guild.emojis)
            if found:
                return str(found), None
        if valid_emoji(text) and not any(ch.isascii() and ch.isalnum() for ch in text):
            return text, None
        return None, "emoji_picker.error.unknown"
    if media.usable(bot, int(match[3])):
        return text, None
    # чужой сервер: копия в хранилище эмодзи бота (скачивание — дольше 3 секунд)
    if not interaction.response.is_done():
        await interaction.response.defer()
    copied = await media.mirror_emoji(bot, text)
    return (copied, None) if copied else (None, "emoji_picker.error.copy_failed")


def prepare_image(data):
    """Любая картинка -> PNG 128×128 с прозрачными полями, не больше 256 КБ."""
    from PIL import Image
    picture = Image.open(io.BytesIO(data))
    picture.seek(0)
    picture = picture.convert("RGBA")
    picture.thumbnail((EMOJI_SIDE, EMOJI_SIDE), Image.LANCZOS)
    canvas = Image.new("RGBA", (EMOJI_SIDE, EMOJI_SIDE), (0, 0, 0, 0))
    canvas.paste(picture, ((EMOJI_SIDE - picture.width) // 2, (EMOJI_SIDE - picture.height) // 2))
    output = io.BytesIO()
    canvas.save(output, "PNG", optimize=True)
    return output.getvalue()


async def from_upload(interaction, file):
    """Картинка -> эмодзи в хранилище бота. -> (эмодзи, ключ ошибки)."""
    import asyncio
    import media
    if not (file.content_type or "").startswith("image/") or file.size > UPLOAD_MAX_BYTES:
        return None, "emoji_picker.error.bad_file"
    if not interaction.response.is_done():
        await interaction.response.defer()
    try:
        png = await asyncio.to_thread(prepare_image, await file.read())
    except Exception:  # не картинка или битый файл
        return None, "emoji_picker.error.bad_file"
    if len(png) > EMOJI_MAX_BYTES:
        return None, "emoji_picker.error.bad_file"
    name = "custom_" + hashlib.sha1(png).hexdigest()[:10]
    bot = interaction.client
    try:
        emoji = await bot.create_application_emoji(name=name, image=png)
    except discord.HTTPException:
        # такая картинка уже загружена (имя занято) — берём её
        try:
            emoji = discord.utils.get(await bot.fetch_application_emojis(), name=name)
        except discord.HTTPException:
            emoji = None
        if emoji is None:
            return None, "emoji_picker.error.upload_failed"
    media._app_emoji_ids.add(emoji.id)
    media._app_emoji_names.add(emoji.name)
    return str(emoji), None
