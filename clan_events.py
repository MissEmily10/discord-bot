"""
clan_events.py
==============
Клановые ивенты (CLANS_TODO п. 7, «Клановые ивенты»): провести — бесплатно,
за ивент награждают.

1. Глава / зам / представитель анонсирует ивент: название, когда, голосовой
   канал, для кого (свои / весь сервер) — /clan → «Ивенты».
2. Во время ивента бот сам считает войс раз в минуту: сколько разных людей
   было, сколько из них не из клана, сколько минут в канале кто-то был.
   Ивент заканчивается кнопкой «Закончить» или сам через 6 часов.
3. После — отчёт (текст и ссылки на скрины) → админам «Принять / Отклонить»
   с тем, что видел бот, и подсказкой, какой это тип.
4. Принято — награда клану (направление 🎉):

   | Тип | Условие (бот видит войс) | Респект | В казну |
   |---|---|---|---|
   | Клановый | ≥ половины клана, ≥ 1 ч | +1 | — |
   | Открытый | ≥ 10 гостей не из клана, ≥ 1 ч | +3 | +500 |
   | Большой | ≥ 25 гостей или ≥ 2 ч с ≥ 10 гостями | +6 | +1500 |

   Не больше 4 принятых ивентов в месяц и одного в день на клан.
"""

import json
import logging
import re
import time
from datetime import datetime

import discord

import core
from core import t, panel_embed, PanelView, Modal
from actions import reply
from database import get_connection

_log = logging.getLogger(__name__)

TIERS = {"own": (1, 0), "open": (3, 500), "big": (6, 1500)}
MAX_PER_MONTH = 4
MAX_HOURS = 6
_WHEN = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\s+(\d{1,2})[:.](\d{2})\s*$")


def ensure_tables():
    connection = get_connection()
    connection.execute("""CREATE TABLE IF NOT EXISTS clan_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, scope_id INTEGER NOT NULL, tag TEXT NOT NULL, title TEXT NOT NULL,
        guild_id INTEGER, voice_id INTEGER NOT NULL, audience TEXT NOT NULL DEFAULT 'own', starts_at INTEGER NOT NULL,
        ended_at INTEGER, status TEXT NOT NULL DEFAULT 'planned', seen_members TEXT NOT NULL DEFAULT '[]',
        seen_guests TEXT NOT NULL DEFAULT '[]', active_minutes INTEGER NOT NULL DEFAULT 0,
        guest_minutes INTEGER NOT NULL DEFAULT 0, by_user INTEGER, report TEXT NOT NULL DEFAULT '',
        channel_id INTEGER, message_id INTEGER, tier TEXT, decided_by INTEGER)""")
    connection.commit()
    connection.close()


_COLUMNS = ("id", "scope_id", "tag", "title", "guild_id", "voice_id", "audience", "starts_at", "ended_at", "status",
            "seen_members", "seen_guests", "active_minutes", "guest_minutes", "by_user", "report", "channel_id",
            "message_id", "tier", "decided_by")


def _rows(sql, params=()):
    ensure_tables()
    connection = get_connection()
    rows = connection.execute(f"SELECT {', '.join(_COLUMNS)} FROM clan_events {sql}", params).fetchall()
    connection.close()
    found = []
    for row in rows:
        event = dict(zip(_COLUMNS, row))
        event["seen_members"] = set(json.loads(event["seen_members"]))
        event["seen_guests"] = set(json.loads(event["seen_guests"]))
        found.append(event)
    return found


def get_event(event_id):
    rows = _rows("WHERE id = ?", (event_id,))
    return rows[0] if rows else None


def _save(event, **changes):
    event.update(changes)
    connection = get_connection()
    connection.execute(
        "UPDATE clan_events SET status = ?, ended_at = ?, seen_members = ?, seen_guests = ?, active_minutes = ?, "
        "guest_minutes = ?, report = ?, channel_id = ?, message_id = ?, tier = ?, decided_by = ? WHERE id = ?",
        (event["status"], event["ended_at"], json.dumps(sorted(event["seen_members"])),
         json.dumps(sorted(event["seen_guests"])), event["active_minutes"], event["guest_minutes"], event["report"],
         event["channel_id"], event["message_id"], event["tier"], event["decided_by"], event["id"]))
    connection.commit()
    connection.close()


