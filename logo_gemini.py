"""
Генерация иконок через Nano Banana (Gemini Image API от Google).

В отличие от FLUX, модель сама смотрит на НЕСКОЛЬКО референсов сразу и
понимает запрос по-русски, в том числе абстрактный («содружество»). Поэтому
ей отдаются: типичные референсы стиля (картинками), запрос как есть, описание
стиля и библия из разбора референсов (если разбор уже есть).

Два способа подключить (любой один):
- OPENROUTER_API_KEY — через OpenRouter (openrouter.ai): предоплата небольшими
  суммами, как на Hugging Face, без привязки к Google Cloud;
- GEMINI_API_KEY — напрямую у Google (aistudio.google.com → Get API key;
  для картинок нужна включённая оплата).
Если заданы оба, используется OpenRouter.
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
OPENROUTER_API = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS = ("google/gemini-3.1-flash-image", "google/gemini-3.1-flash-image-preview",
                     "google/gemini-3-pro-image", "google/gemini-3-pro-image-preview",
                     "google/gemini-2.5-flash-image")
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


def _cfg(name, default=None):
    import logo_module
    return logo_module.cfg(name, default)


def provider():
    """openrouter | google | None."""
    if _cfg("OPENROUTER_API_KEY"):
        return "openrouter"
    if _cfg("GEMINI_API_KEY") or _cfg("GOOGLE_API_KEY"):
        return "google"
    return None


def api_key():
    if provider() == "openrouter":
        return _cfg("OPENROUTER_API_KEY")
    return _cfg("GEMINI_API_KEY") or _cfg("GOOGLE_API_KEY")


def enabled():
    return provider() is not None


def models():
    names = OPENROUTER_MODELS if provider() == "openrouter" else MODELS
    first = _cfg("LOGO_GEMINI_MODEL", names[0])
    if provider() == "openrouter" and "/" not in first:
        first = "google/" + first  # «gemini-3-pro-image-preview» -> «google/…»
    return [first] + [m for m in names if m != first]


def _image_part(data, mime="image/png"):
    return {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}}


async def _post(url, body, headers):
    """Один запрос к API. -> (статус, json). Отдельной функцией — чтобы подменять в тестах."""
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=240)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=body, headers=headers) as response:
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


def _extract_openrouter(data):
    """Ответ chat completions OpenRouter: картинки — в message.images (data:-ссылки)."""
    texts = []
    for choice in data.get("choices") or []:
        message = choice.get("message") or {}
        for image in message.get("images") or []:
            url = ((image.get("image_url") or {}).get("url")) or ""
            if url.startswith("data:") and "," in url:
                return base64.b64decode(url.split(",", 1)[1]), ""
        if isinstance(message.get("content"), str):
            texts.append(message["content"])
    return None, " ".join(texts)[:300]


def _unavailable(status, data):
    """Ошибка «такой модели нет / нет доступа» — тогда пробуем следующую."""
    text = str(data).lower()
    return status in (400, 404) and (("model" in text and "not" in text) or "no endpoints" in text)


def _request(model, prompt, images):
    """-> (url, body, headers) для текущего провайдера."""
    key = api_key()
    if provider() == "openrouter":
        content = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(data).decode()}}
            for data in images]
        body = {"model": model, "messages": [{"role": "user", "content": content}],
                "modalities": ["image", "text"], "image_config": {"aspect_ratio": "1:1"}}
        return OPENROUTER_API, body, {"Authorization": f"Bearer {key}", "X-Title": "Discord bot icons"}
    parts = [{"text": prompt}] + [_image_part(data) for data in images]
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "1:1"}}}
    return API.format(model=model), body, {"x-goog-api-key": key}


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
    bible = None
    if style_row:
        bible = logo_style_ai.load(style_row[0]).get("bible")  # только готовый разбор, без запросов к HF
    last_error = None
    for model in models():
        refs = references_for(style_row, model)
        images = [source_bytes] if mode == "stylize" and source_bytes else []
        images += [pathlib.Path(path).read_bytes() for path in refs]
        url, body, headers = _request(model, build_prompt(mode, style_row, brief, refs, bible), images)
        status, data = await _post(url, body, headers)
        if _unavailable(status, data):
            last_error = f"{model}: {((data.get('error') or {}).get('message') or status)}"
            _log.info("image model unavailable, trying next: %s", last_error)
            continue  # такой модели нет (или нет доступа) — следующая
        if status == 402:
            raise RuntimeError("Nano Banana: на балансе закончились деньги — пополни счёт")
        if status != 200:
            message = (data.get("error") or {}).get("message") or f"HTTP {status}"
            raise RuntimeError(f"Nano Banana: {message}"[:300])
        extract = _extract_openrouter if provider() == "openrouter" else _extract_image
        image, reason = extract(data)
        if image is None:
            raise RuntimeError(f"Nano Banana не вернула картинку: {reason or 'без причины'}"[:300])
        return image
    raise RuntimeError(f"Nano Banana недоступна: {last_error}"[:300])
