"""
clan_info.py
============
Вопрос про кланы упоминанием: «@Purr как работают кланы?», «@Purr кланы
продление», «@Purr что даёт огонёк у кланов?». Purr отвечает карточкой темы
(по словам вопроса — создание, роли, казна, продление, огонёк, биллборд,
квесты, магазин, вступление, что скоро) и меню «Другие темы»: выбранная тема
показывается нажавшему скрыто, общий чат не засоряется.

Тексты — в каталоге (clan_info.<тема>.title / .text): их можно править в
настройках текстов бота, не трогая код.
"""

import discord

import core
from core import t

# (тема, эмодзи, слова в вопросе) — первая подходящая тема отвечает
TOPICS = [
    ("create", "🏗️", ("создат", "создан", "основат", "основа", "взнос", "открыть клан")),
    ("roles", "👑", ("глав", "зам", "представ", "роль", "роли", "права", "исключ", "приглас", "лидер")),
    ("treasury", "💰", ("казн", "наград", "голд", "золот", "респект", "75")),
    ("renewal", "📅", ("продл", "долг", "распуск", "распуст", "роспуск", "5000", "срок")),
    ("fire", "🔥", ("огон", "множит", "серия")),
    ("billboard", "📊", ("биллборд", "билборд", "балл", "топ", "рейтинг", "войс", "голосов", "сообщени")),
    ("quests", "🎯", ("квест", "стипенд", "задани")),
    ("shop", "🛒", ("магазин", "купить", "покуп", "товар", "цена", "стоит")),
    ("join", "🚪", ("вступ", "попасть", "присоедин", "заявк", "выйти", "уйти")),
    ("soon", "🔜", ("скоро", "будет", "план", "новое", "аукцион", "совет", "санкц")),
]
TOPIC_KEYS = [key for key, _, _ in TOPICS]
CLAN_WORDS = ("клан", "clan")


def words_of(message):
    me = message.guild.me.id
    text = (message.content or "").replace(f"<@{me}>", " ").replace(f"<@!{me}>", " ")
    own_role = message.guild.self_role
    if own_role is not None:
        text = text.replace(f"<@&{own_role.id}>", " ")
    return " ".join(text.lower().split())


def wants(message):
    """Вопрос про кланы? (казне и переносу эмодзи не мешаем)"""
    import clan_treasury
    from emoji_export import pinged
    if message.guild is None or message.guild.me is None or not pinged(message):
        return False
    if clan_treasury.wants(message):
        return False
    return any(word in words_of(message) for word in CLAN_WORDS)


def topic_for(text):
    """Тема по словам вопроса; ничего конкретного — общий обзор."""
    text = text.lower()
    for key, _, keys in TOPICS:
        if any(k in text for k in keys):
            return key
    return "overview"


def topic_embed(topic):
    emoji = next((e for key, e, _ in TOPICS if key == topic), "🛡️")
    embed = discord.Embed(title=f"{emoji} {t(f'clan_info.{topic}.title')}"[:256],
                          description=t(f"clan_info.{topic}.text")[:4096], color=core.embed_color())
    embed.set_footer(text=t("clan_info.footer")[:2048])
    return embed


class TopicSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"rb:clinfo"):
    """«Другие темы» под ответом: кнопка постоянная, тема — скрыто нажавшему."""

    def __init__(self, select=None):
        if select is None:
            select = discord.ui.Select(
                custom_id="rb:clinfo", placeholder=t("clan_info.more")[:150],
                options=[discord.SelectOption(label=t("clan_info.overview.title")[:100], emoji="🛡️", value="overview")]
                + [discord.SelectOption(label=t(f"clan_info.{key}.title")[:100], emoji=emoji, value=key)
                   for key, emoji, _ in TOPICS])
        super().__init__(select)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(select=item)

    async def callback(self, interaction):
        topic = self.item.values[0] if self.item.values else "overview"
        if topic not in TOPIC_KEYS and topic != "overview":
            topic = "overview"
        await interaction.response.send_message(embed=topic_embed(topic), ephemeral=True)


def topic_view():
    view = discord.ui.View(timeout=None)
    view.add_item(TopicSelect())
    return view


async def on_message(message):
    if message.author.bot or not wants(message):
        return
    topic = topic_for(words_of(message))
    try:
        await message.reply(embed=topic_embed(topic), view=topic_view(), mention_author=False,
                            allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        core._log.warning("clan info: cannot reply in channel %s", message.channel.id)


DYNAMIC_ITEMS = [TopicSelect]


def register(bot):
    bot.add_listener(on_message, "on_message")
