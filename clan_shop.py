"""
clan_shop.py
============
Магазин клана (CLANS_TODO п. 7): покупки из казны — глава и замы, пока клан
не в долгу.

Покупка = одно условное списание «казна − цена, только если хватает»
(clan_treasury.spend): два одновременных покупателя не уведут казну в минус.
Почти всё бот выдаёт сам; картинки, звуки и названия — после «Одобрить» админа
в канале проверки (то же, что у квестов). Отклонено — деньги возвращаются в
казну. Висит 24 часа — напоминание (с ролью дежурных, если задана), 48 часов —
напоминание создателю бота. Покупатель видит статус в /clan → «Магазин».

Цены — CLANS_TODO (ориентир: продление 5000); правит создатель бота в ITEMS.
"""

import io
import json
import logging
import re
import time

import discord

import core
from core import t, panel_embed, PanelView, Modal
from actions import reply
from database import get_connection

_log = logging.getLogger(__name__)

# ключ -> (цена, нужна ли проверка админа)
ITEMS = {
    "name": (3000, True),
    "tag": (1500, True),
    "color": (2000, False),
    "emblem": (2500, True),
    "text_channel": (4000, False),
    "voice_channel": (4000, False),
    "slots": (2500, False),
    "emoji": (2000, True),
    "sticker": (2000, True),
    "sound": (3500, True),
    "quest_swap": (500, False),
}
MAX_EXTRA_SLOTS = 10
REMIND = (24 * 3600, 48 * 3600)
_TAG = re.compile(r"^[A-ZА-ЯЁ0-9]{2,5}$")
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def ensure_tables():
    connection = get_connection()
    connection.execute("""CREATE TABLE IF NOT EXISTS clan_purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT, scope_id INTEGER NOT NULL, tag TEXT NOT NULL, item TEXT NOT NULL,
        price INTEGER NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}', buyer INTEGER,
        created_at INTEGER NOT NULL, channel_id INTEGER, message_id INTEGER, reminded INTEGER NOT NULL DEFAULT 0,
        reason TEXT NOT NULL DEFAULT '', decided_by INTEGER)""")
    connection.commit()
    connection.close()


_COLUMNS = ("id", "scope_id", "tag", "item", "price", "status", "payload", "buyer", "created_at", "channel_id",
            "message_id", "reminded", "reason", "decided_by")


def get_purchase(purchase_id):
    ensure_tables()
    connection = get_connection()
    row = connection.execute(f"SELECT {', '.join(_COLUMNS)} FROM clan_purchases WHERE id = ?",
                             (purchase_id,)).fetchone()
    connection.close()
    if row is None:
        return None
    found = dict(zip(_COLUMNS, row))
    found["payload"] = json.loads(found["payload"] or "{}")
    return found


def purchases(scope_id, tag, limit=8):
    ensure_tables()
    connection = get_connection()
    rows = connection.execute(f"SELECT {', '.join(_COLUMNS)} FROM clan_purchases WHERE scope_id = ? AND tag = ? "
                              "ORDER BY id DESC LIMIT ?", (scope_id, tag.upper(), limit)).fetchall()
    connection.close()
    return [dict(zip(_COLUMNS, row)) for row in rows]


def _set(purchase_id, **changes):
    connection = get_connection()
    if "payload" in changes:
        changes["payload"] = json.dumps(changes["payload"], ensure_ascii=False)
    connection.execute(f"UPDATE clan_purchases SET {', '.join(f'{k} = ?' for k in changes)} WHERE id = ?",
                       (*changes.values(), purchase_id))
    connection.commit()
    connection.close()


def set_status(purchase_id, old, new, **changes):
    """Сменить статус один раз (второе нажатие «Одобрить» — ничего). -> True, если сменили."""
    connection = get_connection()
    sets = ", ".join(["status = ?"] + [f"{k} = ?" for k in changes])
    cursor = connection.execute(f"UPDATE clan_purchases SET {sets} WHERE id = ? AND status = ?",
                                (new, *changes.values(), purchase_id, old))
    connection.commit()
    connection.close()
    return cursor.rowcount > 0


def extra_slots(scope_id, tag):
    import clan_life
    return int(clan_life.setting(scope_id, f"slots:{tag.upper()}", 0) or 0)


