"""
Паки форм: формы собираются в категории («Служебные роли», «Связь с
администрацией»…), а паки — в одно сообщение (Message Build) с меню выбора.

В меню у каждой формы — название, короткое описание и дудл-эмодзи. Выбрал
форму — бот показывает (видно только тебе) баннер с названием должности,
описание роли и кнопку «Подать заявку».

Название должности по-английски (для баннеров «your moderator form has been
accepted!») бот переводит сам; поправить можно в паке.
"""

import asyncio
import os
import random
import re
import time

import discord

import core
from core import PanelView, Modal, t, panel_embed
from actions import reply
from database import get_connection, get_form, get_forms

ROOT = os.path.dirname(os.path.abspath(__file__))
DOODLE_DIR = os.path.join(ROOT, "panel_art", "emoji")
MAX_PACKS_IN_MESSAGE = 4  # по меню на пак; ещё один ряд остаётся под кнопки
MAX_ITEMS = 25            # лимит Discord на варианты в меню


# ============================================================
# ХРАНИЛИЩЕ
# ============================================================

def ensure_tables():
    connection = get_connection()
    connection.execute("""CREATE TABLE IF NOT EXISTS form_packs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
        name TEXT NOT NULL, intro TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS form_pack_items (
        pack_id INTEGER NOT NULL, form_id INTEGER NOT NULL, position INTEGER NOT NULL DEFAULT 0,
        short_description TEXT NOT NULL DEFAULT '', PRIMARY KEY (pack_id, form_id)
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS form_meta (
        form_id INTEGER PRIMARY KEY, name_en TEXT, emoji TEXT
    )""")
    # надпись сверху баннера карточки: у пака и (по желанию) своя у формы в паке
    for table in ("form_packs", "form_pack_items"):
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if "tagline" not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN tagline TEXT NOT NULL DEFAULT ''")
    connection.commit()
    connection.close()


def _query(sql, params=(), one=False, write=False):
    ensure_tables()
    connection = get_connection()
    cursor = connection.execute(sql, params)
    result = cursor.lastrowid if write else (cursor.fetchone() if one else cursor.fetchall())
    if write:
        connection.commit()
    connection.close()
    return result


def get_packs(guild_id):
    return _query("SELECT id, owner_id, name, intro FROM form_packs WHERE guild_id = ? ORDER BY id", (guild_id,))


def get_pack(pack_id):
    """-> (id, guild_id, owner_id, name, intro, tagline)"""
    return _query("SELECT id, guild_id, owner_id, name, intro, tagline FROM form_packs WHERE id = ?", (pack_id,), one=True)


def save_pack(guild_id, owner_id, name, intro="", tagline=""):
    now = int(time.time())
    return _query("INSERT INTO form_packs (guild_id, owner_id, name, intro, tagline, created_at, updated_at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)", (guild_id, owner_id, name, intro, tagline, now, now), write=True)


def update_pack(pack_id, name, intro, tagline=""):
    _query("UPDATE form_packs SET name = ?, intro = ?, tagline = ?, updated_at = ? WHERE id = ?",
           (name, intro, tagline, int(time.time()), pack_id), write=True)


def delete_pack(pack_id):
    _query("DELETE FROM form_pack_items WHERE pack_id = ?", (pack_id,), write=True)
    _query("DELETE FROM form_packs WHERE id = ?", (pack_id,), write=True)


def pack_items(pack_id):
    """-> [(form_row, short_description)] — только формы, которые ещё существуют."""
    items = []
    for form_id, description in _query(
            "SELECT form_id, short_description FROM form_pack_items WHERE pack_id = ? ORDER BY position, form_id",
            (pack_id,)):
        form = get_form(form_id)
        if form:
            items.append((form, description))
    return items


def pack_item(pack_id, form_id):
    """-> (short_description, tagline) или None."""
    return _query("SELECT short_description, tagline FROM form_pack_items WHERE pack_id = ? AND form_id = ?",
                  (pack_id, form_id), one=True)


