"""
ВРЕМЕННАЯ команда /emoji_wipe — удалить эмодзи разом (только владелец бота).

Три варианта, у каждого — подтверждение:
- «Наши дудлы на этом сервере» — эмодзи сервера с именами наших дудлов (doodle_*, ui_*);
- «Все эмодзи этого сервера» — вообще все обычные и анимированные эмодзи сервера;
- «Хранилище бота» — эмодзи приложения (дудлы на кнопках панелей); при следующем
  запуске бот зальёт их заново из panel_art/emoji.

Когда больше не нужна — удалить этот файл и строку emoji_wipe.register(bot) в bot.py.
"""

import asyncio

import discord

import core
from core import t
from actions import reply


def ours(name):
    return name.startswith("doodle_") or name.startswith("ui_")


async def _delete_all(items, progress=None):
    deleted, failed = 0, 0
    for n, emoji in enumerate(items, 1):
        try:
            await emoji.delete(reason="emoji_wipe")
            deleted += 1
        except discord.HTTPException:
            failed += 1
        if progress and n % 10 == 0:
            await progress(deleted)
    return deleted, failed


class ConfirmView(discord.ui.View):
    def __init__(self, scope, count):
        super().__init__(timeout=120)
        self.scope, self.count = scope, count

    @discord.ui.button(label="Да, удалить", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def yes(self, interaction, button):
        if not core.is_owner_id(interaction.user.id):
            await reply(interaction, "common.owner_only")
            return
        await interaction.response.edit_message(content=t("emoji_wipe.working", count=self.count), view=None)

        async def progress(done):
            try:
                await interaction.edit_original_response(content=t("emoji_wipe.progress", done=done, count=self.count))
            except discord.HTTPException:
                pass

        if self.scope == "app":
            import emoji_sync
            items = [e for e in await interaction.client.fetch_application_emojis() if ours(e.name)]
            deleted, failed = await _delete_all(items, progress)
            # бот забудет хэши — при перезапуске зальёт дудлы заново
            emoji_sync.ensure_table()
            from database import get_connection
            connection = get_connection()
            connection.execute("DELETE FROM emoji_hashes WHERE scope = 'app'")
            connection.commit()
            connection.close()
            # удалённые эмодзи больше нельзя ставить на кнопки — до перезапуска панели с обычными эмодзи
            import form_packs
            import ui_emoji
            ui_emoji._loaded.clear()
            form_packs._doodles.clear()
        else:
            guild = interaction.guild
            items = [e for e in guild.emojis if self.scope == "all" or ours(e.name)]
            deleted, failed = await _delete_all(items, progress)
            if self.scope == "ours":
                from database import get_connection
                connection = get_connection()
                connection.execute("DELETE FROM emoji_hashes WHERE scope = ?", (f"guild:{guild.id}",))
                connection.commit()
                connection.close()
        core.audit(interaction, "emoji.wiped", "emoji", None, f"{self.scope}: {deleted}")
        await interaction.edit_original_response(content=t("emoji_wipe.done", deleted=deleted, failed=failed))

    @discord.ui.button(label="Отмена", emoji="❌", style=discord.ButtonStyle.secondary)
    async def no(self, interaction, button):
        await interaction.response.edit_message(content=t("emoji_wipe.cancelled"), view=None)


def register(bot):
    @bot.tree.command(name="emoji_wipe", description="Временная: удалить эмодзи разом (только владелец бота)")
    @discord.app_commands.guild_only()
    @discord.app_commands.describe(что="Какие эмодзи удалить")
    @discord.app_commands.choices(что=[
        discord.app_commands.Choice(name="наши дудлы на этом сервере", value="ours"),
        discord.app_commands.Choice(name="ВСЕ эмодзи этого сервера", value="all"),
        discord.app_commands.Choice(name="хранилище бота (зальёт заново при перезапуске)", value="app"),
    ])
    async def emoji_wipe(interaction, что: discord.app_commands.Choice[str]):
        if not core.is_owner_id(interaction.user.id):
            await reply(interaction, "common.owner_only")
            return
        scope = что.value
        if scope != "app" and not interaction.guild.me.guild_permissions.manage_emojis_and_stickers:
            await reply(interaction, "emoji_export.no_rights")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if scope == "app":
            count = sum(1 for e in await interaction.client.fetch_application_emojis() if ours(e.name))
        else:
            count = sum(1 for e in interaction.guild.emojis if scope == "all" or ours(e.name))
        if not count:
            await interaction.followup.send(t("emoji_wipe.nothing"), ephemeral=True)
            return
        await interaction.followup.send(t(f"emoji_wipe.confirm_{scope}", count=count),
                                        view=ConfirmView(scope, count), ephemeral=True)
