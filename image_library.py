"""
Библиотека картинок: всё, что сделал бот (иконки /logo, живые картинки,
баннеры), можно сохранить и потом взять из бота — файлом, без сохранения
на телефон. Можно загрузить и свою картинку.

/library → «Картинки» или /pictures → «Библиотека картинок».
Каждый видит свои картинки и те, которыми поделились с сервером.
Файлы лежат рядом с картинками веб-панели (panel_assets), одинаковые не
дублируются.
"""

import asyncio
import io
import os
import time

import discord

import core
from core import PanelView, Modal, t, panel_embed
from actions import reply
from database import get_connection

MAX_PER_USER = 200
KINDS = ("logo", "card", "banner", "pack", "upload")


def ensure_table():
    connection = get_connection()
    connection.execute("""CREATE TABLE IF NOT EXISTS image_library (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
        title TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'upload', file TEXT NOT NULL,
        shared INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL
    )""")
    connection.commit()
    connection.close()


COLUMNS = ("id", "guild_id", "owner_id", "title", "kind", "file", "shared", "created_at")


def _query(sql, params=(), one=False, write=False):
    ensure_table()
    connection = get_connection()
    cursor = connection.execute(sql, params)
    result = cursor.lastrowid if write else (cursor.fetchone() if one else cursor.fetchall())
    if write:
        connection.commit()
    connection.close()
    return result


def get_image(image_id):
    row = _query(f"SELECT {', '.join(COLUMNS)} FROM image_library WHERE id = ?", (image_id,), one=True)
    return dict(zip(COLUMNS, row)) if row else None


def visible_images(guild_id, user_id):
    rows = _query(f"SELECT {', '.join(COLUMNS)} FROM image_library WHERE guild_id = ? AND (owner_id = ? OR shared = 1) "
                  "ORDER BY id DESC", (guild_id, user_id))
    return [dict(zip(COLUMNS, row)) for row in rows]


def count_own(guild_id, user_id):
    return _query("SELECT COUNT(*) FROM image_library WHERE guild_id = ? AND owner_id = ?",
                  (guild_id, user_id), one=True)[0]


def file_path(item):
    import web_panel
    return web_panel.ASSETS_DIR / item["file"]


def read_file(item):
    path = file_path(item)
    return path.read_bytes() if path.is_file() else None


async def save_image(guild_id, owner_id, title, kind, data):
    """-> (id, None) или (None, ключ ошибки)."""
    import web_panel
    if count_own(guild_id, owner_id) >= MAX_PER_USER:
        return None, "library_images.full"
    name, error = await asyncio.to_thread(web_panel.save_asset, data)
    if error:
        return None, "library_images.bad_file"
    item_id = _query("INSERT INTO image_library (guild_id, owner_id, title, kind, file, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     (guild_id, owner_id, (title or "")[:80], kind if kind in KINDS else "upload", name,
                      int(time.time())), write=True)
    return item_id, None


def can_change(interaction, item):
    from extended_modules import is_admin
    return item is not None and interaction.guild is not None and item["guild_id"] == interaction.guild.id \
        and (item["owner_id"] == interaction.user.id or is_admin(interaction))


def can_see(interaction, item):
    return item is not None and interaction.guild is not None and item["guild_id"] == interaction.guild.id \
        and (item["owner_id"] == interaction.user.id or item["shared"] or can_change(interaction, item))


def extension(item):
    return os.path.splitext(item["file"])[1] or ".png"


def item_label(item):
    return t("library_images.option", id=item["id"], title=item["title"] or t(f"library_images.kind.{item['kind']}"),
             kind=t(f"library_images.kind.{item['kind']}"))


# ============================================================
# ПАНЕЛИ
# ============================================================

def home_embed(interaction):
    items = visible_images(interaction.guild.id, interaction.user.id)
    own = sum(1 for item in items if item["owner_id"] == interaction.user.id)
    return panel_embed(interaction, "library_images.home", own=own, shared=len(items) - own, max=MAX_PER_USER)


class LibraryImagesView(PanelView):
    texts = "library_images.home"

    def __init__(self, interaction, back_target=None):
        super().__init__(back_target=back_target)
        items = visible_images(interaction.guild.id, interaction.user.id)
        if items:
            select = core.PagedSelect(placeholder=t("library_images.pick")[:150], options=[
                discord.SelectOption(label=item_label(item)[:100], value=str(item["id"]),
                                     description=(t("library_images.shared_mark") if item["shared"] else None))
                for item in items])

            async def picked(i):
                await open_item(i, int(select.values[0]), back_target=(core.screen(i), self))

            select.on_pick = picked
            self.add_item(select)

    @discord.ui.button(label="Загрузить свою", emoji="📤", style=discord.ButtonStyle.success)
    async def upload(self, interaction, button):
        await interaction.response.send_modal(UploadModal(self.back_target))


async def open_item(interaction, item_id, back_target=None):
    item = get_image(item_id)
    if not can_see(interaction, item):
        await reply(interaction, "library_images.gone")
        return
    data = read_file(item)
    if data is None:
        await reply(interaction, "library_images.gone")
        return
    filename = f"image_{item['id']}{extension(item)}"
    embed = panel_embed(interaction, "library_images.item", hints=False,
                        title=item["title"] or t(f"library_images.kind.{item['kind']}"),
                        kind=t(f"library_images.kind.{item['kind']}"), owner=f"<@{item['owner_id']}>",
                        when=f"<t:{item['created_at']}:R>",
                        shared=t("library_images.shared_yes") if item["shared"] else t("library_images.shared_no"))
    embed.set_image(url=f"attachment://{filename}")
    view = ItemView(item, back_target=back_target)
    file = discord.File(io.BytesIO(data), filename=filename)
    await interaction.response.edit_message(embed=embed, view=view, attachments=[file])


