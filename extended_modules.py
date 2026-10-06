"""
extended_modules.py
===================
/forms, /templates, /webhooks, /select (меню ролей).

Права:
- формы/шаблоны видны по общему правилу actions.can_view (создатель, админ,
  public, разрешённые роли); править и удалять — создатель или админ;
- роли, которые выдаёт форма или меню ролей, проверяются core.role_problem
  при создании И при каждом применении (права роли могли измениться);
- вебхуками управляет их создатель или админ; создать вебхук можно только
  в канале, где у человека самого есть Manage Webhooks.
Заявки, кнопки подачи и меню ролей — постоянные компоненты (переживают рестарт).
"""

import json

import discord

import core
import live
from core import PanelView, Modal, t, panel_embed, embed_color, fetch_bytes, get_user_level
from actions import say, normalize_url, render_source, template_visible, form_visible
from database import (
    save_form, get_form, get_forms, delete_form, save_submission, get_submission,
    get_pending_submission, review_submission,
    save_template, get_template, get_templates, delete_template,
    is_template_favorite, set_user_template_favorite, get_user_favorite_template_ids,
    save_webhook, get_webhook, get_webhooks, update_webhook_record, delete_webhook_record,
    log_webhook_event, get_webhook_history,
    save_role_menu, get_role_menu, get_role_menus, delete_role_menu,
)

MAX_QUESTIONS = 5


def j(value, default):
    try:
        result = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default
    return default if result is None else result


def parse_ids(text):
    return [int(x.strip()) for x in (text or "").split(",") if x.strip().isdigit()]


def is_admin(interaction):
    return core.is_owner(interaction) or core.level_value(get_user_level(interaction)) >= core.level_value("admin")


def owns_or_admin(interaction, owner_id):
    return interaction.user.id == owner_id or is_admin(interaction)


# ============================================================
# FORMS
# ============================================================

class FormState:
    def __init__(self, guild_id, owner_id):
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.name = t("forms.default_name")
        self.description = ""
        self.questions = []
        self.visibility = "public"
        self.category = "roles"
        self.allowed_roles = []
        self.destination = None
        self.reviewer_roles = []
        self.reviewer_users = []
        self.post_action = "review"
        self.post_role = None
        self.target_role = None
        self.dm_applicant = True


def form_builder_embed(i, s):
    qs = "\n".join(
        t("forms.builder.question_line", n=n, label=q["label"], type=q["type"],
          required=t("forms.question.required") if q["required"] else t("forms.question.optional"))
        for n, q in enumerate(s.questions, 1)
    ) or t("forms.builder.no_questions")
    return panel_embed(
        i, "forms.builder",
        name=s.name, category=s.category, visibility=s.visibility,
        destination=f"<#{s.destination}>" if s.destination else t("forms.builder.channel_unset"),
        reviewer_roles=len(s.reviewer_roles), reviewer_users=len(s.reviewer_users),
        post_action=s.post_action,
        post_role=f"<@&{s.post_role}>" if s.post_role else t("forms.builder.role_unset"),
        dm=t("common.yes") if s.dm_applicant else t("common.no"),
        questions=qs,
    )


class FormBasicModal(Modal, title="ОСНОВНЫЕ ДАННЫЕ ФОРМЫ"):
    texts = "forms.basic_modal"
    name = discord.ui.TextInput(label="Название", max_length=100)
    description = discord.ui.TextInput(label="Описание", style=discord.TextStyle.paragraph, required=False, max_length=1000)
    category = discord.ui.TextInput(label="Категория", max_length=50)
    visibility = discord.ui.TextInput(label="private/public", max_length=7)
    roles = discord.ui.TextInput(label="Разрешённые role ID через запятую", required=False, max_length=1000)

    def __init__(self, state, form_view):
        super().__init__()
        self.state = state
        self.form_view = form_view
        self.name.default = state.name
        self.description.default = state.description
        self.category.default = state.category
        self.visibility.default = state.visibility
        self.roles.default = ",".join(map(str, state.allowed_roles))

    async def on_submit(self, interaction):
        v = self.visibility.value.strip().lower()
        if v not in {"private", "public"}:
            await say(interaction, "forms.basic_modal.bad_visibility")
            return
        self.state.name = self.name.value.strip()
        self.state.description = self.description.value.strip()
        self.state.category = self.category.value.strip() or "general"
        self.state.visibility = v
        self.state.allowed_roles = [rid for rid in parse_ids(self.roles.value) if interaction.guild.get_role(rid)]
        await self.form_view.refresh(interaction)


class FormQuestionModal(Modal, title="ВОПРОС ФОРМЫ"):
    texts = "forms.question_modal"
    label = discord.ui.TextInput(label="Вопрос", max_length=45)
    qtype = discord.ui.TextInput(label="short / long / yesno", max_length=10)
    required = discord.ui.TextInput(label="required? yes/no", max_length=3)

    def __init__(self, state, form_view):
        super().__init__()
        self.state = state
        self.form_view = form_view

    async def on_submit(self, interaction):
        qt = self.qtype.value.strip().lower()
        if qt not in {"short", "long", "yesno"}:
            await say(interaction, "forms.question_modal.bad_type")
            return
        if len(self.state.questions) >= MAX_QUESTIONS:
            await say(interaction, "forms.question_modal.too_many", max=MAX_QUESTIONS)
            return
        self.state.questions.append({
            "label": self.label.value.strip(),
            "type": qt,
            "required": self.required.value.strip().lower() in {"yes", "y", "да", "д"},
        })
        await self.form_view.refresh(interaction)


class FormRoutingModal(Modal, title="МАРШРУТИЗАЦИЯ ФОРМЫ"):
    texts = "forms.routing_modal"
    destination = discord.ui.TextInput(label="ID канала заявок", max_length=30)
    reviewer_roles = discord.ui.TextInput(label="Reviewer role IDs через запятую", required=False, max_length=1000)
    reviewer_users = discord.ui.TextInput(label="Reviewer user IDs через запятую", required=False, max_length=1000)
    action = discord.ui.TextInput(label="review / role / none", max_length=10)
    role = discord.ui.TextInput(label="Role ID после одобрения", required=False, max_length=30)

    def __init__(self, state, form_view):
        super().__init__()
        self.state = state
        self.form_view = form_view
        self.destination.default = str(state.destination or "")
        self.reviewer_roles.default = ",".join(map(str, state.reviewer_roles))
        self.reviewer_users.default = ",".join(map(str, state.reviewer_users))
        self.action.default = state.post_action
        self.role.default = str(state.post_role or "")

    async def on_submit(self, interaction):
        action = self.action.value.strip().lower()
        channel_id = int(self.destination.value) if self.destination.value.strip().isdigit() else None
        channel = interaction.guild.get_channel(channel_id) if channel_id else None
        if channel is None or action not in {"review", "role", "none"}:
            await say(interaction, "forms.routing_modal.invalid")
            return
        if not core.bot_can_post(channel):
            await say(interaction, "forms.routing_modal.bot_cannot_post", channel=channel.mention)
            return
        post_role = int(self.role.value) if self.role.value.strip().isdigit() else None
        if post_role is not None:
            problem = core.role_problem(interaction.guild, interaction.guild.get_role(post_role), interaction.user)
            if problem:
                await say(interaction, problem)
                return
        self.state.destination = channel_id
        self.state.reviewer_roles = [rid for rid in parse_ids(self.reviewer_roles.value) if interaction.guild.get_role(rid)]
        self.state.reviewer_users = parse_ids(self.reviewer_users.value)
        self.state.post_action = action
        self.state.post_role = post_role
        await self.form_view.refresh(interaction)


