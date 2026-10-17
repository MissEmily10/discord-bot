"""
Стиль иконок по референсам — подробно и по каждому референсу отдельно.

1. РАЗБОР КАЖДОГО РЕФЕРЕНСА (vision-модель, ответ — JSON): объём (3D / 2.5D /
   плоский), материал и текстура, свет и тени, обводка, характер цвета (тональность,
   насыщенность, температура, контраст — без привязки к конкретным цветам),
   ракурс и перспектива, куда смотрит объект, наклон и поворот, формат и поля,
   фон, пропорции, детализация, настроение — и полный промт, которым можно
   повторить ИМЕННО эту картинку. Разборы хранятся в analysis.json рядом
   с референсами (ключ — имя файла), новые референсы разбираются по одному.

2. БИБЛИЯ СТИЛЯ: текстовая модель сводит все разборы — что общее у
   большинства, то и есть стиль; отдельно — что меняется от картинки к картинке.
   Итог: подробный промт-шаблон с местом под предмет ({subject}), запреты
   (для 3D-набора — «flat 2D vector», для плоского — «3d render» и т. п.)
   и короткое описание по-русски. Если модель не ответила — сводка голосованием
   по полям разборов.

3. ГЕНЕРАЦИЯ (logo_module.generate_image): несколько кандидатов —
   редактирующая модель по самым типичным референсам + текстовая модель по
   библии — и судья (vision-модель) выбирает самый похожий на набор.
"""

import asyncio
import base64
import hashlib
import json
import logging
import pathlib
import re
from collections import Counter

_log = logging.getLogger(__name__)

FIELDS = ("rendering", "material_texture", "lighting", "shading", "outline", "color_character", "perspective",
          "facing", "rotation", "composition", "format_background", "shapes_proportions", "detail_level",
          "depth_effects", "mood")
# поля, по которым голосуем, когда сводим без текстовой модели
VOTE_FIELDS = ("rendering", "perspective", "facing", "outline", "format_background")

ANALYZE_PROMPT = """You are an art director describing ONE icon/sticker image so that an image generator could
reproduce its exact visual STYLE for a different subject. Look very carefully. Answer with ONE JSON object only,
no markdown, with these keys (English, concrete, specific, measurable where possible):
"subject": what is depicted (short; we will NOT copy it),
"rendering": 3D render / 2.5D / flat 2D vector / hand-drawn / pixel / clay / plush etc. and how it reads,
"material_texture": surface material and texture (glossy plastic, soft matte clay, vinyl toy, paper grain, noise...),
"lighting": light direction (e.g. top-left), softness, rim light, specular highlights, reflections,
"shading": how volume is shown (soft gradients, cel shading, ambient occlusion, inner shadows),
"outline": none / thin / thick, color relation (darker shade of fill, black, white), consistency,
"color_character": the CHARACTER of the palette, not exact colors: tonality (pastel / saturated / muted / neon),
  temperature, contrast, how many hues, how colors relate (analogous, complementary), accent usage, value range,
"palette_example": 3-6 approximate hex colors seen (for reference only),
"perspective": camera angle (straight front, slight 3/4 from above, isometric, low angle...), lens feel,
"facing": where the object or its face looks / points (towards viewer, left, right, up-left...),
"rotation": tilt or rotation of the object in degrees and direction (e.g. tilted ~15 degrees clockwise),
"composition": position in frame, how much of the canvas it fills (%), margins, cropping,
"format_background": background (transparent / white / solid color / gradient), die-cut sticker border or not,
  drop shadow or contact shadow,
"shapes_proportions": chunky / thin, rounded / sharp, inflated, cute / serious, simplification level,
"detail_level": low / medium / high, what small details exist,
"depth_effects": thickness, extrusion, bevel, inner glow, gloss, transparency,
"mood": overall feeling,
"prompt": ONE dense paragraph (80-140 words) that would recreate THIS image exactly: subject + all of the above.
"""

