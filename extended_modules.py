import json
import os
from datetime import datetime

import discord
from core import PanelView, Modal, t, panel_embed, embed_color, can_manage_access, fetch_bytes

from database import (
    save_form, get_form, get_forms, save_submission, get_submission,
    review_submission, save_template, get_template, get_templates,
    delete_template, save_webhook, get_webhook, get_webhooks,
    update_webhook_record, delete_webhook_record, log_webhook_event,
    get_webhook_history, log_audit,
    set_template_favorite,
)

MAX_QUESTIONS = 5
MAX_EMBEDS = 10
MAX_BUTTONS = 5


def say(i, key, **params):
    return i.response.send_message(t(key, **params), ephemeral=True)


def j(value, default):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def url(value):
    value = (value or "").strip()
    if not value:
        return None
    return value if value.startswith(("http://", "https://")) else "https://" + value


def role_allowed(interaction, owner_id, visibility, role_ids):
    if interaction.user.id == owner_id:
        return True
    if visibility == "public":
        return True
    return any(r.id in set(role_ids) for r in getattr(interaction.user, "roles", []))


def build_embed(data):
    color = data.get("color", embed_color().value)
    try:
        color = int(color)
    except (TypeError, ValueError):
        color = embed_color().value
    e = discord.Embed(
        title=data.get("title") or None,
        description=data.get("description") or "​",
        color=color,
    )
    if data.get("url"):
        e.url = url(data["url"])
    if data.get("thumbnail"):
        e.set_thumbnail(url=url(data["thumbnail"]))
    if data.get("image"):
        e.set_image(url=url(data["image"]))
    if data.get("author_name"):
        e.set_author(name=str(data["author_name"])[:256], url=url(data.get("author_url")), icon_url=url(data.get("author_icon")))
    if data.get("footer_text"):
        e.set_footer(text=str(data["footer_text"])[:2048], icon_url=url(data.get("footer_icon")))
    if data.get("timestamp"):
        e.timestamp = datetime.now()
    for field in data.get("fields", [])[:25]:
        e.add_field(name=str(field.get("name", "Field"))[:256], value=str(field.get("value", "​"))[:1024], inline=bool(field.get("inline")))
    return e


def button_view(items):
    if not items:
        return None
    view = discord.ui.View(timeout=None)
    styles = {
        "primary": discord.ButtonStyle.primary,
        "secondary": discord.ButtonStyle.secondary,
        "success": discord.ButtonStyle.success,
        "danger": discord.ButtonStyle.danger,
    }
    for item in items[:MAX_BUTTONS]:
        action = item.get("action", "message")
        label = str(item.get("label") or t("common.default_button_label"))[:80]
        emoji = item.get("emoji") or None
        if action == "link":
            target = url(item.get("value"))
            if not target:
                continue
            b = discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.link, url=target)
        else:
            b = discord.ui.Button(label=label, emoji=emoji, style=styles.get(item.get("style"), discord.ButtonStyle.primary))
            async def cb(interaction, action=action, value=item.get("value", "")):
                if action == "message":
                    await interaction.response.send_message(value or t("actions.default.message"), ephemeral=True)
                elif action == "confirm":
                    await interaction.response.send_message(value or t("actions.default.confirm"), ephemeral=True)
                elif action == "ephemeral":
                    await interaction.response.send_message(value or t("actions.default.ephemeral"), ephemeral=True)
            b.callback = cb
        view.add_item(b)
    return view


# =========================
# FORMS
# =========================

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
        questions=qs,
    )


class FormBasicModal(Modal, title="ОСНОВНЫЕ ДАННЫЕ ФОРМЫ"):
    texts = "forms.basic_modal"
    name = discord.ui.TextInput(label="Название", max_length=100)
    description = discord.ui.TextInput(label="Описание", style=discord.TextStyle.paragraph, required=False, max_length=1000)
    category = discord.ui.TextInput(label="Категория", max_length=50)
    visibility = discord.ui.TextInput(label="private/public", max_length=7)
    roles = discord.ui.TextInput(label="Разрешённые role ID через запятую", required=False, max_length=1000)
    def __init__(self, state):
        super().__init__(); self.state=state
        self.name.default=state.name; self.description.default=state.description; self.category.default=state.category; self.visibility.default=state.visibility; self.roles.default=",".join(map(str,state.allowed_roles))
    async def on_submit(self, interaction):
        v=self.visibility.value.strip().lower()
        if v not in {"private","public"}:
            await say(interaction, "forms.basic_modal.bad_visibility"); return
        self.state.name=self.name.value.strip(); self.state.description=self.description.value.strip(); self.state.category=self.category.value.strip() or "general"; self.state.visibility=v
        self.state.allowed_roles=[int(x.strip()) for x in self.roles.value.split(",") if x.strip().isdigit()]
        await interaction.response.edit_message(embed=form_builder_embed(interaction, self.state),view=FormView(self.state))