def set_item(pack_id, form_id, short_description, tagline=""):
    if pack_item(pack_id, form_id):
        _query("UPDATE form_pack_items SET short_description = ?, tagline = ? WHERE pack_id = ? AND form_id = ?",
               (short_description, tagline, pack_id, form_id), write=True)
        return
    position = len(_query("SELECT form_id FROM form_pack_items WHERE pack_id = ?", (pack_id,)))
    _query("INSERT INTO form_pack_items (pack_id, form_id, position, short_description, tagline) VALUES (?, ?, ?, ?, ?)",
           (pack_id, form_id, position, short_description, tagline), write=True)


def tagline_for(pack_id, form_id):
    """Надпись сверху баннера: своя у формы -> у пака -> общая из каталога."""
    item = pack_item(pack_id, form_id) if pack_id else None
    pack = get_pack(pack_id) if pack_id else None
    return (item and item[1]) or (pack and pack[5]) or t("packs.tagline_default")


def remove_item(pack_id, form_id):
    _query("DELETE FROM form_pack_items WHERE pack_id = ? AND form_id = ?", (pack_id, form_id), write=True)


def get_meta(form_id):
    """-> (name_en, emoji) формы; None — не задано."""
    return _query("SELECT name_en, emoji FROM form_meta WHERE form_id = ?", (form_id,), one=True) or (None, None)


def set_meta(form_id, name_en=None, emoji=None):
    old_name, old_emoji = get_meta(form_id)
    _query("INSERT OR REPLACE INTO form_meta (form_id, name_en, emoji) VALUES (?, ?, ?)",
           (form_id, name_en if name_en is not None else old_name, emoji if emoji is not None else old_emoji),
           write=True)


# ============================================================
# ДУДЛ-ЭМОДЗИ (panel_art/emoji -> хранилище эмодзи приложения)
# ============================================================

_doodles = {}  # имя -> "<:имя:id>"


async def load_doodles(bot):
    """Из setup_hook: залить недостающие дудл-эмодзи в хранилище приложения (один раз)."""
    if not os.path.isdir(DOODLE_DIR):
        return
    try:
        existing = {emoji.name: emoji for emoji in await bot.fetch_application_emojis()}
    except (discord.HTTPException, AttributeError) as error:
        core._log.warning("Дудл-эмодзи не загружены: %s", error)
        return
    for file in sorted(os.listdir(DOODLE_DIR)):
        name, ext = os.path.splitext(file)
        if ext.lower() != ".png":
            continue
        emoji = existing.get(name)
        if emoji is None:
            try:
                with open(os.path.join(DOODLE_DIR, file), "rb") as handle:
                    emoji = await bot.create_application_emoji(name=name, image=handle.read())
            except discord.HTTPException as error:
                core._log.warning("Эмодзи %s не загружен: %s", name, error)
                continue
        _doodles[name] = str(emoji)


def pick_emoji(guild_id):
    """Случайный дудл, по возможности ещё не занятый другой формой сервера."""
    if not _doodles:
        return None
    used = set()
    for form_id, *_ in get_forms(guild_id):
        used.add(get_meta(form_id)[1])
    free = [value for value in _doodles.values() if value not in used]
    return random.choice(free or list(_doodles.values()))


def form_emoji(form_id):
    emoji = get_meta(form_id)[1]
    if not emoji:
        return None
    try:
        return discord.PartialEmoji.from_str(emoji)
    except (TypeError, ValueError):
        return None


# ============================================================
# ПЕРЕВОД НАЗВАНИЯ ДОЛЖНОСТИ
# ============================================================

