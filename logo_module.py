"""
logo_module.py
==============
/logo — генератор иконок для ролей Discord в заранее заданном стиле.

Стиль (logo_styles) = текстовое описание + референс-иконки + (опционально)
LoRA, обученная на этих референсах. Режимы:
- "С нуля": короткое ТЗ -> если у стиля есть референсы — новая иконка рисуется
  поверх случайного референса (модель редактирования копирует его манеру),
  иначе text-to-image (FLUX; с LoRA стиля, если задана);
- "Из макета": присланная иконка/набросок -> image-to-image (модель
  редактирования перерисовывает макет в стиле);
- "Описать стиль": vision-модель смотрит на ВСЕ референсы (пачками) и пишет
  подробное описание стиля; если описания нет — оно составляется само при
  первой генерации;
- "Датасет": архив референсов + подписи для обучения LoRA (самый точный стиль).
Результат: PNG 500x500 (рисунок на весь кадр) отдельным файлом, кнопки "Поставить на роль"
(с подсказкой подходящих ролей по названию), "Ещё вариант", "Убрать фон".

Генерация идёт через Hugging Face Inference Providers (huggingface_hub).
Настройки — .env (см. README): HF_TOKEN, LOGO_PROVIDER, LOGO_TEXT_MODEL,
LOGO_EDIT_MODEL, LOGO_VISION_MODEL, LOGO_BG_MODEL, LOGO_DIR, LOGO_HOURLY_LIMIT.
Промпты — в каталоге текстов (logo.prompt.*), их можно править в веб-панели.
Без HF_TOKEN или без пакетов huggingface_hub/Pillow бот работает, а /logo
объясняет, чего не хватает.
"""

import asyncio
import base64
import difflib
import io
import json
import logging
import os
import pathlib
import time
import zipfile

import discord
from discord import app_commands

import core
from core import PanelView, Modal, t, panel_embed, get_user_level
from actions import say
from database import (
    save_logo_style, update_logo_style, set_default_logo_style, get_logo_style,
    get_logo_styles, delete_logo_style, save_logo_generation, get_logo_generation,
    count_recent_logo_generations,
)

_log = logging.getLogger(__name__)

ICON_SIZE = int(os.getenv("LOGO_ICON_SIZE") or 500)  # итоговая иконка: PNG 500×500 (для иконки роли ужимается до 256 КБ)
SUBJECT_MARGIN = 0.02       # поля вокруг рисунка после обрезки пустого фона (доля стороны) — рисунок на всё полотно
STYLE_TEXT_MAX = 1800       # описание стиля: длиннее модели генерации всё равно не читают
ROLE_ICON_MAX_BYTES = 256 * 1024
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_REFERENCES = 60
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")

core.COMMAND_MIN_LEVELS.setdefault("logo", "staff")


# =========================
# CONFIG
# =========================

