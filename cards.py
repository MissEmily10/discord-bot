"""
Живые картинки: сообщение-картинка, которую бот перерисовывает, когда
меняются значения. Первый шаблон — рейтинг (место, имя, очки).

Под картинкой — кнопка «Изменить». Менять могут автор, админы и те люди и
роли, которых автор выбрал. Изменение сразу перерисовывает картинку в канале.

Оформление: пока макета из Figma нет, бот рисует рейтинг сам, в цветах бота.
Когда макет будет готов — фон выгружается в panel_art, а текст пишется по
координатам плейсхолдеров, как в баннерах форм (banner_render.py).
"""

import io
import json
import os
import re
import time

import discord
from PIL import Image, ImageDraw, ImageFont

import core
import ui_emoji
from core import PanelView, Modal, t, panel_embed
from actions import reply
from database import get_connection

ROOT = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(ROOT, "fonts")
MAX_ROWS = 15
MAX_EDITORS = 10
FILENAME = "card.png"

# цвета бота (как у дудлов и баннеров)
INK = (32, 28, 30)
CREAM = (241, 238, 236)
MUTED = (110, 102, 104)
LIME = (180, 238, 58)
PINK = (227, 179, 192)
BLUE = (198, 216, 230)
MAROON = (122, 22, 38)
PODIUM = [LIME, PINK, BLUE]


# ============================================================
# ХРАНИЛИЩЕ
# ============================================================

def ensure_table():
    connection = get_connection()
    connection.execute("""CREATE TABLE IF NOT EXISTS live_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
        template TEXT NOT NULL DEFAULT 'rating', title TEXT NOT NULL DEFAULT '',
        data_json TEXT NOT NULL DEFAULT '{}', editor_ids_json TEXT NOT NULL DEFAULT '[]',
        editor_roles_json TEXT NOT NULL DEFAULT '[]', channel_id INTEGER, message_id INTEGER,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
    )""")
    connection.commit()
    connection.close()


COLUMNS = ("id", "guild_id", "owner_id", "template", "title", "data", "editors", "editor_roles",
           "channel_id", "message_id")


def _row_to_card(row):
    if not row:
        return None
    card = dict(zip(COLUMNS, row))
    card["data"] = _json(card["data"], {})
    card["editors"] = _json(card["editors"], [])
    card["editor_roles"] = _json(card["editor_roles"], [])
    card["data"].setdefault("rows", [])
    return card


def _json(value, default):
    try:
        result = json.loads(value or "")
    except (TypeError, ValueError):
        return default
    return result if isinstance(result, type(default)) else default


def get_card(card_id):
    ensure_table()
    connection = get_connection()
    row = connection.execute(f"SELECT id, guild_id, owner_id, template, title, data_json, editor_ids_json, "
                             f"editor_roles_json, channel_id, message_id FROM live_cards WHERE id = ?",
                             (card_id,)).fetchone()
    connection.close()
    return _row_to_card(row)


def get_cards(guild_id):
    ensure_table()
    connection = get_connection()
    rows = connection.execute("SELECT id FROM live_cards WHERE guild_id = ? ORDER BY id DESC", (guild_id,)).fetchall()
    connection.close()
    return [get_card(card_id) for card_id, in rows]


def create_card(guild_id, owner_id, title, template="rating"):
    ensure_table()
    now = int(time.time())
    connection = get_connection()
    cursor = connection.execute(
        "INSERT INTO live_cards (guild_id, owner_id, template, title, data_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", (guild_id, owner_id, template, title, json.dumps({"rows": []}), now, now))
    connection.commit()
    card_id = cursor.lastrowid
    connection.close()
    return card_id


def save_card(card):
    connection = get_connection()
    connection.execute(
        "UPDATE live_cards SET title = ?, data_json = ?, editor_ids_json = ?, editor_roles_json = ?, "
        "channel_id = ?, message_id = ?, updated_at = ? WHERE id = ?",
        (card["title"], json.dumps(card["data"], ensure_ascii=False), json.dumps(card["editors"]),
         json.dumps(card["editor_roles"]), card["channel_id"], card["message_id"], int(time.time()), card["id"]))
    connection.commit()
    connection.close()


