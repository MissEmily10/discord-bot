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
    # своя слэш-команда картинки (card_commands.py)
    if "command" not in {row[1] for row in connection.execute("PRAGMA table_info(live_cards)")}:
        connection.execute("ALTER TABLE live_cards ADD COLUMN command TEXT NOT NULL DEFAULT ''")
    connection.commit()
    connection.close()


COLUMNS = ("id", "guild_id", "owner_id", "template", "title", "data", "editors", "editor_roles",
           "channel_id", "message_id", "command")


def _row_to_card(row):
    if not row:
        return None
    card = dict(zip(COLUMNS, row))
    card["data"] = _json(card["data"], {})
    card["editors"] = _json(card["editors"], [])
    card["editor_roles"] = _json(card["editor_roles"], [])
    from card_templates import default_data
    for key, value in default_data(card["template"]).items():
        card["data"].setdefault(key, value)
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
                             f"editor_roles_json, channel_id, message_id, command FROM live_cards WHERE id = ?",
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
    from card_templates import default_data
    ensure_table()
    now = int(time.time())
    connection = get_connection()
    cursor = connection.execute(
        "INSERT INTO live_cards (guild_id, owner_id, template, title, data_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", (guild_id, owner_id, template, title,
                                           json.dumps(default_data(template), ensure_ascii=False), now, now))
    connection.commit()
    card_id = cursor.lastrowid
    connection.close()
    return card_id


def save_card(card):
    connection = get_connection()
    connection.execute(
        "UPDATE live_cards SET title = ?, data_json = ?, editor_ids_json = ?, editor_roles_json = ?, "
        "channel_id = ?, message_id = ?, command = ?, updated_at = ? WHERE id = ?",
        (card["title"], json.dumps(card["data"], ensure_ascii=False), json.dumps(card["editors"]),
         json.dumps(card["editor_roles"]), card["channel_id"], card["message_id"], card.get("command") or "",
         int(time.time()), card["id"]))
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


async def card_message(card, guild=None):
    """
    -> kwargs сообщения с картинкой и кнопкой «Изменить». Переменные в текстах подставляются.
    У картинки со своей slash-командой кнопки нет: меняют командой, панель — в /pictures.
    """
    png = await render_card(card, guild)
    embed = discord.Embed(color=core.embed_color()).set_image(url=f"attachment://{FILENAME}")
    view = None
    if not card.get("command"):
        view = discord.ui.View(timeout=None)
        view.add_item(CardEditButton(card["id"]))
    return {"embed": embed, "view": view, "file": discord.File(io.BytesIO(png), filename=FILENAME)}


async def render_card(card, guild=None):
    """Картинка (PNG) с подставленными переменными и аватаркой «участника недели»."""
    import asyncio
    import card_templates as ct
    from actions import VARIABLE, substitute
    title, data = card["title"], json.loads(json.dumps(card["data"]))
    found = ct.texts_of(card)
    needed = {(m[1], m[2]) for _, text in found for m in VARIABLE.finditer(str(text or ""))}
    if needed and guild is not None:
        import live
        variables = await live.collect(guild, needed)
        title = substitute(title, variables)
        for key, _, kind in ct.spec(card["template"]).get("fields", []):
            if kind == ct.T and isinstance(data.get(key), str):
                data[key] = substitute(data[key], variables)
        for row in data.get("rows") or []:
            if isinstance(row, list):
                row[:] = [substitute(cell, variables) if isinstance(cell, str) else cell for cell in row]
    if card["template"] == "spotlight" and guild is not None:
        member = find_member(guild, data.get("who"))
        if member is not None:
            data["_name"] = member.display_name
            data["_avatar"] = await core.fetch_bytes(member.display_avatar.replace(size=256, format="png").url)
    return await asyncio.to_thread(ct.render, card, title, data, t("cards.rating.empty"))


def find_member(guild, who):
    """«<@id>», ID или ник/имя -> участник сервера или None."""
    who = (who or "").strip()
    match = re.fullmatch(r"<@!?(\d+)>|(\d{15,25})", who)
    if match:
        return guild.get_member(int(match[1] or match[2]))
    low = who.lower().lstrip("@")
    if not low:
        return None
    for member in getattr(guild, "members", []):
        if low in (member.name.lower(), member.display_name.lower()):
            return member
    return None


def uses_variables(card):
    import card_templates as ct
    from actions import VARIABLE
    if ct.is_dynamic(card["template"]):
        return True  # отсчёт, календарь, дни рождения — меняются со временем
    return any(VARIABLE.search(str(text or "")) for _, text in ct.texts_of(card))


