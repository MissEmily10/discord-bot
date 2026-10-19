"""
Перенести наши эмодзи (дудлы форм и кнопок, panel_art/emoji) на сервер.

Владелец бота упоминает бота на нужном сервере (@бот, можно со словом
«эмодзи») или пишет /emoji с галочкой «залить_наши_сюда» — бот отвечает, сколько
там свободных мест, и даёт кнопки: залить все, только дудлы форм или только
дудлы кнопок. Эмодзи с тем же именем пропускаются, а если картинку
перерисовали — заменяются новой (emoji_sync).
Боту нужно право «Управлять выражениями» (Manage Expressions).
"""

import asyncio
import os
import time

import discord

import core
from core import PanelView, t

ROOT = os.path.dirname(os.path.abspath(__file__))
SETS = {
    "forms": os.path.join(ROOT, "panel_art", "emoji"),
    "buttons": os.path.join(ROOT, "panel_art", "emoji", "ui"),
}


def files_of(which):
    folders = [SETS[which]] if which in SETS else list(SETS.values())
    found = []
    for folder in folders:
        if os.path.isdir(folder):
            found += [(os.path.splitext(f)[0], os.path.join(folder, f))
                      for f in sorted(os.listdir(folder)) if f.lower().endswith(".png")]
    return found


def free_slots(guild):
    return guild.emoji_limit - len([e for e in guild.emojis if not e.animated])


def summary(guild):
    existing = {e.name for e in guild.emojis}
    return t("emoji_export.text", free=free_slots(guild), limit=guild.emoji_limit,
             forms=len(files_of("forms")), buttons=len(files_of("buttons")),
             already=len([n for n, _ in files_of("all") if n in existing]))


async def upload(guild, which, progress=None):
    """
    -> (залито, уже было, не влезло, ошибки, заменено). Эмодзи с тем же именем,
    но другой картинкой (перерисовали в Figma) удаляется и заливается заново.
    """
    import emoji_sync
    scope = f"guild:{guild.id}"
    existing = {e.name: e for e in guild.emojis}
    added = skipped = no_room = replaced = 0
    errors = []
    last_report = time.time()
    for name, path in files_of(which):
        with open(path, "rb") as handle:
            data = handle.read()
        old = existing.get(name)
        if old is not None and not emoji_sync.is_changed(scope, name, data):
            skipped += 1
            continue
        if old is None and free_slots(guild) <= 0:
            no_room += 1
            continue
        try:
            if old is not None:
                await old.delete(reason="эмодзи бота перерисованы")
            fresh = await guild.create_custom_emoji(name=name, image=data, reason="эмодзи бота")
            emoji_sync.remember(scope, name, emoji_sync.file_hash(data))
            if old is not None:
                emoji_sync.remap_ids(old.id, fresh.id)
                replaced += 1
            else:
                added += 1
        except discord.Forbidden:
            errors.append(t("emoji_export.no_rights"))
            break
        except discord.HTTPException as error:
            errors.append(f"{name}: {error}")
        if progress and time.time() - last_report > 5:
            last_report = time.time()
            await progress(added + replaced)
    return added, skipped, no_room, errors, replaced


class ExportView(PanelView):
    def __init__(self, guild_id):
        super().__init__(timeout=900)
        self.guild_id = guild_id

    async def _run(self, interaction, which):
        if not core.is_owner_id(interaction.user.id):
            await interaction.response.send_message(t("common.owner_only"), ephemeral=True)
            return
        guild = interaction.guild
        if not guild.me.guild_permissions.manage_emojis_and_stickers:
            await interaction.response.send_message(t("emoji_export.no_rights"), ephemeral=True)
            return
        await interaction.response.edit_message(content=t("emoji_export.working"), view=None)
        # edit_original_response работает и для скрытых (ephemeral) сообщений
        async def progress(added):
            try:
                await interaction.edit_original_response(content=t("emoji_export.progress", added=added))
            except discord.HTTPException:
                pass

        added, skipped, no_room, errors, replaced = await upload(guild, which, progress)
        text = t("emoji_export.done", added=added, replaced=replaced, skipped=skipped, free=free_slots(guild))
        if no_room:
            text += "\n" + t("emoji_export.no_room", count=no_room)
        if errors:
            text += "\n⚠️ " + "; ".join(errors[:3])[:500]
        core._log.info("emoji export to %s: +%s", guild.id, added)
        try:
            await interaction.edit_original_response(content=text)
        except discord.HTTPException:
            await interaction.followup.send(text, ephemeral=True)

    @discord.ui.button(label="Все", emoji="📦", style=discord.ButtonStyle.success)
    async def all(self, interaction, button):
        await self._run(interaction, "all")

    @discord.ui.button(label="Дудлы форм", emoji="🌟", style=discord.ButtonStyle.secondary)
    async def forms(self, interaction, button):
        await self._run(interaction, "forms")

    @discord.ui.button(label="Дудлы кнопок", emoji="🔘", style=discord.ButtonStyle.secondary)
    async def buttons(self, interaction, button):
        await self._run(interaction, "buttons")


def pinged(message):
    """Бота упомянули: самим ботом (@Purr) или его ролью (тоже выглядит как @Purr)."""
    guild = message.guild
    if guild is None or guild.me is None:
        return False
    if guild.me in message.mentions or any(u.id == guild.me.id for u in message.mentions):
        return True
    own_role = guild.self_role
    if own_role is not None and own_role in message.role_mentions:
        return True
    raw = message.content or ""
    return f"<@{guild.me.id}>" in raw or f"<@!{guild.me.id}>" in raw


async def offer(channel_or_message, guild):
    """Сводка + кнопки переноса (ответом на сообщение или в канал)."""
    send = channel_or_message.reply if isinstance(channel_or_message, discord.Message) else channel_or_message.send
    kwargs = {"mention_author": False} if isinstance(channel_or_message, discord.Message) else {}
    await send(summary(guild), view=ExportView(guild.id), **kwargs)


async def on_message(message):
    """Владелец упомянул бота — предложить перенос эмодзи на этот сервер.

    Слово «эмодзи» не обязательно: без Message Content Intent Discord может
    не отдать боту текст, и тогда бот молчал бы.
    """
    if message.author.bot or not pinged(message):
        return
    import clan_treasury
    if clan_treasury.wants(message):
        return  # «@Purr зафиксируй / казна» — это казна кланов, не эмодзи
    if not core.is_owner_id(message.author.id):
        core._log.info("emoji export ping from non-owner %s (OWNER_ID=%s)", message.author.id, core.OWNER_ID)
        try:
            await message.reply(t("emoji_export.not_owner"), mention_author=False, delete_after=20)
        except discord.HTTPException:
            pass
        return
    text = (message.content or "").lower()
    words = text.replace(f"<@{message.guild.me.id}>", "").replace(f"<@!{message.guild.me.id}>", "").strip()
    if words and not any(k in words for k in ("эмодз", "emoji", "смайл", "<@&")):
        return  # пинг по другому делу — не мешаем
    core._log.info("emoji export offer in guild %s", message.guild.id)
    try:
        await offer(message, message.guild)
    except discord.Forbidden:
        core._log.warning("emoji export: no permission to reply in channel %s", message.channel.id)


def register(bot):
    bot.add_listener(on_message, "on_message")
