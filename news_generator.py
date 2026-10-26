"""
news_generator.py
=================
Клановая новость по сути от создателя бота: «/post новость: <о чём>».

1. Нейросеть (clan_info.complete — те же ключи, что для вопросов о кланах)
   пишет новость по разделам и выбирает **самое подходящее** оформление:
   шаблон баннера из 20 (scripts/render_news.py) с текстами под него и дудл
   к каждому разделу (3D-набор бота). Знает правила и планы кланов (docs/).
2. Бот рисует баннер с заголовком новости и собирает пост как у создателя
   бота: сверху баннер отдельным embed'ом, дальше раздел = embed: в строке
   автора — иконка (трофей) и заголовок раздела, текст списком, справа дудл.
3. Пост открывается в конструкторе /post на сайте — там правится всё: тексты,
   картинки, порядок, и оттуда же отправляется. Без сайта — в /embed.
"""

import asyncio
import json
import logging
import pathlib
import re
import sys

import core
from core import t

_log = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parent
ICON_DIRS = (ROOT / "panel_art" / "emoji" / "doodle3d", ROOT / "panel_art" / "emoji" / "ui3d")
SKIP_ICONS = ("u3_num", "u3_back", "u3_next", "u3_prev", "u3_cancel", "u3_clear", "u3_remove", "u3_delete")
SKIP_TEMPLATES = ("photo", "digest")  # нужны своя фотография / сложные карточки
DARK = 0x2B2D31  # полоска embed'а в цвет фона — как в примере
MAX_SECTIONS = 8

PROMPT = """Ты — редактор новостей Discord-сервера. Создатель бота дал суть новости для кланов — напиши по ней новость
на русском и выбери оформление. Опирайся на суть и на документы о кланах ниже; не выдумывай чисел и правил,
которых там нет. Планы называй планами. Пиши для участников («вы»), живо и по делу, без внутренней кухни
(доли владельцев, техника, админские процедуры), если о ней прямо не просили.

Ответ — ТОЛЬКО JSON без пояснений:
{{
  "template": "<ключ шаблона баннера — самый подходящий по смыслу>",
  "banner": {{<тексты для шаблона: те же поля, что в его примере; КОРОТКО — заголовок до 40 символов, текст до 100>}},
  "content": "<необязательная строка над постом, можно пусто>",
  "sections": [
    {{"heading": "<заголовок раздела, до 40 символов>",
      "text": "<текст раздела в Markdown Discord: **жирное**, списки «- », подсписки «  - »; до 900 символов>",
      "icon": "<имя дудла из списка — самый подходящий по смыслу раздела>"}}
  ]
}}
Разделов 2–{max_sections}. Иконки разделов не повторяй, если есть подходящие разные.

ШАБЛОНЫ БАННЕРА (ключ — название — поля с примером):
{templates}

ДУДЛЫ ДЛЯ РАЗДЕЛОВ (имя — что изображено):
{icons}

ДОКУМЕНТЫ О КЛАНАХ:
{docs}"""

ICON_WORDS = {
    "balloon": "воздушный шарик, праздник", "bell": "колокольчик, уведомления", "bolt": "молния, быстро, энергия",
    "book": "книга, правила, история", "brush": "кисть, творчество", "cake": "торт, день рождения",
    "camera": "фотоаппарат", "cat": "кот", "chat": "чат, общение", "cherries": "вишни, мило",
    "cloud": "облако", "clover": "клевер, удача", "coffee": "кофе, уют", "crown": "корона, лидер, глава",
    "dice": "кубик, случайность, игры", "eye": "глаз, обзор, видимость", "film": "кино", "fire": "огонь, огонёк, серия",
    "flag": "флаг, цель, старт", "flower": "цветок", "gamepad": "геймпад, игры", "gear": "шестерёнка, настройки, механика",
    "gem": "кристалл, ценность, премиум", "ghost": "призрак, хэллоуин", "gift": "подарок, награда, казна",
    "globe": "глобус, сервер, мир", "headset": "наушники, войс", "heart": "сердце, любовь, активность",
    "hourglass": "песочные часы, срок, продление", "key": "ключ, доступ", "laptop": "ноутбук",
    "lock": "замок, ограничение", "lollipop": "леденец", "mail": "письмо, заявка", "medal": "медаль, достижение",
    "megaphone": "мегафон, объявление", "mic": "микрофон, голосовой", "moon": "луна, ночь", "mushroom": "гриб",
    "music": "музыка", "palette": "палитра, оформление", "pencil": "карандаш, правка", "planet": "планета",
    "rocket": "ракета, запуск, рост", "shield": "щит, клан, защита", "sparkles": "искры, новое",
    "star": "звезда, лучшие", "sun": "солнце", "trophy": "кубок, победа, топ", "wrench": "гаечный ключ, ремонт",
    "add": "плюс, добавить", "announce": "объявление", "box": "коробка, магазин", "button": "кнопка",
    "confirm": "галочка, подтверждение", "edit": "правка", "folder": "папка", "image": "картинка",
    "library": "библиотека", "list": "список", "mail2": "письмо", "people": "люди, участники, состав",
    "play": "старт", "preview": "просмотр", "refresh": "обновление", "roles": "роли", "save": "дискета, сохранение, создание",
    "search": "поиск", "send": "отправка", "settings": "настройки", "text": "текст", "time": "часы, время",
    "warn": "внимание, предупреждение", "web": "сайт",
}