SYNTH_PROMPT = """Below are detailed JSON analyses of ALL reference icons of ONE sticker/icon set. Build the style
bible of this set for an image generator. Rules: describe only the STYLE, never copy the subjects; keep what MOST
references share; when they differ, describe the typical case and mention the allowed range; describe color as
character (tonality, saturation, temperature, contrast, harmony, accent usage) — exact hex only as a light hint.
Answer with ONE JSON object only, no markdown, keys:
"invariants": dense English paragraph (120-220 words): rendering type, material and texture, lighting and
  shading, outline, color character, perspective and camera angle, typical facing direction and rotation/tilt,
  proportions and shapes, detail level, depth effects, composition and margins, background/format, mood,
"variations": short English sentence about what changes between the references,
"prompt_template": ONE English generation prompt (150-260 words) for a NEW icon in this exact style; it MUST
  contain the literal placeholder {subject} where the new object goes; include rendering, material, light,
  colors' character, camera angle, facing, rotation, composition (single centered object, fills ~85% of a square
  canvas, small even margin), background exactly as in the set,
"negative": comma-separated English list of things that would break this style (e.g. if the set is 3D: flat 2D
  vector, line art; if flat: 3d render, realistic lighting) plus text, watermark, multiple objects, cropped,
"summary_ru": 3-5 sentences in Russian for the set owner: what the bot understood about the style.

ANALYSES:
"""

JUDGE_PROMPT = """The first image is a contact sheet of reference icons from ONE set. The next images are candidate
icons numbered 1..{n} (in order). Which candidate looks MOST like it belongs to the same set (rendering, material,
lighting, color character, perspective, outline, proportions, background), while being a single clean centered
icon
of: {subject}? Answer with the number only."""


# ============================================================
# ХРАНИЛИЩЕ
# ============================================================

def analysis_path(style_id):
    import logo_module
    folder = logo_module.logo_dir() / "references" / str(style_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "analysis.json"


def load(style_id):
    path = analysis_path(style_id)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"refs": {}, "bible": None, "bible_key": ""}


def save(style_id, data):
    analysis_path(style_id).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def refs_key(paths):
    return hashlib.sha256("|".join(sorted(pathlib.Path(p).name for p in paths)).encode()).hexdigest()[:16]


# ============================================================
# МОДЕЛИ
# ============================================================

def _cfg(name, default):
    import logo_module
    return logo_module.cfg(name, default)


def enabled():
    """Разбор возможен только с токеном HF (без него — старая генерация по одному описанию)."""
    return bool(_cfg("HF_TOKEN", None))


def _client():
    from huggingface_hub import AsyncInferenceClient
    return AsyncInferenceClient(api_key=_cfg("HF_TOKEN", None), timeout=240)


def _vision_models():
    first = _cfg("LOGO_VISION_MODEL", "Qwen/Qwen2.5-VL-72B-Instruct")
    return [first] + [m for m in ("Qwen/Qwen2.5-VL-72B-Instruct", "Qwen/Qwen2.5-VL-7B-Instruct") if m != first]


def _text_models():
    first = _cfg("LOGO_REASONING_MODEL", "Qwen/Qwen2.5-72B-Instruct")
    return [first] + [m for m in ("Qwen/Qwen2.5-72B-Instruct", "meta-llama/Llama-3.3-70B-Instruct") if m != first]


async def _chat(models, content, max_tokens):
    """Первая модель, которая ответила. -> текст."""
    client = _client()
    last = None
    for model in models:
        try:
            response = await client.chat_completion(messages=[{"role": "user", "content": content}], model=model,
                                                    max_tokens=max_tokens, temperature=0.2)
            return response.choices[0].message.content.strip()
        except Exception as error:  # модель недоступна у провайдера — пробуем следующую
            last = error
            _log.info("model %s failed: %s", model, error)
    raise last or RuntimeError("no model answered")


def _image_part(data):
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(data).decode()}}


def parse_json(text):
    """JSON из ответа модели: даже если вокруг есть ``` и пояснения."""
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None
    raw = match.group(0)
    for attempt in (raw, re.sub(r",\s*([}\]])", r"\1", raw)):
        try:
            value = json.loads(attempt)
            return value if isinstance(value, dict) else None
        except ValueError:
            continue
    return None


TRANSLATE_PROMPT = """Translate this description of an icon subject into short, concrete English for an image
generator (what object, its key visual parts, pose if given). No style words, no quotes, answer with the text only:
"""


