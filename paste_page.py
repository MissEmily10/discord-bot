"""
paste_page.py
=============
Временная страница для длинного текста: в окне Discord поле вмещает только
4000 символов, а JSON сообщения или текст новости бывают длиннее. Бот выдаёт
ссылку на 5 минут — на странице большое поле (можно сразу с текущим
содержимым), «Готово» отправляет текст боту, и он применяет его там, откуда
открыли страницу.

Страница живёт на веб-панели (WEB_PANEL_PORT / WEB_PANEL_URL), ссылка —
одноразовая: после успешной отправки или через 5 минут не работает.
"""

import html
import json
import logging
import secrets
import time

from aiohttp import web

_log = logging.getLogger(__name__)

PASTE_TTL = 5 * 60
MAX_TEXT = 300_000
_pastes = {}  # token -> {"expires", "title", "hint", "initial", "handler", "user_id"}


def available():
    import web_panel
    return web_panel.panel_port() is not None and bool(web_panel.panel_url())


def _purge():
    now = time.time()
    for token in [k for k, v in _pastes.items() if v["expires"] <= now]:
        _pastes.pop(token, None)


def create(title, hint, handler, initial="", user_id=None):
    """
    handler(text) -> (ok, сообщение) — async, вызывается в процессе бота.
    -> ссылка на страницу или None (веб-панель не настроена).
    """
    import web_panel
    if not available():
        return None
    _purge()
    token = secrets.token_urlsafe(24)
    _pastes[token] = {"expires": time.time() + PASTE_TTL, "title": title, "hint": hint,
                      "initial": initial or "", "handler": handler, "user_id": user_id}
    return f"{web_panel.panel_url()}/paste/{token}"


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ color-scheme: dark; --bg:#17141c; --card:#221d29; --text:#f3eef7; --muted:#b3a9bd; --accent:#e58bb5; --ok:#7fd29a; --bad:#ff8a8a; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:16px/1.45 system-ui, sans-serif; }}
main {{ max-width: 980px; margin: 0 auto; padding: 24px 16px 40px; }}
h1 {{ font-size: 22px; margin: 0 0 6px; }}
p {{ color: var(--muted); margin: 0 0 14px; white-space: pre-line; }}
textarea {{ width:100%; min-height: 60vh; background: var(--card); color: var(--text); border: 1px solid #3a3242;
  border-radius: 12px; padding: 14px; font: 14px/1.5 ui-monospace, Menlo, Consolas, monospace; resize: vertical; }}
.row {{ display:flex; gap:12px; align-items:center; margin-top:12px; flex-wrap: wrap; }}
button {{ background: var(--accent); color:#1a1020; border:0; border-radius: 10px; padding: 12px 22px; font-weight:700;
  font-size:16px; cursor:pointer; }}
button:disabled {{ opacity: .5; cursor: default; }}
#status {{ white-space: pre-line; }}
.ok {{ color: var(--ok); }} .bad {{ color: var(--bad); }}
#timer {{ color: var(--muted); font-size: 14px; margin-left: auto; }}
</style></head>
<body><main>
<h1>{title}</h1>
<p>{hint}</p>
<textarea id="text" spellcheck="false">{initial}</textarea>
<div class="row"><button id="send">Готово — отправить боту</button><span id="status"></span><span id="timer"></span></div>
</main>
<script>
const until = Date.now() + {left} * 1000;
const timer = document.getElementById("timer"), status = document.getElementById("status"), send = document.getElementById("send");
function tick() {{
  const s = Math.max(0, Math.round((until - Date.now()) / 1000));
  timer.textContent = s ? "ссылка работает ещё " + Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0") : "ссылка истекла — открой новую в Discord";
  if (!s) send.disabled = true; else setTimeout(tick, 1000);
}}
tick();
send.onclick = async () => {{
  send.disabled = true; status.className = ""; status.textContent = "Отправляю…";
  try {{
    const r = await fetch(location.pathname, {{ method: "POST", headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ text: document.getElementById("text").value }}) }});
    const data = await r.json();
    status.className = data.ok ? "ok" : "bad"; status.textContent = data.message;
    if (!data.ok) send.disabled = false;
  }} catch (e) {{ status.className = "bad"; status.textContent = "Не получилось отправить: " + e; send.disabled = false; }}
}};
</script><script src="/ui-icons.js"></script></body></html>"""


async def page(request):
    _purge()
    entry = _pastes.get(request.match_info["token"])
    if entry is None:
        return web.Response(text="Ссылка истекла или уже использована — открой новую в Discord.",
                            content_type="text/plain", charset="utf-8", status=410)
    body = PAGE.format(title=html.escape(entry["title"]), hint=html.escape(entry["hint"]),
                       initial=html.escape(entry["initial"]), left=int(entry["expires"] - time.time()))
    return web.Response(text=body, content_type="text/html", charset="utf-8", headers={"Cache-Control": "no-store"})


async def submit(request):
    _purge()
    token = request.match_info["token"]
    entry = _pastes.get(token)
    if entry is None:
        return web.json_response({"ok": False, "message": "Ссылка истекла или уже использована — открой новую в Discord."})
    if request.content_type != "application/json":
        return web.json_response({"ok": False, "message": "Ожидается JSON."}, status=415)
    try:
        text = str((await request.json()).get("text") or "")
    except (ValueError, json.JSONDecodeError, AttributeError):
        return web.json_response({"ok": False, "message": "Не получилось прочитать текст."})
    if len(text) > MAX_TEXT:
        return web.json_response({"ok": False, "message": f"Слишком длинно: больше {MAX_TEXT} символов."})
    try:
        ok, message = await entry["handler"](text)
    except Exception:  # noqa: BLE001 — ошибку показываем на странице, а не роняем панель
        _log.exception("paste handler failed")
        ok, message = False, "Бот не смог применить текст — подробности в логе бота."
    if ok:
        _pastes.pop(token, None)
    return web.json_response({"ok": bool(ok), "message": message})


def add_routes(app):
    app.router.add_get("/paste/{token}", page)
    app.router.add_post("/paste/{token}", submit)