def _render_news():
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import render_news
    return render_news


def templates_text():
    rn = _render_news()
    lines = []
    for key, (label, defaults, _) in rn.T.items():
        if key in SKIP_TEMPLATES:
            continue
        sample = json.dumps(defaults, ensure_ascii=False)
        lines.append(f"- {key} — {label} — {sample}")
    return "\n".join(lines)


def icons():
    """имя -> путь: d3_gift -> gift, u3_save -> save (повтор имени из второго набора — с «2»)."""
    found = {}
    for folder in ICON_DIRS:
        for path in sorted(folder.glob("*.png")):
            if path.stem.startswith(SKIP_ICONS):
                continue
            name = path.stem.split("_", 1)[1]
            found[name if name not in found else f"{name}2"] = path
    return found


def icons_text():
    return "\n".join(f"- {name} — {ICON_WORDS.get(name, name)}" for name in icons())


def parse_answer(text):
    """Ответ нейросети -> dict или None (снимаем ```json … ```)."""
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("sections"), list) or not data["sections"]:
        return None
    return data


def _shorten(value, ratio=0.75):
    if not isinstance(value, str) or len(value) < 12:
        return value
    cut = value[:max(8, int(len(value) * ratio))]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(",.;:—- ") + "…"


def render_banner(key, texts):
    """Баннер 1200×600 в шаблоне key. Не влезает — укорачиваем тексты; совсем никак — шаблон «news». -> PNG."""
    import io
    rn = _render_news()
    if key not in rn.T or key in SKIP_TEMPLATES:
        key = "alert"
    allowed = set(rn.T[key][1])
    texts = {k: str(v) for k, v in (texts or {}).items() if k in allowed and v not in (None, "")}
    for _ in range(5):
        try:
            image, _ = rn.render(key, **texts)
            break
        except ValueError as error:  # «… не влезает в …»
            _log.info("news banner %s: %s — shortening", key, error)
            texts = {k: _shorten(v) for k, v in texts.items()}
    else:
        image, _ = rn.render("news", title=_shorten(texts.get("title", ""), 0.5) or "Новости кланов")
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def _asset_url(data):
    import web_panel
    name, error = web_panel.save_asset(data)
    if error or not web_panel.panel_url():
        return None
    return f"{web_panel.panel_url()}/assets/{name}"


def build_post(data, banner_png):
    """Ответ нейросети + баннер -> payload поста (как в примере создателя бота)."""
    found = icons()
    author_icon_name = core.t("news.author_icon") if core.has_text("news.author_icon") else "trophy"
    author_icon = found.get(author_icon_name) or found.get("trophy")
    author_url = _asset_url(author_icon.read_bytes()) if author_icon else None
    embeds = [{"image": _asset_url(banner_png) or "", "color": DARK, "fields": []}]
    used = set()
    for section in data["sections"][:MAX_SECTIONS]:
        if not isinstance(section, dict):
            continue
        icon_name = str(section.get("icon") or "")
        if icon_name not in found or (icon_name in used and len(used) < len(found)):
            icon_name = next((n for n in found if n not in used), icon_name)
        used.add(icon_name)
        icon = found.get(icon_name)
        embeds.append({
            "author_name": str(section.get("heading") or "")[:256], "author_icon": author_url or "",
            "description": str(section.get("text") or "")[:4096], "thumbnail": _asset_url(icon.read_bytes()) if icon else "",
            "color": DARK, "fields": [],
        })
    return {"content": str(data.get("content") or "")[:2000], "embeds": embeds[:10], "buttons": []}


async def generate(brief, secretary=True):
    """Суть новости -> (payload, None) или (None, ключ ошибки)."""
    import clan_info
    if clan_info.provider() is None:
        return None, "news.no_ai"
    system = PROMPT.format(templates=templates_text(), icons=icons_text(), max_sections=MAX_SECTIONS - 2,
                           docs=clan_info.docs_text(brief, public=not secretary) or "—")
    answer = await clan_info.complete(system, f"Суть новости от создателя бота:\n{brief}", max_tokens=4000)
    data = parse_answer(answer)
    if data is None:
        _log.warning("news: model answer is not JSON: %s", (answer or "")[:300])
        return None, "news.bad_answer"
    banner = await asyncio.to_thread(render_banner, str(data.get("template") or "alert"), data.get("banner") or {})
    return await asyncio.to_thread(build_post, data, banner), None


async def run(interaction, brief):
    """/post новость: … — сгенерировать и открыть в конструкторе (или в /embed)."""
    import post_builder
    await interaction.response.defer(ephemeral=True, thinking=True)
    secretary = core.level_value(core.get_user_level(interaction)) >= core.level_value("admin")
    payload, error = await generate(brief, secretary=secretary)
    if error:
        await interaction.followup.send(t(error), ephemeral=True)
        return
    core.audit(interaction, "news.generated", "post", None, brief[:200])
    link = post_builder.open_link(interaction.guild.id, interaction.user.id, draft=payload)
    if link is None:  # сайта нет — правим в /embed
        from build_tools import open_imported
        await open_imported(interaction, payload, name=t("news.default_name"), source_key="news.in_embed")
        return
    import discord
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label=t("news.open")[:80], emoji="🌐", url=link))
    await interaction.followup.send(t("news.ready", sections=len(payload["embeds"]) - 1), view=view, ephemeral=True)