def cfg(name, default=None):
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def logo_dir():
    path = pathlib.Path(cfg("LOGO_DIR", "logo_files")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def hourly_limit():
    try:
        return int(cfg("LOGO_HOURLY_LIMIT", "10"))
    except ValueError:
        return 10


def backend_problem():
    """Ключ каталога с причиной, почему генерация недоступна, или None."""
    if cfg("OPENROUTER_API_KEY") or cfg("GEMINI_API_KEY") or cfg("GOOGLE_API_KEY"):  # Nano Banana — нужен только Pillow
        try:
            import PIL  # noqa: F401
        except ImportError:
            return "logo.no_packages"
        return None
    try:
        import huggingface_hub  # noqa: F401
        import PIL  # noqa: F401
    except ImportError:
        return "logo.no_packages"
    if not cfg("HF_TOKEN"):
        return "logo.no_token"
    return None


def _client():
    from huggingface_hub import AsyncInferenceClient

    return AsyncInferenceClient(provider=cfg("LOGO_PROVIDER", "fal-ai"), api_key=cfg("HF_TOKEN"), timeout=180)


# =========================
# ИЗОБРАЖЕНИЯ
# =========================

def to_icon_png(image, size=ICON_SIZE):
    """PIL.Image -> квадратный PNG (центр-кроп), с сохранением прозрачности."""
    from PIL import Image

    image = image.convert("RGBA")
    side = min(image.size)
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


BG_TOLERANCE = 42      # насколько пиксель может отличаться от цвета фона (сглаживание, шум, шахматка)
BG_BORDER_SHARE = 0.85  # фон есть, если 2–3 цвета занимают столько рамки (иначе это картинка «в край»)
BG_WORK_SIZE = 512      # маску фона считаем на уменьшенной копии — заливка в Pillow небыстрая


def has_transparency(image):
    image = image.convert("RGBA")
    return image.getchannel("A").getextrema()[0] < 250


def strip_background(image):
    """
    Убрать сплошной фон за рисунком: белый, цветной или нарисованную «шахматку»
    прозрачности (нейросети часто рисуют её вместо настоящей прозрачности).
    Цвета фона — самые частые цвета рамки картинки; прозрачным становится всё
    такого цвета, что связано с краем (внутри рисунка белое не трогаем — его
    отделяет контур). -> RGBA или None, если фона нет (картинка «в край»).
    """
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    image = image.convert("RGBA")
    if has_transparency(image):
        return image
    small = image.convert("RGB")
    scale = BG_WORK_SIZE / max(small.size)
    if scale < 1:
        small = small.resize((max(1, int(small.width * scale)), max(1, int(small.height * scale))), Image.BILINEAR)
    w, h = small.size
    # рамка в 2 px -> самые частые цвета
    strip = Image.new("RGB", (2 * (w + h), 2))
    strip.paste(small.crop((0, 0, w, 2)).resize((w, 2)), (0, 0))
    strip.paste(small.crop((0, h - 2, w, h)).resize((w, 2)), (w, 0))
    strip.paste(small.crop((0, 0, 2, h)).rotate(90, expand=True).resize((h, 2)), (2 * w, 0))
    strip.paste(small.crop((w - 2, 0, w, h)).rotate(90, expand=True).resize((h, 2)), (2 * w + h, 0))
    quantized = strip.quantize(colors=6, method=Image.MEDIANCUT)
    palette = quantized.getpalette()
    counts = sorted(quantized.getcolors(), reverse=True)
    total = sum(count for count, _ in counts)
    colors, covered = [], 0
    for count, index in counts[:3]:
        if count < total * 0.08:
            break
        colors.append(tuple(palette[index * 3:index * 3 + 3]))
        covered += count
    if not colors or covered < total * BG_BORDER_SHARE:
        return None
    # пиксели «как фон»: близко к одному из цветов рамки по каждому каналу
    candidates = Image.new("L", (w, h), 0)
    for color in colors:
        diff = ImageChops.difference(small, Image.new("RGB", (w, h), color))
        r, g, b = diff.split()
        worst = ImageChops.lighter(ImageChops.lighter(r, g), b)
        candidates = ImageChops.lighter(candidates, worst.point(lambda d: 255 if d <= BG_TOLERANCE else 0))
    # фон = только то, что связано с краем
    for x, y in [(x, y) for x in range(0, w, 2) for y in (0, h - 1)] + [(x, y) for y in range(0, h, 2) for x in (0, w - 1)]:
        if candidates.getpixel((x, y)) == 255:
            ImageDraw.floodfill(candidates, (x, y), 128)
    background = candidates.point(lambda v: 255 if v == 128 else 0)
    share = sum(background.histogram()[255:]) / (w * h)
    if share < 0.02 or share > 0.97:  # фона почти нет — или «съели» всё: не трогаем
        return None
    # альфа: без фона, края на 1 px внутрь (убирает светлую кайму) и мягкие
    alpha = ImageChops.invert(background).filter(ImageFilter.MinFilter(3)).resize(image.size, Image.LANCZOS)
    alpha = alpha.filter(ImageFilter.GaussianBlur(0.6))
    result = image.copy()
    result.putalpha(alpha)
    return result


def fit_subject(image, size=ICON_SIZE, margin=SUBJECT_MARGIN):
    """
    Рисунок на всё полотно: нейросети любят рисовать маленький значок посреди
    большого фона, а на иконке роли (32 px) он превращается в точку. Убираем
    сплошной фон (strip_background), обрезаем пустые поля и растягиваем
    рисунок до size (пропорции сохраняются, по краям — тонкий запас).
    """
    from PIL import Image, ImageChops

    image = image.convert("RGBA")
    if not has_transparency(image):
        image = strip_background(image) or image
    alpha = image.getchannel("A")
    if alpha.getextrema()[0] < 250:  # есть прозрачность — рисунок там, где не прозрачно
        box = alpha.point(lambda a: 255 if a > 24 else 0).getbbox()
    else:
        w, h = image.size
        corners = [image.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
        background = tuple(sum(c[i] for c in corners) // 4 for i in range(3))
        diff = ImageChops.difference(image.convert("RGB"), Image.new("RGB", image.size, background)).convert("L")
        box = diff.point(lambda d: 255 if d > 28 else 0).getbbox()
    if box:
        left, top, right, bottom = box
        side = max(1, int(max(right - left, bottom - top) * (1 + 2 * margin)))
        cx, cy = (left + right) / 2, (top + bottom) / 2
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0) if alpha.getextrema()[0] < 250
                           else image.getpixel((0, 0)))
        square.paste(image, (int(side / 2 - cx), int(side / 2 - cy)))
        image = square
    return to_icon_png(image, size)


def clean_icon(png_bytes):
    """
    Перед иконкой роли: если сзади остался сплошной фон (белый, шахматка) —
    убрать и растянуть рисунок на всё полотно. Прозрачная картинка — как есть.
    """
    image = open_image(png_bytes).convert("RGBA")
    if has_transparency(image) or strip_background(image) is None:
        return png_bytes
    return fit_subject(image)


def role_icon_bytes(png_bytes):
    """Ужать до лимита иконки роли (256 КБ): уменьшаем размер, затем палитра."""
    from PIL import Image

    if len(png_bytes) <= ROLE_ICON_MAX_BYTES:
        return png_bytes
    image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    for size in (256, 192, 128):
        for quantize in (False, True):
            candidate = image.resize((size, size), Image.LANCZOS)
            if quantize:
                candidate = candidate.quantize(colors=128, method=Image.FASTOCTREE)
            buffer = io.BytesIO()
            candidate.save(buffer, format="PNG", optimize=True)
            if buffer.tell() <= ROLE_ICON_MAX_BYTES:
                return buffer.getvalue()
    return None


def open_image(data):
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    image.load()
    return image


async def read_attachment(attachment):
    if attachment is None:
        return None, "logo.no_image"
    if (attachment.content_type or "").split(";")[0] not in IMAGE_TYPES:
        return None, "logo.bad_image"
    if attachment.size > MAX_UPLOAD_BYTES:
        return None, "logo.image_too_large"
    data = await attachment.read()
    try:
        open_image(data)
    except Exception:  # noqa: BLE001 — любой битый файл
        return None, "logo.bad_image"
    return data, None


def store_generation(guild_id, user_id, style_id, mode, prompt, png_bytes, source_bytes=None):
    folder = logo_dir() / "generated" / str(guild_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{int(time.time() * 1000)}_{user_id}.png"
    path.write_bytes(png_bytes)
    if source_bytes:
        # исходный макет — чтобы "Ещё вариант" перерисовывал его, а не предыдущий результат
        source_path(path).write_bytes(source_bytes)
    return save_logo_generation(guild_id, user_id, style_id, mode, prompt, str(path))


def source_path(result_path):
    result_path = pathlib.Path(result_path)
    return result_path.with_name(result_path.stem + ".src.png")


def references_of(style_row):
    return [p for p in json.loads(style_row[9] or "[]") if pathlib.Path(p).exists()]


def add_reference(style_row, data):
    refs = json.loads(style_row[9] or "[]")
    if len(refs) >= MAX_REFERENCES:
        return False
    folder = logo_dir() / "references" / str(style_row[0])
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{int(time.time() * 1000)}_{len(refs)}.png"
    path.write_bytes(to_icon_png(open_image(data)))
    refs.append(str(path))
    update_logo_style(style_row[0], references_json=json.dumps(refs))
    return True


# =========================
# ГЕНЕРАЦИЯ
# =========================

def build_prompt(mode, style_row, brief):
    """mode: generate (с нуля), reference (новая иконка по референсу), stylize (перерисовать макет)."""
    style_text = (style_row[4] if style_row else "") or t("logo.prompt.default_style")
    key = {"generate": "logo.prompt.generate", "reference": "logo.prompt.from_reference"}.get(mode, "logo.prompt.stylize")
    return t(key, brief=brief or "", style=style_text, base=t("logo.prompt.base"))


def negative_prompt(style_row):
    parts = [t("logo.prompt.negative"), (style_row[5] if style_row else "") or ""]
    return ", ".join(p for p in parts if p)


async def generate_image(mode, style_row, brief, source_bytes=None):
    """-> PNG bytes. Исключения пробрасываются (их показывает вызывающий код)."""
    import logo_gemini
    import logo_style_ai

    lora = style_row and (style_row[7] or style_row[6])
    if logo_gemini.enabled() and not lora:
        # Nano Banana сама смотрит на референсы и понимает русский — главный путь, если есть ключ
        return fit_subject(open_image(await logo_gemini.generate(mode, style_row, brief, source_bytes)))
    client = _client()
    refs = references_of(style_row) if style_row else []
    if brief and logo_style_ai.enabled():
        # абстрактное «содружество» -> конкретный предмет (рукопожатие), по-английски
        brief = await logo_style_ai.english_subject(brief)
    bases = []
    if mode == "generate" and not lora and logo_style_ai.enabled():
        # готовая плоская иконка под запрос (рукопожатие для «содружества») — основа формы,
        # поверх которой рисуется стиль; стиль при этом главнее
        import icon_base
        bases = await icon_base.find(brief, 2)
    if refs and not lora and logo_style_ai.enabled():
        # Главный путь: каждый референс разобран в подробный промт, разборы сведены
        # в «библию» стиля, по ней рисуют несколько кандидатов, судья выбирает.
        try:
            bible = await asyncio.wait_for(logo_style_ai.style_bible(style_row), 900)
        except Exception as error:  # noqa: BLE001 — без библии рисуем по-старому
            _log.warning("style bible failed: %s", error)
            bible = None
        if bible:
            return await generate_by_bible(client, mode, style_row, brief, bible, source_bytes, bases)
    if bases:
        style_text = (style_row[4] if style_row else "") or t("logo.prompt.default_style")
        prompt = t("logo.prompt.base_restyle", subject=brief, bible=style_text, notes="-", base=t("logo.prompt.base"))
        image = await client.image_to_image(bases[0][1], prompt=prompt, negative_prompt=negative_prompt(style_row),
                                            model=cfg("LOGO_EDIT_MODEL", "black-forest-labs/FLUX.1-Kontext-dev"))
        return fit_subject(image)
    if mode == "generate" and refs and not lora:
        # Строго по референсам: модель редактирования получает настоящую иконку
        # стиля и рисует НОВЫЙ предмет её же рукой (линия, цвета, тени, фон).
        # Текстовая модель референсы не видит — только их описание.
        import random
        mode, source_bytes = "reference", pathlib.Path(random.choice(refs)).read_bytes()
    prompt = build_prompt(mode, style_row, brief)
    if mode == "generate":
        # LoRA стиля — это и есть модель для text-to-image (репозиторий на HF)
        model = lora or cfg("LOGO_TEXT_MODEL", "black-forest-labs/FLUX.1-dev")
        image = await client.text_to_image(
            prompt, negative_prompt=negative_prompt(style_row), width=1024, height=1024, model=model,
        )
    else:
        model = cfg("LOGO_EDIT_MODEL", "black-forest-labs/FLUX.1-Kontext-dev")
        image = await client.image_to_image(
            source_bytes, prompt=prompt, negative_prompt=negative_prompt(style_row), model=model,
        )
    return fit_subject(image)


async def generate_by_bible(client, mode, style_row, brief, bible, source_bytes=None, bases=()):
    """
    Кандидаты по библии стиля, лучший — по судье. Есть основа из набора иконок —
    она перерисовывается в стиле (+ один кандидат по типичному референсу);
    нет — перерисовка двух типичных референсов + текстовая модель.
    """
    import logo_style_ai

    subject = brief or "a simple cute object"
    notes = (style_row[4] or "").strip()
    if notes == bible.get("invariants", "").strip():
        notes = ""  # описание стиля и есть библия — не повторяем
    negative = ", ".join(p for p in (bible.get("negative", ""), style_row[5] or "") if p)
    values = dict(subject=subject, bible=bible.get("invariants", ""), notes=notes or "-", base=t("logo.prompt.base"))
    edit_model = cfg("LOGO_EDIT_MODEL", "black-forest-labs/FLUX.1-Kontext-dev")
    jobs = []
    if mode == "stylize":
        prompt = t("logo.prompt.bible_stylize", **values)
        jobs += [client.image_to_image(source_bytes, prompt=prompt, negative_prompt=negative, model=edit_model)
                 for _ in range(2)]
    elif bases:
        prompt = t("logo.prompt.base_restyle", **values)
        jobs += [client.image_to_image(data, prompt=prompt, negative_prompt=negative, model=edit_model)
                 for _, data in bases]
        for path in logo_style_ai.representative(style_row, 1):
            jobs.append(client.image_to_image(pathlib.Path(path).read_bytes(),
                                              prompt=t("logo.prompt.bible_reference", **values),
                                              negative_prompt=negative, model=edit_model))
    else:
        prompt = t("logo.prompt.bible_reference", **values)
        for path in logo_style_ai.representative(style_row, 2):
            jobs.append(client.image_to_image(pathlib.Path(path).read_bytes(), prompt=prompt,
                                              negative_prompt=negative, model=edit_model))
        text_prompt = bible.get("prompt_template", "").replace("{subject}", subject)
        text_prompt = t("logo.prompt.bible_generate", prompt=text_prompt, notes=values["notes"], base=values["base"])
        jobs.append(client.text_to_image(text_prompt, negative_prompt=negative, width=1024, height=1024,
                                         model=cfg("LOGO_TEXT_MODEL", "black-forest-labs/FLUX.1-dev")))
    results = await asyncio.gather(*jobs, return_exceptions=True)
    candidates = [fit_subject(image) for image in results if not isinstance(image, BaseException)]
    if not candidates:
        raise next(r for r in results if isinstance(r, BaseException))
    return candidates[await logo_style_ai.judge(style_row, candidates, subject)]


async def remove_background(png_bytes):
    """Сегментация фона моделью RMBG и применение маски как альфа-канала."""
    client = _client()
    segments = await client.image_segmentation(png_bytes, model=cfg("LOGO_BG_MODEL", "briaai/RMBG-2.0"))
    if not segments:
        raise RuntimeError("empty segmentation")
    image = open_image(png_bytes).convert("RGBA")
    mask = segments[0].mask
    if isinstance(mask, str):  # некоторые провайдеры отдают base64
        mask = open_image(base64.b64decode(mask))
    image.putalpha(mask.convert("L").resize(image.size))
    return fit_subject(image)


async def clear_background(png_bytes):
    """
    Кнопка «Без фона»: сначала свой способ (цвет фона по рамке — чисто убирает
    белый фон и шахматку, без нейросети и бесплатно), не вышло — модель RMBG.
    """
    image = open_image(png_bytes).convert("RGBA")
    if has_transparency(image):
        return fit_subject(image)
    stripped = await asyncio.to_thread(strip_background, image)
    if stripped is not None:
        return await asyncio.to_thread(fit_subject, stripped)
    return await remove_background(png_bytes)


async def describe_style(style_row):
    """
    Очень подробное описание стиля по ВСЕМ референсам: vision-модель смотрит на
    них пачками по 6, а потом сводит заметки в одно описание для генератора.
    """
    refs = references_of(style_row)
    if not refs:
        return None
    from huggingface_hub import AsyncInferenceClient

    client = AsyncInferenceClient(api_key=cfg("HF_TOKEN"), timeout=180)
    model = cfg("LOGO_VISION_MODEL", "Qwen/Qwen2.5-VL-7B-Instruct")
    notes = []
    for start in range(0, len(refs), 6):
        content = [{"type": "text", "text": t("logo.prompt.describe")}]
        for path in refs[start:start + 6]:
            encoded = base64.b64encode(pathlib.Path(path).read_bytes()).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}})
        response = await client.chat_completion(messages=[{"role": "user", "content": content}],
                                                model=model, max_tokens=700)
        notes.append(response.choices[0].message.content.strip())
    if len(notes) == 1:
        return notes[0][:STYLE_TEXT_MAX]
    merged = await client.chat_completion(
        messages=[{"role": "user", "content": t("logo.prompt.merge", notes="\n\n---\n\n".join(notes))}],
        model=model, max_tokens=900)
    return merged.choices[0].message.content.strip()[:STYLE_TEXT_MAX]


