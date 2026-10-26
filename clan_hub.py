"""
clan_hub.py
===========
«Всё о кланах», карточка клана, Совет кланов и панель управления кланами
(CLANS_TODO п. 9, 10, 11, 12).

- «Всё о кланах» — живое сообщение в канале (ставит админ из «Управление
  кланами»): список кланов и выпадающее меню. Выбрал клан — карточка (видна
  только тебе): команда, участники, казна, огонёк, «чем известен», значки,
  квесты срока, статус, кнопки «Подать заявку» и «Респекты подробно».
  В список клан попадает через месяц после создания (настраивается);
  повторный долг — клан скрыт месяц; в долгу — метка «⏳ в долгу».
- Совет кланов: каждый месяц после итогов бот сохраняет историю старого
  канала файлом в журнал кланов, удаляет его и создаёт новый
  («совет-ноябрь-2026») в категории из настроек. Доступ — представителям
  кланов топ-3 месяца и клана — лидера направления 🛠 (не в долгу).
- Управление кланами (/clan → «Управление кланами», админы): включить жизнь
  кланов, каналы (проверка, журнал, биллборд), категория Совета, роль
  дежурных, опубликовать биллборд и «Всё о кланах», правка казны / респекта /
  срока с причиной, роспуск.
"""

import io
import logging
import time

import discord

import core
import ecosystem
from core import t, panel_embed, PanelView, Modal
from actions import reply
from database import get_connection

_log = logging.getLogger(__name__)

HUB_EVERY = 10 * 60
MONTHS_GEN = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
              "ноябрь", "декабрь")


# ============================================================
# КАРТОЧКА КЛАНА
# ============================================================

def known_for(scope_id, tag):
    """«Чем известен»: направление с наибольшим респектом (и второе, если близко)."""
    import clan_treasury
    rows = sorted(((amount, direction) for (row_tag, direction), amount in
                   clan_treasury.respect_by_direction(scope_id, tag).items() if amount > 0), reverse=True)
    if not rows:
        return ""
    shown = [rows[0][1]]
    if len(rows) > 1 and rows[1][0] >= rows[0][0] * 0.8:
        shown.append(rows[1][1])
    return " · ".join(clan_treasury.DIRECTIONS.get(d, d) for d in shown)


def badges(scope_id, tag):
    """Значки навсегда: места месяца и лидерство в направлениях."""
    import clan_life
    import clan_treasury
    clan_life.ensure_tables()
    connection = get_connection()
    rows = connection.execute("SELECT month, place, kind FROM clan_awards WHERE scope_id = ? AND tag = ? "
                              "ORDER BY month DESC", (scope_id, tag.upper())).fetchall()
    connection.close()
    found = []
    for month, place, kind in rows:
        year, number = month.split("-")
        when = f"{MONTHS_GEN[int(number) - 1]} {year}"
        if kind == "place":
            found.append(t("clan_hub.badge_place", medal={1: "🥇", 2: "🥈", 3: "🥉"}.get(place, ""), place=place,
                           when=when))
        elif kind.startswith("dir:"):
            found.append(t("clan_hub.badge_dir", direction=clan_treasury.DIRECTIONS.get(kind[4:], kind[4:]),
                           when=when))
    return found


def card_embed(interaction, clan):
    import clan_life
    import clan_members
    import clan_quests
    scope_id, tag = clan["scope_id"], clan["tag"]
    state = clan_life.state_of(scope_id, tag)
    members = len(clan_members.member_ids(scope_id, tag))
    status = t("clan_hub.status.debt") if state["state"] == "debt" else t("clan_hub.status.active")
    quests = []
    for level, row in sorted(clan_quests.assign(scope_id, tag, state["term_no"]).items(),
                             key=lambda kv: clan_quests.LEVELS.index(kv[0])):
        quests.append(t("clan_hub.quest_line", level=t("clan_quests.level." + level),
                        title=clan_quests.quest_info(row)["title"], status=t("clan_quests.status." + row["status"])))
    found = badges(scope_id, tag)
    embed = panel_embed(interaction, "clan_hub.card", hints=False, tag=tag, name=clan["name"] or "—",
                        leader=clan_members.mention(clan["leader_id"]),
                        deputies=", ".join(clan_members.mention(i) for i in clan["deputies"]) or "—",
                        reps=", ".join(clan_members.mention(i) for i in clan["reps"]) or "—",
                        members=members, max=clan_members.limit_for(scope_id, tag), status=status,
                        life=clan_life.status_line(scope_id, tag), known=known_for(scope_id, tag) or "—",
                        badges="\n".join(found[:10]) or "—", quests="\n".join(quests) or "—")
    emblem = clan_life.setting(scope_id, f"emblem:{tag}")
    if emblem:
        embed.set_thumbnail(url=emblem)
    return embed