ROLE_WORDS = {
    "модератор": "moderator", "модер": "moderator", "младший модератор": "junior moderator",
    "старший модератор": "senior moderator", "хелпер": "helper", "помощник": "helper",
    "администратор": "administrator", "админ": "admin", "администрация": "administration",
    "куратор": "curator", "ивентер": "eventer", "ивентёр": "eventer", "ивент": "event",
    "ведущий": "host", "ведущая": "host", "дизайнер": "designer", "художник": "artist",
    "разработчик": "developer", "программист": "developer", "тех. поддержка": "tech support",
    "техподдержка": "tech support", "поддержка": "support", "саппорт": "support",
    "контент-мейкер": "content maker", "контентмейкер": "content maker", "редактор": "editor",
    "журналист": "journalist", "новости": "news", "переводчик": "translator", "стример": "streamer",
    "партнёр": "partner", "партнер": "partner", "партнёрство": "partnership", "партнерство": "partnership",
    "жалоба": "report", "жалобы": "reports", "апелляция": "appeal", "обжалование": "appeal",
    "предложение": "suggestion", "предложения": "suggestions", "вопрос": "question", "связь": "contact",
    "набор": "recruitment", "заявка": "application", "клан": "clan", "лидер": "leader",
    "трайнер": "trainer", "тренер": "coach", "наставник": "mentor", "музыкант": "musician",
    "диджей": "dj", "организатор": "organizer", "менеджер": "manager", "пиар": "pr",
    "пиар-менеджер": "pr manager", "рекрутер": "recruiter", "стафф": "staff", "персонал": "staff",
    "в": "to", "и": "and", "с": "with", "для": "for", "по": "on",
}
TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
     "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"],
))
TRANSLATE_MODEL = "Helsinki-NLP/opus-mt-ru-en"


def _has_cyrillic(text):
    return bool(re.search("[а-яё]", text, re.I))


def quick_english(name):
    """Быстро и без сети: словарь должностей, а незнакомое — транслитом."""
    text = re.sub(r"\s+", " ", (name or "").strip().lower())
    if not _has_cyrillic(text):
        return text
    if text in ROLE_WORDS:
        return ROLE_WORDS[text]
    words = []
    for word in re.findall(r"[\w.-]+", text):
        words.append(ROLE_WORDS.get(word) or "".join(TRANSLIT.get(char, char) for char in word))
    return " ".join(words)


async def to_english(name):
    """Перевод для баннеров: словарь, иначе модель перевода (если есть HF_TOKEN), иначе транслит."""
    text = re.sub(r"\s+", " ", (name or "").strip().lower())
    if not _has_cyrillic(text) or text in ROLE_WORDS:
        return quick_english(text)
    token = os.getenv("HF_TOKEN")
    if token:
        try:
            from huggingface_hub import AsyncInferenceClient
            client = AsyncInferenceClient(api_key=token, timeout=10)
            result = await asyncio.wait_for(client.translation(text, model=TRANSLATE_MODEL), 12)
            translated = (getattr(result, "translation_text", None) or "").strip().strip(".").lower()
            if translated and not _has_cyrillic(translated):
                return translated[:40]
        except Exception as error:  # сеть, лимиты, модель недоступна — не страшно
            core._log.info("Перевод «%s» не удался: %s", text, error)
    return quick_english(text)


def english_name(form):
    """Название должности по-английски для баннеров итога проверки."""
    return get_meta(form[0])[0] or quick_english(form[3])


# ============================================================
# МЕНЮ В СООБЩЕНИИ И КАРТОЧКА ФОРМЫ
# ============================================================

def pack_select(pack_id, row=None):
    """Меню пака для отправляемого сообщения; None — пак пуст или удалён."""
    pack = get_pack(pack_id)
    items = pack_items(pack_id)[:MAX_ITEMS] if pack else []
    if not items:
        return None
    options = [
        discord.SelectOption(
            label=form[3][:100], value=str(form[0]),
            description=(description or None) and description[:100],
            emoji=form_emoji(form[0]),
        )
        for form, description in items
    ]
    select = discord.ui.Select(
        custom_id=f"rb:fp:{pack_id}", placeholder=pack[3][:150], options=options, row=row,
    )
    return FormPackSelect(pack_id, select=select)


class FormPackSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"rb:fp:(?P<pid>\d+)"):
    """Постоянное меню пака: выбрал форму — видишь её карточку и кнопку «Подать заявку»."""

    def __init__(self, pack_id, *, select=None):
        super().__init__(select or discord.ui.Select(custom_id=f"rb:fp:{pack_id}",
                                                     options=[discord.SelectOption(label="-")]))
        self.pack_id = pack_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["pid"]), select=item)

    async def callback(self, interaction):
        value = (self.item.values or ["0"])[0]
        pack = get_pack(self.pack_id)
        form_id = int(value) if value.isdigit() else 0
        if not pack or interaction.guild is None or pack[1] != interaction.guild.id \
                or not pack_item(self.pack_id, form_id):
            await reply(interaction, "packs.card.gone")
            return
        await show_form_card(interaction, form_id, self.pack_id)