class FormTargetRoleView(PanelView):
    def __init__(self, state, form_view, back_target=None):
        super().__init__(back_target=back_target, timeout=300)
        self.state = state
        self.form_view = form_view
        self.select = discord.ui.RoleSelect(placeholder=t("forms.target_role.placeholder")[:150], min_values=1, max_values=1)
        self.select.callback = self.selected
        self.add_item(self.select)

    async def selected(self, interaction):
        role = self.select.values[0]
        problem = core.role_problem(interaction.guild, role, interaction.user)
        if problem:
            await say(interaction, problem)
            return
        self.state.target_role = role.id
        await interaction.response.edit_message(
            embed=form_builder_embed(interaction, self.state),
            view=FormView(self.state, back_target=self.form_view.back_target),
        )


class FormView(PanelView):
    texts = "forms.builder"

    def __init__(self, state, back_target=None):
        super().__init__(back_target=back_target)
        self.state = state

    async def refresh(self, interaction):
        await interaction.response.edit_message(
            embed=form_builder_embed(interaction, self.state),
            view=FormView(self.state, back_target=self.back_target),
        )

    @discord.ui.button(label="Основные", emoji="✏️", style=discord.ButtonStyle.primary)
    async def basic(self, i, b):
        await i.response.send_modal(FormBasicModal(self.state, self))

    @discord.ui.button(label="Вопрос", emoji="➕", style=discord.ButtonStyle.secondary)
    async def question(self, i, b):
        await i.response.send_modal(FormQuestionModal(self.state, self))

    @discord.ui.button(label="Маршрутизация", emoji="📨", style=discord.ButtonStyle.secondary)
    async def routing(self, i, b):
        await i.response.send_modal(FormRoutingModal(self.state, self))

    @discord.ui.button(label="Роль для заявки", emoji="🎭", style=discord.ButtonStyle.secondary)
    async def target(self, i, b):
        await i.response.edit_message(
            embed=panel_embed(i, "forms.target_role"),
            view=FormTargetRoleView(self.state, self, back_target=(i.message.embeds[0], self)),
        )

    @discord.ui.button(label="Убрать вопрос", emoji="➖", style=discord.ButtonStyle.secondary, row=1)
    async def remove_question(self, i, b):
        if self.state.questions:
            self.state.questions.pop()
        await self.refresh(i)

    @discord.ui.button(label="Уведомлять в ЛС", emoji="✉️", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_dm(self, i, b):
        self.state.dm_applicant = not self.state.dm_applicant
        await self.refresh(i)

    @discord.ui.button(label="Сохранить", emoji="💾", style=discord.ButtonStyle.success, row=1)
    async def save(self, i, b):
        s = self.state
        if not s.questions or not s.destination:
            await say(i, "forms.builder.need_question")
            return
        if s.post_action == "role" and not (s.post_role or s.target_role):
            await say(i, "forms.builder.need_role")
            return
        fid = save_form(
            s.guild_id, i.user.id, s.name, s.description,
            json.dumps(s.questions, ensure_ascii=False), s.visibility, s.category,
            json.dumps(s.allowed_roles), s.destination,
            json.dumps(s.reviewer_roles), json.dumps(s.reviewer_users),
            s.post_action, s.post_role, s.target_role, dm_creator=int(s.dm_applicant),
        )
        core.audit(i, "form.created", "form", fid, s.name)
        role = f"<@&{s.target_role}>" if s.target_role else t("forms.builder.role_unset")
        await i.response.edit_message(
            embed=panel_embed(i, "forms.saved", id=fid, channel=f"<#{s.destination}>", role=role),
            view=FormUseView(fid, back_target=(i.message.embeds[0], self)),
        )


class FormUseView(PanelView):
    texts = "forms.use"

    def __init__(self, form_id, back_target=None):
        super().__init__(back_target=back_target)
        self.form_id = form_id

    @discord.ui.button(label="Подать заявку", emoji="📝", style=discord.ButtonStyle.primary)
    async def apply(self, i, b):
        await open_form(i, self.form_id)

    @discord.ui.button(label="Опубликовать", emoji="📣", style=discord.ButtonStyle.success)
    async def publish(self, i, b):
        row = get_form(self.form_id)
        if not row or row[1] != i.guild.id or not owns_or_admin(i, row[2]):
            await say(i, "forms.manage_denied")
            return
        view = PanelView(timeout=300)
        select = discord.ui.ChannelSelect(
            placeholder=t("embed.send.placeholder")[:150],
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
        )

        async def picked(ci):
            channel = ci.guild.get_channel(select.values[0].id)
            if channel is None or not core.can_post_in(ci.user, channel):
                await say(ci, "embed.send.no_perm_user", channel=f"<#{select.values[0].id}>")
                return
            if not core.bot_can_post(channel):
                await say(ci, "embed.send.no_perm_bot", channel=channel.mention)
                return
            embed = panel_embed(ci, "forms.public_card", title=row[3], description=row[4] or t("forms.no_description"))
            apply_view = discord.ui.View(timeout=None)
            apply_view.add_item(FormApplyButton(self.form_id))
            await channel.send(embed=embed, view=apply_view)
            core.audit(ci, "form.published", "form", self.form_id, f"channel={channel.id}")
            await say(ci, "forms.published", channel=channel.mention)

        select.callback = picked
        view.add_item(select)
        await i.response.send_message(t("embed.send.prompt"), view=view, ephemeral=True)

    @discord.ui.button(label="Удалить форму", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete(self, i, b):
        row = get_form(self.form_id)
        if not row or row[1] != i.guild.id or not owns_or_admin(i, row[2]):
            await say(i, "forms.manage_denied")
            return
        delete_form(self.form_id)
        core.audit(i, "form.deleted", "form", self.form_id, row[3])
        await i.response.edit_message(embed=panel_embed(i, "forms.deleted"), view=None)


class FormApplyButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rb:fa:(?P<fid>\d+)"):
    """Постоянная кнопка подачи заявки в опубликованном сообщении."""

    def __init__(self, form_id, *, item=None):
        super().__init__(item or discord.ui.Button(
            label=t("forms.use.apply")[:80], emoji="📝", style=discord.ButtonStyle.primary,
            custom_id=f"rb:fa:{form_id}",
        ))
        self.form_id = form_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["fid"]), item=item)

    async def callback(self, interaction):
        await open_form(interaction, self.form_id)


