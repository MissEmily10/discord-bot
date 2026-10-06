"""
web_panel.py
============
Веб-панель владельца: правка текстов, цветов и thumbnails из texts.py
прямо в браузере. Работает внутри процесса бота (aiohttp), пишет в ту же
bot_settings через core.set_setting — изменения применяются сразу.

Вход: владелец пишет /panel в Discord и получает одноразовую ссылку
(10 минут). По ней браузер получает cookie-сессию на 7 дней.
Сессии живут в памяти: после перезапуска бота — снова /panel.

Настройка (.env):
    WEB_PANEL_PORT=<порт из вкладки Network на хостинге>
                   (если не задан — берётся SERVER_PORT от Pterodactyl)
    WEB_PANEL_URL=http://<ip>:<порт>   — адрес, который попадёт в ссылку
Без порта панель не запускается, без URL /panel объяснит, что добавить.
"""

import base64
import hashlib
import io
import logging
import os
import re
import pathlib
import secrets
import string
import time

import discord
from aiohttp import web

import core
from texts import CATALOG, kind

_log = logging.getLogger(__name__)

LOGIN_TTL = 10 * 60
SESSION_TTL = 7 * 24 * 3600
COOKIE = "panel_session"
INDEX_HTML = pathlib.Path(__file__).with_name("web_panel.html")
# Картинки, загруженные через панель (макеты из Figma): отдаются по /assets/<имя>
# без входа — Discord должен их скачать, чтобы показать в embed'е.
ASSETS_DIR = pathlib.Path(os.getenv("WEB_PANEL_ASSETS") or "panel_assets")
MAX_ASSET_BYTES = 8 * 1024 * 1024
ASSET_FORMATS = {"PNG": "png", "JPEG": "jpg", "GIF": "gif", "WEBP": "webp"}
_ASSET_NAME = re.compile(r"^[0-9a-f]{20}\.(png|jpg|gif|webp)$")

_login_tokens = {}  # token -> expires_at
_sessions = {}      # session_id -> expires_at
_bot = None


# =========================
# CONFIG
# =========================

def panel_port():
    raw = os.getenv("WEB_PANEL_PORT") or os.getenv("SERVER_PORT")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def panel_url():
    return (os.getenv("WEB_PANEL_URL") or "").strip().rstrip("/")


def _purge(store):
    now = time.time()
    for key in [k for k, expires in store.items() if expires <= now]:
        del store[key]


def create_login_link():
    _purge(_login_tokens)
    token = secrets.token_urlsafe(32)
    _login_tokens[token] = time.time() + LOGIN_TTL
    return f"{panel_url()}/login?token={token}"


# =========================
# CATALOG -> ПОЛЯ РЕДАКТОРА
# =========================

def screen_contexts():
    """
    Экраны с embed'ом: у них есть <экран>.title. Модалки тоже имеют .title,
    но thumbnail им не нужен — их префиксы содержат "modal".
    """
    return sorted(
        key[: -len(".title")]
        for key in CATALOG
        if key.endswith(".title") and "modal" not in key.rsplit(".", 2)[-2]
    )


def editable_keys():
    """
    Ключи в порядке каталога (он идёт по ходу экранов бота); thumbnail экрана —
    сразу после его заголовка, даже если в каталоге для него нет строки.
    """
    contexts = set(screen_contexts())
    keys = []
    for key in CATALOG:
        if key.endswith(".thumbnail") and key[: -len(".thumbnail")] in contexts:
            continue  # добавится после .title
        keys.append(key)
        if key.endswith(".title") and key[: -len(".title")] in contexts:
            keys.append(key[: -len(".title")] + ".thumbnail")
    return keys


def group_of(key):
    if "." not in key or key.startswith(("thumbnail.", "banner.")):
        return "style"
    return key.split(".", 1)[0]


def placeholders(template):
    try:
        return sorted({name for _, name, _, _ in string.Formatter().parse(template) if name})
    except ValueError:
        return []