class FormQuestionModal(Modal, title="ВОПРОС ФОРМЫ"):
    texts = "forms.question_modal"
    label=discord.ui.TextInput(label="Вопрос",max_length=256)
    qtype=discord.ui.TextInput(label="short / long / yesno",max_length=10)
    required=discord.ui.TextInput(label="required? yes/no",max_length=3)
    def __init__(self,state): super().__init__(); self.state=state
    async def on_submit(self,interaction):
        qt=self.qtype.value.strip().lower()
        if qt not in {"short","long","yesno"}:
            await say(interaction, "forms.question_modal.bad_type"); return
        if len(self.state.questions)>=MAX_QUESTIONS:
            await say(interaction, "forms.question_modal.too_many", max=MAX_QUESTIONS); return
        self.state.questions.append({"label":self.label.value.strip(),"type":qt,"required":self.required.value.strip().lower() in {"yes","y","да","д"}})
        await interaction.response.edit_message(embed=form_builder_embed(interaction, self.state),view=FormView(self.state))


class FormRoutingModal(Modal, title="МАРШРУТИЗАЦИЯ ФОРМЫ"):
    texts = "forms.routing_modal"
    destination=discord.ui.TextInput(label="ID канала заявок",max_length=30)
    reviewer_roles=discord.ui.TextInput(label="Reviewer role IDs через запятую",required=False,max_length=1000)
    reviewer_users=discord.ui.TextInput(label="Reviewer user IDs через запятую",required=False,max_length=1000)
    action=discord.ui.TextInput(label="review / role / none",max_length=10)
    role=discord.ui.TextInput(label="Role ID после одобрения",required=False,max_length=30)
    def __init__(self,state):
        super().__init__(); self.state=state
        self.destination.default=str(state.destination or ""); self.reviewer_roles.default=",".join(map(str,state.reviewer_roles)); self.reviewer_users.default=",".join(map(str,state.reviewer_users)); self.action.default=state.post_action; self.role.default=str(state.post_role or "")
    async def on_submit(self,interaction):
        action=self.action.value.strip().lower()
        if not self.destination.value.strip().isdigit() or action not in {"review","role","none"}:
            await say(interaction, "forms.routing_modal.invalid"); return
        self.state.destination=int(self.destination.value); self.state.reviewer_roles=[int(x.strip()) for x in self.reviewer_roles.value.split(",") if x.strip().isdigit()]; self.state.reviewer_users=[int(x.strip()) for x in self.reviewer_users.value.split(",") if x.strip().isdigit()]; self.state.post_action=action; self.state.post_role=int(self.role.value) if self.role.value.strip().isdigit() else None
        await interaction.response.edit_message(embed=form_builder_embed(interaction, self.state),view=FormView(self.state))


class FormTargetRoleView(PanelView):
    def __init__(self,state,back_target=None):
        super().__init__(back_target=back_target, timeout=300); self.state=state
        s=discord.ui.RoleSelect(placeholder=t("forms.target_role.placeholder")[:150],min_values=1,max_values=1); s.callback=self.selected; self.add_item(s)
    async def selected(self,interaction):
        self.state.target_role=self.children[0].values[0].id
        await interaction.response.edit_message(embed=form_builder_embed(interaction, self.state),view=FormView(self.state, back_target=self.back_target))


class FormView(PanelView):
    texts = "forms.builder"
    def __init__(self,state,back_target=None): super().__init__(back_target=back_target); self.state=state
    @discord.ui.button(label="Основные",emoji="✏️",style=discord.ButtonStyle.primary)
    async def basic(self,i,b): await i.response.send_modal(FormBasicModal(self.state))
    @discord.ui.button(label="Вопрос",emoji="➕",style=discord.ButtonStyle.secondary)
    async def question(self,i,b): await i.response.send_modal(FormQuestionModal(self.state))
    @discord.ui.button(label="Маршрутизация",emoji="📨",style=discord.ButtonStyle.secondary)
    async def routing(self,i,b): await i.response.send_modal(FormRoutingModal(self.state))
    @discord.ui.button(label="Роль для заявки",emoji="🎭",style=discord.ButtonStyle.secondary)
    async def target(self,i,b): await i.response.edit_message(embed=panel_embed(i, "forms.target_role"),view=FormTargetRoleView(self.state, back_target=(i.message.embeds[0], self)))
    @discord.ui.button(label="Сохранить",emoji="💾",style=discord.ButtonStyle.success)
    async def save(self,i,b):
        if not self.state.questions or not self.state.destination:
            await say(i, "forms.builder.need_question"); return
        fid=save_form(self.state.guild_id,self.state.owner_id,self.state.name,self.state.description,json.dumps(self.state.questions,ensure_ascii=False),self.state.visibility,self.state.category,json.dumps(self.state.allowed_roles),self.state.destination,json.dumps(self.state.reviewer_roles),json.dumps(self.state.reviewer_users),self.state.post_action,self.state.post_role,self.state.target_role)
        role=f"<@&{self.state.target_role}>" if self.state.target_role else t("forms.builder.role_unset")
        await i.response.edit_message(embed=panel_embed(i, "forms.saved", id=fid, channel=f"<#{self.state.destination}>", role=role),view=FormUseView(fid, back_target=(i.message.embeds[0], self)))


