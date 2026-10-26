"""
clan_members.py
===============
Состав клана в Purr (этап «а» переноса кланов, CLANS_TODO п. 8 и 9):
вступление по заявке, приглашение, выход, исключение, команда (1 глава,
2 зама, 2 представителя), передача главы, замена главы, ушедшего с сервера.

/clan — у каждого своя панель: не в клане — список кланов и «Подать заявку»;
в клане — состав и кнопки по правам:

| | Глава | Зам | Представитель | Участник |
|---|---|---|---|---|
| Принять заявку, пригласить | да | да | да | — |
| Исключить | любого | только рядовых | — | — |
| Назначить / снять замов и представителей | да | — | — | — |
| Передать главу (новый глава подтверждает) | да | — | — | — |
| Выйти | сначала передать главу | да | да | да |

Админы Purr и создатель бота могут то же, что глава (на случай споров).

Заявки и приглашения — постоянные кнопки «Принять / Отклонить» (работают
после перезапуска, второе нажатие ничего не меняет): заявка — в текстовый
канал клана (его категория), иначе главе в ЛС; приглашение и передача главы —
тому, кого зовут, в ЛС, иначе в канал, где нажали.

Роль клана выдаёт и снимает Purr. Если роль выдал или снял кто-то другой
(бот сервера, админ руками) — состав подстраивается сам. Глава ушёл с
сервера → главой становится зам с наибольшим стажем в клане, потом
представитель, потом самый давний участник; клану сообщение в его канал.
"""

import json
import logging
import time

import discord

import core
import ecosystem
from core import Modal, PanelView, t, panel_embed
from actions import reply
from database import get_connection

_log = logging.getLogger(__name__)

MAX_MEMBERS = 50
MAX_DEPUTIES = 2
MAX_REPS = 2
REQUEST_TTL = 7 * 24 * 3600  # заявка/приглашение без ответа больше недели — уже не действует


# ============================================================
# ХРАНИЛИЩЕ
# ============================================================

def ensure_tables():
    connection = get_connection()
    # один человек — один клан в области (связке серверов)
    connection.execute("""CREATE TABLE IF NOT EXISTS clan_members (
        scope_id INTEGER NOT NULL, user_id INTEGER NOT NULL, tag TEXT NOT NULL, joined_at INTEGER NOT NULL,
        PRIMARY KEY (scope_id, user_id)
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS clan_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT, scope_id INTEGER NOT NULL, tag TEXT NOT NULL,
        user_id INTEGER NOT NULL, kind TEXT NOT NULL, by_user INTEGER, text TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open', channel_id INTEGER, message_id INTEGER,
        created_at INTEGER NOT NULL, decided_by INTEGER, decided_at INTEGER
    )""")
    # одна открытая заявка (приглашение, передача) на человека и клан — повторное нажатие не плодит дубли
    # клан, чей состав уже взят с роли (один раз; дальше состав следует за событиями роли)
    connection.execute("""CREATE TABLE IF NOT EXISTS clan_seeded (
        scope_id INTEGER NOT NULL, tag TEXT NOT NULL, seeded_at INTEGER NOT NULL, PRIMARY KEY (scope_id, tag)
    )""")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS clan_requests_open ON clan_requests "
                       "(scope_id, tag, user_id, kind) WHERE status = 'open'")
    connection.commit()
    connection.close()


def _q(sql, params=(), one=False, write=False):
    ensure_tables()
    connection = get_connection()
    try:
        cursor = connection.execute(sql, params)
        if write:
            connection.commit()
            return cursor.rowcount
        return cursor.fetchone() if one else cursor.fetchall()
    finally:
        connection.close()


def members(scope_id, tag):
    """[(user_id, joined_at)] по стажу — давние первыми."""
    return [(row[0], row[1]) for row in _q("SELECT user_id, joined_at FROM clan_members WHERE scope_id = ? AND "
                                           "tag = ? ORDER BY joined_at, user_id", (scope_id, tag.upper()))]


def limit_for(scope_id, tag):
    """Сколько человек может быть в клане: 50 + купленные места (магазин, до +10)."""
    import clan_shop
    return MAX_MEMBERS + clan_shop.extra_slots(scope_id, tag)


def member_ids(scope_id, tag):
    return [user_id for user_id, _ in members(scope_id, tag)]


def tag_of(scope_id, user_id):
    row = _q("SELECT tag FROM clan_members WHERE scope_id = ? AND user_id = ?", (scope_id, user_id), one=True)
    return row[0] if row else None


def clan_of(scope_id, user_id):
    """Клан человека (по составу; глава/команда без записи в составе — тоже) или None."""
    import clans
    tag = tag_of(scope_id, user_id)
    if tag:
        clan = clans.get_clan(scope_id, tag)
        if clan and clan["status"] != "dissolved":
            return clan
    for clan in clans.get_clans(scope_id):
        if user_id == clan["leader_id"] or user_id in clan["deputies"] or user_id in clan["reps"]:
            add_member(scope_id, clan["tag"], user_id)
            return clan
    return None


def add_member(scope_id, tag, user_id, joined_at=None):
    """-> True, если добавлен (или уже в этом клане); False — он в другом клане или клан полон."""
    ensure_tables()
    limit = limit_for(scope_id, tag)  # до транзакции: внутри неё вторая связь с базой ждала бы блокировку
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT tag FROM clan_members WHERE scope_id = ? AND user_id = ?",
                                 (scope_id, user_id)).fetchone()
        if row:
            connection.rollback()
            return row[0] == tag.upper()
        count = connection.execute("SELECT COUNT(*) FROM clan_members WHERE scope_id = ? AND tag = ?",
                                   (scope_id, tag.upper())).fetchone()[0]
        if count >= limit:
            connection.rollback()
            return False
        connection.execute("INSERT INTO clan_members (scope_id, user_id, tag, joined_at) VALUES (?, ?, ?, ?)",
                           (scope_id, user_id, tag.upper(), int(joined_at or time.time())))
        connection.commit()
        return True
    finally:
        connection.close()


