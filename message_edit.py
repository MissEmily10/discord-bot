"""
message_edit.py
===============
Изменить уже отправленное сообщение бота.

Где: правой кнопкой (на телефоне — долгим нажатием) по сообщению →
«Приложения» → «Изменить сообщение»; или /library → «Изменить отправленное»
и ссылка на сообщение.

Что бот делает:
- сообщение из Message Build (отправлено через /embed, /library, сайт) —
  открывает этот build в редакторе; после «Сохранить» само сообщение и все его
  копии в каналах обновляются сразу (без отдельного «Обновить отправленные»);
- другое сообщение бота — бот берёт его вид (текст, embed'ы, кнопки) в
  редактор; после «Сохранить» оно становится живым Message Build и
  обновляется на месте; дальше правится так же;
- живые сообщения бота (биллборд, «Всё о кланах», карточки, живые картинки) —
  их перерисовывает сам бот, правка бы стёрлась — говорим, где их менять;
- чужое сообщение и сообщение временного вебхука Discord менять не даёт —
  предлагаем открыть копию в редакторе и отправить заново.
"""

import discord

import core
from core import t


def _system_message(message):
    """Живое сообщение бота, которое он сам перерисовывает (не из Message Build)."""
    for row in message.components or []:
        for item in getattr(row, "children", [row]):
            custom_id = getattr(item, "custom_id", None) or ""
            if custom_id.startswith("rb:") and not custom_id.startswith("rb:a:"):
                return True
    import cards
    return any(card.get("message_id") == message.id for card in cards.get_cards(message.guild.id))


class CopyView(discord.ui.View):
    """Чужое сообщение: открыть копию в редакторе (отправится заново, оригинал не трогаем)."""

    def __init__(self, message):
        super().__init__(timeout=600)
        self.message = message

    @discord.ui.button(label="Открыть копию в редакторе", emoji="📋", style=discord.ButtonStyle.primary)
    async def copy(self, interaction, button):
        from build_tools import message_to_payload, open_imported
        payload = message_to_payload(self.message, interaction.guild.id)
        await open_imported(interaction, payload, name=t("build_tools.import.name", id=self.message.id))


async def open_for(interaction, message):
    """Открыть сообщение на правку (см. описание модуля)."""
    import embed_module
    from database import get_instance_build, get_message_build, get_sent_instances
    if interaction.guild is None or message.guild is None or message.guild.id != interaction.guild.id:
        await interaction.response.send_message(t("message_edit.other_server"), ephemeral=True)
        return
    if not core.has_command_access(interaction, "embed"):
        await interaction.response.send_message(ephemeral=True, **core.message_for(interaction, "text_access_denied"))
        return
    me = interaction.client.user
    if message.author.id != me.id or message.webhook_id:
        key = "message_edit.webhook" if message.webhook_id else "message_edit.not_bot"
        await interaction.response.send_message(t(key), view=CopyView(message), ephemeral=True)
        return
    if not core.can_post_in(interaction.user, message.channel):
        await interaction.response.send_message(t("embed.send.no_perm_user", channel=message.channel.mention),
                                                ephemeral=True)
        return
    build_id = get_instance_build(message.id)
    row = get_message_build(build_id) if build_id else None
    if row and row[1] == interaction.guild.id:
        if not embed_module.can_manage_build(interaction, row):
            await interaction.response.send_message(t("embed.edit_denied"), ephemeral=True)
            return
        state = embed_module.EmbedState.from_build(row)
        state.resync_on_save = True
        copies = len(get_sent_instances(build_id))
        await interaction.response.send_message(
            t("message_edit.from_build", name=row[3] or "—", copies=copies), ephemeral=True,
            embeds=embed_module.editor_embeds(state), view=embed_module.EmbedEditorView(state, back_target=None))
        return
    if _system_message(message):
        await interaction.response.send_message(t("message_edit.system"), ephemeral=True)
        return
    if message.flags.components_v2:
        await interaction.response.send_message(t("message_edit.v2"), view=CopyView(message), ephemeral=True)
        return
    from build_tools import message_to_payload, open_imported
    payload = message_to_payload(message, interaction.guild.id, with_files=False)
    if not payload["content"] and not payload["embeds"] and not payload["buttons"] and not payload["interactive"]:
        await interaction.response.send_message(t("build_tools.import.empty"), ephemeral=True)
        return
    core.audit(interaction, "message.edit_opened", "message", message.id, f"channel={message.channel.id}")
    await open_imported(interaction, payload, name=t("message_edit.name", channel=getattr(message.channel, "name", "")),
                        source_key="message_edit.opened", attach=(message.id, message.channel.id))


class LinkModal(core.Modal, title="ИЗМЕНИТЬ ОТПРАВЛЕННОЕ"):
    texts = "message_edit.modal"
    link = discord.ui.TextInput(label="Ссылка на сообщение", max_length=200,
                                placeholder="ПКМ по сообщению → Копировать ссылку")

    async def on_submit(self, interaction):
        from build_tools import fetch_message_for
        message, error = await fetch_message_for(interaction, self.link.value)
        if error:
            await interaction.response.send_message(t(error), ephemeral=True)
            return
        await open_for(interaction, message)


def register(bot):
    @bot.tree.context_menu(name="Изменить сообщение")
    @discord.app_commands.guild_only()
    async def edit_message_menu(interaction, message: discord.Message):
        await open_for(interaction, message)
