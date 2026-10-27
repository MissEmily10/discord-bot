"""
clan_quests.py
==============
Квесты кланов (docs/quests/README.md, CLANS_TODO п. 6, 11).

- На каждый 30-дневный срок клан получает ровно три квеста: один лёгкий,
  один средний, один сложный (списки — docs/quests/easy|medium|hard.md, по 150).
  Выполненный кланом квест ему больше не выпадает; выполненный всеми кланами
  уходит из выдачи.
- «Сдать квест» (/clan → «Квесты»): отчёт текстом и ссылками. Бот сам
  прикладывает, что видел: войс и сообщения клана за неделю и месяц.
- Админам — в канал проверки (настройка «Канал проверки») с постоянными
  кнопками «Принять» / «Вернуть» (с комментарием). Нет ответа 2 суток —
  напоминание.
- Принят — респекты клану в направлении квеста (лёгкий +1, средний +3,
  сложный +10), отметка, учёт для стипендии (за сутки до продления:
  1 → 1000, 2 → 2500, 3 → 5000 — clan_life).
- «Замена квеста» — товар магазина (раз за срок).
"""

import hashlib
import logging
import pathlib
import random
import re
import time

import discord

import core
from core import t, panel_embed, PanelView, Modal
from actions import reply
from database import get_connection

_log = logging.getLogger(__name__)

LEVELS = ("easy", "medium", "hard")
RESPECT = {"easy": 1, "medium": 3, "hard": 10}
DOCS = pathlib.Path(__file__).resolve().parent / "docs" / "quests"
_LINE = re.compile(r"^(\d+)\. \*\*(.+?)\*\* — (.+?)(?: \*Отчёт:\*\s*(.+?))? · Направление: (.+?)\s*$")
REMIND_AFTER = 2 * 24 * 3600
_catalog = {}


def catalog():
    """{уровень: {номер: {title, text, report, direction}}} из docs/quests."""
    if not _catalog:
        import clan_treasury
        for level in LEVELS:
            found = {}
            path = DOCS / f"{level}.md"
            for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
                match = _LINE.match(line.strip())
                if match:
                    found[int(match[1])] = {"title": match[2], "text": match[3], "report": match[4] or "",
                                            "direction": clan_treasury.direction_of(match[5]) or "community",
                                            "direction_label": match[5]}
            _catalog[level] = found
    return _catalog


def ensure_tables():
    connection = get_connection()
    connection.execute("""CREATE TABLE IF NOT EXISTS clan_quests (
        scope_id INTEGER NOT NULL, tag TEXT NOT NULL, term_no INTEGER NOT NULL, level TEXT NOT NULL,
        quest_no INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open', report TEXT NOT NULL DEFAULT '',
        comment TEXT NOT NULL DEFAULT '', submitted_by INTEGER, submitted_at INTEGER, reviewed_by INTEGER,
        channel_id INTEGER, message_id INTEGER, reminded INTEGER NOT NULL DEFAULT 0, swapped INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (scope_id, tag, term_no, level))""")
    connection.commit()
    connection.close()


_COLUMNS = ("scope_id", "tag", "term_no", "level", "quest_no", "status", "report", "comment", "submitted_by",
            "submitted_at", "reviewed_by", "channel_id", "message_id", "reminded", "swapped")


def _rows(sql, params=()):
    ensure_tables()
    connection = get_connection()
    rows = connection.execute(f"SELECT {', '.join(_COLUMNS)} FROM clan_quests {sql}", params).fetchall()
    connection.close()
    return [dict(zip(_COLUMNS, row)) for row in rows]


def _done_by(scope_id, tag=None):
    """Принятые квесты: {(уровень, номер)} клана или {(уровень, номер): {теги}} всех."""
    rows = _rows("WHERE scope_id = ? AND status = 'accepted'", (scope_id,))
    if tag:
        return {(row["level"], row["quest_no"]) for row in rows if row["tag"] == tag.upper()}
    found = {}
    for row in rows:
        found.setdefault((row["level"], row["quest_no"]), set()).add(row["tag"])
    return found