class FormUseView(PanelView):
    texts = "forms.use"
    def __init__(self,form_id,back_target=None): super().__init__(back_target=back_target); self.form_id=form_id
    @discord.ui.button(label="Подать заявку",emoji="📝",style=discord.ButtonStyle.primary)
    async def apply(self,i,b):
        row=get_form(self.form_id)
        if row: await i.response.send_modal(SubmissionModal(row))
        else: await say(i, "forms.not_found")


class SubmissionModal(discord.ui.Modal):
    def __init__(self,row):
        self.row=row; super().__init__(title=row[3][:45]); self.fields=[]
        for q in j(row[5],[])[:MAX_QUESTIONS]:
            f=discord.ui.TextInput(label=q['label'][:45],required=q.get('required',True),style=discord.TextStyle.paragraph if q.get('type')=='long' else discord.TextStyle.short,max_length=4000 if q.get('type')=='long' else 1000); self.fields.append(f); self.add_item(f)
    async def on_submit(self,i):
        qs=j(self.row[5],[]); answers={q['label']:f.value for q,f in zip(qs,self.fields)}; sid=save_submission(self.row[0],i.guild.id,i.user.id,json.dumps(answers,ensure_ascii=False)); channel=i.guild.get_channel(self.row[9])
        if channel:
            e=panel_embed(i, "forms.submission", form=self.row[3], user=i.user.mention, id=sid)
            for k,v in answers.items(): e.add_field(name=k[:256],value=str(v)[:1024] or "​",inline=False)
            await channel.send(embed=e,view=ReviewView(sid))
        await say(i, "forms.submitted")


class ReviewView(PanelView):
    texts = "forms.review"
    def __init__(self,sid): super().__init__(timeout=None); self.sid=sid
    def allowed(self,i,row,form):
        return i.user.id==form[2] or i.user.id in j(form[11],[]) or any(r.id in set(j(form[10],[])) for r in i.user.roles)
    @discord.ui.button(label="Одобрить",emoji="✅",style=discord.ButtonStyle.success)
    async def approve(self,i,b): await self.decide(i,"approved")
    @discord.ui.button(label="Отклонить",emoji="❌",style=discord.ButtonStyle.danger)
    async def reject(self,i,b): await self.decide(i,"rejected")
    async def decide(self,i,status):
        row=get_submission(self.sid); form=get_form(row[1]) if row else None
        if not row or not form: await say(i, "forms.review.not_found"); return
        if not self.allowed(i,row,form): await say(i, "forms.review.no_rights"); return
        if row[5] != 'pending': await say(i, "forms.review.already", status=t(f"forms.status.{row[5]}")); return
        review_submission(self.sid,i.user.id,status)
        # post_role_id (роль после одобрения) приоритетнее target_role_id (роль, на которую подавали)
        role_id=form[13] or form[14]
        role_error=None
        if status=='approved' and form[12]=='role' and role_id:
            member=i.guild.get_member(row[3]); role=i.guild.get_role(role_id)
            if member and role:
                try: await member.add_roles(role,reason=f"Form #{self.sid} approved")
                except discord.Forbidden: role_error=t("forms.review.role_forbidden")
            else:
                role_error=t("forms.review.role_missing")
        e=panel_embed(i, "forms.reviewed", status=t(f"forms.status.{status}"), reviewer=i.user.mention)
        if role_error:
            e.description=f"{e.description}\n\n⚠️ {role_error}"
        await i.response.edit_message(embed=e,view=None)


