"""
post_builder.py
===============
Конструктор постов на сайте бота — как message.style / Discohook: слева
редактор (текст, до 10 embed'ов, поля, картинки, автор, подпись, кнопки,
от чьего имени), справа живой предпросмотр в стиле Discord. Готовый пост
бот отправляет в канал (от своего имени или временным вебхуком с любым
именем и аватаркой) и/или сохраняет как Message Build — дальше его можно
править в /embed, повесить действия на кнопки, расписание и т. д.

/post -> одноразовая ссылка (личная, 2 часа с последнего действия). Права
те же, что в Discord: писать можно туда, куда может сам человек; вебхук —
admin+ или «Управлять вебхуками»; действия кнопок — как при сборке в /embed.

Страница: builder.html (рядом). API — /post/<токен>/api/…
"""

import base64
import json
import logging
import pathlib
import secrets
import time

import discord
from aiohttp import web

import core
from core import t

_log = logging.getLogger(__name__)

SESSION_TTL = 2 * 3600
PAGE = pathlib.Path(__file__).with_name("builder.html")
_sessions = {}  # токен -> {"guild_id", "user_id", "expires", "draft"}
_bot = None
core.COMMAND_MIN_LEVELS.setdefault("post", "staff")

CHANNEL_TYPES = (discord.TextChannel, discord.ForumChannel)


class Actor:
    """Кто работает в конструкторе — как interaction для проверок прав (sanitize, validate)."""

    def __init__(self, guild, member):
        self.guild, self.user, self.client = guild, member, _bot
        self.channel_id = None
        self.message = None


def available():
    import web_panel
    return web_panel.panel_port() is not None and bool(web_panel.panel_url())


def _purge():
    now = time.time()
    for token in [k for k, v in _sessions.items() if v["expires"] <= now]:
        _sessions.pop(token, None)


def open_link(guild_id, user_id, draft=None):
    """Новая сессия конструктора -> ссылка или None (сайт не настроен)."""
    import web_panel
    if not available():
        return None
    _purge()
    token = secrets.token_urlsafe(24)
    _sessions[token] = {"guild_id": guild_id, "user_id": user_id, "expires": time.time() + SESSION_TTL,
                        "draft": draft}
    return f"{web_panel.panel_url()}/post/{token}"


class Denied(Exception):
    pass


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
            rows.append({"id": bid, "name": name or t("embed.default_name")})
    return rows[:100]


def clean(actor, payload):
    """Payload со страницы -> (безопасный payload, ошибка-текст | None). Лимиты Discord — как в /embed."""
    from build_tools import sanitize_payload
    from embed_module import EmbedState, state_problem
    if not isinstance(payload, dict):
        return None, "Пустой пост."
    safe, _ = sanitize_payload(actor, payload)
    safe["content"] = str(payload.get("content") or "")
    state = EmbedState(actor.guild.id, actor.user.id)
    state.load_payload(safe)
    state.layout = payload.get("layout") if payload.get("layout") in ("single", "split") else "single"
    problem = state_problem(state)
    if problem:
        return None, t(problem[0], **problem[1])
    if len(state.content) > 2000:
        return None, f"Текст сообщения длиннее 2000 символов ({len(state.content)})."
    return state, None


def save_state(actor, state, name, listed, build_id=None):
    """EmbedState -> build (новый или поверх своего). -> id."""
    from embed_module import can_manage_build
    from database import get_message_build, save_message_build, update_message_build, set_build_listed
    fields = dict(
        name=(name or t("embed.default_name"))[:100], content=state.content,
        embeds_json=json.dumps(state.embeds, ensure_ascii=False),
        buttons_json=json.dumps(state.buttons, ensure_ascii=False),
        interactive_json=json.dumps(state.interactive, ensure_ascii=False),
    )
    row = get_message_build(build_id) if build_id else None
    if row and row[1] == actor.guild.id and can_manage_build(actor, row):
        from build_tools import remember_version
        remember_version(build_id, actor.user.id)
        update_message_build(build_id, layout=state.layout, visibility=row[7], category=row[8],
                             allowed_role_ids_json=row[9], visibility_levels_json=row[10], **fields)
        if listed:
            set_build_listed(build_id, True)
        return build_id
    return save_message_build(guild_id=actor.guild.id, owner_id=actor.user.id, listed=listed, layout=state.layout,
                              visibility="private", category="general", allowed_role_ids_json="[]",
                              visibility_levels_json="[]", **fields)


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
            return await handler(request, *_context(request))
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


