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


def badge_keys(scope_id, tag):
    """Значки для картинки: place1…3, направления (help/events/art/dev/compete/community), reliable."""
    import clan_life
    clan_life.ensure_tables()
    connection = get_connection()
    rows = connection.execute("SELECT place, kind FROM clan_awards WHERE scope_id = ? AND tag = ? "
                              "ORDER BY month DESC", (scope_id, tag.upper())).fetchall()
    connection.close()
    keys = []
    for place, kind in rows:
        key = f"place{place}" if kind == "place" else kind[4:] if kind.startswith("dir:") else None
        if key and key not in keys:
            keys.append(key)
    if clan_life.reliable(scope_id, tag) and "reliable" not in keys:
        keys.append("reliable")
    return keys


def card_style(scope_id, tag):
    import clan_art
    import clan_life
    value = clan_life.setting(scope_id, f"card_style:{tag}")
    try:
        return int(value) if int(value) in clan_art.STYLES else clan_art.DEFAULT_STYLE
    except (TypeError, ValueError):
        return clan_art.DEFAULT_STYLE


def card_data(guild, clan):
    """Всё, что рисуется на карточке клана (clan_art.render_card)."""
    import clan_life
    import clan_members
    import clan_treasury
    scope_id, tag = clan["scope_id"], clan["tag"]
    state = clan_life.state_of(scope_id, tag)
    treasury, respect = clan_treasury.balance(scope_id, tag)
    mult, weeks = clan_life.fire_multiplier(scope_id, tag)

    def name(user_id):
        member = guild.get_member(user_id) if guild is not None and user_id else None
        return getattr(member, "display_name", None) or getattr(member, "name", None) or ("—" if not user_id else "ушёл")

    return dict(name=clan["name"] or tag, tag=tag,
                status=t("clan_hub.status.debt") if state["state"] == "debt" else t("clan_hub.status.active"),
                known=known_for(scope_id, tag),
                members=f"{len(clan_members.member_ids(scope_id, tag))} / {clan_members.limit_for(scope_id, tag)}",
                treasury=treasury, respect=respect, streak=weeks, mult=mult, leader=name(clan["leader_id"]),
                deputies=[name(i) for i in clan["deputies"]], reps=[name(i) for i in clan["reps"]],
                badges=badge_keys(scope_id, tag), emblem=None)


async def card_file(guild, clan, style=None):
    """Картинка карточки клана (стиль клана или выбранный) -> discord.File «clan.png»."""
    import clan_art
    import clan_life
    data = card_data(guild, clan)
    emblem = clan_life.setting(clan["scope_id"], f"emblem:{clan['tag']}")
    if emblem:
        try:
            raw = await core.fetch_bytes(emblem)
            from PIL import Image
            data["emblem"] = Image.open(io.BytesIO(raw)) if raw else None
        except Exception:  # герб не скачался — рисуем тег
            data["emblem"] = None
    return await clan_art.file(clan_art.render_card, data, style or card_style(clan["scope_id"], clan["tag"]),
                               filename="clan.png")


def card_embed(interaction, clan):
    """Текст под картинкой карточки: срок, квесты, значки словами; сама карточка — картинкой."""
    import clan_life
    import clan_members
    import clan_quests
    scope_id, tag = clan["scope_id"], clan["tag"]
    state = clan_life.state_of(scope_id, tag)
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
                        life=clan_life.status_line(scope_id, tag), badges="\n".join(found[:10]) or "—",
                        quests="\n".join(quests) or "—")
    embed.set_image(url="attachment://clan.png")
    return embed


def can_style(clan, user_id, guild=None, member=None):
    """Стиль карточки меняют глава и замы (и админы)."""
    import clan_members
    if user_id == clan["leader_id"] or user_id in clan["deputies"]:
        return True
    return member is not None and clan_members.is_admin(guild, member)


class CardView(discord.ui.View):
    """Под карточкой (видна только нажавшему): «Респекты подробно» и «Стиль карточки» (глава/замы)."""

    def __init__(self, tag, can_apply=False, can_restyle=False):
        super().__init__(timeout=600)
        self.tag = tag
        if not can_restyle:
            self.remove_item(self.style)

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

    @discord.ui.button(label="Стиль карточки", emoji="🎨", style=discord.ButtonStyle.primary)
    async def style(self, interaction, button):
        import clan_art
        await interaction.response.defer(ephemeral=True, thinking=True)
        sheet = await clan_art.file(clan_art.preview_sheet, filename="styles.png")
        view = StyleView(self.tag)
        await interaction.followup.send(t("clan_hub.style_pick"), file=sheet, view=view, ephemeral=True) \
            if sheet else await interaction.followup.send(t("clan_hub.style_pick"), view=view, ephemeral=True)