async def english_subject(brief):
    """Описание предмета по-английски: генераторы плохо понимают русский и рисуют что попало."""
    brief = (brief or "").strip()
    if not re.search("[а-яё]", brief, re.I) or not enabled():
        return brief
    try:
        text = await asyncio.wait_for(_chat(_text_models(), TRANSLATE_PROMPT + brief, 120), 40)
        text = text.strip().strip('"').strip()
        if text and not re.search("[а-яё]", text, re.I):
            return text[:300]
    except Exception as error:
        _log.info("subject translation failed: %s", error)
    try:
        import form_packs
        return await form_packs.to_english(brief)
    except Exception:
        return brief


# ============================================================
# 1. РАЗБОР КАЖДОГО РЕФЕРЕНСА
# ============================================================

async def analyze_reference(path):
    data = pathlib.Path(path).read_bytes()
    text = await _chat(_vision_models(), [{"type": "text", "text": ANALYZE_PROMPT}, _image_part(data)], 900)
    result = parse_json(text)
    if not result:  # модель ответила текстом — сохраняем как промт
        result = {"prompt": text[:1500]}
    return {key: (str(value)[:600] if not isinstance(value, list) else value) for key, value in result.items()}


async def analyze_missing(style_row, progress=None):
    """Разобрать референсы, которых ещё нет в analysis.json. -> (данные, сколько новых)."""
    import logo_module
    refs = logo_module.references_of(style_row)
    data = load(style_row[0])
    names = {pathlib.Path(p).name for p in refs}
    data["refs"] = {name: value for name, value in data["refs"].items() if name in names}  # удалённые — убрать
    todo = [p for p in refs if pathlib.Path(p).name not in data["refs"]]
    semaphore = asyncio.Semaphore(3)
    done = 0

    async def one(path):
        nonlocal done
        async with semaphore:
            try:
                data["refs"][pathlib.Path(path).name] = await analyze_reference(path)
            except Exception as error:
                _log.warning("reference analysis %s: %s", path, error)
            done += 1
            if progress:
                await progress(done, len(todo))

    await asyncio.gather(*(one(path) for path in todo))
    save(style_row[0], data)
    return data, len(todo)


_scheduled = {}


def schedule_analysis(style_id, delay=30):
    """Новые референсы разбираются в фоне — к первой генерации всё уже готово.
    Пока референсы ещё кидают, таймер сдвигается."""
    if not enabled():
        return
    old = _scheduled.pop(style_id, None)
    if old and not old.done():
        old.cancel()

    async def later():
        await asyncio.sleep(delay)
        from database import get_logo_style
        row = get_logo_style(style_id)
        if row:
            try:
                await style_bible(row)
            except Exception as error:
                _log.warning("background analysis of style %s: %s", style_id, error)

    try:
        _scheduled[style_id] = asyncio.get_running_loop().create_task(later())
    except RuntimeError:  # нет цикла событий (тесты, скрипты) — разберётся при генерации
        pass


# ============================================================
# 2. БИБЛИЯ СТИЛЯ
# ============================================================

def vote(analyses):
    """Сводка без текстовой модели: самое частое значение каждого поля."""
    bible = {}
    for field in FIELDS:
        values = [str(a.get(field, "")).strip() for a in analyses if a.get(field)]
        if values:
            bible[field] = Counter(values).most_common(1)[0][0]
    rendering = (bible.get("rendering") or "").lower()
    negative = "flat 2D vector, line art, paper cutout" if "3d" in rendering else "3d render, realistic lighting"
    invariants = ". ".join(f"{field.replace('_', ' ')}: {value}" for field, value in bible.items())
    return {
        "invariants": invariants[:1500],
        "variations": "",
        "prompt_template": ("A single icon of {subject}. " + invariants[:1200] +
                            ". One object, centered, fills about 85% of a square canvas with a small even margin."),
        "negative": negative + ", text, watermark, multiple objects, cropped",
        "summary_ru": "",
    }


async def synthesize(analyses):
    compact = [{k: v for k, v in a.items() if k != "prompt"} | {"prompt": a.get("prompt", "")[:700]}
               for a in analyses]
    text = await _chat(_text_models(), SYNTH_PROMPT + json.dumps(compact, ensure_ascii=False)[:24000], 1600)
    bible = parse_json(text)
    if not bible or "{subject}" not in str(bible.get("prompt_template", "")):
        raise ValueError("bad bible")
    return {key: str(bible.get(key, "")) for key in ("invariants", "variations", "prompt_template", "negative",
                                                     "summary_ru")}


