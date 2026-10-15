"""
Прикрепить набор кнопок к уже существующему сообщению.

Discord даёт боту менять только свои сообщения, поэтому:
- сообщение бота — кнопки набора ставятся под него, старые кнопки убираются;
- чужое сообщение (твоё, участника, другого бота) — бот переотправляет его с
  кнопками: от своего имени или от имени автора (временный вебхук: имя и
  аватарка автора, потом вебхук удаляется). Оригинал удаляется, если можно.

От имени автора — только сам автор, админы и те, кто может управлять вебхуками.
"""

import io

import discord

import core
import live
from actions import render_source, reply
from core import PanelView, Modal, t, panel_embed
from database import get_instance_build

MAX_REPOST_FILES = 10


async def open_attach(interaction, set_id):
    await interaction.response.send_modal(AttachModal(set_id))


class AttachModal(Modal, title="ПРИКРЕПИТЬ К СООБЩЕНИЮ"):
    texts = "attach.modal"
    link = discord.ui.TextInput(label="Ссылка на сообщение", max_length=200)

    def __init__(self, set_id):
        super().__init__()
        self.set_id = set_id

    async def on_submit(self, interaction):
        from build_tools import fetch_message_for
        message, error = await fetch_message_for(interaction, self.link.value)
        if error:
            await reply(interaction, error)
            return
        if message.guild is None or message.guild.id != interaction.guild.id:
            await reply(interaction, "attach.other_server")
            return
        if not core.can_post_in(interaction.user, message.channel):
            await reply(interaction, "embed.send.no_perm_user", channel=message.channel.mention)
            return
        _, _, view = render_source("s", self.set_id)
        if view is None:
            await reply(interaction, "attach.empty_set")
            return
        if message.author.id == interaction.client.user.id and message.webhook_id is None:
            await attach_to_own(interaction, message, view)
            return
        # чужое сообщение: спросить, от чьего имени переотправить
        await interaction.response.send_message(
            embed=panel_embed(interaction, "attach.repost", author=message.author.display_name),
            view=RepostChoiceView(self.set_id, message), ephemeral=True,
        )


def merged_view(message, view):
    """Кнопки набора ВМЕСТО старых кнопок сообщения (старые убираются)."""
    return view


async def attach_to_own(interaction, message, view):
    if get_instance_build(message.id):
        # кнопки build'а при «Обновить отправленные» перезапишутся — их место в самом build'е
        await reply(interaction, "attach.is_build")
        return
    merged = merged_view(message, view)
    if merged is None:
        await reply(interaction, "attach.no_room")
        return
    try:
        await message.edit(view=merged)
    except discord.HTTPException as error:
        await reply(interaction, "embed.send.http_error", channel=message.channel.mention, error=error)
        return
    core.audit(interaction, "button_set.attached", "message", message.id)
    await reply(interaction, "attach.done", link=message.jump_url)


def may_impersonate(interaction, message):
    return interaction.user.id == message.author.id or live.can_use_webhooks(interaction.guild, interaction.user)


async def repost(interaction, set_id, message, as_author):
    """Переотправить сообщение с кнопками; оригинал удалить, если можно."""
    _, _, view = render_source("s", set_id)
    merged = merged_view(message, view) if view else None
    if merged is None:
        await reply(interaction, "attach.no_room")
        return
    channel = message.channel
    await interaction.response.defer(ephemeral=True, thinking=True)
    files = []
    for attachment in message.attachments[:MAX_REPOST_FILES]:
        try:
            files.append(await attachment.to_file())
        except discord.HTTPException:
            pass
    payload = {
        "content": message.content or None,
        "embeds": [embed for embed in message.embeds if embed.type == "rich"][:10],
        "files": files, "view": merged, "allowed_mentions": discord.AllowedMentions.none(),
    }
    try:
        if as_author:
            target = channel.parent if isinstance(channel, discord.Thread) else channel
            hook = await target.create_webhook(name=message.author.display_name[:80] or "user",
                                               reason=f"buttons by {interaction.user.id}")
            try:
                extra = {"thread": channel} if isinstance(channel, discord.Thread) else {}
                sent = await hook.send(wait=True, username=message.author.display_name[:80],
                                       avatar_url=message.author.display_avatar.url, **extra, **payload)
            finally:
                await hook.delete(reason="temporary webhook")
        else:
            sent = await channel.send(**payload)
    except discord.Forbidden:
        missing = core.bot_missing_text(channel, files=bool(files))
        if as_author:
            missing = t("perm.manage_webhooks")
        await interaction.followup.send(t("embed.send.no_perm_bot", channel=channel.mention, perms=missing),
                                        ephemeral=True)
        return
    except discord.HTTPException as error:
        await interaction.followup.send(t("embed.send.http_error", channel=channel.mention, error=error), ephemeral=True)
        return
    # оригинал — только если его автор ты или у тебя есть право удалять сообщения
    can_remove = interaction.user.id == message.author.id or channel.permissions_for(interaction.user).manage_messages
    removed = False
    if can_remove:
        try:
            await message.delete()
            removed = True
        except discord.HTTPException:
            pass
    core.audit(interaction, "button_set.reposted", "message", sent.id, "as_author" if as_author else "as_bot")
    await interaction.followup.send(t("attach.reposted" if removed else "attach.reposted_kept", link=getattr(sent, "jump_url", channel.mention)),
                                    ephemeral=True)


class RepostChoiceView(PanelView):
    texts = "attach.repost"

    def __init__(self, set_id, message):
        super().__init__(timeout=600)
        self.set_id = set_id
        self.message = message

    @discord.ui.button(label="От имени автора", emoji="👤", style=discord.ButtonStyle.primary)
    async def as_author(self, interaction, button):
        if not may_impersonate(interaction, self.message):
            await reply(interaction, "attach.no_impersonation")
            return
        await repost(interaction, self.set_id, self.message, as_author=True)

    @discord.ui.button(label="От бота", emoji="📤", style=discord.ButtonStyle.secondary)
    async def as_bot(self, interaction, button):
        await repost(interaction, self.set_id, self.message, as_author=False)