def remove_member(scope_id, user_id):
    return _q("DELETE FROM clan_members WHERE scope_id = ? AND user_id = ?", (scope_id, user_id), write=True) > 0


def set_team(scope_id, tag, leader_id, deputies, reps):
    import clans
    clans.ensure_tables()
    _q("UPDATE clans SET leader_id = ?, deputies_json = ?, reps_json = ? WHERE scope_id = ? AND tag = ?",
       (leader_id, json.dumps(list(deputies)[:MAX_DEPUTIES]), json.dumps(list(reps)[:MAX_REPS]),
        scope_id, tag.upper()), write=True)
    return clans.get_clan(scope_id, tag)


def seed(guild, clan):
    """
    Один раз на клан: в состав — все с ролью клана и команда (стаж — с этого
    момента). Потом с роли не перечитываем: кэш Discord обновляется с задержкой,
    и только что исключённый вернулся бы — дальше состав ведут события роли.
    """
    scope_id = clan["scope_id"]
    role = guild.get_role(clan["role_id"]) if guild is not None and clan.get("role_id") else None
    if role is None or _q("SELECT 1 FROM clan_seeded WHERE scope_id = ? AND tag = ?", (scope_id, clan["tag"]),
                          one=True):
        return
    _q("INSERT OR IGNORE INTO clan_seeded (scope_id, tag, seeded_at) VALUES (?, ?, ?)",
       (scope_id, clan["tag"], int(time.time())), write=True)
    known = set(member_ids(scope_id, clan["tag"]))
    people = [clan["leader_id"], *clan["deputies"], *clan["reps"]] + [m.id for m in (role.members if role else [])]
    for user_id in people:
        if user_id and user_id not in known:
            add_member(scope_id, clan["tag"], user_id)
            known.add(user_id)


# ============================================================
# ПРАВА
# ============================================================

def rank_of(clan, user_id):
    """leader / deputy / rep / member / None."""
    if clan is None:
        return None
    if user_id == clan["leader_id"]:
        return "leader"
    if user_id in clan["deputies"]:
        return "deputy"
    if user_id in clan["reps"]:
        return "rep"
    return "member" if tag_of(clan["scope_id"], user_id) == clan["tag"] else None


def is_admin(guild, member):
    import clans
    return guild is not None and member is not None and clans.can_manage(guild, member)


def can(clan, actor_id, action, target_id=None, admin=False):
    """Может ли actor: accept (заявки, приглашения), kick, team, transfer, leave."""
    rank = "leader" if admin and rank_of(clan, actor_id) != "leader" and action != "leave" else rank_of(clan, actor_id)
    if action == "accept":
        return rank in ("leader", "deputy", "rep")
    if action == "kick":
        target = rank_of(clan, target_id)
        if target is None or target_id == actor_id or target == "leader":
            return False
        return rank == "leader" or (rank == "deputy" and target == "member")
    if action in ("team", "transfer"):
        return rank == "leader"
    if action == "leave":
        return rank in ("deputy", "rep", "member")
    return False


def successor(clan):
    """Кто станет главой, если глава ушёл: зам с бо́льшим стажем → представитель → самый давний участник."""
    order = [user_id for user_id in member_ids(clan["scope_id"], clan["tag"]) if user_id != clan["leader_id"]]
    for group in (clan["deputies"], clan["reps"], order):
        candidates = [user_id for user_id in order if user_id in group]
        if candidates:
            return candidates[0]
    return None


# ============================================================
# ЗАЯВКИ, ПРИГЛАШЕНИЯ, ПЕРЕДАЧА ГЛАВЫ
# ============================================================

_REQUEST_COLUMNS = ("id", "scope_id", "tag", "user_id", "kind", "by_user", "text", "status", "channel_id",
                    "message_id", "created_at", "decided_by", "decided_at")


def get_request(request_id):
    row = _q(f"SELECT {', '.join(_REQUEST_COLUMNS)} FROM clan_requests WHERE id = ?", (request_id,), one=True)
    return dict(zip(_REQUEST_COLUMNS, row)) if row else None


def open_request(scope_id, tag, user_id, kind, by_user=None, text=""):
    """-> (заявка, создана ли сейчас). Уже есть открытая — её же (без дубля)."""
    ensure_tables()
    _q("UPDATE clan_requests SET status = 'expired' WHERE status = 'open' AND created_at < ?",
       (int(time.time()) - REQUEST_TTL,), write=True)
    connection = get_connection()
    try:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO clan_requests (scope_id, tag, user_id, kind, by_user, text, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", (scope_id, tag.upper(), user_id, kind, by_user, text[:500],
                                             int(time.time())))
        connection.commit()
        created = cursor.rowcount > 0
    finally:
        connection.close()
    row = _q("SELECT id FROM clan_requests WHERE scope_id = ? AND tag = ? AND user_id = ? AND kind = ? AND "
             "status = 'open'", (scope_id, tag.upper(), user_id, kind), one=True)
    return get_request(row[0]), created


def decide(request_id, status, by_user):
    """Закрыть заявку один раз (второе нажатие — False)."""
    return _q("UPDATE clan_requests SET status = ?, decided_by = ?, decided_at = ? WHERE id = ? AND status = 'open'",
              (status, by_user, int(time.time()), request_id), write=True) > 0


def reopen(request_id):
    _q("UPDATE clan_requests SET status = 'open', decided_by = NULL, decided_at = NULL WHERE id = ?",
       (request_id,), write=True)


def place_request(request_id, channel_id, message_id):
    _q("UPDATE clan_requests SET channel_id = ?, message_id = ? WHERE id = ?", (channel_id, message_id, request_id),
       write=True)


# ============================================================
# DISCORD: роль, канал клана, сообщения
# ============================================================

