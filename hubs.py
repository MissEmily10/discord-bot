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
from database import get_forms, get_form, get_form_submissions, count_pending_submissions, get_submission

for _name in ("library", "interactive", "forms", "eminemovna"):
    core.COMMAND_MIN_LEVELS.setdefault(_name, "staff")


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


class ReviewHome(PanelView):
    """Формы, заявки по которым ты можешь проверять, — с числом ожидающих."""

    texts = "forms.review_home"

    def __init__(self, interaction, back_target=None):
        super().__init__(back_target=back_target)
        forms = reviewable_forms(interaction)
        if not forms:
            self.add_item(discord.ui.Button(label=t("forms.review_home.empty")[:80], disabled=True))
            return
        select = discord.ui.Select(
            placeholder=t("forms.review_home.placeholder")[:150],
            options=[discord.SelectOption(
                label=t("forms.review_home.option", name=form[3], pending=pending)[:100], value=str(form[0]),
            ) for form, pending in forms[:25]],
        )

        async def picked(i):
            form = get_form(int(select.values[0]))
            await show(i, submissions_embed(i, form), SubmissionListView(i, form[0], back_target=(core.screen(i), self)))

        select.callback = picked
        self.add_item(select)


def submissions_embed(interaction, form):
    pending = get_form_submissions(form[0])
    lines = [t("forms.review_list.line", id=sid, user=f"<@{applicant}>", when=f"<t:{created}:R>")
             for sid, applicant, _, created in pending[:15]]
    return panel_embed(interaction, "forms.review_list", form=form[3],
                       list="\n".join(lines) or t("forms.review_list.empty"))


class SubmissionListView(PanelView):
    def __init__(self, interaction, form_id, back_target):
        super().__init__(back_target=back_target)
        pending = get_form_submissions(form_id)
        if not pending:
            return
        select = discord.ui.Select(
            placeholder=t("forms.review_list.placeholder")[:150],
            options=[discord.SelectOption(
                label=t("forms.review_list.option", id=sid, user=_member_name(interaction.guild, applicant))[:100],
                value=str(sid),
            ) for sid, applicant, _, created in pending],
        )

        async def picked(i):
            sid = int(select.values[0])
            await show(i, submission_embed(i, sid), SubmissionReviewView(sid, back_target=(core.screen(i), self)))

        select.callback = picked
        self.add_item(select)


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
        await decide(interaction, self.sid, "approved", "", None)

    @discord.ui.button(label="Отклонить", emoji="❌", style=discord.ButtonStyle.danger)
    async def reject(self, interaction, button):
        from extended_modules import RejectReasonModal
        await interaction.response.send_modal(RejectReasonModal(self.sid, None))


# ============================================================
# /eminemovna — админка
# ============================================================

class EminemovnaHome(PanelView):
    texts = "eminemovna.home"
    button_order = ("access", "design", "webhooks", "logo_styles", "web_panel", "bunker")

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

    @discord.ui.button(label="Вебхуки", emoji="🪝", style=discord.ButtonStyle.secondary)
    async def webhooks(self, interaction, button):
        from extended_modules import WebhookHome
        if core.level_value(get_user_level(interaction)) < core.level_value("admin"):
            await say(interaction, "common.need_admin")
            return
        await show(interaction, panel_embed(interaction, "webhooks.home"), WebhookHome(back_target=self.back(interaction)))

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

    @bot.tree.command(name="forms", description="Проверка заявок по формам")
    @app_commands.guild_only()
    async def forms(interaction):
        if not await core.require_command_access(interaction, "forms"):
            return
        await interaction.response.send_message(embed=panel_embed(interaction, "forms.review_home"),
                                                view=ReviewHome(interaction), ephemeral=True)

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
