"""
Основа для иконки из набора плоских иконок (датасет HF andrewburns/hf_flat_icons:
1445 иконок flaticon с английскими тегами).

Зачем: модель редактирования лучше рисует, когда у неё есть готовый понятный
предмет. Бот ищет в наборе иконку под запрос («содружество» -> «hands
handshake» -> иконка рукопожатия), берёт её ТОЛЬКО как основу — предмет,
силуэт, компоновку — и перерисовывает целиком в стиле с референсов.
Стиль важнее основы (см. logo.prompt.base_restyle).

Набор: LOGO_BASE_DATASET (по умолчанию andrewburns/hf_flat_icons, пусто — выключить).
CSV и скачанные иконки кэшируются в logo_dir()/icon_base/.
"""

import csv
import io
import logging
import re

_log = logging.getLogger(__name__)

DEFAULT_DATASET = "andrewburns/hf_flat_icons"
CSV_NAME = "flat_icons.csv"
STOP = {"a", "an", "the", "of", "and", "with", "in", "on", "for", "to", "two", "one", "single", "simple", "cute",
        "icon", "small", "big", "its", "is", "at", "by", "together", "each", "other", "style"}
_rows = None
used = {}  # описание предмета -> названия основ (для карточки результата)


def dataset():
    import logo_module
    value = logo_module.cfg("LOGO_BASE_DATASET", DEFAULT_DATASET)
    return "" if value in ("0", "off", "none", "-") else value


def enabled():
    return bool(dataset())


def _folder():
    import logo_module
    folder = logo_module.logo_dir() / "icon_base"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


async def _fetch(url):
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        async with session.get(url) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status} for {url}")
            return await response.read()


def parse_csv(text):
    """-> [{set, title, tags, link}] только строки со ссылкой на картинку."""
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        if (row.get("link") or "").startswith("http"):
            rows.append({key: (row.get(key) or "").strip() for key in ("set", "title", "tags", "link")})
    return rows


async def rows():
    global _rows
    if _rows is not None:
        return _rows
    path = _folder() / CSV_NAME
    if not path.exists():
        data = await _fetch(f"https://huggingface.co/datasets/{dataset()}/resolve/main/{CSV_NAME}")
        path.write_bytes(data)
    _rows = parse_csv(path.read_text(encoding="utf-8", errors="replace"))
    return _rows


def _words(text):
    words = set()
    for word in re.findall(r"[a-z]+", (text or "").lower()):
        if word in STOP or len(word) < 3:
            continue
        words.add(word)
        if word.endswith("es") and len(word) > 4:
            words.add(word[:-2])
        if word.endswith("s") and len(word) > 3:
            words.add(word[:-1])
    return words


def search(subject, rows_list, limit=2):
    """Лучшие иконки под английское описание: совпадения с названием важнее тегов. -> [row]."""
    wanted = _words(subject)
    if not wanted:
        return []
    scored = []
    for row in rows_list:
        name = _words(row["set"])
        tags = set()
        for tag in row["tags"].split(","):
            tags |= _words(tag)
        score = 3 * len(wanted & name) + 2 * len(wanted & tags) + len(wanted & _words(row["title"]))
        if name and name <= wanted:
            score += 2  # всё название иконки есть в запросе («handshake»)
        score -= len(name - wanted)  # «Golden gate bridge» для «golden wreath» — мимо
        if score >= 3:
            scored.append((score, row))
    scored.sort(key=lambda item: -item[0])
    result, seen = [], set()
    for score, row in scored:
        if score < 0.7 * scored[0][0]:
            break  # заметно хуже лучшей — случайное совпадение по одному слову
        if row["set"].lower() in seen:
            continue
        seen.add(row["set"].lower())
        result.append(row)
        if len(result) >= limit:
            break
    return result


def _on_white(data):
    """Прозрачный PNG -> на белом фоне (модели редактирования не любят прозрачность)."""
    from PIL import Image
    image = Image.open(io.BytesIO(data)).convert("RGBA")
    canvas = Image.new("RGBA", image.size, (255, 255, 255, 255))
    canvas.alpha_composite(image)
    output = io.BytesIO()
    canvas.convert("RGB").resize((1024, 1024), Image.LANCZOS).save(output, "PNG")
    return output.getvalue()


async def image_of(row):
    name = re.sub(r"[^0-9a-z]+", "_", row["link"].lower())[-60:]
    path = _folder() / f"{name}.png"
    if not path.exists():
        path.write_bytes(_on_white(await _fetch(row["link"])))
    return path.read_bytes()


async def find(subject, limit=2):
    """-> [(название, PNG основы)]; пусто — подходящей основы нет или набор недоступен."""
    if not enabled() or not subject:
        return []
    try:
        found = search(subject, await rows(), limit)
    except Exception as error:  # сеть, битый CSV — рисуем без основы
        _log.info("icon base search failed: %s", error)
        return []
    result = []
    for row in found:
        try:
            result.append((row["set"], await image_of(row)))
        except Exception as error:
            _log.info("icon base download failed %s: %s", row["link"], error)
    if result:
        if len(used) > 500:
            used.clear()
        used[subject.lower()] = ", ".join(name for name, _ in result)
    return result