# ============================================================
# ПОКУПКА
# ============================================================

def check_payload(scope_id, clan, item, payload):
    """-> None или ключ ошибки (проверки — как при создании клана)."""
    import clans
    if item == "name":
        name = payload.get("name", "").strip()
        if not 3 <= len(name) <= 24:
            return "clan_shop.error.name_length"
        if any(c["name"].lower() == name.lower() and c["tag"] != clan["tag"] for c in clans.get_clans(scope_id)):
            return "clan_shop.error.name_taken"
    if item == "tag":
        tag = payload.get("tag", "").strip().upper()
        if not _TAG.match(tag):
            return "clan_shop.error.tag_format"
        if clans.get_clan(scope_id, tag) is not None and clans.get_clan(scope_id, tag)["status"] != "dissolved":
            return "clan_shop.error.tag_taken"
    if item == "color" and not _HEX.match(payload.get("color", "").strip()):
        return "clan_shop.error.color"
    if item in ("text_channel", "voice_channel") and not 2 <= len(payload.get("channel", "").strip()) <= 40:
        return "clan_shop.error.channel_name"
    if item == "slots" and extra_slots(scope_id, clan["tag"]) + 5 > MAX_EXTRA_SLOTS:
        return "clan_shop.error.slots_max"
    if item in ("emblem", "emoji", "sticker", "sound") and not payload.get("file"):
        return "clan_shop.error.file"
    return None


async def buy(interaction, clan, item, payload):
    """Списать цену и выдать (или отправить на проверку). -> ключ результата."""
    import clan_life
    import clan_treasury
    scope_id = clan["scope_id"]
    if not clan_life.shop_open(scope_id, clan["tag"]):
        return "clan_shop.error.debt"
    problem = check_payload(scope_id, clan, item, payload)
    if problem:
        return problem
    price, needs_review = ITEMS[item]
    ensure_tables()
    connection = get_connection()
    cursor = connection.execute("INSERT INTO clan_purchases (scope_id, tag, item, price, status, payload, buyer, "
                                "created_at) VALUES (?, ?, ?, ?, 'paying', ?, ?, ?)",
                                (scope_id, clan["tag"], item, price, json.dumps(payload, ensure_ascii=False),
                                 interaction.user.id, int(time.time())))
    purchase_id = cursor.lastrowid
    connection.commit()
    connection.close()
    paid, left = clan_treasury.spend(scope_id, clan["tag"], price, f"магазин: {t(f'clan_shop.item.{item}')}",
                                     source_key=f"purchase:{purchase_id}", by_user=interaction.user.id)
    if paid is False:
        _set(purchase_id, status="failed", reason="не хватило в казне")
        return "clan_shop.error.money"
    core.audit(interaction, "clan.purchase", "clan", clan["tag"], f"{item} #{purchase_id}")
    if needs_review:
        _set(purchase_id, status="review")
        await send_review(interaction.client, get_purchase(purchase_id))
        return "clan_shop.sent_review"
    _set(purchase_id, status="delivering")
    error = await deliver(interaction.client, get_purchase(purchase_id), interaction.guild)
    if error:
        await refund(get_purchase(purchase_id), error)
        return error
    set_status(purchase_id, "delivering", "delivered")
    return "clan_shop.delivered"


async def refund(purchase, reason):
    import clan_treasury
    clan_treasury.record(purchase["scope_id"], purchase["tag"], "", purchase["price"], 0,
                         f"возврат за покупку #{purchase['id']}", source_key=f"refund:{purchase['id']}")
    _set(purchase["id"], status="refunded", reason=str(t(reason) if reason.startswith("clan_shop.") else reason)[:200])


# ============================================================
# ВЫДАЧА
# ============================================================

