"""
ecosystem.py
============
Связка серверов («экосистема»): несколько серверов работают с одними данными
кланов (казна, реестр, дальше — биллборд, квесты). Один сервер — основа,
остальные к нему подключены. Не связанный сервер живёт сам по себе — так
пробный сервер не смешивается с основным.

Как связать (только создатель бота, Purr должен быть на обоих серверах):
1. На основном сервере: /eminemovna → Бункер → «Связка серверов» → «Код для подключения»
   (действует 10 минут).
2. На другом сервере: там же → «Подключить по коду».
Отвязать — там же; данные кланов остаются у основы.

Код модулей кланов берёт область данных через scope(guild_id).
"""

import secrets
import string
import time

import discord

import core
from core import Modal, PanelView, t, panel_embed
from actions import say
from database import get_connection

CODE_TTL = 10 * 60
_codes = {}  # код -> (id основы, истекает)


def ensure_tables():
    connection = get_connection()
    connection.execute("""CREATE TABLE IF NOT EXISTS server_links (
        guild_id INTEGER PRIMARY KEY, root_id INTEGER NOT NULL, linked_by INTEGER, linked_at INTEGER NOT NULL
    )""")
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


def scope(guild_id):
    """Область данных кланов: id основы связки или сам сервер."""
    if guild_id is None:
        return None
    row = _q("SELECT root_id FROM server_links WHERE guild_id = ?", (guild_id,), one=True)
    return row[0] if row else guild_id


def satellites(root_id):
    return [row[0] for row in _q("SELECT guild_id FROM server_links WHERE root_id = ? ORDER BY linked_at",
                                 (root_id,))]


def guilds_of(guild_id):
    """Все серверы связки этого сервера (основа первой)."""
    root = scope(guild_id)
    return [root] + satellites(root)


def new_code(root_id):
    now = time.time()
    for code in [c for c, (_, expires) in _codes.items() if expires <= now]:
        _codes.pop(code, None)
    code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    _codes[code] = (root_id, now + CODE_TTL)
    return code


def link(guild_id, code, by_user=None):
    """Подключить сервер к основе по коду. -> (True, id основы) | (False, ключ ошибки)."""
    entry = _codes.get((code or "").strip().upper())
    if entry is None or entry[1] <= time.time():
        return False, "ecosystem.bad_code"
    root_id = entry[0]
    if root_id == guild_id:
        return False, "ecosystem.self"
    if scope(guild_id) != guild_id:
        return False, "ecosystem.already_linked"
    if satellites(guild_id):
        return False, "ecosystem.is_root"  # основа другой связки не может стать её частью
    if scope(root_id) != root_id:
        return False, "ecosystem.root_is_linked"
    _q("INSERT INTO server_links (guild_id, root_id, linked_by, linked_at) VALUES (?, ?, ?, ?)",
       (guild_id, root_id, by_user, int(time.time())), write=True)
    _codes.pop(code.strip().upper(), None)
    return True, root_id


def unlink(guild_id):
    return _q("DELETE FROM server_links WHERE guild_id = ?", (guild_id,), write=True) > 0


# ============================================================
# ПАНЕЛЬ: /eminemovna → «Связка серверов»
# ============================================================

def _name(client, guild_id):
    guild = client.get_guild(guild_id) if client else None
    return f"**{guild.name}**" if guild else f"`{guild_id}`"


def status_embed(interaction):
    guild_id = interaction.guild.id
    root = scope(guild_id)
    if root != guild_id:
        status = t("ecosystem.status.linked", root=_name(interaction.client, root))
    elif satellites(guild_id):
        names = "\n".join(f"• {_name(interaction.client, g)}" for g in satellites(guild_id))
        status = t("ecosystem.status.root", list=names)
    else:
        status = t("ecosystem.status.alone")
    return panel_embed(interaction, "ecosystem", hints=False, status=status)


class EcosystemView(PanelView):
    def __init__(self, interaction, back_target=None):
        super().__init__(back_target=back_target, timeout=600)
        guild_id = interaction.guild.id
        linked = scope(guild_id) != guild_id
        if not linked:
            self.add_item(self._button("ecosystem.get_code", "🔑", discord.ButtonStyle.primary, self.get_code))
            if not satellites(guild_id):
                self.add_item(self._button("ecosystem.use_code", "🔗", discord.ButtonStyle.success, self.use_code))
        else:
            self.add_item(self._button("ecosystem.unlink", "✂️", discord.ButtonStyle.danger, self.unlink_self))
        if satellites(guild_id):
            select = discord.ui.Select(
                placeholder=t("ecosystem.unlink_placeholder")[:150],
                options=[discord.SelectOption(label=(getattr(interaction.client.get_guild(g), "name", None)
                                                     or str(g))[:100], value=str(g)) for g in satellites(guild_id)][:25])

            async def picked(i):
                if not await _owner(i):
                    return
                unlink(int(select.values[0]))
                core.audit(i, "ecosystem.unlinked", "guild", int(select.values[0]))
                await i.response.edit_message(embed=status_embed(i), view=EcosystemView(i, self.back_target))
            select.callback = picked
            self.add_item(select)

    def _button(self, key, emoji, style, callback):
        button = discord.ui.Button(label=t(key)[:80], emoji=emoji, style=style)
        button.callback = callback
        return button

    async def get_code(self, interaction):
        if not await _owner(interaction):
            return
        code = new_code(interaction.guild.id)
        await say(interaction, "ecosystem.code", code=code, minutes=CODE_TTL // 60)

    async def use_code(self, interaction):
        if not await _owner(interaction):
            return
        await interaction.response.send_modal(CodeModal(self.back_target))

    async def unlink_self(self, interaction):
        if not await _owner(interaction):
            return
        unlink(interaction.guild.id)
        core.audit(interaction, "ecosystem.unlinked", "guild", interaction.guild.id)
        await interaction.response.edit_message(embed=status_embed(interaction),
                                                view=EcosystemView(interaction, self.back_target))


async def _owner(interaction):
    if not core.is_owner(interaction):
        await say(interaction, "common.owner_only")
        return False
    return True


class CodeModal(Modal, title="ПОДКЛЮЧИТЬ"):
    def __init__(self, back_target):
        super().__init__(title=t("ecosystem.modal_title")[:45])
        self.back_target = back_target
        self.code = discord.ui.TextInput(max_length=12, placeholder="A1B2C3")
        self.add_item(discord.ui.Label(text=t("ecosystem.modal_label")[:45],
                                       description=t("ecosystem.modal_help")[:100], component=self.code))

    async def on_submit(self, interaction):
        ok, result = link(interaction.guild.id, self.code.value, by_user=interaction.user.id)
        if not ok:
            await say(interaction, result)
            return
        core.audit(interaction, "ecosystem.linked", "guild", result)
        await interaction.response.edit_message(embed=status_embed(interaction),
                                                view=EcosystemView(interaction, self.back_target))