async def open_form(interaction, form_id):
    """Единая точка открытия формы: видимость, дубли, наличие канала заявок."""
    row = get_form(form_id)
    if not row or interaction.guild is None or row[1] != interaction.guild.id:
        await say(interaction, "forms.not_found")
        return
    if not form_visible(interaction, row):
        await say(interaction, "forms.unavailable")
        return
    if get_pending_submission(form_id, interaction.user.id):
        await say(interaction, "forms.already_pending")
        return
    if interaction.guild.get_channel(row[9]) is None:
        await say(interaction, "forms.destination_missing")
        return
    await interaction.response.send_modal(SubmissionModal(row))


YES_WORDS = {"yes", "y", "да", "д", "+"}
NO_WORDS = {"no", "n", "нет", "н", "-"}


class SubmissionModal(discord.ui.Modal):
    def __init__(self, row):
        self.row = row
        super().__init__(title=(row[3] or "")[:45] or "FORM")
        self.answer_inputs = []
        self.questions = j(row[5], [])[:MAX_QUESTIONS]
        for q in self.questions:
            qtype = q.get("type")
            field = discord.ui.TextInput(
                label=str(q.get("label") or "?")[:45],
                required=q.get("required", True),
                style=discord.TextStyle.paragraph if qtype == "long" else discord.TextStyle.short,
                max_length=4000 if qtype == "long" else (3 if qtype == "yesno" else 1000),
                placeholder=t("forms.yesno_placeholder")[:100] if qtype == "yesno" else None,
            )
            self.answer_inputs.append(field)
            self.add_item(field)

    async def on_submit(self, i):
        answers = {}
        for q, field in zip(self.questions, self.answer_inputs):
            value = field.value.strip()
            if q.get("type") == "yesno" and value:
                low = value.lower()
                if low in YES_WORDS:
                    value = t("common.yes")
                elif low in NO_WORDS:
                    value = t("common.no")
                else:
                    await say(i, "forms.yesno_invalid", question=q.get("label"))
                    return
            answers[q.get("label")] = value
        channel = i.guild.get_channel(self.row[9])
        if channel is None:
            await say(i, "forms.destination_missing")
            return
        sid = save_submission(self.row[0], i.guild.id, i.user.id, json.dumps(answers, ensure_ascii=False))
        e = panel_embed(i, "forms.submission", form=self.row[3], user=i.user.mention, id=sid)
        for k, v in answers.items():
            e.add_field(name=str(k)[:256], value=str(v)[:1024] or "​", inline=False)
        view = discord.ui.View(timeout=None)
        view.add_item(ReviewButton(sid, "approve"))
        view.add_item(ReviewButton(sid, "reject"))
        try:
            await channel.send(embed=e, view=view, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            review_submission(sid, i.client.user.id, "failed", "destination unavailable")
            await say(i, "forms.destination_missing")
            return
        live.submissions_changed(i.guild)  # {submissions}, {last_submission} в отправленных build'ах
        await say(i, "forms.submitted")


def can_review(i, form):
    if i.user.id == form[2] or is_admin(i):
        return True
    if i.user.id in j(form[11], []):
        return True
    member_roles = {r.id for r in getattr(i.user, "roles", [])}
    return bool(member_roles & set(j(form[10], [])))


class ReviewButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rb:rv:(?P<sid>\d+):(?P<decision>approve|reject)"):
    def __init__(self, sid, decision, *, item=None):
        approve = decision == "approve"
        super().__init__(item or discord.ui.Button(
            label=t("forms.review.approve" if approve else "forms.review.reject")[:80],
            emoji="✅" if approve else "❌",
            style=discord.ButtonStyle.success if approve else discord.ButtonStyle.danger,
            custom_id=f"rb:rv:{sid}:{decision}",
        ))
        self.sid = sid
        self.decision = decision

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["sid"]), match["decision"], item=item)

    async def callback(self, interaction):
        row, form = await _review_context(interaction, self.sid)
        if row is None:
            return
        if self.decision == "reject":
            await interaction.response.send_modal(RejectReasonModal(self.sid, interaction.message))
            return
        await decide(interaction, self.sid, "approved", "", interaction.message)


async def _review_context(i, sid):
    row = get_submission(sid)
    form = get_form(row[1]) if row else None
    if not row or not form or row[2] != i.guild.id:
        await say(i, "forms.review.not_found")
        return None, None
    if not can_review(i, form):
        await say(i, "forms.review.no_rights")
        return None, None
    if row[5] != "pending":
        await say(i, "forms.review.already", status=t(f"forms.status.{row[5]}") if core.has_text(f"forms.status.{row[5]}") else row[5])
        return None, None
    return row, form


class RejectReasonModal(Modal, title="ПРИЧИНА ОТКАЗА"):
    texts = "forms.reject_modal"
    reason = discord.ui.TextInput(label="Причина (увидит заявитель)", required=False, style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, sid, message):
        super().__init__()
        self.sid = sid
        self.message = message

    async def on_submit(self, interaction):
        await decide(interaction, self.sid, "rejected", self.reason.value.strip(), self.message)


async def decide(i, sid, status, reason, message):
    row, form = await _review_context(i, sid)
    if row is None:
        return
    review_submission(sid, i.user.id, status, reason)
    live.submissions_changed(i.guild)
    role_error = None
    role_id = form[13] or form[14]  # роль после одобрения приоритетнее роли, на которую подавали
    if status == "approved" and form[12] == "role" and role_id:
        member = i.guild.get_member(row[3])
        role = i.guild.get_role(role_id)
        creator = i.guild.get_member(form[2])
        problem = core.role_problem(i.guild, role, creator, allow_dangerous=core.is_owner_id(form[2]))
        if member is None:
            role_error = t("forms.review.role_missing")
        elif problem:
            role_error = t(problem)
        else:
            try:
                await member.add_roles(role, reason=f"Form #{form[0]} submission #{sid} approved by {i.user.id}")
            except discord.Forbidden:
                role_error = t("forms.review.role_forbidden")
    core.audit(i, f"form.{status}", "submission", sid, reason or None)

    status_text = t(f"forms.status.{status}")
    result = t("forms.reviewed.text", status=status_text, reviewer=i.user.mention)
    if reason:
        result += "\n" + t("forms.reviewed.reason", reason=reason)
    if role_error:
        result += f"\n\n⚠️ {role_error}"
    embed = message.embeds[0] if message and message.embeds else panel_embed(i, "forms.reviewed")
    embed.add_field(name=t("forms.reviewed.title")[:256], value=result[:1024], inline=False)
    if status == "approved":
        embed.color = discord.Color.green()
    else:
        embed.color = core.danger_color()
    if i.response.is_done():
        await message.edit(embed=embed, view=None)
    else:
        await i.response.edit_message(embed=embed, view=None)

    if form[16]:  # уведомить заявителя
        applicant = i.guild.get_member(row[3])
        if applicant is not None:
            key = "forms.dm.approved" if status == "approved" else "forms.dm.rejected"
            try:
                await applicant.send(t(key, form=form[3], guild=i.guild.name, reason=reason or t("forms.dm.no_reason")))
            except discord.HTTPException:
                pass  # закрытые ЛС — не ошибка