class StyleView(discord.ui.View):
    """10 стилей карточки из макетов; выбор сохраняется для клана."""

    def __init__(self, tag):
        super().__init__(timeout=600)
        import clan_art
        self.tag = tag
        select = discord.ui.Select(placeholder=t("clan_hub.style_placeholder")[:150], options=[
            discord.SelectOption(label=f"{number}. {title}"[:100], value=str(number))
            for number, title in clan_art.style_names().items()])

        async def picked(interaction):
            import clan_life
            import clans
            scope_id = ecosystem.scope(interaction.guild.id)
            clan = clans.get_clan(scope_id, self.tag)
            if clan is None or not can_style(clan, interaction.user.id, interaction.guild, interaction.user):
                await reply(interaction, "clan_hub.style_denied")
                return
            number = int(select.values[0])
            clan_life.set_setting(scope_id, f"card_style:{self.tag}", number)
            core.audit(interaction, "clan.card_style", "clan", self.tag, str(number))
            await interaction.response.defer()
            picture = await card_file(interaction.guild, clan, number)
            kwargs = {"attachments": [picture]} if picture else {}
            await interaction.edit_original_response(
                content=t("clan_hub.style_saved", style=clan_art.style_names()[number]), view=None, **kwargs)
        select.callback = picked
        self.add_item(select)


async def show_card(interaction, clan, edit=False):
    """Карточка клана картинкой (стиль клана) + текст; видна только нажавшему."""
    restyle = can_style(clan, interaction.user.id, interaction.guild, interaction.user)
    await interaction.response.defer(ephemeral=True, thinking=True)
    picture = await card_file(interaction.guild, clan)
    embed = card_embed(interaction, clan)
    if picture is None:
        embed.set_image(url=None)
    kwargs = {"file": picture} if picture else {}
    await interaction.followup.send(embed=embed, view=CardView(clan["tag"], can_restyle=restyle), ephemeral=True,
                                    **kwargs)


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
    embed = discord.Embed(title=t("clan_hub.hub_title"), color=0x2B2D31,
                          description=t("clan_hub.hub_text", lines="\n".join(lines) or t("clan_hub.hub_empty"))[:4096])
    embed.set_image(url="attachment://clans.png")
    return embed


async def hub_file(scope_id):
    """Картинка «Всё о кланах» (макет clans.directory) с живыми числами кланов и участников."""
    import clan_art
    import clan_members
    shown = visible_clans(scope_id)
    people = sum(len(clan_members.member_ids(scope_id, clan["tag"])) for clan in shown)
    return await clan_art.file(clan_art.render_directory, len(shown), people, filename="clans.png")


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
    picture = await hub_file(scope_id)
    embed = hub_embed(scope_id)
    if picture is None:
        embed.set_image(url=None)
    message = await channel.send(embed=embed, view=hub_view(scope_id), **({"file": picture} if picture else {}))
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
        picture = await hub_file(scope_id)
        embed = hub_embed(scope_id)
        if picture is None:
            embed.set_image(url=None)
        await message.edit(embed=embed, view=hub_view(scope_id), attachments=[picture] if picture else [])
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
        import clan_art
        banner = clan_art.art_bytes("clans.council.banner.png")
        extra = {"file": discord.File(io.BytesIO(banner), filename="council.png")} if banner else {}
        await channel.send(t("clan_hub.council_welcome", clans=", ".join(f"[{tag}]" for tag in seats) or "—"),
                           **extra)
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
                                                    view=DirectionsView(back_target=(core.screen(interaction), self),
                                                                        scope_id=ecosystem.scope(interaction.guild.id)))

    @discord.ui.button(label="Новые кланы в списке", emoji="🗓️", style=discord.ButtonStyle.secondary)
    async def hub_days(self, interaction, button):
        if await self._ok(interaction):
            await interaction.response.send_modal(HubDaysModal())


def directions_embed(interaction):
    """Должности глав категорий: за что каждый даёт респект (за .награду в казну); можно несколько."""
    import clan_treasury
    scope_id = ecosystem.scope(interaction.guild.id)
    lines = []
    for giver in positions_map(scope_id):
        found = clan_treasury.giver_directions(scope_id, giver)
        if found:
            lines.append(f"<@{giver}> → " + ", ".join(clan_treasury.DIRECTIONS[d] for d in found))
    return panel_embed(interaction, "clan_hub.directions", hints=False, lines="\n".join(lines) or "—")


def positions_map(scope_id):
    """{id главы: [направления]} — старое «одно направление строкой» читается как список."""
    import clan_life
    raw = clan_life.setting(scope_id, "giver_directions", {}) or {}
    return {giver: ([value] if isinstance(value, str) else list(value or [])) for giver, value in raw.items()}


