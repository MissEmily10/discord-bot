"""
Шепталка: личное сообщение участнику — текст и/или картинка, анонимно или нет.

Отправить: /whisper кому: текст: картинка: анонимно: куда — или долгое
нажатие / ПКМ по участнику → Приложения → «Шепнуть».
- в личку: бот пишет получателю в ЛС (закрыта — шепталка уходит в чат);
- в чат: в канале появляется «📩 @Аня, тебе шепталка» с кнопкой «Прочитать» —
  прочитать может только получатель, остальные видят лишь, что шепталка есть.

Под шепталкой: «Ответить» (тоже анонимно или нет — ответ дойдёт и анониму),
«Не принимать от него», «Пожаловаться», «Не принимать шепталки».
Анонимность: получатель автора не знает. По жалобе админы видят автора и
текст (в канале жалоб из настроек или в журнале /access).
"""

import asyncio
import io
import logging
import time

import discord
from discord import app_commands

import core
from core import PanelView, Modal, t, panel_embed
from actions import reply
from database import get_connection

_log = logging.getLogger(__name__)
MAX_TEXT = 1500
RATE_WINDOW, RATE_LIMIT, RATE_PER_TARGET = 10 * 60, 5, 3
core.COMMAND_MIN_LEVELS.setdefault("whisper", "member")


# ============================================================
# ХРАНИЛИЩЕ
# ============================================================

