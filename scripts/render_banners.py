"""
Баннеры экранов бота 1200×300 в фирменном стиле: стеклянная плашка, жирный
строчный текст с растянутыми буквами, 3D-смайлики и иконки. Анимированные GIF
(Discord их проигрывает в embed'ах) + PNG первого кадра (для Figma).

Запуск из корня проекта:
    python3 scripts/render_banners.py                 # все
    python3 scripts/render_banners.py embed.home      # выборочно

Рисует panel_art/<экран>.banner.gif и .png. Шаблоны форм (одобрена,
отклонена, карточка, новая заявка) не трогает — на них бот пишет сам.

Каждый баннер проверяет раскладку: текст не выходит за плашку, а
смайлики и картинки не наезжают на текст (иначе — ошибка, файл не пишется).
"""

import math
import pathlib
import random
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import render_profile as rp  # noqa: E402
from art3d import ICE, LIME, MAROON, MILK, PINK, WINE  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "panel_art"
W, H = 1200, 300
PANEL = (34, -20, 1166, 266)        # стеклянная плашка, как в макетах
TEXT_X = 62
LIMIT = PANEL[2] - 40               # правый край текста

LIME_WAVE = (MILK, (214, 240, 150), LIME, (120, 170, 30))
ICE_WAVE = (MILK, ICE, (120, 150, 190), (60, 80, 120))
PINK_WAVE = (MILK, PINK, MAROON, WINE)


class Layout:
    """Прямоугольники, занятые текстом и картинками: пересечения запрещены."""

    def __init__(self, name):
        self.name = name
        self.boxes = []

    def add(self, kind, box, group=None):
        """group — свои части (строка и смайлик в её букве) друг другу не мешают."""
        x0, y0, x1, y1 = box
        if x0 < 0 or x1 > W or y0 < 0 or y1 > H:
            raise ValueError(f"{self.name}: {kind} выходит за баннер {box}")
        for other_kind, other, other_group in self.boxes:
            if group is not None and group == other_group:
                continue
            if x0 < other[2] and other[0] < x1 and y0 < other[3] and other[1] < y1:
                raise ValueError(f"{self.name}: {kind} {box} наезжает на {other_kind} {other}")
        self.boxes.append((kind, box, group or kind))


def text_box(text, x, base, size, track=-0.075, slots=(), scale=1.0):
    """Рамка букв строки (без смайликов): верх выносных, низ подстрочных."""
    f = rp.font(size)
    tops, bottoms = [], []
    for ch, off, width, sprite in rp.char_layout(text, size, track, slots, scale):
        if ch == " " or sprite:
            continue
        box = f.getbbox(ch, anchor="ls")
        tops.append(box[1])
        bottoms.append(box[3])
    width = rp.text_width(text, size, track, slots, scale)
    return (x, int(base + min(tops)), int(x + width), int(base + max(0, max(bottoms))))


def place_text(layout, kind, text, base, size, limit=LIMIT, x=TEXT_X, smileys=(), scale=1.0):
    """Строка (и смайлики вместо букв smileys) в раскладку; -> (кегль, рамка строки)."""
    slots = set(smileys)
    size = rp.fit_size(text, limit - x, size, slots=slots, scale=scale)
    box = text_box(text, x, base, size, slots=slots, scale=scale)
    if box[2] > limit:
        raise ValueError(f"{layout.name}: {kind} не влезает ({box[2]} > {limit})")
    layout.add(kind, box, group=kind)
    for ch, off, width, sprite in rp.char_layout(text, size, -0.075, slots, scale):
        if sprite:
            if ch not in "oae":
                raise ValueError(f"{layout.name}: смайлик на букве «{ch}»")
            cx, cy = x + off + width / 2, base - size * 0.27
            vis = sprite * rp.SLOT_VISIBLE / 2
            layout.add(f"{kind}: смайлик", (int(cx - vis), int(cy - vis), int(cx + vis), int(cy + vis)), group=kind)
            # соседние буквы не должны заходить на смайлик
            if off - (cx - vis - x) > 0 or (cx + vis - x) > off + width:
                raise ValueError(f"{layout.name}: смайлик шире своего места")
    return size, box