class CardView(discord.ui.View):
    """Под карточкой (видна только нажавшему): «Подать заявку» и «Респекты подробно»."""

    def __init__(self, tag, can_apply=True):
        super().__init__(timeout=600)
        self.tag = tag
        if not can_apply:
            self.remove_item(self.apply)

    @discord.ui.button(label="Подать заявку", emoji="📨", style=discord.ButtonStyle.success)
    async def apply(self, interaction, button):
        import clan_life
        import clan_members
        scope_id = ecosystem.scope(interaction.guild.id)
        if clan_members.clan_of(scope_id, interaction.user.id) is not None:
            await reply(interaction, "clan.error.other_clan")
            return
        if not clan_life.can_accept(scope_id, self.tag):
            await reply(interaction, "clan_hub.no_accept_debt")
            return
        await interaction.response.send_modal(clan_members.ApplyModal(self.tag))

    @discord.ui.button(label="Респекты подробно", emoji="🏅", style=discord.ButtonStyle.secondary)
    async def respect(self, interaction, button):
        import clan_treasury
        scope_id = ecosystem.scope(interaction.guild.id)
        rows = sorted(((amount, direction) for (_, direction), amount in
                       clan_treasury.respect_by_direction(scope_id, self.tag).items()), reverse=True)
        lines = [t("clan_hub.respect_line", direction=clan_treasury.DIRECTIONS.get(d, d), amount=f"{a:g}")
                 for a, d in rows if a]
        await interaction.response.send_message(t("clan_hub.respect_detail", tag=self.tag,
                                                  lines="\n".join(lines) or "—"), ephemeral=True)


async def show_card(interaction, clan):
    import clan_members
    mine = clan_members.clan_of(clan["scope_id"], interaction.user.id)
    await interaction.response.send_message(embed=card_embed(interaction, clan),
                                            view=CardView(clan["tag"], can_apply=mine is None), ephemeral=True)


# ============================================================
# «ВСЁ О КЛАНАХ» — ЖИВОЕ СООБЩЕНИЕ
# ============================================================

def visible_clans(scope_id, now=None):
    import clan_life
    import clans
    now = now or time.time()
    min_days = clan_life.setting(scope_id, "hub_min_days", 30)
    return [clan for clan in clans.get_clans(scope_id)
            if clan["created_at"] <= now - int(min_days or 0) * 86400 and not clan_life.hidden(scope_id, clan["tag"], now)]


def hub_embed(scope_id):
    import clan_life
    import clan_members
    lines = []
    for clan in visible_clans(scope_id):
        debt = " " + t("clan_billboard.in_debt") if clan_life.in_debt(scope_id, clan["tag"]) else ""
        reliable = " " + t("clan_life.line.reliable") if clan_life.reliable(scope_id, clan["tag"]) else ""
        known = known_for(scope_id, clan["tag"])
        lines.append(t("clan_hub.hub_line", tag=clan["tag"], name=clan["name"] or "—",
                       members=len(clan_members.member_ids(scope_id, clan["tag"])), known=f" · {known}" if known else "",
                       marks=debt + reliable))
    return discord.Embed(title=t("clan_hub.hub_title"), color=0x2B2D31,
                         description=t("clan_hub.hub_text", lines="\n".join(lines) or t("clan_hub.hub_empty"))[:4096])


class HubSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"rb:clh:sel"):
    def __init__(self, options=None, *, item=None):
        super().__init__(item or discord.ui.Select(
            custom_id="rb:clh:sel", placeholder=t("clan_hub.pick")[:150],
            options=options or [discord.SelectOption(label="—", value="-")]))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(item=item)

    async def callback(self, interaction):
        import clans
        value = self.item.values[0] if self.item.values else "-"
        clan = clans.get_clan(ecosystem.scope(interaction.guild.id), value) if value != "-" else None
        if clan is None or clan["status"] == "dissolved":
            await reply(interaction, "clan.error.no_clan")
            return
        await show_card(interaction, clan)