async def refresh_message(guild, card):
    """Перерисовать опубликованную картинку. -> False, если сообщения больше нет."""
    channel = guild.get_channel_or_thread(card["channel_id"]) if card["channel_id"] else None
    if channel is None or not card["message_id"]:
        return False
    message_kwargs = await card_message(card, guild)
    file = message_kwargs.pop("file")
    try:
        message = await channel.fetch_message(card["message_id"])
        await message.edit(attachments=[file], **message_kwargs)
    except discord.HTTPException:
        return False
    return True


def apply_counter(card, name, change):
    """
    Кнопка «Счётчик» с тем же именем, что и значение в картинке, меняет и его:
    строку рейтинга/таблицы («Kick»), числовое поле по подписи («счёт слева»)
    или главное число картинки, если имя совпадает с её командой.
    change — «+1», «-5» или «7». -> True, если картинка изменилась.
    """
    import card_templates as ct
    data, template = card["data"], card["template"]
    key = (name or "").strip().lower()
    if not key:
        return False
    if template == "rating":
        if any(row["name"].lower() == key for row in data.get("rows") or []):
            return set_score(card, name, change) is None
        return False
    field = ct.field_by_label(template, key)
    if field and next((k for f, _, k in ct.spec(template).get("fields", []) if f == field), None) == ct.N:
        return ct.set_field(template, data, field, change) is None
    if ct.has_rows(template) and not key.isdigit() and ct.row_index(data, name) is not None:
        return ct.set_row(template, data, name, change) is None
    if key == (card.get("command") or "").lower():
        if template == "progress":
            return ct.set_progress(data, change) is None
        fields = ct.spec(template).get("fields", [])
        if fields and fields[0][2] == ct.N:
            return ct.set_field(template, data, fields[0][0], change) is None
    return False


def counter_in_text(card, name):
    """Картинка показывает {counter:имя} — её надо перерисовать."""
    import card_templates as ct
    needle = "{counter:" + (name or "").strip().lower() + "}"
    return any(needle in str(text or "").lower() for _, text in ct.texts_of(card))


async def counter_changed(guild, name, op, number):
    """Счётчик изменён кнопкой -> те же значения в живых картинках. -> сколько картинок обновлено."""
    value = int(number) if float(number).is_integer() else number
    change = {"+": f"+{value}", "-": f"-{value}"}.get(op, str(value))
    updated = 0
    for card in get_cards(guild.id):
        changed = apply_counter(card, name, change)
        if changed:
            save_card(card)
        if changed or counter_in_text(card, name):
            updated += 1
            if card["message_id"]:
                await refresh_message(guild, card)
    return updated


VARIABLES_REFRESH = 10 * 60  # картинки с переменными ({voice}, {online}…) перерисовываются сами


async def refresh_loop(bot):
    import asyncio
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(VARIABLES_REFRESH)
        ensure_table()
        connection = get_connection()
        rows = connection.execute("SELECT id FROM live_cards WHERE message_id IS NOT NULL").fetchall()
        connection.close()
        for card_id, in rows:
            card = get_card(card_id)
            guild = bot.get_guild(card["guild_id"]) if card else None
            if guild is None or not uses_variables(card):
                continue
            try:
                await refresh_message(guild, card)
            except Exception:  # одна картинка не должна ломать остальные
                core._log.exception("card refresh %s", card_id)


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


COMMON_BUTTONS = ("rename", "access", "command", "save_to_library", "publish", "delete_card")


def template_buttons(template):
    """Кнопки правки картинки этого шаблона (остальные убираются из панели)."""
    import card_templates as ct
    if template == "rating":
        return ("set_score", "remove")
    buttons = []
    if template in ("progress", "counter"):
        buttons.append("value")
    if ct.spec(template).get("fields"):
        buttons.append("fields")
    if ct.has_rows(template):
        buttons += ["row", "remove_row"]
    if template == "table":
        buttons.append("columns")
    buttons.append("theme")
    return tuple(buttons)


def template_name(template):
    import card_templates as ct
    return t("cards.template.rating") if template == "rating" else ct.spec(template).get("name", template)


def template_about(template):
    import card_templates as ct
    return t("cards.template.rating.about") if template == "rating" else ct.spec(template).get("about", "")


