"""
scene_editor.py
===============
Редактор холста на сайте — как Figma/Photoshop с базовым набором: живая
картинка, тексты с переменными, дудлы-стикеры, фигуры, свои картинки; слои,
перетаскивание, размер, поворот, прозрачность, выравнивание, отмена.

Открывается личной ссылкой (2 часа с последнего действия):
- /pictures → Баннер сервера → «Редактор на сайте» (весь стафф) —
  холст баннера 960×540; собран из текущего фона и строк;
- живая картинка → «Внешний вид» → «Редактор на сайте» (кто может менять
  картинку) — её же картинка слоем, вокруг — стикеры, тексты, другой размер.

Что собрано, бот рисует сам (scene.py) — с живыми значениями переменных, и
обновляет, как обычную живую картинку / баннер.
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
PAGE = pathlib.Path(__file__).with_name("scene_editor.html")
_sessions = {}  # токен -> {"kind": banner|card, "guild_id", "user_id", "card_id", "expires"}
_card_cache = {}  # (ref, хэш данных) -> (время, PNG)
_bot = None


class Denied(Exception):
    pass


def _actor(guild, member):
    import post_builder
    return post_builder.Actor(guild, member)


def _purge():
    now = time.time()
    for token in [k for k, v in _sessions.items() if v["expires"] <= now]:
        _sessions.pop(token, None)


def open_link(kind, guild_id, user_id, card_id=None):
    import post_builder
    import web_panel
    if not post_builder.available():
        return None
    _purge()
    token = secrets.token_urlsafe(24)
    _sessions[token] = {"kind": kind, "guild_id": guild_id, "user_id": user_id, "card_id": card_id,
                        "expires": time.time() + SESSION_TTL}
    return f"{web_panel.panel_url()}/scene/{token}"


def _context(request):
    """-> (сессия, сервер, участник, живая картинка | None). Права проверяются каждый раз."""
    import cards
    import server_banner
    _purge()
    entry = _sessions.get(request.match_info["token"])
    if entry is None:
        raise Denied("Ссылка истекла — открой редактор заново из Discord.")
    guild = _bot.get_guild(entry["guild_id"]) if _bot else None
    member = guild.get_member(entry["user_id"]) if guild else None
    if member is None:
        raise Denied("Не вижу тебя на сервере — открой редактор заново.")
    actor = _actor(guild, member)
    card = None
    if entry["kind"] == "banner":
        if not server_banner.can_manage(actor):
            raise Denied("Баннер сервера меняет стафф сервера.")
    else:
        card = cards.get_card(entry["card_id"])
        if card is None:
            raise Denied("Этой живой картинки больше нет.")
        if not cards.can_edit(actor, card):
            raise Denied("Нет прав менять эту картинку.")
    entry["expires"] = time.time() + SESSION_TTL
    return entry, guild, member, card


def api(handler):
    async def wrapped(request):
        try:
            entry, guild, member, card = _context(request)
            return await handler(request, entry, guild, member, card)
        except Denied as error:
            return web.json_response({"ok": False, "error": str(error)}, status=403)
        except Exception:  # noqa: BLE001 — показываем на странице, не роняем сайт
            _log.exception("scene editor api failed")
            return web.json_response({"ok": False, "error": "Что-то сломалось на стороне бота — подробности в логе."})
    return wrapped


async def _body(request):
    try:
        data = await request.json()
    except (ValueError, json.JSONDecodeError):
        raise Denied("Не получилось прочитать запрос.")
    return data if isinstance(data, dict) else {}


def _guild_cards(guild):
    import cards
    return cards.get_cards(guild.id)


def _clean(raw, guild, card):
    import scene
    return scene.clean(raw, card_ids={c["id"] for c in _guild_cards(guild)}, self_card=card is not None)


async def _card_parts(guild, card, ref, hide=()):
    """Живая картинка без своего холста (то, что лежит слоем) и её тексты — с кэшем на минуту.
    hide — номера текстов, вынесенных отдельными слоями (их картинка не рисует). -> (PNG, тексты) или None."""
    import cards
    target = card if ref == "self" else cards.get_card(int(ref))
    if target is None or target["guild_id"] != guild.id:
        return None
    data = {k: v for k, v in target["data"].items() if k != "scene"}
    hide = tuple(sorted(set(hide)))
    key = (target["id"], target["title"], json.dumps(data, sort_keys=True, default=str), hide)
    cached = _card_cache.get(key)
    if cached and time.time() - cached[0] < 60:
        return cached[1]
    parts = await cards.render_card_parts(dict(target, data=data), guild, skip=hide)
    if len(_card_cache) > 64:
        _card_cache.clear()
    _card_cache[key] = (time.time(), parts)
    return parts


async def _card_png(guild, card, ref, hide=()):
    parts = await _card_parts(guild, card, ref, hide)
    return parts[0] if parts else None


# ============================================================
# СТРАНИЦА И ДАННЫЕ
# ============================================================

async def page(request):
    _purge()
    if request.match_info["token"] not in _sessions:
        return web.Response(text="Ссылка истекла — открой редактор заново из Discord.", content_type="text/plain",
                            charset="utf-8", status=410)
    return web.FileResponse(PAGE, headers={"Cache-Control": "no-store"})


@api
async def api_init(request, entry, guild, member, card):
    import io
    import post_builder
    import scene
    import server_banner
    import banner_styles
    from database import get_counters
    from PIL import Image
    if card is not None:
        current = card["data"].get("scene")
        saved = bool(current)
        png, items = await _card_parts(guild, card, "self")
        size = Image.open(io.BytesIO(png)).size
        title = card["title"] or "Живая картинка"
        presets = scene.size_presets(size)
        if not current:  # каждый текст картинки — отдельным слоем
            current = scene.for_card(card, size, items)
    else:
        banner = server_banner.get_or_create(guild.id, member.id)
        saved = bool(banner.get("scene"))
        current = banner.get("scene") or scene.from_banner(banner)
        title = "Баннер сервера"
        presets = [p for p in scene.size_presets() if p["key"] == "banner"]
    return web.json_response({
        "ok": True, "kind": entry["kind"], "title": title, "guild": guild.name, "scene": current, "saved": saved,
        "fixed_size": card is None, "presets": presets,
        "fonts": [{"key": key, "label": label, "file": pathlib.Path(file).name,
                   "cyrillic": scene.font_has_cyrillic(key)} for key, (label, file, _) in scene.FONTS.items()],
        "stickers": {key: {"label": scene.STICKER_SETS[key][0], "names": names}
                     for key, names in scene.stickers().items()},
        "styles": [{"key": key, "name": value[0]} for key, value in banner_styles.STYLES.items()],
        "cards": [{"id": c["id"], "title": c["title"] or "—"} for c in _guild_cards(guild)
                  if card is None or c["id"] != card["id"]],
        "variables": post_builder.variables_catalog(),
        "counters": sorted(get_counters(guild.id)),
        "limits": {"layers": scene.MAX_LAYERS},
    })


@api
async def api_values(request, entry, guild, member, card):
    """Тексты с переменными -> те же тексты с живыми значениями (для просмотра на холсте)."""
    import live
    from actions import VARIABLE, substitute
    data = await _body(request)
    texts = [str(text)[:500] for text in (data.get("texts") or [])][:60]
    needed = {(m[1], m[2]) for text in texts for m in VARIABLE.finditer(text)}
    variables = await live.collect(guild, needed) if needed else {}
    return web.json_response({"ok": True, "values": [substitute(text, variables) for text in texts]})


@api
async def api_card(request, entry, guild, member, card):
    ref = request.match_info["ref"]
    if ref != "self" and not ref.isdigit():
        raise web.HTTPNotFound()
    if ref == "self" and card is None:
        raise web.HTTPNotFound()
    hide = [int(v) for v in (request.query.get("hide") or "").split(",") if v.isdigit() and int(v) < 1000][:300]
    png = await _card_png(guild, card, ref, hide)
    if png is None:
        raise web.HTTPNotFound()
    return web.Response(body=png, content_type="image/png", headers={"Cache-Control": "no-store"})


@api
async def api_split(request, entry, guild, member, card):
    """Слой живой картинки -> её тексты отдельными слоями (картинка их больше не рисует)."""
    import io
    import scene
    from PIL import Image
    data = await _body(request)
    ref = data.get("card")
    if ref != "self" and not str(ref or "").isdigit():
        return web.json_response({"ok": False, "error": "Нет такой картинки."})
    if ref == "self" and card is None:
        return web.json_response({"ok": False, "error": "Нет такой картинки."})
    ref = ref if ref == "self" else int(ref)
    parts = await _card_parts(guild, card, ref)
    if parts is None:
        return web.json_response({"ok": False, "error": "Нет такой картинки."})
    png, items = parts
    box = {k: scene._num(data.get(k), -scene.MAX_SIDE, 2 * scene.MAX_SIDE, 0) for k in ("x", "y", "w", "h")}
    if box["w"] < 4 or box["h"] < 4:
        return web.json_response({"ok": False, "error": "Слой картинки слишком маленький."})
    hide, layers = scene.split_layers(ref, items, box, Image.open(io.BytesIO(png)).size)
    return web.json_response({"ok": True, "hide": hide, "layers": layers})


async def sticker(request):
    import scene
    if request.match_info["token"] not in _sessions:
        raise web.HTTPNotFound()
    path = scene.sticker_path(request.match_info["set"], request.match_info["name"])
    if path is None:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


@api
async def api_background(request, entry, guild, member, card):
    """Фон-подсказка для холста: готовый стиль баннера или фон стиля живой картинки — PNG."""
    import asyncio
    import io
    import scene
    w = int(scene._num(request.query.get("w"), scene.MIN_SIDE, scene.MAX_SIDE, 960))
    h = int(scene._num(request.query.get("h"), scene.MIN_SIDE, scene.MAX_SIDE, 540))
    bg = {"color": "#F1EEEC"}
    looks = {}
    if request.query.get("style"):
        bg["style"] = request.query["style"]
    ref = request.query.get("card")
    if ref:
        import cards
        target = card if ref == "self" else (cards.get_card(int(ref)) if ref.isdigit() else None)
        if target is None or target["guild_id"] != guild.id:
            raise web.HTTPNotFound()
        bg["card"] = ref
        looks[ref] = target["data"]

    def build():
        output = io.BytesIO()
        scene._background({"bg": bg}, {}, looks, (w, h)).convert("RGB").save(output, "PNG")
        return output.getvalue()
    return web.Response(body=await asyncio.to_thread(build), content_type="image/png",
                        headers={"Cache-Control": "max-age=300"})


@api
async def api_upload(request, entry, guild, member, card):
    import web_panel
    data = await _body(request)
    raw = str(data.get("data") or "")
    if "," in raw[:100]:
        raw = raw.split(",", 1)[1]
    try:
        content = base64.b64decode(raw, validate=False)
    except ValueError:
        return web.json_response({"ok": False, "error": "Не получилось прочитать картинку."})
    name, error = web_panel.save_asset(content)
    if error:
        return web.json_response({"ok": False, "error": error})
    return web.json_response({"ok": True, "asset": name, "url": f"/assets/{name}"})


@api
async def api_preview(request, entry, guild, member, card):
    """Как нарисует бот — точная картинка с живыми значениями."""
    import scene
    data = await _body(request)
    cleaned, error = _clean(data.get("scene"), guild, card)
    if error:
        return web.json_response({"ok": False, "error": error})
    png = await scene.render_live(cleaned, guild, self_card=card,
                                  size=scene.BANNER if entry["kind"] == "banner" else None)
    return web.json_response({"ok": True, "png": "data:image/png;base64," + base64.b64encode(png).decode()})


@api
async def api_save(request, entry, guild, member, card):
    import cards
    import server_banner
    data = await _body(request)
    cleaned, error = _clean(data.get("scene"), guild, card)
    if error:
        return web.json_response({"ok": False, "error": error})
    actor = _actor(guild, member)
    if card is not None:
        card["data"]["scene"] = cleaned
        cards.save_card(card)
        core.audit(actor, "card.scene_saved", "card", card["id"], f"{len(cleaned['layers'])} layers")
        published = await cards.refresh_message(guild, card)
        note = "Сохранено — картинка в канале обновлена." if published else \
            "Сохранено. Картинка ещё не опубликована — «Где и кто» → «Опубликовать»."
        return web.json_response({"ok": True, "note": note})
    banner = server_banner.get_or_create(guild.id, member.id)
    banner["scene"] = cleaned
    server_banner.save(banner)
    core.audit(actor, "server_banner.scene_saved", "guild", guild.id, f"{len(cleaned['layers'])} layers")
    note = "Сохранено."
    if data.get("apply"):
        done, key = await server_banner.apply(guild, banner, force=True)
        note = "Сохранено и поставлено баннером сервера." if key is None else f"Сохранено, но баннер не поставлен: {t(key)}"
    return web.json_response({"ok": True, "note": note})


@api
async def api_reset(request, entry, guild, member, card):
    """Убрать холст: картинка/баннер снова обычные."""
    import cards
    import server_banner
    if card is not None:
        card["data"].pop("scene", None)
        cards.save_card(card)
        await cards.refresh_message(guild, card)
    else:
        banner = server_banner.get_or_create(guild.id, member.id)
        banner["scene"] = None
        server_banner.save(banner)
    core.audit(_actor(guild, member), "scene.reset", entry["kind"], card["id"] if card else guild.id, None)
    return web.json_response({"ok": True})


@api
async def api_counter(request, entry, guild, member, card):
    """Новая переменная-счётчик ({counter:имя}) — сразу можно вставить в текст."""
    from database import change_counter, get_counters
    data = await _body(request)
    name = str(data.get("name") or "").strip().replace("{", "").replace("}", "").replace(":", "")[:40]
    if not name:
        return web.json_response({"ok": False, "error": "Как назовём счётчик?"})
    start = data.get("value")
    change_counter(guild.id, name, set_to=int(start) if str(start or "").lstrip("-").isdigit() else 0)
    core.audit(_actor(guild, member), "counter.created", "counter", None, name)
    return web.json_response({"ok": True, "name": name, "counters": sorted(get_counters(guild.id))})


def add_routes(app):
    app.router.add_get("/scene/{token}", page)
    app.router.add_get("/scene/{token}/api/init", api_init)
    app.router.add_post("/scene/{token}/api/values", api_values)
    app.router.add_get("/scene/{token}/api/card/{ref}.png", api_card)
    app.router.add_post("/scene/{token}/api/split", api_split)
    app.router.add_get("/scene/{token}/api/bg.png", api_background)
    app.router.add_get("/scene/{token}/sticker/{set}/{name}.png", sticker)
    app.router.add_post("/scene/{token}/api/upload", api_upload)
    app.router.add_post("/scene/{token}/api/preview", api_preview)
    app.router.add_post("/scene/{token}/api/save", api_save)
    app.router.add_post("/scene/{token}/api/reset", api_reset)
    app.router.add_post("/scene/{token}/api/counter", api_counter)


# ============================================================
# ССЫЛКА ИЗ DISCORD
# ============================================================

async def send_link(interaction, kind, card_id=None):
    """Личная ссылка на редактор (эфемерно)."""
    link = open_link(kind, interaction.guild.id, interaction.user.id, card_id)
    if link is None:
        await interaction.response.send_message(t("post.disabled"), ephemeral=True)
        return
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label=t("scene.open")[:80], emoji="🌐", url=link))
    kwargs = dict(embed=core.panel_embed(interaction, f"scene.{kind}", hints=False), view=view, ephemeral=True)
    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


def register(bot):
    global _bot
    _bot = bot