def ensure_tables():
    connection = get_connection()
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS whispers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, sender_id INTEGER NOT NULL,
            recipient_id INTEGER NOT NULL, text TEXT NOT NULL DEFAULT '', image TEXT NOT NULL DEFAULT '',
            anonymous INTEGER NOT NULL DEFAULT 1, delivery TEXT NOT NULL DEFAULT 'dm', channel_id INTEGER,
            reply_to INTEGER, reported INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, read_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS whisper_blocks (
            guild_id INTEGER NOT NULL, recipient_id INTEGER NOT NULL, sender_id INTEGER NOT NULL,
            PRIMARY KEY (guild_id, recipient_id, sender_id)
        );
        CREATE TABLE IF NOT EXISTS whisper_optout (
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY (guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS whisper_settings (
            guild_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1, report_channel_id INTEGER,
            allow_images INTEGER NOT NULL DEFAULT 1
        );
    """)
    connection.commit()
    connection.close()


def _q(sql, params=(), one=False, write=False):
    ensure_tables()
    connection = get_connection()
    cursor = connection.execute(sql, params)
    result = cursor.lastrowid if write else (cursor.fetchone() if one else cursor.fetchall())
    if write:
        connection.commit()
    connection.close()
    return result


COLUMNS = ("id", "guild_id", "sender_id", "recipient_id", "text", "image", "anonymous", "delivery", "channel_id",
           "reply_to", "reported", "created_at", "read_at")


def get_whisper(whisper_id):
    row = _q(f"SELECT {', '.join(COLUMNS)} FROM whispers WHERE id = ?", (whisper_id,), one=True)
    return dict(zip(COLUMNS, row)) if row else None


def get_settings(guild_id):
    row = _q("SELECT enabled, report_channel_id, allow_images FROM whisper_settings WHERE guild_id = ?",
             (guild_id,), one=True)
    if row is None:
        return {"guild_id": guild_id, "enabled": 1, "report_channel_id": None, "allow_images": 1}
    return {"guild_id": guild_id, "enabled": row[0], "report_channel_id": row[1], "allow_images": row[2]}


def save_settings(settings):
    _q("INSERT OR REPLACE INTO whisper_settings (guild_id, enabled, report_channel_id, allow_images) "
       "VALUES (?, ?, ?, ?)", (settings["guild_id"], int(settings["enabled"]), settings["report_channel_id"],
                               int(settings["allow_images"])), write=True)


def is_blocked(guild_id, recipient_id, sender_id):
    return bool(_q("SELECT 1 FROM whisper_blocks WHERE guild_id = ? AND recipient_id = ? AND sender_id = ?",
                   (guild_id, recipient_id, sender_id), one=True))


def opted_out(guild_id, user_id):
    return bool(_q("SELECT 1 FROM whisper_optout WHERE guild_id = ? AND user_id = ?", (guild_id, user_id), one=True))


def set_optout(guild_id, user_id, value):
    if value:
        _q("INSERT OR IGNORE INTO whisper_optout (guild_id, user_id) VALUES (?, ?)", (guild_id, user_id), write=True)
    else:
        _q("DELETE FROM whisper_optout WHERE guild_id = ? AND user_id = ?", (guild_id, user_id), write=True)


def rate_problem(guild_id, sender_id, recipient_id):
    since = int(time.time()) - RATE_WINDOW
    total = _q("SELECT COUNT(*) FROM whispers WHERE guild_id = ? AND sender_id = ? AND created_at >= ?",
               (guild_id, sender_id, since), one=True)[0]
    if total >= RATE_LIMIT:
        return "whispers.rate"
    to_one = _q("SELECT COUNT(*) FROM whispers WHERE guild_id = ? AND sender_id = ? AND recipient_id = ? "
                "AND created_at >= ?", (guild_id, sender_id, recipient_id, since), one=True)[0]
    return "whispers.rate_one" if to_one >= RATE_PER_TARGET else None


def check(guild, sender, recipient, has_image):
    """-> ключ ошибки или None."""
    settings = get_settings(guild.id)
    if not settings["enabled"]:
        return "whispers.disabled"
    if recipient is None or recipient.bot:
        return "whispers.no_bots"
    if recipient.id == sender.id:
        return "whispers.self"
    if has_image and not settings["allow_images"]:
        return "whispers.no_images"
    if opted_out(guild.id, recipient.id):
        return "whispers.opted_out"
    if is_blocked(guild.id, recipient.id, sender.id):
        return "whispers.blocked"
    return rate_problem(guild.id, sender.id, recipient.id)


# ============================================================
# ДОСТАВКА
# ============================================================

def image_bytes(whisper):
    if not whisper["image"]:
        return None
    import web_panel
    path = web_panel.ASSETS_DIR / whisper["image"]
    return path.read_bytes() if path.is_file() else None


def whisper_embed(guild, whisper):
    sender = guild.get_member(whisper["sender_id"]) if guild else None
    who = t("whispers.anonymous") if whisper["anonymous"] else (sender.mention if sender else t("whispers.someone"))
    embed = discord.Embed(title=t("whispers.title_reply" if whisper["reply_to"] else "whispers.title"),
                          description=whisper["text"] or None, color=core.embed_color())
    embed.add_field(name=t("whispers.from"), value=who)
    if whisper["reply_to"]:
        original = get_whisper(whisper["reply_to"])
        if original and original["text"]:
            embed.add_field(name=t("whispers.reply_to"), value=f"«{original['text'][:200]}»", inline=False)
    if guild:
        embed.set_footer(text=t("whispers.footer", server=guild.name))
    return embed


def message_kwargs(guild, whisper, with_actions=True):
    kwargs = {"embed": whisper_embed(guild, whisper)}
    data = image_bytes(whisper)
    if data:
        kwargs["embed"].set_image(url="attachment://whisper.png")
        kwargs["file"] = discord.File(io.BytesIO(data), filename="whisper.png")
    if with_actions:
        view = discord.ui.View(timeout=None)
        for act in ("reply", "block", "report", "mute"):
            view.add_item(WhisperAction(whisper["id"], act))
        kwargs["view"] = view
    return kwargs


def hides_recipient(whisper):
    """Ответ на анонимную шепталку: получатель (автор той шепталки) — аноним, его нельзя называть."""
    original = get_whisper(whisper["reply_to"]) if whisper.get("reply_to") else None
    return bool(original and original["anonymous"])


async def deliver(guild, whisper, fallback_channel=None):
    """-> куда дошло: "dm", "chat" или None (никуда)."""
    recipient = guild.get_member(whisper["recipient_id"])
    if recipient is None:
        return None
    if whisper["delivery"] == "dm":
        try:
            await recipient.send(**message_kwargs(guild, whisper))
            return "dm"
        except discord.HTTPException:
            pass  # ЛС закрыта — в чат
    channel = guild.get_channel_or_thread(whisper["channel_id"] or 0) or fallback_channel
    if channel is None or core.bot_missing(channel):
        return None
    view = discord.ui.View(timeout=None)
    view.add_item(WhisperRead(whisper["id"]))
    if hides_recipient(whisper):
        # ответ анониму: упомянуть его в чате = раскрыть, кто писал. Без имени и пинга,
        # «Прочитать» откроет только он
        text, mentions = t("whispers.in_chat_anon"), discord.AllowedMentions.none()
    else:
        text = t("whispers.in_chat", user=recipient.mention)
        mentions = discord.AllowedMentions(users=[recipient], roles=False, everyone=False)
    try:
        await channel.send(content=text, view=view, allowed_mentions=mentions)
    except discord.HTTPException:
        return None
    _q("UPDATE whispers SET delivery = 'chat', channel_id = ? WHERE id = ?", (channel.id, whisper["id"]), write=True)
    return "chat"


async def send_whisper(interaction, recipient, text, attachment, anonymous, delivery, reply_to=None,
                       guild=None, channel=None):
    """Проверить, сохранить и доставить. Ответ — тому, кто отправил (видно только ему)."""
    guild = guild or interaction.guild
    text = (text or "").strip()[:MAX_TEXT]
    if not text and attachment is None:
        await reply(interaction, "whispers.empty")
        return
    problem = check(guild, interaction.user, recipient, attachment is not None)
    if problem:
        await reply(interaction, problem)
        return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    image = ""
    if attachment is not None:
        if attachment.size > 8 * 1024 * 1024 or not (attachment.content_type or "").startswith("image/"):
            await interaction.followup.send(t("whispers.bad_image"), ephemeral=True)
            return
        import web_panel
        name, error = await asyncio.to_thread(web_panel.save_asset, await attachment.read())
        if error:
            await interaction.followup.send(t("whispers.bad_image"), ephemeral=True)
            return
        image = name
    from live import safe_text
    whisper_id = _q("INSERT INTO whispers (guild_id, sender_id, recipient_id, text, image, anonymous, delivery, "
                    "channel_id, reply_to, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (guild.id, interaction.user.id, recipient.id, safe_text(text) if text else "", image,
                     int(anonymous), delivery, channel.id if channel else (interaction.channel_id or None),
                     reply_to, int(time.time())), write=True)
    fallback = channel or (interaction.channel if interaction.guild else None)
    where = await deliver(guild, get_whisper(whisper_id), fallback)
    if where is None:
        _q("DELETE FROM whispers WHERE id = ?", (whisper_id,), write=True)
        await interaction.followup.send(t("whispers.undelivered"), ephemeral=True)
        return
    key = "whispers.sent_dm" if where == "dm" else "whispers.sent_chat"
    if where == "chat" and delivery == "dm":
        key = "whispers.sent_fallback"
    if hides_recipient(get_whisper(whisper_id)):
        # ответ на анонимную шепталку: нельзя называть, кому ушло, — это и есть аноним
        key = "whispers.sent_to_anon_dm" if where == "dm" else "whispers.sent_to_anon_chat"
    await interaction.followup.send(t(key, user=recipient.display_name) + ("\n" + t("whispers.sent_anon")
                                                                          if anonymous else ""), ephemeral=True)


# ============================================================
# КНОПКИ ПОД ШЕПТАЛКОЙ
# ============================================================

def _guild_of(interaction, whisper):
    return interaction.client.get_guild(whisper["guild_id"]) if whisper else None


class WhisperRead(discord.ui.DynamicItem[discord.ui.Button], template=r"rb:wr:(?P<wid>\d+)"):
    """«Прочитать» в чате — открывает шепталку только получателю."""

    def __init__(self, whisper_id, *, item=None):
        super().__init__(item or discord.ui.Button(label=t("whispers.read_button")[:80], emoji="📩",
                                                   style=discord.ButtonStyle.primary, custom_id=f"rb:wr:{whisper_id}"))
        self.whisper_id = whisper_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["wid"]), item=item)

    async def callback(self, interaction):
        whisper = get_whisper(self.whisper_id)
        if whisper is None:
            await reply(interaction, "whispers.gone")
            return
        if interaction.user.id != whisper["recipient_id"]:
            await reply(interaction, "whispers.not_for_you")
            return
        _q("UPDATE whispers SET read_at = COALESCE(read_at, ?) WHERE id = ?", (int(time.time()), whisper["id"]),
           write=True)
        await interaction.response.send_message(ephemeral=True, **message_kwargs(_guild_of(interaction, whisper),
                                                                                  whisper))


class WhisperAction(discord.ui.DynamicItem[discord.ui.Button], template=r"rb:wh:(?P<wid>\d+):(?P<act>reply|block|report|mute)"):
    LABELS = {"reply": ("whispers.act.reply", "💬", discord.ButtonStyle.primary),
              "block": ("whispers.act.block", "🚫", discord.ButtonStyle.secondary),
              "report": ("whispers.act.report", "⚠️", discord.ButtonStyle.secondary),
              "mute": ("whispers.act.mute", "🔕", discord.ButtonStyle.secondary)}

    def __init__(self, whisper_id, act, *, item=None):
        if item is None:
            key, emoji, style = self.LABELS[act]
            item = discord.ui.Button(label=t(key)[:80], emoji=emoji, style=style, custom_id=f"rb:wh:{whisper_id}:{act}")
        super().__init__(item)
        self.whisper_id, self.act = whisper_id, act

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["wid"]), match["act"], item=item)

    async def callback(self, interaction):
        whisper = get_whisper(self.whisper_id)
        guild = _guild_of(interaction, whisper)
        if whisper is None or guild is None:
            await reply(interaction, "whispers.gone")
            return
        if interaction.user.id != whisper["recipient_id"]:
            await reply(interaction, "whispers.not_for_you")
            return
        if self.act == "reply":
            await interaction.response.send_modal(ReplyModal(whisper))
        elif self.act == "block":
            _q("INSERT OR IGNORE INTO whisper_blocks (guild_id, recipient_id, sender_id) VALUES (?, ?, ?)",
               (guild.id, whisper["recipient_id"], whisper["sender_id"]), write=True)
            await reply(interaction, "whispers.blocked_done")
        elif self.act == "mute":
            muted = not opted_out(guild.id, interaction.user.id)
            set_optout(guild.id, interaction.user.id, muted)
            await reply(interaction, "whispers.muted" if muted else "whispers.unmuted")
        else:
            await report(interaction, guild, whisper)


async def report(interaction, guild, whisper):
    """Жалоба: админы видят автора и текст — в канале жалоб или в журнале."""
    if whisper["reported"]:
        await reply(interaction, "whispers.reported")
        return
    _q("UPDATE whispers SET reported = 1 WHERE id = ?", (whisper["id"],), write=True)
    from database import log_audit
    try:
        log_audit(guild.id, interaction.user.id, "whisper.reported", "whisper", whisper["id"],
                  f"from={whisper['sender_id']} to={whisper['recipient_id']}: {whisper['text'][:300]}")
    except Exception:
        _log.exception("whisper report audit")
    channel = guild.get_channel(get_settings(guild.id)["report_channel_id"] or 0)
    if channel is not None:
        embed = discord.Embed(title=t("whispers.report_title"), color=core.danger_color(),
                              description=whisper["text"] or "—")
        embed.add_field(name=t("whispers.report_from"), value=f"<@{whisper['sender_id']}> (`{whisper['sender_id']}`)")
        embed.add_field(name=t("whispers.report_to"), value=f"<@{whisper['recipient_id']}>")
        embed.add_field(name=t("whispers.report_anon"), value=t("common.yes") if whisper["anonymous"] else t("common.no"))
        kwargs = {"embed": embed}
        data = image_bytes(whisper)
        if data:
            embed.set_image(url="attachment://whisper.png")
            kwargs["file"] = discord.File(io.BytesIO(data), filename="whisper.png")
        try:
            await channel.send(allowed_mentions=discord.AllowedMentions.none(), **kwargs)
        except discord.HTTPException:
            pass
    await reply(interaction, "whispers.reported")


CHOICE_YES, CHOICE_NO = "yes", "no"


def _anon_select(default=True):
    return discord.ui.Select(options=[
        discord.SelectOption(label=t("whispers.anon_yes")[:100], value=CHOICE_YES, default=default),
        discord.SelectOption(label=t("whispers.anon_no")[:100], value=CHOICE_NO, default=not default)])


class ReplyModal(Modal, title="ОТВЕТ"):
    """Ответ дойдёт отправителю, даже анонимному: бот знает, кому."""

    def __init__(self, whisper):
        super().__init__(title=t("whispers.reply_modal.title")[:45])
        self.whisper_id = whisper["id"]
        self.text = discord.ui.TextInput(style=discord.TextStyle.paragraph, max_length=MAX_TEXT, required=False)
        self.image = discord.ui.FileUpload(required=False, max_values=1)
        self.anon = _anon_select(True)
        self.add_item(discord.ui.Label(text=t("whispers.modal.text")[:45], component=self.text))
        self.add_item(discord.ui.Label(text=t("whispers.modal.image")[:45], component=self.image))
        self.add_item(discord.ui.Label(text=t("whispers.modal.anon")[:45], component=self.anon))

    async def on_submit(self, interaction):
        original = get_whisper(self.whisper_id)
        guild = _guild_of(interaction, original)
        if original is None or guild is None or interaction.user.id != original["recipient_id"]:
            await reply(interaction, "whispers.gone")
            return
        sender = guild.get_member(original["sender_id"])
        if sender is None:
            await reply(interaction, "whispers.sender_left")
            return
        # ответчик в ЛС — interaction.user это User, а не участник сервера
        me = guild.get_member(interaction.user.id)
        if me is None:
            await reply(interaction, "whispers.gone")
            return
        channel = guild.get_channel_or_thread(original["channel_id"] or 0)
        await send_whisper(interaction, sender, self.text.value, (self.image.values or [None])[0],
                           (self.anon.values or [CHOICE_YES])[0] == CHOICE_YES, original["delivery"],
                           reply_to=original["id"], guild=guild, channel=channel)


class WhisperModal(Modal, title="ШЕПТАЛКА"):
    """Из меню участника «Шепнуть»."""

    def __init__(self, recipient):
        super().__init__(title=t("whispers.modal.title", user=recipient.display_name)[:45])
        self.recipient_id = recipient.id
        self.text = discord.ui.TextInput(style=discord.TextStyle.paragraph, max_length=MAX_TEXT, required=False)
        self.image = discord.ui.FileUpload(required=False, max_values=1)
        self.anon = _anon_select(True)
        self.where = discord.ui.Select(options=[
            discord.SelectOption(label=t("whispers.where_dm")[:100], value="dm", default=True),
            discord.SelectOption(label=t("whispers.where_chat")[:100], value="chat")])
        self.add_item(discord.ui.Label(text=t("whispers.modal.text")[:45], component=self.text))
        self.add_item(discord.ui.Label(text=t("whispers.modal.image")[:45], component=self.image))
        self.add_item(discord.ui.Label(text=t("whispers.modal.anon")[:45], component=self.anon))
        self.add_item(discord.ui.Label(text=t("whispers.modal.where")[:45], component=self.where))

    async def on_submit(self, interaction):
        recipient = interaction.guild.get_member(self.recipient_id)
        await send_whisper(interaction, recipient, self.text.value, (self.image.values or [None])[0],
                           (self.anon.values or [CHOICE_YES])[0] == CHOICE_YES, (self.where.values or ["dm"])[0])


# ============================================================
# НАСТРОЙКИ: /interactive → «Шепталка»
# ============================================================

def can_manage(interaction):
    from extended_modules import is_admin
    return interaction.guild is not None and is_admin(interaction)


def home_embed(interaction):
    settings = get_settings(interaction.guild.id)
    sent = _q("SELECT COUNT(*) FROM whispers WHERE guild_id = ? AND created_at >= ?",
              (interaction.guild.id, int(time.time()) - 7 * 24 * 3600), one=True)[0]
    return panel_embed(
        interaction, "whispers.home",
        state=t("whispers.on") if settings["enabled"] else t("whispers.off"),
        images=t("common.yes") if settings["allow_images"] else t("common.no"),
        reports=f"<#{settings['report_channel_id']}>" if settings["report_channel_id"] else t("whispers.reports_log"),
        week=sent)


async def open_home(interaction, back_target=None):
    if not can_manage(interaction):
        await reply(interaction, "common.need_admin")
        return
    settings = get_settings(interaction.guild.id)
    view = WhisperHome(back_target=back_target)
    view.toggle.label = t("whispers.home.toggle_off" if settings["enabled"] else "whispers.home.toggle")[:80]
    view.images.label = t("whispers.home.images_off" if settings["allow_images"] else "whispers.home.images")[:80]
    await interaction.response.edit_message(embed=home_embed(interaction), view=view)


class WhisperHome(PanelView):
    texts = "whispers.home"

    async def _settings(self, interaction):
        if can_manage(interaction):
            return get_settings(interaction.guild.id)
        await reply(interaction, "common.need_admin")
        return None

    @discord.ui.button(label="Выключить", emoji="🔕", style=discord.ButtonStyle.secondary)
    async def toggle(self, interaction, button):
        settings = await self._settings(interaction)
        if settings:
            settings["enabled"] = 0 if settings["enabled"] else 1
            save_settings(settings)
            core.audit(interaction, "whispers.toggle", "guild", interaction.guild.id, str(settings["enabled"]))
            await open_home(interaction, self.back_target)

    @discord.ui.button(label="Картинки", emoji="🖼️", style=discord.ButtonStyle.secondary)
    async def images(self, interaction, button):
        settings = await self._settings(interaction)
        if settings:
            settings["allow_images"] = 0 if settings["allow_images"] else 1
            save_settings(settings)
            await open_home(interaction, self.back_target)

    @discord.ui.button(label="Канал жалоб", emoji="⚠️", style=discord.ButtonStyle.secondary)
    async def reports(self, interaction, button):
        if not await self._settings(interaction):
            return
        view = PanelView(back_target=(core.screen(interaction), self), timeout=600)
        select = discord.ui.ChannelSelect(placeholder=t("whispers.pick_reports")[:150], min_values=0, max_values=1,
                                          channel_types=[discord.ChannelType.text])

        async def picked(i):
            settings = await self._settings(i)
            if settings:
                settings["report_channel_id"] = select.values[0].id if select.values else None
                save_settings(settings)
                await open_home(i, self.back_target)

        select.callback = picked
        view.add_item(select)
        await interaction.response.edit_message(view=view)


# ============================================================
# КОМАНДЫ
# ============================================================

def register(bot):
    @bot.tree.command(name="whisper", description="Шепталка: личное сообщение участнику — анонимно или нет")
    @app_commands.guild_only()
    @app_commands.describe(кому="Кому шепнуть", текст="Что написать", картинка="Картинка (необязательно)",
                           анонимно="Скрыть, от кого (по умолчанию — да)",
                           куда="В личку или в чат (прочитает только он)")
    @app_commands.choices(куда=[app_commands.Choice(name="в личку", value="dm"),
                                app_commands.Choice(name="в чат — откроет только получатель", value="chat")])
    async def whisper(interaction, кому: discord.Member, текст: str = None, картинка: discord.Attachment = None,
                      анонимно: bool = True, куда: app_commands.Choice[str] = None):
        if not await core.require_command_access(interaction, "whisper"):
            return
        await send_whisper(interaction, кому, текст, картинка, анонимно, куда.value if куда else "dm")

    @bot.tree.context_menu(name="Шепнуть")
    @app_commands.guild_only()
    async def whisper_menu(interaction, member: discord.Member):
        if not await core.require_command_access(interaction, "whisper"):
            return
        problem = check(interaction.guild, interaction.user, member, False)
        if problem:
            await reply(interaction, problem)
            return
        await interaction.response.send_modal(WhisperModal(member))


DYNAMIC_ITEMS = [WhisperRead, WhisperAction]