async def deliver(bot, purchase, guild=None):
    """Выдать купленное. -> None или ключ/текст ошибки (тогда деньги вернутся)."""
    import clan_members
    import clans
    clan = clans.get_clan(purchase["scope_id"], purchase["tag"])
    if clan is None:
        return "clan_shop.error.no_clan"
    guild = clan_members.clan_guild(bot, clan, guild)
    payload = purchase["payload"]
    item = purchase["item"]
    role = guild.get_role(clan.get("role_id") or 0) if guild else None
    category = guild.get_channel(clan.get("category_id") or 0) if guild else None
    try:
        if item == "name":
            new = payload["name"].strip()
            old = clan["name"]
            _update_clan(clan, name=new)
            if role is not None and old and old in role.name:
                await role.edit(name=role.name.replace(old, new)[:100], reason="Purr: магазин клана")
            if category is not None:
                await category.edit(name=f"{clan['tag']} · {new}"[:100], reason="Purr: магазин клана")
        elif item == "tag":
            new = payload["tag"].strip().upper()
            old = clan["tag"]
            clans.rename_tag(clan["scope_id"], old, new)
            if role is not None and old in role.name:
                await role.edit(name=role.name.replace(old, new)[:100], reason="Purr: магазин клана")
            if category is not None:
                await category.edit(name=f"{new} · {clan['name']}"[:100], reason="Purr: магазин клана")
        elif item == "color":
            if role is None:
                return "clan_shop.error.no_role"
            await role.edit(colour=discord.Colour(int(_HEX.match(payload["color"].strip())[1], 16)),
                            reason="Purr: магазин клана")
        elif item in ("text_channel", "voice_channel"):
            if category is None:
                return "clan_shop.error.no_category"
            make = category.create_text_channel if item == "text_channel" else category.create_voice_channel
            await make(payload["channel"].strip()[:40], reason="Purr: магазин клана")
        elif item == "slots":
            import clan_life
            clan_life.set_setting(clan["scope_id"], f"slots:{clan['tag']}", extra_slots(clan["scope_id"], clan["tag"]) + 5)
        elif item == "quest_swap":
            import clan_life
            import clan_quests
            term = clan_life.state_of(clan["scope_id"], clan["tag"])["term_no"]
            if clan_quests.swap(clan["scope_id"], clan["tag"], term, payload.get("level", "easy")) is None:
                return "clan_shop.error.swap"
        elif item in ("emblem", "emoji", "sticker", "sound"):
            data = await _file_bytes(payload["file"])
            if data is None:
                return "clan_shop.error.file_gone"
            if item == "emblem":
                import clan_life
                clan_life.set_setting(clan["scope_id"], f"emblem:{clan['tag']}", payload["file"])
                if role is not None and "ROLE_ICONS" in guild.features:
                    await role.edit(display_icon=data, reason="Purr: герб клана")
            elif item == "emoji":
                await guild.create_custom_emoji(name=(payload.get("name") or clan["tag"]).lower()[:32], image=data,
                                                reason=f"Purr: эмодзи клана [{clan['tag']}]")
            elif item == "sticker":
                await guild.create_sticker(name=(payload.get("name") or clan["name"])[:30], description=clan["name"][:100],
                                           emoji="⭐", file=discord.File(io.BytesIO(data), filename="sticker.png"),
                                           reason=f"Purr: стикер клана [{clan['tag']}]")
            else:
                await guild.create_soundboard_sound(name=(payload.get("name") or clan["tag"])[:32], sound=data,
                                                    reason=f"Purr: звук клана [{clan['tag']}]")
    except discord.Forbidden:
        return "clan_shop.error.forbidden"
    except discord.HTTPException as error:
        _log.warning("clan shop deliver %s: %s", purchase["id"], error)
        return f"Discord не дал выдать: {error.text or error}"[:200]
    except (AttributeError, TypeError) as error:  # нет такой возможности у сервера / версии
        return f"Не получилось выдать: {error}"[:200]
    return None


async def _file_bytes(url):
    import web_panel
    name = str(url).rsplit("/", 1)[-1]
    path = web_panel.ASSETS_DIR / name
    if path.is_file():
        return path.read_bytes()
    return await core.fetch_bytes(url)


def _update_clan(clan, **changes):
    connection = get_connection()
    connection.execute(f"UPDATE clans SET {', '.join(f'{k} = ?' for k in changes)} WHERE scope_id = ? AND tag = ?",
                       (*changes.values(), clan["scope_id"], clan["tag"]))
    connection.commit()
    connection.close()


# ============================================================
# ПРОВЕРКА АДМИНОМ
# ============================================================

class ReviewButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rb:cls:(?P<pid>\d+):(?P<act>[ar])"):
    def __init__(self, purchase_id, act, *, item=None):
        ok = act == "a"
        super().__init__(item or discord.ui.Button(
            label=t("clan_shop.approve" if ok else "clan_shop.reject")[:80], emoji="✅" if ok else "✖️",
            style=discord.ButtonStyle.success if ok else discord.ButtonStyle.secondary,
            custom_id=f"rb:cls:{purchase_id}:{act}"))
        self.purchase_id, self.act = purchase_id, act

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["pid"]), match["act"], item=item)

    async def callback(self, interaction):
        import clan_quests
        if not clan_quests.can_review(interaction):
            await reply(interaction, "clan_quests.no_rights")
            return
        purchase = get_purchase(self.purchase_id)
        if purchase is None or purchase["status"] != "review":
            await reply(interaction, "clan_shop.already")
            return
        if self.act == "r":
            await interaction.response.send_modal(RejectModal(self.purchase_id))
            return
        if not set_status(self.purchase_id, "review", "delivering", decided_by=interaction.user.id):
            await reply(interaction, "clan_shop.already")
            return
        await interaction.response.defer()
        error = await deliver(interaction.client, purchase, interaction.guild)
        if error:
            await refund(purchase, error)
            await _close(interaction, t("clan_shop.result_failed", reason=t(error) if error.startswith("clan_shop.")
                                        else error))
            await _notify(interaction.client, purchase, "clan_shop.notice_failed",
                          reason=t(error) if error.startswith("clan_shop.") else error)
            return
        set_status(self.purchase_id, "delivering", "delivered")
        core.audit(interaction, "clan.purchase_approved", "clan", purchase["tag"], f"#{purchase['id']}")
        await _close(interaction, t("clan_shop.result_approved", who=interaction.user.mention))
        await _notify(interaction.client, purchase, "clan_shop.notice_approved")


class RejectModal(Modal, title="ОТКЛОНИТЬ ПОКУПКУ"):
    texts = "clan_shop.reject_modal"
    reason = discord.ui.TextInput(label="Причина", style=discord.TextStyle.paragraph, max_length=300)

    def __init__(self, purchase_id):
        super().__init__()
        self.purchase_id = purchase_id

    async def on_submit(self, interaction):
        purchase = get_purchase(self.purchase_id)
        if purchase is None or not set_status(self.purchase_id, "review", "rejecting", decided_by=interaction.user.id):
            await reply(interaction, "clan_shop.already")
            return
        await interaction.response.defer()
        await refund(purchase, self.reason.value.strip())
        _set(self.purchase_id, status="rejected")
        core.audit(interaction, "clan.purchase_rejected", "clan", purchase["tag"], f"#{purchase['id']}")
        await _close(interaction, t("clan_shop.result_rejected", who=interaction.user.mention,
                                    reason=self.reason.value.strip()))
        await _notify(interaction.client, purchase, "clan_shop.notice_rejected", reason=self.reason.value.strip())


async def _close(interaction, text):
    try:
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else None
        if embed is not None:
            embed.add_field(name=t("clan_shop.result"), value=text[:1024], inline=False)
        await interaction.message.edit(embed=embed, view=None)
    except (discord.HTTPException, AttributeError):
        pass


async def _notify(bot, purchase, key, **params):
    import clan_members
    import clans
    clan = clans.get_clan(purchase["scope_id"], purchase["tag"])
    if clan:
        await clan_members.tell_clan(clan_members.clan_guild(bot, clan), clan,
                                     t(key, tag=clan["tag"], item=t(f"clan_shop.item.{purchase['item']}"), **params))


def review_view(purchase_id):
    view = discord.ui.View(timeout=None)
    view.add_item(ReviewButton(purchase_id, "a"))
    view.add_item(ReviewButton(purchase_id, "r"))
    return view


