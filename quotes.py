"""
Цитата дня.

1. Днём участники собирают смешные фразы: долгое нажатие / ПКМ по сообщению →
   Приложения → «В цитаты дня». Это анонимно: ответ видит только тот, кто
   предложил, в канале ничего не появляется.
2. В заданное время (/interactive → «Цитата дня») бот публикует голосование:
   кнопка «Читать и голосовать» открывает (только тебе) все цитаты с листалкой
   и меню выбора. Один голос на человека, можно передумать, за свою — нельзя.
3. Через час (настраивается) — итог: карточка «цитата дня» (шаблон «Цитата»)
   и, если задана роль, она переходит к автору цитаты до следующего победителя.

Ничья — побеждает цитата, предложенная раньше. Нет цитат — голосования нет.
"""

import asyncio
import io
import logging
import re
import time

import discord

import core
from core import PanelView, Modal, t, panel_embed
from actions import reply
from database import get_connection

_log = logging.getLogger(__name__)
MAX_TEXT = 300
PER_PAGE = 8
MAX_PER_DAY = 10          # сколько цитат один человек предлагает за день
CHECK_EVERY = 30


# ============================================================
# ХРАНИЛИЩЕ
# ============================================================

def ensure_tables():
    connection = get_connection()
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS quote_settings (
            guild_id INTEGER PRIMARY KEY, channel_id INTEGER, vote_time TEXT NOT NULL DEFAULT '21:00',
            duration_min INTEGER NOT NULL DEFAULT 60, role_id INTEGER, enabled INTEGER NOT NULL DEFAULT 0,
            last_run_date TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER, message_id INTEGER,
            author_id INTEGER NOT NULL, submitter_id INTEGER NOT NULL, text TEXT NOT NULL,
            round_id INTEGER, created_at INTEGER NOT NULL, UNIQUE (guild_id, message_id)
        );
        CREATE TABLE IF NOT EXISTS quote_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
            message_id INTEGER, ends_at INTEGER NOT NULL, closed INTEGER NOT NULL DEFAULT 0, winner_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS quote_votes (
            round_id INTEGER NOT NULL, voter_id INTEGER NOT NULL, quote_id INTEGER NOT NULL,
            PRIMARY KEY (round_id, voter_id)
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


SETTING_COLUMNS = ("guild_id", "channel_id", "vote_time", "duration_min", "role_id", "enabled", "last_run_date")


def get_settings(guild_id):
    row = _q(f"SELECT {', '.join(SETTING_COLUMNS)} FROM quote_settings WHERE guild_id = ?", (guild_id,), one=True)
    if row is None:
        _q("INSERT INTO quote_settings (guild_id) VALUES (?)", (guild_id,), write=True)
        return get_settings(guild_id)
    return dict(zip(SETTING_COLUMNS, row))


def save_settings(settings):
    _q("UPDATE quote_settings SET channel_id = ?, vote_time = ?, duration_min = ?, role_id = ?, enabled = ?, "
       "last_run_date = ? WHERE guild_id = ?",
       (settings["channel_id"], settings["vote_time"], settings["duration_min"], settings["role_id"],
        int(settings["enabled"]), settings["last_run_date"], settings["guild_id"]), write=True)


QUOTE_COLUMNS = ("id", "guild_id", "channel_id", "message_id", "author_id", "submitter_id", "text", "round_id",
                 "created_at")


def _quotes(sql, params):
    return [dict(zip(QUOTE_COLUMNS, row)) for row in _q(f"SELECT {', '.join(QUOTE_COLUMNS)} FROM quotes " + sql,
                                                         params)]


def pending_quotes(guild_id):
    return _quotes("WHERE guild_id = ? AND round_id IS NULL ORDER BY id", (guild_id,))


def round_quotes(round_id):
    return _quotes("WHERE round_id = ? ORDER BY id", (round_id,))


def get_round(round_id):
    row = _q("SELECT id, guild_id, channel_id, message_id, ends_at, closed, winner_id FROM quote_rounds WHERE id = ?",
             (round_id,), one=True)
    return dict(zip(("id", "guild_id", "channel_id", "message_id", "ends_at", "closed", "winner_id"), row)) \
        if row else None


def clean_text(text):
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text[:MAX_TEXT - 1] + "…" if len(text) > MAX_TEXT else text


def add_quote(guild_id, message, submitter_id):
    """-> (id, None) или (None, ключ ошибки)."""
    text = clean_text(message.content)
    if not text:
        return None, "quotes.no_text"
    if _q("SELECT 1 FROM quotes WHERE guild_id = ? AND message_id = ?", (guild_id, message.id), one=True):
        return None, "quotes.already"
    since = int(time.time()) - 24 * 3600
    count = _q("SELECT COUNT(*) FROM quotes WHERE guild_id = ? AND submitter_id = ? AND created_at >= ?",
               (guild_id, submitter_id, since), one=True)[0]
    if count >= MAX_PER_DAY:
        return None, "quotes.too_many"
    quote_id = _q("INSERT INTO quotes (guild_id, channel_id, message_id, author_id, submitter_id, text, created_at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (guild_id, message.channel.id, message.id, message.author.id, submitter_id, text,
                   int(time.time())), write=True)
    return quote_id, None


