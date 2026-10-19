"""
action_wizard.py
================
Действие кнопки — пошагово, вопрос за вопросом, по-русски. Никаких ID и
форматов вроде «очки +1»: бот спрашивает, ты выбираешь.

Выбор действия (список с понятными названиями и тем, что будет при нажатии):

- 💬 Ответить текстом     -> кому показать -> текст (с переменными)
- 🎭 Роль                 -> какая роль -> выдать / снять / переключать
- 🏷️ Меню ролей           -> какие роли -> одну или несколько
- 📝 Открыть форму        -> какая форма
- 📄 Показать сообщение   -> какой Message Build -> отдельно / шагом мастера
- 🔄 Обновить сообщения   -> чьи отправленные копии (можно этот же build)
- 🔢 Счётчик              -> counter_wizard.py
- ✏️ Редактировать это сообщение -> что будет, «Добавить»
- 🪝 Через вебхук         -> какой вебхук -> текст спрашивать / заранее

Результат — то же действие и значение кнопки, что сохраняет
embed_module.commit_pending (старые кнопки продолжают работать).
"""

import json

import discord

import core
from core import Modal, PanelView, t, panel_embed

# (ид, действия, эмодзи) — в этом порядке в списке «Что делает кнопка»
CHOICES = [
    ("reply", ("message.send",), "💬"),
    ("role", ("role.toggle", "role.assign", "role.remove"), "🎭"),
    ("role_menu", ("select.trigger",), "🏷️"),
    ("form", ("form.trigger",), "📝"),
    ("show", ("build.trigger", "build.goto"), "📄"),
    ("refresh", ("build.refresh",), "🔄"),
    ("counter", ("counter.change",), "🔢"),
    ("edit", ("message.edit",), "✏️"),
    ("webhook", ("webhook.send",), "🪝"),
]
REPLY_TARGETS = ("me", "channel", "dm")
ROLE_MODES = ("role.toggle", "role.assign", "role.remove")
MAX_MENU_ROLES = 25


# ---------- значения действий (новый формат — JSON, старый — как был) ----------

def _json_value(value):
    text = (value or "").strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None
    return None


def reply_options(value):
    """«Ответить текстом»: {"to": me|channel|dm, "text": ...}. Старое значение — просто текст нажавшему."""
    data = _json_value(value)
    if data is not None and "text" in data:
        to = data.get("to") if data.get("to") in REPLY_TARGETS else "me"
        return {"to": to, "text": str(data.get("text") or "")}
    return {"to": "me", "text": value or ""}


def role_menu_options(value):
    """«Меню ролей»: {"roles": [id…], "single": bool} или None (старое «role/user/channel»)."""
    data = _json_value(value)
    if data is None or not isinstance(data.get("roles"), list):
        return None
    roles = [int(r) for r in data["roles"] if str(r).isdigit()][:MAX_MENU_ROLES]
    return {"roles": roles, "single": bool(data.get("single"))}


def webhook_options(value):
    """«Через вебхук»: {"id": запись, "text": заранее заданный текст или ""}. Старое — просто ID."""
    data = _json_value(value)
    if data is not None and str(data.get("id", "")).isdigit():
        return {"id": int(data["id"]), "text": str(data.get("text") or "")}
    return {"id": int(value) if str(value or "").strip().isdigit() else None, "text": ""}


def dump(data):
    return json.dumps(data, ensure_ascii=False)


# ---------- общий шаг ----------

def allowed_keys(interaction, state):
    import embed_module
    from core import actions_for_level
    return {row[0] for row in actions_for_level(embed_module.creator_of(interaction, state)[1])}


def available(choice, keys, allowed):
    """Пункт виден, если создателю можно его действие; меню ролей — только тем, кому можно «Роль»."""
    if choice == "role_menu" and "role.toggle" not in allowed:
        return False
    return bool(allowed & set(keys))


def choice_label(choice):
    return t(f"action_wizard.pick.{choice}")