def hub_view(scope_id):
    options = [discord.SelectOption(label=f"[{c['tag']}] {c['name'] or ''}"[:100], value=c["tag"])
               for c in visible_clans(scope_id)][:25]
    view = discord.ui.View(timeout=None)
    view.add_item(HubSelect(options or None))
    return view


async def publish_hub(channel, scope_id):
    import clan_life
    message = await channel.send(embed=hub_embed(scope_id), view=hub_view(scope_id))
    clan_life.set_setting(scope_id, "hub_channel", channel.id)
    clan_life.set_setting(scope_id, "hub_message", message.id)
    return message


async def refresh_hub(bot, scope_id):
    import clan_life
    channel_id, message_id = clan_life.setting(scope_id, "hub_channel"), clan_life.setting(scope_id, "hub_message")
    channel = bot.get_channel(int(channel_id)) if channel_id else None
    if channel is None or not message_id:
        return
    try:
        message = await channel.fetch_message(int(message_id))
        await message.edit(embed=hub_embed(scope_id), view=hub_view(scope_id))
    except discord.HTTPException:
        pass


# ============================================================
# СОВЕТ КЛАНОВ
# ============================================================

async def refresh_council(bot, scope_id, month, top_tags, dev_order=()):
    """Новый канал Совета на месяц: представители топ-3 и лидера 🛠 (не в долгу); старый — в архив файлом."""
    import clan_life
    import clans
    category_id = clan_life.setting(scope_id, "council_category")
    category = bot.get_channel(int(category_id)) if category_id else None
    if category is None:
        return None
    guild = category.guild
    old_id = clan_life.setting(scope_id, "council_channel")
    old = guild.get_channel(int(old_id)) if old_id else None
    if old is not None:
        lines = []
        try:
            async for message in old.history(limit=5000, oldest_first=True):
                lines.append(f"[{message.created_at:%Y-%m-%d %H:%M}] {message.author.display_name}: {message.content}")
        except discord.HTTPException:
            pass
        log_channel = bot.get_channel(int(clan_life.setting(scope_id, "log_channel") or 0))
        if log_channel is not None and lines:
            try:
                await log_channel.send(t("clan_hub.council_archive", name=old.name), file=discord.File(
                    io.BytesIO("\n".join(lines).encode("utf-8")), filename=f"{old.name}.txt"))
            except discord.HTTPException:
                pass
        try:
            await old.delete(reason="Purr: новый Совет кланов")
        except discord.HTTPException:
            pass
    seats = [tag for tag in top_tags if not clan_life.in_debt(scope_id, tag)]
    # лидер направления 🛠; если он уже в топ-3 — место следующему по 🛠
    dev_tag = next((tag for tag in dev_order if tag not in seats and not clan_life.in_debt(scope_id, tag)), None)
    if dev_tag:
        seats.append(dev_tag)
    overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    for tag in seats:
        clan = clans.get_clan(scope_id, tag)
        for user_id in (clan or {}).get("reps") or []:
            member = guild.get_member(user_id)
            if member is not None:
                overwrites[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                                 read_message_history=True)
    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
    year, number = month.split("-")
    name = f"совет-{MONTHS_GEN[int(number) % 12]}-{year if int(number) < 12 else int(year) + 1}"
    try:
        channel = await guild.create_text_channel(name, category=category, overwrites=overwrites,
                                                  reason="Purr: Совет кланов на месяц")
    except discord.HTTPException as error:
        _log.warning("council create: %s", error)
        return None
    clan_life.set_setting(scope_id, "council_channel", channel.id)
    try:
        await channel.send(t("clan_hub.council_welcome", clans=", ".join(f"[{tag}]" for tag in seats) or "—"))
    except discord.HTTPException:
        pass
    return channel


# ============================================================
# ПАНЕЛЬ УПРАВЛЕНИЯ КЛАНАМИ (админы)
# ============================================================