async def send_review(bot, purchase):
    import clan_life
    channel_id = clan_life.setting(purchase["scope_id"], "review_channel")
    channel = bot.get_channel(int(channel_id)) if channel_id else None
    if channel is None:
        _log.warning("clan shop: no review channel for purchase %s", purchase["id"])
        return
    payload = purchase["payload"]
    details = "\n".join(f"**{k}:** {v}" for k, v in payload.items() if k != "file")
    embed = discord.Embed(title=t("clan_shop.review_title", tag=purchase["tag"],
                                  item=t(f"clan_shop.item.{purchase['item']}"))[:256],
                          description=t("clan_shop.review_text", who=f"<@{purchase['buyer']}>", price=purchase["price"],
                                        details=details or "—")[:4096], color=0x2B2D31)
    if payload.get("file") and purchase["item"] != "sound":
        embed.set_image(url=payload["file"])
    elif payload.get("file"):
        embed.add_field(name=t("clan_shop.sound"), value=payload["file"], inline=False)
    try:
        message = await channel.send(embed=embed, view=review_view(purchase["id"]))
        _set(purchase["id"], channel_id=channel.id, message_id=message.id)
    except discord.HTTPException:
        pass


async def remind_stale(bot):
    """24 ч — напоминание (роль дежурных, если задана), 48 ч — создателю бота."""
    import clan_life
    ensure_tables()
    connection = get_connection()
    rows = connection.execute("SELECT id FROM clan_purchases WHERE status = 'review'").fetchall()
    connection.close()
    now = time.time()
    for (purchase_id,) in rows:
        purchase = get_purchase(purchase_id)
        stage = sum(1 for after in REMIND if now - purchase["created_at"] >= after)
        if stage <= purchase["reminded"]:
            continue
        channel = bot.get_channel(purchase["channel_id"] or 0)
        if channel is not None:
            if stage == 1:
                role = clan_life.setting(purchase["scope_id"], "duty_role")
                who = f"<@&{role}>" if role else ""
            else:
                who = f"<@{core.OWNER_ID}>" if core.OWNER_ID else ""
            try:
                await channel.send(t("clan_shop.remind", who=who, tag=purchase["tag"],
                                     item=t(f"clan_shop.item.{purchase['item']}"),
                                     link=f"https://discord.com/channels/{channel.guild.id}/{channel.id}/"
                                          f"{purchase['message_id']}"),
                                   allowed_mentions=discord.AllowedMentions(roles=True, users=True))
            except discord.HTTPException:
                pass
        _set(purchase_id, reminded=stage)


def pending_count(scope_id):
    ensure_tables()
    connection = get_connection()
    count = connection.execute("SELECT COUNT(*) FROM clan_purchases WHERE scope_id = ? AND status = 'review'",
                               (scope_id,)).fetchone()[0]
    connection.close()
    return count


# ============================================================
# ПАНЕЛЬ МАГАЗИНА (/clan → «Магазин»)
# ============================================================

def shop_embed(interaction, clan):
    import clan_life
    import clan_treasury
    treasury, _ = clan_treasury.balance(clan["scope_id"], clan["tag"])
    items = "\n".join(t("clan_shop.line", item=t(f"clan_shop.item.{key}"), price=price,
                        about=t(f"clan_shop.item.{key}.about")) for key, (price, _) in ITEMS.items())
    history = "\n".join(t("clan_shop.history_line", id=p["id"], item=t(f"clan_shop.item.{p['item']}"),
                          status=t(f"clan_shop.status.{p['status']}"), reason=p["reason"])
                        for p in purchases(clan["scope_id"], clan["tag"])) or "—"
    closed = "" if clan_life.shop_open(clan["scope_id"], clan["tag"]) else t("clan_shop.closed")
    embed = panel_embed(interaction, "clan_shop.home", hints=False, tag=clan["tag"], treasury=treasury, items=items,
                        history=history, closed=closed)
    embed.set_image(url="attachment://shop.png")
    return embed


async def shop_file(clan):
    """Картинка магазина (макет clans.shop): казна и все товары с ценами."""
    import clan_art
    import clan_treasury
    treasury, _ = clan_treasury.balance(clan["scope_id"], clan["tag"])
    items = [(t(f"clan_shop.item.{key}").lower(), price) for key, (price, _) in ITEMS.items()]
    return await clan_art.file(clan_art.render_shop, items, treasury, filename="shop.png")


