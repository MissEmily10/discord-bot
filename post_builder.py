"""
post_builder.py
===============
Конструктор сообщений на сайте бота — весь /embed в браузере, по образцу
message.style: слева редактор, справа живой предпросмотр в стиле Discord.

Что можно (то же, что в /embed, с теми же правами):
- текст, до 10 embed'ов (автор, заголовок, ссылка, описание, поля, цвет,
  картинки — ссылкой или файлом, подпись, время), «одним / разными сообщениями»;
- кнопки со всеми действиями (ответить текстом, роль, меню ролей, форма,
  показать сообщение / шаг мастера, обновить сообщения, счётчик, правка,
  вебхук) и кнопки-ссылки; список с действиями у каждого пункта; меню ролей
  прямо в сообщении; кнопка формы;
- название, категория, видимость (публичное / приватное / по уровню или ролям);
- переменные — справочник с поиском, вставка в текст;
- отправка в несколько каналов, в форум (название поста), от бота или
  временным вебхуком со своим именем и аватаркой;
- сохранённые сообщения: открыть, сохранить, удалить, история версий (откат),
  шаблоны, обновить отправленные, импорт/экспорт JSON (Discohook);
- автоматика: расписание, триггеры (новый участник, ветка, ключевые слова),
  живое обновление, родитель стиля, варианты по роли/уровню.

/post -> личная ссылка (2 часа с последнего действия). /post news:… —
клановая новость (news_generator.py) открывается здесь же.

Страница: builder.html, шрифты создателя бота — /fonts/… (папка fonts).
"""

import base64
import json
import logging
import pathlib
import re
import secrets
import time

import discord
from aiohttp import web

import core
from core import t

_log = logging.getLogger(__name__)

SESSION_TTL = 2 * 3600
PAGE = pathlib.Path(__file__).with_name("builder.html")
FONTS_DIR = pathlib.Path(__file__).with_name("fonts")
_FONT_NAME = re.compile(r"^[\w.-]{1,80}\.(otf|ttf|woff2?)$")
_sessions = {}  # токен -> {"guild_id", "user_id", "expires", "draft"}
_bot = None
core.COMMAND_MIN_LEVELS.setdefault("post", "staff")

CHANNEL_TYPES = (discord.TextChannel, discord.ForumChannel)
VISIBILITIES = ("public", "private", "restricted")
LEVELS = ("member", "staff", "admin")


class Actor:
    """Кто работает в конструкторе — как interaction для проверок прав (sanitize, validate, audit)."""

    def __init__(self, guild, member):
        self.guild, self.user, self.client = guild, member, _bot
        self.channel_id = None
        self.channel = None
        self.message = None


class Denied(Exception):
    pass


def available():
    import web_panel
    return web_panel.panel_port() is not None and bool(web_panel.panel_url())


def _purge():
    now = time.time()
    for token in [k for k, v in _sessions.items() if v["expires"] <= now]:
        _sessions.pop(token, None)


def open_link(guild_id, user_id, draft=None, build_id=None):
    """Новая сессия конструктора -> ссылка или None (сайт не настроен)."""
    import web_panel
    if not available():
        return None
    _purge()
    token = secrets.token_urlsafe(24)
    _sessions[token] = {"guild_id": guild_id, "user_id": user_id, "expires": time.time() + SESSION_TTL,
                        "draft": draft, "build_id": build_id}
    return f"{web_panel.panel_url()}/post/{token}"


def _context(request):
    """-> (сессия, сервер, участник). Продлевает сессию."""
    _purge()
    entry = _sessions.get(request.match_info["token"])
    if entry is None:
        raise Denied("Ссылка истекла — открой конструктор заново: /post в Discord.")
    guild = _bot.get_guild(entry["guild_id"]) if _bot else None
    member = guild.get_member(entry["user_id"]) if guild else None
    if member is None:
        raise Denied("Не вижу тебя на сервере — открой конструктор заново: /post.")
    if not core.has_command_access(Actor(guild, member), "post"):
        raise Denied("Нет доступа к конструктору.")
    entry["expires"] = time.time() + SESSION_TTL
    return entry, guild, member


# ============================================================
# ДАННЫЕ ДЛЯ РЕДАКТОРА
# ============================================================

def _avatar(user):
    asset = getattr(user, "display_avatar", None)
    return str(asset.url) if asset else ""


