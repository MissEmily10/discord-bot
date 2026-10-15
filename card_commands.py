"""
Своя слэш-команда у живой картинки: /очки_клана, /прогресс, /табло…

Создатель картинки задаёт имя («Своя команда» в панели правки) — бот
регистрирует на ЭТОМ сервере команду с полями:
    значение — что поставить: +5, -1, =10, «цель 500», текст, «Аня | дизайн | пт»
    кто      — для рейтинга и таблицы: имя или номер строки
    столбец  — для таблицы: номер столбца (одна ячейка)
Каждый вызов перерисовывает картинку в канале. Пользоваться командой могут
те же, кто может менять картинку: автор, админы и выбранные в «Кто меняет».

Discord: до 100 своих команд на сервере; создавать/менять команды — не чаще
~200 раз в сутки на сервер (нам хватает с запасом).
"""

import io
import logging
import re

import discord
from discord import app_commands

import core
from core import Modal, t
from actions import reply

_log = logging.getLogger(__name__)
NAME = re.compile(r"^[a-zа-яё0-9_-]{1,32}$")


def clean_name(raw):
    name = re.sub(r"\s+", "_", (raw or "").strip().lower().lstrip("/"))
    return name if NAME.match(name) else None


def taken_names(bot, guild_id, except_card=None):
    import cards
    names = {command.name for command in bot.tree.get_commands()}  # глобальные: embed, forms…
    names |= {card["command"] for card in cards.get_cards(guild_id)
              if card.get("command") and card["id"] != except_card}
    return names


def build_command(card_id, name):
    """Команда для одной картинки (имя — с сервера, обработчик знает id картинки)."""

    @app_commands.describe(значение="+5, -1, =10, текст, дата 25.12 20:00 или строка «Аня | дизайн | пт»",
                           кто="Название поля (автор, цель…), имя или номер строки",
                           столбец="Номер столбца — поменять одну ячейку строки")
    async def handler(interaction: discord.Interaction, значение: str, кто: str = None, столбец: int = None):
        await run(interaction, card_id, значение, кто, столбец)

    return app_commands.Command(name=name, description=t("cards.command.description")[:100], callback=handler)


def apply_value(card, value, who=None, column=None):
    """
    Изменение по шаблону картинки. -> None или ключ ошибки.
    кто = название поля («автор», «цель», «счёт слева») — меняется это поле;
    кто = имя/номер строки (или значение с «|») — строка; без «кто» — главное поле.
    """
    import cards
    import card_templates as ct
    data, template = card["data"], card["template"]
    value = (value or "").strip()
    if template == "rating":
        if not who:
            return "cards.error.no_name"
        if value.lower() in ("убрать", "удалить", "remove"):
            data["rows"] = [row for row in data["rows"] if row["name"].lower() != who.strip().lower()]
            return None
        return cards.set_score(card, who, value)
    field = ct.field_by_label(template, who) if who else None
    if field:
        return ct.set_field(template, data, field, value)
    fields = ct.spec(template).get("fields", [])
    if column and not ct.has_rows(template):
        return "cards.error.no_rows"
    if ct.has_rows(template) and (who or "|" in value or not fields or column):
        return ct.set_row(template, data, who or "", value, column)
    if not fields:
        return "cards.error.bad_value"
    if template == "progress":
        return ct.set_progress(data, value)
    return ct.set_field(template, data, fields[0][0], value)


async def run(interaction, card_id, value, who=None, column=None):
    import cards
    card = cards.get_card(card_id)
    if card is None:
        await reply(interaction, "cards.gone")
        return
    if not cards.can_edit(interaction, card):
        await reply(interaction, "cards.denied")
        return
    problem = apply_value(card, value, who, column)
    if problem:
        await reply(interaction, problem, max=cards.MAX_ROWS)
        return
    cards.save_card(card)
    await interaction.response.defer(ephemeral=True, thinking=True)
    core.audit(interaction, "card.command", "card", card_id, f"{who or ''} {value}"[:200])
    if card["message_id"] and await cards.refresh_message(interaction.guild, card):
        await interaction.followup.send(t("cards.command.done", channel=f"<#{card['channel_id']}>"), ephemeral=True)
        return
    # не опубликована (или сообщение удалили) — показываем картинку тому, кто вызвал
    message = await cards.card_message(card, interaction.guild)
    await interaction.followup.send(t("cards.command.done_unpublished"), embed=message["embed"],
                                    file=message["file"], ephemeral=True)


