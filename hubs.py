"""
hubs.py
=======
Команды-«хабы» (docs/STRUCTURE.md): одна команда — один раздел, внутри —
уже существующие экраны.

    /library      сохранённые сообщения и шаблоны
    /interactive  наборы кнопок, меню ролей, создание форм
    /forms        проверка заявок
    /eminemovna   доступ, оформление, веб-панель, вебхуки, стили иконок, бункер
"""

import json

import discord
from discord import app_commands

import core
from core import PanelView, panel_embed, t, get_user_level
from actions import say
from database import (get_forms, get_form, get_form_submissions, count_pending_submissions, get_submission,
                      get_form_status, set_form_status, count_form_submissions)

for _name in ("library", "interactive", "forms", "eminemovna"):
    core.COMMAND_MIN_LEVELS.setdefault(_name, "staff")
# Вебхук — это «писать от любого имени»: по умолчанию admin+, дальше — через
# доступ по ролям/людям (/eminemovna → Доступ).
core.COMMAND_MIN_LEVELS.setdefault("webhook", "admin")


def open_with_back(view, back_target):
    """Чужой экран с кнопкой «Назад» в хаб (у многих экранов её не было — открывались командой)."""
    view.back_target = back_target
    view._insert_back_button()
    return view


async def show(interaction, embed, view):
    await interaction.response.edit_message(embed=embed, view=view)


# ============================================================
# /library
# ============================================================