def votes_of(round_id):
    """-> {quote_id: голосов}."""
    return {quote_id: count for quote_id, count in
            _q("SELECT quote_id, COUNT(*) FROM quote_votes WHERE round_id = ? GROUP BY quote_id", (round_id,))}


def winner(quotes, votes):
    """Больше голосов; ничья — кто раньше предложен. Нет голосов — None."""
    best = max(quotes, key=lambda q: (votes.get(q["id"], 0), -q["id"]), default=None)
    return best if best and votes.get(best["id"], 0) > 0 else None


def parse_time(text):
    match = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", text or "")
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        return None
    return f"{int(match[1]):02d}:{match[2]}"


# ============================================================
# ГОЛОСОВАНИЕ
# ============================================================

def name_of(guild, user_id):
    member = guild.get_member(user_id) if guild else None
    return member.display_name if member else t("quotes.someone")


def round_embed(guild, rnd, quotes, closed=False):
    key = "quotes.round_closed" if closed else "quotes.round"
    embed = discord.Embed(title=t(f"{key}.title"), color=core.embed_color(),
                          description=t(f"{key}.text", count=len(quotes), ends=f"<t:{rnd['ends_at']}:R>"))
    return embed


async def start_round(guild, settings):
    """Опубликовать голосование. -> ключ ошибки или None."""
    quotes = pending_quotes(guild.id)
    if not quotes:
        return "quotes.nothing"
    channel = guild.get_channel(settings["channel_id"] or 0)
    if channel is None:
        return "quotes.no_channel"
    if core.bot_missing(channel):
        return "quotes.no_rights"
    ends_at = int(time.time()) + max(5, settings["duration_min"]) * 60
    round_id = _q("INSERT INTO quote_rounds (guild_id, channel_id, ends_at) VALUES (?, ?, ?)",
                  (guild.id, channel.id, ends_at), write=True)
    _q("UPDATE quotes SET round_id = ? WHERE guild_id = ? AND round_id IS NULL", (round_id, guild.id), write=True)
    rnd = get_round(round_id)
    view = discord.ui.View(timeout=None)
    view.add_item(QuoteVoteButton(round_id))
    try:
        message = await channel.send(embed=round_embed(guild, rnd, round_quotes(round_id)), view=view)
    except discord.HTTPException:
        _q("UPDATE quotes SET round_id = NULL WHERE round_id = ?", (round_id,), write=True)
        _q("DELETE FROM quote_rounds WHERE id = ?", (round_id,), write=True)
        return "quotes.no_rights"
    _q("UPDATE quote_rounds SET message_id = ? WHERE id = ?", (message.id, round_id), write=True)
    return None