class FormStartView(PanelView):
    texts = "forms.start"
    def __init__(self,guild_id,user_id,back_target=None): super().__init__(back_target=back_target); self.guild_id=guild_id; self.user_id=user_id
    @discord.ui.button(label="Создать",emoji="➕",style=discord.ButtonStyle.success)
    async def create(self,i,b):
        state = FormState(self.guild_id, self.user_id)
        await i.response.edit_message(embed=form_builder_embed(i, state),view=FormView(state, back_target=(i.message.embeds[0], self)))
    @discord.ui.button(label="Сохранённые",emoji="📦",style=discord.ButtonStyle.secondary)
    async def saved(self,i,b): await i.response.edit_message(embed=panel_embed(i, "forms.list"),view=FormListView(i.guild.id,i.user.id, back_target=(i.message.embeds[0], self)))


class FormListView(PanelView):
    def __init__(self,guild_id,user_id,back_target=None):
        super().__init__(back_target=back_target); rows=get_forms(guild_id,user_id,True)
        for fid,owner,name,visibility,category,roles,updated in rows[:20]:
            b=discord.ui.Button(label=name[:70],style=discord.ButtonStyle.secondary)
            async def cb(i,fid=fid):
                row=get_form(fid)
                if row and role_allowed(i,row[2],row[6],j(row[8],[])): await i.response.send_message(embed=panel_embed(i, "forms.card", title=row[3], description=row[4] or t("forms.no_description")),view=FormUseView(fid, back_target=(i.message.embeds[0], self)),ephemeral=True)
                else: await say(i, "forms.unavailable")
            b.callback=cb; self.add_item(b)
        if not rows:self.add_item(discord.ui.Button(label=t("forms.list.empty")[:80],disabled=True))


# =========================
# TEMPLATES
# =========================

class TemplateModal(Modal, title="СОХРАНИТЬ ШАБЛОН"):
    texts = "templates.modal"
    name=discord.ui.TextInput(label="Название",max_length=100); typ=discord.ui.TextInput(label="message / buttons / form",max_length=20); category_visibility=discord.ui.TextInput(label="Категория/видимость (general/public)",max_length=60); roles=discord.ui.TextInput(label="Role IDs через запятую",required=False,max_length=1000); payload=discord.ui.TextInput(label="JSON payload",style=discord.TextStyle.paragraph,max_length=4000)
    async def on_submit(self,i):
        typ=self.typ.value.strip().lower()
        category, _, vis = self.category_visibility.value.partition("/")
        category=category.strip() or "general"
        vis=vis.strip().lower() or "private"
        if typ not in {'message','buttons','form'} or vis not in {'private','public'}:
            await say(i, "templates.modal.bad_type"); return
        try: json.loads(self.payload.value)
        except json.JSONDecodeError: await say(i, "templates.modal.bad_json"); return
        roles=[int(x.strip()) for x in self.roles.value.split(',') if x.strip().isdigit()]
        tid=save_template(i.guild.id,i.user.id,self.name.value.strip(),typ,self.payload.value,vis,category,json.dumps(roles))
        await say(i, "templates.modal.saved", id=tid)


def template_card_embed(i, row, is_favorite):
    return panel_embed(
        i, "templates.card", title=row[3],
        type=row[4], category=row[7], visibility=row[6],
        favorite=t("common.yes") if is_favorite else t("common.no"),
    )


class TemplateListView(PanelView):
    def __init__(self,guild_id,user_id,back_target=None):
        super().__init__(back_target=back_target); rows=get_templates(guild_id,user_id,include_public=True)
        for tid,owner,name,typ,payload,vis,cat,roles,logo,is_favorite,updated in rows[:20]:
            marker = ' ★' if is_favorite else ''
            b=discord.ui.Button(label=f'{name[:48]}{marker} · {typ}',style=discord.ButtonStyle.secondary)
            async def cb(i,tid=tid):
                row=get_template(tid)
                if not row or not role_allowed(i,row[2],row[6],j(row[8],[])): await say(i, "templates.unavailable"); return
                await i.response.send_message(embed=template_card_embed(i,row,row[10]),view=TemplateActions(tid, back_target=(i.message.embeds[0], self)),ephemeral=True)
            b.callback=cb; self.add_item(b)
        if not rows:self.add_item(discord.ui.Button(label=t("templates.list.empty")[:80],disabled=True))