class Step(PanelView):
    """Экран шага: кнопки-ответы, «Назад» — к списку кнопок."""

    def __init__(self, wizard):
        import embed_module
        back = wizard.back or embed_module.builder_screen(wizard.interaction, wizard.builder_view)
        super().__init__(back_target=back, timeout=600)
        self.wizard = wizard

    def answer(self, label, emoji, callback, style=discord.ButtonStyle.secondary, row=None):
        button = discord.ui.Button(label=label[:80], emoji=emoji, style=style, row=row)

        async def cb(interaction):
            await callback(interaction)
        button.callback = cb
        self.add_item(button)
        return button


class Wizard:
    """Состояние мастера одной кнопки (или опции списка)."""

    def __init__(self, interaction, state, pending, target, builder_view, back=None):
        self.interaction = interaction
        self.state, self.pending, self.target, self.builder_view = state, pending, target, builder_view
        self.back = back  # (embed, view) для «Назад», если мастер открыт не из списка кнопок

    def args(self):
        return self.state, self.pending, self.target, self.builder_view

    async def commit(self, interaction, action_key, value):
        import embed_module
        self.pending["action_key"] = action_key
        await embed_module.commit_pending(interaction, *self.args(), value)

    async def show(self, interaction, context, view, **params):
        embed = panel_embed(interaction, context, hints=False, **params)
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.edit_message(embed=embed, view=view)


def options_text(prefix, keys, **params):
    """Строки «**Ответ** — что будет» под вопросом."""
    return "\n".join(f"**{t(f'{prefix}.{key}', **params)}** — {t(f'{prefix}.{key}.about', **params)}" for key in keys)


# ---------- 1. что делает кнопка ----------

class PickSelect(core.PagedSelect):
    def __init__(self, interaction, state, pending, target, builder_view):
        allowed = allowed_keys(interaction, state)
        options = [
            discord.SelectOption(label=choice_label(choice)[:100], emoji=emoji, value=choice,
                                 description=t(f"action_wizard.pick.{choice}.about")[:100])
            for choice, keys, emoji in CHOICES if available(choice, keys, allowed)
        ] or [discord.SelectOption(label=t("embed.no_actions")[:100], value="__none__")]
        super().__init__(placeholder=t("action_wizard.pick.placeholder")[:150], options=options, row=0)
        self.wizard = Wizard(interaction, state, pending, target, builder_view)
        self.allowed = allowed

    async def pick(self, interaction):
        from actions import say
        choice = self.values[0]
        if choice == "__none__":
            await say(interaction, "embed.no_actions_allowed")
            return
        self.wizard.interaction = interaction
        await START[choice](interaction, self.wizard, self.allowed)


def pick_view(interaction, state, pending, target, builder_view):
    import embed_module
    view = PanelView(back_target=embed_module.builder_screen(interaction, builder_view), timeout=600)
    view.add_item(PickSelect(interaction, state, pending, target, builder_view))
    return view


def pick_embed(interaction, state):
    allowed = allowed_keys(interaction, state)
    lines = [f"{emoji} **{choice_label(choice)}** — {t(f'action_wizard.pick.{choice}.about')}"
             for choice, keys, emoji in CHOICES if available(choice, keys, allowed)]
    return panel_embed(interaction, "action_wizard.pick", hints=False, choices="\n".join(lines))


# ---------- 💬 ответить текстом ----------

async def start_reply(interaction, wizard, allowed):
    view = Step(wizard)
    for to, emoji in zip(REPLY_TARGETS, ("👤", "📢", "✉️")):
        async def chosen(i, to=to):
            await i.response.send_modal(ReplyTextModal(wizard, to))
        view.answer(t(f"action_wizard.reply.{to}"), emoji, chosen)
    await wizard.show(interaction, "action_wizard.reply", view,
                      options=options_text("action_wizard.reply", REPLY_TARGETS))


class ReplyTextModal(Modal, title="ТЕКСТ ОТВЕТА"):
    def __init__(self, wizard, to):
        super().__init__(title=t("action_wizard.reply.modal.title")[:45])
        self.wizard, self.to = wizard, to
        self.text = discord.ui.TextInput(style=discord.TextStyle.paragraph, max_length=2000,
                                         placeholder=t("action_wizard.reply.modal.placeholder")[:100])
        self.add_item(discord.ui.Label(text=t("action_wizard.reply.modal.label")[:45],
                                       description=t("action_wizard.reply.modal.help")[:100], component=self.text))

    async def on_submit(self, interaction):
        value = dump({"to": self.to, "text": self.text.value.strip()}) if self.to != "me" else self.text.value.strip()
        await self.wizard.commit(interaction, "message.send", value)