def clan_guild(client, clan, fallback=None):
    get_guild = getattr(client, "get_guild", None)
    guild = get_guild(clan["guild_id"]) if get_guild is not None and clan.get("guild_id") else None
    return guild or fallback


def clan_channel(guild, clan):
    """Текстовый канал клана (в его категории) или None."""
    category = guild.get_channel(clan.get("category_id") or 0) if guild is not None else None
    for channel in getattr(category, "channels", None) or []:
        if isinstance(channel, discord.TextChannel):
            return channel
    return None


async def fetch_member(guild, user_id):
    if guild is None or not user_id:
        return None
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except (discord.HTTPException, AttributeError):
            return None
    return member


async def set_role(guild, clan, user_id, give):
    """Выдать/снять роль клана. -> True, если всё в порядке (роли нет — тоже не ошибка)."""
    import clans
    role = guild.get_role(clan["role_id"]) if guild is not None and clan.get("role_id") else None
    if role is None and guild is not None:
        role = clans.find_role(guild, clan)
    member = await fetch_member(guild, user_id)
    if role is None or member is None:
        return True
    try:
        if give and role not in member.roles:
            await member.add_roles(role, reason=f"Purr: клан [{clan['tag']}]")
        elif not give and role in member.roles:
            await member.remove_roles(role, reason=f"Purr: клан [{clan['tag']}]")
    except discord.HTTPException as error:
        _log.warning("clan role %s %s: %s", clan["tag"], user_id, error)
        return False
    return True


async def tell_clan(guild, clan, text, mention_ids=()):
    """Сообщение в канал клана; канала нет — главе в ЛС."""
    channel = clan_channel(guild, clan)
    allowed = discord.AllowedMentions(users=[discord.Object(i) for i in mention_ids if i], roles=False,
                                      everyone=False)
    try:
        if channel is not None:
            await channel.send(text[:2000], allowed_mentions=allowed)
            return True
        leader = await fetch_member(guild, clan["leader_id"])
        if leader is not None:
            await leader.send(text[:2000])
            return True
    except discord.HTTPException:
        pass
    return False


async def dm(user, text, view=None, embed=None):
    try:
        return await user.send(content=text, view=view, embed=embed)
    except (discord.HTTPException, AttributeError):
        return None


def mention(user_id):
    return f"<@{user_id}>" if user_id else "—"


# ============================================================
# ДЕЙСТВИЯ (одни и те же для панели, кнопок и событий сервера)
# ============================================================

async def join(guild, clan, user_id):
    """Принять в клан. -> None или ключ ошибки."""
    import clan_life
    if not clan_life.can_accept(clan["scope_id"], clan["tag"]):
        return "clan.error.debt"
    if tag_of(clan["scope_id"], user_id) not in (None, clan["tag"]):
        return "clan.error.other_clan"
    if user_id not in member_ids(clan["scope_id"], clan["tag"]) and \
            len(member_ids(clan["scope_id"], clan["tag"])) >= limit_for(clan["scope_id"], clan["tag"]):
        return "clan.error.full"
    if await fetch_member(guild, user_id) is None:
        return "clan.error.left_server"
    if not add_member(clan["scope_id"], clan["tag"], user_id):
        return "clan.error.full"
    await set_role(guild, clan, user_id, True)
    return None


async def leave(guild, clan, user_id, kicked_by=None):
    """Убрать из клана (вышел сам / исключили / ушёл с сервера): роль, команда, состав."""
    remove_member(clan["scope_id"], user_id)
    deputies = [i for i in clan["deputies"] if i != user_id]
    reps = [i for i in clan["reps"] if i != user_id]
    if (deputies, reps) != (clan["deputies"], clan["reps"]):
        clan = set_team(clan["scope_id"], clan["tag"], clan["leader_id"], deputies, reps)
    if guild is not None:
        await set_role(guild, clan, user_id, False)
    return clan


async def replace_leader(guild, clan):
    """Глава ушёл: новый — по стажу (зам → представитель → участник). -> (клан, новый глава | None)."""
    old = clan["leader_id"]
    remove_member(clan["scope_id"], old)
    new = successor(clan)
    deputies = [i for i in clan["deputies"] if i not in (old, new)]
    reps = [i for i in clan["reps"] if i not in (old, new)]
    clan = set_team(clan["scope_id"], clan["tag"], new, deputies, reps)
    if new is None:
        await tell_clan(guild, clan, t("clan.notice.no_leader", tag=clan["tag"], old=mention(old)))
    else:
        await tell_clan(guild, clan, t("clan.notice.new_leader_auto", tag=clan["tag"], old=mention(old),
                                       new=mention(new)), [new])
    return clan, new


async def transfer(clan, new_leader):
    """Глава → new_leader (из состава). Старый глава становится рядовым."""
    deputies = [i for i in clan["deputies"] if i != new_leader]
    reps = [i for i in clan["reps"] if i != new_leader]
    return set_team(clan["scope_id"], clan["tag"], new_leader, deputies, reps)


# ============================================================
# СОБЫТИЯ СЕРВЕРА: ушёл с сервера, роль выдали/сняли руками
# ============================================================

def _clans_with_role(guild):
    import clans
    return [clan for clan in clans.get_clans(ecosystem.scope(guild.id))
            if clan.get("role_id") and clan.get("guild_id") in (None, guild.id)]


async def on_member_remove(member):
    guild = member.guild
    scope_id = ecosystem.scope(guild.id)
    clan = clan_of(scope_id, member.id)
    if clan is None or clan.get("guild_id") not in (None, guild.id):
        return
    rank = rank_of(clan, member.id)
    if rank == "leader":
        await replace_leader(guild, clan)
        return
    clan = await leave(None, clan, member.id)
    if rank in ("deputy", "rep"):
        await tell_clan(guild, clan, t(f"clan.notice.{rank}_left", tag=clan["tag"], who=mention(member.id),
                                       leader=mention(clan["leader_id"])), [clan["leader_id"]])