async def show_form_card(interaction, form_id, pack_id=None):
    """Баннер с названием должности + описание роли + «Подать заявку» (видно только тебе)."""
    from banner_render import banner_parts
    from extended_modules import FormApplyButton
    form = get_form(form_id)
    if not form or interaction.guild is None or form[1] != interaction.guild.id:
        await reply(interaction, "forms.not_found")
        return
    banner, file = banner_parts(interaction, "forms.public_card", name=form[3],
                                tagline=tagline_for(pack_id, form_id))
    card = panel_embed(interaction, "packs.card", title=form[3],
                       description=form[4] or t("forms.no_description"), hints=False)
    view = discord.ui.View(timeout=None)
    view.add_item(FormApplyButton(form_id))
    kwargs = {"embeds": [banner, card] if banner else [card], "view": view, "ephemeral": True}
    if file:
        kwargs["file"] = file
    await interaction.response.send_message(**kwargs)


# ============================================================
# ПАНЕЛИ: /interactive → Паки форм
# ============================================================

def can_manage(interaction, pack):
    from extended_modules import owns_or_admin
    return pack is not None and pack[1] == interaction.guild.id and owns_or_admin(interaction, pack[2])


def home_embed(interaction):
    packs = get_packs(interaction.guild.id)
    lines = [t("packs.home.line", name=name, count=len(pack_items(pid))) for pid, _, name, _ in packs]
    return panel_embed(interaction, "packs.home", packs="\n".join(lines) or t("packs.home.empty"))


class PacksHome(PanelView):
    texts = "packs.home"

    def __init__(self, interaction, back_target=None):
        super().__init__(back_target=back_target)
        packs = get_packs(interaction.guild.id)
        if packs:
            select = discord.ui.Select(
                placeholder=t("packs.home.pick")[:150],
                options=[discord.SelectOption(label=name[:100], value=str(pid)) for pid, _, name, _ in packs[:25]],
            )

            async def picked(i):
                await open_pack(i, int(select.values[0]), back_target=(core.screen(i), self))

            select.callback = picked
            self.add_item(select)

    @discord.ui.button(label="Создать пак", emoji="➕", style=discord.ButtonStyle.success)
    async def create(self, interaction, button):
        await interaction.response.send_modal(PackModal(None, back_target=(core.screen(interaction), self)))


def pack_embed(interaction, pack):
    lines = []
    for form, description in pack_items(pack[0]):
        lines.append(t("packs.pack.line", emoji=get_meta(form[0])[1] or "▫️", name=form[3],
                       name_en=english_name(form), description=description or t("packs.pack.no_description")))
    return panel_embed(interaction, "packs.pack", title=pack[3], intro=pack[4] or t("packs.pack.no_intro"),
                       tagline=pack[5] or t("packs.tagline_default"),
                       forms="\n".join(lines) or t("packs.pack.empty"), max=MAX_ITEMS)


async def open_pack(interaction, pack_id, back_target=None, edit=None):
    pack = get_pack(pack_id)
    if not can_manage(interaction, pack):
        await reply(interaction, "packs.denied")
        return
    view = PackView(pack_id, back_target=back_target)
    if edit is not None:
        await interaction.edit_original_response(embed=pack_embed(interaction, pack), view=view)
    else:
        await interaction.response.edit_message(embed=pack_embed(interaction, pack), view=view)