def channels_for(guild, member):
    import live
    result = []
    for channel in guild.channels:
        if isinstance(channel, CHANNEL_TYPES) and live.can_post(member, channel) is None:
            result.append({"id": str(channel.id), "name": channel.name,
                           "forum": isinstance(channel, discord.ForumChannel),
                           "category": channel.category.name if channel.category else ""})
    result.sort(key=lambda c: (c["category"], c["name"]))
    return result


def builds_for(actor):
    from actions import build_visible
    from database import get_message_build, get_message_builds
    rows = []
    for bid, owner, name, vis, cat, updated in get_message_builds(actor.guild.id):
        row = get_message_build(bid)
        if row and build_visible(actor, row):
            rows.append({"id": bid, "name": name or t("embed.default_name"), "category": cat or "general",
                         "visibility": vis, "updated": updated, "mine": owner == actor.user.id})
    return rows[:200]


def roles_for(actor):
    """Роли сервера: можно ли их раздавать кнопкой (как проверяет мастер)."""
    roles = []
    for role in sorted(actor.guild.roles, key=lambda r: -r.position):
        if role.is_default() or role.managed:
            continue
        problem = core.role_problem(actor.guild, role, actor.user)
        color = getattr(role, "color", None)
        roles.append({"id": str(role.id), "name": role.name, "color": color.value if color else 0,
                      "problem": t(problem) if problem else None})
    return roles


def forms_for(actor):
    from actions import form_visible
    from database import get_form, get_form_status, get_forms
    result = []
    for row in get_forms(actor.guild.id):
        form = get_form(row[0])
        if form and form_visible(actor, form):
            status = get_form_status(row[0])
            result.append({"id": str(row[0]), "name": row[2] or f"#{row[0]}", "closed": bool(status and status[0])})
    return result


def webhooks_for(actor):
    from database import get_webhooks
    from extended_modules import can_manage_webhook
    result = []
    for row in get_webhooks(actor.guild.id):
        if can_manage_webhook(actor, (row[0], actor.guild.id, row[1])):
            channel = actor.guild.get_channel(row[3])
            result.append({"id": str(row[0]), "name": row[4] or f"#{row[0]}", "channel": channel.name if channel else ""})
    return result


def templates_for(actor):
    from actions import template_visible
    from database import get_template, get_templates
    result = []
    for row in get_templates(actor.guild.id, template_type="message"):
        full = get_template(row[0])
        if full and template_visible(actor, full):
            result.append({"id": row[0], "name": full[3] or f"#{row[0]}", "category": full[7] or "general"})
    return result[:200]


def variables_catalog():
    import variable_catalog as vc
    return [{"name": v.name, "section": v.section, "title": v.title, "about": v.about, "args": v.args,
             "examples": v.examples} for v in vc.VARS]


def actions_for(actor):
    """Какие действия создатель может вешать на кнопки (как список в /embed)."""
    import action_wizard
    allowed = action_wizard.allowed_keys(actor, None)
    return [{"key": key, "name": core.action_name(key)} for key in sorted(allowed)]


def counters_for(actor):
    import counter_wizard
    return [{"name": name, "label": label, "where": where} for name, label, where in counter_wizard.existing(actor.guild.id)]


def form_packs_for(actor):
    try:
        import form_packs
        return [{"id": str(p[0]), "name": p[2]} for p in form_packs.get_packs(actor.guild.id)]
    except Exception:  # noqa: BLE001 — паки необязательны
        return []


def level_of(actor):
    return core.member_level(actor.guild, actor.user)


# ============================================================
# ПРОВЕРКА И СОХРАНЕНИЕ
# ============================================================

def check_items(actor, payload):
    """
    Действия кнопок и пунктов списка — как при сборке в Discord, но с понятной
    ошибкой у конкретной кнопки (а не молча сброшенное действие). -> [текст].
    """
    from actions import validate_action_value
    errors = []

    def check(item, where):
        style = item.get("style")
        key = item.get("action_key")
        if style == "link" or key:
            value, error = validate_action_value(actor, None if style == "link" else key, item.get("value"),
                                                 style=style, actor=actor.user)
            if error:
                key_, params = error if isinstance(error, tuple) else (error, {})
                errors.append(f"{where}: {t(key_, **params)}")
            elif key and not core.is_action_allowed(level_of(actor), key):
                errors.append(f"{where}: {t('actions.no_access')}")

    for n, button in enumerate(payload.get("buttons") or [], 1):
        if isinstance(button, dict):
            check(button, f"Кнопка {n} «{button.get('label') or '—'}»")
    interactive = payload.get("interactive") or {}
    if isinstance(interactive, dict) and interactive.get("type") == "list":
        for n, option in enumerate(interactive.get("options") or [], 1):
            if isinstance(option, dict):
                check(option, f"Пункт списка {n} «{option.get('label') or '—'}»")
    return errors


