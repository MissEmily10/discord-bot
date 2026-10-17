"""
Генерация иконок через Nano Banana (Gemini Image API от Google).

В отличие от FLUX, модель сама смотрит на НЕСКОЛЬКО референсов сразу и
понимает запрос по-русски, в том числе абстрактный («содружество»). Поэтому
ей отдаются: типичные референсы стиля (картинками), запрос как есть, описание
стиля и библия из разбора референсов (если разбор уже есть).

Ключ: GEMINI_API_KEY в .env (aistudio.google.com → Get API key; генерация
картинок обычно требует включённой оплаты в Google AI Studio).
Модель: LOGO_GEMINI_MODEL; если она недоступна — пробуются следующие из списка.
"""

import base64
import logging
import pathlib

_log = logging.getLogger(__name__)

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Nano Banana 2 (дешёвая и хорошая) -> Pro (дороже, точнее) -> первая -> на случай нового имени
MODELS = ("gemini-3.1-flash-image-preview", "gemini-3-pro-image-preview", "gemini-2.5-flash-image",
          "gemini-nano-banana-2.1")
MAX_REFERENCES = 8
OLD_MODEL_REFERENCES = 3  # первая Nano Banana (2.5) лучше всего работает с 1–3 картинками

PROMPT = """You are drawing ONE new icon for an existing icon/sticker set.
{refs_line}
NEW ICON SUBJECT (the request may be in Russian or another language): «{brief}»
If the subject is an abstract idea, a group or a role name (e.g. «содружество», «победа», «модератор»), draw the single
most recognizable concrete symbol of it — one object a person would instantly read, never a random shape.

Match the STYLE of the references exactly, as if the same artist made the next icon of the same set: the same
rendering (flat 2D or 3D / volume), material and surface texture, lighting direction and shading, outline (or its
absence), the character of the colors (tonality, saturation, temperature, contrast — not necessarily the same hues),
camera angle and perspective, the way the object faces and is tilted, proportions and cuteness, level of detail,
background treatment and margins. Do NOT copy the references' subjects.
{bible}{notes}
Format: a square image with ONE object only, perfectly centered, filling about 85% of the canvas with a small even
margin, readable even at 32x32 pixels. No text, no letters, no numbers, no watermark, no frame, no extra objects."""

STYLIZE = """The FIRST image is the user's draft icon. Redraw it as an icon of the set shown in the other images:
keep its subject, silhouette and pose recognizable, but change the whole look to the set's style.
Extra wishes (may be in Russian): «{brief}»
Match the set exactly: rendering (2D or 3D), material and texture, lighting and shading, outline, color character,
perspective, proportions, detail level, background and margins.
{bible}{notes}
Format: square image, one centered object filling about 85% of the canvas, no text, no frame."""


def api_key():
    import logo_module
    return logo_module.cfg("GEMINI_API_KEY") or logo_module.cfg("GOOGLE_API_KEY")


def enabled():
    return bool(api_key())


def models():
    import logo_module
    first = logo_module.cfg("LOGO_GEMINI_MODEL", MODELS[0])
    return [first] + [m for m in MODELS if m != first]


def _image_part(data, mime="image/png"):
    return {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}}


async def _post(url, body, key):
    """Один запрос к API. -> (статус, json). Отдельной функцией — чтобы подменять в тестах."""
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=240)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=body, headers={"x-goog-api-key": key}) as response:
            try:
                data = await response.json(content_type=None)
            except ValueError:
                data = {}
            return response.status, data


def _extract_image(data):
    """-> (bytes картинки | None, текст ответа модели)."""
    texts = []
    for candidate in data.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"]), ""
            if part.get("text"):
                texts.append(part["text"])
    reason = (data.get("promptFeedback") or {}).get("blockReason")
    return None, reason or " ".join(texts)[:300]


def references_for(style_row, model):
    """Самые типичные референсы стиля (по разбору, если он есть)."""
    import logo_module
    import logo_style_ai
    if not style_row:
        return []
    limit = OLD_MODEL_REFERENCES if "2.5" in model else MAX_REFERENCES
    try:
        refs = logo_style_ai.representative(style_row, limit)
    except Exception:  # разбора нет или он битый — просто первые
        refs = logo_module.references_of(style_row)[:limit]
    return refs[:limit]


def build_prompt(mode, style_row, brief, refs, bible=None):
    notes = ((style_row[4] if style_row else "") or "").strip()
    if bible and notes == (bible.get("invariants") or "").strip():
        notes = ""
    values = dict(
        brief=(brief or "").strip() or "a simple cute icon in the spirit of the set",
        refs_line=(f"The {len(refs)} attached images are reference icons from this set (one artist, one style)."
                   if refs else "There are no reference images; follow the style description below."),
        bible=f"\nStyle of the set (from a careful analysis of every reference): {bible['invariants']}\n"
              if bible and bible.get("invariants") else "",
        notes=f"\nOwner's style notes: {notes}\n" if notes else "",
    )
    if not refs and not notes and not bible:
        import core
        values["notes"] = f"\nStyle: {core.t('logo.prompt.default_style')}\n"
    return (STYLIZE if mode == "stylize" else PROMPT).format(**values)


async def generate(mode, style_row, brief, source_bytes=None):
    """-> PNG bytes (сырые, без подгонки под иконку). Ошибки — исключением с понятным текстом."""
    import logo_style_ai
    key = api_key()
    bible = None
    if style_row:
        bible = logo_style_ai.load(style_row[0]).get("bible")  # только готовый разбор, без запросов к HF
    last_error = None
    for model in models():
        refs = references_for(style_row, model)
        parts = [{"text": build_prompt(mode, style_row, brief, refs, bible)}]
        if mode == "stylize" and source_bytes:
            parts.append(_image_part(source_bytes))
        parts += [_image_part(pathlib.Path(path).read_bytes()) for path in refs]
        body = {"contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "1:1"}}}
        status, data = await _post(API.format(model=model), body, key)
        if status in (400, 404) and "model" in str(data).lower() and "not" in str(data).lower():
            last_error = f"{model}: {((data.get('error') or {}).get('message') or status)}"
            _log.info("gemini model unavailable, trying next: %s", last_error)
            continue  # такой модели нет (или нет доступа) — следующая
        if status != 200:
            message = (data.get("error") or {}).get("message") or f"HTTP {status}"
            raise RuntimeError(f"Nano Banana: {message}"[:300])
        image, reason = _extract_image(data)
        if image is None:
            raise RuntimeError(f"Nano Banana не вернула картинку: {reason or 'без причины'}"[:300])
        return image
    raise RuntimeError(f"Nano Banana недоступна: {last_error}"[:300])