async def ensure_style_text(style_row):
    """Есть референсы, но нет описания стиля — составить его один раз и сохранить.
    С токеном — из подробного разбора каждого референса, иначе — старым описанием пачками."""
    if style_row is None or (style_row[4] or "").strip() or not references_of(style_row) or not cfg("HF_TOKEN"):
        return style_row
    import logo_style_ai
    try:
        bible = await logo_style_ai.style_bible(style_row) if logo_style_ai.enabled() else None
        text = bible.get("invariants") if bible else await describe_style(style_row)
    except Exception as error:  # описание — помощь, а не условие генерации
        _log.warning("style description failed: %s", error)
        return style_row
    if text:
        update_logo_style(style_row[0], prompt=text[:STYLE_TEXT_MAX])
        return get_logo_style(style_row[0])
    return style_row


def build_dataset_zip(style_row):
    """Архив для обучения LoRA: картинки + подписи с триггер-словом стиля."""
    trigger = f"rbstyle{style_row[0]}"
    caption = f"{trigger}, {style_row[4] or 'icon'}"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, path in enumerate(references_of(style_row), start=1):
            archive.write(path, f"{index:03d}.png")
            archive.writestr(f"{index:03d}.txt", caption)
        archive.writestr("README.txt", t("logo.dataset_readme", trigger=trigger))
    buffer.seek(0)
    return buffer, trigger


