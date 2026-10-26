"""
site_icons.py
=============
Дудлы вместо обычных эмодзи на всех страницах сайта бота (конструктор
/post, редактор холста /scene, веб-панель, временные страницы) — тем же
набором, что и на кнопках в Discord (ui_emoji: дудлы создателя бота или
объёмные 3D; «standard» — обычные эмодзи и на сайте).

/ui-icons.js — маленький скрипт: находит в тексте страницы эмодзи из
ui_emoji.MAP (+ свои значки сайта ниже) и ставит вместо них картинку
/ui-icon/<имя>.png. Не трогает то, что пишут люди: поля ввода, предпросмотр
сообщения в стиле Discord, холст редактора ([data-no-doodle], #stage).
"""

import json
import pathlib
import re

from aiohttp import web

import ui_emoji

ROOT = pathlib.Path(__file__).resolve().parent / "panel_art" / "emoji"
_NAME = re.compile(r"^(ui|u3|d3|doodle)_[a-z0-9_]{1,30}$")
# значки, что есть только на сайте (в Discord их нет)
SITE_EXTRA = {
    "🐱": "d3_cat", "🅣": "ui_text", "⬛": "ui_box", "📊": "ui_trophy", "🖐": "ui_button", "📢": "ui_announce",
    "😀": "d3_cat", "🔣": "ui_text", "🔤": "ui_text", "✦": "ui_star", "🔓": "ui_lock", "⬆": "ui_send",
    "⛶": "ui_image", "✕": "ui_cancel", "⏱": "ui_time",
}


def _file(name):
    """Имя значка -> файл с учётом набора (3d — объёмные u3_/d3_, иначе плоские ui_/doodle_)."""
    three_d = ui_emoji.style() == "3d"
    if name.startswith("d3_"):
        flat = ROOT / f"doodle_{name[3:]}.png"
        volume = ROOT / "doodle3d" / f"{name}.png"
        return volume if three_d and volume.is_file() else flat
    if name.startswith("ui_"):
        volume = ROOT / "ui3d" / f"u3_{name[3:]}.png"
        flat = ROOT / "ui" / f"{name}.png"
        return volume if three_d and volume.is_file() else flat
    return None


def icon_map():
    """{эмодзи без FE0F: адрес картинки} — для текущего набора; у «standard» пусто."""
    if not ui_emoji.enabled():
        return {}
    found = {}
    for emoji, name in {**ui_emoji.MAP, **SITE_EXTRA}.items():
        key = emoji.replace("️", "")
        path = _file(name)
        if path is not None and path.is_file():
            found[key] = f"/ui-icon/{name}.png"
    return found


SCRIPT = r"""(function () {
  const MAP = __MAP__;
  const keys = Object.keys(MAP).sort((a, b) => b.length - a.length);
  if (!keys.length) return;
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const RE = new RegExp("(" + keys.map(esc).join("|") + ")️?", "gu");
  const SKIP = "textarea,input,select,option,script,style,[contenteditable],[data-no-doodle],#stage,#preview";
  const css = document.createElement("style");
  css.textContent = "img.dd{height:1.2em;width:auto;vertical-align:-0.25em;margin:0 .06em;pointer-events:none}";
  document.head.appendChild(css);
  function swap(node) {
    const text = node.nodeValue;
    RE.lastIndex = 0;
    if (!RE.test(text)) return;
    const parent = node.parentNode;
    if (!parent || (parent.closest && parent.closest(SKIP))) return;
    const frag = document.createDocumentFragment();
    let last = 0;
    RE.lastIndex = 0;
    for (const m of text.matchAll(RE)) {
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      const img = document.createElement("img");
      img.className = "dd"; img.alt = m[1]; img.src = MAP[m[1]]; img.draggable = false;
      frag.appendChild(img);
      last = m.index + m[0].length;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    parent.replaceChild(frag, node);
  }
  function walk(root) {
    if (root.nodeType === 3) { swap(root); return; }
    if (root.nodeType !== 1 || (root.closest && root.closest(SKIP))) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(swap);
  }
  let pending = new Set(), scheduled = false;
  function flush() { scheduled = false; const list = [...pending]; pending.clear(); list.forEach(walk); }
  new MutationObserver(records => {
    for (const r of records) {
      if (r.type === "characterData") pending.add(r.target);
      r.addedNodes.forEach(n => pending.add(n));
    }
    if (!scheduled) { scheduled = true; requestAnimationFrame(flush); }
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  if (document.body) walk(document.body);
  else document.addEventListener("DOMContentLoaded", () => walk(document.body));
})();
"""


async def script(request):
    body = SCRIPT.replace("__MAP__", json.dumps(icon_map(), ensure_ascii=False))
    return web.Response(text=body, content_type="application/javascript", charset="utf-8",
                        headers={"Cache-Control": "no-cache"})


async def icon(request):
    name = request.match_info["name"]
    if not _NAME.match(name):
        raise web.HTTPNotFound()
    path = _file(name) if not name.startswith(("u3_", "doodle_")) else None
    if path is None or not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


def add_routes(app):
    app.router.add_get("/ui-icons.js", script)
    app.router.add_get("/ui-icon/{name}.png", icon)