def entry(key):
    default = CATALOG.get(key, "")
    overrides = core.overrides()
    return {
        "key": key,
        "default": default,
        "value": overrides.get(key),
        "kind": kind(key),
        "group": group_of(key),
        "placeholders": placeholders(default),
    }


def validate(key, raw):
    """
    -> (значение для сохранения или None = сбросить к дефолту, ошибка или None)
    """
    value = (raw or "").strip() if kind(key) != "text" else (raw or "")
    if not value.strip():
        return None, None

    if kind(key) == "color":
        hex_value = value.strip().lower().replace("#", "")
        if hex_value.startswith("0x"):
            hex_value = hex_value[2:]
        if len(hex_value) != 6 or any(ch not in "0123456789abcdef" for ch in hex_value):
            return None, "Цвет должен быть в формате #RRGGBB."
        return "0x" + hex_value.upper(), None

    if kind(key) == "image":
        if value.lower() == "none" or value == "{bot_avatar}":
            return value, None
        if len(value) > 1000 or " " in value:
            return None, "Нужна прямая ссылка на картинку, none или {bot_avatar}."
        return value if value.startswith(("http://", "https://")) else "https://" + value, None

    if len(value) > 4000:
        return None, "Слишком длинно: максимум 4000 символов."
    try:
        value.format_map(_AnyName())
    except (ValueError, IndexError, AttributeError):
        return None, "Сломаны фигурные скобки: подстановки пишутся как {name}."
    unknown = set(placeholders(value)) - set(placeholders(CATALOG.get(key, "")))
    if unknown:
        names = ", ".join("{" + name + "}" for name in sorted(unknown))
        return None, f"Таких подстановок у этой фразы нет: {names}."
    return value, None


class _AnyName(dict):
    def __missing__(self, key):
        return ""


# =========================
# HTTP
# =========================

def _session_ok(request):
    _purge(_sessions)
    sid = request.cookies.get(COOKIE)
    return bool(sid) and sid in _sessions


_SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    # картинки превью — внешние ссылки, поэтому img-src шире остального
    "Content-Security-Policy": (
        "default-src 'self'; img-src * data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
}


@web.middleware
async def security_headers(request, handler):
    try:
        response = await handler(request)
    except web.HTTPException as error:
        response = error
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if isinstance(response, web.HTTPException) and response.status >= 300:
        raise response
    return response


@web.middleware
async def auth_middleware(request, handler):
    if request.path.startswith("/api/"):
        if not _session_ok(request):
            return web.json_response({"error": "Сессия истекла — получи новую ссылку через /panel."}, status=401)
        # SameSite=Strict + обязательный JSON закрывают CSRF через обычные формы
        if request.method != "GET" and request.content_type != "application/json":
            return web.json_response({"error": "Ожидается JSON."}, status=415)
    return await handler(request)


async def index(request):
    if not _session_ok(request):
        return web.Response(
            text="Вход в панель — через команду /panel в Discord.",
            content_type="text/plain", charset="utf-8", status=401,
        )
    return web.FileResponse(INDEX_HTML, headers={"Cache-Control": "no-store"})


async def login(request):
    _purge(_login_tokens)
    token = request.query.get("token", "")
    if not token or _login_tokens.pop(token, None) is None:
        return web.Response(
            text="Ссылка недействительна или уже использована. Получи новую через /panel.",
            content_type="text/plain", charset="utf-8", status=403,
        )
    _purge(_sessions)
    sid = secrets.token_urlsafe(32)
    _sessions[sid] = time.time() + SESSION_TTL
    response = web.HTTPFound("/")
    response.set_cookie(
        COOKIE, sid, max_age=SESSION_TTL, httponly=True, samesite="Strict",
        secure=panel_url().startswith("https://"),
    )
    raise response


async def logout(request):
    _sessions.pop(request.cookies.get(COOKIE), None)
    response = web.json_response({"ok": True})
    response.del_cookie(COOKIE)
    return response