class FormStartView(PanelView):
    texts = "forms.start"

    def __init__(self, guild_id, user_id, back_target=None):
        super().__init__(back_target=back_target)
        self.guild_id = guild_id
        self.user_id = user_id

    @discord.ui.button(label="Создать", emoji="➕", style=discord.ButtonStyle.success)
    async def create(self, i, b):
        state = FormState(self.guild_id, i.user.id)
        await i.response.edit_message(embed=form_builder_embed(i, state), view=FormView(state, back_target=(i.message.embeds[0], self)))

    @discord.ui.button(label="Сохранённые", emoji="📦", style=discord.ButtonStyle.secondary)
    async def saved(self, i, b):
        await i.response.edit_message(embed=panel_embed(i, "forms.list"), view=FormListView(i, back_target=(i.message.embeds[0], self)))


class FormListView(PanelView):
    def __init__(self, interaction, back_target=None):
        super().__init__(back_target=back_target)
        rows = [row for row in get_forms(interaction.guild.id) if form_visible(interaction, get_form(row[0]))]
        for fid, owner, name, visibility, category, roles, updated in rows[:20]:
            b = discord.ui.Button(label=f"{(name or '')[:60]} · #{fid}", style=discord.ButtonStyle.secondary)

            async def cb(i, fid=fid):
                row = get_form(fid)
                if row and form_visible(i, row):
                    await i.response.edit_message(
                        embed=panel_embed(i, "forms.card", title=row[3], description=row[4] or t("forms.no_description")),
                        view=FormUseView(fid, back_target=(i.message.embeds[0], self)),
                    )
                else:
                    await say(i, "forms.unavailable")

            b.callback = cb
            self.add_item(b)
        if not rows:
            self.add_item(discord.ui.Button(label=t("forms.list.empty")[:80], disabled=True))


# ============================================================
# TEMPLATES
# ============================================================

class TemplateModal(Modal, title="СОХРАНИТЬ ШАБЛОН"):
    texts = "templates.modal"
    name = discord.ui.TextInput(label="Название", max_length=100)
    typ = discord.ui.TextInput(label="message / buttons / form", max_length=20)
    category_visibility = discord.ui.TextInput(label="Категория/видимость (general/public)", max_length=60)
    roles = discord.ui.TextInput(label="Role IDs через запятую", required=False, max_length=1000)
    payload = discord.ui.TextInput(label="JSON payload", style=discord.TextStyle.paragraph, max_length=4000)

    async def on_submit(self, i):
        typ = self.typ.value.strip().lower()
        category, _, vis = self.category_visibility.value.partition("/")
        category = category.strip() or "general"
        vis = vis.strip().lower() or "private"
        if typ not in {"message", "buttons", "form"} or vis not in {"private", "public"}:
            await say(i, "templates.modal.bad_type")
            return
        try:
            payload = json.loads(self.payload.value)
        except json.JSONDecodeError:
            await say(i, "templates.modal.bad_json")
            return
        if not isinstance(payload, dict):
            await say(i, "templates.modal.bad_json")
            return
        if typ == "form":
            form = get_form(int(payload.get("form_id"))) if str(payload.get("form_id", "")).isdigit() else None
            if not form or form[1] != i.guild.id:
                await say(i, "templates.modal.bad_form")
                return
        roles = [rid for rid in parse_ids(self.roles.value) if i.guild.get_role(rid)]
        tid = save_template(i.guild.id, i.user.id, self.name.value.strip(), typ, json.dumps(payload, ensure_ascii=False), vis, category, json.dumps(roles))
        core.audit(i, "template.created", "template", tid, self.name.value.strip())
        await say(i, "templates.modal.saved", id=tid)


def template_card_embed(i, row):
    return panel_embed(
        i, "templates.card", title=row[3],
        type=row[4], category=row[7], visibility=row[6],
        favorite=t("common.yes") if is_template_favorite(i.user.id, row[0]) else t("common.no"),
    )


class TemplateListView(PanelView):
    def __init__(self, interaction, back_target=None):
        super().__init__(back_target=back_target)
        favorites = get_user_favorite_template_ids(interaction.user.id)
        rows = [row for row in get_templates(interaction.guild.id) if template_visible(interaction, get_template(row[0]))]
        rows.sort(key=lambda row: row[0] not in favorites)  # избранные — первыми
        for tid, owner, name, typ, *_ in rows[:20]:
            marker = " ★" if tid in favorites else ""
            b = discord.ui.Button(label=f"{(name or '')[:48]}{marker} · {typ}", style=discord.ButtonStyle.secondary)

            async def cb(i, tid=tid):
                row = get_template(tid)
                if not row or not template_visible(i, row):
                    await say(i, "templates.unavailable")
                    return
                await i.response.edit_message(embed=template_card_embed(i, row), view=TemplateActions(tid, back_target=(i.message.embeds[0], self)))

            b.callback = cb
            self.add_item(b)
        if not rows:
            self.add_item(discord.ui.Button(label=t("templates.list.empty")[:80], disabled=True))