def import_zip(style_row, data):
    """Добавить референсы из zip. Защита от zip-бомб: лимиты на число и размер."""
    added = skipped = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = [m for m in archive.infolist() if not m.is_dir()][:MAX_REFERENCES * 2]
        for member in members:
            if not member.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) or member.file_size > MAX_UPLOAD_BYTES:
                skipped += 1
                continue
            try:
                if add_reference(get_logo_style(style_row[0]), archive.read(member)):
                    added += 1
                else:
                    skipped += 1
            except Exception:  # noqa: BLE001 — битая картинка в архиве
                skipped += 1
    return added, skipped


# =========================
# ПРАВА
# =========================

def can_manage_styles(interaction):
    return core.is_owner(interaction) or core.level_value(get_user_level(interaction)) >= core.level_value("admin")


def rate_limited(interaction):
    if core.is_owner(interaction):
        return False
    return count_recent_logo_generations(interaction.user.id, int(time.time()) - 3600) >= hourly_limit()


def role_icon_problem(guild, role, member):
    if role is None:
        return "roles.not_found"
    if "ROLE_ICONS" not in guild.features:
        return "logo.no_role_icons"
    if not guild.me.guild_permissions.manage_roles:
        return "actions.role.no_manage_roles"
    if role.is_default() or role.managed:
        return "roles.managed"
    if role >= guild.me.top_role:
        return "roles.above_bot"
    if core.is_owner_id(member.id) or member.id == guild.owner_id:
        return None
    if not member.guild_permissions.manage_roles:
        return "logo.need_manage_roles"
    if role >= member.top_role:
        return "roles.above_actor"
    return None