async def on_member_update(before, after):
    """Роль клана выдал или снял не Purr (бот сервера, админ) — состав следом."""
    added = {role.id for role in after.roles} - {role.id for role in before.roles}
    removed = {role.id for role in before.roles} - {role.id for role in after.roles}
    if not added and not removed:
        return
    for clan in _clans_with_role(after.guild):
        if clan["role_id"] in added and tag_of(clan["scope_id"], after.id) is None:
            add_member(clan["scope_id"], clan["tag"], after.id)
        elif clan["role_id"] in removed and tag_of(clan["scope_id"], after.id) == clan["tag"]:
            if rank_of(clan, after.id) == "leader":
                await replace_leader(after.guild, clan)
            else:
                await leave(None, clan, after.id)


# ============================================================
# ПОСТОЯННЫЕ КНОПКИ: «Принять / Отклонить»
# ============================================================

class ClanRequestButton(discord.ui.DynamicItem[discord.ui.Button],
                        template=r"rb:clr:(?P<rid>\d+):(?P<act>[yn])"):
    def __init__(self, request_id, act, *, item=None):
        yes = act == "y"
        super().__init__(item or discord.ui.Button(
            label=t("clan.request.yes" if yes else "clan.request.no")[:80], emoji="✅" if yes else "✖️",
            style=discord.ButtonStyle.success if yes else discord.ButtonStyle.secondary,
            custom_id=f"rb:clr:{request_id}:{act}"))
        self.request_id, self.act = request_id, act

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["rid"]), match["act"], item=item)

    async def callback(self, interaction):
        await answer_request(interaction, self.request_id, self.act == "y")


def request_view(request_id):
    view = discord.ui.View(timeout=None)
    view.add_item(ClanRequestButton(request_id, "y"))
    view.add_item(ClanRequestButton(request_id, "n"))
    return view


async def _close_message(interaction, text):
    """Под заявкой — итог, кнопки убираем."""
    try:
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else None
        if embed is not None:
            embed.add_field(name=t("clan.request.result"), value=text[:1024], inline=False)
        await interaction.response.edit_message(embed=embed, view=None) if embed is not None else \
            await interaction.response.edit_message(content=text[:2000], view=None)
    except discord.HTTPException:
        pass


async def answer_request(interaction, request_id, yes):
    import clans
    request = get_request(request_id)
    if request is None or request["status"] != "open":
        await reply(interaction, "clan.request.closed")
        return
    clan = clans.get_clan(request["scope_id"], request["tag"])
    if clan is None or clan["status"] == "dissolved":
        decide(request_id, "expired", interaction.user.id)
        await reply(interaction, "clan.error.no_clan")
        return
    guild = clan_guild(interaction.client, clan, interaction.guild)
    kind, user_id = request["kind"], request["user_id"]
    if kind == "apply":  # решают глава, замы, представители (и админы)
        admin = is_admin(guild, await fetch_member(guild, interaction.user.id))
        if not can(clan, interaction.user.id, "accept", admin=admin):
            await reply(interaction, "clan.error.not_team")
            return
    elif kind == "dissolve":  # роспуск, начатый главой, подтверждает зам
        if rank_of(clan, interaction.user.id) != "deputy":
            await reply(interaction, "clan.dissolve.only_deputy")
            return
    elif interaction.user.id != user_id:  # приглашение и передачу главы решает тот, кого зовут
        await reply(interaction, "clan.error.not_you")
        return
    if not decide(request_id, "accepted" if yes else "declined", interaction.user.id):
        await reply(interaction, "clan.request.closed")
        return
    if kind == "dissolve":
        if not yes:
            await _close_message(interaction, t("clan.request.declined.dissolve", who=mention(interaction.user.id)))
            return
        import clan_life
        await _close_message(interaction, t("clan.request.accepted.dissolve", who=mention(interaction.user.id)))
        await clan_life.dissolve(guild, clan, "leader", request["by_user"])
        return
    if not yes:
        await _close_message(interaction, t(f"clan.request.declined.{kind}", who=mention(interaction.user.id)))
        if kind == "apply":
            user = await fetch_member(guild, user_id)
            await dm(user, t("clan.dm.declined", tag=clan["tag"], name=clan["name"] or "—"))
        elif kind == "transfer":
            await tell_clan(guild, clan, t("clan.notice.transfer_declined", tag=clan["tag"], who=mention(user_id)))
        return
    if kind == "transfer":
        if str(clan["leader_id"]) != request["text"] or rank_of(clan, user_id) is None:
            await _close_message(interaction, t("clan.request.outdated"))
            return
        clan = await transfer(clan, user_id)
        await _close_message(interaction, t("clan.request.accepted.transfer", who=mention(user_id)))
        old = int(request["text"])
        await tell_clan(guild, clan, t("clan.notice.new_leader", tag=clan["tag"], old=mention(old),
                                       new=mention(user_id)), [user_id, old])
        return
    problem = await join(guild, clan, user_id)
    if problem:
        if problem == "clan.error.full":  # место освободится — заявку можно будет принять
            reopen(request_id)
        await reply(interaction, problem)
        return
    await _close_message(interaction, t(f"clan.request.accepted.{kind}", who=mention(interaction.user.id)))
    if kind == "apply":
        await dm(await fetch_member(guild, user_id), t("clan.dm.accepted", tag=clan["tag"], name=clan["name"] or "—"))
    await tell_clan(guild, clan, t("clan.notice.joined", tag=clan["tag"], who=mention(user_id)), [user_id])