def sprite_box(cx, cy, size, pad=0.06):
    """Видимая часть 3D-смайлика/иконки меньше картинки — поля по краям."""
    p = size * pad
    return (int(cx - size / 2 + p), int(cy - size / 2 + p), int(cx + size / 2 - p), int(cy + size / 2 - p))


def panel_clip(x0, x1):
    """Маска: внутренность стеклянной плашки в полосе x0..x1 (с отступом от краёв)."""
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).rounded_rectangle((x0, 8, x1, PANEL[3] - 8), radius=18, fill=255)
    return m


def clipped(canvas, layer, clip):
    layer.putalpha(rp.ImageChops.multiply(layer.getchannel("A"), clip))
    canvas.alpha_composite(layer)


def check_apart(name, boxes):
    """Движущиеся картинки в кадре не пересекаются (видимые части)."""
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                raise ValueError(f"{name}: картинки в кадре наезжают друг на друга {a} {b}")


def base_bg(kind="wine", seed=2):
    return rp.brand_bg(W, H, kind, seed=seed)


def glass_on(img, alpha=34):
    rp.glass(img, PANEL, radius=26, alpha=alpha)


def save(screen, frames, ms=60):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pal = [f.convert("RGB").quantize(colors=128, method=rp.Image.Quantize.MEDIANCUT, dither=rp.Image.Dither.FLOYDSTEINBERG)
           for f in frames]
    gif = OUT_DIR / f"{screen}.banner.gif"
    pal[0].save(gif, save_all=True, append_images=pal[1:], duration=ms, loop=0, disposal=1)
    frames[0].convert("RGB").save(OUT_DIR / f"{screen}.banner.png", optimize=True)
    print(f"{gif.relative_to(ROOT)}: {len(frames)} кадров, {gif.stat().st_size // 1024} КБ")


def run(word, ch):
    """Растянутые буквы: начало и конец (не включая) самого длинного повтора ch."""
    best = (0, 0)
    i = 0
    while i < len(word):
        j = i
        while j < len(word) and word[j] == ch:
            j += 1
        if j - i > best[1] - best[0]:
            best = (i, j)
        i = max(j, i + 1)
    return best


# ============================================================

def manage(screen, word, letter, wave, after, seed):
    """manage your <word> — как в макетах: лаймовый смайлик в «a», волна по растянутым буквам."""
    lay = Layout(screen)
    bg = base_bg("ice" if screen.startswith("forms") else "wine", seed)
    glass_on(bg)
    s1, b1 = place_text(lay, "строка 1", "manage your", 124, 100, limit=900, smileys=(1,))
    s2, b2 = place_text(lay, "строка 2", word, 240, 112)
    ax = b1[2] + 26 + 44
    lay.add("смайлик справа", sprite_box(ax, 88, 96))
    frames = []
    n = 40
    for i in range(n):
        p = i / n
        f = bg.copy()
        rp.text_run(f, "manage your", TEXT_X, 124, s1, MILK, smiley_at={1}, smiley=rp.solid("smile_lime", p, amp=0.5))
        rp.text_run(f, word, TEXT_X, 240, s2, rp.stretch_colors(word, *run(word, letter), phase=p, palette=wave))
        rp.put(f, rp.solid(after, p + 0.3, amp=0.6), ax, 88 + 5 * math.sin(2 * math.pi * p), 96)
        frames.append(f)
    save(screen, frames)


def embed_home():
    manage("embed.home", "embeeeeeeeeeds", "e", PINK_WAVE, "cry", 2)


def forms_review_home():
    manage("forms.review_home", "fooooooooooorms", "o", LIME_WAVE, "monocle", 6)