def clean(actor, payload):
    """Payload со страницы -> (EmbedState, ошибка-текст | None). Лимиты Discord — как в /embed."""
    from build_tools import sanitize_payload
    from embed_module import EmbedState, state_problem
    if not isinstance(payload, dict):
        return None, "Пустое сообщение."
    errors = check_items(actor, payload)
    if errors:
        return None, "\n".join(errors[:10])
    safe, _ = sanitize_payload(actor, payload)
    content = str(payload.get("content") or "")
    if len(content) > 2000:
        return None, f"Текст сообщения длиннее 2000 символов ({len(content)})."
    safe["content"] = content
    state = EmbedState(actor.guild.id, actor.user.id)
    state.load_payload(safe)
    state.layout = payload.get("layout") if payload.get("layout") in ("single", "split") else "single"
    problem = state_problem(state)
    if problem:
        return None, t(problem[0], **problem[1])
    return state, None


def apply_meta(actor, state, meta):
    """Название, категория, видимость. -> ошибка-текст | None."""
    meta = meta or {}
    state.name = (str(meta.get("name") or "").strip() or t("embed.default_name"))[:100]
    state.category = (str(meta.get("category") or "").strip() or "general")[:50]
    visibility = meta.get("visibility") if meta.get("visibility") in VISIBILITIES else "private"
    state.visibility = visibility
    state.visibility_roles, state.visibility_levels = [], []
    if visibility == "restricted":
        roles = [int(r) for r in meta.get("roles") or [] if str(r).isdigit() and actor.guild.get_role(int(r))]
        levels = [lvl for lvl in meta.get("levels") or [] if lvl in LEVELS]
        if not roles and not levels:
            return "Для «ограниченного» выбери роли или уровень доступа."
        state.visibility_roles, state.visibility_levels = roles[:25], levels
    return None


def save_state(actor, state, listed=True, build_id=None):
    """EmbedState -> build (новый или поверх того, что можно править). -> (id, ошибка | None)."""
    from build_tools import remember_version
    from database import get_message_build, save_message_build, update_message_build, set_build_listed
    from embed_module import can_manage_build
    fields = dict(
        name=state.name, content=state.content,
        embeds_json=json.dumps(state.embeds, ensure_ascii=False),
        buttons_json=json.dumps(state.buttons, ensure_ascii=False),
        visibility=state.visibility, category=state.category,
        allowed_role_ids_json=json.dumps(state.visibility_roles),
        visibility_levels_json=json.dumps(state.visibility_levels),
        interactive_json=json.dumps(state.interactive, ensure_ascii=False),
    )
    if build_id:
        row = get_message_build(int(build_id))
        if not row or row[1] != actor.guild.id:
            return None, t("embed.build_not_found")
        if not can_manage_build(actor, row):
            return None, t("embed.edit_denied")
        remember_version(row[0], actor.user.id)
        update_message_build(row[0], layout=state.layout, **fields)
        if listed:
            set_build_listed(row[0], True)
        core.audit(actor, "build.updated", "build", row[0], "site")
        return row[0], None
    new_id = save_message_build(guild_id=actor.guild.id, owner_id=actor.user.id, listed=listed,
                                layout=state.layout, **fields)
    core.audit(actor, "build.created", "build", new_id, "site" if listed else "site, unlisted")
    return new_id, None