async def send_application(interaction, clan, text):
    """Заявка в клан: в канал клана (или главе в ЛС) с кнопками «Принять / Отклонить»."""
    guild = clan_guild(interaction.client, clan, interaction.guild)
    request, created = open_request(clan["scope_id"], clan["tag"], interaction.user.id, "apply",
                                    interaction.user.id, text)
    if not created:
        return "clan.apply.already"
    embed = discord.Embed(title=t("clan.apply.card_title", tag=clan["tag"], name=clan["name"] or "—")[:256],
                          description=t("clan.apply.card_text", who=interaction.user.mention,
                                        text=text or t("clan.apply.no_text"))[:4096], color=core.embed_color())
    channel = clan_channel(guild, clan)
    message = None
    try:
        if channel is not None:
            team = [clan["leader_id"], *clan["deputies"], *clan["reps"]]
            message = await channel.send(" ".join(mention(i) for i in team if i)[:2000], embed=embed,
                                         view=request_view(request["id"]),
                                         allowed_mentions=discord.AllowedMentions(users=True, roles=False,
                                                                                  everyone=False))
    except discord.HTTPException:
        message = None
    if message is None:
        message = await dm(await fetch_member(guild, clan["leader_id"]), None, request_view(request["id"]), embed)
    if message is None:
        decide(request["id"], "expired", interaction.user.id)
        return "clan.apply.nowhere"
    place_request(request["id"], message.channel.id, message.id)
    return None


async def send_offer(interaction, clan, user, kind):
    """Приглашение (kind=invite) или передача главы (transfer): тому, кого зовут, в ЛС, иначе — сюда в канал."""
    # у передачи главы запоминаем, кто глава сейчас: сменился до ответа — передача уже не действует
    request, created = open_request(clan["scope_id"], clan["tag"], user.id, kind, interaction.user.id,
                                    str(clan["leader_id"]) if kind == "transfer" else "")
    if not created:
        return "clan.offer.already"
    text = t(f"clan.offer.{kind}", who=user.mention, by=interaction.user.mention, tag=clan["tag"],
             name=clan["name"] or "—")
    message = await dm(user, text, request_view(request["id"]))
    if message is None and interaction.channel is not None:
        try:
            message = await interaction.channel.send(
                text[:2000], view=request_view(request["id"]),
                allowed_mentions=discord.AllowedMentions(users=[user], roles=False, everyone=False))
        except discord.HTTPException:
            message = None
    if message is None:
        decide(request["id"], "expired", interaction.user.id)
        return "clan.offer.nowhere"
    place_request(request["id"], message.channel.id, message.id)
    return None


# ============================================================
# ПАНЕЛЬ /clan
# ============================================================

def roster_text(guild, clan):
    """Состав по-русски: команда сверху, дальше участники по стажу."""
    lines = [t("clan.roster.leader", who=mention(clan["leader_id"]))]
    lines += [t("clan.roster.deputy", who=mention(i)) for i in clan["deputies"]]
    lines += [t("clan.roster.rep", who=mention(i)) for i in clan["reps"]]
    team = {clan["leader_id"], *clan["deputies"], *clan["reps"]}
    rest = [(user_id, joined) for user_id, joined in members(clan["scope_id"], clan["tag"]) if user_id not in team]
    lines += [t("clan.roster.member", who=mention(user_id), since=f"<t:{joined}:d>") for user_id, joined in rest]
    free = [t("clan.roster.free_deputy")] * (MAX_DEPUTIES - len(clan["deputies"])) + \
        [t("clan.roster.free_rep")] * (MAX_REPS - len(clan["reps"]))
    return "\n".join(lines + free)[:3500]


def home_embed(interaction, clan, rank):
    if clan is None:
        import clans
        empty = not clans.get_clans(ecosystem.scope(interaction.guild.id))
        return panel_embed(interaction, "clan.join", t("clan.join.empty") if empty else None, hints=False)
    count = len(member_ids(clan["scope_id"], clan["tag"]))
    import clan_life
    return panel_embed(interaction, "clan.home", hints=False, tag=clan["tag"], name=clan["name"] or "—",
                       life=clan_life.status_line(clan["scope_id"], clan["tag"]),
                       rank=t(f"clan.rank.{rank}"), count=count, max=limit_for(clan["scope_id"], clan["tag"]),
                       roster=roster_text(interaction.guild, clan))