def admin_embed(interaction):
    import clan_life
    import clan_quests
    import clan_shop
    import clans
    scope_id = ecosystem.scope(interaction.guild.id)
    rows = clans.get_clans(scope_id)
    debt = [c["tag"] for c in rows if clan_life.in_debt(scope_id, c["tag"])]
    pending_quests = len(clan_quests._rows("WHERE scope_id = ? AND status = 'submitted'", (scope_id,)))

    def channel(key):
        value = clan_life.setting(scope_id, key)
        return f"<#{value}>" if value else t("clan_hub.admin.not_set")
    duty = clan_life.setting(scope_id, "duty_role")
    return panel_embed(interaction, "clan_hub.admin", hints=False,
                       life=t("clan_hub.admin.on") if clan_life.enabled(scope_id) else t("clan_hub.admin.off"),
                       clans=len(rows), debt=", ".join(f"[{d}]" for d in debt) or "—",
                       quests=pending_quests, purchases=clan_shop.pending_count(scope_id),
                       review=channel("review_channel"), log=channel("log_channel"),
                       billboard=channel("billboard_channel"), hub=channel("hub_channel"),
                       council=channel("council_category"), duty=f"<@&{duty}>" if duty else t("clan_hub.admin.not_set"),
                       min_days=clan_life.setting(scope_id, "hub_min_days", 30))