class ShopView(PanelView):
    clear_files_on_back = True

    def __init__(self, clan, back_target=None):
        super().__init__(back_target=back_target, timeout=900)
        self.scope_id, self.tag = clan["scope_id"], clan["tag"]
        select = discord.ui.Select(placeholder=t("clan_shop.pick")[:150], options=[
            discord.SelectOption(label=f"{t(f'clan_shop.item.{key}')} — {price}"[:100], value=key)
            for key, (price, _) in ITEMS.items()])

        async def picked(i):
            import clans
            clan_now = clans.get_clan(self.scope_id, self.tag)
            await i.response.send_modal(BuyModal(clan_now, select.values[0]))
        select.callback = picked
        self.add_item(select)


class BuyModal(Modal):
    """Одна форма на товар: что именно (название, тег, цвет, файл…) и подтверждение цены."""

    def __init__(self, clan, item):
        price = ITEMS[item][0]
        super().__init__(title=t("clan_shop.buy_title", price=price)[:45])
        self.clan, self.item = clan, item
        self.fields = {}
        if item == "name":
            self._text("name", "clan_shop.field.name", clan["name"], 24)
        elif item == "tag":
            self._text("tag", "clan_shop.field.tag", clan["tag"], 5)
        elif item == "color":
            self._text("color", "clan_shop.field.color", "#C6F135", 7)
        elif item in ("text_channel", "voice_channel"):
            self._text("channel", "clan_shop.field.channel", "", 40)
        elif item == "quest_swap":
            self._text("level", "clan_shop.field.level", "лёгкий", 10)
        elif item in ("emblem", "emoji", "sticker", "sound"):
            if item != "emblem":
                self._text("name", "clan_shop.field.short_name", clan["tag"].lower(), 30)
            self.upload = discord.ui.FileUpload(required=True, max_values=1)
            self.add_item(discord.ui.Label(text=t(f"clan_shop.field.file.{item}")[:45],
                                           description=t(f"clan_shop.field.file.{item}.help")[:100],
                                           component=self.upload))
        self.confirm = discord.ui.TextInput(label=t("clan_shop.field.confirm", price=price)[:45], max_length=3,
                                            placeholder="да")
        self.add_item(self.confirm)

    def _text(self, key, label_key, default, length):
        item = discord.ui.TextInput(label=t(label_key)[:45], default=default or None, max_length=length,
                                    required=True)
        self.fields[key] = item
        self.add_item(item)

    async def on_submit(self, interaction):
        import clan_members
        import clans
        import web_panel
        clan = clans.get_clan(self.clan["scope_id"], self.clan["tag"])
        admin = clan_members.is_admin(interaction.guild, interaction.user)
        if clan is None or not (clan_members.rank_of(clan, interaction.user.id) in ("leader", "deputy") or admin):
            await reply(interaction, "clan_shop.error.rights")
            return
        if self.confirm.value.strip().lower() not in ("да", "yes", "д", "y"):
            await reply(interaction, "clan_shop.error.not_confirmed")
            return
        payload = {key: item.value.strip() for key, item in self.fields.items()}
        if self.item == "quest_swap":
            payload["level"] = {"лёгкий": "easy", "легкий": "easy", "средний": "medium", "сложный": "hard"}.get(
                payload.get("level", "").lower(), payload.get("level", "easy"))
        await interaction.response.defer(ephemeral=True, thinking=True)
        if hasattr(self, "upload"):
            files = self.upload.values or []
            if files:
                data = await files[0].read()
                if self.item == "sound":
                    if len(data) > 512 * 1024 or not files[0].filename.lower().endswith((".mp3", ".ogg")):
                        await reply(interaction, "clan_shop.error.sound")
                        return
                    name = f"{int(time.time())}_{re.sub(r'[^a-z0-9._-]', '', files[0].filename.lower())}"[:60]
                    web_panel.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
                    (web_panel.ASSETS_DIR / name).write_bytes(data)
                    payload["file"] = f"{web_panel.panel_url() or ''}/assets/{name}"
                else:
                    name, error = web_panel.save_asset(data)
                    if error:
                        await reply(interaction, "clan_shop.error.file")
                        return
                    payload["file"] = f"{web_panel.panel_url() or ''}/assets/{name}"
        result = await buy(interaction, clan, self.item, payload)
        await reply(interaction, result, item=t(f"clan_shop.item.{self.item}"))


DYNAMIC_ITEMS = [ReviewButton]