def set_status(event_id, old, new):
    connection = get_connection()
    cursor = connection.execute("UPDATE clan_events SET status = ? WHERE id = ? AND status = ?", (new, event_id, old))
    connection.commit()
    connection.close()
    return cursor.rowcount > 0


def create(scope_id, tag, title, guild_id, voice_id, audience, starts_at, by_user):
    ensure_tables()
    connection = get_connection()
    cursor = connection.execute("INSERT INTO clan_events (scope_id, tag, title, guild_id, voice_id, audience, starts_at, "
                                "by_user) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (scope_id, tag.upper(), title[:80], guild_id, voice_id, audience, int(starts_at),
                                 by_user))
    connection.commit()
    connection.close()
    return cursor.lastrowid


def parse_when(text, now=None):
    """«сейчас» / «25.12 20:00» / «25.12.2026 20:00» (МСК) -> unix | None."""
    import clan_life
    raw = (text or "").strip().lower()
    now = now or time.time()
    if raw in ("", "сейчас", "now"):
        return int(now)
    match = _WHEN.match(raw)
    if not match:
        return None
    today = datetime.fromtimestamp(now, clan_life.MSK)
    year = int(match[3]) if match[3] else today.year
    year = year + 2000 if year < 100 else year
    try:
        moment = datetime(year, int(match[2]), int(match[1]), int(match[4]), int(match[5]), tzinfo=clan_life.MSK)
    except ValueError:
        return None
    return int(moment.timestamp())


def classify(event, clan_size):
    """Какой это ивент по данным бота: big / open / own / None."""
    hours = event["active_minutes"] / 60
    guests = len(event["seen_guests"])
    if guests >= 25 or (hours >= 2 and guests >= 10):
        return "big"
    if guests >= 10 and hours >= 1:
        return "open"
    if clan_size and len(event["seen_members"]) >= clan_size / 2 and hours >= 1:
        return "own"
    return None


def limit_problem(event):
    """Не больше 4 принятых в месяц и одного в день. -> ключ ошибки | None."""
    import clan_life
    month = clan_life.month_key(event["starts_at"])
    day = datetime.fromtimestamp(event["starts_at"], clan_life.MSK).date()
    accepted = _rows("WHERE scope_id = ? AND tag = ? AND status = 'accepted' AND id != ?",
                     (event["scope_id"], event["tag"], event["id"]))
    if sum(1 for e in accepted if clan_life.month_key(e["starts_at"]) == month) >= MAX_PER_MONTH:
        return "clan_events.limit_month"
    if any(datetime.fromtimestamp(e["starts_at"], clan_life.MSK).date() == day for e in accepted):
        return "clan_events.limit_day"
    return None


# ============================================================
# УЧЁТ ВОЙСА — раз в минуту (из цикла биллборда)
# ============================================================

def tick(bot, now=None):
    import clan_members
    now = int(now or time.time())
    for event in _rows("WHERE status IN ('planned', 'running') AND starts_at <= ?", (now,)):
        guild = bot.get_guild(event["guild_id"] or 0)
        channel = guild.get_channel(event["voice_id"]) if guild else None
        if event["status"] == "planned":
            event["status"] = "running"
        people = [m for m in getattr(channel, "members", []) if not m.bot]
        members = set(clan_members.member_ids(event["scope_id"], event["tag"]))
        for person in people:
            (event["seen_members"] if person.id in members else event["seen_guests"]).add(person.id)
        if people:
            event["active_minutes"] += 1
            if any(p.id not in members for p in people):
                event["guest_minutes"] += 1
        if now - event["starts_at"] >= MAX_HOURS * 3600:
            event["status"], event["ended_at"] = "ended", now
        _save(event)


# ============================================================
# ПАНЕЛЬ /clan → «Ивенты»
# ============================================================

def events_embed(interaction, scope_id, tag):
    rows = _rows("WHERE scope_id = ? AND tag = ? ORDER BY id DESC LIMIT 6", (scope_id, tag.upper()))
    lines = [t("clan_events.line", title=e["title"], when=f"<t:{e['starts_at']}:f>",
               status=t(f"clan_events.status.{e['status']}"), people=len(e["seen_members"]) + len(e["seen_guests"]),
               guests=len(e["seen_guests"]), hours=f"{e['active_minutes'] / 60:.1f}") for e in rows]
    return panel_embed(interaction, "clan_events.panel", hints=False, tag=tag, events="\n".join(lines) or "—")