class TemplateActions(PanelView):
    texts = "templates.card"
    def __init__(self,tid,back_target=None): super().__init__(back_target=back_target); self.tid=tid
    @discord.ui.button(label='Использовать',emoji='▶️',style=discord.ButtonStyle.success)
    async def use(self,i,b):
        row=get_template(self.tid); p=j(row[5],{}) if row else {}
        if not row: await say(i, "templates.not_found"); return
        if row[4]=='message':
            content=p.get('content') or None
            embeds=[build_embed(x) for x in p.get('embeds',[])[:MAX_EMBEDS]]
            view=button_view(p.get('buttons',[]))
            if not content and not embeds and view is None:
                await say(i, "templates.empty")
                return
            await i.response.send_message(content=content,embeds=embeds,view=view,ephemeral=True)
        elif row[4]=='buttons': await i.response.send_message(embed=panel_embed(i, "templates.button_set", title=row[3]),view=button_view(p.get('buttons',[])),ephemeral=True)
        else: await say(i, "templates.form_hint")
    @discord.ui.button(label='Избранное',emoji='⭐',style=discord.ButtonStyle.secondary)
    async def favorite(self,i,b):
        row=get_template(self.tid)
        if not row:
            await say(i, "templates.not_found")
            return
        is_favorite = not bool(row[10])
        set_template_favorite(self.tid, is_favorite)
        await i.response.edit_message(
            embed=template_card_embed(i, row, is_favorite),
            view=TemplateActions(self.tid, back_target=self.back_target),
        )
    @discord.ui.button(label='Удалить',emoji='🗑️',style=discord.ButtonStyle.danger)
    async def delete(self,i,b):
        row=get_template(self.tid)
        if row and (row[2]==i.user.id or i.user.id==int(os.getenv('OWNER_ID','0'))):
            delete_template(self.tid,row[2]); await i.response.edit_message(embed=panel_embed(i, "templates.deleted"),view=None)
        else: await say(i, "templates.delete_denied")


# =========================
# WEBHOOKS
# =========================

async def avatar_bytes(value):
    return await fetch_bytes(value)


class WebhookCreateModal(Modal, title='СОЗДАТЬ WEBHOOK'):
    texts = "webhooks.create_modal"
    name=discord.ui.TextInput(label='Название',max_length=80); avatar=discord.ui.TextInput(label='Avatar URL',required=False,max_length=1000)
    async def on_submit(self,i): await i.response.send_message(t("webhooks.pick_channel"),view=WebhookChannelView(self.name.value.strip(),self.avatar.value.strip()),ephemeral=True)


class WebhookChannelView(PanelView):
    def __init__(self,name,avatar,back_target=None):
        super().__init__(back_target=back_target, timeout=300); self.name=name; self.avatar=avatar; s=discord.ui.ChannelSelect(placeholder=t("webhooks.channel_placeholder")[:150],channel_types=[discord.ChannelType.text]); s.callback=self.selected; self.add_item(s)
    async def selected(self,i):
        ch=self.children[0].values[0]
        try: wh=await ch.create_webhook(name=self.name,avatar=await avatar_bytes(self.avatar),reason='Webhook Manager')
        except discord.Forbidden: await say(i, "webhooks.need_manage"); return
        except discord.HTTPException as e: await say(i, "webhooks.discord_error", error=e); return
        rid=save_webhook(i.guild.id,i.user.id,wh.id,ch.id,wh.name,wh.url,self.avatar or None); log_webhook_event(i.guild.id,i.user.id,wh.id,'created',ch.id); log_audit(i.guild.id,i.user.id,'webhook.created','webhook',wh.id)
        await i.response.edit_message(embed=panel_embed(i, "webhooks.created", channel=ch.mention, name=wh.name, id=wh.id),view=WebhookActions(rid))


class WebhookListView(PanelView):
    def __init__(self,guild_id,user_id,back_target):
        super().__init__(back_target=back_target); rows=get_webhooks(guild_id,user_id)
        for rid,owner,wid,cid,name,avatar,created,updated in rows[:20]:
            b=discord.ui.Button(label=name[:70],style=discord.ButtonStyle.secondary)
            async def cb(i,rid=rid):
                row=get_webhook(rid)
                if row: await i.response.edit_message(embed=panel_embed(i, "webhooks.card", name=row[5], channel=f"<#{row[4]}>", id=row[3]),view=WebhookActions(rid, back_target=(i.message.embeds[0], self)))
                else: await say(i, "webhooks.not_found")
            b.callback=cb; self.add_item(b)
        if not rows:self.add_item(discord.ui.Button(label=t("webhooks.list.empty")[:80],disabled=True))