class PackView(PanelView):
    texts = "packs.pack"
    button_order = ("add_form", "build_message", "edit_form", "remove_form", "rename", "delete_pack")

    def __init__(self, pack_id, back_target=None):
        super().__init__(back_target=back_target)
        self.pack_id = pack_id

    async def _pack(self, interaction):
        pack = get_pack(self.pack_id)
        if not can_manage(interaction, pack):
            await reply(interaction, "packs.denied")
            return None
        return pack

    async def _pick_form(self, interaction, forms, on_pick):
        """Список форм под панелью пака, выбор — on_pick(i, form_row)."""
        if not forms:
            await reply(interaction, "packs.pack.no_forms")
            return
        view = PanelView(back_target=(core.screen(interaction), self), timeout=600)
        select = discord.ui.Select(
            placeholder=t("packs.pack.pick_form")[:150],
            options=[discord.SelectOption(label=form[3][:100], value=str(form[0])) for form in forms[:25]],
        )

        async def picked(i):
            form = next(form for form in forms if str(form[0]) == select.values[0])
            await on_pick(i, form)

        select.callback = picked
        view.add_item(select)
        await interaction.response.edit_message(view=view)

    @discord.ui.button(label="Добавить форму", emoji="➕", style=discord.ButtonStyle.success)
    async def add_form(self, interaction, button):
        if not await self._pack(interaction):
            return
        inside = {form[0] for form, _ in pack_items(self.pack_id)}
        forms = [get_form(fid) for fid, *_ in get_forms(interaction.guild.id) if fid not in inside]
        if len(inside) >= MAX_ITEMS:
            await reply(interaction, "packs.pack.full", max=MAX_ITEMS)
            return
        await self._pick_form(interaction, [f for f in forms if f],
                              lambda i, form: i.response.send_modal(PackItemModal(self.pack_id, form, self.back_target)))

    @discord.ui.button(label="Изменить форму", emoji="✏️", style=discord.ButtonStyle.secondary)
    async def edit_form(self, interaction, button):
        if not await self._pack(interaction):
            return
        await self._pick_form(interaction, [form for form, _ in pack_items(self.pack_id)],
                              lambda i, form: i.response.send_modal(PackItemModal(self.pack_id, form, self.back_target)))

    @discord.ui.button(label="Убрать форму", emoji="➖", style=discord.ButtonStyle.secondary)
    async def remove_form(self, interaction, button):
        if not await self._pack(interaction):
            return

        async def remove(i, form):
            remove_item(self.pack_id, form[0])
            await open_pack(i, self.pack_id, back_target=self.back_target)

        await self._pick_form(interaction, [form for form, _ in pack_items(self.pack_id)], remove)

    @discord.ui.button(label="Собрать сообщение", emoji="📨", style=discord.ButtonStyle.primary)
    async def build_message(self, interaction, button):
        pack = await self._pack(interaction)
        if not pack:
            return
        if not pack_items(self.pack_id):
            await reply(interaction, "packs.pack.empty_message")
            return
        import embed_module
        state = embed_module.EmbedState(interaction.guild.id, interaction.user.id)
        state.name = pack[3][:100]
        state.embeds = [{"title": t("packs.message.title"), "description": pack[4] or t("packs.message.text"),
                         "color": core.embed_color().value}]
        state.interactive = {"type": "form_packs", "packs": [self.pack_id]}
        await embed_module.go_to_editor(interaction, state, (core.screen(interaction), self))

    @discord.ui.button(label="Название и текст", emoji="📝", style=discord.ButtonStyle.secondary)
    async def rename(self, interaction, button):
        pack = await self._pack(interaction)
        if pack:
            await interaction.response.send_modal(PackModal(pack, back_target=self.back_target))

    @discord.ui.button(label="Удалить пак", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete_pack(self, interaction, button):
        pack = await self._pack(interaction)
        if not pack:
            return
        delete_pack(self.pack_id)
        core.audit(interaction, "form_pack.deleted", "form_pack", self.pack_id, pack[3])
        await interaction.response.edit_message(embed=home_embed(interaction),
                                                view=PacksHome(interaction, back_target=None))


class PackModal(Modal, title="ПАК ФОРМ"):
    texts = "packs.pack_modal"
    name = discord.ui.TextInput(label="Название пака", max_length=100)
    intro = discord.ui.TextInput(label="Текст сообщения", style=discord.TextStyle.paragraph,
                                 required=False, max_length=1500)
    tagline = discord.ui.TextInput(label="Надпись сверху баннера", required=False, max_length=80)

    def __init__(self, pack, back_target=None):
        super().__init__()
        self.pack = pack
        self.back_target = back_target
        if pack:
            self.name.default = pack[3]
            self.intro.default = pack[4]
            self.tagline.default = pack[5]

    async def on_submit(self, interaction):
        name, intro, tagline = self.name.value.strip(), self.intro.value.strip(), self.tagline.value.strip()
        if self.pack:
            if not can_manage(interaction, get_pack(self.pack[0])):
                await reply(interaction, "packs.denied")
                return
            update_pack(self.pack[0], name, intro, tagline)
            pack_id = self.pack[0]
        else:
            pack_id = save_pack(interaction.guild.id, interaction.user.id, name, intro, tagline)
            core.audit(interaction, "form_pack.created", "form_pack", pack_id, name)
        await open_pack(interaction, pack_id, back_target=self.back_target)


class PackItemModal(Modal, title="ФОРМА В ПАКЕ"):
    texts = "packs.item_modal"
    description = discord.ui.TextInput(label="Описание в меню", max_length=100, required=False)
    name_en = discord.ui.TextInput(label="Должность по-английски", max_length=40, required=False)
    tagline = discord.ui.TextInput(label="Своя надпись сверху баннера", max_length=80, required=False)

    def __init__(self, pack_id, form, back_target=None):
        super().__init__()
        self.pack_id = pack_id
        self.form = form
        self.back_target = back_target
        current = pack_item(pack_id, form[0])
        self.description.default = (current[0] if current else (form[4] or ""))[:100]
        self.tagline.default = current[1] if current else ""
        # пусто — переведу сам после «Отправить»; известную должность подставляем сразу
        known = re.sub(r"\s+", " ", form[3].strip().lower())
        self.name_en.default = get_meta(form[0])[0] or ROLE_WORDS.get(known) or ""

    async def on_submit(self, interaction):
        pack = get_pack(self.pack_id)
        if not can_manage(interaction, pack) or self.form[1] != interaction.guild.id:
            await reply(interaction, "packs.denied")
            return
        await interaction.response.defer()
        name_en = self.name_en.value.strip().lower() or await to_english(self.form[3])
        emoji = None if get_meta(self.form[0])[1] else pick_emoji(interaction.guild.id)
        set_meta(self.form[0], name_en=name_en, emoji=emoji)
        set_item(self.pack_id, self.form[0], self.description.value.strip(), self.tagline.value.strip())
        await open_pack(interaction, self.pack_id, back_target=self.back_target, edit=True)


# ============================================================
# В КОНСТРУКТОРЕ СООБЩЕНИЙ
# ============================================================

def packs_summary(interactive):
    names = [get_pack(pid)[3] for pid in interactive.get("packs") or [] if get_pack(pid)]
    return t("embed.interactive.summary_packs", names=", ".join(names) or "—")


def clean_interactive(guild_id, interactive):
    """Только свои паки сервера, не больше MAX_PACKS_IN_MESSAGE; None — ничего не осталось."""
    packs = []
    for raw in (interactive or {}).get("packs") or []:
        try:
            pack = get_pack(int(raw))
        except (TypeError, ValueError):
            continue
        if pack and pack[1] == guild_id and pack[0] not in packs:
            packs.append(pack[0])
    packs = packs[:MAX_PACKS_IN_MESSAGE]
    return {"type": "form_packs", "packs": packs} if packs else None


def packs_picker(interaction, state, on_done, back_target):
    """Выбор паков для меню в сообщении (до MAX_PACKS_IN_MESSAGE)."""
    packs = get_packs(interaction.guild.id)
    view = PanelView(back_target=back_target, timeout=600)
    select = discord.ui.Select(
        placeholder=t("packs.picker.placeholder")[:150], min_values=1,
        max_values=min(MAX_PACKS_IN_MESSAGE, len(packs)),
        options=[discord.SelectOption(label=name[:100], value=str(pid)) for pid, _, name, _ in packs[:25]],
    )

    async def picked(i):
        state.interactive = clean_interactive(i.guild.id, {"packs": select.values})
        await on_done(i)

    select.callback = picked
    view.add_item(select)
    return view


DYNAMIC_ITEMS = [FormPackSelect]