def build_info(actor, row):
    """Всё о сохранённом сообщении для редактора."""
    from actions import load_source
    from database import get_build_settings, get_build_versions, get_schedules, get_sent_instances, get_triggers
    from embed_module import can_manage_build
    import automation
    data = load_source("b", row[0]) or {}
    settings = get_build_settings(row[0])
    manage = can_manage_build(actor, row)
    info = {
        "id": row[0], "manage": manage, "automate": automation.can_automate(actor, row),
        "payload": {"content": data.get("content") or "", "embeds": data.get("embeds") or [],
                    "buttons": data.get("buttons") or [], "interactive": data.get("interactive"),
                    "layout": row[14] if len(row) > 14 else "single"},
        "meta": {"name": row[3] or "", "category": row[8] or "general", "visibility": row[7],
                 "roles": [str(r) for r in json.loads(row[9] or "[]")], "levels": json.loads(row[10] or "[]")},
        "sent": len(get_sent_instances(row[0])),
        "settings": {"live_minutes": settings.get("live_minutes") or 0,
                     "parent_id": str(settings.get("parent_id") or ""),
                     "variants": settings.get("variants") or []},
        "schedules": [], "triggers": [], "versions": [],
    }
    if manage:
        info["versions"] = [{"id": v[0], "user": str(v[1]), "at": v[2]} for v in get_build_versions(row[0])]
        info["schedules"] = [{"id": r[0], "channel": str(r[4]), "next_run": r[5], "every": r[6],
                              "every_text": automation.interval_text(r[6]), "enabled": bool(r[7]), "error": r[9]}
                             for r in get_schedules(build_id=row[0])]
        info["triggers"] = [{"id": r[0], "event": r[4], "words": r[5], "watch": str(r[6] or ""),
                             "target": str(r[7] or ""), "enabled": bool(r[9]), "error": r[11]}
                            for r in get_triggers(build_id=row[0])]
    return info


def _row_for(actor, build_id, manage=False, automate=False):
    from actions import build_visible
    from database import get_message_build
    from embed_module import can_manage_build
    import automation
    row = get_message_build(int(build_id or 0))
    if not row or row[1] != actor.guild.id or not build_visible(actor, row):
        raise Denied(t("embed.build_not_found"))
    if manage and not can_manage_build(actor, row):
        raise Denied(t("embed.edit_denied"))
    if automate and not automation.can_automate(actor, row):
        raise Denied(t("automation.denied"))
    return row


# ============================================================
# HTTP
# ============================================================

def _json_error(message, status=200):
    return web.json_response({"ok": False, "error": message}, status=status)


async def _body(request):
    if request.content_type != "application/json":
        raise Denied("Ожидается JSON.")
    try:
        data = await request.json()
    except (ValueError, json.JSONDecodeError):
        raise Denied("Не получилось прочитать запрос.")
    return data if isinstance(data, dict) else {}


def api(handler):
    async def wrapped(request):
        try:
            entry, guild, member = _context(request)
            return await handler(request, entry, Actor(guild, member))
        except Denied as error:
            return _json_error(str(error), status=403)
        except Exception:  # noqa: BLE001 — показываем на странице, не роняем панель
            _log.exception("post builder api failed")
            return _json_error("Что-то сломалось на стороне бота — подробности в логе.")
    return wrapped


async def page(request):
    _purge()
    if request.match_info["token"] not in _sessions:
        return web.Response(text="Ссылка истекла — открой конструктор заново: /post в Discord.",
                            content_type="text/plain", charset="utf-8", status=410)
    return web.FileResponse(PAGE, headers={"Cache-Control": "no-store"})


async def font(request):
    """Шрифты создателя бота (fonts/, fonts/banner/) — для сайта."""
    name = request.match_info["name"]
    if not _FONT_NAME.match(name):
        raise web.HTTPNotFound()
    for folder in (FONTS_DIR, FONTS_DIR / "banner"):
        path = folder / name
        if path.is_file():
            return web.FileResponse(path, headers={"Cache-Control": "public, max-age=604800"})
    raise web.HTTPNotFound()


def fonts_list():
    found = []
    for folder in (FONTS_DIR, FONTS_DIR / "banner"):
        found += sorted(p.name for p in folder.glob("*") if _FONT_NAME.match(p.name))
    return found


@api
async def api_init(request, entry, actor):
    import live
    guild, member = actor.guild, actor.user
    me = guild.me
    level = level_of(actor)
    return web.json_response({
        "ok": True,
        "guild": guild.name,
        "user": {"id": str(member.id), "name": member.display_name, "avatar": _avatar(member), "level": level},
        "bot": {"name": me.display_name if me else "Purr", "avatar": _avatar(me) if me else ""},
        "can_webhook": live.can_use_webhooks(guild, member),
        "can_automate": core.level_value(level) >= core.level_value("staff"),
        "channels": channels_for(guild, member),
        "builds": builds_for(actor),
        "templates": templates_for(actor),
        "roles": roles_for(actor),
        "forms": forms_for(actor),
        "webhooks": webhooks_for(actor),
        "form_packs": form_packs_for(actor),
        "counters": counters_for(actor),
        "actions": actions_for(actor),
        "variables": variables_catalog(),
        "fonts": fonts_list(),
        "draft": entry.get("draft"),
        "open_build": entry.get("build_id"),
        "limits": {"embeds": 10, "buttons": 20, "content": 2000, "options": 25},
    })


