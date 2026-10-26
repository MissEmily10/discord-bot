"""
Баннер сервера с живыми цифрами: бот сам ставит картинку-баннер сервера и
вписывает в неё строки с переменными — {member_count}, {voice}, {online},
{boosts}, {date}, {counter:имя}… Что выводить, каждый сервер выбирает сам.

/interactive → «Баннер сервера»: строки (текст, место, размер, шрифт, цвет),
свой фон (лучше 960×540), предпросмотр, «Поставить сейчас» и автообновление
раз в N минут (не чаще 10: Discord не любит частых правок сервера).

Ограничения Discord: баннер сервера есть только со 2-го уровня буста,
боту нужно право «Управлять сервером».
"""

import asyncio
import hashlib
import io
import json
import logging
import re
import time

import discord
from PIL import Image, ImageDraw

import core
from core import PanelView, Modal, t, panel_embed
from actions import reply, substitute, VARIABLE
from database import get_connection

_log = logging.getLogger(__name__)

SIZE = (960, 540)            # рекомендуемый размер баннера сервера
MAX_LINES = 6
MIN_INTERVAL, MAX_INTERVAL = 10, 1440  # минуты
CHECK_EVERY = 60             # как часто фоновая задача смотрит, кому пора
CREAM, INK = (241, 238, 236), (32, 28, 30)

POSITIONS = ("tl", "tc", "tr", "ml", "mc", "mr", "bl", "bc", "br")
SIZES = {"S": 0.065, "M": 0.095, "L": 0.15, "XL": 0.22}  # доля высоты картинки
FONTS = ("coolvetica", "dunkin", "drunk")
DEFAULT_LINES = [
    {"text": "{server}", "pos": "bl", "size": "L", "font": "coolvetica", "color": "#201C1E"},
    {"text": "участников: {member_count} · в войсе: {voice}", "pos": "bl", "size": "S", "font": "dunkin",
     "color": "#5A5254"},
]


# ============================================================
# ХРАНИЛИЩЕ
# ============================================================

def ensure_table():
    connection = get_connection()
    connection.execute("""CREATE TABLE IF NOT EXISTS server_banners (
        guild_id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, base TEXT NOT NULL DEFAULT '',
        lines_json TEXT NOT NULL DEFAULT '[]', auto INTEGER NOT NULL DEFAULT 0,
        interval_min INTEGER NOT NULL DEFAULT 10, last_hash TEXT NOT NULL DEFAULT '',
        last_applied INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT ''
    )""")
    if "scene_json" not in {row[1] for row in connection.execute("PRAGMA table_info(server_banners)")}:
        # холст из редактора на сайте (scene.py); пусто — обычный баннер: фон + строки
        connection.execute("ALTER TABLE server_banners ADD COLUMN scene_json TEXT NOT NULL DEFAULT ''")
    connection.commit()
    connection.close()


_COLUMNS = ("guild_id", "owner_id", "base", "lines", "auto", "interval_min", "last_hash", "last_applied",
            "last_error")


def get_banner(guild_id):
    ensure_table()
    connection = get_connection()
    row = connection.execute("SELECT guild_id, owner_id, base, lines_json, auto, interval_min, last_hash, "
                             "last_applied, last_error, scene_json FROM server_banners WHERE guild_id = ?",
                             (guild_id,)).fetchone()
    connection.close()
    if row is None:
        return None
    banner = dict(zip(_COLUMNS + ("scene",), row))
    try:
        banner["scene"] = json.loads(banner["scene"]) if banner["scene"] else None
    except ValueError:
        banner["scene"] = None
    try:
        banner["lines"] = [clean_line(line) for line in json.loads(banner["lines"])][:MAX_LINES]
    except (ValueError, TypeError):
        banner["lines"] = []
    return banner


def get_or_create(guild_id, owner_id):
    banner = get_banner(guild_id)
    if banner is None:
        connection = get_connection()
        connection.execute("INSERT INTO server_banners (guild_id, owner_id, lines_json) VALUES (?, ?, ?)",
                           (guild_id, owner_id, json.dumps(DEFAULT_LINES, ensure_ascii=False)))
        connection.commit()
        connection.close()
        banner = get_banner(guild_id)
    return banner