# ---------- 🎭 роль ----------

def _role_select(placeholder, max_values):
    return discord.ui.RoleSelect(placeholder=placeholder[:150], min_values=1, max_values=max_values, row=0)


async def start_role(interaction, wizard, allowed):
    import embed_module
    view = Step(wizard)
    select = _role_select(t("action_wizard.role.placeholder"), 1)
    creator = embed_module.creator_of(interaction, wizard.state)[0]

    async def picked(i):
        from actions import say
        role = i.guild.get_role(select.values[0].id)
        problem = core.role_problem(i.guild, role, creator)
        if problem:
            await say(i, problem)
            return
        await role_mode(i, wizard, allowed, role)

    select.callback = picked
    view.add_item(select)
    await wizard.show(interaction, "action_wizard.role", view)


async def role_mode(interaction, wizard, allowed, role):
    view = Step(wizard)
    modes = [key for key in ROLE_MODES if key in allowed]
    for key, emoji in zip(ROLE_MODES, ("🔁", "➕", "➖")):
        if key not in allowed:
            continue

        async def chosen(i, key=key):
            await wizard.commit(i, key, str(role.id))
        view.answer(t(f"action_wizard.role_mode.{key}"), emoji, chosen,
                    style=discord.ButtonStyle.primary if key == "role.toggle" else discord.ButtonStyle.secondary)
    await wizard.show(interaction, "action_wizard.role_mode", view, role=role.mention,
                      options=options_text("action_wizard.role_mode", modes, role=role.mention))


# ---------- 🏷️ меню ролей ----------

async def start_role_menu(interaction, wizard, allowed, on_done=None):
    """Какие роли в меню -> одну или несколько. on_done(i, roles, single) — для меню прямо в сообщении."""
    import embed_module
    view = Step(wizard)
    select = _role_select(t("action_wizard.role_menu.placeholder"), MAX_MENU_ROLES)
    creator = embed_module.creator_of(interaction, wizard.state)[0]

    async def picked(i):
        from actions import say
        roles = [i.guild.get_role(r.id) for r in select.values]
        for role in roles:
            problem = core.role_problem(i.guild, role, creator)
            if problem:
                await say(i, "action_wizard.role_menu.bad_role", role=getattr(role, "mention", "?"),
                          reason=t(problem))
                return
        await role_menu_mode(i, wizard, [r.id for r in roles], on_done)

    select.callback = picked
    view.add_item(select)
    await wizard.show(interaction, "action_wizard.role_menu", view)


async def role_menu_mode(interaction, wizard, role_ids, on_done=None):
    view = Step(wizard)
    roles = ", ".join(f"<@&{r}>" for r in role_ids)
    for key, emoji, single in (("many", "🧺", False), ("single", "☝️", True)):
        async def chosen(i, single=single):
            if on_done is not None:
                await on_done(i, role_ids, single)
                return
            await wizard.commit(i, "select.trigger", dump({"roles": role_ids, "single": single}))
        view.answer(t(f"action_wizard.role_menu_mode.{key}"), emoji, chosen)
    await wizard.show(interaction, "action_wizard.role_menu_mode", view, roles=roles,
                      options=options_text("action_wizard.role_menu_mode", ("many", "single")))


# ---------- 📝 форма ----------

async def start_form(interaction, wizard, allowed):
    from database import get_forms, get_form, get_form_status
    from actions import form_visible
    view = Step(wizard)
    rows = [row for row in get_forms(interaction.guild.id) if form_visible(interaction, get_form(row[0]))]
    if not rows:
        view.add_item(discord.ui.Button(label=t("embed.form_attach.empty")[:80], disabled=True))
    else:
        def state_of(form_id):
            status = get_form_status(form_id)
            return t("action_wizard.form.closed" if status and status[0] else "action_wizard.form.open")

        select = core.PagedSelect(
            placeholder=t("embed.form_attach.placeholder")[:150],
            options=[discord.SelectOption(label=(row[2] or t("embed.default_name"))[:100], value=str(row[0]),
                                          description=state_of(row[0])[:100]) for row in rows])

        async def picked(i):
            await wizard.commit(i, "form.trigger", select.values[0])
        select.on_pick = picked
        view.add_item(select)
    await wizard.show(interaction, "action_wizard.form", view)


