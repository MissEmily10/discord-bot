"""
clans_now.py
============
/clans-now — информационные посты для участников: как кланы устроены сейчас,
пока Purr нет на сервере (всё делает бот сервера). Их пересылают в ветки
информационного канала (в Discord: … → «Переслать»).

- /clans-now тема:<о чём> — как /post новость, но информационный пост: нейросеть
  подробно объясняет, как работает функция, по одним фактам из
  docs/CLANS_NOW.md (без приветствий, планов и Purr); бот рисует баннер, пост
  открывается в конструкторе на сайте — там правится и отправляется.
- /clans-now без темы — общий обзор: десять карточек с баннерами создателя
  бота (panel_art/purr) одним сообщением в выбранный канал.

Только то, что уже работает в боте сервера (docs/CLANS_INFO_CHANNEL.md, п. 1–2):
без Purr, без «скоро» и без открытых вопросов. Тексты меняются в веб-панели
(ключи clans_now.*), картинки — файлы panel_art/purr.
"""

import os

import discord

import core
from core import t

ROOT = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(ROOT, "panel_art", "purr")
core.COMMAND_MIN_LEVELS.setdefault("clans-now", "staff")

# раздел -> баннер (на них нет Purr и нет того, что ещё не работает)
SECTIONS = (
    ("intro", "purr.002.split.png"),
    ("create", "purr.003.rules.png"),
    ("rights", "purr.035.rules.png"),
    ("treasury", "purr.008.news.png"),
    ("renew", "purr.014.maintenance.png"),
    ("fire", "purr.015.news.png"),
    ("billboard", "purr.019.rules.png"),
    ("quests", "purr.026.update.png"),
    ("shop", "purr.030.news.png"),
    ("old", None),
)


def build():
    """-> (embeds, files): карточки поста и их картинки."""
    color = core.embed_color()
    embeds, files = [], []
    for key, art in SECTIONS:
        embed = discord.Embed(title=t(f"clans_now.{key}.title")[:256], description=t(f"clans_now.{key}.text")[:4096],
                              color=color)
        path = os.path.join(ART, art) if art else None
        if path and os.path.isfile(path):
            name = f"clans_{key}.png"
            files.append(discord.File(path, filename=name))
            embed.set_image(url=f"attachment://{name}")
        embeds.append(embed)
    return embeds, files


def total_length(embeds):
    return sum(len(embed) for embed in embeds)


class SendView(core.PanelView):
    """Куда отправить пост: этот канал или выбранный."""

    def __init__(self):
        super().__init__(timeout=900)
        self.target = None
        pick = discord.ui.ChannelSelect(placeholder=t("clans_now.pick")[:150],
                                        channel_types=[discord.ChannelType.text, discord.ChannelType.news])

        async def picked(interaction):
            self.target = pick.values[0]
            await interaction.response.defer()
        pick.callback = picked
        self.add_item(pick)

    @discord.ui.button(label="Отправить", emoji="📤", style=discord.ButtonStyle.success)
    async def send(self, interaction, button):
        channel = interaction.guild.get_channel(self.target.id) if self.target else interaction.channel
        if channel is None or not core.can_post_in(interaction.user, channel):
            await interaction.response.send_message(t("clans_now.no_rights"), ephemeral=True)
            return
        me = channel.permissions_for(interaction.guild.me)
        if not (me.send_messages and me.embed_links and me.attach_files):
            await interaction.response.send_message(t("clans_now.bot_rights", channel=channel.mention), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        embeds, files = build()
        try:
            message = await channel.send(embeds=embeds, files=files)
        except discord.HTTPException as error:
            await interaction.followup.send(t("clans_now.failed", error=error), ephemeral=True)
            return
        core.audit(interaction, "clans_now.sent", "channel", channel.id, None)
        await interaction.followup.send(t("clans_now.sent", link=message.jump_url), ephemeral=True)


def register(bot):
    @bot.tree.command(name="clans-now", description="Инфо-пост о том, как кланы работают сейчас (без Purr) — по теме или обзор")
    @discord.app_commands.guild_only()
    @discord.app_commands.describe(topic="О чём пост — нейросеть подробно расскажет, как это работает сейчас")
    @discord.app_commands.rename(topic="тема")
    async def clans_now_command(interaction, topic: discord.app_commands.Range[str, 3, 1000] = None):
        if not await core.require_command_access(interaction, "clans-now"):
            return
        if topic:
            import news_generator
            await news_generator.run(interaction, topic, info=True)
            return
        await interaction.response.send_message(embed=core.panel_embed(interaction, "clans_now.home", hints=False),
                                                view=SendView(), ephemeral=True)