class ItemView(PanelView):
    texts = "library_images.item"

    def __init__(self, item, back_target=None):
        super().__init__(back_target=back_target, timeout=900)
        self.item_id = item["id"]
        self.share.label = t("library_images.item.unshare" if item["shared"] else "library_images.item.share")[:80]
        # «Назад» убирает картинку-вложение (обычный «Назад» оставил бы её под списком)
        back = next((child for child in self.children if getattr(child, "_panel_nav", False)), None)
        if back is not None and back_target is not None:
            async def go_back(i):
                embed, view = self.back_target
                await i.response.edit_message(**core.expand_embeds({"embed": embed}), view=view, attachments=[])
            back.callback = go_back

    @discord.ui.button(label="Прислать файлом", emoji="📥", style=discord.ButtonStyle.primary)
    async def download(self, interaction, button):
        item = get_image(self.item_id)
        data = read_file(item) if can_see(interaction, item) else None
        if data is None:
            await reply(interaction, "library_images.gone")
            return
        name = "".join(ch for ch in (item["title"] or "image") if ch.isalnum() or ch in " _-").strip() or "image"
        await interaction.response.send_message(
            t("library_images.file"), ephemeral=True,
            file=discord.File(io.BytesIO(data), filename=f"{name[:40]}{extension(item)}"))

    @discord.ui.button(label="Поделиться с сервером", emoji="👥", style=discord.ButtonStyle.secondary)
    async def share(self, interaction, button):
        item = get_image(self.item_id)
        if not can_change(interaction, item):
            await reply(interaction, "library_images.not_yours")
            return
        _query("UPDATE image_library SET shared = ? WHERE id = ?", (0 if item["shared"] else 1, item["id"]),
               write=True)
        await open_item(interaction, item["id"], back_target=self.back_target)

    @discord.ui.button(label="Название", emoji="📝", style=discord.ButtonStyle.secondary)
    async def rename(self, interaction, button):
        item = get_image(self.item_id)
        if not can_change(interaction, item):
            await reply(interaction, "library_images.not_yours")
            return
        await interaction.response.send_modal(RenameModal(item, self.back_target))

    @discord.ui.button(label="Удалить", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete(self, interaction, button):
        item = get_image(self.item_id)
        if not can_change(interaction, item):
            await reply(interaction, "library_images.not_yours")
            return
        _query("DELETE FROM image_library WHERE id = ?", (item["id"],), write=True)
        core.audit(interaction, "library_image.deleted", "image", item["id"], item["title"])
        await interaction.response.edit_message(embed=home_embed(interaction), attachments=[],
                                                view=LibraryImagesView(interaction, back_target=_home_back(self)))


def _home_back(view):
    """«Назад» из картинки ведёт в список; у списка — свой «Назад»."""
    if view.back_target and isinstance(view.back_target[1], LibraryImagesView):
        return view.back_target[1].back_target
    return None


class RenameModal(Modal, title="НАЗВАНИЕ"):
    def __init__(self, item, back_target):
        super().__init__(title=t("library_images.rename_modal.title")[:45])
        self.item_id, self.back_target = item["id"], back_target
        self.name = discord.ui.TextInput(label=t("library_images.rename_modal.name")[:45], max_length=80,
                                         default=item["title"] or None)
        self.add_item(self.name)

    async def on_submit(self, interaction):
        item = get_image(self.item_id)
        if not can_change(interaction, item):
            await reply(interaction, "library_images.not_yours")
            return
        _query("UPDATE image_library SET title = ? WHERE id = ?", (self.name.value.strip()[:80], item["id"]),
               write=True)
        await open_item(interaction, item["id"], back_target=self.back_target)


class UploadModal(Modal, title="СВОЯ КАРТИНКА"):
    def __init__(self, back_target):
        super().__init__(title=t("library_images.upload_modal.title")[:45])
        self.back_target = back_target
        self.name = discord.ui.TextInput(max_length=80, required=False)
        self.files = discord.ui.FileUpload(required=True, min_values=1, max_values=10)
        self.add_item(discord.ui.Label(text=t("library_images.upload_modal.name")[:45], component=self.name))
        self.add_item(discord.ui.Label(text=t("library_images.upload_modal.files")[:45],
                                       description=t("library_images.upload_modal.help")[:100], component=self.files))

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        saved, problems = 0, []
        for n, attachment in enumerate(self.files.values or []):
            if attachment.size > 8 * 1024 * 1024:
                problems.append(attachment.filename)
                continue
            title = self.name.value.strip() or os.path.splitext(attachment.filename)[0]
            if len(self.files.values) > 1 and self.name.value.strip():
                title = f"{title} {n + 1}"
            item_id, problem = await save_image(interaction.guild.id, interaction.user.id, title, "upload",
                                                await attachment.read())
            if problem:
                problems.append(attachment.filename)
            else:
                saved += 1
        text = t("library_images.uploaded", count=saved)
        if problems:
            text += "\n" + t("library_images.upload_failed", files=", ".join(problems)[:500])
        await interaction.followup.send(text, ephemeral=True)


async def open_home(interaction, back_target=None):
    await interaction.response.edit_message(embed=home_embed(interaction), attachments=[],
                                            view=LibraryImagesView(interaction, back_target=back_target))