@api
async def api_parse(request, entry, actor):
    from build_tools import payload_from_json_text
    data = await _body(request)
    payload, error = payload_from_json_text(str(data.get("text") or ""))
    if error:
        key, params = error if isinstance(error, tuple) else (error, {})
        return _json_error(t(key, **params))
    return web.json_response({"ok": True, "payload": payload})


@api
async def api_upload(request, entry, actor):
    import web_panel
    data = await _body(request)
    raw = str(data.get("data") or "")
    if "," in raw[:100]:
        raw = raw.split(",", 1)[1]  # data:image/png;base64,....
    try:
        content = base64.b64decode(raw, validate=False)
    except ValueError:
        return _json_error("Не получилось прочитать картинку.")
    name, error = web_panel.save_asset(content)
    if error:
        return _json_error(error)
    return web.json_response({"ok": True, "url": f"{web_panel.panel_url()}/assets/{name}"})


@api
async def api_check(request, entry, actor):
    """Проверить сообщение без сохранения: ошибки у конкретных кнопок и лимиты Discord."""
    data = await _body(request)
    state, error = clean(actor, data.get("payload"))
    return web.json_response({"ok": error is None, "error": error})


@api
async def api_build(request, entry, actor):
    row = _row_for(actor, request.match_info["build_id"])
    return web.json_response({"ok": True, "build": build_info(actor, row)})


@api
async def api_save(request, entry, actor):
    data = await _body(request)
    state, error = clean(actor, data.get("payload"))
    if error:
        return _json_error(error)
    error = apply_meta(actor, state, data.get("meta"))
    if error:
        return _json_error(error)
    build_id, error = save_state(actor, state, listed=True, build_id=data.get("build_id"))
    if error:
        return _json_error(error)
    return web.json_response({"ok": True, "id": build_id,
                              "message": f"Сохранено: Message Build #{build_id} (виден в /embed → «Мои сохранённые»)."})


@api
async def api_send(request, entry, actor):
    import live
    data = await _body(request)
    channel_ids = [int(c) for c in (data.get("channels") or [data.get("channel_id")]) if str(c or "").isdigit()]
    channels = [actor.guild.get_channel_or_thread(c) for c in channel_ids]
    channels = [c for c in channels if c is not None]
    if not channels:
        return _json_error("Выбери канал.")
    state, error = clean(actor, data.get("payload"))
    if error:
        return _json_error(error)
    error = apply_meta(actor, state, data.get("meta"))
    if error:
        return _json_error(error)
    listed = bool(data.get("save"))
    build_id, error = save_state(actor, state, listed=listed, build_id=data.get("build_id"))
    if error:
        return _json_error(error)
    results, problems = [], []
    if data.get("mode") == "webhook":
        if not live.can_use_webhooks(actor.guild, actor.user):
            return _json_error(t("live.error.webhook_no_rights", channel=channels[0].mention))
        avatar = await _avatar_bytes(str(data.get("webhook_avatar") or ""))
        name = (str(data.get("webhook_name") or "").strip() or actor.user.display_name)[:80]
        for channel in channels:
            message, error = await live.deliver_temp_webhook(actor.guild, actor.user, build_id, channel, name, avatar)
            (results.append(message.jump_url) if message else None)
            if error:
                problems.append(error)
    else:
        for channel in channels:
            message, error = await live.deliver(actor.guild, actor.user, build_id, channel,
                                                post_title=str(data.get("forum_title") or "") or None)
            if message is not None:
                results.append(message.jump_url)
                core.audit(actor, "build.sent", "build", build_id, f"channel={channel.id} site")
            if error:
                problems.append(error)
    if not results:
        return _json_error("\n".join(problems) or "Не отправилось.")
    note = "Отправлено:\n" + "\n".join(results)
    if problems:
        note += "\n⚠️ " + "\n⚠️ ".join(problems)
    return web.json_response({"ok": True, "id": build_id, "saved": listed, "urls": results, "message": note})


@api
async def api_delete(request, entry, actor):
    from database import delete_message_build
    data = await _body(request)
    row = _row_for(actor, data.get("build_id"), manage=True)
    delete_message_build(row[0])
    core.audit(actor, "build.deleted", "build", row[0], "site")
    return web.json_response({"ok": True, "message": f"Удалено: #{row[0]}."})