async def templates_sheets():
    """Все шаблоны с примерами — две картинки-сетки (для «Все шаблоны»)."""
    import asyncio
    import card_templates as ct

    def build():
        from PIL import ImageDraw as Draw
        import banner_render
        keys = list(ct.TEMPLATES)
        tiles = []
        for n, key in enumerate(keys):
            if key == "rating":
                png = render_rating("Рейтинг клана", [{"name": "Аня", "score": 120}, {"name": "Кот", "score": 95},
                                                      {"name": "Пёс", "score": 60}])
            else:
                png = ct.sample(key, list(ct.THEMES)[n % len(ct.THEMES)])
            tile = Image.open(io.BytesIO(png)).convert("RGB")
            tile.thumbnail((560, 2000))
            tiles.append((f"{n + 1}. {template_name(key)}", tile))
        files = []
        label_font = banner_render._font("fallback:Inter-SemiBold.otf", 22)
        for part, chunk in enumerate((tiles[:11], tiles[11:])):
            heights = [0, 0]
            placed = []
            for label, tile in chunk:
                column = heights.index(min(heights))
                placed.append((column, heights[column], label, tile))
                heights[column] += tile.height + 44
            sheet = Image.new("RGB", (2 * 560 + 3 * 16, max(heights) + 16), (43, 45, 49))
            draw = Draw.Draw(sheet)
            for column, y, label, tile in placed:
                x = 16 + column * (560 + 16)
                draw.text((x, y + 30), label, font=label_font, fill=(230, 230, 230), anchor="ls")
                sheet.paste(tile, (x, y + 40))
            output = io.BytesIO()
            sheet.save(output, "PNG", optimize=True)
            files.append(output.getvalue())
        return files

    datas = await asyncio.to_thread(build)
    return [discord.File(io.BytesIO(data), filename=f"templates_{n + 1}.png") for n, data in enumerate(datas)]


def summary(card):
    """Что сейчас на картинке — текстом для панели правки."""
    import card_templates as ct
    data, template = card["data"], card["template"]
    if template == "rating":
        rows = sorted_rows(card)
        return "\n".join(t("cards.editor.line", place=n, name=row["name"], score=score_text(row["score"]))
                         for n, row in enumerate(rows, 1)) or t("cards.rating.empty")
    lines = []
    for key, label, kind in ct.spec(template).get("fields", []):
        value = data.get(key)
        value = ct.number_text(value) if kind == ct.N else (str(value or "—")[:200])
        lines.append(f"**{label.split(' (')[0]}:** {value}")
    if ct.has_rows(template):
        columns = ct.columns_of(template, data)
        lines.append("\n**" + " | ".join(name.split(" (")[0] for name, _ in columns) + "**")
        rows = data.get("rows") or []
        lines += [f"`{n}.` " + " | ".join(ct.number_text(cell) if isinstance(cell, (int, float)) else (cell or "—")
                                          for cell in row) for n, row in enumerate(rows, 1)]
        if not rows:
            lines.append(t("cards.table.empty"))
    lines.append(t("cards.summary.theme", theme=ct.THEME_NAMES.get(data.get("theme"), "—")))
    return "\n".join(lines)[:3000]


def editor_embed(interaction, card):
    editors = [f"<@{uid}>" for uid in card["editors"]] + [f"<@&{rid}>" for rid in card["editor_roles"]]
    command = t("cards.command.line", name=card["command"]) if card.get("command") else t("cards.command.none")
    embed = panel_embed(interaction, "cards.editor", hints=False,
                        title=card["title"] or template_name(card["template"]),
                        rows=summary(card), max=MAX_ROWS, command=command,
                        editors=", ".join(editors) or t("cards.editor.only_owner"))
    lines = [t("hints.line", label=t(f"cards.editor.{name}"), hint=t(f"cards.editor.{name}.hint"))
             for name in template_buttons(card["template"]) + COMMON_BUTTONS
             if core.has_text(f"cards.editor.{name}.hint") and t(f"cards.editor.{name}.hint").strip()]
    if lines:
        embed.description = f"{embed.description}\n\n{t('hints.header')}\n" + "\n".join(lines)
    return embed


async def apply_change(interaction, card, problem=None):
    """Сохранить, перерисовать картинку в канале, обновить панель правки."""
    if problem:
        await reply(interaction, problem, max=MAX_ROWS)
        return
    save_card(card)
    await interaction.response.defer()
    published = await refresh_message(interaction.guild, card)
    # attachments=[] — если правили из галереи стилей, её коллаж убираем
    await interaction.edit_original_response(embed=editor_embed(interaction, card), view=CardEditorView(card["id"]),
                                             attachments=[])
    if card["message_id"] and not published:
        await interaction.followup.send(t("cards.error.message_gone"), ephemeral=True)