def suggest_roles(guild, brief, member, limit=3):
    """Роли, чьё название похоже на ТЗ, и которые этот участник может менять."""
    brief = (brief or "").lower()
    if not brief:
        return []
    scored = []
    for role in guild.roles:
        if role_icon_problem(guild, role, member):
            continue
        name = role.name.lower()
        score = difflib.SequenceMatcher(None, brief, name).ratio()
        if name in brief or any(word and word in name for word in brief.split() if len(word) > 3):
            score += 0.5
        scored.append((score, role))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [role for score, role in scored[:limit] if score >= 0.45]


# =========================
# РЕЗУЛЬТАТ: постоянные кнопки
# =========================

def generation_file(generation_id):
    row = get_logo_generation(generation_id)
    if not row or not pathlib.Path(row[6]).exists():
        return None, None
    return row, pathlib.Path(row[6]).read_bytes()


class LogoActionButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rb:lg:(?P<gid>\d+):(?P<act>apply|again|nobg|save)"):
    LABELS = {"apply": ("logo.result.apply", "🎭", discord.ButtonStyle.success),
              "again": ("logo.result.again", "🔁", discord.ButtonStyle.secondary),
              "nobg": ("logo.result.nobg", "✂️", discord.ButtonStyle.secondary),
              "save": ("logo.result.save", "📚", discord.ButtonStyle.secondary)}

    def __init__(self, generation_id, act, *, item=None):
        if item is None:
            key, emoji, style = self.LABELS[act]
            item = discord.ui.Button(label=t(key)[:80], emoji=emoji, style=style, custom_id=f"rb:lg:{generation_id}:{act}")
        super().__init__(item)
        self.generation_id = generation_id
        self.act = act

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["gid"]), match["act"], item=item)

    async def callback(self, interaction):
        row, data = generation_file(self.generation_id)
        if row is None or interaction.guild is None or row[1] != interaction.guild.id:
            await say(interaction, "logo.result.gone")
            return
        if row[2] != interaction.user.id and not can_manage_styles(interaction):
            await say(interaction, "logo.result.not_yours")
            return
        if self.act == "save":  # в библиотеку картинок — потом брать из бота, а не с телефона
            import image_library
            await interaction.response.defer(ephemeral=True, thinking=True)
            item_id, problem = await image_library.save_image(interaction.guild.id, interaction.user.id,
                                                              (row[5] or t("library_images.kind.logo"))[:80], "logo", data)
            await interaction.followup.send(t(problem) if problem else t("library_images.saved", id=item_id),
                                            ephemeral=True)
            return
        if self.act == "apply":
            await interaction.response.send_message(
                t("logo.apply.prompt"), view=ApplyRoleView(self.generation_id, interaction, row[5]), ephemeral=True,
            )
            return
        problem = backend_problem()
        if problem:
            await say(interaction, problem)
            return
        if rate_limited(interaction):
            await say(interaction, "logo.rate_limited", limit=hourly_limit())
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        style_row = get_logo_style(row[3]) if row[3] else None
        try:
            if self.act == "nobg":
                png = await clear_background(data)
                mode = "nobg"
            else:
                mode = row[4] if row[4] in ("generate", "stylize") else "generate"
                source = None
                if mode == "stylize":
                    src = source_path(row[6])
                    source = src.read_bytes() if src.exists() else data
                png = await generate_image(mode, style_row, row[5], source_bytes=source)
        except Exception as error:  # noqa: BLE001 — ошибки провайдера показываем текстом
            _log.warning("logo generation failed: %s", error)
            await interaction.followup.send(t("logo.failed", error=str(error)[:300]), ephemeral=True)
            return
        await send_result(interaction, style_row, mode, row[5], png, source_bytes=source if self.act == "again" else None)