def delete_card(card_id):
    connection = get_connection()
    connection.execute("DELETE FROM live_cards WHERE id = ?", (card_id,))
    connection.commit()
    connection.close()


# ============================================================
# РЕЙТИНГ: ДАННЫЕ
# ============================================================

def sorted_rows(card):
    return sorted(card["data"]["rows"], key=lambda row: (-row["score"], row["name"].lower()))


def parse_change(raw, current):
    """«+10» / «-5» — прибавить или отнять, «42» — поставить. -> число или None."""
    text = (raw or "").strip().replace(",", ".").replace(" ", "")
    match = re.fullmatch(r"([+-]?)(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    value = float(match[2])
    value = int(value) if value.is_integer() else round(value, 2)
    if match[1] == "+":
        return current + value
    if match[1] == "-":
        return current - value
    return value


def set_score(card, name, raw):
    """-> None или ключ ошибки. Новое имя добавляется, старое — меняется."""
    name = re.sub(r"\s+", " ", (name or "").strip())[:32]
    if not name:
        return "cards.error.no_name"
    rows = card["data"]["rows"]
    row = next((row for row in rows if row["name"].lower() == name.lower()), None)
    value = parse_change(raw, row["score"] if row else 0)
    if value is None:
        return "cards.error.bad_value"
    if row is None:
        if len(rows) >= MAX_ROWS:
            return "cards.error.full"
        rows.append({"name": name, "score": value})
    else:
        row["score"] = value
    return None


def score_text(value):
    return f"{value:,}".replace(",", " ") if isinstance(value, int) else str(value)


# ============================================================
# РЕЙТИНГ: КАРТИНКА
# ============================================================

_fonts = {}


def _font(name, size):
    key = (name, size)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(os.path.join(FONT_DIR, name), size)
    return _fonts[key]


def _fit(draw_text, font_name, size, width, min_size=20):
    font = _font(font_name, size)
    while font.getlength(draw_text) > width and size > min_size:
        size -= 2
        font = _font(font_name, size)
    while font.getlength(draw_text) > width and len(draw_text) > 2:
        draw_text = draw_text[:-2] + "…"
    return draw_text, font


def render_rating(title, rows, empty_text="—"):
    """Рейтинг в цветах бота. -> PNG (bytes)."""
    width, pad, head, row_h = 1200, 56, 190, 84
    height = head + max(1, len(rows)) * row_h + pad
    picture = Image.new("RGB", (width, height), CREAM)
    draw = ImageDraw.Draw(picture)
    # заголовок
    shown, font = _fit((title or "").lower(), "Inter-Bold.otf", 88, width - 2 * pad - 140)
    draw.text((pad, 104), shown, font=font, fill=INK, anchor="ls")
    draw.rounded_rectangle((pad, 132, pad + 120, 144), radius=6, fill=LIME, outline=INK, width=3)
    # восклицательный знак-акцент, как на баннерах
    draw.text((width - pad, 150), "!", font=_font("Inter-Bold.otf", 170), fill=BLUE, anchor="rs")
    if not rows:
        draw.text((pad, head + 50), empty_text, font=_font("Inter-SemiBold.otf", 34), fill=MUTED, anchor="ls")
    place = 0
    previous = None
    for index, row in enumerate(rows):
        if row["score"] != previous:
            place = index + 1  # одинаковые очки — одинаковое место
            previous = row["score"]
        top = head + index * row_h
        middle = top + row_h // 2
        if index:
            draw.line((pad, top, width - pad, top), fill=(214, 208, 206), width=2)
        # место: кружок у тройки лидеров, просто цифра — у остальных
        cx = pad + 34
        if place <= 3:
            draw.ellipse((cx - 30, middle - 30, cx + 30, middle + 30), fill=PODIUM[place - 1], outline=INK, width=5)
        draw.text((cx, middle + 1), str(place), font=_font("Inter-Bold.otf", 34), fill=INK, anchor="mm")
        score = score_text(row["score"])
        score_font = _font("Inter-Bold.otf", 40)
        score_w = score_font.getlength(score)
        name, name_font = _fit(row["name"], "Inter-SemiBold.otf", 38, width - 2 * pad - 110 - score_w - 40)
        draw.text((pad + 96, middle), name, font=name_font, fill=INK, anchor="lm")
        draw.text((width - pad, middle), score, font=score_font, fill=MAROON if place == 1 else INK, anchor="rm")
    output = io.BytesIO()
    picture.save(output, "PNG", optimize=True)
    return output.getvalue()


def card_message(card):
    """-> kwargs сообщения с картинкой и кнопкой «Изменить»."""
    png = render_rating(card["title"], sorted_rows(card), t("cards.rating.empty"))
    embed = discord.Embed(color=core.embed_color()).set_image(url=f"attachment://{FILENAME}")
    view = discord.ui.View(timeout=None)
    view.add_item(CardEditButton(card["id"]))
    return {"embed": embed, "view": view, "file": discord.File(io.BytesIO(png), filename=FILENAME)}


async def refresh_message(guild, card):
    """Перерисовать опубликованную картинку. -> False, если сообщения больше нет."""
    channel = guild.get_channel_or_thread(card["channel_id"]) if card["channel_id"] else None
    if channel is None or not card["message_id"]:
        return False
    message_kwargs = card_message(card)
    file = message_kwargs.pop("file")
    try:
        message = await channel.fetch_message(card["message_id"])
        await message.edit(attachments=[file], **message_kwargs)
    except discord.HTTPException:
        return False
    return True


# ============================================================
# ДОСТУП
# ============================================================

def can_edit(interaction, card):
    from extended_modules import is_admin
    if card is None or interaction.guild is None or card["guild_id"] != interaction.guild.id:
        return False
    if interaction.user.id == card["owner_id"] or interaction.user.id in card["editors"] or is_admin(interaction):
        return True
    roles = {role.id for role in getattr(interaction.user, "roles", [])}
    return bool(roles & set(card["editor_roles"]))


def can_manage(interaction, card):
    from extended_modules import owns_or_admin
    return card is not None and interaction.guild is not None and card["guild_id"] == interaction.guild.id \
        and owns_or_admin(interaction, card["owner_id"])


# ============================================================
# КНОПКА ПОД КАРТИНКОЙ И ПАНЕЛЬ ПРАВКИ
# ============================================================

class CardEditButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rb:cd:(?P<cid>\d+)"):
    def __init__(self, card_id, *, item=None):
        super().__init__(item or discord.ui.Button(
            label=t("cards.edit_button")[:80], emoji=ui_emoji.swap("✏️"), style=discord.ButtonStyle.secondary,
            custom_id=f"rb:cd:{card_id}",
        ))
        self.card_id = card_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["cid"]), item=item)

    async def callback(self, interaction):
        card = get_card(self.card_id)
        if card is None:
            await reply(interaction, "cards.gone")
            return
        if not can_edit(interaction, card):
            await reply(interaction, "cards.denied")
            return
        await interaction.response.send_message(embed=editor_embed(interaction, card),
                                                view=CardEditorView(self.card_id), ephemeral=True)