class TemplateActions(PanelView):
    texts = "templates.card"

    def __init__(self, tid, back_target=None):
        super().__init__(back_target=back_target)
        self.tid = tid

    async def _row(self, i):
        row = get_template(self.tid)
        if not row or row[1] != i.guild.id or not template_visible(i, row):
            await say(i, "templates.not_found")
            return None
        return row

    @discord.ui.button(label="Использовать", emoji="▶️", style=discord.ButtonStyle.success)
    async def use(self, i, b):
        row = await self._row(i)
        if not row:
            return
        if row[4] == "form":
            payload = j(row[5], {})
            form_id = payload.get("form_id") if isinstance(payload, dict) else None
            if not str(form_id or "").isdigit():
                await say(i, "templates.form_hint")
                return
            await open_form(i, int(form_id))
            return
        content, embeds, view = render_source("t", self.tid)
        if row[4] == "buttons" and not embeds:
            embeds = [panel_embed(i, "templates.button_set", title=row[3])]
        if not content and not embeds and view is None:
            await say(i, "templates.empty")
            return
        await i.response.send_message(content=content, embeds=embeds, view=view, ephemeral=True,
                                      allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="В редактор", emoji="✏️", style=discord.ButtonStyle.primary)
    async def to_editor(self, i, b):
        row = await self._row(i)
        if not row:
            return
        if row[4] != "message" or not core.has_command_access(i, "embed"):
            await say(i, "templates.editor_unavailable")
            return
        from embed_module import EmbedState, EmbedEditorView, render_active_preview

        state = EmbedState(i.guild.id, i.user.id)
        state.load_payload(j(row[5], {}))
        state.name = row[3]
        await i.response.edit_message(embed=render_active_preview(state), view=EmbedEditorView(state, back_target=(i.message.embeds[0], self)))

    @discord.ui.button(label="Избранное", emoji="⭐", style=discord.ButtonStyle.secondary)
    async def favorite(self, i, b):
        row = await self._row(i)
        if not row:
            return
        set_user_template_favorite(i.user.id, self.tid, not is_template_favorite(i.user.id, self.tid))
        await i.response.edit_message(embed=template_card_embed(i, row), view=TemplateActions(self.tid, back_target=self.back_target))

    @discord.ui.button(label="Удалить", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete(self, i, b):
        row = await self._row(i)
        if not row:
            return
        if not owns_or_admin(i, row[2]):
            await say(i, "templates.delete_denied")
            return
        delete_template(self.tid, row[2])
        core.audit(i, "template.deleted", "template", self.tid, row[3])
        await i.response.edit_message(embed=panel_embed(i, "templates.deleted"), view=None)


class TemplateHome(PanelView):
    texts = "templates.home"

    def __init__(self, back_target=None):
        super().__init__(back_target=back_target)

    @discord.ui.button(label="Список", emoji="📚", style=discord.ButtonStyle.primary)
    async def list(self, i, b):
        await i.response.edit_message(embed=panel_embed(i, "templates.list"), view=TemplateListView(i, back_target=(i.message.embeds[0], self)))

    @discord.ui.button(label="Сохранить JSON", emoji="💾", style=discord.ButtonStyle.secondary)
    async def save(self, i, b):
        await i.response.send_modal(TemplateModal())


# ============================================================
# WEBHOOKS
# ============================================================

def can_manage_webhook(i, row):
    return row is not None and row[1] == i.guild.id and owns_or_admin(i, row[2])


async def _webhook_row(i, rid):
    row = get_webhook(rid)
    if not can_manage_webhook(i, row):
        await say(i, "webhooks.access_denied")
        return None
    return row


class WebhookCreateModal(Modal, title="СОЗДАТЬ WEBHOOK"):
    texts = "webhooks.create_modal"
    name = discord.ui.TextInput(label="Название", max_length=80)
    avatar = discord.ui.TextInput(label="Avatar URL", required=False, max_length=1000)

    async def on_submit(self, i):
        await i.response.send_message(t("webhooks.pick_channel"), view=WebhookChannelView(self.name.value.strip(), self.avatar.value.strip()), ephemeral=True)


class WebhookChannelView(PanelView):
    def __init__(self, name, avatar, back_target=None):
        super().__init__(back_target=back_target, timeout=300)
        self.name = name
        self.avatar = avatar
        self.select = discord.ui.ChannelSelect(placeholder=t("webhooks.channel_placeholder")[:150], channel_types=[discord.ChannelType.text, discord.ChannelType.news])
        self.select.callback = self.selected
        self.add_item(self.select)

    async def selected(self, i):
        picked = self.select.values[0]
        ch = i.guild.get_channel(picked.id)
        # создавать вебхук через бота можно только там, где у человека самого есть Manage Webhooks
        if ch is None or not (is_admin(i) or ch.permissions_for(i.user).manage_webhooks):
            await say(i, "webhooks.need_user_manage")
            return
        if not ch.permissions_for(i.guild.me).manage_webhooks:
            await say(i, "webhooks.need_manage")
            return
        await i.response.defer(ephemeral=True)
        avatar = await fetch_bytes(normalize_url(self.avatar)) if self.avatar else None
        try:
            wh = await ch.create_webhook(name=self.name, avatar=avatar, reason=f"Webhook Manager ({i.user.id})")
        except discord.HTTPException as e:
            await i.followup.send(t("webhooks.discord_error", error=e), ephemeral=True)
            return
        rid = save_webhook(i.guild.id, i.user.id, wh.id, ch.id, wh.name, wh.url, normalize_url(self.avatar))
        log_webhook_event(i.guild.id, i.user.id, wh.id, "created", ch.id)
        core.audit(i, "webhook.created", "webhook", wh.id)
        await i.edit_original_response(
            content=None,
            embed=panel_embed(i, "webhooks.created", channel=ch.mention, name=wh.name, id=wh.id),
            view=WebhookActions(rid),
        )


class WebhookListView(PanelView):
    def __init__(self, interaction, back_target):
        super().__init__(back_target=back_target)
        rows = get_webhooks(interaction.guild.id, None if is_admin(interaction) else interaction.user.id)
        for rid, owner, wid, cid, name, avatar, created, updated in rows[:20]:
            b = discord.ui.Button(label=(name or "")[:70], style=discord.ButtonStyle.secondary)

            async def cb(i, rid=rid):
                row = await _webhook_row(i, rid)
                if row:
                    await i.response.edit_message(
                        embed=panel_embed(i, "webhooks.card", name=row[5], channel=f"<#{row[4]}>", id=row[3]),
                        view=WebhookActions(rid, back_target=(i.message.embeds[0], self)),
                    )

            b.callback = cb
            self.add_item(b)
        if not rows:
            self.add_item(discord.ui.Button(label=t("webhooks.list.empty")[:80], disabled=True))


class WebhookActions(PanelView):
    texts = "webhooks.card"

    def __init__(self, rid, back_target=None):
        super().__init__(back_target=back_target)
        self.rid = rid

    @discord.ui.button(label="Показать URL", emoji="🔑", style=discord.ButtonStyle.secondary)
    async def show(self, i, b):
        row = await _webhook_row(i, self.rid)
        if row:
            core.audit(i, "webhook.url_shown", "webhook", row[3])
            await say(i, "webhooks.url", url=row[6])

    @discord.ui.button(label="Отправить", emoji="📨", style=discord.ButtonStyle.primary)
    async def send(self, i, b):
        if await _webhook_row(i, self.rid):
            await i.response.send_modal(WebhookMessageModal(self.rid))

    @discord.ui.button(label="Переименовать", emoji="✏️", style=discord.ButtonStyle.secondary)
    async def rename(self, i, b):
        if await _webhook_row(i, self.rid):
            await i.response.send_modal(WebhookRenameModal(self.rid))

    @discord.ui.button(label="Аватар", emoji="🖼️", style=discord.ButtonStyle.secondary)
    async def avatar(self, i, b):
        if await _webhook_row(i, self.rid):
            await i.response.send_modal(WebhookAvatarModal(self.rid))

    @discord.ui.button(label="Тест", emoji="🧪", style=discord.ButtonStyle.success)
    async def test(self, i, b):
        row = await _webhook_row(i, self.rid)
        if not row:
            return
        try:
            wh = await i.client.fetch_webhook(row[3])
            await wh.send(t("webhooks.test_message"), wait=False)
            log_webhook_event(i.guild.id, i.user.id, row[3], "test", row[4])
            await say(i, "webhooks.test_sent")
        except discord.HTTPException as e:
            await say(i, "webhooks.error", error=e)

    @discord.ui.button(label="История", emoji="📜", style=discord.ButtonStyle.secondary, row=1)
    async def history(self, i, b):
        row = await _webhook_row(i, self.rid)
        if not row:
            return
        rows = get_webhook_history(i.guild.id, row[3])
        text = "\n".join(f"<t:{c}:R> · <@{o}> · `{a}` · {d or ''}" for o, _, a, _, d, c in rows) or t("webhooks.history.empty")
        await i.response.send_message(embed=panel_embed(i, "webhooks.history", description=text[:4000]), ephemeral=True)

    @discord.ui.button(label="Удалить", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def delete(self, i, b):
        if await _webhook_row(i, self.rid):
            await i.response.send_message(t("webhooks.delete.confirm"), view=WebhookDeleteView(self.rid), ephemeral=True)

    @discord.ui.button(label="Пересоздать URL", emoji="♻️", style=discord.ButtonStyle.danger, row=1)
    async def regenerate(self, i, b):
        if await _webhook_row(i, self.rid):
            await i.response.send_message(t("webhooks.regenerate.confirm"), view=WebhookRegenerateView(self.rid), ephemeral=True)


class WebhookDeleteView(PanelView):
    texts = "webhooks.delete"

    def __init__(self, rid, back_target=None):
        super().__init__(back_target=back_target, timeout=300)
        self.rid = rid

    @discord.ui.button(label="Удалить", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def yes(self, i, b):
        row = await _webhook_row(i, self.rid)
        if not row:
            return
        try:
            wh = await i.client.fetch_webhook(row[3])
            await wh.delete(reason=f"Webhook Manager ({i.user.id})")
        except discord.NotFound:
            pass  # уже удалён в Discord — просто чистим запись
        except discord.HTTPException as e:
            await say(i, "webhooks.error", error=e)
            return
        log_webhook_event(i.guild.id, i.user.id, row[3], "deleted", row[4])
        core.audit(i, "webhook.deleted", "webhook", row[3])
        delete_webhook_record(self.rid)
        await i.response.edit_message(content=t("webhooks.delete.done"), view=None)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, i, b):
        await i.response.edit_message(content=t("common.cancelled"), view=None)


class WebhookRegenerateView(PanelView):
    texts = "webhooks.regenerate"

    def __init__(self, rid, back_target=None):
        super().__init__(back_target=back_target, timeout=300)
        self.rid = rid

    @discord.ui.button(label="Да, пересоздать", emoji="♻️", style=discord.ButtonStyle.danger)
    async def yes(self, i, b):
        row = await _webhook_row(i, self.rid)
        if not row:
            return
        ch = i.guild.get_channel(row[4])
        if ch is None:
            await say(i, "webhooks.not_found")
            return
        try:
            old = await i.client.fetch_webhook(row[3])
            avatar = await old.avatar.read() if old.avatar else None
            await old.delete(reason="Webhook URL regeneration")
            new = await ch.create_webhook(name=row[5], avatar=avatar, reason="Webhook URL regeneration")
        except discord.HTTPException as e:
            await say(i, "webhooks.error", error=e)
            return
        update_webhook_record(self.rid, name=new.name, channel_id=ch.id, url=new.url, webhook_id=new.id)
        log_webhook_event(i.guild.id, i.user.id, new.id, "regenerated", ch.id, f"old_webhook_id={row[3]}")
        core.audit(i, "webhook.regenerated", "webhook", new.id)
        await i.response.edit_message(content=t("webhooks.regenerate.done"), view=None)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, i, b):
        await i.response.edit_message(content=t("common.cancelled"), view=None)


class WebhookMessageModal(Modal, title="WEBHOOK MESSAGE"):
    texts = "webhooks.message_modal"
    # поле embed_title, а не title: атрибут title занят заголовком самой модалки,
    # и TextInput с таким именем молча пропадал из формы.
    text = discord.ui.TextInput(label="Текст", required=False, style=discord.TextStyle.paragraph, max_length=2000)
    embed_title = discord.ui.TextInput(label="Embed title", required=False, max_length=256)
    description = discord.ui.TextInput(label="Embed description", required=False, style=discord.TextStyle.paragraph, max_length=4000)
    image = discord.ui.TextInput(label="Image URL", required=False, max_length=1000)
    thumbnail = discord.ui.TextInput(label="Thumbnail URL", required=False, max_length=1000)

    def __init__(self, rid):
        super().__init__()
        self.rid = rid

    async def on_submit(self, i):
        row = await _webhook_row(i, self.rid)
        if not row:
            return
        if not any((self.text.value.strip(), self.embed_title.value.strip(), self.description.value.strip(), self.image.value.strip(), self.thumbnail.value.strip())):
            await say(i, "webhooks.message_modal.empty")
            return
        e = None
        if any((self.embed_title.value.strip(), self.description.value.strip(), self.image.value.strip(), self.thumbnail.value.strip())):
            e = discord.Embed(title=self.embed_title.value.strip() or None, description=self.description.value.strip() or None, color=embed_color())
            if normalize_url(self.image.value):
                e.set_image(url=normalize_url(self.image.value))
            if normalize_url(self.thumbnail.value):
                e.set_thumbnail(url=normalize_url(self.thumbnail.value))
        channel = i.guild.get_channel(row[4])
        can_mass = channel is not None and (core.is_owner(i) or channel.permissions_for(i.user).mention_everyone)
        try:
            wh = await i.client.fetch_webhook(row[3])
            await wh.send(self.text.value or None, embed=e, wait=False,
                          allowed_mentions=discord.AllowedMentions(everyone=can_mass, roles=can_mass, users=True))
        except discord.HTTPException as ex:
            await say(i, "webhooks.error", error=ex)
            return
        log_webhook_event(i.guild.id, i.user.id, row[3], "send", row[4], "message+embed" if e else "message")
        await say(i, "webhooks.message_modal.sent")


class WebhookRenameModal(Modal, title="ПЕРЕИМЕНОВАТЬ WEBHOOK"):
    texts = "webhooks.rename_modal"
    name = discord.ui.TextInput(label="Новое имя", max_length=80)

    def __init__(self, rid):
        super().__init__()
        self.rid = rid

    async def on_submit(self, i):
        row = await _webhook_row(i, self.rid)
        if not row:
            return
        try:
            wh = await i.client.fetch_webhook(row[3])
            await wh.edit(name=self.name.value.strip())
        except discord.HTTPException as e:
            await say(i, "webhooks.error", error=e)
            return
        update_webhook_record(self.rid, name=self.name.value.strip())
        log_webhook_event(i.guild.id, i.user.id, row[3], "rename", row[4], self.name.value.strip())
        await say(i, "webhooks.rename_modal.done")


class WebhookAvatarModal(Modal, title="ИЗМЕНИТЬ АВАТАР"):
    texts = "webhooks.avatar_modal"
    avatar = discord.ui.TextInput(label="Прямая URL-ссылка", max_length=1000)

    def __init__(self, rid):
        super().__init__()
        self.rid = rid

    async def on_submit(self, i):
        row = await _webhook_row(i, self.rid)
        if not row:
            return
        await i.response.defer(ephemeral=True)
        url = normalize_url(self.avatar.value)
        data = await fetch_bytes(url)
        if not data:
            await i.followup.send(t("webhooks.avatar_modal.fetch_failed"), ephemeral=True)
            return
        try:
            wh = await i.client.fetch_webhook(row[3])
            await wh.edit(avatar=data)
        except discord.HTTPException as e:
            await i.followup.send(t("webhooks.error", error=e), ephemeral=True)
            return
        update_webhook_record(self.rid, avatar_url=url)
        log_webhook_event(i.guild.id, i.user.id, row[3], "avatar", row[4])
        await i.followup.send(t("webhooks.avatar_modal.done"), ephemeral=True)


class WebhookHome(PanelView):
    texts = "webhooks.home"

    def __init__(self, back_target=None):
        super().__init__(back_target=back_target)

    @discord.ui.button(label="Создать", emoji="➕", style=discord.ButtonStyle.success)
    async def create(self, i, b):
        await i.response.send_modal(WebhookCreateModal())

    @discord.ui.button(label="Список", emoji="📚", style=discord.ButtonStyle.secondary)
    async def list(self, i, b):
        await i.response.edit_message(embed=panel_embed(i, "webhooks.list"), view=WebhookListView(i, back_target=(i.message.embeds[0], self)))

    @discord.ui.button(label="История", emoji="📜", style=discord.ButtonStyle.secondary)
    async def history(self, i, b):
        rows = get_webhook_history(i.guild.id, limit=50)
        if not is_admin(i):
            mine = {row[2] for row in get_webhooks(i.guild.id, i.user.id)}
            rows = [row for row in rows if row[1] in mine]
        text = "\n".join(f"<t:{c}:R> · <@{o}> · `{a}` · webhook `{w}`" for o, w, a, _, _, c in rows[:20]) or t("webhooks.history.empty")
        await i.response.edit_message(embed=panel_embed(i, "webhooks.history", description=text[:4000]), view=PanelView(back_target=(i.message.embeds[0], self)))


# ============================================================
# /select — МЕНЮ РОЛЕЙ (self-roles)
# ============================================================

def role_menu_embed(i, row):
    roles = ", ".join(f"<@&{rid}>" for rid in j(row[5], [])) or "—"
    return panel_embed(i, "select.menu_card", title=row[3], roles=roles, max=row[6], id=row[0])


class RoleMenuSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"rb:rm:(?P<mid>\d+)"):
    """Постоянное меню: участник выбирает роли, бот выдаёт выбранные и снимает остальные из меню."""

    def __init__(self, menu_id, guild=None, row=None, *, item=None):
        if item is None:
            role_ids = j(row[5], []) if row else []
            options = []
            for rid in role_ids[:25]:
                role = guild.get_role(rid) if guild else None
                if role is not None:
                    options.append(discord.SelectOption(label=role.name[:100], value=str(rid)))
            item = discord.ui.Select(
                custom_id=f"rb:rm:{menu_id}",
                placeholder=(row[4] if row and row[4] else t("select.menu_placeholder"))[:150],
                min_values=0,
                max_values=max(1, min(row[6] if row else 1, len(options) or 1)),
                options=options or [discord.SelectOption(label="—", value="0")],
            )
        super().__init__(item)
        self.menu_id = menu_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["mid"]), item=item)

    async def callback(self, interaction):
        row = get_role_menu(self.menu_id)
        guild = interaction.guild
        if not row or guild is None or row[1] != guild.id:
            await say(interaction, "actions.source_gone")
            return
        # создатель меню должен по-прежнему иметь право раздавать роли
        if not core.is_action_allowed(core.member_level(guild, row[2]), "role.toggle"):
            await say(interaction, "actions.creator_revoked")
            return
        if not guild.me.guild_permissions.manage_roles:
            await say(interaction, "actions.role.no_manage_roles")
            return
        creator = guild.get_member(row[2])
        member = interaction.user
        chosen = {int(v) for v in self.item.values if v.isdigit()}
        added, removed, skipped = [], [], []
        for rid in j(row[5], []):
            role = guild.get_role(rid)
            if role is None:
                continue
            if core.role_problem(guild, role, creator, allow_dangerous=core.is_owner_id(row[2])):
                skipped.append(role.mention)
                continue
            try:
                if rid in chosen and role not in member.roles:
                    await member.add_roles(role, reason=f"Role menu #{self.menu_id}")
                    added.append(role.mention)
                elif rid not in chosen and role in member.roles:
                    await member.remove_roles(role, reason=f"Role menu #{self.menu_id}")
                    removed.append(role.mention)
            except discord.Forbidden:
                skipped.append(role.mention)
        core.audit(interaction, "role_menu.used", "role_menu", self.menu_id, f"+{len(added)} -{len(removed)}")
        await say(
            interaction, "select.menu_result",
            added=", ".join(added) or "—", removed=", ".join(removed) or "—",
            skipped=(t("select.menu_skipped", roles=", ".join(skipped)) if skipped else ""),
        )