async def style_bible(style_row, force=False, progress=None):
    """Библия стиля (кэш — пока набор референсов тот же). None — референсов нет или модели недоступны."""
    import logo_module
    refs = logo_module.references_of(style_row)
    if not refs:
        return None
    data, new = await analyze_missing(style_row, progress)
    analyses = [value for value in data["refs"].values() if value]
    if not analyses:
        return None
    key = refs_key(refs)
    if data.get("bible") and data.get("bible_key") == key and not force:
        return data["bible"]
    try:
        bible = await synthesize(analyses)
    except Exception as error:
        _log.warning("style synthesis failed, voting instead: %s", error)
        bible = vote(analyses)
    data.update(bible=bible, bible_key=key)
    save(style_row[0], data)
    return bible


def representative(style_row, count=2):
    """Самые типичные референсы: чаще всех совпадают с большинством по ключевым полям."""
    import logo_module
    refs = logo_module.references_of(style_row)
    data = load(style_row[0])
    analyses = {name: value for name, value in data["refs"].items() if value}
    if not analyses:
        return refs[:count]
    common = {field: Counter(str(a.get(field, "")).lower()[:40] for a in analyses.values()).most_common(1)[0][0]
              for field in VOTE_FIELDS}

    def score(path):
        a = analyses.get(pathlib.Path(path).name) or {}
        return sum(str(a.get(field, "")).lower()[:40] == value for field, value in common.items())

    return sorted(refs, key=score, reverse=True)[:count]


def report(style_row):
    """Текстовый отчёт: промт каждого референса и библия — для кнопки «Разбор референсов»."""
    data = load(style_row[0])
    lines = [f"СТИЛЬ: {style_row[3]}", ""]
    bible = data.get("bible")
    if bible:
        lines += ["=== ЧТО БОТ ПОНЯЛ (кратко) ===", bible.get("summary_ru", ""), "",
                  "=== ОБЩЕЕ ДЛЯ НАБОРА ===", bible.get("invariants", ""), "",
                  "=== ЧТО МЕНЯЕТСЯ ===", bible.get("variations", ""), "",
                  "=== ПРОМТ ДЛЯ НОВОЙ ИКОНКИ ({subject} — предмет) ===", bible.get("prompt_template", ""), "",
                  "=== ЗАПРЕТЫ ===", bible.get("negative", ""), ""]
    for n, (name, value) in enumerate(sorted(data["refs"].items()), 1):
        lines.append(f"=== РЕФЕРЕНС {n}: {name} ===")
        for field in ("subject",) + FIELDS + ("palette_example", "prompt"):
            if value.get(field):
                lines.append(f"{field}: {value[field]}")
        lines.append("")
    return "\n".join(lines)


# ============================================================
# 3. СУДЬЯ
# ============================================================

def contact_sheet(paths, size=256, cols=4):
    from PIL import Image
    import io
    images = []
    for path in paths[:12]:
        try:
            images.append(Image.open(path).convert("RGBA").resize((size, size)))
        except Exception:
            continue
    if not images:
        return None
    rows = -(-len(images) // cols)
    sheet = Image.new("RGB", (cols * size, rows * size), (255, 255, 255))
    for n, image in enumerate(images):
        sheet.paste(image, ((n % cols) * size, (n // cols) * size), image)
    output = io.BytesIO()
    sheet.save(output, "PNG")
    return output.getvalue()


async def judge(style_row, candidates, subject):
    """-> индекс лучшего кандидата (0, если судья не ответил)."""
    import logo_module
    if len(candidates) < 2:
        return 0
    sheet = contact_sheet(representative(style_row, 12) or logo_module.references_of(style_row))
    if sheet is None:
        return 0
    content = [{"type": "text", "text": JUDGE_PROMPT.format(n=len(candidates), subject=subject)}, _image_part(sheet)]
    content += [_image_part(png) for png in candidates]
    try:
        text = await _chat(_vision_models(), content, 10)
        number = int(re.search(r"\d+", text).group(0))
        return number - 1 if 1 <= number <= len(candidates) else 0
    except Exception as error:
        _log.info("judge failed: %s", error)
        return 0