@api
async def api_resync(request, entry, actor):
    import live
    data = await _body(request)
    row = _row_for(actor, data.get("build_id"), manage=True)
    updated, removed, added, missing = await live.resync_build(actor.guild, row[0])
    core.audit(actor, "build.resynced", "build", row[0], "site")
    return web.json_response({"ok": True, "message": t("build_tools.resync.done", updated=updated, removed=removed,
                                                       added=added, missing=missing)
                              if core.has_text("build_tools.resync.done") else
                              f"Обновлено: {updated} · удалено лишних: {removed} · дослано: {added} · не найдено: {missing}"})


@api
async def api_version(request, entry, actor):
    from database import get_build_version
    version = get_build_version(int(request.match_info["version_id"]))
    if not version:
        raise Denied("Версия не найдена.")
    _row_for(actor, version[1], manage=True)
    snap = version[3]
    return web.json_response({"ok": True, "payload": {
        "content": snap.get("content") or "", "embeds": json.loads(snap.get("embeds_json") or "[]"),
        "buttons": json.loads(snap.get("buttons_json") or "[]"),
        "interactive": json.loads(snap.get("interactive_json") or "null")}})


@api
async def api_template(request, entry, actor):
    from actions import template_visible
    from database import get_template
    row = get_template(int(request.match_info["template_id"]))
    if not row or row[1] != actor.guild.id or not template_visible(actor, row):
        raise Denied("Шаблон не найден.")
    payload = json.loads(row[5] or "{}")
    return web.json_response({"ok": True, "payload": payload, "name": row[3]})


@api
async def api_template_save(request, entry, actor):
    from actions import strip_secrets
    from database import save_template
    data = await _body(request)
    state, error = clean(actor, data.get("payload"))
    if error:
        return _json_error(error)
    name = str(data.get("name") or "").strip()[:100]
    if not name:
        return _json_error("Впиши название шаблона.")
    payload = {"content": state.content, "embeds": state.embeds, "buttons": strip_secrets(state.buttons),
               "interactive": state.interactive, "layout": state.layout}
    template_id = save_template(guild_id=actor.guild.id, owner_id=actor.user.id, name=name, template_type="message",
                                payload_json=json.dumps(payload, ensure_ascii=False),
                                visibility="public" if data.get("public") else "private",
                                category=str(data.get("category") or "general")[:50])
    core.audit(actor, "template.created", "template", template_id, "site")
    return web.json_response({"ok": True, "id": template_id, "message": f"Шаблон сохранён: #{template_id}."})


# ---------- автоматика ----------

@api
async def api_schedule(request, entry, actor):
    import automation
    import live
    from database import add_schedule, delete_schedule, get_schedule, get_schedules, update_schedule
    data = await _body(request)
    row = _row_for(actor, data.get("build_id"), automate=True)
    action = data.get("action")
    if action == "add":
        if len(get_schedules(build_id=row[0])) >= automation.MAX_SCHEDULES_PER_BUILD:
            return _json_error(t("automation.too_many", max=automation.MAX_SCHEDULES_PER_BUILD))
        when = automation.parse_when(str(data.get("when") or ""))
        if when is None:
            return _json_error(t("automation.bad_when"))
        interval = automation.parse_interval(str(data.get("repeat") or ""))
        if interval is None:
            return _json_error(t("automation.bad_interval", min=automation.MIN_INTERVAL_MINUTES))
        channel = actor.guild.get_channel_or_thread(int(data.get("channel_id") or 0))
        if channel is None:
            return _json_error("Выбери канал.")
        problem = live.can_post(actor.user, channel)
        if problem:
            return _json_error(t(problem, channel=channel.mention))
        sid = add_schedule(actor.guild.id, row[0], actor.user.id, channel.id, int(when.timestamp()), interval)
        core.audit(actor, "schedule.created", "build", row[0], f"schedule={sid} site")
    else:
        schedule = get_schedule(int(data.get("id") or 0))
        if not schedule or schedule[2] != row[0]:
            return _json_error("Расписание не найдено.")
        if action == "toggle":
            fields = {"enabled": 0 if schedule[7] else 1, "last_error": None}
            if not schedule[7] and schedule[5] <= int(time.time()):
                fields["next_run"] = automation._next_run(schedule, int(time.time())) or int(time.time()) + 60
            update_schedule(schedule[0], **fields)
        elif action == "delete":
            delete_schedule(schedule[0])
            core.audit(actor, "schedule.deleted", "build", row[0], f"schedule={schedule[0]} site")
    return web.json_response({"ok": True, "build": build_info(actor, row)})