class RoleMenuRolesView(PanelView):
    def __init__(self, back_target=None):
        super().__init__(back_target=back_target, timeout=600)
        select = discord.ui.RoleSelect(placeholder=t("select.menu_pick_roles")[:150], min_values=1, max_values=25)
        select.callback = self.picked
        self.select = select
        self.add_item(select)

    async def picked(self, i):
        problems = []
        for role in self.select.values:
            problem = core.role_problem(i.guild, role, i.user)
            if problem:
                problems.append(f"{role.mention} — {t(problem)}")
        if problems:
            await say(i, "select.menu_bad_roles", roles="\n".join(problems))
            return
        await i.response.send_modal(RoleMenuModal([role.id for role in self.select.values]))


class RoleMenuModal(Modal, title="МЕНЮ РОЛЕЙ"):
    texts = "select.menu_modal"
    name = discord.ui.TextInput(label="Название меню", max_length=80)
    placeholder = discord.ui.TextInput(label="Подсказка в списке", required=False, max_length=100)
    max_values = discord.ui.TextInput(label="Сколько ролей можно выбрать", default="1", max_length=2)

    def __init__(self, role_ids):
        super().__init__()
        self.role_ids = role_ids

    async def on_submit(self, i):
        raw = self.max_values.value.strip()
        max_values = int(raw) if raw.isdigit() else 1
        max_values = max(1, min(max_values, len(self.role_ids)))
        menu_id = save_role_menu(i.guild.id, i.user.id, self.name.value.strip(), self.placeholder.value.strip(), json.dumps(self.role_ids), max_values)
        core.audit(i, "role_menu.created", "role_menu", menu_id, self.name.value.strip())
        row = get_role_menu(menu_id)
        await i.response.edit_message(embed=role_menu_embed(i, row), view=RoleMenuActions(menu_id))