@api
async def api_init(request, entry, guild, member):
    import live
    actor = Actor(guild, member)
    me = guild.me
    return web.json_response({
        "ok": True,
        "guild": guild.name,
        "user": {"name": member.display_name, "avatar": _avatar(member)},
        "bot": {"name": me.display_name if me else "Purr", "avatar": _avatar(me) if me else ""},
        "can_webhook": live.can_use_webhooks(guild, member),
        "can_upload": True,
        "channels": channels_for(guild, member),
        "builds": builds_for(actor),
        "draft": entry.get("draft"),
        "limits": {"embeds": 10, "buttons": 20, "content": 2000},
    })


@api
async def api_parse(request, entry, guild, member):
    from build_tools import payload_from_json_text
    data = await _body(request)
    payload, error = payload_from_json_text(str(data.get("text") or ""))
    if error:
        key, params = error if isinstance(error, tuple) else (error, {})
        return _json_error(t(key, **params))
    return web.json_response({"ok": True, "payload": payload})


@api
async def api_upload(request, entry, guild, member):
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
async def api_build(request, entry, guild, member):
    from actions import build_visible, load_source
    from database import get_message_build
    build_id = int(request.match_info["build_id"])
    row = get_message_build(build_id)
    if not row or row[1] != guild.id or not build_visible(Actor(guild, member), row):
        return _json_error("Сообщение не найдено или недоступно.")
    data = load_source("b", build_id) or {}
    return web.json_response({"ok": True, "build": {
        "id": build_id, "name": row[3] or "", "content": data.get("content") or "",
        "embeds": data.get("embeds") or [], "buttons": data.get("buttons") or [],
        "layout": row[14] if len(row) > 14 else "single"}})


@api
async def api_save(request, entry, guild, member):
    data = await _body(request)
    actor = Actor(guild, member)
    state, error = clean(actor, data.get("payload"))
    if error:
        return _json_error(error)
    build_id = save_state(actor, state, data.get("name"), listed=True, build_id=data.get("build_id"))
    core._log.info("post builder: build %s saved by %s", build_id, member.id)
    return web.json_response({"ok": True, "id": build_id,
                              "message": f"Сохранено как Message Build #{build_id} — его видно в /embed → «Мои сохранённые»."})


@api
async def api_send(request, entry, guild, member):
    import live
    data = await _body(request)
    actor = Actor(guild, member)
    channel = guild.get_channel_or_thread(int(data.get("channel_id") or 0))
    if channel is None:
        return _json_error("Выбери канал.")
    state, error = clean(actor, data.get("payload"))
    if error:
        return _json_error(error)
    name = str(data.get("name") or "").strip()
    build_id = save_state(actor, state, name or None, listed=bool(name), build_id=data.get("build_id"))
    if data.get("mode") == "webhook":
        if not live.can_use_webhooks(guild, member):
            return _json_error("Отправлять от другого имени могут админы и те, кто управляет вебхуками.")
        avatar = await _avatar_bytes(str(data.get("webhook_avatar") or ""))
        webhook_name = str(data.get("webhook_name") or member.display_name)[:80] or member.display_name
        message, error = await live.deliver_temp_webhook(guild, member, build_id, channel, webhook_name, avatar)
    else:
        message, error = await live.deliver(guild, member, build_id, channel)
    if message is None:
        return _json_error(error or "Не отправилось.")
    core._log.info("post builder: build %s sent to %s by %s", build_id, channel.id, member.id)
    note = f"Отправлено: {message.jump_url}"
    if error:
        note += f"\n⚠️ {error}"
    return web.json_response({"ok": True, "url": message.jump_url, "id": build_id, "message": note})


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
    app.router.add_get("/post/{token}", page)
    app.router.add_get("/post/{token}/api/init", api_init)
    app.router.add_post("/post/{token}/api/parse", api_parse)
    app.router.add_post("/post/{token}/api/upload", api_upload)
    app.router.add_get("/post/{token}/api/build/{build_id:\\d+}", api_build)
    app.router.add_post("/post/{token}/api/save", api_save)
    app.router.add_post("/post/{token}/api/send", api_send)


# ============================================================
# /post
# ============================================================

async def send_link(interaction, draft=None):
    link = open_link(interaction.guild.id, interaction.user.id, draft=draft)
    if link is None:
        await interaction.response.send_message(t("post.disabled"), ephemeral=True)
        return
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label=t("post.open")[:80], emoji="🌐", url=link))
    await interaction.response.send_message(embed=core.panel_embed(interaction, "post"), view=view, ephemeral=True)


def register(bot):
    global _bot
    _bot = bot

    @bot.tree.command(name="post", description="Конструктор постов на сайте: редактор и живой предпросмотр")
    @discord.app_commands.guild_only()
    async def post_command(interaction):
        if not await core.require_command_access(interaction, "post"):
            return
        await send_link(interaction)