def quote_card_png(guild, quote):
    import card_templates as ct
    data = ct.default_data("quote")
    data.update({"text": quote["text"], "author": name_of(guild, quote["author_id"])})
    return ct.render({"template": "quote"}, t("quotes.card_title"), data)


async def move_role(guild, role_id, member):
    """Роль «цитата дня» — только у победителя. -> ключ предупреждения или None."""
    role = guild.get_role(role_id or 0)
    if role is None:
        return None
    if not guild.me.guild_permissions.manage_roles or role >= guild.me.top_role:
        return "quotes.role_failed"
    try:
        for holder in list(role.members):
            if member is None or holder.id != member.id:
                await holder.remove_roles(role, reason="цитата дня перешла другому")
        if member is not None and role not in member.roles:
            await member.add_roles(role, reason="цитата дня")
    except discord.HTTPException:
        return "quotes.role_failed"
    return None


async def close_round(guild, rnd):
    quotes = round_quotes(rnd["id"])
    votes = votes_of(rnd["id"])
    best = winner(quotes, votes)
    _q("UPDATE quote_rounds SET closed = 1, winner_id = ? WHERE id = ?", (best["id"] if best else None, rnd["id"]),
       write=True)
    channel = guild.get_channel(rnd["channel_id"])
    if channel is None:
        return
    if rnd["message_id"]:
        try:
            message = await channel.fetch_message(rnd["message_id"])
            await message.edit(embed=round_embed(guild, rnd, quotes, closed=True), view=None)
        except discord.HTTPException:
            pass
    if best is None:
        await channel.send(t("quotes.no_votes"))
        return
    settings = get_settings(guild.id)
    member = guild.get_member(best["author_id"])
    warning = await move_role(guild, settings["role_id"], member) if settings["role_id"] else None
    png = await asyncio.to_thread(quote_card_png, guild, best)
    embed = discord.Embed(color=core.embed_color()).set_image(url="attachment://quote.png")
    text = t("quotes.winner", user=f"<@{best['author_id']}>", votes=votes.get(best["id"], 0),
             total=sum(votes.values()))
    if best["message_id"]:
        text += "\n" + t("quotes.jump", url=f"https://discord.com/channels/{guild.id}/{best['channel_id']}/"
                                            f"{best['message_id']}")
    try:
        await channel.send(text, embed=embed, file=discord.File(io.BytesIO(png), filename="quote.png"),
                           allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
    except discord.HTTPException as error:
        _log.warning("quote winner %s: %s", guild.id, error)
    if warning:
        _log.info("quote role %s: %s", guild.id, warning)


async def _loop(bot):
    import live
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            now = int(time.time())
            for (round_id,) in _q("SELECT id FROM quote_rounds WHERE closed = 0 AND ends_at <= ?", (now,)):
                rnd = get_round(round_id)
                guild = bot.get_guild(rnd["guild_id"])
                if guild is None:
                    _q("UPDATE quote_rounds SET closed = 1 WHERE id = ?", (round_id,), write=True)
                    continue
                await close_round(guild, rnd)
            local = live.now_local()
            today, clock = local.strftime("%Y-%m-%d"), local.strftime("%H:%M")
            for (guild_id,) in _q("SELECT guild_id FROM quote_settings WHERE enabled = 1"):
                settings = get_settings(guild_id)
                guild = bot.get_guild(guild_id)
                if guild is None or settings["last_run_date"] == today or clock < settings["vote_time"]:
                    continue
                settings["last_run_date"] = today
                save_settings(settings)
                problem = await start_round(guild, settings)
                if problem and problem != "quotes.nothing":
                    _log.warning("quote round %s: %s", guild_id, problem)
        except Exception:
            _log.exception("quotes loop")
        await asyncio.sleep(CHECK_EVERY)


def start(bot):
    ensure_tables()
    return asyncio.create_task(_loop(bot))


class QuoteVoteButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rb:qv:(?P<rid>\d+)"):
    def __init__(self, round_id, *, item=None):
        super().__init__(item or discord.ui.Button(label=t("quotes.vote_button")[:80], emoji="🗳️",
                                                   style=discord.ButtonStyle.primary, custom_id=f"rb:qv:{round_id}"))
        self.round_id = round_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["rid"]), item=item)

    async def callback(self, interaction):
        rnd = get_round(self.round_id)
        if rnd is None or rnd["closed"] or rnd["ends_at"] <= time.time():
            await reply(interaction, "quotes.closed")
            return
        view = VoteView(interaction, rnd)
        await interaction.response.send_message(embed=view.page_embed(interaction), view=view, ephemeral=True)