class RoleMenuActions(PanelView):
    texts = "select.menu_actions"

    def __init__(self, menu_id, back_target=None):
        super().__init__(back_target=back_target)
        self.menu_id = menu_id

    async def _row(self, i):
        row = get_role_menu(self.menu_id)
        if not row or row[1] != i.guild.id:
            await say(i, "actions.source_gone")
            return None
        return row

    @discord.ui.button(label="Опубликовать", emoji="📣", style=discord.ButtonStyle.success)
    async def publish(self, i, b):
        row = await self._row(i)
        if not row:
            return
        view = PanelView(timeout=300)
        select = discord.ui.ChannelSelect(placeholder=t("embed.send.placeholder")[:150], channel_types=[discord.ChannelType.text, discord.ChannelType.news])

        async def picked(ci):
            channel = ci.guild.get_channel(select.values[0].id)
            if channel is None or not core.can_post_in(ci.user, channel):
                await say(ci, "embed.send.no_perm_user", channel=f"<#{select.values[0].id}>")
                return
            if not core.bot_can_post(channel):
                await say(ci, "embed.send.no_perm_bot", channel=channel.mention)
                return
            menu_view = discord.ui.View(timeout=None)
            menu_view.add_item(RoleMenuSelect(self.menu_id, ci.guild, row))
            await channel.send(embed=panel_embed(ci, "select.public_menu", title=row[3]), view=menu_view)
            core.audit(ci, "role_menu.published", "role_menu", self.menu_id, f"channel={channel.id}")
            await say(ci, "forms.published", channel=channel.mention)

        select.callback = picked
        view.add_item(select)
        await i.response.send_message(t("embed.send.prompt"), view=view, ephemeral=True)

    @discord.ui.button(label="Удалить", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete(self, i, b):
        row = await self._row(i)
        if not row:
            return
        if not owns_or_admin(i, row[2]):
            await say(i, "forms.manage_denied")
            return
        delete_role_menu(self.menu_id)
        core.audit(i, "role_menu.deleted", "role_menu", self.menu_id, row[3])
        await i.response.edit_message(embed=panel_embed(i, "select.menu_deleted"), view=None)


class SelectHome(PanelView):
    texts = "select.home"

    def __init__(self, back_target=None):
        super().__init__(back_target=back_target)

    @discord.ui.button(label="Создать меню ролей", emoji="🎭", style=discord.ButtonStyle.primary)
    async def create_menu(self, i, b):
        if not core.is_action_allowed(get_user_level(i), "role.toggle"):
            await say(i, "embed.no_actions_allowed")
            return
        await i.response.edit_message(embed=panel_embed(i, "select.menu_new"), view=RoleMenuRolesView(back_target=(i.message.embeds[0], self)))

    @discord.ui.button(label="Мои меню", emoji="📚", style=discord.ButtonStyle.secondary)
    async def menus(self, i, b):
        view = PanelView(back_target=(i.message.embeds[0], self))
        rows = [row for row in get_role_menus(i.guild.id) if owns_or_admin(i, row[1])]
        for mid, owner, name, roles_json, max_values, updated in rows[:20]:
            btn = discord.ui.Button(label=f"{(name or '')[:60]} · #{mid}", style=discord.ButtonStyle.secondary)

            async def cb(ci, mid=mid):
                row = get_role_menu(mid)
                if not row:
                    await say(ci, "actions.source_gone")
                    return
                await ci.response.edit_message(embed=role_menu_embed(ci, row), view=RoleMenuActions(mid, back_target=(ci.message.embeds[0], view)))

            btn.callback = cb
            view.add_item(btn)
        if not rows:
            view.add_item(discord.ui.Button(label=t("select.menus_empty")[:80], disabled=True))
        await i.response.edit_message(embed=panel_embed(i, "select.menus"), view=view)


DYNAMIC_ITEMS = [FormApplyButton, ReviewButton, RoleMenuSelect]


# ============================================================
# REGISTRATION
# ============================================================

def register_extended(bot, require_access):
    @bot.tree.command(name="forms", description="Создание и управление формами")
    @discord.app_commands.guild_only()
    async def forms(i):
        if not await require_access(i, "forms"):
            return
        await i.response.send_message(embed=panel_embed(i, "forms.home"), view=FormStartView(i.guild.id, i.user.id), ephemeral=True)

    @bot.tree.command(name="select", description="Меню ролей и выборы")
    @discord.app_commands.guild_only()
    async def select(i):
        if not await require_access(i, "select"):
            return
        await i.response.send_message(embed=panel_embed(i, "select.home"), view=SelectHome(), ephemeral=True)

    @bot.tree.command(name="templates", description="Библиотека публичных и приватных шаблонов")
    @discord.app_commands.guild_only()
    async def templates(i):
        if not await require_access(i, "templates"):
            return
        await i.response.send_message(embed=panel_embed(i, "templates.home"), view=TemplateHome(), ephemeral=True)

    @bot.tree.command(name="webhooks", description="Полноценное управление вебхуками")
    @discord.app_commands.guild_only()
    async def webhooks(i):
        if not await require_access(i, "webhooks"):
            return
        await i.response.send_message(embed=panel_embed(i, "webhooks.home"), view=WebhookHome(), ephemeral=True)