class EventsView(PanelView):
    texts = "clan_events.panel"

    def __init__(self, scope_id, tag, can_run, back_target=None):
        super().__init__(back_target=back_target, timeout=900)
        self.scope_id, self.tag = scope_id, tag
        if not can_run:
            for item in (self.announce, self.finish, self.report):
                self.remove_item(item)
            return
        rows = _rows("WHERE scope_id = ? AND tag = ? ORDER BY id DESC LIMIT 6", (scope_id, tag.upper()))
        self.running = next((e for e in rows if e["status"] in ("planned", "running")), None)
        self.ended = next((e for e in rows if e["status"] == "ended"), None)
        if self.running is None:
            self.remove_item(self.finish)
        if self.ended is None:
            self.remove_item(self.report)

    @discord.ui.button(label="Анонсировать ивент", emoji="📣", style=discord.ButtonStyle.success)
    async def announce(self, interaction, button):
        await interaction.response.send_modal(AnnounceModal(self.scope_id, self.tag))

    @discord.ui.button(label="Ивент закончился", emoji="⏹️", style=discord.ButtonStyle.secondary)
    async def finish(self, interaction, button):
        event = get_event(self.running["id"])
        if event and event["status"] in ("planned", "running"):
            _save(event, status="ended", ended_at=int(time.time()))
        await interaction.response.edit_message(embed=events_embed(interaction, self.scope_id, self.tag),
                                                view=EventsView(self.scope_id, self.tag, True,
                                                                back_target=self.back_target))

    @discord.ui.button(label="Сдать отчёт", emoji="📨", style=discord.ButtonStyle.primary)
    async def report(self, interaction, button):
        await interaction.response.send_modal(ReportModal(self.ended["id"]))


class AnnounceModal(Modal):
    def __init__(self, scope_id, tag):
        super().__init__(title=t("clan_events.announce_modal.title")[:45])
        self.scope_id, self.tag = scope_id, tag
        self.name = discord.ui.TextInput(label=t("clan_events.announce_modal.name")[:45], max_length=80)
        self.when = discord.ui.TextInput(label=t("clan_events.announce_modal.when")[:45], max_length=20,
                                         default="сейчас")
        self.audience = discord.ui.TextInput(label=t("clan_events.announce_modal.audience")[:45], max_length=10,
                                             default="сервер")
        self.voice = discord.ui.ChannelSelect(channel_types=[discord.ChannelType.voice, discord.ChannelType.stage_voice])
        for item in (self.name, self.when, self.audience):
            self.add_item(item)
        self.add_item(discord.ui.Label(text=t("clan_events.announce_modal.voice")[:45], component=self.voice))

    async def on_submit(self, interaction):
        when = parse_when(self.when.value)
        if when is None:
            await reply(interaction, "clan_events.bad_when")
            return
        if not self.voice.values:
            await reply(interaction, "clan_events.no_voice")
            return
        audience = "own" if self.audience.value.strip().lower().startswith("сво") else "server"
        event_id = create(self.scope_id, self.tag, self.name.value.strip(), interaction.guild.id,
                          self.voice.values[0].id, audience, when, interaction.user.id)
        core.audit(interaction, "clan.event_announced", "clan", self.tag, f"#{event_id}")
        await reply(interaction, "clan_events.announced", title=self.name.value.strip(), when=f"<t:{when}:R>",
                    voice=f"<#{self.voice.values[0].id}>")