class WebhookActions(PanelView):
    texts = "webhooks.card"
    def __init__(self,rid,back_target=None): super().__init__(back_target=back_target); self.rid=rid
    def admin(self,i): return can_manage_access(i)
    @discord.ui.button(label='Показать URL',emoji='🔑',style=discord.ButtonStyle.secondary)
    async def show(self,i,b):
        row=get_webhook(self.rid)
        if not row or not self.admin(i): await say(i, "webhooks.access_denied"); return
        await say(i, "webhooks.url", url=row[6])
    @discord.ui.button(label='Отправить',emoji='📨',style=discord.ButtonStyle.primary)
    async def send(self,i,b):
        if not self.admin(i): await say(i, "common.not_enough_rights"); return
        await i.response.send_modal(WebhookMessageModal(self.rid))
    @discord.ui.button(label='Переименовать',emoji='✏️',style=discord.ButtonStyle.secondary)
    async def rename(self,i,b):
        if not self.admin(i): await say(i, "common.not_enough_rights"); return
        await i.response.send_modal(WebhookRenameModal(self.rid))
    @discord.ui.button(label='Аватар',emoji='🖼️',style=discord.ButtonStyle.secondary)
    async def avatar(self,i,b):
        if not self.admin(i): await say(i, "common.not_enough_rights"); return
        await i.response.send_modal(WebhookAvatarModal(self.rid))
    @discord.ui.button(label='Тест',emoji='🧪',style=discord.ButtonStyle.success)
    async def test(self,i,b):
        if not self.admin(i): await say(i, "common.not_enough_rights"); return
        row=get_webhook(self.rid)
        try:
            wh=await i.client.fetch_webhook(row[3]); await wh.send(t("webhooks.test_message"),wait=False); log_webhook_event(i.guild.id,i.user.id,row[3],'test',row[4]); await say(i, "webhooks.test_sent")
        except discord.HTTPException as e: await say(i, "webhooks.error", error=e)
    @discord.ui.button(label='История',emoji='📜',style=discord.ButtonStyle.secondary)
    async def history(self,i,b):
        row=get_webhook(self.rid); rows=get_webhook_history(i.guild.id,row[3] if row else None); text='\n'.join(f'`{a}` · {d or ""}' for _,_,a,_,d,_ in rows) or t("webhooks.history.empty"); await i.response.send_message(embed=panel_embed(i, "webhooks.history", description=text),ephemeral=True)
    @discord.ui.button(label='Удалить',emoji='🗑️',style=discord.ButtonStyle.danger)
    async def delete(self,i,b):
        if not self.admin(i): await say(i, "common.not_enough_rights"); return
        await i.response.send_message(t("webhooks.delete.confirm"),view=WebhookDeleteView(self.rid, back_target=(i.message.embeds[0], self)),ephemeral=True)
    @discord.ui.button(label='Пересоздать URL',emoji='♻️',style=discord.ButtonStyle.danger)
    async def regenerate(self,i,b):
        if i.user.id!=int(os.getenv('OWNER_ID','0')): await say(i, "webhooks.regenerate.owner_only"); return
        await i.response.send_message(t("webhooks.regenerate.confirm"),view=WebhookRegenerateView(self.rid, back_target=(i.message.embeds[0], self)),ephemeral=True)


class WebhookDeleteView(PanelView):
    texts = "webhooks.delete"
    def __init__(self,rid,back_target=None): super().__init__(back_target=back_target, timeout=300); self.rid=rid
    @discord.ui.button(label='Удалить',emoji='🗑️',style=discord.ButtonStyle.danger)
    async def yes(self,i,b):
        row=get_webhook(self.rid)
        try: wh=await i.client.fetch_webhook(row[3]); await wh.delete(reason='Webhook Manager'); log_webhook_event(i.guild.id,i.user.id,row[3],'deleted',row[4]); delete_webhook_record(self.rid); await say(i, "webhooks.delete.done")
        except discord.HTTPException as e: await say(i, "webhooks.error", error=e)
    @discord.ui.button(label='Отмена',style=discord.ButtonStyle.secondary)
    async def cancel(self,i,b): await say(i, "common.cancelled")


class WebhookRegenerateView(PanelView):
    texts = "webhooks.regenerate"
    def __init__(self,rid,back_target=None): super().__init__(back_target=back_target, timeout=300); self.rid=rid
    @discord.ui.button(label='Да, пересоздать',emoji='♻️',style=discord.ButtonStyle.danger)
    async def yes(self,i,b):
        row=get_webhook(self.rid); old_id=row[3]; ch=i.guild.get_channel(row[4])
        try:
            old=await i.client.fetch_webhook(old_id); await old.delete(reason='Webhook URL regeneration'); new=await ch.create_webhook(name=row[5],reason='Webhook URL regeneration'); update_webhook_record(self.rid,name=new.name,channel_id=ch.id,url=new.url); log_webhook_event(i.guild.id,i.user.id,new.id,'regenerated',ch.id,f'old_webhook_id={old_id}'); await say(i, "webhooks.regenerate.done")
        except (discord.HTTPException,discord.Forbidden) as e: await say(i, "webhooks.error", error=e)
    @discord.ui.button(label='Отмена',style=discord.ButtonStyle.secondary)
    async def cancel(self,i,b): await say(i, "common.cancelled")