def save(banner):
    connection = get_connection()
    connection.execute(
        "UPDATE server_banners SET base = ?, lines_json = ?, auto = ?, interval_min = ?, last_hash = ?, "
        "last_applied = ?, last_error = ?, scene_json = ? WHERE guild_id = ?",
        (banner["base"], json.dumps(banner["lines"][:MAX_LINES], ensure_ascii=False), int(banner["auto"]),
         int(banner["interval_min"]), banner["last_hash"], int(banner["last_applied"]), banner["last_error"],
         json.dumps(banner["scene"], ensure_ascii=False) if banner.get("scene") else "", banner["guild_id"]))
    connection.commit()
    connection.close()


def auto_banners():
    ensure_table()
    connection = get_connection()
    rows = connection.execute("SELECT guild_id FROM server_banners WHERE auto = 1").fetchall()
    connection.close()
    return [row[0] for row in rows]


_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def parse_color(text, default="#201C1E"):
    match = _HEX.match((text or "").strip())
    return f"#{match[1].upper()}" if match else default


def clean_line(line):
    line = line if isinstance(line, dict) else {}
    return {
        "text": str(line.get("text") or "")[:200],
        "pos": line.get("pos") if line.get("pos") in POSITIONS else "bl",
        "size": line.get("size") if line.get("size") in SIZES else "M",
        "font": line.get("font") if line.get("font") in FONTS else "coolvetica",
        "color": parse_color(line.get("color")),
    }


# ============================================================
# КАРТИНКА
# ============================================================