class CardEditorView(PanelView):
    texts = "cards.editor"
    button_order = ("set_score", "value", "fields", "row", "remove", "remove_row", "columns", "theme",
                    "rename", "publish", "command", "access", "save_to_library", "delete_card")

    def __init__(self, card_id, back_target=None):
        super().__init__(back_target=back_target, timeout=900)
        self.card_id = card_id
        card = get_card(card_id)
        keep = set(template_buttons(card["template"] if card else "rating")) | set(COMMON_BUTTONS)
        for name in ("set_score", "remove", "value", "fields", "row", "remove_row", "columns", "theme"):
            if name not in keep:
                self.remove_item(getattr(self, name))

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

    @discord.ui.button(label="Значение", emoji="➕", style=discord.ButtonStyle.primary)
    async def value(self, interaction, button):
        if await self._card(interaction):
            await interaction.response.send_modal(ValueModal(self.card_id))

    @discord.ui.button(label="Поля", emoji="✏️", style=discord.ButtonStyle.primary)
    async def fields(self, interaction, button):
        card = await self._card(interaction)
        if card:
            await interaction.response.send_modal(FieldsModal(card))

    @discord.ui.button(label="Тема", emoji="🎨", style=discord.ButtonStyle.secondary)
    async def theme(self, interaction, button):
        card = await self._card(interaction)
        if card:
            await interaction.response.edit_message(view=ThemeView(card, back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Строка", emoji="➕", style=discord.ButtonStyle.primary)
    async def row(self, interaction, button):
        card = await self._card(interaction)
        if card:
            await interaction.response.send_modal(RowModal(card))

    @discord.ui.button(label="Убрать строку", emoji="➖", style=discord.ButtonStyle.secondary)
    async def remove_row(self, interaction, button):
        card = await self._card(interaction)
        if not card:
            return
        if not card["data"].get("rows"):
            await reply(interaction, "cards.table.empty")
            return
        view = PanelView(back_target=(core.screen(interaction), self), timeout=600)
        select = core.PagedSelect(placeholder=t("cards.editor.pick_row")[:150], options=[
            discord.SelectOption(label=(f"{n}. " + " | ".join(str(cell) for cell in row if cell != ""))[:100],
                                 value=str(n - 1)) for n, row in enumerate(card["data"]["rows"], 1)])

        async def picked(i):
            fresh = await self._card(i)
            index = int(select.values[0])
            if fresh and index < len(fresh["data"]["rows"]):
                fresh["data"]["rows"].pop(index)
                await apply_change(i, fresh)

        select.on_pick = picked
        view.add_item(select)
        await interaction.response.edit_message(view=view)

    @discord.ui.button(label="Столбцы", emoji="📋", style=discord.ButtonStyle.secondary)
    async def columns(self, interaction, button):
        card = await self._card(interaction)
        if card:
            await interaction.response.send_modal(ColumnsModal(card))

    @discord.ui.button(label="В библиотеку", emoji="📚", style=discord.ButtonStyle.secondary)
    async def save_to_library(self, interaction, button):
        card = await self._card(interaction)
        if not card:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        import image_library
        png = await render_card(card, interaction.guild)
        item_id, problem = await image_library.save_image(interaction.guild.id, interaction.user.id,
                                                          card["title"] or template_name(card["template"]),
                                                          "card", png)
        await interaction.followup.send(t(problem) if problem else t("library_images.saved", id=item_id),
                                        ephemeral=True)

    @discord.ui.button(label="Своя команда", emoji="⏯️", style=discord.ButtonStyle.secondary)
    async def command(self, interaction, button):
        card = await self._card(interaction, manage=True)
        if card:
            import card_commands
            await interaction.response.send_modal(card_commands.CommandModal(card))

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
            message = await channel.send(**await card_message(fresh, i.guild))
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
        if card.get("command"):
            import card_commands
            await card_commands.unregister(interaction.client, card)
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


class _CardModal(Modal):
    """Общее: проверка доступа и сохранение с перерисовкой."""

    def __init__(self, card, title_key):
        super().__init__(title=t(title_key)[:45])
        self.card_id = card["id"]

    def field(self, key, default="", style=discord.TextStyle.short, max_length=100, required=True):
        item = discord.ui.TextInput(label=t(key)[:45], default=(str(default) if default not in (None, "") else None),
                                    style=style, max_length=max_length, required=required)
        if core.has_text(f"{key}.placeholder"):
            item.placeholder = t(f"{key}.placeholder")[:100]
        self.add_item(item)
        return item

    async def fresh(self, interaction):
        card = get_card(self.card_id)
        if not can_edit(interaction, card):
            await reply(interaction, "cards.denied" if card else "cards.gone")
            return None
        return card


class ValueModal(_CardModal):
    def __init__(self, card_id):
        card = get_card(card_id)
        super().__init__(card, "cards.value_modal.title")
        hint = "cards.value_modal.progress" if card["template"] == "progress" else "cards.value_modal.counter"
        self.number = self.field(hint, max_length=30)

    async def on_submit(self, interaction):
        import card_templates as ct
        card = await self.fresh(interaction)
        if not card:
            return
        if card["template"] == "progress":
            problem = ct.set_progress(card["data"], self.number.value)
        else:
            problem = ct.change_number(card["data"], "value", self.number.value)
        await apply_change(interaction, card, problem)


class FieldsModal(_CardModal):
    """Все поля шаблона (до 5) одной формой; у текста — большое поле."""

    def __init__(self, card):
        import card_templates as ct
        super().__init__(card, "cards.fields_modal.title")
        self.template = card["template"]
        self.inputs = {}
        for key, label, kind in ct.spec(self.template).get("fields", [])[:5]:
            value = card["data"].get(key)
            value = ct.number_text(value).replace(" ", "") if kind == ct.N else value
            big = key in ("text", "reason")
            item = discord.ui.TextInput(label=label[:45], default=str(value) if value not in (None, "") else None,
                                        style=discord.TextStyle.paragraph if big else discord.TextStyle.short,
                                        max_length=ct.MAX_TEXT if big else 120, required=False)
            self.add_item(item)
            self.inputs[key] = item

    async def on_submit(self, interaction):
        import card_templates as ct
        card = await self.fresh(interaction)
        if not card:
            return
        problem = None
        for key, item in self.inputs.items():
            kind = next(k for f, _, k in ct.spec(self.template)["fields"] if f == key)
            raw = item.value.strip()
            if kind == ct.N and not raw:
                raw = "0"
            problem = ct.set_field(self.template, card["data"], key, raw) or problem
        await apply_change(interaction, card, problem)


class RowModal(_CardModal):
    def __init__(self, card):
        import card_templates as ct
        super().__init__(card, "cards.row_modal.title")
        self.template = card["template"]
        self.who = self.field("cards.row_modal.who", max_length=60, required=False)
        self.cells = self.field("cards.row_modal.cells", max_length=300)
        self.cells.placeholder = " | ".join(name.split(" (")[0]
                                            for name, _ in ct.columns_of(self.template, card["data"]))[:100]

    async def on_submit(self, interaction):
        import card_templates as ct
        card = await self.fresh(interaction)
        if card:
            await apply_change(interaction, card, ct.set_row(self.template, card["data"], self.who.value,
                                                             self.cells.value))


# ============================================================
# ГАЛЕРЕИ ВЫБОРА: 4 примера на коллаже под номерами, кнопки 1–4, листание
# ============================================================

GALLERY_SIZE = 4
NUMBERS = ("1️⃣", "2️⃣", "3️⃣", "4️⃣")
_gallery_cache = {}


def gallery_png(tiles):
    """[(подпись, PNG)] до 4 -> коллаж 2×2 с номерами-дудлами в углах. -> PNG."""
    import banner_render
    width, gap, top = 580, 24, 46
    images = []
    for label, png in tiles:
        tile = Image.open(io.BytesIO(png)).convert("RGB")
        tile.thumbnail((width, 2000))
        images.append((label, tile))
    rows = [images[i:i + 2] for i in range(0, len(images), 2)]
    heights = [max(tile.height for _, tile in row) + top for row in rows]
    sheet = Image.new("RGB", (2 * width + 3 * gap, sum(heights) + gap * (len(rows) + 1)), (43, 45, 49))
    draw = ImageDraw.Draw(sheet)
    font = banner_render._font("fallback:Inter-SemiBold.otf", 24)
    badges = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel_art", "badges")
    y, n = gap, 0
    for row, height in zip(rows, heights):
        for col, (label, tile) in enumerate(row):
            n += 1
            x = gap + col * (width + gap)
            draw.text((x + 64, y + 30), label, font=font, fill=(235, 235, 235), anchor="ls")
            sheet.paste(tile, (x, y + top))
            badge_path = os.path.join(badges, f"num{n}.png")
            if os.path.exists(badge_path):
                badge = Image.open(badge_path).convert("RGBA").resize((58, 58), Image.LANCZOS)
                sheet.paste(badge, (x, y - 4), badge)
        y += height + gap
    output = io.BytesIO()
    sheet.save(output, "PNG", optimize=True)
    return output.getvalue()


def _type_preview(key):
    import card_templates as ct
    if key == "rating":
        return render_rating("Рейтинг клана", [{"name": "Аня", "score": 120}, {"name": "Кот", "score": 95},
                                              {"name": "Пёс", "score": 60}])
    return ct.sample(key)


def type_uses(key):
    import card_templates as ct
    if key == "rating":
        return t("cards.template.rating.uses")
    return ", ".join(purpose for purpose, _, _ in ct.USES.get(key, []))


class GalleryView(PanelView):
    """
    Выбор из длинного списка картинками: коллаж из 4 примеров под номерами,
    кнопки 1–4 — выбрать, ◀ ▶ — листать. Подклассы: items(), tile(key),
    label(key), line(key), pick(interaction, key), screen().
    """

    clear_files_on_back = True
    filename = "gallery.png"

    def __init__(self, page=0, back_target=None):
        super().__init__(back_target=back_target, timeout=600)
        keys = self.items()
        self.pages = max(1, -(-len(keys) // GALLERY_SIZE))
        self.page = page % self.pages
        self.keys = keys[self.page * GALLERY_SIZE:(self.page + 1) * GALLERY_SIZE]
        for n, key in enumerate(self.keys):
            button = discord.ui.Button(label=self.label(key)[:80], emoji=NUMBERS[n],
                                       style=discord.ButtonStyle.primary, row=0)
            button.callback = lambda i, key=key: self.pick(i, key)
            self.add_item(button)
        if self.pages > 1:
            for step, emoji in ((-1, "◀️"), (1, "▶️")):
                nav = self._nav(discord.ui.Button(emoji=emoji, style=discord.ButtonStyle.secondary, row=1))
                nav.callback = lambda i, step=step: self.turn(i, step)
                self.add_item(nav)
            counter = self._nav(discord.ui.Button(label=t("nav_page", page=self.page + 1, pages=self.pages),
                                                  style=discord.ButtonStyle.secondary, disabled=True, row=1))
            self.add_item(counter)

    def items(self):
        return []

    def tile(self, key):
        return b""

    def label(self, key):
        return key

    def line(self, key):
        return self.label(key)

    def screen(self, interaction):
        return panel_embed(interaction, "cards.gallery", hints=False)

    async def pick(self, interaction, key):
        raise NotImplementedError

    def copy(self, page):
        return type(self)(page, back_target=self.back_target)

    async def turn(self, interaction, step):
        await self.copy(self.page + step).show(interaction)

    def build(self):
        cache_key = (type(self).__name__, getattr(self, "template", None), tuple(self.keys))
        if cache_key not in _gallery_cache:
            if len(_gallery_cache) > 64:
                _gallery_cache.clear()
            _gallery_cache[cache_key] = gallery_png([(self.label(key), self.tile(key)) for key in self.keys])
        return _gallery_cache[cache_key]

    async def show(self, interaction):
        """Перерисовать сообщение этой страницей галереи (с коллажем вложением)."""
        import asyncio
        if not interaction.response.is_done():
            await interaction.response.defer()
        png = await asyncio.to_thread(self.build)
        embed = self.screen(interaction)
        lines = "\n".join(f"**{n}.** {self.line(key)}" for n, key in enumerate(self.keys, 1))
        embed.description = f"{embed.description}\n\n{lines}"[:4096]
        embed.set_image(url=f"attachment://{self.filename}")
        await interaction.edit_original_response(
            embed=embed, view=self, attachments=[discord.File(io.BytesIO(png), filename=self.filename)])


class TypeGalleryView(GalleryView):
    """Новая картинка, шаг 1: тип — 4 примера на странице."""

    filename = "types.png"

    def items(self):
        import card_templates as ct
        return list(ct.TEMPLATES)

    def tile(self, key):
        return _type_preview(key)

    def label(self, key):
        return template_name(key)

    def line(self, key):
        return t("cards.gallery.type_line", name=template_name(key), about=template_about(key),
                 uses=type_uses(key))

    def screen(self, interaction):
        return panel_embed(interaction, "cards.gallery_types", hints=False, page=self.page + 1, pages=self.pages)

    async def pick(self, interaction, key):
        if key == "rating":  # у рейтинга пока один вид
            await interaction.response.send_modal(TitleModal(None, template_name(key), create=True, template=key))
            return
        await StyleGalleryView(key, on_pick=None, back_page=self.page, back_target=self.back_target).show(interaction)


class StyleGalleryView(GalleryView):
    """Стиль картинки — 4 стиля этого типа на странице. on_pick(interaction, style) — для смены стиля."""

    filename = "styles.png"

    def __init__(self, template, on_pick=None, page=0, back_page=None, back_target=None, theme="cream"):
        self.template, self.on_pick, self.back_page, self.theme = template, on_pick, back_page, theme
        super().__init__(page, back_target=None if back_page is not None else back_target)
        self.outer_back = back_target
        if back_page is not None:  # к галерее типов (она тоже с картинкой — перерисовываем)
            back = self._nav(discord.ui.Button(label=t("cards.gallery.to_types")[:80], emoji="◀️",
                                               style=discord.ButtonStyle.secondary, row=4))

            async def to_types(i):
                await TypeGalleryView(self.back_page, back_target=self.outer_back).show(i)

            back.callback = to_types
            self.add_item(back)

    def copy(self, page):
        view = StyleGalleryView(self.template, self.on_pick, page, self.back_page, self.outer_back, self.theme)
        return view

    def items(self):
        import card_templates as ct
        return list(ct.STYLES)

    def tile(self, key):
        import card_templates as ct
        return ct.sample(self.template, self.theme, key)

    def label(self, key):
        import card_templates as ct
        return ct.STYLES[key]

    def screen(self, interaction):
        return panel_embed(interaction, "cards.gallery_styles", hints=False, kind=template_name(self.template),
                           page=self.page + 1, pages=self.pages)

    def build(self):
        cache_key = ("styles", self.template, self.theme, tuple(self.keys))
        if cache_key not in _gallery_cache:
            if len(_gallery_cache) > 64:
                _gallery_cache.clear()
            _gallery_cache[cache_key] = gallery_png([(self.label(key), self.tile(key)) for key in self.keys])
        return _gallery_cache[cache_key]

    async def pick(self, interaction, key):
        if self.on_pick is not None:
            await self.on_pick(interaction, key)
            return
        await interaction.response.send_modal(TitleModal(None, template_name(self.template), create=True,
                                                         template=self.template, style=key))


class StylePickView(PanelView):
    """Создание картинки, шаг 2: стиль — макет из Figma или классика (с примером каждого)."""

    def __init__(self, template, back_target):
        import card_templates as ct
        super().__init__(back_target=back_target, timeout=600)
        self.template = template
        select = discord.ui.Select(placeholder=t("cards.style_pick.placeholder")[:150], options=[
            discord.SelectOption(label=name[:100], value=key, default=key == ct.DEFAULT_STYLE)
            for key, name in ct.STYLES.items()])

        async def picked(i):
            await i.response.send_modal(TitleModal(None, template_name(self.template), create=True,
                                                   template=self.template, style=select.values[0]))

        select.callback = picked
        self.add_item(select)

    @discord.ui.button(label="Показать все стили", emoji="👁️", style=discord.ButtonStyle.secondary)
    async def show_styles(self, interaction, button):
        import asyncio
        await interaction.response.defer(ephemeral=True, thinking=True)
        png = await asyncio.to_thread(styles_sheet, self.template)
        await interaction.followup.send(t("cards.style_pick.examples"), ephemeral=True,
                                        file=discord.File(io.BytesIO(png), filename="styles.png"))


def styles_sheet(template, theme="cream"):
    """Один шаблон во всех стилях — сетка 4 в ряд с подписями. -> PNG."""
    import banner_render
    import card_templates as ct
    tiles = []
    for n, (key, name) in enumerate(ct.STYLES.items(), 1):
        tile = Image.open(io.BytesIO(ct.sample(template, theme, key))).convert("RGB")
        tile.thumbnail((400, 1200))
        tiles.append((f"{n}. {name}", tile))
    label_font = banner_render._font("fallback:Inter-SemiBold.otf", 18)
    cols, width, gap = 4, 400, 12
    rows = [tiles[i:i + cols] for i in range(0, len(tiles), cols)]
    height = sum(max(tile.height for _, tile in row) + 30 + gap for row in rows) + gap
    sheet = Image.new("RGB", (cols * (width + gap) + gap, height), (43, 45, 49))
    draw = ImageDraw.Draw(sheet)
    y = gap
    for row in rows:
        for i, (label, tile) in enumerate(row):
            x = gap + i * (width + gap)
            draw.text((x, y + 20), label, font=label_font, fill=(230, 230, 230), anchor="ls")
            sheet.paste(tile, (x, y + 28))
        y += max(tile.height for _, tile in row) + 30 + gap
    output = io.BytesIO()
    sheet.save(output, "PNG", optimize=True)
    return output.getvalue()


class ThemeView(PanelView):
    """Тема картинки (цвета), стиль (макет / классика) и «!» справа."""

    def __init__(self, card, back_target):
        import card_templates as ct
        super().__init__(back_target=back_target, timeout=600)
        self.card_id = card["id"]
        select = discord.ui.Select(placeholder=t("cards.theme.pick")[:150], options=[
            discord.SelectOption(label=name, value=key, default=key == card["data"].get("theme"))
            for key, name in ct.THEME_NAMES.items()])

        async def picked(i):
            fresh = get_card(self.card_id)
            if not can_edit(i, fresh):
                await reply(i, "cards.denied")
                return
            fresh["data"]["theme"] = select.values[0]
            await apply_change(i, fresh)

        select.callback = picked
        self.add_item(select)
        style = discord.ui.Select(placeholder=t("cards.style_pick.placeholder")[:150], options=[
            discord.SelectOption(label=name[:100], value=key,
                                 default=key == (card["data"].get("style") or ct.DEFAULT_STYLE))
            for key, name in ct.STYLES.items()])

        async def picked_style(i):
            fresh = get_card(self.card_id)
            if not can_edit(i, fresh):
                await reply(i, "cards.denied")
                return
            fresh["data"]["style"] = style.values[0]
            await apply_change(i, fresh)

        style.callback = picked_style
        self.add_item(style)
        gallery = discord.ui.Button(label=t("cards.theme.styles_gallery")[:80], emoji="🖼️",
                                    style=discord.ButtonStyle.secondary)

        async def open_gallery(i):
            fresh = get_card(self.card_id)
            if not can_edit(i, fresh):
                await reply(i, "cards.denied")
                return

            async def chosen(ci, key):
                card = get_card(self.card_id)
                if not can_edit(ci, card):
                    await reply(ci, "cards.denied")
                    return
                card["data"]["style"] = key
                await apply_change(ci, card)

            await StyleGalleryView(fresh["template"], on_pick=chosen, theme=fresh["data"].get("theme") or "cream",
                                   back_target=(core.screen(i), self)).show(i)

        gallery.callback = open_gallery
        self.add_item(gallery)
        bang = discord.ui.Button(label=t("cards.theme.bang_off" if card["data"].get("bang", True)
                                         else "cards.theme.bang_on")[:80], style=discord.ButtonStyle.secondary,
                                 emoji="❗")

        async def toggle(i):
            fresh = get_card(self.card_id)
            if not can_edit(i, fresh):
                await reply(i, "cards.denied")
                return
            fresh["data"]["bang"] = not fresh["data"].get("bang", True)
            await apply_change(i, fresh)

        bang.callback = toggle
        self.add_item(bang)


class ColumnsModal(_CardModal):
    def __init__(self, card):
        super().__init__(card, "cards.columns_modal.title")
        self.names = self.field("cards.columns_modal.names", " | ".join(card["data"]["columns"]), max_length=200)

    async def on_submit(self, interaction):
        import card_templates as ct
        card = await self.fresh(interaction)
        if card:
            await apply_change(interaction, card, ct.set_columns(card["data"], self.names.value))


class TitleModal(Modal, title="НАЗВАНИЕ"):
    texts = "cards.title_modal"
    name = discord.ui.TextInput(label="Название картинки", max_length=60)

    def __init__(self, card_id, current="", create=False, template="rating", style=None):
        super().__init__()
        self.card_id = card_id
        self.create = create
        self.template = template
        self.style = style
        self.name.default = current

    async def on_submit(self, interaction):
        title = self.name.value.strip()
        if self.create:
            card = get_card(create_card(interaction.guild.id, interaction.user.id, title, self.template))
            if self.style:
                card["data"]["style"] = self.style
                save_card(card)
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
    lines = [t("cards.home.line", title=card["title"], kind=template_name(card["template"]),
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

    @discord.ui.button(label="Новая картинка", emoji="➕", style=discord.ButtonStyle.success)
    async def create_card(self, interaction, button):
        # тип — галереей: 4 примера на коллаже, кнопки 1–4, листание
        await TypeGalleryView(0, back_target=(core.screen(interaction), self)).show(interaction)


DYNAMIC_ITEMS = [CardEditButton]