async def register(bot, card, sync=True):
    guild = discord.Object(id=card["guild_id"])
    bot.tree.add_command(build_command(card["id"], card["command"]), guild=guild, override=True)
    if sync:
        await bot.tree.sync(guild=guild)


async def unregister(bot, card, sync=True):
    guild = discord.Object(id=card["guild_id"])
    bot.tree.remove_command(card["command"], guild=guild)
    if sync:
        try:
            await bot.tree.sync(guild=guild)
        except discord.HTTPException as error:
            _log.warning("card command unregister %s: %s", card["command"], error)


async def load_all(bot):
    """Из setup_hook: вернуть команды всех картинок в дерево и синхронизировать их сервера."""
    import cards
    cards.ensure_table()
    from database import get_connection
    connection = get_connection()
    rows = connection.execute("SELECT id FROM live_cards WHERE command != ''").fetchall()
    connection.close()
    guilds = set()
    for card_id, in rows:
        card = cards.get_card(card_id)
        if card and clean_name(card["command"]):
            bot.tree.add_command(build_command(card["id"], card["command"]),
                                 guild=discord.Object(id=card["guild_id"]), override=True)
            guilds.add(card["guild_id"])
    for guild_id in guilds:
        try:
            await bot.tree.sync(guild=discord.Object(id=guild_id))
        except discord.HTTPException as error:  # бота выгнали с сервера и т. п.
            _log.warning("card commands sync %s: %s", guild_id, error)


class CommandModal(Modal, title="СВОЯ КОМАНДА"):
    def __init__(self, card):
        super().__init__(title=t("cards.command_modal.title")[:45])
        self.card_id = card["id"]
        self.name = discord.ui.TextInput(default=card.get("command") or None, max_length=32, required=False,
                                         placeholder=t("cards.command_modal.placeholder")[:100])
        self.add_item(discord.ui.Label(text=t("cards.command_modal.name")[:45],
                                       description=t("cards.command_modal.help")[:100], component=self.name))

    async def on_submit(self, interaction):
        import cards
        card = cards.get_card(self.card_id)
        if not cards.can_manage(interaction, card):
            await reply(interaction, "cards.denied" if card else "cards.gone")
            return
        raw = self.name.value.strip()
        bot = interaction.client
        if not raw:  # пусто — убрать команду
            if card.get("command"):
                await interaction.response.defer()
                await unregister(bot, card)
                core.audit(interaction, "card.command_removed", "card", card["id"], card["command"])
                card["command"] = ""
                cards.save_card(card)
                await interaction.edit_original_response(embed=cards.editor_embed(interaction, card),
                                                         view=cards.CardEditorView(card["id"]))
                return
            await reply(interaction, "cards.command.bad_name")
            return
        name = clean_name(raw)
        if name is None:
            await reply(interaction, "cards.command.bad_name")
            return
        if name in taken_names(bot, interaction.guild.id, except_card=card["id"]):
            await reply(interaction, "cards.command.taken", name=name)
            return
        await interaction.response.defer()
        old = card.get("command")
        if old and old != name:
            bot.tree.remove_command(old, guild=discord.Object(id=card["guild_id"]))
        card["command"] = name
        try:
            await register(bot, card)
        except discord.HTTPException as error:
            _log.warning("card command register %s: %s", name, error)
            bot.tree.remove_command(name, guild=discord.Object(id=card["guild_id"]))
            card["command"] = old or ""
            await interaction.followup.send(t("cards.command.failed"), ephemeral=True)
            return
        cards.save_card(card)
        core.audit(interaction, "card.command_set", "card", card["id"], name)
        await interaction.edit_original_response(embed=cards.editor_embed(interaction, card),
                                                 view=cards.CardEditorView(card["id"]))
        await interaction.followup.send(t("cards.command.ready", name=name), ephemeral=True)