def base_image(banner):
    """Свой фон (загружен модалкой), готовый стиль (style:<ключ>) или сливочный фон."""
    if (banner.get("base") or "").startswith("style:"):
        import banner_styles
        return banner_styles.render_base(banner["base"][6:])
    if banner.get("base"):
        import web_panel
        path = web_panel.ASSETS_DIR / banner["base"]
        if path.is_file():
            try:
                image = Image.open(path).convert("RGBA")
                # под пропорции баннера: обрезаем по центру и приводим к SIZE
                ratio = SIZE[0] / SIZE[1]
                width, height = image.size
                if width / height > ratio:
                    new_width = int(height * ratio)
                    image = image.crop(((width - new_width) // 2, 0, (width + new_width) // 2, height))
                else:
                    new_height = int(width / ratio)
                    image = image.crop((0, (height - new_height) // 2, width, (height + new_height) // 2))
                return image.resize(SIZE, Image.LANCZOS)
            except Exception as error:  # битый файл — рисуем на стандартном фоне
                _log.warning("server banner base %s: %s", path, error)
    return Image.new("RGBA", SIZE, CREAM + (255,))


def restore_lines(banner):
    """Фон больше не живая картинка, а строк нет (раньше их стирала установка картинки) — стандартные строки."""
    if not banner.get("lines") and not (banner.get("base") or "").startswith("card:"):
        banner["lines"] = [dict(line) for line in DEFAULT_LINES]


def render(banner, texts, picture=None):
    """texts — строки уже с подставленными значениями; picture — готовый фон (живая картинка). -> PNG."""
    import banner_render
    picture = picture.copy() if picture is not None else base_image(banner)
    draw = ImageDraw.Draw(picture)
    width, height = picture.size
    margin = int(width * 0.06)
    groups = {}
    for line, text in zip(banner["lines"], texts):
        if text.strip():
            groups.setdefault(line["pos"], []).append((line, text))
    for pos, items in groups.items():
        placed = []
        for line, text in items:
            kind = banner_render.kind_for(line["font"], text)
            size = max(10, int(height * SIZES[line["size"]]))
            font = banner_render._font(kind, size)
            while font.getlength(text) > width - 2 * margin and size > 12:
                size = int(size * 0.92)
                font = banner_render._font(kind, size)
            placed.append((line, text, font))
        block = sum(int(font.size * 1.12) for _, _, font in placed)
        y = {"t": margin, "m": (height - block) // 2, "b": height - margin - block}[pos[0]]
        x, anchor = {"l": (margin, "la"), "c": (width // 2, "ma"), "r": (width - margin, "ra")}[pos[1]]
        for line, text, font in placed:
            draw.text((x, y), text, font=font, fill=line["color"], anchor=anchor)
            y += int(font.size * 1.12)
    output = io.BytesIO()
    picture.convert("RGB").save(output, "PNG", optimize=True)
    return output.getvalue()


async def line_texts(guild, banner):
    """Строки баннера с подставленными значениями: [{text, size, pos, font, color}]."""
    import live
    needed = {(m[1], m[2]) for line in banner["lines"] for m in VARIABLE.finditer(line["text"])}
    variables = await live.collect(guild, needed) if needed else {}
    return [dict(line, text=substitute(line["text"], variables)) for line in banner["lines"]]


async def render_for(guild, banner):
    if banner.get("scene"):  # собран в редакторе на сайте — холст целиком
        import scene
        return await scene.render_live(banner["scene"], guild, size=SIZE)
    lines = await line_texts(guild, banner)
    texts = [line["text"] for line in lines]
    picture = None
    if (banner.get("base") or "").startswith("card:"):
        # баннер = живая картинка: перерисовываем её со свежими данными сразу в формате баннера,
        # строки — не поверх картинки, а под ней, в общем макете
        import cards
        card = cards.get_card(int(banner["base"][5:] or 0))
        if card is not None and card["guild_id"] == guild.id:
            png = await cards.render_banner(card, guild, lines)
            picture = Image.open(io.BytesIO(png)).convert("RGBA").resize(SIZE, Image.LANCZOS)
            texts = ["" for _ in texts]
    return await asyncio.to_thread(render, banner, texts, picture)


# ============================================================
# УСТАНОВКА НА СЕРВЕР
# ============================================================

def problem(guild):
    """Почему баннер нельзя поставить: ключ текста или None."""
    if "BANNER" not in guild.features:
        return "server_banner.need_boost"
    if not guild.me.guild_permissions.manage_guild:
        return "server_banner.no_rights"
    return None


def available(guild):
    """Есть ли у бота право «Управлять сервером». Нет — опции баннера на этом сервере не видно."""
    me = getattr(guild, "me", None)
    return bool(me and me.guild_permissions.manage_guild)


async def apply(guild, banner, force=False):
    """Поставить баннер. -> (поставлен ли, ключ ошибки | None). Тот же кадр не шлём повторно."""
    key = problem(guild)
    if key is None:
        data = await render_for(guild, banner)
        digest = hashlib.sha256(data).hexdigest()[:20]
        if digest == banner["last_hash"] and not force:
            banner["last_applied"] = int(time.time())
            save(banner)
            return False, None
        try:
            await guild.edit(banner=data, reason="Purr: баннер сервера")
            banner.update(last_hash=digest, last_applied=int(time.time()), last_error="")
            save(banner)
            return True, None
        except discord.Forbidden:
            key = "server_banner.no_rights"
        except discord.HTTPException as error:
            _log.warning("server banner %s: %s", guild.id, error)
            key = "server_banner.http_error"
    banner.update(last_applied=int(time.time()), last_error=key)
    save(banner)
    return False, key


async def _loop(bot):
    await bot.wait_until_ready()
    while not bot.is_closed():
        for guild_id in auto_banners():
            guild = bot.get_guild(guild_id)
            banner = get_banner(guild_id)
            if guild is None or banner is None or not available(guild):
                continue  # право сняли — баннер просто стоит на паузе
            if time.time() - banner["last_applied"] < max(MIN_INTERVAL, banner["interval_min"]) * 60:
                continue
            try:
                await apply(guild, banner)
            except Exception:  # одна поломка не должна останавливать остальные сервера
                _log.exception("server banner loop %s", guild_id)
        await asyncio.sleep(CHECK_EVERY)


def start(bot):
    ensure_table()
    return asyncio.create_task(_loop(bot))


# ============================================================
# ПАНЕЛЬ: /interactive → Баннер сервера
# ============================================================

def can_manage(interaction):
    from extended_modules import is_admin
    return interaction.guild is not None and is_admin(interaction)


def line_label(line):
    return t("server_banner.line", text=line["text"] or "—", pos=t(f"server_banner.pos.{line['pos']}"),
             size=line["size"])


def _background_name(banner):
    if banner.get("scene"):
        return t("server_banner.base_scene", layers=len(banner["scene"].get("layers") or []))
    base = banner["base"] or ""
    if base.startswith("card:"):
        import cards
        card = cards.get_card(int(base[5:] or 0))
        return t("server_banner.base_card", name=card["title"] if card else "—")
    if base.startswith("style:"):
        import banner_styles
        return t("server_banner.base_style", name=banner_styles.STYLES.get(base[6:], ("—",))[0])
    return t("server_banner.base_own") if base else t("server_banner.base_default")


def home_embed(interaction, banner):
    guild = interaction.guild
    key = problem(guild)
    status = t(key) if key else t("server_banner.ready")
    lines = "\n".join(f"{n}. {line_label(line)}" for n, line in enumerate(banner["lines"], 1))
    if banner.get("scene"):
        lines = t("server_banner.scene_lines")
    auto = (t("server_banner.auto_on", minutes=max(MIN_INTERVAL, banner["interval_min"]))
            if banner["auto"] else t("server_banner.auto_off"))
    last = (t("server_banner.last", when=f"<t:{banner['last_applied']}:R>") if banner["last_applied"] else "")
    if banner["last_error"]:
        last += "\n⚠️ " + t(banner["last_error"])
    import live
    hide = ("style", "lines", "background") if banner.get("scene") else ("plain",)
    return panel_embed(interaction, "server_banner.home", hide=hide, status=status,
                       lines=lines or t("server_banner.no_lines"),
                       auto=auto, last=last, background=_background_name(banner), variables=live.VARIABLE_HELP)


async def open_home(interaction, back_target=None, edit=True):
    if not can_manage(interaction):
        await reply(interaction, "common.need_admin")
        return
    if not available(interaction.guild):  # старая панель, открытая до снятия права
        await reply(interaction, "server_banner.no_rights")
        return
    banner = get_or_create(interaction.guild.id, interaction.user.id)
    view = ServerBannerView(back_target=back_target)
    view.refresh_labels(banner)
    embed = home_embed(interaction, banner)
    if interaction.response.is_done():
        await interaction.edit_original_response(embed=embed, view=view)
    elif edit:
        await interaction.response.edit_message(embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class ServerBannerView(PanelView):
    texts = "server_banner.home"
    button_order = ("site_editor", "style", "preview", "apply_now", "lines", "auto", "interval", "background",
                    "plain", "save_to_library")

    def refresh_labels(self, banner):
        if banner.get("scene"):  # фон и строки — в редакторе на сайте, тут они ни при чём
            for item in (self.style, self.lines, self.background):
                self.remove_item(item)
        else:
            self.remove_item(self.plain)
        self.auto.label = t("server_banner.home.auto_off_button" if banner["auto"]
                            else "server_banner.home.auto")[:80]
        self.auto.style = discord.ButtonStyle.danger if banner["auto"] else discord.ButtonStyle.success

    async def _banner(self, interaction):
        if not can_manage(interaction):
            await reply(interaction, "common.need_admin")
            return None
        return get_or_create(interaction.guild.id, interaction.user.id)

    @discord.ui.button(label="Редактор на сайте", emoji="🌐", style=discord.ButtonStyle.primary)
    async def site_editor(self, interaction, button):
        banner = await self._banner(interaction)
        if banner:
            import scene_editor
            await scene_editor.send_link(interaction, "banner")

    @discord.ui.button(label="Обычный баннер", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def plain(self, interaction, button):
        banner = await self._banner(interaction)
        if not banner:
            return
        banner["scene"] = None  # фон и строки, какими были до редактора, остались в настройках
        save(banner)
        core.audit(interaction, "server_banner.scene_removed", "guild", interaction.guild.id, None)
        await open_home(interaction, back_target=self.back_target)

    @discord.ui.button(label="Стиль", emoji="🎨", style=discord.ButtonStyle.primary)
    async def style(self, interaction, button):
        banner = await self._banner(interaction)
        if banner:
            await interaction.response.edit_message(view=StylePickView(back_target=(core.screen(interaction), self),
                                                                       home_back=self.back_target))

    @discord.ui.button(label="Предпросмотр", emoji="👁️", style=discord.ButtonStyle.secondary)
    async def preview(self, interaction, button):
        banner = await self._banner(interaction)
        if not banner:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        data = await render_for(interaction.guild, banner)
        await interaction.followup.send(t("server_banner.preview"), ephemeral=True,
                                        file=discord.File(io.BytesIO(data), filename="server_banner.png"))

    @discord.ui.button(label="В библиотеку", emoji="📚", style=discord.ButtonStyle.secondary)
    async def save_to_library(self, interaction, button):
        banner = await self._banner(interaction)
        if not banner:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        import image_library
        data = await render_for(interaction.guild, banner)
        item_id, problem = await image_library.save_image(interaction.guild.id, interaction.user.id,
                                                          t("server_banner.home.title").capitalize(), "banner", data)
        await interaction.followup.send(t(problem) if problem else t("library_images.saved", id=item_id),
                                        ephemeral=True)

    @discord.ui.button(label="Поставить сейчас", emoji="📤", style=discord.ButtonStyle.primary)
    async def apply_now(self, interaction, button):
        banner = await self._banner(interaction)
        if not banner:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        done, key = await apply(interaction.guild, banner, force=True)
        core.audit(interaction, "server_banner.applied", "guild", interaction.guild.id, key or "ok")
        await interaction.followup.send(t(key) if key else t("server_banner.applied"), ephemeral=True)

    @discord.ui.button(label="Строки", emoji="📝", style=discord.ButtonStyle.secondary)
    async def lines(self, interaction, button):
        banner = await self._banner(interaction)
        if banner:
            await interaction.response.edit_message(view=LinesView(banner, back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Автообновление", emoji="🔄", style=discord.ButtonStyle.success)
    async def auto(self, interaction, button):
        banner = await self._banner(interaction)
        if not banner:
            return
        banner["auto"] = 0 if banner["auto"] else 1
        banner["last_applied"] = 0  # включили — обновить при ближайшей проверке
        save(banner)
        core.audit(interaction, "server_banner.auto", "guild", interaction.guild.id, str(banner["auto"]))
        await open_home(interaction, back_target=self.back_target)

    @discord.ui.button(label="Как часто", emoji="🕒", style=discord.ButtonStyle.secondary)
    async def interval(self, interaction, button):
        banner = await self._banner(interaction)
        if banner:
            await interaction.response.send_modal(IntervalModal(banner, self.back_target))

    @discord.ui.button(label="Фон", emoji="🖼️", style=discord.ButtonStyle.secondary)
    async def background(self, interaction, button):
        banner = await self._banner(interaction)
        if banner:
            await interaction.response.send_modal(BaseModal(banner, self.back_target))


def apply_style(banner, key):
    """Готовый стиль: фон + цвета строк (первая — заголовок, остальные — подписи)."""
    import banner_styles
    title, sub = banner_styles.colors(key)
    banner["base"] = f"style:{key}"
    restore_lines(banner)
    for n, line in enumerate(banner["lines"]):
        line["color"] = title if n == 0 else sub
    save(banner)


def styles_sheet(banner, texts):
    """Все стили одной картинкой — с текущими строками сервера."""
    import banner_styles
    from PIL import ImageDraw as Draw
    import banner_render
    tile_w, tile_h, pad = 320, 180, 10
    keys = list(banner_styles.STYLES)
    sheet = Image.new("RGB", (4 * (tile_w + pad) + pad, 5 * (tile_h + 36) + pad), (43, 45, 49))
    draw = Draw.Draw(sheet)
    font = banner_render._font("fallback:Inter-SemiBold.otf", 16)
    for n, key in enumerate(keys):
        title, sub = banner_styles.colors(key)
        trial = dict(banner, base=f"style:{key}", lines=[dict(line, color=title if i == 0 else sub)
                                                         for i, line in enumerate(banner["lines"])])
        tile = Image.open(io.BytesIO(render(trial, texts))).resize((tile_w, tile_h), Image.LANCZOS)
        x, y = pad + (n % 4) * (tile_w + pad), pad + (n // 4) * (tile_h + 36)
        sheet.paste(tile, (x, y))
        draw.text((x, y + tile_h + 6), f"{n + 1}. {banner_styles.STYLES[key][0]}", font=font, fill=(230, 230, 230))
    output = io.BytesIO()
    sheet.save(output, "PNG", optimize=True)
    return output.getvalue()


class StylePickView(PanelView):
    texts = "server_banner.styles"

    def __init__(self, back_target, home_back):
        super().__init__(back_target=back_target)
        import banner_styles
        self.home_back = home_back
        keys = list(banner_styles.STYLES)
        for row, bright in enumerate((False, True)):
            select = discord.ui.Select(
                placeholder=t("server_banner.styles.bright" if bright else "server_banner.styles.minimal")[:150],
                row=row, options=[discord.SelectOption(label=f"{keys.index(key) + 1}. {name}"[:100], value=key)
                                  for key, (name, is_bright, *_) in banner_styles.STYLES.items()
                                  if is_bright == bright])
            select.callback = self._picked(select)
            self.add_item(select)

    def _picked(self, select):
        async def callback(interaction):
            banner = get_banner(interaction.guild.id)
            if not can_manage(interaction) or not banner:
                await reply(interaction, "common.need_admin")
                return
            apply_style(banner, select.values[0])
            core.audit(interaction, "server_banner.style", "guild", interaction.guild.id, select.values[0])
            await open_home(interaction, back_target=self.home_back)
            data = await render_for(interaction.guild, banner)
            await interaction.followup.send(t("server_banner.preview"), ephemeral=True,
                                            file=discord.File(io.BytesIO(data), filename="server_banner.png"))
        return callback

    @discord.ui.button(label="Все стили картинкой", emoji="🖼️", style=discord.ButtonStyle.secondary, row=2)
    async def sheet(self, interaction, button):
        banner = get_banner(interaction.guild.id)
        if not can_manage(interaction) or not banner:
            await reply(interaction, "common.need_admin")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        import live
        needed = {(m[1], m[2]) for line in banner["lines"] for m in VARIABLE.finditer(line["text"])}
        variables = await live.collect(interaction.guild, needed) if needed else {}
        texts = [substitute(line["text"], variables) for line in banner["lines"]]
        data = await asyncio.to_thread(styles_sheet, banner, texts)
        await interaction.followup.send(t("server_banner.styles.sheet_text"), ephemeral=True,
                                        file=discord.File(io.BytesIO(data), filename="styles.png"))


class LinesView(PanelView):
    texts = "server_banner.lines"

    def __init__(self, banner, back_target):
        super().__init__(back_target=back_target)
        self.home_back = back_target[1].back_target if back_target else None
        if banner["lines"]:
            select = discord.ui.Select(placeholder=t("server_banner.lines.pick")[:150], options=[
                discord.SelectOption(label=line_label(line)[:100], value=str(n))
                for n, line in enumerate(banner["lines"])])

            async def picked(i):
                fresh = get_banner(i.guild.id)
                index = int(select.values[0])
                if not can_manage(i) or not fresh or index >= len(fresh["lines"]):
                    await reply(i, "common.need_admin")
                    return
                await i.response.send_modal(LineModal(fresh, index, self.home_back))

            select.callback = picked
            self.add_item(select)

    @discord.ui.button(label="Добавить строку", emoji="➕", style=discord.ButtonStyle.success)
    async def add_line(self, interaction, button):
        banner = get_banner(interaction.guild.id)
        if not can_manage(interaction) or not banner:
            await reply(interaction, "common.need_admin")
            return
        if len(banner["lines"]) >= MAX_LINES:
            await reply(interaction, "server_banner.too_many", max=MAX_LINES)
            return
        await interaction.response.send_modal(LineModal(banner, None, self.home_back))

    @discord.ui.button(label="Убрать последнюю", emoji="➖", style=discord.ButtonStyle.secondary)
    async def remove_line(self, interaction, button):
        banner = get_banner(interaction.guild.id)
        if not can_manage(interaction) or not banner:
            await reply(interaction, "common.need_admin")
            return
        banner["lines"] = banner["lines"][:-1]
        save(banner)
        await open_home(interaction, back_target=self.home_back)


def _options(values, current, key):
    return [discord.SelectOption(label=t(f"{key}.{value}")[:100], value=value, default=value == current)
            for value in values]


class LineModal(Modal, title="СТРОКА БАННЕРА"):
    def __init__(self, banner, index, back_target):
        super().__init__(title=t("server_banner.line_modal.title")[:45])
        self.guild_id, self.index, self.back_target = banner["guild_id"], index, back_target
        line = banner["lines"][index] if index is not None else clean_line({"size": "M", "pos": "bl"})
        self.text = discord.ui.TextInput(default=line["text"] or None, max_length=200,
                                         placeholder=t("server_banner.line_modal.text_placeholder")[:100])
        self.pos = discord.ui.Select(options=_options(POSITIONS, line["pos"], "server_banner.pos"))
        self.size = discord.ui.Select(options=_options(tuple(SIZES), line["size"], "server_banner.size"))
        self.font = discord.ui.Select(options=_options(FONTS, line["font"], "server_banner.font"))
        self.color = discord.ui.TextInput(default=line["color"], max_length=7, required=False)
        for label, component in (("text", self.text), ("pos", self.pos), ("size", self.size),
                                 ("font", self.font), ("color", self.color)):
            self.add_item(discord.ui.Label(text=t(f"server_banner.line_modal.{label}")[:45], component=component))

    async def on_submit(self, interaction):
        banner = get_banner(self.guild_id)
        if not can_manage(interaction) or not banner:
            await reply(interaction, "common.need_admin")
            return
        line = clean_line({
            "text": self.text.value.strip(), "pos": (self.pos.values or ["bl"])[0],
            "size": (self.size.values or ["M"])[0], "font": (self.font.values or ["coolvetica"])[0],
            "color": self.color.value,
        })
        if self.index is None:
            banner["lines"].append(line)
        elif self.index < len(banner["lines"]):
            banner["lines"][self.index] = line
        save(banner)
        await open_home(interaction, back_target=self.back_target)


class IntervalModal(Modal, title="КАК ЧАСТО"):
    def __init__(self, banner, back_target):
        super().__init__(title=t("server_banner.interval_modal.title")[:45])
        self.guild_id, self.back_target = banner["guild_id"], back_target
        self.minutes = discord.ui.TextInput(default=str(banner["interval_min"]), max_length=4)
        self.add_item(discord.ui.Label(text=t("server_banner.interval_modal.minutes")[:45],
                                       description=t("server_banner.interval_modal.help", min=MIN_INTERVAL)[:100],
                                       component=self.minutes))

    async def on_submit(self, interaction):
        banner = get_banner(self.guild_id)
        if not can_manage(interaction) or not banner:
            await reply(interaction, "common.need_admin")
            return
        value = self.minutes.value.strip()
        if not value.isdigit():
            await reply(interaction, "server_banner.bad_interval", min=MIN_INTERVAL, max=MAX_INTERVAL)
            return
        banner["interval_min"] = max(MIN_INTERVAL, min(MAX_INTERVAL, int(value)))
        save(banner)
        await open_home(interaction, back_target=self.back_target)


class BaseModal(Modal, title="ФОН"):
    def __init__(self, banner, back_target):
        super().__init__(title=t("server_banner.base_modal.title")[:45])
        self.guild_id, self.back_target = banner["guild_id"], back_target
        self.upload = discord.ui.FileUpload(required=False, max_values=1)
        self.add_item(discord.ui.Label(text=t("server_banner.base_modal.file")[:45],
                                       description=t("server_banner.base_modal.help")[:100], component=self.upload))

    async def on_submit(self, interaction):
        banner = get_banner(self.guild_id)
        if not can_manage(interaction) or not banner:
            await reply(interaction, "common.need_admin")
            return
        files = self.upload.values or []
        if not files:  # пусто — вернуть стандартный фон
            banner["base"] = ""
            restore_lines(banner)
            save(banner)
            await open_home(interaction, back_target=self.back_target)
            return
        await interaction.response.defer()
        import web_panel
        data = await files[0].read()
        name, error = await asyncio.to_thread(web_panel.save_asset, data)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        banner["base"] = name
        restore_lines(banner)
        save(banner)
        core.audit(interaction, "server_banner.base", "guild", interaction.guild.id, name)
        await open_home(interaction, back_target=self.back_target)