def assign(scope_id, tag, term_no):
    """Квесты срока: выдать недостающие (по одному на уровень). -> {уровень: строка}."""
    import clans
    tag = tag.upper()
    current = {row["level"]: row for row in _rows("WHERE scope_id = ? AND tag = ? AND term_no = ?",
                                                  (scope_id, tag, term_no))}
    if len(current) == len(LEVELS):
        return current
    mine = _done_by(scope_id, tag)
    everyone = _done_by(scope_id)
    active = {clan["tag"] for clan in clans.get_clans(scope_id)}
    seed = int(hashlib.sha256(f"{scope_id}:{tag}:{term_no}".encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    connection = get_connection()
    for level in LEVELS:
        if level in current:
            continue
        pool = [n for n in catalog()[level] if (level, n) not in mine
                and not (active and active <= everyone.get((level, n), set()))]
        if not pool:
            continue
        connection.execute("INSERT OR IGNORE INTO clan_quests (scope_id, tag, term_no, level, quest_no) "
                           "VALUES (?, ?, ?, ?, ?)", (scope_id, tag, term_no, level, rng.choice(pool)))
    connection.commit()
    connection.close()
    return {row["level"]: row for row in _rows("WHERE scope_id = ? AND tag = ? AND term_no = ?",
                                               (scope_id, tag, term_no))}


def done_count(scope_id, tag, term_no):
    return len(_rows("WHERE scope_id = ? AND tag = ? AND term_no = ? AND status = 'accepted'",
                     (scope_id, tag.upper(), term_no)))


def get(scope_id, tag, term_no, level):
    rows = _rows("WHERE scope_id = ? AND tag = ? AND term_no = ? AND level = ?", (scope_id, tag.upper(), term_no, level))
    return rows[0] if rows else None


def _update(row, **changes):
    connection = get_connection()
    sets = ", ".join(f"{key} = ?" for key in changes)
    connection.execute(f"UPDATE clan_quests SET {sets} WHERE scope_id = ? AND tag = ? AND term_no = ? AND level = ?",
                       (*changes.values(), row["scope_id"], row["tag"], row["term_no"], row["level"]))
    connection.commit()
    connection.close()


def swap(scope_id, tag, term_no, level):
    """Товар «Замена квеста»: другой квест того же уровня, раз за срок. -> новый номер | None."""
    row = assign(scope_id, tag, term_no).get(level)
    if row is None or row["swapped"] or row["status"] == "accepted":
        return None
    mine = _done_by(scope_id, tag) | {(level, row["quest_no"])}
    pool = [n for n in catalog()[level] if (level, n) not in mine]
    if not pool:
        return None
    number = random.Random(time.time()).choice(pool)
    _update(row, quest_no=number, swapped=1, status="open", report="", comment="")
    return number


def quest_info(row):
    return catalog()[row["level"]].get(row["quest_no"], {"title": "—", "text": "", "report": "",
                                                          "direction": "community", "direction_label": "—"})


def quests_text(scope_id, tag, term_no):
    lines = []
    for level, row in sorted(assign(scope_id, tag, term_no).items(), key=lambda kv: LEVELS.index(kv[0])):
        info = quest_info(row)
        lines.append(t("clan_quests.line", level=t(f"clan_quests.level.{level}"), title=info["title"],
                       status=t(f"clan_quests.status.{row['status']}"), text=info["text"],
                       direction=info["direction_label"]))
    return "\n\n".join(lines) or t("clan_quests.none")


# ============================================================
# СДАТЬ — ПРОВЕРИТЬ
# ============================================================

def seen_by_bot(scope_id, tag):
    """Что бот видел сам: войс и сообщения клана за неделю и за месяц."""
    import clan_billboard
    week = clan_billboard.activity(scope_id, clan_billboard.week_key()).get(tag.upper(), (0, 0))
    month = clan_billboard.activity(scope_id, clan_billboard.month_key()).get(tag.upper(), (0, 0))
    return t("clan_quests.seen", week_voice=f"{week[0] / 3600:.1f}", week_messages=week[1],
             month_voice=f"{month[0] / 3600:.1f}", month_messages=month[1])


class ReviewButton(discord.ui.DynamicItem[discord.ui.Button],
                   template=r"rb:clq:(?P<scope>\d+):(?P<tag>[^:]{1,12}):(?P<term>\d+):(?P<level>easy|medium|hard):(?P<act>[ar])"):
    def __init__(self, scope_id, tag, term_no, level, act, *, item=None):
        accept = act == "a"
        super().__init__(item or discord.ui.Button(
            label=t("clan_quests.accept" if accept else "clan_quests.return")[:80], emoji="✅" if accept else "↩️",
            style=discord.ButtonStyle.success if accept else discord.ButtonStyle.secondary,
            custom_id=f"rb:clq:{scope_id}:{tag}:{term_no}:{level}:{act}"))
        self.key = (int(scope_id), tag, int(term_no), level)
        self.act = act

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["scope"], match["tag"], match["term"], match["level"], match["act"], item=item)

    async def callback(self, interaction):
        if not can_review(interaction):
            await reply(interaction, "clan_quests.no_rights")
            return
        row = get(*self.key)
        if row is None or row["status"] != "submitted":
            await reply(interaction, "clan_quests.already")
            return
        if self.act == "r":
            await interaction.response.send_modal(ReturnModal(self.key))
            return
        await decide(interaction, row, True)


def review_view(row):
    view = discord.ui.View(timeout=None)
    for act in ("a", "r"):
        view.add_item(ReviewButton(row["scope_id"], row["tag"], row["term_no"], row["level"], act))
    return view


def can_review(interaction):
    return core.is_owner(interaction) or \
        core.level_value(core.get_user_level(interaction)) >= core.level_value("admin")


async def decide(interaction, row, accepted, comment=""):
    import clan_treasury
    import clans
    import clan_members
    info = quest_info(row)
    if accepted:
        amount = RESPECT[row["level"]]
        clan_treasury.record(row["scope_id"], row["tag"], "", 0, amount, f"квест: {info['title']}",
                             source_key=f"quest:{row['tag']}:{row['term_no']}:{row['level']}:{row['quest_no']}",
                             by_user=interaction.user.id, direction=info["direction"])
    _update(row, status="accepted" if accepted else "returned", comment=comment[:500], reviewed_by=interaction.user.id)
    core.audit(interaction, "clan.quest_" + ("accepted" if accepted else "returned"), "clan", row["tag"], info["title"])
    result = t("clan_quests.result_accepted" if accepted else "clan_quests.result_returned",
               who=interaction.user.mention, comment=comment or "—")
    try:
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else None
        if embed is not None:
            embed.add_field(name=t("clan_quests.result"), value=result[:1024], inline=False)
        if interaction.response.is_done():
            await interaction.message.edit(embed=embed, view=None)
        else:
            await interaction.response.edit_message(embed=embed, view=None)
    except (discord.HTTPException, AttributeError):  # сообщение проверки уже удалили
        pass
    clan = clans.get_clan(row["scope_id"], row["tag"])
    if clan:
        guild = clan_members.clan_guild(interaction.client, clan, interaction.guild)
        await clan_members.tell_clan(guild, clan, t("clan_quests.notice_accepted" if accepted else
                                                    "clan_quests.notice_returned", tag=row["tag"],
                                                    title=info["title"], respect=RESPECT[row["level"]],
                                                    comment=comment or "—"))


class ReturnModal(Modal, title="ВЕРНУТЬ НА ДОРАБОТКУ"):
    texts = "clan_quests.return_modal"
    comment = discord.ui.TextInput(label="Что доделать", style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, key):
        super().__init__()
        self.key = key

    async def on_submit(self, interaction):
        row = get(*self.key)
        if row is None or row["status"] != "submitted":
            await reply(interaction, "clan_quests.already")
            return
        await interaction.response.defer()
        await decide(interaction, row, False, self.comment.value.strip())


class SubmitModal(Modal, title="СДАТЬ КВЕСТ"):
    texts = "clan_quests.submit_modal"
    report = discord.ui.TextInput(label="Отчёт", style=discord.TextStyle.paragraph, max_length=1500)
    links = discord.ui.TextInput(label="Ссылки на скрины / сообщения", style=discord.TextStyle.paragraph,
                                 max_length=800, required=False)

    def __init__(self, scope_id, tag, term_no, level):
        super().__init__()
        self.key = (scope_id, tag, term_no, level)

    async def on_submit(self, interaction):
        import clan_life
        row = get(*self.key)
        if row is None or row["status"] in ("submitted", "accepted"):
            await reply(interaction, "clan_quests.already")
            return
        channel_id = clan_life.setting(self.key[0], "review_channel")
        channel = interaction.client.get_channel(int(channel_id)) if channel_id else None
        if channel is None:
            await reply(interaction, "clan_quests.no_review_channel")
            return
        info = quest_info(row)
        report = self.report.value.strip() + (f"\n\n{self.links.value.strip()}" if self.links.value.strip() else "")
        embed = discord.Embed(title=t("clan_quests.card_title", tag=row["tag"], title=info["title"])[:256],
                              description=t("clan_quests.card_text", level=t(f"clan_quests.level.{row['level']}"),
                                            task=info["text"], need=info["report"] or "—",
                                            who=interaction.user.mention, report=report,
                                            seen=seen_by_bot(row["scope_id"], row["tag"]))[:4096], color=0x2B2D31)
        try:
            message = await channel.send(embed=embed, view=review_view(row))
        except discord.HTTPException:
            await reply(interaction, "clan_quests.no_review_channel")
            return
        _update(row, status="submitted", report=report[:2000], submitted_by=interaction.user.id,
                submitted_at=int(time.time()), channel_id=channel.id, message_id=message.id, reminded=0)
        core.audit(interaction, "clan.quest_submitted", "clan", row["tag"], info["title"])
        await reply(interaction, "clan_quests.sent", title=info["title"])


class QuestsView(PanelView):
    """Квесты срока: три квеста и «Сдать» у каждого (глава, замы, представители)."""

    clear_files_on_back = True

    def __init__(self, scope_id, tag, term_no, can_submit, back_target=None):
        super().__init__(back_target=back_target, timeout=900)
        rows = assign(scope_id, tag, term_no)
        if not can_submit:
            return
        for level in LEVELS:
            row = rows.get(level)
            if row is None or row["status"] in ("submitted", "accepted"):
                continue
            button = discord.ui.Button(label=t("clan_quests.submit", level=t(f"clan_quests.level.{level}"))[:80],
                                       emoji="📨", style=discord.ButtonStyle.primary)

            async def submit(i, level=level):
                await i.response.send_modal(SubmitModal(scope_id, tag, term_no, level))
            button.callback = submit
            self.add_item(button)


def quests_embed(interaction, scope_id, tag, term_no):
    embed = panel_embed(interaction, "clan_quests.panel", hints=False, tag=tag, term=term_no,
                        quests=quests_text(scope_id, tag, term_no)[:3500], done=done_count(scope_id, tag, term_no))
    embed.set_image(url="attachment://quests.png")
    return embed


async def quests_file(scope_id, tag, term_no):
    """Картинка квестов срока (макет clans.quests)."""
    import re

    import clan_art
    rows = []
    for level, row in sorted(assign(scope_id, tag, term_no).items(), key=lambda kv: LEVELS.index(kv[0])):
        status = re.sub(r"^[^\w«]+", "", t(f"clan_quests.status.{row['status']}"))  # без эмодзи — их нет в шрифте
        rows.append((t(f"clan_quests.level.{level}").lower(), quest_info(row)["title"], status))
    return await clan_art.file(clan_art.render_quests, f"срок {term_no}", rows, filename="quests.png")


async def remind_stale(bot):
    """Проверка висит дольше 2 суток — одно напоминание в тот же канал."""
    for row in _rows("WHERE status = 'submitted' AND reminded = 0 AND submitted_at < ?",
                     (int(time.time()) - REMIND_AFTER,)):
        channel = bot.get_channel(row["channel_id"] or 0)
        if channel is not None:
            try:
                await channel.send(t("clan_quests.remind", tag=row["tag"], title=quest_info(row)["title"],
                                     link=f"https://discord.com/channels/{channel.guild.id}/{channel.id}/"
                                          f"{row['message_id']}"))
            except discord.HTTPException:
                pass
        _update(row, reminded=1)


DYNAMIC_ITEMS = [ReviewButton]
