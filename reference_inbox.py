"""
Референсы стиля иконок — пачками, без слэш-команд.

1. Режим сбора: /eminemovna → Стили иконок → стиль → «Добавить референсы».
   Дальше просто кидаешь сообщения (в этот канал или боту в личку): в каждом
   до 10 картинок, стикеры, свои эмодзи, zip — сколько угодно сообщений.
   Бот ставит ✅ и пишет, сколько добавлено. «Готово» — или само через 15 минут.
2. Меню сообщения: долгое нажатие / ПКМ по любому сообщению → Приложения →
   «В референсы стиля» → выбрать стиль. Работает и для старых сообщений.

Discord отдаёт боту картинки чужих сообщений, только если включён Message
Content Intent — или если бота упомянули в сообщении (или это ЛС). Меню
сообщения работает всегда.
"""

import asyncio
import re
import time
import zipfile

import discord

import core
from core import PanelView, t
from actions import reply

COLLECT_SECONDS = 15 * 60
CUSTOM_EMOJI = re.compile(r"<(a?):\w{2,32}:(\d{15,25})>")
_collecting = {}  # user_id -> {"guild_id", "style_id", "channel_id", "until", "added"}


def _logo():
    import logo_module
    return logo_module


async def images_from_message(message):
    """-> [(bytes, тип)] из вложений, стикеров и своих эмодзи сообщения."""
    found = []
    for attachment in message.attachments:
        name = attachment.filename.lower()
        if name.endswith(".zip"):
            if attachment.size <= 50 * 1024 * 1024:
                found.append((await attachment.read(), "zip"))
            continue
        if (attachment.content_type or "").startswith("image/") and attachment.size <= 8 * 1024 * 1024:
            found.append((await attachment.read(), "image"))
    for sticker in getattr(message, "stickers", []) or []:
        if getattr(sticker, "format", None) == discord.StickerFormatType.lottie:
            continue  # анимация-вектор — не картинка
        data = await core.fetch_bytes(sticker.url)
        if data:
            found.append((data, "image"))
    for match in CUSTOM_EMOJI.finditer(message.content or ""):
        data = await core.fetch_bytes(f"https://cdn.discordapp.com/emojis/{match[2]}.png?size=512")
        if data:
            found.append((data, "image"))
    return found


async def add_to_style(style_id, found):
    """-> (добавлено, пропущено, всего в стиле)."""
    logo = _logo()
    from database import get_logo_style
    added = skipped = 0
    for data, kind in found:
        row = get_logo_style(style_id)
        if row is None:
            break
        if kind == "zip":
            try:
                plus, minus = await asyncio.to_thread(logo.import_zip, row, data)
            except zipfile.BadZipFile:
                plus, minus = 0, 1
            added += plus
            skipped += minus
            continue
        try:
            ok = await asyncio.to_thread(logo.add_reference, row, data)
        except Exception:  # битая картинка
            ok = False
        added, skipped = (added + 1, skipped) if ok else (added, skipped + 1)
    row = get_logo_style(style_id)
    return added, skipped, len(logo.references_of(row)) if row else 0


# ---------- 1. режим сбора ----------

def start_collecting(interaction, style_id):
    _collecting[interaction.user.id] = {
        "guild_id": interaction.guild.id, "style_id": style_id, "channel_id": interaction.channel_id,
        "until": time.time() + COLLECT_SECONDS, "added": 0,
    }


class CollectView(PanelView):
    def __init__(self, user_id):
        super().__init__(timeout=COLLECT_SECONDS)
        self.user_id = user_id

    @discord.ui.button(label="Готово", emoji="✅", style=discord.ButtonStyle.success)
    async def done(self, interaction, button):
        state = _collecting.pop(self.user_id, None)
        added = state["added"] if state else 0
        await interaction.response.edit_message(content=t("refs.collect.finished", added=added), embed=None, view=None)


async def on_message(message):
    if message.author.bot:
        return
    state = _collecting.get(message.author.id)
    if not state:
        return
    if time.time() > state["until"]:
        _collecting.pop(message.author.id, None)
        return
    in_dm = message.guild is None
    if not in_dm and (message.guild.id != state["guild_id"] or message.channel.id != state["channel_id"]):
        return
    found = await images_from_message(message)
    if not found:
        return
    added, skipped, total = await add_to_style(state["style_id"], found)
    state["added"] += added
    state["until"] = time.time() + COLLECT_SECONDS  # пока кидаешь — режим не закрывается
    try:
        await message.add_reaction("✅" if added else "⚠️")
        await message.reply(t("refs.collect.added", added=added, skipped=skipped, total=total,
                              max=_logo().MAX_REFERENCES), mention_author=False, delete_after=15)
    except discord.HTTPException:
        pass


# ---------- 2. меню сообщения ----------

class StylePickView(PanelView):
    def __init__(self, interaction, message):
        super().__init__(timeout=300)
        from database import get_logo_styles
        styles = get_logo_styles(interaction.guild.id)
        select = core.PagedSelect(placeholder=t("refs.pick_style")[:150], options=[
            discord.SelectOption(label=(row[3] or "—")[:100], value=str(row[0])) for row in styles])

        async def picked(i):
            if not _logo().can_manage_styles(i):
                await reply(i, "common.need_admin")
                return
            await i.response.defer(ephemeral=True, thinking=True)
            found = await images_from_message(message)
            if not found:
                await i.followup.send(t("refs.none_in_message"), ephemeral=True)
                return
            added, skipped, total = await add_to_style(int(select.values[0]), found)
            core.audit(i, "logo.references_added", "logo_style", int(select.values[0]), f"+{added} from message")
            await i.followup.send(t("refs.collect.added", added=added, skipped=skipped, total=total,
                                    max=_logo().MAX_REFERENCES), ephemeral=True)

        select.on_pick = picked
        self.add_item(select)


def register(bot):
    bot.add_listener(on_message, "on_message")

    @bot.tree.context_menu(name="В референсы стиля")
    @discord.app_commands.guild_only()
    async def to_references(interaction, message: discord.Message):
        from database import get_logo_styles
        if not _logo().can_manage_styles(interaction):
            await reply(interaction, "common.need_admin")
            return
        if not get_logo_styles(interaction.guild.id):
            await reply(interaction, "refs.no_styles")
            return
        await interaction.response.send_message(t("refs.pick_prompt"), view=StylePickView(interaction, message),
                                                ephemeral=True)
