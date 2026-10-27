"""
Перенести наши эмодзи (дудлы форм, клановые дудлы и дудлы кнопок,
panel_art/emoji) на сервер.

Владелец бота открывает /eminemovna → «Эмодзи бота» на нужном сервере — бот
показывает, сколько там свободных мест, и даёт кнопки: залить все, только дудлы
форм, только клановые, только фирменные смайлики или только дудлы кнопок. Эмодзи с тем же именем
пропускаются, а если картинку перерисовали — заменяются новой (emoji_sync).
Боту нужно право «Управлять выражениями» (Manage Expressions).
"""

import asyncio
import os
import time

import discord

import core
from core import PanelView, t

ROOT = os.path.dirname(os.path.abspath(__file__))
EMOJI_DIR = os.path.join(ROOT, "panel_art", "emoji")
# набор -> (папка, какие файлы берём)
SETS = {
    "forms": (EMOJI_DIR, lambda name: not name.startswith("doodle_clan_")),
    "clans": (os.path.join(EMOJI_DIR, "doodle3d"), lambda name: name.startswith("d3_clan_")),  # клановые — 3D
    "smileys": (os.path.join(EMOJI_DIR, "smiley"), lambda name: name.startswith("smile_")),  # фирменные смайлики
    "buttons": (os.path.join(EMOJI_DIR, "ui"), lambda name: True),
}


def files_of(which):
    sets = [SETS[which]] if which in SETS else list(SETS.values())
    found = []
    for folder, wanted in sets:
        if os.path.isdir(folder):
            found += [(os.path.splitext(f)[0], os.path.join(folder, f)) for f in sorted(os.listdir(folder))
                      if f.lower().endswith(".png") and wanted(os.path.splitext(f)[0])]
    return found


def card(text):
    return discord.Embed(description=text[:4096], color=core.embed_color())


def free_slots(guild):
    return guild.emoji_limit - len([e for e in guild.emojis if not e.animated])


def summary(guild):
    existing = {e.name for e in guild.emojis}
    return t("emoji_export.text", free=free_slots(guild), limit=guild.emoji_limit,
             forms=len(files_of("forms")), clans=len(files_of("clans")), smileys=len(files_of("smileys")),
             buttons=len(files_of("buttons")),
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
    button_order = ("all", "forms", "clans", "smileys", "buttons")

    def __init__(self, guild_id, back_target=None):
        super().__init__(timeout=900, back_target=back_target)
        self.guild_id = guild_id

    async def _run(self, interaction, which):
        if not core.is_owner_id(interaction.user.id):
            await interaction.response.send_message(t("common.owner_only"), ephemeral=True)
            return
        guild = interaction.guild
        if not guild.me.guild_permissions.manage_emojis_and_stickers:
            await interaction.response.send_message(t("emoji_export.no_rights"), ephemeral=True)
            return
        await interaction.response.edit_message(embed=card(t("emoji_export.working")), view=None)
        # edit_original_response работает и для скрытых (ephemeral) сообщений
        async def progress(added):
            try:
                await interaction.edit_original_response(embed=card(t("emoji_export.progress", added=added)))
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
            await interaction.edit_original_response(embed=card(text))
        except discord.HTTPException:
            await interaction.followup.send(text, ephemeral=True)

    @discord.ui.button(label="Все", emoji="📦", style=discord.ButtonStyle.success)
    async def all(self, interaction, button):
        await self._run(interaction, "all")

    @discord.ui.button(label="Дудлы форм", emoji="🌟", style=discord.ButtonStyle.secondary)
    async def forms(self, interaction, button):
        await self._run(interaction, "forms")

    @discord.ui.button(label="Клановые 3D-дудлы", emoji="🛡️", style=discord.ButtonStyle.secondary)
    async def clans(self, interaction, button):
        await self._run(interaction, "clans")

    @discord.ui.button(label="Смайлики", emoji="😊", style=discord.ButtonStyle.secondary)
    async def smileys(self, interaction, button):
        await self._run(interaction, "smileys")

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


async def open_panel(interaction, back_target=None):
    """/eminemovna → «Эмодзи бота»: сводка по серверу и кнопки заливки (только владелец бота)."""
    if not core.is_owner_id(interaction.user.id):
        await interaction.response.send_message(t("emoji_export.not_owner"), ephemeral=True)
        return
    view = ExportView(interaction.guild.id, back_target=back_target)
    if back_target is not None:
        await interaction.response.edit_message(embed=card(summary(interaction.guild)), view=view)
    else:
        await interaction.response.send_message(embed=card(summary(interaction.guild)), view=view, ephemeral=True)