@api
async def api_trigger(request, entry, actor):
    import automation
    import live
    from database import add_trigger, delete_trigger, get_trigger, get_triggers, update_trigger
    data = await _body(request)
    row = _row_for(actor, data.get("build_id"), automate=True)
    action = data.get("action")
    if action == "add":
        if len(get_triggers(build_id=row[0])) >= automation.MAX_TRIGGERS_PER_BUILD:
            return _json_error(t("automation.too_many", max=automation.MAX_TRIGGERS_PER_BUILD))
        event = data.get("event")
        channel = actor.guild.get_channel_or_thread(int(data.get("channel_id") or 0)) if data.get("channel_id") else None
        pattern, cooldown = None, 30
        if event == "member_join":
            if channel is None:
                return _json_error("Выбери канал, куда отправлять приветствие.")
        elif event == "thread_create":
            if channel is None:
                return _json_error("Выбери канал или форум, где следить за новыми ветками.")
        elif event == "keyword":
            pattern = ", ".join(w.strip() for w in str(data.get("words") or "").split(",") if w.strip())
            if not pattern:
                return _json_error(t("automation.keyword.empty"))
            raw = str(data.get("cooldown") or "60")
            if not raw.isdigit() or int(raw) < 10:
                return _json_error(t("automation.keyword.bad_cooldown"))
            cooldown = int(raw)
        else:
            return _json_error("Неизвестный триггер.")
        if channel is not None and not isinstance(channel, discord.ForumChannel):
            problem = live.can_post(actor.user, channel)
            if problem:
                return _json_error(t(problem, channel=channel.mention))
        trigger_id = add_trigger(actor.guild.id, row[0], actor.user.id, event, pattern=pattern,
                                 watch_channel_id=channel.id if channel and event != "member_join" else None,
                                 target_channel_id=channel.id if channel and event == "member_join" else None,
                                 cooldown_seconds=cooldown)
        automation.invalidate_triggers(actor.guild.id)
        core.audit(actor, "trigger.created", "build", row[0], f"trigger={trigger_id} event={event} site")
    else:
        trigger = get_trigger(int(data.get("id") or 0))
        if not trigger or trigger[2] != row[0]:
            return _json_error("Триггер не найден.")
        if action == "toggle":
            update_trigger(trigger[0], enabled=0 if trigger[9] else 1, last_error=None)
        elif action == "delete":
            delete_trigger(trigger[0])
            core.audit(actor, "trigger.deleted", "build", row[0], f"trigger={trigger[0]} site")
        automation.invalidate_triggers(actor.guild.id)
    return web.json_response({"ok": True, "build": build_info(actor, row)})


@api
async def api_settings(request, entry, actor):
    import automation
    from actions import build_visible
    from database import get_build_settings, get_child_builds, get_message_build, set_build_settings
    data = await _body(request)
    row = _row_for(actor, data.get("build_id"), automate=True)
    settings = get_build_settings(row[0])
    minutes = int(data.get("live_minutes") or 0)
    if 0 < minutes < automation.MIN_LIVE_MINUTES:
        return _json_error(t("automation.live.bad", min=automation.MIN_LIVE_MINUTES))
    settings["live_minutes"] = minutes or None
    parent_id = int(data.get("parent_id") or 0)
    if parent_id:
        parent = get_message_build(parent_id)
        if parent_id in {row[0], *get_child_builds(row[0])} or not parent or parent[1] != actor.guild.id \
                or not build_visible(actor, parent):
            return _json_error("Этот build нельзя сделать родителем стиля.")
    settings["parent_id"] = parent_id or None
    variants = []
    for variant in (data.get("variants") or [])[:10]:
        target = get_message_build(int(variant.get("build_id") or 0))
        if not target or target[1] != actor.guild.id or not build_visible(actor, target) or target[0] == row[0]:
            continue
        if variant.get("level") in ("staff", "admin"):
            variants.append({"level": variant["level"], "build_id": target[0]})
        elif variant.get("roles"):
            roles = [int(r) for r in variant["roles"] if str(r).isdigit()][:10]
            if roles:
                variants.append({"roles": roles, "build_id": target[0]})
    settings["variants"] = variants or None
    set_build_settings(row[0], {k: v for k, v in settings.items() if v not in (None, [], 0, "")})
    core.audit(actor, "build.settings", "build", row[0], "site")
    return web.json_response({"ok": True, "build": build_info(actor, row)})