# ---------- 📄 показать сообщение / 🔄 обновить ----------

def _build_options(interaction, wizard, include_self=False):
    from actions import build_visible
    from database import get_message_build, get_message_builds
    options = []
    own_id = getattr(wizard.state, "build_id", None)  # у набора кнопок своего build'а нет
    if include_self and own_id:
        options.append(discord.SelectOption(label=t("action_wizard.refresh.self")[:100], emoji="🔁",
                                            value=str(own_id)))
    for bid, owner, name, vis, cat, updated in get_message_builds(interaction.guild.id):
        if bid == own_id or not build_visible(interaction, get_message_build(bid)):
            continue
        options.append(discord.SelectOption(label=f"{(name or t('embed.default_name'))[:90]} · #{bid}", value=str(bid),
                                            description=t("embed.build_pick.option", visibility=vis)[:100]))
    return options


def _build_select(interaction, wizard, on_pick, include_self=False):
    options = _build_options(interaction, wizard, include_self)
    if not options:
        return discord.ui.Button(label=t("embed.saved.empty")[:80], disabled=True)
    select = core.PagedSelect(placeholder=t("embed.build_pick.placeholder")[:150], options=options)

    async def picked(i):
        await on_pick(i, select.values[0])
    select.on_pick = picked
    return select


async def start_show(interaction, wizard, allowed):
    view = Step(wizard)

    async def picked(i, build_id):
        await show_mode(i, wizard, allowed, build_id)
    view.add_item(_build_select(interaction, wizard, picked))
    await wizard.show(interaction, "action_wizard.show", view)


async def show_mode(interaction, wizard, allowed, build_id):
    view = Step(wizard)
    modes = [key for key in ("build.trigger", "build.goto") if key in allowed]
    for key, emoji in (("build.trigger", "📨"), ("build.goto", "➡️")):
        if key not in allowed:
            continue

        async def chosen(i, key=key):
            await wizard.commit(i, key, build_id)
        view.answer(t(f"action_wizard.show_mode.{key}"), emoji, chosen)
    await wizard.show(interaction, "action_wizard.show_mode", view,
                      options=options_text("action_wizard.show_mode", modes))


async def start_refresh(interaction, wizard, allowed):
    view = Step(wizard)

    async def picked(i, build_id):
        await wizard.commit(i, "build.refresh", build_id)
    view.add_item(_build_select(interaction, wizard, picked, include_self=True))
    await wizard.show(interaction, "action_wizard.refresh", view)


# ---------- 🔢 счётчик ----------

async def start_counter(interaction, wizard, allowed):
    import counter_wizard
    wizard.pending["action_key"] = "counter.change"
    await counter_wizard.start(interaction, *wizard.args())


# ---------- ✏️ редактировать это сообщение ----------

async def start_edit(interaction, wizard, allowed):
    view = Step(wizard)

    async def add(i):
        await wizard.commit(i, "message.edit", "")
    view.answer(t("action_wizard.edit.add"), "✅", add, style=discord.ButtonStyle.success)
    await wizard.show(interaction, "action_wizard.edit", view)


# ---------- 🪝 через вебхук ----------

async def start_webhook(interaction, wizard, allowed):
    from database import get_webhooks
    from extended_modules import can_manage_webhook
    view = Step(wizard)
    rows = [row for row in get_webhooks(interaction.guild.id)
            if can_manage_webhook(interaction, (row[0], interaction.guild.id, row[1]))]
    if not rows:
        view.add_item(discord.ui.Button(label=t("action_wizard.webhook.empty")[:80], disabled=True))
    else:
        select = core.PagedSelect(
            placeholder=t("action_wizard.webhook.placeholder")[:150],
            options=[discord.SelectOption(label=(row[4] or f"#{row[0]}")[:100], value=str(row[0]),
                                          description=f"#{interaction.guild.get_channel(row[3]) or row[3]}"[:100])
                     for row in rows])

        async def picked(i):
            await webhook_text(i, wizard, int(select.values[0]))
        select.on_pick = picked
        view.add_item(select)
    await wizard.show(interaction, "action_wizard.webhook", view)


