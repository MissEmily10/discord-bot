"""
Перенести наши эмодзи (дудлы форм и кнопок, panel_art/emoji) на сервер.

Владелец бота упоминает бота на нужном сервере (@бот) — бот отвечает, сколько
там свободных мест, и даёт кнопки: залить все, только дудлы форм или только
дудлы кнопок. Эмодзи с тем же именем, что уже есть на сервере, пропускаются.
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
    """-> (залито, уже было, не влезло, ошибки)."""
    existing = {e.name for e in guild.emojis}
    added = skipped = no_room = 0
    errors = []
    last_report = time.time()
    for name, path in files_of(which):
        if name in existing:
            skipped += 1
            continue
        if free_slots(guild) <= 0:
            no_room += 1
            continue
        try:
            with open(path, "rb") as handle:
                await guild.create_custom_emoji(name=name, image=handle.read(), reason="эмодзи бота")
            added += 1
        except discord.Forbidden:
            errors.append(t("emoji_export.no_rights"))
            break
        except discord.HTTPException as error:
            errors.append(f"{name}: {error}")
        if progress and time.time() - last_report > 5:
            last_report = time.time()
            await progress(added)
    return added, skipped, no_room, errors


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
        message = interaction.message

        async def progress(added):
            try:
                await message.edit(content=t("emoji_export.progress", added=added))
            except discord.HTTPException:
                pass

        added, skipped, no_room, errors = await upload(guild, which, progress)
        text = t("emoji_export.done", added=added, skipped=skipped, free=free_slots(guild))
        if no_room:
            text += "\n" + t("emoji_export.no_room", count=no_room)
        if errors:
            text += "\n⚠️ " + "; ".join(errors[:3])[:500]
        core._log.info("emoji export to %s: +%s", guild.id, added)
        await message.edit(content=text)

    @discord.ui.button(label="Все", emoji="📦", style=discord.ButtonStyle.success)
    async def all(self, interaction, button):
        await self._run(interaction, "all")

    @discord.ui.button(label="Дудлы форм", emoji="🌟", style=discord.ButtonStyle.secondary)
    async def forms(self, interaction, button):
        await self._run(interaction, "forms")

    @discord.ui.button(label="Дудлы кнопок", emoji="🔘", style=discord.ButtonStyle.secondary)
    async def buttons(self, interaction, button):
        await self._run(interaction, "buttons")


async def on_message(message):
    """Владелец упомянул бота словом «эмодзи» — предложить перенос эмодзи на этот сервер."""
    me = message.guild.me if message.guild else None
    if me is None or message.author.bot or me not in message.mentions:
        return
    if not core.is_owner_id(message.author.id):
        return
    text = (message.content or "").lower()
    if "эмодз" not in text and "emoji" not in text and "смайл" not in text:
        return
    await message.reply(summary(message.guild), view=ExportView(message.guild.id), mention_author=False)


def register(bot):
    bot.add_listener(on_message, "on_message")