def editor_embed(interaction, card):
    rows = sorted_rows(card)
    lines = [t("cards.editor.line", place=n, name=row["name"], score=score_text(row["score"]))
             for n, row in enumerate(rows, 1)]
    editors = [f"<@{uid}>" for uid in card["editors"]] + [f"<@&{rid}>" for rid in card["editor_roles"]]
    return panel_embed(interaction, "cards.editor", title=card["title"] or t("cards.editor.title"),
                       rows="\n".join(lines) or t("cards.rating.empty"), max=MAX_ROWS,
                       editors=", ".join(editors) or t("cards.editor.only_owner"))


async def apply_change(interaction, card, problem=None):
    """Сохранить, перерисовать картинку в канале, обновить панель правки."""
    if problem:
        await reply(interaction, problem, max=MAX_ROWS)
        return
    save_card(card)
    await interaction.response.defer()
    published = await refresh_message(interaction.guild, card)
    await interaction.edit_original_response(embed=editor_embed(interaction, card), view=CardEditorView(card["id"]))
    if card["message_id"] and not published:
        await interaction.followup.send(t("cards.error.message_gone"), ephemeral=True)


class CardEditorView(PanelView):
    texts = "cards.editor"
    button_order = ("set_score", "remove", "rename", "access", "publish", "delete_card")

    def __init__(self, card_id, back_target=None):
        super().__init__(back_target=back_target, timeout=900)
        self.card_id = card_id

    async def _card(self, interaction, manage=False):
        card = get_card(self.card_id)
        allowed = can_manage(interaction, card) if manage else can_edit(interaction, card)
        if not allowed:
            await reply(interaction, "cards.denied" if card else "cards.gone")
            return None
        return card

    @discord.ui.button(label="Очки", emoji="➕", style=discord.ButtonStyle.primary)
    async def set_score(self, interaction, button):
        if await self._card(interaction):
            await interaction.response.send_modal(ScoreModal(self.card_id))

    @discord.ui.button(label="Убрать", emoji="➖", style=discord.ButtonStyle.secondary)
    async def remove(self, interaction, button):
        card = await self._card(interaction)
        if not card:
            return
        rows = sorted_rows(card)
        if not rows:
            await reply(interaction, "cards.rating.empty")
            return
        view = PanelView(back_target=(core.screen(interaction), self), timeout=600)
        select = core.PagedSelect(placeholder=t("cards.editor.pick_row")[:150], options=[
            discord.SelectOption(label=row["name"][:100], description=score_text(row["score"])[:100], value=str(n))
            for n, row in enumerate(rows)])

        async def picked(i):
            fresh = await self._card(i)
            if not fresh:
                return
            name = rows[int(select.values[0])]["name"]
            fresh["data"]["rows"] = [row for row in fresh["data"]["rows"] if row["name"] != name]
            await apply_change(i, fresh)

        select.on_pick = picked
        view.add_item(select)
        await interaction.response.edit_message(view=view)

    @discord.ui.button(label="Название", emoji="📝", style=discord.ButtonStyle.secondary)
    async def rename(self, interaction, button):
        card = await self._card(interaction)
        if card:
            await interaction.response.send_modal(TitleModal(self.card_id, card["title"]))

    @discord.ui.button(label="Кто меняет", emoji="👥", style=discord.ButtonStyle.secondary)
    async def access(self, interaction, button):
        card = await self._card(interaction, manage=True)
        if not card:
            return
        view = PanelView(back_target=(core.screen(interaction), self), timeout=600)
        users = discord.ui.UserSelect(placeholder=t("cards.editor.pick_users")[:150], min_values=0,
                                      max_values=MAX_EDITORS, row=0)
        roles = discord.ui.RoleSelect(placeholder=t("cards.editor.pick_roles")[:150], min_values=0,
                                      max_values=MAX_EDITORS, row=1)

        async def picked_users(i):
            fresh = await self._card(i, manage=True)
            if fresh:
                fresh["editors"] = [user.id for user in users.values if not user.bot]
                await apply_change(i, fresh)

        async def picked_roles(i):
            fresh = await self._card(i, manage=True)
            if fresh:
                fresh["editor_roles"] = [role.id for role in roles.values if not role.is_default()]
                await apply_change(i, fresh)

        users.callback, roles.callback = picked_users, picked_roles
        view.add_item(users)
        view.add_item(roles)
        await interaction.response.edit_message(embed=panel_embed(interaction, "cards.access"), view=view)

    @discord.ui.button(label="Опубликовать", emoji="📤", style=discord.ButtonStyle.success)
    async def publish(self, interaction, button):
        card = await self._card(interaction, manage=True)
        if not card:
            return
        view = PanelView(back_target=(core.screen(interaction), self), timeout=600)
        select = discord.ui.ChannelSelect(placeholder=t("embed.send.placeholder")[:150],
                                          channel_types=[discord.ChannelType.text, discord.ChannelType.news])

        async def picked(i):
            fresh = await self._card(i, manage=True)
            channel = i.guild.get_channel(select.values[0].id) if fresh else None
            if not fresh:
                return
            if channel is None or not core.can_post_in(i.user, channel):
                await reply(i, "embed.send.no_perm_user", channel=f"<#{select.values[0].id}>")
                return
            if core.bot_missing(channel, files=True):
                await reply(i, "embed.send.no_perm_bot", channel=channel.mention,
                            perms=core.bot_missing_text(channel, files=True))
                return
            await i.response.defer()
            message = await channel.send(**card_message(fresh))
            fresh["channel_id"], fresh["message_id"] = channel.id, message.id
            save_card(fresh)
            core.audit(i, "card.published", "card", fresh["id"], f"channel={channel.id}")
            await i.edit_original_response(embed=editor_embed(i, fresh), view=CardEditorView(fresh["id"]))
            await i.followup.send(t("cards.published", channel=channel.mention), ephemeral=True)

        select.callback = picked
        view.add_item(select)
        await interaction.response.edit_message(view=view)

    @discord.ui.button(label="Удалить", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete_card(self, interaction, button):
        card = await self._card(interaction, manage=True)
        if not card:
            return
        delete_card(card["id"])
        core.audit(interaction, "card.deleted", "card", card["id"], card["title"])
        await interaction.response.edit_message(embed=panel_embed(interaction, "cards.deleted"), view=None)


class ScoreModal(Modal, title="ОЧКИ"):
    texts = "cards.score_modal"
    name = discord.ui.TextInput(label="Кто", max_length=32)
    value = discord.ui.TextInput(label="Очки: 42, +10 или -5", max_length=16)

    def __init__(self, card_id):
        super().__init__()
        self.card_id = card_id

    async def on_submit(self, interaction):
        card = get_card(self.card_id)
        if not can_edit(interaction, card):
            await reply(interaction, "cards.denied")
            return
        await apply_change(interaction, card, set_score(card, self.name.value, self.value.value))


class TitleModal(Modal, title="НАЗВАНИЕ"):
    texts = "cards.title_modal"
    name = discord.ui.TextInput(label="Название картинки", max_length=60)

    def __init__(self, card_id, current="", create=False):
        super().__init__()
        self.card_id = card_id
        self.create = create
        self.name.default = current

    async def on_submit(self, interaction):
        title = self.name.value.strip()
        if self.create:
            card = get_card(create_card(interaction.guild.id, interaction.user.id, title))
            core.audit(interaction, "card.created", "card", card["id"], title)
            await interaction.response.edit_message(embed=editor_embed(interaction, card),
                                                    view=CardEditorView(card["id"]))
            return
        card = get_card(self.card_id)
        if not can_edit(interaction, card):
            await reply(interaction, "cards.denied")
            return
        card["title"] = title
        await apply_change(interaction, card)


# ============================================================
# /interactive → Живые картинки
# ============================================================

def home_embed(interaction):
    cards = get_cards(interaction.guild.id)
    lines = [t("cards.home.line", title=card["title"], count=len(card["data"]["rows"]),
               where=f"<#{card['channel_id']}>" if card["message_id"] else t("cards.home.not_published"))
             for card in cards[:20]]
    return panel_embed(interaction, "cards.home", cards="\n".join(lines) or t("cards.home.empty"))


class CardsHome(PanelView):
    texts = "cards.home"

    def __init__(self, interaction, back_target=None):
        super().__init__(back_target=back_target)
        cards = [card for card in get_cards(interaction.guild.id) if can_edit(interaction, card)]
        if cards:
            select = core.PagedSelect(placeholder=t("cards.home.pick")[:150], options=[
                discord.SelectOption(label=(card["title"] or "—")[:100], value=str(card["id"])) for card in cards])

            async def picked(i):
                card = get_card(int(select.values[0]))
                if not can_edit(i, card):
                    await reply(i, "cards.denied")
                    return
                await i.response.edit_message(embed=editor_embed(i, card),
                                              view=CardEditorView(card["id"], back_target=(core.screen(i), self)))

            select.on_pick = picked
            self.add_item(select)

    @discord.ui.button(label="Новый рейтинг", emoji="🏆", style=discord.ButtonStyle.success)
    async def create_rating(self, interaction, button):
        await interaction.response.send_modal(TitleModal(None, t("cards.rating.default_title"), create=True))


DYNAMIC_ITEMS = [CardEditButton]