async def webhook_text(interaction, wizard, record_id):
    view = Step(wizard)

    async def ask(i):
        await wizard.commit(i, "webhook.send", str(record_id))

    async def preset(i):
        await i.response.send_modal(WebhookTextModal(wizard, record_id))
    view.answer(t("action_wizard.webhook_text.ask"), "⌨️", ask)
    view.answer(t("action_wizard.webhook_text.preset"), "📝", preset)
    await wizard.show(interaction, "action_wizard.webhook_text", view,
                      options=options_text("action_wizard.webhook_text", ("ask", "preset")))


class WebhookTextModal(Modal, title="ТЕКСТ"):
    def __init__(self, wizard, record_id):
        super().__init__(title=t("action_wizard.webhook_text.modal")[:45])
        self.wizard, self.record_id = wizard, record_id
        self.text = discord.ui.TextInput(style=discord.TextStyle.paragraph, max_length=2000)
        self.add_item(discord.ui.Label(text=t("action_wizard.reply.modal.label")[:45],
                                       description=t("action_wizard.reply.modal.help")[:100], component=self.text))

    async def on_submit(self, interaction):
        await self.wizard.commit(interaction, "webhook.send", dump({"id": self.record_id, "text": self.text.value.strip()}))


START = {
    "reply": start_reply, "role": start_role, "role_menu": start_role_menu, "form": start_form,
    "show": start_show, "refresh": start_refresh, "counter": start_counter, "edit": start_edit,
    "webhook": start_webhook,
}


# ============================================================
# ПРИ НАЖАТИИ
# ============================================================

async def with_variables(interaction, text):
    """Переменные в тексте ответа — для нажавшего ({user}, {counter:очки}…)."""
    import live
    from actions import VARIABLE, substitute
    needed = {(m[1], m[2]) for m in VARIABLE.finditer(text or "")}
    if not needed:
        return text
    return substitute(text, await live.collect(interaction.guild, needed, user=interaction.user))


async def apply_role_menu(interaction, role_ids, chosen_ids, single, creator_id, *, exact):
    """
    Меню ролей. exact=True — выбранное становится набором ролей нажавшего из
    меню (личное меню с отметками); False — каждая выбранная роль
    переключается (меню в общем сообщении: выбрал ещё раз — снял).
    -> (выданные, снятые) или ключ ошибки.
    """
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
    if member is None:
        return "actions.role.no_member"
    if not guild.me.guild_permissions.manage_roles:
        return "actions.role.no_manage_roles"
    if creator_id is not None and not core.is_action_allowed(core.member_level(guild, creator_id), "role.toggle"):
        return "actions.creator_revoked"
    creator = guild.get_member(creator_id) if creator_id else None
    menu = [guild.get_role(r) for r in role_ids]
    menu = [r for r in menu if r is not None and not core.role_problem(
        guild, r, creator, allow_dangerous=bool(creator_id and core.is_owner_id(creator_id)))]
    chosen = [r for r in menu if r.id in set(chosen_ids)]
    has = [r for r in menu if r in member.roles]
    if exact:
        add = [r for r in chosen if r not in has]
        remove = [r for r in has if r not in chosen]
    else:
        add = [r for r in chosen if r not in has]
        remove = [r for r in chosen if r in has]
        if single and add:
            remove = [r for r in has if r not in add]
    if single and len(add) > 1:
        return "action_wizard.role_menu.only_one"
    try:
        for role in add:
            await member.add_roles(role, reason="Role menu")
        for role in remove:
            await member.remove_roles(role, reason="Role menu")
    except discord.Forbidden:
        return "actions.role.forbidden"
    for role in add:
        core.audit(interaction, "role.added", "role", role.id, "via role menu")
    for role in remove:
        core.audit(interaction, "role.removed", "role", role.id, "via role menu")
    return add, remove