def _floating(bg_kind, seed, icons, n, zone, lay):
    """Иконки всплывают снизу вверх только в свободной зоне справа от текста (zone = x0, x1)."""
    bg = base_bg(bg_kind, seed)
    glass_on(bg)
    imgs = [rp.icon(nm) for nm in icons]
    rng = random.Random(seed)
    x0, x1 = zone
    x1 = min(x1, PANEL[2] - 80)     # правее — три точки плашки
    size = 80
    lay.add("зона иконок", (int(x0), 0, int(x1), H))
    cols = max(1, int((x1 - x0) // (size + 24)))
    # в одной колонке иконки идут друг за другом с равным шагом — не догоняют друг друга
    per_col = math.ceil(len(imgs) / cols)
    spots = [(x0 + size / 2 + 4 + (k % cols) * (x1 - x0 - size - 8) / max(1, cols - 1),
              (k // cols) / per_col + (k % cols) / (cols * per_col), rng.uniform(0, 1)) for k in range(len(imgs))]
    if (H + 2 * size) / per_col < size * 1.3:
        raise ValueError(f"{lay.name}: иконкам тесно в колонке")
    clip = panel_clip(x0, x1)
    layers = []
    for i in range(n):
        p = i / n
        f = bg.copy()
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        boxes = []
        for (x, ph, rot), img in zip(spots, imgs):
            y = H + size - ((p + ph) % 1) * (H + 2 * size)
            im = rp.fit(img, size).rotate(14 * math.sin(2 * math.pi * (p + rot)), resample=Image.BICUBIC)
            rp.put(layer, im, x, y)
            boxes.append(sprite_box(x, y, size, pad=0.12))
        check_apart(lay.name, boxes)
        clipped(f, layer, clip)
        layers.append(f)
    return layers


def library_home():
    lay = Layout("library.home")
    s1, b1 = place_text(lay, "строка 1", "your", 124, 100, limit=600, smileys=(1,))
    word = "libraaaaaaary"
    s2, b2 = place_text(lay, "строка 2", word, 240, 112)
    n = 48
    zone = (max(b1[2], b2[2]) + 50, PANEL[2] - 20)
    backs = _floating("wine", 13, ["doodle3d/d3_book.png", "ui3d/u3_library.png", "ui3d/u3_folder.png", "doodle3d/d3_star.png",
                                   "doodle3d/d3_music.png", "ui3d/u3_text.png", "doodle3d/d3_film.png", "doodle3d/d3_heart.png",
                                   "ui3d/u3_image.png", "doodle3d/d3_sparkles.png"], n, zone, lay)
    frames = []
    for i, f in enumerate(backs):
        p = i / n
        rp.text_run(f, "your", TEXT_X, 124, s1, MILK, smiley_at={1}, smiley=rp.solid("stars", p, amp=0.5))
        rp.text_run(f, word, TEXT_X, 240, s2, rp.stretch_colors(word, *run(word, "a"), phase=p, palette=PINK_WAVE))
        frames.append(f)
    save("library.home", frames)


def interactive_home():
    lay = Layout("interactive.home")
    bg = base_bg("wine", 9)
    glass_on(bg)
    s1, b1 = place_text(lay, "строка 1", "make it", 124, 100, limit=760)
    word = "interaaaaactive"
    s2, b2 = place_text(lay, "строка 2", word, 240, 112, limit=820)
    btn_box = (860, 70, 1130, 150)
    lay.add("кнопка", btn_box)
    lay.add("курсор", (1040, 150, 1140, 250))
    lay.add("смайлик", sprite_box(995, 205, 96))
    cursor = rp.icon("ui3d/u3_send.png")
    n = 40
    frames = []
    for i in range(n):
        p = i / n
        f = bg.copy()
        rp.text_run(f, "make it", TEXT_X, 124, s1, MILK, shadow=True)
        rp.text_run(f, word, TEXT_X, 240, s2, rp.stretch_colors(word, *run(word, "a"), phase=p, palette=LIME_WAVE))
        press = 0.42 < p < 0.52
        d = ImageDraw.Draw(f)
        x0, y0, x1, y1 = btn_box
        off = 6 if press else 0
        d.rounded_rectangle((x0, y0 + 10, x1, y1 + 4), radius=36, fill=(140, 90, 110))
        d.rounded_rectangle((x0, y0 + off, x1, y1 - 6 + off), radius=36, fill=PINK, outline=(34, 30, 34), width=4)
        d.text(((x0 + x1) / 2, (y0 + y1 - 6) / 2 + off), "click me", font=rp.font(34), fill=(34, 30, 34), anchor="mm")
        t = rp.ease(min(1, p / 0.42)) if p < 0.6 else 1 - rp.ease((p - 0.6) / 0.4)
        rp.put(f, cursor.rotate(200, resample=Image.BICUBIC), 1110 - 40 * t, 220 - 60 * t, 84)
        rp.put(f, rp.solid("laugh" if 0.45 < p < 0.8 else "smile", p, amp=0.5), 995, 205, 96)
        frames.append(f)
    save("interactive.home", frames)


def logo_home():
    lay = Layout("logo.home")
    o_index = "need some logos?".index("o", 10)
    s1, b1 = place_text(lay, "строка 1", "need some logos?", 124, 100, limit=1010, smileys=(o_index,))
    s2, b2 = place_text(lay, "строка 2", "deal!", 240, 112, limit=600)
    icons_x = [b2[2] + 80 + k * 120 for k in range(4)]
    if icons_x[-1] + 48 > PANEL[2] - 80:
        raise ValueError("logo.home: иконкам не хватает места")
    for k, x in enumerate(icons_x):
        lay.add(f"иконка {k}", sprite_box(x, 210, 96))
    bg = base_bg("wine", 14)
    glass_on(bg)
    icons = [rp.icon("doodle3d/d3_palette.png"), rp.icon("doodle3d/d3_brush.png"), rp.icon("doodle3d/d3_pencil.png"),
             rp.icon("doodle3d/d3_sparkles.png")]
    n = 40
    frames = []
    for i in range(n):
        p = i / n
        f = bg.copy()
        rp.text_run(f, "need some logos?", TEXT_X, 124, s1, MILK, smiley_at={o_index}, smiley=rp.solid("cool", p, amp=0.5))
        rp.text_run(f, "deal!", TEXT_X, 240, s2, [MILK, MILK, MILK, MILK, LIME])
        for k, (x, img) in enumerate(zip(icons_x, icons)):
            hop = max(0.0, math.sin(2 * math.pi * (p - k / 8)))
            rp.put(f, img, x, 210 - 10 * hop, 96)
        frames.append(f)
    save("logo.home", frames)


def eminemovna_home():
    lay = Layout("eminemovna.home")
    rng = random.Random(10)
    bg = base_bg("wine", 2)
    glass_on(bg)
    text = "eminemovnaaaa"
    size, box = place_text(lay, "надпись", text, 200, 150, smileys=(6,))
    n = 60
    frames = []
    for i in range(n):
        p = i / n
        three = 22 <= i < 52
        sm = rp.solid("smile", p * 2, amp=0.6) if three else rp.flat("smile")
        f = bg.copy()
        rp.text_run(f, text, TEXT_X, 200, size, rp.stretch_colors(text, *run(text, "a"), phase=p, palette=PINK_WAVE),
                    smiley_at={6}, smiley=sm)
        if 19 <= i < 25 or 49 <= i < 55:
            f = rp.glitch(f, 0.8, rng)
        frames.append(f)
    save("eminemovna.home", frames)


def webhooks_home():
    lay = Layout("webhooks.home")
    s1, b1 = place_text(lay, "строка 1", "send it via", 124, 100, limit=800)
    word = "webhoooooooks"
    s2, b2 = place_text(lay, "строка 2", word, 240, 112, limit=880)
    face_x = b1[2] + 70
    lay.add("смайлик", sprite_box(face_x, 88, 96))
    x0, x1 = max(face_x + 60, b2[2] + 40), PANEL[2] - 80
    lay.add("зона самолётиков", (int(x0), 0, int(x1), H))
    bg = base_bg("ice", 15)
    glass_on(bg)
    plane = rp.icon("ui3d/u3_send.png")
    n = 40
    frames = []
    for i in range(n):
        p = i / n
        f = bg.copy()
        rp.text_run(f, "send it via", TEXT_X, 124, s1, MILK)
        rp.text_run(f, word, TEXT_X, 240, s2, rp.stretch_colors(word, *run(word, "o"), phase=p, palette=ICE_WAVE))
        rp.put(f, rp.solid("wink", p, amp=0.6), face_x, 88 + 5 * math.sin(2 * math.pi * p), 96)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        boxes = []
        for k in range(3):
            q = (p + k / 3) % 1
            size = 72
            x = x0 + size / 2 + q * (x1 - x0 - size)
            y = H + size / 2 - q * (H + size)
            rp.put(layer, plane, x, y, size)
            boxes.append(sprite_box(x, y, size, pad=0.12))
        check_apart("webhooks.home", boxes)
        clipped(f, layer, panel_clip(int(x0), int(x1)))
        frames.append(f)
    save("webhooks.home", frames)


def access_denied():
    lay = Layout("access_denied")
    rng = random.Random(12)
    bg = base_bg("lime", 4)
    glass_on(bg, alpha=22)
    s1, b1 = place_text(lay, "строка 1", "oops! your access", 124, 100, limit=1060)
    word = "was denied!"
    faces = [i for i, ch in enumerate(word) if ch == "e"]
    s2, b2 = place_text(lay, "строка 2", word, 240, 112, limit=900, smileys=faces, scale=0.8)
    n = 40
    frames = []
    for i in range(n):
        p = i / n
        f = bg.copy()
        rp.text_run(f, "oops! your access", TEXT_X, 124, s1, MILK)
        three = (i // 10) % 2 == 1
        rp.text_run(f, word, TEXT_X, 240, s2, [MILK] * 4 + [MAROON] * 6 + [MILK], smiley_at=set(faces),
                    smiley=rp.solid("angry", p, amp=0.8) if three else rp.flat("angry"), smiley_scale=0.8)
        if i % 10 in (8, 9):
            f = rp.glitch(f, 1.0, rng)
        frames.append(f)
    save("access_denied", frames)


def select_public_menu():
    lay = Layout("select.public_menu")
    s1, b1 = place_text(lay, "строка 1", "pick your", 124, 100, limit=900, smileys=(6,))
    word = "roooooooles"
    s2, b2 = place_text(lay, "строка 2", word, 240, 112)
    n = 48
    zone = (max(b1[2], b2[2]) + 50, PANEL[2] - 20)
    backs = _floating("wine", 21, ["ui3d/u3_roles.png", "doodle3d/d3_crown.png", "doodle3d/d3_shield.png", "doodle3d/d3_gem.png",
                                   "ui3d/u3_roles.png", "doodle3d/d3_medal.png", "doodle3d/d3_heart.png", "doodle3d/d3_star.png"], n, zone, lay)
    frames = []
    for i, f in enumerate(backs):
        p = i / n
        rp.text_run(f, "pick your", TEXT_X, 124, s1, MILK, smiley_at={6}, smiley=rp.solid("love", p, amp=0.5))
        rp.text_run(f, word, TEXT_X, 240, s2, rp.stretch_colors(word, *run(word, "o"), phase=p, palette=PINK_WAVE))
        frames.append(f)
    save("select.public_menu", frames)


BANNERS = {
    "embed.home": embed_home, "library.home": library_home, "interactive.home": interactive_home,
    "forms.review_home": forms_review_home, "logo.home": logo_home, "eminemovna.home": eminemovna_home,
    "webhooks.home": webhooks_home, "access_denied": access_denied, "select.public_menu": select_public_menu,
}


def main():
    wanted = set(sys.argv[1:])
    for screen, fn in BANNERS.items():
        if not wanted or screen in wanted:
            fn()


if __name__ == "__main__":
    main()