class ReportModal(Modal):
    def __init__(self, event_id):
        super().__init__(title=t("clan_events.report_modal.title")[:45])
        self.event_id = event_id
        self.text = discord.ui.TextInput(label=t("clan_events.report_modal.text")[:45],
                                         style=discord.TextStyle.paragraph, max_length=1500)
        self.links = discord.ui.TextInput(label=t("clan_events.report_modal.links")[:45],
                                          style=discord.TextStyle.paragraph, max_length=800, required=False)
        self.add_item(self.text)
        self.add_item(self.links)

    async def on_submit(self, interaction):
        import clan_life
        import clan_members
        event = get_event(self.event_id)
        if event is None or not set_status(self.event_id, "ended", "review"):
            await reply(interaction, "clan_events.already")
            return
        channel_id = clan_life.setting(event["scope_id"], "review_channel")
        channel = interaction.client.get_channel(int(channel_id)) if channel_id else None
        if channel is None:
            set_status(self.event_id, "review", "ended")
            await reply(interaction, "clan_quests.no_review_channel")
            return
        size = len(clan_members.member_ids(event["scope_id"], event["tag"]))
        tier = classify(event, size)
        report = self.text.value.strip() + (f"\n\n{self.links.value.strip()}" if self.links.value.strip() else "")
        embed = discord.Embed(
            title=t("clan_events.card_title", tag=event["tag"], title=event["title"])[:256], color=0x2B2D31,
            description=t("clan_events.card_text", who=interaction.user.mention,
                          audience=t(f"clan_events.audience.{event['audience']}"), voice=f"<#{event['voice_id']}>",
                          hours=f"{event['active_minutes'] / 60:.1f}", members=len(event["seen_members"]), size=size,
                          guests=len(event["seen_guests"]),
                          tier=t(f"clan_events.tier.{tier}") if tier else t("clan_events.tier.none"),
                          report=report)[:4096])
        view = discord.ui.View(timeout=None)
        for key in ("own", "open", "big", "no"):
            view.add_item(EventDecision(event["id"], key))
        try:
            message = await channel.send(embed=embed, view=view)
        except discord.HTTPException:
            set_status(self.event_id, "review", "ended")
            await reply(interaction, "clan_quests.no_review_channel")
            return
        _save(get_event(self.event_id), report=report[:2000], channel_id=channel.id, message_id=message.id)
        await reply(interaction, "clan_events.sent")


class EventDecision(discord.ui.DynamicItem[discord.ui.Button], template=r"rb:cle:(?P<eid>\d+):(?P<tier>own|open|big|no)"):
    def __init__(self, event_id, tier, *, item=None):
        super().__init__(item or discord.ui.Button(
            label=t(f"clan_events.decide.{tier}")[:80],
            style=discord.ButtonStyle.secondary if tier == "no" else discord.ButtonStyle.success,
            custom_id=f"rb:cle:{event_id}:{tier}"))
        self.event_id, self.tier = event_id, tier

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["eid"]), match["tier"], item=item)

    async def callback(self, interaction):
        import clan_members
        import clan_quests
        import clan_treasury
        import clans
        if not clan_quests.can_review(interaction):
            await reply(interaction, "clan_quests.no_rights")
            return
        event = get_event(self.event_id)
        if event is None or event["status"] != "review":
            await reply(interaction, "clan_events.already")
            return
        if self.tier != "no":
            problem = limit_problem(event)
            if problem:
                await reply(interaction, problem)
                return
        if not set_status(self.event_id, "review", "rejected" if self.tier == "no" else "accepted"):
            await reply(interaction, "clan_events.already")
            return
        event = get_event(self.event_id)
        _save(event, tier=None if self.tier == "no" else self.tier, decided_by=interaction.user.id)
        if self.tier != "no":
            respect, money = TIERS[self.tier]
            clan_treasury.record(event["scope_id"], event["tag"], "", money, respect, f"ивент: {event['title']}",
                                 source_key=f"event:{event['id']}", by_user=interaction.user.id, direction="events")
        core.audit(interaction, "clan.event_" + ("rejected" if self.tier == "no" else "accepted"), "clan",
                   event["tag"], f"#{event['id']} {self.tier}")
        result = t("clan_events.result_" + ("rejected" if self.tier == "no" else "accepted"),
                   who=interaction.user.mention, tier=t(f"clan_events.tier.{self.tier}") if self.tier != "no" else "")
        try:
            embed = interaction.message.embeds[0]
            embed.add_field(name=t("clan_quests.result"), value=result[:1024], inline=False)
            await interaction.response.edit_message(embed=embed, view=None)
        except (discord.HTTPException, AttributeError, IndexError):
            pass
        clan = clans.get_clan(event["scope_id"], event["tag"])
        if clan:
            await clan_members.tell_clan(clan_members.clan_guild(interaction.client, clan, interaction.guild), clan,
                                         t("clan_events.notice_" + ("rejected" if self.tier == "no" else "accepted"),
                                           tag=event["tag"], title=event["title"],
                                           reward=t(f"clan_events.reward.{self.tier}") if self.tier != "no" else ""))


DYNAMIC_ITEMS = [EventDecision]