class AdminView(PanelView):
    texts = "clan_hub.admin"
    button_order = ("life", "channels", "publish_billboard", "publish_hub", "manage_clan", "directions", "hub_days")

    def __init__(self, interaction, back_target=None):
        super().__init__(back_target=back_target, timeout=900)
        import clan_life
        on = clan_life.enabled(ecosystem.scope(interaction.guild.id))
        self.life.label = t("clan_hub.admin.life_off" if on else "clan_hub.admin.life_on")[:80]
        self.life.style = discord.ButtonStyle.danger if on else discord.ButtonStyle.success

    async def _ok(self, interaction):
        import clan_members
        if not clan_members.is_admin(interaction.guild, interaction.user):
            await reply(interaction, "common.need_admin")
            return False
        return True

    async def _home(self, interaction):
        await interaction.response.edit_message(embed=admin_embed(interaction),
                                                view=AdminView(interaction, back_target=self.back_target))

    @discord.ui.button(label="Жизнь кланов", emoji="⏳", style=discord.ButtonStyle.success)
    async def life(self, interaction, button):
        import clan_life
        if not await self._ok(interaction):
            return
        scope_id = ecosystem.scope(interaction.guild.id)
        if clan_life.enabled(scope_id):
            clan_life.set_setting(scope_id, "life", False)
            core.audit(interaction, "clans.life_off", "guild", scope_id, None)
            await self._home(interaction)
            return
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "clan_hub.admin.life_ask", hints=False),
            view=LifeConfirmView(back_target=self.back_target))

    @discord.ui.button(label="Каналы и роли", emoji="📁", style=discord.ButtonStyle.secondary)
    async def channels(self, interaction, button):
        if await self._ok(interaction):
            await interaction.response.edit_message(embed=panel_embed(interaction, "clan_hub.channels", hints=False),
                                                    view=ChannelsView(back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Биллборд — сюда", emoji="📊", style=discord.ButtonStyle.secondary)
    async def publish_billboard(self, interaction, button):
        import clan_billboard
        if not await self._ok(interaction):
            return
        await interaction.response.defer()
        try:
            await clan_billboard.publish(interaction.channel, ecosystem.scope(interaction.guild.id))
        except discord.HTTPException:
            await interaction.followup.send(t("clan_hub.cant_post"), ephemeral=True)
            return
        await interaction.edit_original_response(embed=admin_embed(interaction),
                                                 view=AdminView(interaction, back_target=self.back_target))

    @discord.ui.button(label="«Всё о кланах» — сюда", emoji="📣", style=discord.ButtonStyle.secondary)
    async def publish_hub(self, interaction, button):
        if not await self._ok(interaction):
            return
        await interaction.response.defer()
        try:
            await publish_hub(interaction.channel, ecosystem.scope(interaction.guild.id))
        except discord.HTTPException:
            await interaction.followup.send(t("clan_hub.cant_post"), ephemeral=True)
            return
        await interaction.edit_original_response(embed=admin_embed(interaction),
                                                 view=AdminView(interaction, back_target=self.back_target))

    @discord.ui.button(label="Клан…", emoji="🛡️", style=discord.ButtonStyle.primary)
    async def manage_clan(self, interaction, button):
        import clans
        if not await self._ok(interaction):
            return
        rows = clans.get_clans(ecosystem.scope(interaction.guild.id))
        if not rows:
            await reply(interaction, "clans.empty")
            return
        view = PanelView(back_target=(core.screen(interaction), self), timeout=600)
        select = core.PagedSelect(placeholder=t("clan_hub.admin.pick")[:150], options=[
            discord.SelectOption(label=f"[{c['tag']}] {c['name'] or ''}"[:100], value=c["tag"]) for c in rows])

        async def picked(i):
            clan = clans.get_clan(ecosystem.scope(i.guild.id), select.values[0])
            await i.response.edit_message(embed=clan_admin_embed(i, clan),
                                          view=ClanAdminView(clan["tag"], back_target=(core.screen(i), view)))
        select.on_pick = picked
        view.add_item(select)
        await interaction.response.edit_message(view=view)

    @discord.ui.button(label="Направления наград", emoji="🧭", style=discord.ButtonStyle.secondary)
    async def directions(self, interaction, button):
        if await self._ok(interaction):
            await interaction.response.edit_message(embed=directions_embed(interaction),
                                                    view=DirectionsView(back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Новые кланы в списке", emoji="🗓️", style=discord.ButtonStyle.secondary)
    async def hub_days(self, interaction, button):
        if await self._ok(interaction):
            await interaction.response.send_modal(HubDaysModal())


def directions_embed(interaction):
    """Кто из глав категорий даёт респект в каком направлении (за .награду в казну)."""
    import clan_life
    import clan_treasury
    mapping = clan_life.setting(ecosystem.scope(interaction.guild.id), "giver_directions", {})
    lines = [f"<@{giver}> → {clan_treasury.DIRECTIONS.get(direction, direction)}" for giver, direction in mapping.items()]
    return panel_embed(interaction, "clan_hub.directions", hints=False, lines="\n".join(lines) or "—")


class DirectionsView(PanelView):
    """Сначала — направление, потом — кто (глава категории). Остальные — «👥 Сообщество»."""

    def __init__(self, back_target=None, direction=None):
        import clan_treasury
        super().__init__(back_target=back_target, timeout=600)
        self.direction = direction
        select = discord.ui.Select(placeholder=t("clan_hub.directions.pick")[:150], options=[
            discord.SelectOption(label=label, value=key, default=key == direction)
            for key, label in clan_treasury.DIRECTIONS.items()])

        async def picked(i):
            await i.response.edit_message(view=DirectionsView(self.back_target, select.values[0]))
        select.callback = picked
        self.add_item(select)
        if direction:
            users = discord.ui.UserSelect(placeholder=t("clan_hub.directions.who")[:150], max_values=10)

            async def chosen(i):
                import clan_life
                import clan_members
                if not clan_members.is_admin(i.guild, i.user):
                    await reply(i, "common.need_admin")
                    return
                scope_id = ecosystem.scope(i.guild.id)
                mapping = clan_life.setting(scope_id, "giver_directions", {})
                for user in users.values:
                    mapping[str(user.id)] = self.direction
                clan_life.set_setting(scope_id, "giver_directions", mapping)
                core.audit(i, "clans.giver_directions", "guild", scope_id, self.direction)
                await i.response.edit_message(embed=directions_embed(i), view=DirectionsView(self.back_target))
            users.callback = chosen
            self.add_item(users)


class LifeConfirmView(PanelView):
    def __init__(self, back_target=None):
        super().__init__(timeout=600)
        self.back = back_target

    @discord.ui.button(label="Да, включить", emoji="✅", style=discord.ButtonStyle.danger)
    async def yes(self, interaction, button):
        import clan_life
        import clan_members
        if not clan_members.is_admin(interaction.guild, interaction.user):
            await reply(interaction, "common.need_admin")
            return
        scope_id = ecosystem.scope(interaction.guild.id)
        clan_life.set_setting(scope_id, "life", True)
        core.audit(interaction, "clans.life_on", "guild", scope_id, None)
        await interaction.response.edit_message(embed=admin_embed(interaction),
                                                view=AdminView(interaction, back_target=self.back))

    @discord.ui.button(label="Нет", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def no(self, interaction, button):
        await interaction.response.edit_message(embed=admin_embed(interaction),
                                                view=AdminView(interaction, back_target=self.back))


class ChannelsView(PanelView):
    """Выбор каналов списками: проверка, журнал, биллборд; категория Совета; роль дежурных."""

    def __init__(self, back_target=None):
        super().__init__(back_target=back_target, timeout=600)
        for key, types in (("review_channel", [discord.ChannelType.text]), ("log_channel", [discord.ChannelType.text]),
                           ("billboard_channel", [discord.ChannelType.text]),
                           ("council_category", [discord.ChannelType.category])):
            select = discord.ui.ChannelSelect(placeholder=t(f"clan_hub.channels.{key}")[:150], channel_types=types)

            async def picked(i, key=key, select=select):
                await self.store(i, key, select.values[0].id)
            select.callback = picked
            self.add_item(select)
        role = discord.ui.RoleSelect(placeholder=t("clan_hub.channels.duty_role")[:150])

        async def role_picked(i):
            await self.store(i, "duty_role", role.values[0].id)
        role.callback = role_picked
        self.add_item(role)

    async def store(self, interaction, key, value):
        import clan_life
        import clan_members
        if not clan_members.is_admin(interaction.guild, interaction.user):
            await reply(interaction, "common.need_admin")
            return
        clan_life.set_setting(ecosystem.scope(interaction.guild.id), key, value)
        core.audit(interaction, "clans.setting", "guild", interaction.guild.id, f"{key}={value}")
        await reply(interaction, "clan_hub.channels.saved")


class HubDaysModal(Modal, title="НОВЫЕ КЛАНЫ В СПИСКЕ"):
    texts = "clan_hub.days_modal"
    days = discord.ui.TextInput(label="Через сколько дней после создания", max_length=3, default="30")

    async def on_submit(self, interaction):
        import clan_life
        import clan_members
        if not clan_members.is_admin(interaction.guild, interaction.user):
            await reply(interaction, "common.need_admin")
            return
        value = self.days.value.strip()
        if not value.isdigit():
            await reply(interaction, "clan_hub.bad_number")
            return
        clan_life.set_setting(ecosystem.scope(interaction.guild.id), "hub_min_days", int(value))
        await interaction.response.edit_message(embed=admin_embed(interaction), view=AdminView(interaction))


def clan_admin_embed(interaction, clan):
    import clan_life
    import clan_treasury
    scope_id = ecosystem.scope(interaction.guild.id)
    entries = [t("clan_treasury.entry", when=f"<t:{created}:d>", amount=f"{amount:+d}", respect=f"{respect:+g}",
                 reason=reason, who=f"<@{member}>" if member else "—")
               for amount, respect, reason, member, created in clan_treasury.ledger(scope_id, clan["tag"], 12)]
    return panel_embed(interaction, "clan_hub.clan_admin", hints=False, tag=clan["tag"], name=clan["name"] or "—",
                       life=clan_life.status_line(scope_id, clan["tag"]), ledger="\n".join(entries) or "—")


class ClanAdminView(PanelView):
    texts = "clan_hub.clan_admin"

    def __init__(self, tag, back_target=None):
        super().__init__(back_target=back_target, timeout=900)
        self.tag = tag

    @discord.ui.button(label="Казна ±", emoji="💰", style=discord.ButtonStyle.primary)
    async def money(self, interaction, button):
        await interaction.response.send_modal(AdjustModal(self.tag, "money"))

    @discord.ui.button(label="Респект ±", emoji="🏅", style=discord.ButtonStyle.secondary)
    async def respect(self, interaction, button):
        await interaction.response.send_modal(AdjustModal(self.tag, "respect"))

    @discord.ui.button(label="Срок ±", emoji="🗓️", style=discord.ButtonStyle.secondary)
    async def term(self, interaction, button):
        await interaction.response.send_modal(AdjustModal(self.tag, "term"))

    @discord.ui.button(label="Распустить", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def dissolve(self, interaction, button):
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "clan_hub.dissolve_ask", hints=False, tag=self.tag),
            view=DissolveAdminView(self.tag, back_target=self.back_target))


class DissolveAdminView(PanelView):
    def __init__(self, tag, back_target=None):
        super().__init__(timeout=600)
        self.tag, self.back = tag, back_target

    @discord.ui.button(label="Да, распустить", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def yes(self, interaction, button):
        import clan_life
        import clan_members
        import clans
        if not clan_members.is_admin(interaction.guild, interaction.user):
            await reply(interaction, "common.need_admin")
            return
        clan = clans.get_clan(ecosystem.scope(interaction.guild.id), self.tag)
        if clan is None or clan["status"] == "dissolved":
            await reply(interaction, "clan.error.no_clan")
            return
        await interaction.response.defer()
        await clan_life.dissolve(clan_members.clan_guild(interaction.client, clan, interaction.guild), clan, "admin",
                                 interaction.user.id)
        core.audit(interaction, "clan.dissolved", "clan", self.tag, "admin")
        await interaction.edit_original_response(embed=panel_embed(interaction, "clan_hub.dissolved", hints=False,
                                                                   tag=self.tag), view=None)

    @discord.ui.button(label="Нет", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def no(self, interaction, button):
        await interaction.response.edit_message(embed=admin_embed(interaction),
                                                view=AdminView(interaction, back_target=self.back))


class AdjustModal(Modal):
    """Ручная правка (п. 10): число и обязательная причина — в журнал и аудит."""

    def __init__(self, tag, kind):
        super().__init__(title=t(f"clan_hub.adjust.{kind}.title")[:45])
        self.tag, self.kind = tag, kind
        self.value = discord.ui.TextInput(label=t(f"clan_hub.adjust.{kind}.value")[:45], max_length=12,
                                          placeholder="+500 / -200")
        self.reason = discord.ui.TextInput(label=t("clan_hub.adjust.reason")[:45], max_length=200)
        self.add_item(self.value)
        self.add_item(self.reason)
        if kind == "respect":
            self.direction = discord.ui.TextInput(label=t("clan_hub.adjust.direction")[:45], max_length=30,
                                                  default="сообщество")
            self.add_item(self.direction)

    async def on_submit(self, interaction):
        import clan_life
        import clan_members
        import clan_treasury
        if not clan_members.is_admin(interaction.guild, interaction.user):
            await reply(interaction, "common.need_admin")
            return
        raw = self.value.value.strip().replace(" ", "").replace(",", ".")
        try:
            number = float(raw)
        except ValueError:
            await reply(interaction, "clan_hub.bad_number")
            return
        scope_id = ecosystem.scope(interaction.guild.id)
        reason = self.reason.value.strip()
        key = f"admin:{self.kind}:{self.tag}:{interaction.id}"
        if self.kind == "money":
            if number < 0 and clan_treasury.balance(scope_id, self.tag)[0] + number < 0:
                await reply(interaction, "clan_hub.no_negative")
                return
            clan_treasury.record(scope_id, self.tag, "", int(number), 0, f"админ: {reason}", source_key=key,
                                 by_user=interaction.user.id)
        elif self.kind == "respect":
            clan_treasury.record(scope_id, self.tag, "", 0, number, f"админ: {reason}", source_key=key,
                                 by_user=interaction.user.id,
                                 direction=clan_treasury.direction_of(self.direction.value) or "community")
        else:
            state = clan_life.state_of(scope_id, self.tag)
            target = "debt_until" if state["state"] == "debt" else "due_at"
            state[target] = int(state[target] + number * 86400)
            clan_life.save_state(state)
        core.audit(interaction, f"clan.admin_{self.kind}", "clan", self.tag, f"{raw}: {reason}")
        import clans
        clan = clans.get_clan(scope_id, self.tag)
        await interaction.response.edit_message(embed=clan_admin_embed(interaction, clan),
                                                view=ClanAdminView(self.tag))


# ============================================================
# ФОН
# ============================================================

async def loop(bot):
    import asyncio
    import clan_life
    import clan_quests
    import clan_shop
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            for scope_id in clan_life.scopes(bot):
                await refresh_hub(bot, scope_id)
            await clan_quests.remind_stale(bot)
            await clan_shop.remind_stale(bot)
        except Exception:
            _log.exception("clan hub loop")
        await asyncio.sleep(HUB_EVERY)


def start(bot):
    import asyncio
    return asyncio.create_task(loop(bot))


DYNAMIC_ITEMS = [HubSelect]