class ApplyRoleView(PanelView):
    def __init__(self, generation_id, interaction, brief):
        super().__init__(timeout=300)
        self.generation_id = generation_id
        for role in suggest_roles(interaction.guild, brief, interaction.user):
            button = discord.ui.Button(label=role.name[:80], emoji="✨", style=discord.ButtonStyle.primary, row=0)

            async def cb(i, role=role):
                await apply_icon(i, self.generation_id, role)

            button.callback = cb
            self.add_item(button)
        select = discord.ui.RoleSelect(placeholder=t("logo.apply.placeholder")[:150], row=1)

        async def picked(i):
            await apply_icon(i, self.generation_id, select.values[0])

        select.callback = picked
        self.add_item(select)


async def apply_icon(interaction, generation_id, role):
    row, data = generation_file(generation_id)
    if row is None:
        await say(interaction, "logo.result.gone")
        return
    role = interaction.guild.get_role(role.id)
    problem = role_icon_problem(interaction.guild, role, interaction.user)
    if problem:
        await say(interaction, problem)
        return
    # на иконке роли не должно быть белого фона/шахматки сзади — проверяем и убираем
    icon = role_icon_bytes(await asyncio.to_thread(clean_icon, data))
    if icon is None:
        await say(interaction, "logo.apply.too_large")
        return
    try:
        await role.edit(display_icon=icon, reason=f"/logo by {interaction.user.id}")
    except discord.HTTPException as error:
        await say(interaction, "logo.apply.failed", error=str(error)[:200])
        return
    core.audit(interaction, "logo.applied", "role", role.id, f"generation={generation_id}")
    await say(interaction, "logo.apply.done", role=role.mention)


async def send_result(interaction, style_row, mode, brief, png, source_bytes=None):
    generation_id = store_generation(interaction.guild.id, interaction.user.id, style_row[0] if style_row else None,
                                     mode, brief, png, source_bytes)
    core.audit(interaction, "logo.generated", "logo", generation_id, mode)
    view = discord.ui.View(timeout=None)
    for act in ("apply", "again", "nobg", "save"):
        view.add_item(LogoActionButton(generation_id, act))
    embed = panel_embed(
        interaction, "logo.result",
        style=style_row[3] if style_row else t("logo.no_style"), mode=t(f"logo.mode.{mode}"), brief=brief or "—",
    )
    import logo_style_ai
    subject = logo_style_ai.understood(brief)
    if subject:  # видно, что именно бот рисовал — если понял не так, можно уточнить запрос
        embed.description += "\n" + t("logo.result.understood", subject=subject)
        import icon_base
        if icon_base.used.get(subject.lower()):
            embed.description += "\n" + t("logo.result.base", name=icon_base.used[subject.lower()])
    # PNG — отдельным файлом, а не картинкой внутри карточки: картинки в карточках
    # Discord пережимает (на телефоне — в мелкий WebP), а файл скачивается как есть
    await interaction.followup.send(embed=embed, file=discord.File(io.BytesIO(png), f"icon_{ICON_SIZE}.png"),
                                    view=view, ephemeral=True)


DYNAMIC_ITEMS = [LogoActionButton]


# =========================
# СТИЛИ: панель
# =========================

def style_embed(interaction, row):
    return panel_embed(
        interaction, "logo.style_card", title=row[3],
        id=row[0], prompt=(row[4] or "—")[:1500], negative=row[5] or "—",
        model=row[6] or t("logo.default_model"), lora=row[7] or "—",
        refs=len(references_of(row)), default=t("common.yes") if row[10] else t("common.no"),
    )


class StyleModal(Modal, title="СТИЛЬ ИКОНОК"):
    texts = "logo.style_modal"

    name = discord.ui.TextInput(label="Название стиля", max_length=80)
    prompt = discord.ui.TextInput(label="Описание стиля (для нейросети)", style=discord.TextStyle.paragraph, required=False, max_length=4000)
    negative = discord.ui.TextInput(label="Чего избегать", required=False, max_length=500)
    lora = discord.ui.TextInput(label="LoRA (репозиторий HF), если есть", required=False, max_length=200)
    model = discord.ui.TextInput(label="Своя модель text-to-image", required=False, max_length=200)

    def __init__(self, row=None):
        super().__init__()
        self.row = row
        if row:
            self.name.default = row[3]
            self.prompt.default = row[4]
            self.negative.default = row[5]
            self.lora.default = row[7] or ""
            self.model.default = row[6] or ""

    async def on_submit(self, interaction):
        fields = dict(
            name=self.name.value.strip(), prompt=self.prompt.value.strip(), negative_prompt=self.negative.value.strip(),
            lora=self.lora.value.strip() or None, model=self.model.value.strip() or None,
        )
        if self.row:
            update_logo_style(self.row[0], **fields)
            style_id = self.row[0]
        else:
            style_id = save_logo_style(interaction.guild.id, interaction.user.id, fields["name"], fields["prompt"],
                                       fields["negative_prompt"], fields["model"], fields["lora"])
            if len(get_logo_styles(interaction.guild.id)) == 1:
                set_default_logo_style(interaction.guild.id, style_id)
        core.audit(interaction, "logo.style_saved", "logo_style", style_id, fields["name"])
        row = get_logo_style(style_id)
        await interaction.response.send_message(embed=style_embed(interaction, row), view=StyleActions(style_id), ephemeral=True)