def role_menu_result(result):
    if isinstance(result, str):
        return t(result)
    add, remove = result
    lines = []
    if add:
        lines.append(t("action_wizard.role_menu.added", roles=", ".join(r.mention for r in add)))
    if remove:
        lines.append(t("action_wizard.role_menu.removed", roles=", ".join(r.mention for r in remove)))
    return "\n".join(lines) or t("action_wizard.role_menu.nothing")


def role_menu_select(guild, role_ids, single, *, custom_id=None, member=None, row=None):
    """Select с ролями меню; member — отметить его роли (личное меню)."""
    roles = [guild.get_role(r) for r in role_ids]
    roles = [r for r in roles if r is not None][:MAX_MENU_ROLES]
    options = [discord.SelectOption(label=r.name[:100], value=str(r.id),
                                    default=bool(member is not None and r in member.roles)) for r in roles]
    if not options:
        options = [discord.SelectOption(label=t("action_wizard.role_menu.empty")[:100], value="0")]
    kwargs = {"custom_id": custom_id} if custom_id else {}
    return discord.ui.Select(
        placeholder=t("action_wizard.role_menu.press_placeholder" if member is not None
                      else "action_wizard.role_menu.public_placeholder")[:150],
        min_values=0 if member is not None else 1, max_values=1 if single else len(options),
        options=options, row=row, **kwargs)


async def open_role_menu(interaction, options, creator_id):
    """Кнопка «Меню ролей»: личное меню с отметками, выбор = итоговый набор ролей."""
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    select = role_menu_select(interaction.guild, options["roles"], options["single"], member=member)
    view = discord.ui.View(timeout=300)

    async def picked(i):
        result = await apply_role_menu(i, options["roles"], [int(v) for v in select.values if v != "0"],
                                       options["single"], creator_id, exact=True)
        await i.response.edit_message(content=role_menu_result(result), view=None)
    select.callback = picked
    view.add_item(select)
    await interaction.response.send_message(t("action_wizard.role_menu.prompt"), view=view, ephemeral=True)


# ============================================================
# ПОДПИСЬ В СПИСКЕ КНОПОК
# ============================================================

def describe(action_key, value, style=None):
    """Что делает кнопка — по-человечески, для списка кнопок в конструкторе."""
    from database import get_form, get_message_build, get_webhook
    value = value or ""
    if style == "link":
        return t("action_wizard.describe.link", url=value[:80])
    if action_key in ("message.send", "message.confirm"):
        options = reply_options(value)
        text = options["text"].replace("\n", " ")
        return t(f"action_wizard.describe.reply.{options['to']}", text=(text[:40] + "…") if len(text) > 40 else text)
    if action_key in ROLE_MODES:
        return t(f"action_wizard.describe.{action_key}", role=f"<@&{value}>")
    if action_key == "select.trigger":
        menu = role_menu_options(value)
        if menu is None:
            return t("action_wizard.describe.native", kind=value)
        return t("action_wizard.describe.role_menu", roles=", ".join(f"<@&{r}>" for r in menu["roles"]),
                 mode=t("action_wizard.role_menu_mode.single" if menu["single"] else "action_wizard.role_menu_mode.many"))
    if action_key == "form.trigger":
        row = get_form(int(value)) if value.isdigit() else None
        return t("action_wizard.describe.form", name=row[3] if row else f"#{value}")
    if action_key in ("build.trigger", "build.goto", "build.refresh"):
        row = get_message_build(int(value)) if value.isdigit() else None
        return t(f"action_wizard.describe.{action_key}", name=(row[3] if row else "?") or t("embed.default_name"), id=value)
    if action_key == "counter.change":
        return t("action_wizard.describe.counter", value=value)
    if action_key == "message.edit":
        return t("action_wizard.describe.edit")
    if action_key == "webhook.send":
        options = webhook_options(value)
        row = get_webhook(options["id"]) if options["id"] is not None else None
        key = "action_wizard.describe.webhook_preset" if options["text"] else "action_wizard.describe.webhook"
        return t(key, name=(row[5] if row else None) or f"#{options['id']}")
    return action_key or "?"