class LibraryHome(PanelView):
    texts = "library.home"

    @discord.ui.button(label="Сообщения", emoji="📦", style=discord.ButtonStyle.primary)
    async def messages(self, interaction, button):
        from embed_module import SavedBuildsListView
        await show(interaction, panel_embed(interaction, "embed.saved"),
                   SavedBuildsListView(interaction, back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Шаблоны", emoji="📁", style=discord.ButtonStyle.secondary)
    async def templates(self, interaction, button):
        from extended_modules import TemplateHome
        await show(interaction, panel_embed(interaction, "templates.home"),
                   TemplateHome(back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Картинки", emoji="🖼️", style=discord.ButtonStyle.secondary)
    async def images(self, interaction, button):
        import image_library
        await image_library.open_home(interaction, back_target=(core.screen(interaction), self))


# ============================================================
# /interactive
# ============================================================

class InteractiveHome(PanelView):
    texts = "interactive.home"

    def __init__(self, make_button_sets):
        super().__init__()
        self.make_button_sets = make_button_sets

    @discord.ui.button(label="Наборы кнопок", emoji="🔘", style=discord.ButtonStyle.primary)
    async def button_sets(self, interaction, button):
        import actions
        view = open_with_back(self.make_button_sets(interaction.guild.id, interaction.user.id),
                              (core.screen(interaction), self))
        await show(interaction, panel_embed(interaction, "buttons.home", max=actions.MAX_BUTTONS), view)

    @discord.ui.button(label="Меню ролей", emoji="🎭", style=discord.ButtonStyle.secondary)
    async def role_menus(self, interaction, button):
        from extended_modules import SelectHome
        await show(interaction, panel_embed(interaction, "select.home"),
                   SelectHome(back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Формы", emoji="📝", style=discord.ButtonStyle.secondary)
    async def forms(self, interaction, button):
        from extended_modules import FormStartView
        await show(interaction, panel_embed(interaction, "forms.home"),
                   FormStartView(interaction.guild.id, interaction.user.id, back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Паки форм", emoji="🗂️", style=discord.ButtonStyle.secondary)
    async def form_packs(self, interaction, button):
        import form_packs
        await show(interaction, form_packs.home_embed(interaction),
                   form_packs.PacksHome(interaction, back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Цитата дня", emoji="💬", style=discord.ButtonStyle.secondary)
    async def quotes(self, interaction, button):
        import quotes
        await quotes.open_home(interaction, back_target=(core.screen(interaction), self))

    @discord.ui.button(label="Шепталка", emoji="📩", style=discord.ButtonStyle.secondary)
    async def whispers(self, interaction, button):
        import whispers
        await whispers.open_home(interaction, back_target=(core.screen(interaction), self))



# ============================================================
# /pictures — картинки бота отдельно от кнопок и форм
# ============================================================

class PicturesHome(PanelView):
    texts = "pictures.home"
    button_order = ("live_cards", "templates_sheet", "server_banner", "image_library", "variables")

    @discord.ui.button(label="Живые картинки", emoji="🏆", style=discord.ButtonStyle.primary)
    async def live_cards(self, interaction, button):
        import cards
        await show(interaction, cards.home_embed(interaction),
                   cards.CardsHome(interaction, back_target=(core.screen(interaction), self)))

    @discord.ui.button(label="Все шаблоны", emoji="🖼️", style=discord.ButtonStyle.secondary)
    async def templates_sheet(self, interaction, button):
        import cards
        await interaction.response.defer(ephemeral=True, thinking=True)
        files = await cards.templates_sheets()
        await interaction.followup.send(t("pictures.sheet"), files=files, ephemeral=True)

    @discord.ui.button(label="Баннер сервера", emoji="🎨", style=discord.ButtonStyle.secondary)
    async def server_banner(self, interaction, button):
        import server_banner
        await server_banner.open_home(interaction, back_target=(core.screen(interaction), self))

    @discord.ui.button(label="Библиотека картинок", emoji="📚", style=discord.ButtonStyle.secondary)
    async def image_library(self, interaction, button):
        import image_library
        await image_library.open_home(interaction, back_target=(core.screen(interaction), self))

    @discord.ui.button(label="Переменные", emoji="🧩", style=discord.ButtonStyle.secondary)
    async def variables(self, interaction, button):
        import variables
        await show(interaction, variables.library_embed(interaction),
                   variables.VariablesView(back_target=(core.screen(interaction), self)))


# ============================================================
# /forms — проверка заявок
# ============================================================

def reviewable_forms(interaction):
    from extended_modules import can_review
    rows = []
    for form_id, *_ in get_forms(interaction.guild.id):
        form = get_form(form_id)
        if form and can_review(interaction, form):
            rows.append((form, count_pending_submissions(form_id)))
    rows.sort(key=lambda item: -item[1])  # сначала формы, где есть что проверять
    return rows


def split_closed(forms):
    """Закрытые наборы без новых заявок — отдельно, чтобы не копились в общем списке."""
    active = [(form, n) for form, n in forms if n or not get_form_status(form[0])[0]]
    closed = [(form, n) for form, n in forms if not n and get_form_status(form[0])[0]]
    return active, closed


class ReviewHome(PanelView):
    """Формы, заявки по которым ты можешь проверять, — с числом ожидающих."""

    texts = "forms.review_home"

    def __init__(self, interaction, back_target=None, show_closed=False):
        super().__init__(back_target=back_target)
        active, closed = split_closed(reviewable_forms(interaction))
        forms = closed if show_closed else active
        if not show_closed and closed:
            button = discord.ui.Button(label=t("forms.review_home.closed_button", count=len(closed))[:80],
                                       emoji="🔒", style=discord.ButtonStyle.secondary, row=1)

            async def open_closed(i):
                await show(i, panel_embed(i, "forms.review_home.closed"),
                           ReviewHome(i, back_target=(core.screen(i), self), show_closed=True))

            button.callback = open_closed
            self.add_item(button)
        if not forms:
            self.add_item(discord.ui.Button(label=t("forms.review_home.empty")[:80], disabled=True))
            return
        select = core.PagedSelect(
            placeholder=t("forms.review_home.placeholder")[:150],
            options=[discord.SelectOption(
                label=t("forms.review_home.option", name=form[3], pending=pending)[:100], value=str(form[0]),
                emoji="🔒" if get_form_status(form[0])[0] else None,
            ) for form, pending in forms],
        )

        async def picked(i):
            form = get_form(int(select.values[0]))
            await show(i, submissions_embed(i, form), SubmissionListView(i, form[0], back_target=(core.screen(i), self)))

        select.on_pick = picked
        self.add_item(select)


def submissions_embed(interaction, form):
    pending = get_form_submissions(form[0])
    lines = [t("forms.review_list.line", id=sid, user=f"<@{applicant}>", when=f"<t:{created}:R>")
             for sid, applicant, _, created in pending[:15]]
    from database import form_dm_enabled
    closed, limit, _ = get_form_status(form[0])
    status = t("forms.status_line.closed" if closed else "forms.status_line.open",
               count=count_form_submissions(form[0]),
               limit=t("forms.status_line.limit", limit=limit) if limit else "")
    status += "\n" + t("forms.status_line.dm_on" if form_dm_enabled(form) else "forms.status_line.dm_off")
    return panel_embed(interaction, "forms.review_list", form=form[3],
                       list=f"{status}\n\n" + ("\n".join(lines) or t("forms.review_list.empty")))


class SubmissionListView(PanelView):
    texts = "forms.review_list"

    def __init__(self, interaction, form_id, back_target):
        super().__init__(back_target=back_target)
        self.form_id = form_id
        closed = get_form_status(form_id)[0]
        self.toggle.label = t("forms.review_list.reopen" if closed else "forms.review_list.close")[:80]
        self.toggle.style = discord.ButtonStyle.success if closed else discord.ButtonStyle.danger
        pending = get_form_submissions(form_id)
        if not pending:
            return
        select = core.PagedSelect(
            placeholder=t("forms.review_list.placeholder")[:150],
            options=[discord.SelectOption(
                label=t("forms.review_list.option", id=sid, user=_member_name(interaction.guild, applicant))[:100],
                value=str(sid),
            ) for sid, applicant, _, created in pending],
        )

        async def picked(i):
            sid = int(select.values[0])
            await show(i, submission_embed(i, sid), SubmissionReviewView(sid, back_target=(core.screen(i), self)))

        select.on_pick = picked
        self.add_item(select)

    async def _form(self, interaction):
        from extended_modules import can_review
        form = get_form(self.form_id)
        if not form or form[1] != interaction.guild.id or not can_review(interaction, form):
            await say(interaction, "forms.review.no_rights")
            return None
        return form

    async def refresh(self, interaction, form):
        await show(interaction, submissions_embed(interaction, form),
                   SubmissionListView(interaction, self.form_id, back_target=self.back_target))

    @discord.ui.button(label="Закрыть набор", emoji="🔒", style=discord.ButtonStyle.danger, row=2)
    async def toggle(self, interaction, button):
        form = await self._form(interaction)
        if not form:
            return
        closed = not get_form_status(self.form_id)[0]
        set_form_status(self.form_id, closed=closed)
        core.audit(interaction, "form.closed" if closed else "form.reopened", "form", self.form_id)
        await self.refresh(interaction, form)

    @discord.ui.button(label="Лимит заявок", emoji="🔢", style=discord.ButtonStyle.secondary, row=2)
    async def limit(self, interaction, button):
        if await self._form(interaction):
            await interaction.response.send_modal(FormLimitModal(self))

    @discord.ui.button(label="ЛС заявителю", emoji="✉️", style=discord.ButtonStyle.secondary, row=2)
    async def dm(self, interaction, button):
        from database import form_dm_enabled
        form = await self._form(interaction)
        if not form:
            return
        set_form_status(self.form_id, dm_off=form_dm_enabled(form))  # переключить
        await self.refresh(interaction, form)


class FormLimitModal(core.Modal, title="ЛИМИТ ЗАЯВОК"):
    texts = "forms.limit_modal"
    value = discord.ui.TextInput(label="Сколько заявок принять", required=False, max_length=5)

    def __init__(self, list_view):
        super().__init__()
        self.list_view = list_view
        limit = get_form_status(list_view.form_id)[1]
        self.value.default = str(limit) if limit else ""

    async def on_submit(self, interaction):
        form = await self.list_view._form(interaction)
        if not form:
            return
        raw = self.value.value.strip()
        if raw and (not raw.isdigit() or int(raw) < 1):
            await say(interaction, "forms.limit_modal.bad")
            return
        limit = int(raw) if raw else None
        # лимит уже набран — набор закрывается сразу; новый лимит выше — снова открыт
        closed = bool(limit and count_form_submissions(self.list_view.form_id) >= limit)
        set_form_status(self.list_view.form_id, closed=closed if limit else None, max_submissions=limit)
        await self.list_view.refresh(interaction, form)


def _member_name(guild, user_id):
    member = guild.get_member(user_id)
    return member.display_name if member else str(user_id)


def submission_embed(interaction, sid):
    row = get_submission(sid)
    form = get_form(row[1]) if row else None
    embed = panel_embed(interaction, "forms.review_card", form=form[3] if form else "?",
                        user=f"<@{row[3]}>" if row else "?", when=f"<t:{row[8]}:f>" if row else "?")
    try:
        answers = json.loads(row[4] or "{}") if row else {}
    except (TypeError, ValueError):
        answers = {}
    for question, answer in list(answers.items())[:20]:
        embed.add_field(name=str(question)[:256] or "​", value=str(answer)[:1024] or "​", inline=False)
    return embed


class SubmissionReviewView(PanelView):
    texts = "forms.review_card"

    def __init__(self, sid, back_target):
        super().__init__(back_target=back_target)
        self.sid = sid

    @discord.ui.button(label="Одобрить", emoji="✅", style=discord.ButtonStyle.success)
    async def approve(self, interaction, button):
        from extended_modules import decide
        await decide(interaction, self.sid, "approved", "", None, panel_back=self.back_target)

    @discord.ui.button(label="Отклонить", emoji="❌", style=discord.ButtonStyle.danger)
    async def reject(self, interaction, button):
        from extended_modules import RejectReasonModal
        await interaction.response.send_modal(RejectReasonModal(self.sid, None, panel_back=self.back_target))


# ============================================================
# /eminemovna — админка
# ============================================================

class EminemovnaHome(PanelView):
    texts = "eminemovna.home"
    button_order = ("access", "design", "logo_styles", "web_panel", "bunker")

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    def back(self, interaction):
        return (core.screen(interaction), self)

    @discord.ui.button(label="Доступ", emoji="🛡️", style=discord.ButtonStyle.primary)
    async def access(self, interaction, button):
        from access_module import AccessHomeView, level_label
        if not core.can_view_access(interaction):
            await say(interaction, "text_access_denied")
            return
        view = open_with_back(AccessHomeView(self.bot), self.back(interaction))
        await show(interaction, panel_embed(interaction, "access.home", level=level_label(get_user_level(interaction))), view)

    @discord.ui.button(label="Оформление", emoji="🎨", style=discord.ButtonStyle.secondary)
    async def design(self, interaction, button):
        from access_module import DesignView, design_embed
        if not core.is_owner(interaction):
            await say(interaction, "common.owner_only")
            return
        await show(interaction, design_embed(interaction), DesignView(back_target=self.back(interaction)))

    @discord.ui.button(label="Стили иконок", emoji="🖌️", style=discord.ButtonStyle.secondary)
    async def logo_styles(self, interaction, button):
        from logo_module import StylesHome, can_manage_styles
        if not can_manage_styles(interaction):
            await say(interaction, "common.need_admin")
            return
        view = open_with_back(StylesHome(interaction), self.back(interaction))
        await show(interaction, panel_embed(interaction, "logo.styles"), view)

    @discord.ui.button(label="Веб-панель", emoji="🌐", style=discord.ButtonStyle.secondary)
    async def web_panel(self, interaction, button):
        import web_panel
        await web_panel.send_panel_link(interaction)

    @discord.ui.button(label="Бункер", emoji="⚙️", style=discord.ButtonStyle.secondary)
    async def bunker(self, interaction, button):
        from access_module import SettingsView
        if not core.is_owner(interaction):
            await say(interaction, "common.owner_only")
            return
        await show(interaction, panel_embed(interaction, "access.settings"),
                   SettingsView(self.bot, back_target=self.back(interaction)))


# ============================================================
# /webhook — создать вебхук и получить ссылку
# ============================================================

WEBHOOK_AVATAR_MAX = 8 * 1024 * 1024


async def create_webhook_for(interaction, channel, name, avatar):
    from database import save_webhook
    from extended_modules import is_admin
    # постоянный вебхук = писать в канал от любого имени: только тем, кто и в
    # Discord может управлять вебхуками в этом канале (или админам бота)
    if not core.can_post_in(interaction.user, channel) or not (
            is_admin(interaction) or channel.permissions_for(interaction.user).manage_webhooks):
        await say(interaction, "live.error.webhook_no_rights", channel=channel.mention)
        return
    if not channel.permissions_for(interaction.guild.me).manage_webhooks:
        await say(interaction, "webhook_cmd.bot_rights", channel=channel.mention)
        return
    data = None
    if avatar is not None:
        if not (avatar.content_type or "").startswith("image/") or avatar.size > WEBHOOK_AVATAR_MAX:
            await say(interaction, "embed.webhook_modal.bad_avatar")
            return
        data = await avatar.read()
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        webhook = await channel.create_webhook(name=name[:80], avatar=data, reason=f"/webhook by {interaction.user.id}")
    except discord.HTTPException as error:
        await interaction.followup.send(t("live.error.webhook_create", error=error), ephemeral=True)
        return
    record_id = save_webhook(interaction.guild.id, interaction.user.id, webhook.id, channel.id, webhook.name,
                             webhook.url, None)
    core.audit(interaction, "webhook.created", "webhook", record_id, f"channel={channel.id}")
    await interaction.followup.send(
        t("webhook_cmd.created", name=webhook.name, channel=channel.mention, url=webhook.url), ephemeral=True,
    )


# ============================================================
# РЕГИСТРАЦИЯ
# ============================================================

def register_hubs(bot, make_button_sets):
    from logo_module import style_autocomplete, add_references_from

    @bot.tree.command(name="library", description="Сохранённые сообщения и шаблоны")
    @app_commands.guild_only()
    async def library(interaction):
        if not await core.require_command_access(interaction, "library"):
            return
        await interaction.response.send_message(embed=panel_embed(interaction, "library.home"),
                                                view=LibraryHome(), ephemeral=True)

    @bot.tree.command(name="interactive", description="Кнопки, меню ролей и формы-заявки")
    @app_commands.guild_only()
    async def interactive(interaction):
        if not await core.require_command_access(interaction, "interactive"):
            return
        await interaction.response.send_message(embed=panel_embed(interaction, "interactive.home"),
                                                view=InteractiveHome(make_button_sets), ephemeral=True)

    @bot.tree.command(name="pictures", description="Живые картинки, баннер сервера и библиотека картинок")
    @app_commands.guild_only()
    async def pictures(interaction):
        if not await core.require_command_access(interaction, "pictures"):
            return
        await interaction.response.send_message(embed=panel_embed(interaction, "pictures.home"),
                                                view=PicturesHome(), ephemeral=True)

    @bot.tree.command(name="forms", description="Проверка заявок по формам")
    @app_commands.guild_only()
    async def forms(interaction):
        if not await core.require_command_access(interaction, "forms"):
            return
        await interaction.response.send_message(embed=panel_embed(interaction, "forms.review_home"),
                                                view=ReviewHome(interaction), ephemeral=True)

    @bot.tree.command(name="webhook", description="Создать вебхук и получить его ссылку (или открыть список)")
    @app_commands.guild_only()
    @app_commands.describe(channel="Канал вебхука", name="Имя, от которого он пишет",
                           avatar="Аватарка (картинка)")
    async def webhook(interaction, channel: discord.TextChannel = None, name: app_commands.Range[str, 1, 80] = None,
                      avatar: discord.Attachment = None):
        if not await core.require_command_access(interaction, "webhook"):
            return
        if channel is None:
            from extended_modules import WebhookHome
            await interaction.response.send_message(embed=panel_embed(interaction, "webhooks.home"),
                                                    view=WebhookHome(), ephemeral=True)
            return
        await create_webhook_for(interaction, channel, name or interaction.guild.me.display_name, avatar)

    @bot.tree.command(name="eminemovna", description="Настройки бота: доступ, оформление, вебхуки, стили иконок")
    @app_commands.guild_only()
    @app_commands.describe(file="Референсы для стиля иконок: картинка или zip", style="Стиль иконок для референсов")
    @app_commands.autocomplete(style=style_autocomplete)
    async def eminemovna(interaction, file: discord.Attachment = None, style: int = None):
        if not await core.require_command_access(interaction, "eminemovna"):
            return
        if file is not None:
            await add_references_from(interaction, style, file)
            return
        await interaction.response.send_message(embed=panel_embed(interaction, "eminemovna.home"),
                                                view=EminemovnaHome(bot), ephemeral=True)