class StyleActions(PanelView):
    texts = "logo.style_actions"
    button_order = ("add_refs", "edit", "describe", "make_default", "dataset", "delete")

    def __init__(self, style_id, back_target=None):
        super().__init__(back_target=back_target)
        self.style_id = style_id

    async def _row(self, interaction):
        row = get_logo_style(self.style_id)
        if not row or row[1] != interaction.guild.id:
            await say(interaction, "logo.style_missing")
            return None
        if not can_manage_styles(interaction):
            await say(interaction, "common.need_admin")
            return None
        return row

    @discord.ui.button(label="Изменить", emoji="✏️", style=discord.ButtonStyle.primary)
    async def edit(self, interaction, button):
        row = await self._row(interaction)
        if row:
            await interaction.response.send_modal(StyleModal(row))

    @discord.ui.button(label="Добавить референсы", emoji="🖼️", style=discord.ButtonStyle.success)
    async def add_refs(self, interaction, button):
        row = await self._row(interaction)
        if not row:
            return
        import reference_inbox
        reference_inbox.start_collecting(interaction, self.style_id)
        await interaction.response.send_message(
            t("refs.collect.start", style=row[3], minutes=reference_inbox.COLLECT_SECONDS // 60),
            view=reference_inbox.CollectView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label="По умолчанию", emoji="⭐", style=discord.ButtonStyle.secondary)
    async def make_default(self, interaction, button):
        row = await self._row(interaction)
        if row:
            set_default_logo_style(interaction.guild.id, self.style_id)
            await interaction.response.edit_message(embed=style_embed(interaction, get_logo_style(self.style_id)), view=self)

    @discord.ui.button(label="Разбор референсов", emoji="🔍", style=discord.ButtonStyle.secondary)
    async def describe(self, interaction, button):
        row = await self._row(interaction)
        if not row:
            return
        problem = backend_problem()
        if problem:
            await say(interaction, problem)
            return
        if not references_of(row):
            await say(interaction, "logo.no_references")
            return
        import logo_style_ai
        if not logo_style_ai.enabled():
            await say(interaction, "logo.analysis.need_hf")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        last = [0.0]

        async def progress(done, total):
            if time.time() - last[0] < 3 and done < total:  # не чаще раза в 3 секунды
                return
            last[0] = time.time()
            try:
                await interaction.edit_original_response(content=t("logo.analysis.progress", done=done, total=total))
            except discord.HTTPException:
                pass

        try:
            bible = await logo_style_ai.style_bible(row, force=True, progress=progress)
        except Exception as error:  # noqa: BLE001
            await interaction.followup.send(t("logo.failed", error=str(error)[:300]), ephemeral=True)
            return
        if not bible:
            await interaction.followup.send(t("logo.analysis.failed"), ephemeral=True)
            return
        update_logo_style(self.style_id, prompt=bible.get("invariants", "")[:STYLE_TEXT_MAX])
        report = logo_style_ai.report(get_logo_style(self.style_id)).encode("utf-8")
        await interaction.followup.send(
            t("logo.analysis.done", count=len(references_of(row)), summary=(bible.get("summary_ru") or "—")[:1500]),
            file=discord.File(io.BytesIO(report), filename="razbor_referensov.txt"), ephemeral=True)

    @discord.ui.button(label="Датасет для LoRA", emoji="📦", style=discord.ButtonStyle.secondary, row=1)
    async def dataset(self, interaction, button):
        row = await self._row(interaction)
        if not row:
            return
        if not references_of(row):
            await say(interaction, "logo.no_references")
            return
        buffer, trigger = build_dataset_zip(row)
        try:
            await interaction.response.send_message(
                t("logo.dataset_ready", trigger=trigger),
                file=discord.File(buffer, f"style_{row[0]}_dataset.zip"), ephemeral=True,
            )
        except discord.HTTPException:
            await say(interaction, "access.settings.backup_too_large")

    @discord.ui.button(label="Удалить", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def delete(self, interaction, button):
        row = await self._row(interaction)
        if row:
            delete_logo_style(self.style_id)
            core.audit(interaction, "logo.style_deleted", "logo_style", self.style_id, row[3])
            await interaction.response.edit_message(embed=panel_embed(interaction, "logo.style_deleted"), view=None)


class StylesHome(PanelView):
    texts = "logo.styles"

    def __init__(self, interaction):
        super().__init__()
        for index, row in enumerate(get_logo_styles(interaction.guild.id)[:15]):
            marker = " ⭐" if row[10] else ""
            button = discord.ui.Button(label=f"{row[3][:60]}{marker} · #{row[0]}", style=discord.ButtonStyle.secondary, row=1 + index // 5)

            async def cb(i, style_id=row[0]):
                style = get_logo_style(style_id)
                if not style:
                    await say(i, "logo.style_missing")
                    return
                await i.response.edit_message(embed=style_embed(i, style), view=StyleActions(style_id, back_target=(core.screen(i), self)))

            button.callback = cb
            self.add_item(button)

    @discord.ui.button(label="Новый стиль", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def create(self, interaction, button):
        if not can_manage_styles(interaction):
            await say(interaction, "common.need_admin")
            return
        await interaction.response.send_modal(StyleModal())


# =========================
# /logo
# =========================

async def style_autocomplete(interaction, current):
    if interaction.guild is None:
        return []
    current = (current or "").lower()
    return [
        app_commands.Choice(name=f"{row[3][:90]} (#{row[0]})", value=row[0])
        for row in get_logo_styles(interaction.guild.id)
        if current in row[3].lower()
    ][:25]


def pick_style(guild_id, style_id):
    if style_id:
        row = get_logo_style(style_id)
        return row if row and row[1] == guild_id else None
    rows = get_logo_styles(guild_id)
    return rows[0] if rows else None  # первым идёт стиль по умолчанию


async def _precheck(interaction):
    if not await core.require_command_access(interaction, "logo"):
        return False
    problem = backend_problem()
    if problem:
        await say(interaction, problem)
        return False
    if rate_limited(interaction):
        await say(interaction, "logo.rate_limited", limit=hourly_limit())
        return False
    return True


async def run_generate(interaction, brief, style=None):
    """Сгенерировать иконку с нуля (из /logo brief: или из панели /logo)."""
    if not await _precheck(interaction):
        return
    style_row = pick_style(interaction.guild.id, style)
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    style_row = await ensure_style_text(style_row)  # описание стиля по референсам, если его ещё нет
    try:
        png = await generate_image("generate", style_row, brief)
    except Exception as error:  # noqa: BLE001
        _log.warning("logo generation failed: %s", error)
        await interaction.followup.send(t("logo.failed", error=str(error)[:300]), ephemeral=True)
        return
    await send_result(interaction, style_row, "generate", brief, png)


async def run_stylize(interaction, image, brief="", style=None):
    """Перерисовать макет в стиле (из /logo image:)."""
    if not await _precheck(interaction):
        return
    data, error = await read_attachment(image)
    if error:
        await say(interaction, error)
        return
    style_row = pick_style(interaction.guild.id, style)
    await interaction.response.defer(ephemeral=True, thinking=True)
    source = to_icon_png(open_image(data), 1024)
    style_row = await ensure_style_text(style_row)
    try:
        png = await generate_image("stylize", style_row, brief[:300], source_bytes=source)
    except Exception as error:  # noqa: BLE001
        _log.warning("logo stylize failed: %s", error)
        await interaction.followup.send(t("logo.failed", error=str(error)[:300]), ephemeral=True)
        return
    await send_result(interaction, style_row, "stylize", brief[:300], png, source_bytes=source)


async def add_references_from(interaction, style, file):
    """Референсы в стиль (картинка или zip) — из /eminemovna file: style:. Только для управляющих стилями."""
    if not can_manage_styles(interaction):
        await say(interaction, "common.need_admin")
        return
    row = pick_style(interaction.guild.id, style)
    if row is None:
        await say(interaction, "logo.style_missing")
        return
    try:
        import PIL  # noqa: F401
    except ImportError:
        await say(interaction, "logo.no_packages")
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    if file.filename.lower().endswith(".zip"):
        if file.size > 50 * 1024 * 1024:
            await interaction.followup.send(t("logo.image_too_large"), ephemeral=True)
            return
        try:
            added, skipped = await asyncio.to_thread(import_zip, row, await file.read())
        except zipfile.BadZipFile:
            await interaction.followup.send(t("logo.bad_image"), ephemeral=True)
            return
    else:
        data, error = await read_attachment(file)
        if error:
            await interaction.followup.send(t(error), ephemeral=True)
            return
        added, skipped = (1, 0) if add_reference(row, data) else (0, 1)
    total = len(references_of(get_logo_style(row[0])))
    core.audit(interaction, "logo.references_added", "logo_style", row[0], f"+{added}")
    await interaction.followup.send(t("logo.references_added", added=added, skipped=skipped, total=total, max=MAX_REFERENCES), ephemeral=True)


class LogoBriefModal(Modal, title="НОВАЯ ИКОНКА"):
    texts = "logo.brief_modal"

    brief = discord.ui.TextInput(label="Что изобразить, коротко", min_length=2, max_length=300)

    async def on_submit(self, interaction):
        await run_generate(interaction, self.brief.value.strip())


class LogoHome(PanelView):
    """/logo без параметров: генерация для staff; стили и референсы — в /eminemovna."""

    texts = "logo.home"

    @discord.ui.button(label="Сгенерировать", emoji="✨", style=discord.ButtonStyle.primary)
    async def generate(self, interaction, button):
        await interaction.response.send_modal(LogoBriefModal())


def register_logo(bot):
    @bot.tree.command(name="logo", description="Иконки для ролей в стиле сервера")
    @app_commands.guild_only()
    @app_commands.describe(
        brief="Что изобразить (без картинки — с нуля)",
        image="Твой макет или набросок — перерисую в стиле",
        style="Стиль (по умолчанию — основной)",
    )
    @app_commands.autocomplete(style=style_autocomplete)
    async def logo(interaction, brief: app_commands.Range[str, 0, 300] = "", image: discord.Attachment = None,
                   style: int = None):
        if image is not None:
            await run_stylize(interaction, image, brief or "", style)
        elif brief and len(brief.strip()) >= 2:
            await run_generate(interaction, brief.strip(), style)
        else:
            if not await core.require_command_access(interaction, "logo"):
                return
            await interaction.response.send_message(embed=panel_embed(interaction, "logo.home"),
                                                    view=LogoHome(), ephemeral=True)
