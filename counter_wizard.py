"""
counter_wizard.py
=================
Действие кнопки «Счётчик» (counter.change) — пошагово, без ручного «очки +1»:

1. Новый счётчик (имя) или существующий: счётчики сервера и значения из живых
   картинок — строки рейтингов и таблиц, картинки с одним числом.
2. Что сделать: прибавить, отнять, поставить число, сбросить в 0.
3. Сколько (для прибавить / отнять / поставить).

Результат — то же значение действия, что и раньше («kick +1»), его сохраняет
embed_module.commit_pending.
"""

import re

import discord

import core
from core import Modal, PanelView, t, panel_embed

MAX_NAME = 32


def counter_name(text):
    """Имя счётчика из подписи: одно слово, без пробелов и знаков («Кот Пёс» -> «котпёс»)."""
    return re.sub(r"[^\w-]+", "", str(text or "").lower())[:MAX_NAME]


def existing(guild_id):
    """
    Что уже есть: [(имя, подпись для списка, где)] — счётчики сервера и
    числа в живых картинках. Имена без повторов.
    """
    import cards
    import card_templates as ct
    from database import get_counters
    found, seen = [], set()

    def add(name, label, where):
        key = counter_name(name)
        if key and key not in seen:
            seen.add(key)
            found.append((key, label[:100], where[:100]))

    for name, value in sorted((get_counters(guild_id) or {}).items()):
        add(name, f"{name} · {value}", t("counter_wizard.where.counter"))
    for card in cards.get_cards(guild_id):
        data, template, title = card["data"], card["template"], card["title"]
        if template == "rating":
            for row in data.get("rows") or []:
                add(row["name"], f"{row['name']} · {cards.score_text(row['score'])}",
                    t("counter_wizard.where.row", card=title))
            continue
        fields = ct.spec(template).get("fields", [])
        if template == "progress" or (fields and fields[0][2] == ct.N):
            main = "value" if template == "progress" else fields[0][0]
            add(title, f"{title} · {data.get(main, 0)}", t("counter_wizard.where.card", kind=cards.template_name(template)))
        if ct.has_rows(template):
            columns = ct.columns_of(template, data)
            for row in data.get("rows") or []:
                numeric = [row[n] for n in range(1, min(len(row), len(columns)))
                           if columns[n][1] == ct.N or cards.parse_change(str(row[n]), 0) is not None]
                if row and numeric:
                    add(row[0], f"{row[0]} · {numeric[0]}", t("counter_wizard.where.row", card=title))
    return found


async def start(interaction, state, pending, target, builder_view):
    """Шаг 1: новый или существующий."""
    view = PickView(interaction, state, pending, target, builder_view)
    await interaction.response.edit_message(embed=panel_embed(interaction, "counter_wizard.pick", hints=False),
                                            view=view)


class _Step(PanelView):
    def __init__(self, state, pending, target, builder_view, back_target=None):
        super().__init__(back_target=back_target, timeout=600)
        self.state, self.pending, self.target, self.builder_view = state, pending, target, builder_view

    def args(self):
        return self.state, self.pending, self.target, self.builder_view


class PickView(_Step):
    texts = "counter_wizard.pick"

    def __init__(self, interaction, state, pending, target, builder_view):
        import embed_module
        super().__init__(state, pending, target, builder_view,
                         back_target=embed_module.builder_screen(interaction, builder_view))
        items = existing(interaction.guild.id)
        if items:
            select = core.PagedSelect(
                placeholder=t("counter_wizard.pick.existing")[:150], row=1,
                options=[discord.SelectOption(label=label, description=where, value=name)
                         for name, label, where in items])

            async def picked(i):
                name = select.values[0]
                label = next((lbl for key, lbl, _ in items if key == name), name)
                await show_ops(i, name, label, *self.args())

            select.on_pick = picked
            self.add_item(select)

    @discord.ui.button(label="Новый счётчик", emoji="➕", style=discord.ButtonStyle.success)
    async def new(self, interaction, button):
        await interaction.response.send_modal(NameModal(*self.args()))


class NameModal(Modal, title="НОВЫЙ СЧЁТЧИК"):
    def __init__(self, state, pending, target, builder_view):
        super().__init__(title=t("counter_wizard.name_modal.title")[:45])
        self.state, self.pending, self.target, self.builder_view = state, pending, target, builder_view
        self.name = discord.ui.TextInput(max_length=MAX_NAME, placeholder=t("counter_wizard.name_modal.placeholder")[:100])
        self.add_item(discord.ui.Label(text=t("counter_wizard.name_modal.name")[:45],
                                       description=t("counter_wizard.name_modal.help")[:100], component=self.name))

    async def on_submit(self, interaction):
        from actions import say
        name = counter_name(self.name.value)
        if not name:
            await say(interaction, "counter_wizard.bad_name")
            return
        await show_ops(interaction, name, name, self.state, self.pending, self.target, self.builder_view)


async def show_ops(interaction, name, label, state, pending, target, builder_view):
    """Шаг 2: что сделать со значением."""
    view = OpsView(name, state, pending, target, builder_view)
    await interaction.response.edit_message(
        embed=panel_embed(interaction, "counter_wizard.ops", hints=False, name=label), view=view)


class OpsView(_Step):
    texts = "counter_wizard.ops"
    button_order = ("plus", "minus", "set_to", "reset")

    def __init__(self, name, state, pending, target, builder_view):
        super().__init__(state, pending, target, builder_view)
        self.name = name

    async def _ask(self, interaction, op):
        await interaction.response.send_modal(AmountModal(self.name, op, *self.args()))

    @discord.ui.button(label="Прибавить", emoji="➕", style=discord.ButtonStyle.success)
    async def plus(self, interaction, button):
        await self._ask(interaction, "+")

    @discord.ui.button(label="Отнять", emoji="➖", style=discord.ButtonStyle.danger)
    async def minus(self, interaction, button):
        await self._ask(interaction, "-")

    @discord.ui.button(label="Поставить число", emoji="🎯", style=discord.ButtonStyle.primary)
    async def set_to(self, interaction, button):
        await self._ask(interaction, "=")

    @discord.ui.button(label="Сбросить в 0", emoji="🔄", style=discord.ButtonStyle.secondary)
    async def reset(self, interaction, button):
        import embed_module
        await embed_module.commit_pending(interaction, *self.args(), f"{self.name} =0")


class AmountModal(Modal, title="СКОЛЬКО"):
    def __init__(self, name, op, state, pending, target, builder_view):
        kind = {"+": "plus", "-": "minus", "=": "set"}[op]
        super().__init__(title=t(f"counter_wizard.amount.{kind}")[:45])
        self.name, self.op = name, op
        self.state, self.pending, self.target, self.builder_view = state, pending, target, builder_view
        self.amount = discord.ui.TextInput(default="1" if op != "=" else "0", max_length=9)
        self.add_item(discord.ui.Label(text=t("counter_wizard.amount.label")[:45], component=self.amount))

    async def on_submit(self, interaction):
        import embed_module
        from actions import say
        raw = self.amount.value.strip().lstrip("+-=")
        if not raw.isdigit():
            await say(interaction, "counter_wizard.bad_amount")
            return
        await embed_module.commit_pending(interaction, self.state, self.pending, self.target, self.builder_view,
                                          f"{self.name} {self.op}{int(raw)}")