class WebhookMessageModal(Modal,title='WEBHOOK MESSAGE'):
    texts = "webhooks.message_modal"
    # поле embed_title, а не title: атрибут title занят заголовком самой модалки,
    # и TextInput с таким именем молча пропадал из формы.
    text=discord.ui.TextInput(label='Текст',required=False,style=discord.TextStyle.paragraph,max_length=2000); embed_title=discord.ui.TextInput(label='Embed title',required=False,max_length=256); description=discord.ui.TextInput(label='Embed description',required=False,style=discord.TextStyle.paragraph,max_length=4000); image=discord.ui.TextInput(label='Image URL',required=False,max_length=1000); thumbnail=discord.ui.TextInput(label='Thumbnail URL',required=False,max_length=1000)
    def __init__(self,rid): super().__init__(); self.rid=rid
    async def on_submit(self,i):
        row=get_webhook(self.rid); e=None
        if not any((self.text.value.strip(), self.embed_title.value.strip(), self.description.value.strip(), self.image.value.strip(), self.thumbnail.value.strip())):
            await say(i, "webhooks.message_modal.empty"); return
        if self.embed_title.value.strip() or self.description.value.strip():
            e=discord.Embed(title=self.embed_title.value.strip() or None,description=self.description.value.strip() or None,color=embed_color())
            if url(self.image.value): e.set_image(url=url(self.image.value))
            if url(self.thumbnail.value): e.set_thumbnail(url=url(self.thumbnail.value))
        try:
            wh=await i.client.fetch_webhook(row[3]); await wh.send(self.text.value or None,embed=e,wait=False); log_webhook_event(i.guild.id,i.user.id,row[3],'send',row[4],'message+embed' if e else 'message'); await say(i, "webhooks.message_modal.sent")
        except discord.HTTPException as ex: await say(i, "webhooks.error", error=ex)


class WebhookRenameModal(Modal,title='ПЕРЕИМЕНОВАТЬ WEBHOOK'):
    texts = "webhooks.rename_modal"
    name=discord.ui.TextInput(label='Новое имя',max_length=80)
    def __init__(self,rid): super().__init__(); self.rid=rid
    async def on_submit(self,i):
        row=get_webhook(self.rid)
        try: wh=await i.client.fetch_webhook(row[3]); await wh.edit(name=self.name.value.strip()); update_webhook_record(self.rid,name=self.name.value.strip()); log_webhook_event(i.guild.id,i.user.id,row[3],'rename',row[4],self.name.value.strip()); await say(i, "webhooks.rename_modal.done")
        except discord.HTTPException as e: await say(i, "webhooks.error", error=e)


class WebhookAvatarModal(Modal,title='ИЗМЕНИТЬ АВАТАР'):
    texts = "webhooks.avatar_modal"
    avatar=discord.ui.TextInput(label='Прямая URL-ссылка',max_length=1000)
    def __init__(self,rid): super().__init__(); self.rid=rid
    async def on_submit(self,i):
        row=get_webhook(self.rid); data=await avatar_bytes(self.avatar.value.strip())
        if not data: await say(i, "webhooks.avatar_modal.fetch_failed"); return
        try: wh=await i.client.fetch_webhook(row[3]); await wh.edit(avatar=data); update_webhook_record(self.rid,avatar_url=self.avatar.value.strip()); log_webhook_event(i.guild.id,i.user.id,row[3],'avatar',row[4]); await say(i, "webhooks.avatar_modal.done")
        except discord.HTTPException as e: await say(i, "webhooks.error", error=e)


class WebhookHome(PanelView):
    texts = "webhooks.home"
    def __init__(self,back_target=None): super().__init__(back_target=back_target)
    @discord.ui.button(label='Создать',emoji='➕',style=discord.ButtonStyle.success)
    async def create(self,i,b): await i.response.send_modal(WebhookCreateModal())
    @discord.ui.button(label='Список',emoji='📚',style=discord.ButtonStyle.secondary)
    async def list(self,i,b): await i.response.edit_message(embed=panel_embed(i, "webhooks.list"),view=WebhookListView(i.guild.id,i.user.id,back_target=(i.message.embeds[0], self)))
    @discord.ui.button(label='История',emoji='📜',style=discord.ButtonStyle.secondary)
    async def history(self,i,b):
        rows=get_webhook_history(i.guild.id); text='\n'.join(f'<@{o}> · `{a}` · webhook `{w}`' for o,w,a,_,_,_ in rows) or t("webhooks.history.empty"); await i.response.edit_message(embed=panel_embed(i, "webhooks.history", description=text),view=WebhookBack(back_target=(i.message.embeds[0], self)))