class VoteView(discord.ui.View):
    """Все цитаты раунда (по 8 на страницу) и меню голоса — видно только голосующему."""

    def __init__(self, interaction, rnd):
        super().__init__(timeout=1800)
        self.round_id = rnd["id"]
        self.quotes = round_quotes(rnd["id"])
        self.page = 0
        self.pages = max(1, -(-len(self.quotes) // PER_PAGE))
        mine = _q("SELECT quote_id FROM quote_votes WHERE round_id = ? AND voter_id = ?",
                  (rnd["id"], interaction.user.id), one=True)
        self.mine = mine[0] if mine else None
        guild = interaction.guild
        select = core.PagedSelect(placeholder=t("quotes.pick")[:150], row=0, options=[
            discord.SelectOption(label=f"№{n} · {quote['text']}"[:100], value=str(quote["id"]),
                                 description=name_of(guild, quote["author_id"])[:100],
                                 default=quote["id"] == self.mine)
            for n, quote in enumerate(self.quotes, 1)])
        select.on_pick = self._vote(select)
        self.add_item(select)
        if self.pages > 1:
            for step, emoji in ((-1, "◀️"), (1, "▶️")):
                button = discord.ui.Button(emoji=emoji, style=discord.ButtonStyle.secondary, row=1)
                button.callback = self._turn(step)
                self.add_item(button)

    def page_embed(self, interaction):
        start = self.page * PER_PAGE
        lines = []
        for n, quote in enumerate(self.quotes[start:start + PER_PAGE], start + 1):
            mark = " ✅" if quote["id"] == self.mine else ""
            lines.append(f"**№{n}**{mark} «{discord.utils.escape_markdown(quote['text'])}»\n"
                         f"— {discord.utils.escape_markdown(name_of(interaction.guild, quote['author_id']))}")
        rnd = get_round(self.round_id)
        return discord.Embed(title=t("quotes.vote_title"), color=core.embed_color(),
                             description="\n\n".join(lines)[:4000]).set_footer(
            text=t("quotes.vote_footer", page=self.page + 1, pages=self.pages, count=len(self.quotes))
        ).add_field(name=t("quotes.ends"), value=f"<t:{rnd['ends_at']}:R>")

    def _turn(self, step):
        async def callback(interaction):
            self.page = (self.page + step) % self.pages
            await interaction.response.edit_message(embed=self.page_embed(interaction), view=self)
        return callback

    def _vote(self, select):
        async def callback(interaction):
            rnd = get_round(self.round_id)
            if rnd is None or rnd["closed"] or rnd["ends_at"] <= time.time():
                await reply(interaction, "quotes.closed")
                return
            quote_id = int(select.values[0])
            quote = next((q for q in self.quotes if q["id"] == quote_id), None)
            if quote is None:
                await reply(interaction, "quotes.closed")
                return
            if quote["author_id"] == interaction.user.id:
                await reply(interaction, "quotes.own")
                return
            _q("INSERT OR REPLACE INTO quote_votes (round_id, voter_id, quote_id) VALUES (?, ?, ?)",
               (self.round_id, interaction.user.id, quote_id), write=True)
            self.mine = quote_id
            await interaction.response.edit_message(embed=self.page_embed(interaction), view=self)
            await interaction.followup.send(t("quotes.voted", n=self.quotes.index(quote) + 1), ephemeral=True)
        return callback


# ============================================================
# НАСТРОЙКИ: /interactive → «Цитата дня»
# ============================================================

def can_manage(interaction):
    from extended_modules import is_admin
    return interaction.guild is not None and is_admin(interaction)


def home_embed(interaction):
    settings = get_settings(interaction.guild.id)
    return panel_embed(
        interaction, "quotes.home",
        state=t("quotes.on") if settings["enabled"] else t("quotes.off"),
        channel=f"<#{settings['channel_id']}>" if settings["channel_id"] else t("quotes.not_set"),
        time=settings["vote_time"], duration=settings["duration_min"],
        role=f"<@&{settings['role_id']}>" if settings["role_id"] else t("quotes.no_role"),
        pending=len(pending_quotes(interaction.guild.id)))


async def open_home(interaction, back_target=None):
    if not can_manage(interaction):
        await reply(interaction, "common.need_admin")
        return
    view = QuotesHome(back_target=back_target)
    view.toggle.label = t("quotes.home.toggle_off" if get_settings(interaction.guild.id)["enabled"]
                          else "quotes.home.toggle")[:80]
    if interaction.response.is_done():
        await interaction.edit_original_response(embed=home_embed(interaction), view=view)
    else:
        await interaction.response.edit_message(embed=home_embed(interaction), view=view)


class QuotesHome(PanelView):
    texts = "quotes.home"
    button_order = ("toggle", "channel", "schedule", "role", "queue", "run_now")

    async def _ok(self, interaction):
        if can_manage(interaction):
            return get_settings(interaction.guild.id)
        await reply(interaction, "common.need_admin")
        return None

    @discord.ui.button(label="Включить", emoji="✅", style=discord.ButtonStyle.success)
    async def toggle(self, interaction, button):
        settings = await self._ok(interaction)
        if not settings:
            return
        if not settings["enabled"] and not settings["channel_id"]:
            await reply(interaction, "quotes.no_channel")
            return
        settings["enabled"] = 0 if settings["enabled"] else 1
        save_settings(settings)
        core.audit(interaction, "quotes.toggle", "guild", interaction.guild.id, str(settings["enabled"]))
        await open_home(interaction, self.back_target)

    @discord.ui.button(label="Канал", emoji="📣", style=discord.ButtonStyle.secondary)
    async def channel(self, interaction, button):
        if not await self._ok(interaction):
            return
        view = PanelView(back_target=(core.screen(interaction), self), timeout=600)
        select = discord.ui.ChannelSelect(placeholder=t("quotes.pick_channel")[:150],
                                          channel_types=[discord.ChannelType.text, discord.ChannelType.news])

        async def picked(i):
            settings = await self._ok(i)
            if settings:
                settings["channel_id"] = select.values[0].id
                save_settings(settings)
                await open_home(i, self.back_target)

        select.callback = picked
        view.add_item(select)
        await interaction.response.edit_message(view=view)

    @discord.ui.button(label="Время и длительность", emoji="🕘", style=discord.ButtonStyle.secondary)
    async def schedule(self, interaction, button):
        settings = await self._ok(interaction)
        if settings:
            await interaction.response.send_modal(ScheduleModal(settings, self.back_target))

    @discord.ui.button(label="Роль победителю", emoji="🎭", style=discord.ButtonStyle.secondary)
    async def role(self, interaction, button):
        if not await self._ok(interaction):
            return
        view = PanelView(back_target=(core.screen(interaction), self), timeout=600)
        select = discord.ui.RoleSelect(placeholder=t("quotes.pick_role")[:150], min_values=0, max_values=1)

        async def picked(i):
            settings = await self._ok(i)
            if not settings:
                return
            role = select.values[0] if select.values else None
            if role is not None and (role.managed or role >= i.guild.me.top_role):
                await reply(i, "quotes.role_too_high")
                return
            settings["role_id"] = role.id if role else None
            save_settings(settings)
            await open_home(i, self.back_target)

        select.callback = picked
        view.add_item(select)
        await interaction.response.edit_message(view=view)

    @discord.ui.button(label="Цитаты в очереди", emoji="📜", style=discord.ButtonStyle.secondary)
    async def queue(self, interaction, button):
        if not await self._ok(interaction):
            return
        quotes = pending_quotes(interaction.guild.id)
        if not quotes:
            await reply(interaction, "quotes.nothing")
            return
        view = PanelView(back_target=(core.screen(interaction), self), timeout=600)
        select = core.PagedSelect(placeholder=t("quotes.remove_pick")[:150], options=[
            discord.SelectOption(label=f"№{n} · {q['text']}"[:100], value=str(q["id"]),
                                 description=name_of(interaction.guild, q["author_id"])[:100])
            for n, q in enumerate(quotes, 1)])

        async def picked(i):
            if not await self._ok(i):
                return
            _q("DELETE FROM quotes WHERE id = ? AND guild_id = ? AND round_id IS NULL",
               (int(select.values[0]), i.guild.id), write=True)
            core.audit(i, "quotes.removed", "quote", int(select.values[0]))
            await open_home(i, self.back_target)

        select.on_pick = picked
        view.add_item(select)
        await interaction.response.edit_message(view=view)

    @discord.ui.button(label="Запустить сейчас", emoji="🗳️", style=discord.ButtonStyle.primary)
    async def run_now(self, interaction, button):
        settings = await self._ok(interaction)
        if not settings:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        problem = await start_round(interaction.guild, settings)
        core.audit(interaction, "quotes.round_started", "guild", interaction.guild.id, problem or "ok")
        await interaction.followup.send(t(problem) if problem else t("quotes.started"), ephemeral=True)


class ScheduleModal(Modal, title="ВРЕМЯ"):
    def __init__(self, settings, back_target):
        super().__init__(title=t("quotes.schedule_modal.title")[:45])
        self.back_target = back_target
        self.when = discord.ui.TextInput(label=t("quotes.schedule_modal.time")[:45], default=settings["vote_time"],
                                         max_length=5)
        self.minutes = discord.ui.TextInput(label=t("quotes.schedule_modal.duration")[:45],
                                            default=str(settings["duration_min"]), max_length=4)
        self.add_item(self.when)
        self.add_item(self.minutes)

    async def on_submit(self, interaction):
        if not can_manage(interaction):
            await reply(interaction, "common.need_admin")
            return
        when = parse_time(self.when.value)
        minutes = self.minutes.value.strip()
        if when is None or not minutes.isdigit() or not 5 <= int(minutes) <= 24 * 60:
            await reply(interaction, "quotes.bad_schedule")
            return
        settings = get_settings(interaction.guild.id)
        settings["vote_time"], settings["duration_min"] = when, int(minutes)
        save_settings(settings)
        await open_home(interaction, self.back_target)


# ============================================================
# МЕНЮ СООБЩЕНИЯ «В цитаты дня»
# ============================================================

def register(bot):
    @bot.tree.context_menu(name="В цитаты дня")
    @discord.app_commands.guild_only()
    async def to_quotes(interaction, message: discord.Message):
        if not await core.require_command_access(interaction, "quotes"):
            return
        settings = get_settings(interaction.guild.id)
        if not settings["enabled"]:
            await reply(interaction, "quotes.disabled")
            return
        if message.author.bot:
            await reply(interaction, "quotes.no_bots")
            return
        quote_id, problem = add_quote(interaction.guild.id, message, interaction.user.id)
        if problem:
            await reply(interaction, problem, max=MAX_PER_DAY)
            return
        await interaction.response.send_message(t("quotes.added", time=settings["vote_time"]), ephemeral=True)


DYNAMIC_ITEMS = [QuoteVoteButton]