async def open_home(interaction, edit=False):
    import clans
    scope_id = ecosystem.scope(interaction.guild.id)
    clan = clan_of(scope_id, interaction.user.id)
    if clan is not None:
        seed(clan_guild(interaction.client, clan, interaction.guild), clan)
    admin = is_admin(interaction.guild, interaction.user)
    rank = rank_of(clan, interaction.user.id)
    view = MyClanView(clan, rank, admin) if clan else JoinView(clans.get_clans(scope_id), admin)
    embed = home_embed(interaction, clan, rank)
    if edit:
        await interaction.response.edit_message(embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


def clan_card(interaction, clan):
    count = len(member_ids(clan["scope_id"], clan["tag"]))
    return panel_embed(interaction, "clan.card", hints=False, tag=clan["tag"], name=clan["name"] or "—",
                       leader=mention(clan["leader_id"]), count=count, max=limit_for(clan["scope_id"], clan["tag"]),
                       deputies=", ".join(mention(i) for i in clan["deputies"]) or "—",
                       reps=", ".join(mention(i) for i in clan["reps"]) or "—")


class JoinView(PanelView):
    """Не в клане: выбрать клан → его карточка → «Подать заявку»."""

    def __init__(self, clan_list, admin=False):
        super().__init__(timeout=600)
        if admin:  # админ без клана — сразу к управлению кланами
            button = discord.ui.Button(label=t("clan.home.admin_panel")[:80], emoji="⚙️",
                                       style=discord.ButtonStyle.secondary)

            async def manage(i):
                import clan_hub
                await i.response.edit_message(embed=clan_hub.admin_embed(i),
                                              view=clan_hub.AdminView(i, back_target=(core.screen(i), self)))
            button.callback = manage
            self.add_item(button)
        if not clan_list:
            return
        select = core.PagedSelect(placeholder=t("clan.join.pick")[:150], options=[
            discord.SelectOption(label=f"[{c['tag']}] {c['name'] or ''}"[:100], value=c["tag"]) for c in clan_list])

        async def picked(i):
            import clans
            clan = clans.get_clan(ecosystem.scope(i.guild.id), select.values[0])
            if clan is None:
                await reply(i, "clan.error.no_clan")
                return
            seed(clan_guild(i.client, clan, i.guild), clan)
            await i.response.edit_message(embed=clan_card(i, clan),
                                          view=ApplyView(clan["tag"], back_target=(core.screen(i), self)))
        select.on_pick = picked
        self.add_item(select)


class ApplyView(PanelView):
    texts = "clan.card"

    def __init__(self, tag, back_target=None):
        super().__init__(back_target=back_target, timeout=600)
        self.tag = tag

    @discord.ui.button(label="Подать заявку", emoji="📨", style=discord.ButtonStyle.success)
    async def apply(self, interaction, button):
        scope_id = ecosystem.scope(interaction.guild.id)
        if clan_of(scope_id, interaction.user.id) is not None:
            await reply(interaction, "clan.error.other_clan")
            return
        await interaction.response.send_modal(ApplyModal(self.tag))


class ApplyModal(Modal, title="ЗАЯВКА В КЛАН"):
    texts = "clan.apply_modal"
    about = discord.ui.TextInput(label="Пара слов о себе", style=discord.TextStyle.paragraph, max_length=500,
                                 required=False)

    def __init__(self, tag):
        super().__init__()
        self.tag = tag

    async def on_submit(self, interaction):
        import clans
        clan = clans.get_clan(ecosystem.scope(interaction.guild.id), self.tag)
        if clan is None:
            await reply(interaction, "clan.error.no_clan")
            return
        if len(member_ids(clan["scope_id"], clan["tag"])) >= limit_for(clan["scope_id"], clan["tag"]):
            await reply(interaction, "clan.error.full")
            return
        problem = await send_application(interaction, clan, self.about.value.strip())
        await reply(interaction, problem or "clan.apply.sent", tag=clan["tag"])


class MyClanView(PanelView):
    """Моя панель в клане: кнопки — только те, что можно по роли в клане."""

    texts = "clan.home"
    button_order = ("card", "quests", "shop", "invite", "kick", "team", "transfer", "dissolve", "leave", "admin_panel")

    def __init__(self, clan, rank, admin=False):
        super().__init__(timeout=900)
        self.tag, self.scope_id = clan["tag"], clan["scope_id"]
        allowed = {
            "card": True,
            "quests": True,
            "shop": rank in ("leader", "deputy") or admin,
            "invite": rank in ("leader", "deputy", "rep") or admin,
            "kick": rank in ("leader", "deputy") or admin,
            "team": rank == "leader" or admin,
            "transfer": rank == "leader" or admin,
            "dissolve": rank == "leader",
            "leave": rank in ("deputy", "rep", "member"),
            "admin_panel": admin,
        }
        for name, ok in allowed.items():
            if not ok:
                self.remove_item(getattr(self, name))

    def clan(self):
        import clans
        return clans.get_clan(self.scope_id, self.tag)

    def admin(self, interaction):
        return is_admin(interaction.guild, interaction.user)

    @discord.ui.button(label="Карточка клана", emoji="🛡️", style=discord.ButtonStyle.primary)
    async def card(self, interaction, button):
        import clan_hub
        await clan_hub.show_card(interaction, self.clan())

    @discord.ui.button(label="Квесты", emoji="🎯", style=discord.ButtonStyle.primary)
    async def quests(self, interaction, button):
        import clan_life
        import clan_quests
        clan = self.clan()
        term = clan_life.state_of(self.scope_id, self.tag)["term_no"]
        can_submit = rank_of(clan, interaction.user.id) in ("leader", "deputy", "rep") or self.admin(interaction)
        await interaction.response.edit_message(
            embed=clan_quests.quests_embed(interaction, self.scope_id, self.tag, term),
            view=clan_quests.QuestsView(self.scope_id, self.tag, term, can_submit,
                                        back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Магазин", emoji="🛒", style=discord.ButtonStyle.secondary)
    async def shop(self, interaction, button):
        import clan_shop
        clan = self.clan()
        await interaction.response.edit_message(embed=clan_shop.shop_embed(interaction, clan),
                                                view=clan_shop.ShopView(clan, back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Распустить клан", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def dissolve(self, interaction, button):
        clan = self.clan()
        if not clan["deputies"]:
            await reply(interaction, "clan.dissolve.no_deputy")
            return
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "clan.dissolve", hints=False, tag=self.tag),
            view=YesNoView(self.scope_id, self.tag, "dissolve", interaction.user.id,
                           back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Управление кланами", emoji="⚙️", style=discord.ButtonStyle.secondary)
    async def admin_panel(self, interaction, button):
        import clan_hub
        await interaction.response.edit_message(embed=clan_hub.admin_embed(interaction),
                                                view=clan_hub.AdminView(interaction,
                                                                        back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Пригласить", emoji="➕", style=discord.ButtonStyle.success)
    async def invite(self, interaction, button):
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "clan.invite", hints=False, tag=self.tag),
            view=InviteView(self.scope_id, self.tag, back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Исключить", emoji="🚪", style=discord.ButtonStyle.danger)
    async def kick(self, interaction, button):
        clan = self.clan()
        admin = self.admin(interaction)
        targets = [user_id for user_id in member_ids(self.scope_id, self.tag)
                   if can(clan, interaction.user.id, "kick", user_id, admin=admin)]
        if not targets:
            await reply(interaction, "clan.kick.nobody")
            return
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "clan.kick", hints=False, tag=self.tag),
            view=PickMemberView(interaction.guild, self.scope_id, self.tag, targets, "kick",
                                back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Команда", emoji="👑", style=discord.ButtonStyle.primary)
    async def team(self, interaction, button):
        await interaction.response.edit_message(embed=team_embed(interaction, self.clan()),
                                                view=TeamView(self.scope_id, self.tag,
                                                              back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Передать главу", emoji="🔁", style=discord.ButtonStyle.secondary)
    async def transfer(self, interaction, button):
        clan = self.clan()
        targets = [user_id for user_id in member_ids(self.scope_id, self.tag) if user_id != clan["leader_id"]]
        if not targets:
            await reply(interaction, "clan.transfer.nobody")
            return
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "clan.transfer", hints=False, tag=self.tag),
            view=PickMemberView(interaction.guild, self.scope_id, self.tag, targets, "transfer",
                                back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Выйти из клана", emoji="👋", style=discord.ButtonStyle.secondary)
    async def leave(self, interaction, button):
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "clan.leave", hints=False, tag=self.tag),
            view=YesNoView(self.scope_id, self.tag, "leave", interaction.user.id,
                           back_target=(core.screen(interaction), self)))


def _member_label(guild, user_id):
    member = guild.get_member(user_id) if guild is not None else None
    return getattr(member, "display_name", None) or getattr(member, "name", None) or str(user_id)


class PickMemberView(PanelView):
    """Выбрать человека из состава (кого исключить / кому передать главу / кого назначить)."""

    def __init__(self, guild, scope_id, tag, targets, action, back_target=None, role=None):
        super().__init__(back_target=back_target, timeout=600)
        self.scope_id, self.tag, self.action, self.role = scope_id, tag, action, role
        select = core.PagedSelect(placeholder=t(f"clan.{action}.pick")[:150], options=[
            discord.SelectOption(label=_member_label(guild, user_id)[:100], value=str(user_id))
            for user_id in targets])

        async def picked(i):
            target = int(select.values[0])
            await i.response.edit_message(
                embed=panel_embed(i, f"clan.{action}.ask", hints=False, who=mention(target), tag=self.tag,
                                  role=t(f"clan.rank.{role}") if role else ""),
                view=YesNoView(self.scope_id, self.tag, action, target, role=role, back_target=self.back_target))
        select.on_pick = picked
        self.add_item(select)


class YesNoView(PanelView):
    """Один вопрос — «Да / Нет». Что делаем: leave, kick, transfer, appoint, dismiss."""

    def __init__(self, scope_id, tag, action, target, role=None, back_target=None):
        super().__init__(timeout=600)
        self.scope_id, self.tag, self.action, self.target, self.role = scope_id, tag, action, target, role
        yes = discord.ui.Button(label=t(f"clan.{action}.yes")[:80], emoji="✅",
                                style=discord.ButtonStyle.danger if action in ("leave", "kick", "dismiss", "dissolve")
                                else discord.ButtonStyle.success)
        no = discord.ui.Button(label=t("clan.no")[:80], emoji="↩️", style=discord.ButtonStyle.secondary)
        yes.callback, no.callback = self.do, self.cancel
        self.add_item(yes)
        self.add_item(no)

    async def cancel(self, interaction):
        await open_home(interaction, edit=True)

    async def do(self, interaction):
        import clans
        clan = clans.get_clan(self.scope_id, self.tag)
        if clan is None:
            await reply(interaction, "clan.error.no_clan")
            return
        guild = clan_guild(interaction.client, clan, interaction.guild)
        admin = is_admin(interaction.guild, interaction.user)
        me, target = interaction.user.id, self.target
        if self.action == "leave":
            if not can(clan, me, "leave"):
                await reply(interaction, "clan.leave.leader")
                return
            await leave(guild, clan, me)
            core.audit(interaction, "clan.left", "clan", clan["tag"], None)
            await tell_clan(guild, clan, t("clan.notice.left", tag=clan["tag"], who=mention(me)))
            await interaction.response.edit_message(embed=panel_embed(interaction, "clan.left", hints=False,
                                                                      tag=clan["tag"]), view=None)
            return
        if self.action == "dissolve":
            if clan["leader_id"] != me:
                await reply(interaction, "clan.error.no_rights")
                return
            request, created = open_request(clan["scope_id"], clan["tag"], me, "dissolve", me)
            if not created:
                await reply(interaction, "clan.offer.already")
                return
            text = t("clan.dissolve.request", tag=clan["tag"], by=mention(me),
                     deputies=" ".join(mention(i) for i in clan["deputies"]))
            channel = clan_channel(guild, clan)
            message = None
            try:
                if channel is not None:
                    message = await channel.send(text[:2000], view=request_view(request["id"]),
                                                 allowed_mentions=discord.AllowedMentions(users=True, roles=False))
            except discord.HTTPException:
                message = None
            for deputy in clan["deputies"]:  # и в ЛС каждому заму
                sent = await dm(await fetch_member(guild, deputy), text, request_view(request["id"]))
                message = message or sent
            if message is None:
                decide(request["id"], "expired", me)
                await reply(interaction, "clan.offer.nowhere")
                return
            place_request(request["id"], message.channel.id, message.id)
            core.audit(interaction, "clan.dissolve_requested", "clan", clan["tag"], None)
            await interaction.response.edit_message(embed=panel_embed(interaction, "clan.dissolve_sent", hints=False,
                                                                      tag=clan["tag"]), view=None)
            return
        if self.action == "kick":
            if not can(clan, me, "kick", target, admin=admin):
                await reply(interaction, "clan.error.no_rights")
                return
            await leave(guild, clan, target, kicked_by=me)
            core.audit(interaction, "clan.kicked", "clan", clan["tag"], str(target))
            await tell_clan(guild, clan, t("clan.notice.kicked", tag=clan["tag"], who=mention(target),
                                           by=mention(me)))
            await dm(await fetch_member(guild, target), t("clan.dm.kicked", tag=clan["tag"], name=clan["name"] or "—"))
            await open_home(interaction, edit=True)
            return
        if not can(clan, me, "team" if self.action in ("appoint", "dismiss") else "transfer", admin=admin):
            await reply(interaction, "clan.error.no_rights")
            return
        if self.action == "transfer":
            user = await fetch_member(guild, target)
            if user is None or rank_of(clan, target) is None:
                await reply(interaction, "clan.error.left_server")
                return
            problem = await send_offer(interaction, clan, user, "transfer")
            core.audit(interaction, "clan.transfer_offered", "clan", clan["tag"], str(target))
            await interaction.response.edit_message(
                embed=panel_embed(interaction, "clan.transfer.sent" if not problem else "clan.transfer", hints=False,
                                  who=mention(target), tag=clan["tag"]), view=None)
            if problem:
                await interaction.followup.send(t(problem), ephemeral=True)
            return
        deputies, reps = list(clan["deputies"]), list(clan["reps"])
        if self.action == "appoint":
            if rank_of(clan, target) != "member":
                await reply(interaction, "clan.team.not_member")
                return
            group, limit = (deputies, MAX_DEPUTIES) if self.role == "deputy" else (reps, MAX_REPS)
            if len(group) >= limit:
                await reply(interaction, "clan.team.full")
                return
            group.append(target)
            notice = "clan.notice.appointed"
        else:
            deputies = [i for i in deputies if i != target]
            reps = [i for i in reps if i != target]
            notice = "clan.notice.dismissed"
        clan = set_team(self.scope_id, self.tag, clan["leader_id"], deputies, reps)
        core.audit(interaction, f"clan.{self.action}", "clan", clan["tag"], f"{target}:{self.role or ''}")
        await tell_clan(guild, clan, t(notice, tag=clan["tag"], who=mention(target),
                                       role=t(f"clan.rank.{self.role}") if self.role else ""), [target])
        await interaction.response.edit_message(embed=team_embed(interaction, clan),
                                                view=TeamView(self.scope_id, self.tag))


class InviteView(PanelView):
    def __init__(self, scope_id, tag, back_target=None):
        super().__init__(back_target=back_target, timeout=600)
        self.scope_id, self.tag = scope_id, tag
        select = discord.ui.UserSelect(placeholder=t("clan.invite.pick")[:150])

        async def picked(i):
            import clans
            user = select.values[0]
            clan = clans.get_clan(self.scope_id, self.tag)
            if clan is None or not (can(clan, i.user.id, "accept") or is_admin(i.guild, i.user)):
                await reply(i, "clan.error.no_rights")
                return
            if getattr(user, "bot", False):
                await reply(i, "clan.invite.bot")
                return
            if tag_of(self.scope_id, user.id) is not None:
                await reply(i, "clan.invite.in_clan", who=user.mention)
                return
            if len(member_ids(self.scope_id, self.tag)) >= limit_for(self.scope_id, self.tag):
                await reply(i, "clan.error.full")
                return
            problem = await send_offer(i, clan, user, "invite")
            core.audit(i, "clan.invited", "clan", clan["tag"], str(user.id))
            await reply(i, problem or "clan.invite.sent", who=user.mention)
        select.callback = picked
        self.add_item(select)


def team_embed(interaction, clan):
    return panel_embed(interaction, "clan.team", hints=False, tag=clan["tag"],
                       leader=mention(clan["leader_id"]),
                       deputies=", ".join(mention(i) for i in clan["deputies"]) or "—",
                       reps=", ".join(mention(i) for i in clan["reps"]) or "—",
                       free_deputies=MAX_DEPUTIES - len(clan["deputies"]), free_reps=MAX_REPS - len(clan["reps"]))


class TeamView(PanelView):
    """Команда: назначить зама/представителя (если место свободно) или снять."""

    texts = "clan.team"

    def __init__(self, scope_id, tag, back_target=None):
        super().__init__(back_target=back_target, timeout=600)
        self.scope_id, self.tag = scope_id, tag
        back = self._nav(discord.ui.Button(label=t("clan.to_home")[:80], emoji="◀️",
                                           style=discord.ButtonStyle.secondary, row=4))

        async def go(i):
            await open_home(i, edit=True)
        back.callback = go
        self.add_item(back)

    def clan(self):
        import clans
        return clans.get_clan(self.scope_id, self.tag)

    async def _appoint(self, interaction, role):
        clan = self.clan()
        group, limit = (clan["deputies"], MAX_DEPUTIES) if role == "deputy" else (clan["reps"], MAX_REPS)
        if len(group) >= limit:
            await reply(interaction, "clan.team.full")
            return
        targets = [i for i in member_ids(self.scope_id, self.tag) if rank_of(clan, i) == "member"]
        if not targets:
            await reply(interaction, "clan.team.nobody")
            return
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "clan.appoint", hints=False, role=t(f"clan.rank.{role}"), tag=self.tag),
            view=PickMemberView(interaction.guild, self.scope_id, self.tag, targets, "appoint",
                                back_target=(core.screen(interaction), self), role=role))

    @discord.ui.button(label="Назначить зама", emoji="🛡️", style=discord.ButtonStyle.success)
    async def add_deputy(self, interaction, button):
        await self._appoint(interaction, "deputy")

    @discord.ui.button(label="Назначить представителя", emoji="📣", style=discord.ButtonStyle.success)
    async def add_rep(self, interaction, button):
        await self._appoint(interaction, "rep")

    @discord.ui.button(label="Снять с должности", emoji="➖", style=discord.ButtonStyle.secondary)
    async def dismiss(self, interaction, button):
        clan = self.clan()
        targets = clan["deputies"] + clan["reps"]
        if not targets:
            await reply(interaction, "clan.team.empty")
            return
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "clan.dismiss", hints=False, tag=self.tag),
            view=PickMemberView(interaction.guild, self.scope_id, self.tag, targets, "dismiss",
                                back_target=(core.screen(interaction), self)))


DYNAMIC_ITEMS = [ClanRequestButton]


def register(bot):
    ensure_tables()
    bot.add_listener(on_member_remove, "on_member_remove")
    bot.add_listener(on_member_update, "on_member_update")

    @bot.tree.command(name="clan", description="Мой клан: вступить, состав, пригласить, выйти")
    @discord.app_commands.guild_only()
    async def clan_command(interaction):
        await open_home(interaction)