class WebhookBack(PanelView):
    def __init__(self,back_target=None): super().__init__(back_target=back_target)


# =========================
# REGISTRATION
# =========================

def register_extended(bot, require_access):
    @bot.tree.command(name='forms',description='Создание и управление формами')
    async def forms(i):
        if not await require_access(i,'forms'): return
        await i.response.send_message(embed=panel_embed(i, "forms.home"),view=FormStartView(i.guild.id,i.user.id),ephemeral=True)

    @bot.tree.command(name='select',description='Создание и проверка Select Menu')
    async def select(i):
        if not await require_access(i,'select'): return
        await i.response.send_message(embed=panel_embed(i, "select.home"),view=SelectHome(),ephemeral=True)

    @bot.tree.command(name='templates',description='Библиотека публичных и приватных шаблонов')
    async def templates(i):
        if not await require_access(i,'templates'): return
        await i.response.send_message(embed=panel_embed(i, "templates.home"),view=TemplateHome(),ephemeral=True)

    @bot.tree.command(name='messages',description='Управление сохранёнными Message Build')
    async def messages(i):
        if not await require_access(i,'messages'): return
        await i.response.send_message(embed=panel_embed(i, "messages.home"),view=MessageList(i.guild.id,i.user.id),ephemeral=True)

    @bot.tree.command(name='webhooks',description='Полноценное управление вебхуками')
    async def webhooks(i):
        if not await require_access(i,'webhooks'): return
        await i.response.send_message(embed=panel_embed(i, "webhooks.home"),view=WebhookHome(),ephemeral=True)


class SelectHome(PanelView):
    texts = "select.home"
    def __init__(self,back_target=None): super().__init__(back_target=back_target)
    @discord.ui.button(label='Выбор роли',emoji='🎭',style=discord.ButtonStyle.primary)
    async def role(self,i,b):
        s=discord.ui.RoleSelect(placeholder=t("common.pick_role")[:150],min_values=1,max_values=1); s.callback=lambda x: say(x, "select.picked", value=s.values[0].mention); self.clear_items(); self.add_item(s); await i.response.edit_message(embed=panel_embed(i, "select.role"),view=self)
    @discord.ui.button(label='Выбор канала',emoji='📁',style=discord.ButtonStyle.secondary)
    async def channel(self,i,b): await say(i, "select.channel_hint")
    @discord.ui.button(label='Выбор пользователя',emoji='👤',style=discord.ButtonStyle.secondary)
    async def user(self,i,b): await say(i, "select.user_hint")


class TemplateHome(PanelView):
    texts = "templates.home"
    def __init__(self,back_target=None): super().__init__(back_target=back_target)
    @discord.ui.button(label='Список',emoji='📚',style=discord.ButtonStyle.primary)
    async def list(self,i,b): await i.response.edit_message(embed=panel_embed(i, "templates.list"),view=TemplateListView(i.guild.id,i.user.id, back_target=(i.message.embeds[0], self)))
    @discord.ui.button(label='Сохранить JSON',emoji='💾',style=discord.ButtonStyle.secondary)
    async def save(self,i,b): await i.response.send_modal(TemplateModal())


class MessageList(PanelView):
    def __init__(self,guild_id,user_id,back_target=None):
        super().__init__(back_target=back_target)
        from database import get_message_builds
        rows=get_message_builds(guild_id,user_id,True)
        for bid, owner, name, vis, cat, updated in rows[:20]:
            b=discord.ui.Button(label=name[:70],style=discord.ButtonStyle.secondary)
            async def cb(i,bid=bid):
                from database import get_message_build
                row=get_message_build(bid)
                if not row: await say(i, "messages.not_found"); return
                content=row[4] or None
                embeds=[build_embed(x) for x in j(row[5],[])[:MAX_EMBEDS]]
                view=button_view(j(row[6],[]))
                if not content and not embeds and view is None:
                    await say(i, "messages.empty")
                    return
                await i.response.send_message(content=content,embeds=embeds,view=view,ephemeral=True)
            b.callback=cb; self.add_item(b)
        if not rows:self.add_item(discord.ui.Button(label=t("messages.list.empty")[:80],disabled=True))