class DirectionsView(PanelView):
    """Сначала — должность (направление), потом — кто её занимает. У человека может быть несколько должностей."""

    def __init__(self, back_target=None, direction=None, scope_id=None):
        import clan_treasury
        super().__init__(back_target=back_target, timeout=600)
        self.direction = direction
        select = discord.ui.Select(placeholder=t("clan_hub.directions.pick")[:150], options=[
            discord.SelectOption(label=label, value=key, default=key == direction)
            for key, label in clan_treasury.DIRECTIONS.items()])

        async def picked(i):
            await i.response.edit_message(view=DirectionsView(self.back_target, select.values[0],
                                                              ecosystem.scope(i.guild.id)))
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
                scope = ecosystem.scope(i.guild.id)
                mapping = positions_map(scope)
                for user in users.values:
                    held = mapping.setdefault(str(user.id), [])
                    if self.direction not in held:
                        held.append(self.direction)
                clan_life.set_setting(scope, "giver_directions", mapping)
                core.audit(i, "clans.giver_directions", "guild", scope, self.direction)
                await i.response.edit_message(embed=directions_embed(i), view=DirectionsView(self.back_target,
                                                                                             scope_id=scope))
            users.callback = chosen
            self.add_item(users)
        held = [(giver, d) for giver, found in positions_map(scope_id).items() for d in found] if scope_id else []
        if held:
            remove = discord.ui.Select(placeholder=t("clan_hub.directions.remove")[:150], options=[
                discord.SelectOption(label=t("clan_hub.directions.remove_line", direction=clan_treasury.DIRECTIONS.get(
                    d, d), who=_user_label(giver))[:100], value=f"{giver}:{d}") for giver, d in held[:25]])

            async def removed(i):
                import clan_life
                import clan_members
                if not clan_members.is_admin(i.guild, i.user):
                    await reply(i, "common.need_admin")
                    return
                scope = ecosystem.scope(i.guild.id)
                giver, direction = remove.values[0].split(":", 1)
                mapping = positions_map(scope)
                mapping[giver] = [d for d in mapping.get(giver, []) if d != direction]
                if not mapping[giver]:
                    mapping.pop(giver)
                clan_life.set_setting(scope, "giver_directions", mapping)
                core.audit(i, "clans.giver_direction_removed", "guild", scope, f"{giver}:{direction}")
                await i.response.edit_message(embed=directions_embed(i), view=DirectionsView(self.back_target,
                                                                                             scope_id=scope))
            remove.callback = removed
            self.add_item(remove)


_bot = None


def _user_label(user_id):
    user = _bot.get_user(int(user_id)) if _bot is not None else None
    return getattr(user, "display_name", None) or getattr(user, "name", None) or str(user_id)


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
        # пять списков + «Назад» в одно сообщение не влезают — роль дежурных отдельным экраном
        duty = discord.ui.Button(label=t("clan_hub.channels.duty_button")[:80], emoji="👥",
                                 style=discord.ButtonStyle.secondary, row=4)

        async def open_duty(i):
            view = PanelView(back_target=(core.screen(i), self), timeout=600)
            role = discord.ui.RoleSelect(placeholder=t("clan_hub.channels.duty_role")[:150])

            async def role_picked(ri):
                await self.store(ri, "duty_role", role.values[0].id)
            role.callback = role_picked
            view.add_item(role)
            await i.response.edit_message(view=view)
        duty.callback = open_duty
        self.add_item(duty)

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

    @discord.ui.button(label="Панель клана", emoji="🛡️", style=discord.ButtonStyle.secondary)
    async def panel(self, interaction, button):
        import clan_members
        import clans
        clan = clans.get_clan(ecosystem.scope(interaction.guild.id), self.tag)
        if clan is None:
            await reply(interaction, "clan.error.no_clan")
            return
        rank = clan_members.rank_of(clan, interaction.user.id)
        await interaction.response.edit_message(
            embed=clan_members.home_embed(interaction, clan, rank),
            view=clan_members.MyClanView(clan, rank, admin=True, back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Роль клана", emoji="🧭", style=discord.ButtonStyle.secondary)
    async def role(self, interaction, button):
        import clans
        clan = clans.get_clan(ecosystem.scope(interaction.guild.id), self.tag)
        if clan is None:
            await reply(interaction, "clan.error.no_clan")
            return
        text, view = clans.role_question(interaction.guild, clan)
        await interaction.response.send_message(text[:2000], view=view, ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

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
    global _bot
    _bot = bot
    return asyncio.create_task(loop(bot))


DYNAMIC_ITEMS = [HubSelect]