async def get_settings(request):
    user = _bot.user if _bot else None
    return web.json_response({
        "entries": [entry(key) for key in editable_keys()],
        "contexts": screen_contexts(),
        "command_of": {context: core.command_of(context) for context in screen_contexts()},
        "uploads": bool(panel_url()),
        "bot": {
            "name": user.name if user else "Bot",
            "avatar": user.display_avatar.url if user else None,
        },
    })


async def put_setting(request):
    try:
        body = await request.json()
    except ValueError:
        return web.json_response({"error": "Некорректный JSON."}, status=400)
    key = body.get("key")
    if key not in editable_keys():
        return web.json_response({"error": "Неизвестный ключ."}, status=404)
    value, error = validate(key, body.get("value"))
    if error:
        return web.json_response({"error": error}, status=422)
    if value is None or value == CATALOG.get(key):
        core.reset_setting(key)
    else:
        core.set_setting(key, value)
    _log.info("web panel: %s %s", "reset" if value is None else "set", key)
    return web.json_response(entry(key))


def save_asset(raw):
    """Байты картинки -> (имя файла, None) или (None, ошибка). Одинаковые файлы не дублируются."""
    if len(raw) > MAX_ASSET_BYTES:
        return None, "Файл больше 8 МБ."
    try:
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as image:
            fmt = image.format
            image.verify()
    except Exception:
        return None, "Это не картинка (нужен PNG, JPG, GIF или WebP)."
    ext = ASSET_FORMATS.get(fmt)
    if ext is None:
        return None, "Формат не поддерживается: нужен PNG, JPG, GIF или WebP."
    name = f"{hashlib.sha256(raw).hexdigest()[:20]}.{ext}"
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    path = ASSETS_DIR / name
    if not path.exists():
        path.write_bytes(raw)
    return name, None


async def upload_asset(request):
    if not panel_url():
        return web.json_response({"error": "Загрузка работает, когда в .env задан WEB_PANEL_URL."}, status=409)
    try:
        body = await request.json()
        raw = base64.b64decode(body.get("data") or "", validate=True)
    except (ValueError, TypeError):
        return web.json_response({"error": "Файл не прочитался, попробуй ещё раз."}, status=400)
    name, error = save_asset(raw)
    if error:
        return web.json_response({"error": error}, status=422)
    _log.info("web panel: uploaded asset %s (%d bytes)", name, len(raw))
    return web.json_response({"url": f"{panel_url()}/assets/{name}"})


async def get_asset(request):
    name = request.match_info["name"]
    path = ASSETS_DIR / name
    if not _ASSET_NAME.match(name) or not path.is_file():
        raise web.HTTPNotFound()
    # имя = хэш содержимого, поэтому файл по этому адресу никогда не меняется
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


def build_app():
    # base64 картинки до 8 МБ занимает ~11 МБ
    app = web.Application(middlewares=[security_headers, auth_middleware], client_max_size=12 * 1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/login", login)
    app.router.add_post("/api/logout", logout)
    app.router.add_get("/api/settings", get_settings)
    app.router.add_put("/api/settings", put_setting)
    app.router.add_post("/api/assets", upload_asset)
    app.router.add_get("/assets/{name}", get_asset)
    return app


async def start(bot):
    """Запуск из setup_hook. Без порта — панель выключена, бот работает как обычно."""
    global _bot
    _bot = bot
    port = panel_port()
    if port is None:
        _log.info("Веб-панель выключена: не задан WEB_PANEL_PORT.")
        return None
    runner = web.AppRunner(build_app(), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"Веб-панель слушает порт {port}" + (f" ({panel_url()})" if panel_url() else ""))
    return runner


# =========================
# /panel
# =========================

def register_panel(bot):
    @bot.tree.command(name="panel", description="Ссылка на веб-панель настроек бота")
    async def panel(interaction):
        if not core.is_owner(interaction):
            await interaction.response.send_message(core.t("common.owner_only"), ephemeral=True)
            return
        if panel_port() is None or not panel_url():
            await interaction.response.send_message(core.t("panel.disabled"), ephemeral=True)
            return
        link = create_login_link()
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label=core.t("panel.open")[:80], url=link))
        await interaction.response.send_message(core.t("panel.link"), view=view, ephemeral=True)