async def _avatar_bytes(url):
    """Аватарка вебхука: своя загрузка с сайта — с диска, иначе скачать (до 8 МБ)."""
    import web_panel
    if not url:
        return None
    prefix = f"{web_panel.panel_url()}/assets/"
    if url.startswith(prefix):
        path = web_panel.ASSETS_DIR / url[len(prefix):].split("?")[0]
        return path.read_bytes() if path.is_file() else None
    return await core.fetch_bytes(url, max_bytes=8 * 1024 * 1024)


def add_routes(app):
    app.router.add_get("/fonts/{name}", font)
    app.router.add_get("/post/{token}", page)
    app.router.add_get("/post/{token}/api/init", api_init)
    app.router.add_post("/post/{token}/api/parse", api_parse)
    app.router.add_post("/post/{token}/api/upload", api_upload)
    app.router.add_post("/post/{token}/api/check", api_check)
    app.router.add_get("/post/{token}/api/build/{build_id:\\d+}", api_build)
    app.router.add_get("/post/{token}/api/version/{version_id:\\d+}", api_version)
    app.router.add_get("/post/{token}/api/template/{template_id:\\d+}", api_template)
    app.router.add_post("/post/{token}/api/template", api_template_save)
    app.router.add_post("/post/{token}/api/save", api_save)
    app.router.add_post("/post/{token}/api/send", api_send)
    app.router.add_post("/post/{token}/api/delete", api_delete)
    app.router.add_post("/post/{token}/api/resync", api_resync)
    app.router.add_post("/post/{token}/api/schedule", api_schedule)
    app.router.add_post("/post/{token}/api/trigger", api_trigger)
    app.router.add_post("/post/{token}/api/settings", api_settings)


# ============================================================
# /post
# ============================================================

async def send_link(interaction, draft=None, build_id=None, context="site"):
    """Личная ссылка на конструктор (из /embed → «Создать новый» → «На сайте» и из /post)."""
    link = open_link(interaction.guild.id, interaction.user.id, draft=draft, build_id=build_id)
    if link is None:
        await interaction.response.send_message(t("post.disabled"), ephemeral=True)
        return
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label=t("site.open")[:80], emoji="🌐", url=link))
    kwargs = dict(embed=core.panel_embed(interaction, context, hints=False), view=view, ephemeral=True)
    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


class ClanPostView(discord.ui.View):
    """/post — посты для кланов: один вопрос — что делаем."""

    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Новость — напишет нейросеть", emoji="📰", style=discord.ButtonStyle.primary)
    async def news(self, interaction, button):
        await interaction.response.send_modal(NewsModal())

    @discord.ui.button(label="Пустой пост в стиле кланов", emoji="🧩", style=discord.ButtonStyle.secondary)
    async def blank(self, interaction, button):
        import news_generator
        await interaction.response.defer(ephemeral=True, thinking=True)
        draft = await news_generator.clan_template()
        await send_link(interaction, draft=draft, context="post.blank")


class NewsModal(core.Modal, title="НОВОСТЬ ДЛЯ КЛАНОВ"):
    def __init__(self):
        super().__init__(title=t("post.news_modal.title")[:45])
        self.brief = discord.ui.TextInput(style=discord.TextStyle.paragraph, min_length=3, max_length=4000,
                                          placeholder=t("post.news_modal.placeholder")[:100])
        self.add_item(discord.ui.Label(text=t("post.news_modal.label")[:45],
                                       description=t("post.news_modal.help")[:100], component=self.brief))

    async def on_submit(self, interaction):
        import news_generator
        await news_generator.run(interaction, self.brief.value)


def register(bot):
    global _bot
    _bot = bot

    @bot.tree.command(name="post", description="Посты для кланов: новость с нейросетью или пост в клановом оформлении")
    @discord.app_commands.guild_only()
    @discord.app_commands.describe(news="О чём новость — нейросеть напишет текст, бот нарисует баннер и соберёт пост")
    async def post_command(interaction, news: discord.app_commands.Range[str, 3, 4000] = None):
        if not await core.require_command_access(interaction, "post"):
            return
        if news:
            import news_generator
            await news_generator.run(interaction, news)
            return
        await interaction.response.send_message(embed=core.panel_embed(interaction, "post", hints=False),
                                                view=ClanPostView(), ephemeral=True)
