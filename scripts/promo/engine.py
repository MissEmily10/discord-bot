"""
Движок промо-роликов: окно «как в Discord», переписка, ответы бота
(баннеры, embed'ы, кнопки, меню), модальные окна, курсор с кликами, панель
шагов справа, звуки — и сборка в MP4 через ffmpeg (пакет imageio-ffmpeg).

Ролик пишется сценарием: v.type_input("/embed"), v.click("btn:create"),
v.do(lambda s: ...) — каждое действие ставится на внутренние часы v.t.
Кадр = состояние на момент t (все действия до t проигрываются заново).
"""

import copy
import math
import os
import pathlib
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import sounds  # noqa: E402

W, H, FPS = 1920, 1080, 30
K = 1.4                                  # масштаб интерфейса Discord
WIN = (44, 50)                           # левый верх окна Discord в кадре
WIN_W, WIN_H = 920, 700                  # окно в «пикселях Discord»
RAIL, CHANNELS = 72, 220
CHAT_X = RAIL + CHANNELS
CHAT_W = WIN_W - CHAT_X
HEADER_H, INPUT_H = 48, 76
PANEL_X = int(WIN[0] + WIN_W * K + 40)   # правая колонка с шагами

C = {
    "rail": "#1E1F22", "side": "#2B2D31", "chat": "#313338", "embed": "#2B2D31", "input": "#383A40",
    "text": "#DBDEE1", "header": "#F2F3F5", "muted": "#949BA4", "link": "#00A8FC", "blurple": "#5865F2",
    "hover": "#35373C", "select": "#1E1F22", "modal": "#313338", "field": "#1E1F22", "divider": "#3F4147",
    "green": "#248046", "red": "#DA373C", "grey": "#4E5058", "ephemeral": "#949BA4", "popup": "#2B2D31",
}
BTN = {1: ("#5865F2", "#4752C4"), 2: ("#4E5058", "#6D6F78"), 3: ("#248046", "#1A6334"), 4: ("#DA373C", "#A12828"),
       5: ("#4E5058", "#6D6F78")}

FONTS = {"regular": "Inter-Regular.otf", "semibold": "Inter-SemiBold.otf", "bold": "Inter-Bold.otf",
         "mono": "DejaVuSansMono.ttf"}
_fonts = {}


def font(size, weight="regular"):
    key = (round(size * K), weight)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(str(ROOT / "fonts" / FONTS[weight]), key[0])
    return _fonts[key]


def px(v):
    return int(round(v * K))


def rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    a, b = rgb(a) if isinstance(a, str) else a, rgb(b) if isinstance(b, str) else b
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def ease_out(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


# ---------- картинки ----------

_imgs = {}


def img(path, size=None):
    """Картинка из репозитория (кэш); size — сторона квадрата или (w, h) в пикселях кадра."""
    key = (path, size)
    if key not in _imgs:
        im = Image.open(ROOT / path).convert("RGBA")
        if size:
            wh = size if isinstance(size, tuple) else (size, size)
            im = im.resize(wh, Image.LANCZOS)
        _imgs[key] = im
    return _imgs[key]


_gifs = {}


def gif_frame(path, t, width):
    """Кадр GIF на момент t (по своей длительности кадров), шириной width."""
    key = (path, width)
    if key not in _gifs:
        im = Image.open(ROOT / path)
        frames, durs = [], []
        for i in range(im.n_frames):
            im.seek(i)
            f = im.convert("RGBA")
            frames.append(f.resize((width, int(f.height * width / f.width)), Image.LANCZOS))
            durs.append(im.info.get("duration", 60) / 1000)
        _gifs[key] = (frames, durs, sum(durs))
    frames, durs, total = _gifs[key]
    t %= total
    for f, d in zip(frames, durs):
        if t < d:
            return f
        t -= d
    return frames[-1]


def circle_crop(im):
    m = Image.new("L", (im.width * 3, im.height * 3), 0)
    ImageDraw.Draw(m).ellipse((0, 0, m.width - 1, m.height - 1), fill=255)
    m = m.resize(im.size, Image.LANCZOS)
    out = im.copy()
    out.putalpha(Image.composite(im.getchannel("A"), Image.new("L", im.size, 0), m))
    return out


def letter_avatar(letter, color, size):
    im = Image.new("RGBA", (size, size), color + (255,))
    d = ImageDraw.Draw(im)
    d.text((size / 2, size / 2), letter, font=ImageFont.truetype(str(ROOT / "fonts" / FONTS["bold"]), int(size * 0.45)),
           fill=(255, 255, 255), anchor="mm")
    return circle_crop(im)


# ---------- текст с **жирным**, `кодом` и :эмодзи: ----------

def tokens(text):
    """-> [(слово/пробел/эмодзи, стиль)]; эмодзи — путь к картинке."""
    out = []
    bold = code = False
    i = 0
    buf = ""

    def flush():
        nonlocal buf
        if buf:
            for part in _split_spaces(buf):
                out.append((part, "bold" if bold else ("code" if code else "regular")))
            buf = ""
    while i < len(text):
        if text.startswith("**", i):
            flush()
            bold = not bold
            i += 2
        elif text[i] == "`":
            flush()
            code = not code
            i += 1
        elif text[i] == ":" and ":" in text[i + 1:i + 40]:
            j = text.index(":", i + 1)
            name = text[i + 1:j]
            path = EMOJI.get(name)
            if path:
                flush()
                out.append((path, "emoji"))
                i = j + 1
            else:
                buf += text[i]
                i += 1
        elif text[i] == "\n":
            flush()
            out.append(("\n", "br"))
            i += 1
        else:
            buf += text[i]
            i += 1
    flush()
    return out


def _units(text):
    """Буквы для печати; :эмодзи: — одним знаком."""
    out, i = [], 0
    while i < len(text):
        if text[i] == ":" and ":" in text[i + 1:i + 40] and text[i + 1:text.index(":", i + 1)] in EMOJI:
            j = text.index(":", i + 1)
            out.append(text[i:j + 1])
            i = j + 1
        else:
            out.append(text[i])
            i += 1
    return out


def _split_spaces(s):
    parts, cur = [], ""
    for ch in s:
        if ch == " ":
            if cur:
                parts.append(cur)
            parts.append(" ")
            cur = ""
        else:
            cur += ch
    if cur:
        parts.append(cur)
    return parts


EMOJI = {}
for folder, prefix in (("panel_art/emoji/ui3d", "u3_"), ("panel_art/emoji/doodle3d", "d3_"),
                       ("panel_art/emoji/smiley", "smile_")):
    for f in sorted((ROOT / folder).glob("*.png")):
        EMOJI[f.stem] = f"{folder}/{f.name}"


def rich_lines(text, size, width, weight="regular"):
    """Разбивка на строки: [[(кусок, стиль, ширина)]], высота строки."""
    lh = size * 1.375
    lines, cur, cur_w = [], [], 0.0
    for piece, style in tokens(text):
        if style == "br":
            lines.append(cur)
            cur, cur_w = [], 0.0
            continue
        if style == "emoji":
            w = size * 1.375
        else:
            f = font(size, "bold" if style == "bold" else ("mono" if style == "code" else weight))
            w = f.getlength(piece) / K
        if piece == " " and not cur:
            continue
        if cur_w + w > width and cur and piece != " ":
            lines.append(cur)
            cur, cur_w = [], 0.0
            if piece == " ":
                continue
        cur.append((piece, style, w))
        cur_w += w
    lines.append(cur)
    return lines, lh


def draw_rich(canvas, d, x, y, text, size, width, color, weight="regular"):
    """Рисует текст в логических координатах окна (x, y — пиксели кадра). -> высота (лог.)."""
    lines, lh = rich_lines(text, size, width, weight)
    for n, line in enumerate(lines):
        cx = x
        base = y + px(n * lh + size * 1.05)
        for piece, style, w in line:
            if style == "emoji":
                e = img(piece, px(size * 1.3))
                canvas.alpha_composite(e, (int(cx), int(base - px(size * 1.12))))
            elif style == "code":
                f = font(size * 0.9, "mono")
                d.rounded_rectangle((cx, base - px(size * 1.0), cx + px(w), base + px(size * 0.3)), radius=px(3),
                                    fill=rgb("#1E1F22"))
                d.text((cx, base), piece, font=f, fill=rgb(color), anchor="ls")
            else:
                f = font(size, "bold" if style == "bold" else weight)
                d.text((cx, base), piece, font=f, fill=rgb("#F2F3F5") if style == "bold" else rgb(color), anchor="ls")
            cx += px(w)
    return len(lines) * lh


def rich_height(text, size, width, weight="regular"):
    lines, lh = rich_lines(text, size, width, weight)
    return len(lines) * lh


# ============================================================
# СЦЕНАРИЙ
# ============================================================

class Video:
    def __init__(self, name, title, subtitle, steps, user, bot, channel="общий", channels=None, server="eminemovna"):
        self.name, self.title, self.subtitle, self.steps = name, title, subtitle, steps
        self.user, self.bot = user, bot
        self.t = 0.0
        self.tempo = 1.0         # >1 — длиннее паузы на чтение (скорость действий та же)
        self.actions = []        # (t0, t1, fn(state, p))
        self.moves = []          # (t0, t1, target)
        self.clicks = []         # (t, target)
        self.sounds = []         # (t, имя)
        self.base = {"messages": [], "input": "", "popup": None, "modal": None, "channel": channel,
                     "step": -1, "overlay": None, "pressed": {}, "typing": None,
                     "channels": channels or ["общий", "анонсы", "новости", "заявки", "роли"],
                     "server": server, "dropdown": None, "cursor_label": None, "steps": None, "chapter": None}
        self._msg = 0

    # --- часы ---
    def wait(self, s):
        self.t += s * self.tempo
        return self

    def at(self, fn, dur=0.0):
        t0 = self.t
        self.actions.append((t0, t0 + dur, fn))
        return t0

    def do(self, fn):
        """Мгновенное изменение состояния: fn(state)."""
        self.at(lambda s, p: fn(s))
        return self

    def sound(self, name, offset=0.0):
        self.sounds.append((self.t + offset, name))

    # --- шаги справа ---
    def step(self, n):
        self.do(lambda s: s.update(step=n))
        self.sound("tick")
        return self

    # --- сообщения ---
    def message(self, msg, sound="pop"):
        msg = dict(msg)
        self._msg += 1
        msg.setdefault("id", f"m{self._msg}")
        msg.setdefault("channel", self.base["channel"])
        t = self.t
        msg["t"] = t

        def add(s, msg=msg):
            s["messages"].append(copy.deepcopy(msg))
        self.do(add)
        if sound:
            self.sound(sound)
        return msg["id"]

    def edit(self, msg_id, fn, sound=None):
        """Изменить сообщение: fn(msg). Время правки — для мягкого перехода."""
        t = self.t

        def apply(s):
            for m in s["messages"]:
                if m["id"] == msg_id:
                    fn(m)
                    m["t_edit"] = t
        self.do(apply)
        if sound:
            self.sound(sound)

    def bump(self, msg_id):
        """Поднять сообщение вниз ленты (как будто к нему прокрутили / бот прислал панель заново)."""
        t = self.t

        def apply(s):
            keep = [m for m in s["messages"] if m["id"] == msg_id]
            if keep:
                s["messages"] = [m for m in s["messages"] if m["id"] != msg_id] + keep
                keep[0]["t"] = t
        self.do(apply)

    def remove(self, msg_id):
        self.do(lambda s: s.update(messages=[m for m in s["messages"] if m["id"] != msg_id]))

    def bot_typing(self, dur=0.8):
        self.do(lambda s: s.update(typing=self.bot["name"]))
        self.wait(dur)
        self.do(lambda s: s.update(typing=None))

    # --- ввод ---
    def type_input(self, text, dur=None, start=""):
        dur = dur or max(0.4, len(text) * 0.07)
        self.at(lambda s, p, text=text: s.update(input=start + text[:int(round(p * len(text)))]), dur)
        for i in range(len(text)):
            self.sounds.append((self.t + dur * i / max(1, len(text)), "key"))
        self.wait(dur)

    def type_field(self, field_id, text, dur=None):
        dur = dur or max(0.5, min(2.2, len(text) * 0.045))
        units = _units(text)

        def apply(s, p, units=units):
            if s["modal"]:
                for f in s["modal"]["fields"]:
                    if f["id"] == field_id:
                        f["value"] = "".join(units[:int(round(p * len(units)))])
                        f["focus"] = p < 1
        self.at(apply, dur)
        n = min(len(text), 30)
        for i in range(n):
            self.sounds.append((self.t + dur * i / n, "key"))
        self.wait(dur)

    # --- курсор ---
    def move(self, target, dur=0.55):
        self.moves.append((self.t, self.t + dur, target))
        self.wait(dur)

    def click(self, target, move=0.55, after=0.25, sound="click"):
        if target is not None:
            self.move(target, move)
        self.clicks.append((self.t, target))
        tt = self.t

        def press(s, p):
            s["pressed"][target] = tt
        self.at(press)
        if sound:
            self.sound(sound)
        self.wait(after)

    # --- оверлеи ---
    def overlay(self, kind, dur, **data):
        t0 = self.t
        self.at(lambda s, p: s.update(overlay=dict(kind=kind, p=p, t0=t0, **data)), dur)
        self.wait(dur)
        self.do(lambda s: s.update(overlay=None))

    # ============================================================
    # РЕНДЕР
    # ============================================================

    def duration(self):
        return self.t

    def state_at(self, t):
        s = copy.deepcopy(self.base)
        for t0, t1, fn in self.actions:
            if t0 > t:
                break
            p = 1.0 if t1 <= t0 else max(0.0, min(1.0, (t - t0) / (t1 - t0)))
            fn(s, p)
        s["now"] = t
        return s


# ============================================================
# РИСОВАНИЕ КАДРА
# ============================================================

class Frame:
    def __init__(self, video, bg):
        self.v = video
        self.bg = bg
        self.rects = {}
        self._move_cache = {}
        self._move_seen = {}

    # перевод логических координат окна в кадр
    @staticmethod
    def X(x):
        return WIN[0] + px(x)

    @staticmethod
    def Y(y):
        return WIN[1] + px(y)

    def render(self, t):
        v = self.v
        s = v.state_at(t)
        canvas = self.bg.copy()
        d = ImageDraw.Draw(canvas)
        self.rects = {}
        self.window(canvas, d, s)
        self.panel(canvas, d, s)
        if s["overlay"]:
            self.draw_overlay(canvas, s)
        cur = self.cursor_pos(t, s)
        if cur and not s["overlay"]:
            self.draw_cursor(canvas, cur, t, s.get("cursor_label"))
        return canvas

    # ---------- окно ----------
    def window(self, canvas, d, s):
        x0, y0 = WIN
        x1, y1 = x0 + px(WIN_W), y0 + px(WIN_H)
        # тень окна
        sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle((x0 + 10, y0 + 18, x1 + 10, y1 + 18), radius=px(10), fill=(0, 0, 0, 120))
        canvas.alpha_composite(sh.filter(ImageFilter.GaussianBlur(24)))
        mask = Image.new("L", canvas.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((x0, y0, x1, y1), radius=px(10), fill=255)
        win = Image.new("RGBA", canvas.size, rgb(C["chat"]) + (255,))
        wd = ImageDraw.Draw(win)
        self.rail(win, wd, s)
        self.sidebar(win, wd, s)
        self.chat(win, wd, s)
        canvas.paste(win, (0, 0), mask)

    def rail(self, c, d, s):
        X, Y = self.X, self.Y
        d.rectangle((X(0), Y(0), X(RAIL), Y(WIN_H)), fill=rgb(C["rail"]))
        icon = circle_crop(img("panel_art/profile/avatar.png", px(48)))
        d.rounded_rectangle((X(0), Y(56), X(4), Y(96)), radius=px(2), fill=rgb("#F2F3F5"))
        c.alpha_composite(icon, (X(12), Y(52)))
        d.text((X(36), Y(20)), "", fill=0)
        for n, col in enumerate(("#5865F2", "#B6EC34", "#E4AEC4")):
            yy = 120 + n * 56
            d.ellipse((X(12), Y(yy), X(60), Y(yy + 48)), fill=rgb(col))
        d.line((X(20), Y(108), X(52), Y(108)), fill=rgb(C["divider"]), width=px(2))

    def sidebar(self, c, d, s):
        X, Y = self.X, self.Y
        d.rectangle((X(RAIL), Y(0), X(CHAT_X), Y(WIN_H)), fill=rgb(C["side"]))
        off = 0
        if s.get("server_banner"):
            # баннер сервера над каналами, как в Discord
            bw = CHANNELS
            banner = img(s["server_banner"], (px(bw), px(bw * 9 / 16)))
            c.alpha_composite(banner, (X(RAIL), Y(0)))
            off = bw * 9 / 16 - HEADER_H + 8
        else:  # на баннере название уже есть
            d.text((X(RAIL + 16), Y(16)), s["server"], font=font(15, "bold"), fill=rgb(C["header"]))
        if not s.get("server_banner"):
            d.line((X(RAIL), Y(HEADER_H), X(CHAT_X), Y(HEADER_H)), fill=rgb("#1F2023"), width=px(1))
        d.text((X(RAIL + 16), Y(66 + off)), "ТЕКСТОВЫЕ КАНАЛЫ", font=font(11, "bold"), fill=rgb(C["muted"]))
        for n, ch in enumerate(s["channels"]):
            yy = 86 + off + n * 34
            active = ch == s["channel"]
            if active:
                d.rounded_rectangle((X(RAIL + 8), Y(yy), X(CHAT_X - 8), Y(yy + 30)), radius=px(4), fill=rgb("#404249"))
            d.text((X(RAIL + 18), Y(yy + 15)), "#", font=font(18, "regular"), fill=rgb(C["muted"]), anchor="lm")
            d.text((X(RAIL + 38), Y(yy + 15)), ch, font=font(15, "semibold" if active else "regular"),
                   fill=rgb(C["header"] if active else C["muted"]), anchor="lm")
            self.rects[f"ch:{ch}"] = (X(RAIL + 8), Y(yy), X(CHAT_X - 8), Y(yy + 30))
        # панель пользователя
        d.rectangle((X(RAIL), Y(WIN_H - 52), X(CHAT_X), Y(WIN_H)), fill=rgb("#232428"))
        av = self.v.user["avatar"](px(32), s["now"])
        c.alpha_composite(av, (X(RAIL + 8), Y(WIN_H - 42)))
        d.text((X(RAIL + 48), Y(WIN_H - 34)), self.v.user["name"], font=font(14, "semibold"), fill=rgb(C["header"]))
        d.text((X(RAIL + 48), Y(WIN_H - 17)), "В сети", font=font(12), fill=rgb(C["muted"]))

    def chat(self, c, d, s):
        X, Y = self.X, self.Y
        d.text((X(CHAT_X + 16), Y(24)), "#", font=font(22), fill=rgb(C["muted"]), anchor="lm")
        d.text((X(CHAT_X + 38), Y(24)), s["channel"], font=font(16, "bold"), fill=rgb(C["header"]), anchor="lm")
        d.line((X(CHAT_X), Y(HEADER_H), X(WIN_W), Y(HEADER_H)), fill=rgb("#26272B"), width=px(1))
        # сообщения: снизу вверх
        top_limit = HEADER_H + 4
        bottom = WIN_H - INPUT_H - 6
        msgs = [m for m in s["messages"] if m.get("channel", s["channel"]) == s["channel"]]
        heights = [self.msg_height(m) for m in msgs]
        y = bottom
        placed = []
        for m, h in zip(reversed(msgs), reversed(heights)):
            y -= h
            placed.append((m, y))
        layer = Image.new("RGBA", c.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        for m, yy in reversed(placed):
            if yy + self.msg_height(m) < top_limit - 400:
                continue
            age = s["now"] - m["t"]
            k = ease_out(age / 0.25)
            dy = (1 - k) * 10
            self.draw_message(layer, ld, m, yy + dy, s, alpha=k)
        clip = Image.new("L", c.size, 0)
        ImageDraw.Draw(clip).rectangle((X(CHAT_X), Y(top_limit), X(WIN_W), Y(bottom + 2)), fill=255)
        c.paste(layer, (0, 0), Image.composite(layer.getchannel("A"), Image.new("L", c.size, 0), clip))
        self.input_box(c, d, s)
        if s["popup"]:
            self.draw_popup(c, d, s)
        if s["dropdown"]:
            self.draw_dropdown(c, d, s)
        if s["modal"]:
            self.draw_modal(c, d, s)

    def input_box(self, c, d, s):
        X, Y = self.X, self.Y
        y0 = WIN_H - INPUT_H + 8
        d.rounded_rectangle((X(CHAT_X + 16), Y(y0), X(WIN_W - 16), Y(y0 + 44)), radius=px(8), fill=rgb(C["input"]))
        d.ellipse((X(CHAT_X + 28), Y(y0 + 11), X(CHAT_X + 50), Y(y0 + 33)), fill=rgb("#B5BAC1"))
        d.text((X(CHAT_X + 39), Y(y0 + 22)), "+", font=font(18, "bold"), fill=rgb(C["input"]), anchor="mm")
        text = s["input"]
        if text:
            cmd, _, rest = text.partition(" ")
            fx = X(CHAT_X + 64)
            if text.startswith("/") and " " in text:
                w = font(15, "semibold").getlength(cmd)
                d.rounded_rectangle((fx - px(4), Y(y0 + 10), fx + w + px(4), Y(y0 + 34)), radius=px(4), fill=rgb("#4E5058"))
                d.text((fx, Y(y0 + 22)), cmd, font=font(15, "semibold"), fill=rgb(C["header"]), anchor="lm")
                fx += w + px(10)
                text_rest = rest
            else:
                text_rest = text
            d.text((fx, Y(y0 + 22)), text_rest, font=font(15), fill=rgb(C["text"]), anchor="lm")
            caret_x = fx + font(15).getlength(text_rest) + px(2)
            if int(s["now"] * 2) % 2 == 0:
                d.line((caret_x, Y(y0 + 12), caret_x, Y(y0 + 32)), fill=rgb(C["text"]), width=px(1.5))
        else:
            d.text((X(CHAT_X + 64), Y(y0 + 22)), f"Написать в #{s['channel']}", font=font(15), fill=rgb("#6D6F78"),
                   anchor="lm")
        self.rects["input"] = (X(CHAT_X + 16), Y(y0), X(WIN_W - 16), Y(y0 + 44))
        if s["typing"]:
            dots = "•" * (1 + int(s["now"] * 4) % 3)
            d.text((X(CHAT_X + 18), Y(WIN_H - 10)), f"{dots}  {s['typing']} печатает…", font=font(12, "semibold"),
                   fill=rgb(C["muted"]), anchor="lm")

    # ---------- сообщения ----------
    def msg_height(self, m):
        if m.get("kind") == "system":
            return 30
        h = 0
        if m.get("reply"):
            h += 20
        h += 24  # имя
        if m.get("text"):
            h += rich_height(m["text"], 15, CHAT_W - 88)
        for e in m.get("embeds", []):
            h += 6 + self.embed_height(e)
        for row in m.get("rows", []):
            h += 6 + self.row_height(row)
        if m.get("file"):
            h += 6 + 60
        if m.get("ephemeral"):
            h += 22
        return h + 18

    def draw_message(self, c, d, m, y, s, alpha=1.0):
        X, Y = self.X, self.Y
        x = CHAT_X + 16
        if m.get("kind") == "system":
            c.alpha_composite(img("panel_art/emoji/ui3d/u3_next.png", px(20)), (X(x + 12), Y(y + 4)))
            draw_rich(c, d, X(x + 56), Y(y + 1), m["text"], 15, CHAT_W - 88, C["muted"])
            return
        if m.get("reply"):
            d.line((X(x + 18), Y(y + 10), X(x + 18), Y(y + 18)), fill=rgb("#4E5058"), width=px(2))
            d.line((X(x + 18), Y(y + 10), X(x + 38), Y(y + 10)), fill=rgb("#4E5058"), width=px(2))
            uav = self.v.user["avatar"](px(16), s["now"])
            c.alpha_composite(uav, (X(x + 42), Y(y + 2)))
            d.text((X(x + 62), Y(y + 10)), self.v.user["name"], font=font(13, "semibold"), fill=rgb(self.v.user["color"]),
                   anchor="lm")
            nw = font(13, "semibold").getlength(self.v.user["name"]) / K
            d.text((X(x + 66 + nw), Y(y + 10)), f"использует {m['reply']}", font=font(13), fill=rgb(C["muted"]),
                   anchor="lm")
            y += 20
        author = self.v.bot if m.get("kind") == "bot" else (m.get("author") or self.v.user)
        c.alpha_composite(author["avatar"](px(40), s["now"]), (X(x), Y(y + 2)))
        d.text((X(x + 56), Y(y + 2)), author["name"], font=font(15, "semibold"), fill=rgb(author.get("color", "#F2F3F5")))
        nx = x + 56 + font(15, "semibold").getlength(author["name"]) / K + 6
        if author.get("role_icon"):
            c.alpha_composite(img(author["role_icon"], px(20)), (X(nx), Y(y + 1)))
            nx += 26
        if author.get("bot"):
            d.rounded_rectangle((X(nx), Y(y + 4), X(nx + 34), Y(y + 19)), radius=px(3), fill=rgb(C["blurple"]))
            d.text((X(nx + 17), Y(y + 11.5)), "APP", font=font(10, "bold"), fill=(255, 255, 255), anchor="mm")
            nx += 40
        d.text((X(nx), Y(y + 4)), m.get("time", "Сегодня, в 18:42"), font=font(12), fill=rgb(C["muted"]))
        y += 24
        cx = x + 56
        if m.get("text"):
            y += draw_rich(c, d, X(cx), Y(y), m["text"], 15, CHAT_W - 88, C["text"])
        for e in m.get("embeds", []):
            y += 6
            self.draw_embed(c, d, e, cx, y, s, m)
            y += self.embed_height(e)
        for row in m.get("rows", []):
            y += 6
            self.draw_row(c, d, row, cx, y, s)
            y += self.row_height(row)
        if m.get("file"):
            y += 6
            name, size = m["file"]
            d.rounded_rectangle((X(cx), Y(y), X(cx + 400), Y(y + 60)), radius=px(8), fill=rgb(C["embed"]),
                                outline=rgb("#1E1F22"), width=px(1))
            c.alpha_composite(img("panel_art/emoji/ui3d/u3_text.png", px(40)), (X(cx + 10), Y(y + 10)))
            d.text((X(cx + 60), Y(y + 22)), name, font=font(15, "semibold"), fill=rgb(C["link"]), anchor="lm")
            d.text((X(cx + 60), Y(y + 42)), size, font=font(12), fill=rgb(C["muted"]), anchor="lm")
            y += 60
        if m.get("ephemeral"):
            y += 6
            d.text((X(cx), Y(y + 8)), "Только вы видите это сообщение •", font=font(12), fill=rgb(C["muted"]), anchor="lm")
            ww = font(12).getlength("Только вы видите это сообщение • ") / K
            d.text((X(cx + ww), Y(y + 8)), "Скрыть сообщение", font=font(12), fill=rgb(C["link"]), anchor="lm")

    def embed_width(self, e):
        return min(CHAT_W - 88, e.get("width", 432))

    def embed_height(self, e):
        w = self.embed_width(e)
        if e.get("banner"):
            return w * 0.25 + 4
        inner = w - 32 - (88 if e.get("thumb") else 0)
        h = 16
        if e.get("author"):
            h += 26
        if e.get("title"):
            h += rich_height(e["title"], 16, inner, "bold") + 4
        if e.get("desc"):
            h += rich_height(e["desc"], 14, inner) + 2
        for f in e.get("fields", []):
            h += 8 + rich_height(f[0], 14, inner, "bold") + rich_height(f[1], 14, inner)
        if e.get("thumb"):
            h = max(h, 16 + 80)
        if e.get("image"):
            h += 12 + (w - 32) * e.get("image_ratio", 0.25)
        if e.get("footer"):
            h += 26
        return h + 14

    def draw_embed(self, c, d, e, x, y, s, m):
        X, Y = self.X, self.Y
        w = self.embed_width(e)
        h = self.embed_height(e)
        color = e.get("color", C["blurple"])
        if e.get("t_color") is not None:
            k = ease((s["now"] - e["t_color"]) / 0.4)
            color = mix(e.get("old_color", C["blurple"]), color, k)
        d.rounded_rectangle((X(x), Y(y), X(x + w), Y(y + h)), radius=px(4), fill=rgb(C["embed"]))
        d.rounded_rectangle((X(x), Y(y), X(x + 4), Y(y + h)), radius=px(2), fill=color if isinstance(color, tuple) else rgb(color))
        if e.get("banner"):
            fr = gif_frame(e["banner"], s["now"], px(w - 16))
            c.alpha_composite(fr, (X(x + 10), Y(y + 2)))
            return
        inner = w - 32 - (88 if e.get("thumb") else 0)
        yy = y + 14
        if e.get("author"):
            a = e["author"]
            c.alpha_composite(circle_crop(img(a[1], px(22))), (X(x + 16), Y(yy)))
            d.text((X(x + 44), Y(yy + 11)), a[0], font=font(13, "semibold"), fill=rgb(C["header"]), anchor="lm")
            yy += 26
        if e.get("title"):
            yy += draw_rich(c, d, X(x + 16), Y(yy), e["title"], 16, inner, C["header"], "bold") + 4
        if e.get("desc"):
            yy += draw_rich(c, d, X(x + 16), Y(yy), e["desc"], 14, inner, C["text"]) + 2
        for f in e.get("fields", []):
            yy += 8
            yy += draw_rich(c, d, X(x + 16), Y(yy), f[0], 14, inner, C["header"], "bold")
            yy += draw_rich(c, d, X(x + 16), Y(yy), f[1], 14, inner, C["text"])
        if e.get("thumb"):
            th = img(e["thumb"], px(80))
            c.alpha_composite(th, (X(x + w - 96), Y(y + 14)))
        if e.get("image"):
            yy = max(yy, y + 16 + 80 if e.get("thumb") else yy) + 12
            iw = w - 32
            if e["image"].endswith(".gif"):
                fr = gif_frame(e["image"], s["now"], px(iw))
            else:
                fr = img(e["image"], (px(iw), px(iw * e.get("image_ratio", 0.25))))
            mask = Image.new("L", fr.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, fr.width - 1, fr.height - 1), radius=px(4), fill=255)
            c.paste(fr, (X(x + 16), Y(yy)), Image.composite(fr.getchannel("A"), Image.new("L", fr.size, 0), mask))
            yy += iw * e.get("image_ratio", 0.25)
        if e.get("footer"):
            d.text((X(x + 16), Y(y + h - 18)), e["footer"], font=font(12), fill=rgb(C["muted"]), anchor="lm")

    def row_layout(self, row):
        """Позиции компонентов ряда с переносом: [(comp, dx, dy, w)], высота."""
        max_w = CHAT_W - 88
        out, cx, cy, line_h = [], 0, 0, 0
        for comp in row:
            if comp.get("type") == "select":
                w, h = min(max_w, 400), 40
            else:
                label = comp.get("label", "")
                tw = font(14, "semibold").getlength(label) / K
                w = 32 + tw + (26 if comp.get("emoji") else 0) if label else 44
                h = 32
            if cx and cx + w > max_w:
                cx, cy = 0, cy + line_h + 8
                line_h = 0
            out.append((comp, cx, cy, w))
            cx += w + 8
            line_h = max(line_h, h)
        return out, cy + line_h + (2 if row and row[0].get("type") == "select" else 2)

    def row_height(self, row):
        return self.row_layout(row)[1]

    def draw_row(self, c, d, row, x, y, s):
        X, Y = self.X, self.Y
        for comp, dx, dy, w in self.row_layout(row)[0]:
            cx, yy = x + dx, y + dy
            if comp.get("type") == "select":
                d.rounded_rectangle((X(cx), Y(yy), X(cx + w), Y(yy + 40)), radius=px(4), fill=rgb(C["select"]))
                label = comp.get("value") or comp.get("placeholder", "Сделайте выбор")
                if comp.get("value_emoji"):
                    c.alpha_composite(img(comp["value_emoji"], px(20)), (X(cx + 12), Y(yy + 10)))
                    d.text((X(cx + 40), Y(yy + 20)), label, font=font(14), fill=rgb(C["text"]), anchor="lm")
                else:
                    d.text((X(cx + 12), Y(yy + 20)), label, font=font(14),
                           fill=rgb(C["text"] if comp.get("value") else C["muted"]), anchor="lm")
                d.text((X(cx + w - 20), Y(yy + 20)), "⌄", font=font(16, "bold"), fill=rgb(C["text"]), anchor="mm")
                self.rects[comp["id"]] = (X(cx), Y(yy), X(cx + w), Y(yy + 40))
                self.select_pos = getattr(self, "select_pos", {})
                self.select_pos[comp["id"]] = (cx, yy, w)
                continue
            label = comp.get("label", "")
            emoji = comp.get("emoji")
            style = comp.get("style", 2)
            bg, pressed = BTN[style]
            pt = s["pressed"].get(comp["id"])
            col = pressed if pt is not None and 0 <= s["now"] - pt < 0.25 else bg
            if comp.get("disabled"):
                col = "#3B3D44"
            d.rounded_rectangle((X(cx), Y(yy), X(cx + w), Y(yy + 32)), radius=px(8), fill=rgb(col))
            tx = cx + 16
            if emoji:
                c.alpha_composite(img(emoji, px(22)), (X(tx - 2 if label else cx + 11), Y(yy + 5)))
                tx += 26
            if label:
                d.text((X(tx), Y(yy + 16)), label, font=font(14, "semibold"),
                       fill=(255, 255, 255) if not comp.get("disabled") else rgb("#8E9297"), anchor="lm")
            self.rects[comp["id"]] = (X(cx), Y(yy), X(cx + w), Y(yy + 32))

    # ---------- меню команд, выпадающий список, модалка ----------
    def draw_popup(self, c, d, s):
        X, Y = self.X, self.Y
        items = s["popup"]["items"]
        h = 40 + 48 * len(items)
        y1 = WIN_H - INPUT_H + 2
        y0 = y1 - h
        d.rounded_rectangle((X(CHAT_X + 16), Y(y0), X(WIN_W - 16), Y(y1)), radius=px(8), fill=rgb(C["popup"]))
        d.text((X(CHAT_X + 28), Y(y0 + 20)), "КОМАНДЫ — " + self.v.bot["name"].upper(), font=font(11, "bold"),
               fill=rgb(C["muted"]), anchor="lm")
        for n, it in enumerate(items):
            yy = y0 + 40 + n * 48
            if n == s["popup"].get("active", 0):
                d.rounded_rectangle((X(CHAT_X + 22), Y(yy), X(WIN_W - 22), Y(yy + 44)), radius=px(4), fill=rgb("#404249"))
            c.alpha_composite(self.v.bot["avatar"](px(28), s["now"]), (X(CHAT_X + 30), Y(yy + 8)))
            d.text((X(CHAT_X + 68), Y(yy + 22)), it["name"], font=font(15, "semibold"), fill=rgb(C["header"]), anchor="lm")
            nw = font(15, "semibold").getlength(it["name"]) / K
            d.text((X(CHAT_X + 76 + nw), Y(yy + 22)), it["desc"], font=font(13), fill=rgb(C["muted"]), anchor="lm")
            self.rects[f"cmd:{it['name']}"] = (X(CHAT_X + 22), Y(yy), X(WIN_W - 22), Y(yy + 44))

    def draw_dropdown(self, c, d, s):
        X, Y = self.X, self.Y
        dd = s["dropdown"]
        pos = getattr(self, "select_pos", {}).get(dd["select"])
        if not pos:
            return
        x, y, w = pos
        opts = dd["options"]
        h = 8 + 44 * len(opts)
        up = dd.get("up", True) and y - h - 4 >= HEADER_H + 4
        y0 = y - h - 4 if up else y + 44
        d.rounded_rectangle((X(x), Y(y0), X(x + w), Y(y0 + h)), radius=px(4), fill=rgb("#2B2D31"),
                            outline=rgb("#1E1F22"), width=px(1))
        for n, o in enumerate(opts):
            yy = y0 + 4 + n * 44
            if dd.get("hover") == n:
                d.rounded_rectangle((X(x + 4), Y(yy), X(x + w - 4), Y(yy + 42)), radius=px(3), fill=rgb("#35373C"))
            tx = x + 12
            if o.get("emoji"):
                c.alpha_composite(img(o["emoji"], px(22)), (X(tx), Y(yy + 10)))
                tx += 32
            d.text((X(tx), Y(yy + (14 if o.get("desc") else 21))), o["label"], font=font(14, "semibold"),
                   fill=rgb(C["header"]), anchor="lm")
            if o.get("desc"):
                d.text((X(tx), Y(yy + 31)), o["desc"], font=font(12), fill=rgb(C["muted"]), anchor="lm")
            self.rects[f"{dd['select']}:{n}"] = (X(x + 4), Y(yy), X(x + w - 4), Y(yy + 42))

    def draw_modal(self, c, d, s):
        X, Y = self.X, self.Y
        mdl = s["modal"]
        k = ease_out((s["now"] - mdl.get("t", 0)) / 0.2)
        dim = Image.new("RGBA", c.size, (0, 0, 0, int(150 * k)))
        win_mask = Image.new("L", c.size, 0)
        ImageDraw.Draw(win_mask).rectangle((X(0), Y(0), X(WIN_W), Y(WIN_H)), fill=255)
        c.paste(dim, (0, 0), Image.composite(dim.getchannel("A"), Image.new("L", c.size, 0), win_mask))
        w = 440
        fields = mdl["fields"]
        h = 70 + sum(28 + (76 if f.get("tall") else 40) + 12 for f in fields) + 70
        x0 = (WIN_W - w) / 2 + 20
        y0 = (WIN_H - h) / 2 + (1 - k) * 20
        d.rounded_rectangle((X(x0), Y(y0), X(x0 + w), Y(y0 + h)), radius=px(8), fill=rgb(C["modal"]))
        c.alpha_composite(self.v.bot["avatar"](px(24), s["now"]), (X(x0 + 16), Y(y0 + 20)))
        d.text((X(x0 + 48), Y(y0 + 32)), mdl["title"], font=font(18, "bold"), fill=rgb(C["header"]), anchor="lm")
        d.text((X(x0 + w - 24), Y(y0 + 32)), "×", font=font(22), fill=rgb(C["muted"]), anchor="mm")
        yy = y0 + 66
        for f in fields:
            d.text((X(x0 + 16), Y(yy + 10)), f["label"].upper(), font=font(12, "bold"), fill=rgb("#B5BAC1"), anchor="lm")
            yy += 24
            fh = 76 if f.get("tall") else 40
            d.rounded_rectangle((X(x0 + 16), Y(yy), X(x0 + w - 16), Y(yy + fh)), radius=px(4), fill=rgb(C["field"]),
                                outline=rgb(C["blurple"]) if f.get("focus") else None, width=px(1.5))
            val = f.get("value", "")
            if val:
                draw_rich(c, d, X(x0 + 28), Y(yy + 3), val + ("|" if f.get("focus") and int(s["now"] * 3) % 2 == 0 else ""),
                          14, w - 56, C["text"])
            else:
                d.text((X(x0 + 28), Y(yy + 20)), f.get("placeholder", ""), font=font(14), fill=rgb("#6D6F78"), anchor="lm")
            self.rects[f"field:{f['id']}"] = (X(x0 + 16), Y(yy), X(x0 + w - 16), Y(yy + fh))
            yy += fh + 16
        by = y0 + h - 56
        d.rectangle((X(x0), Y(by - 8), X(x0 + w), Y(y0 + h)), fill=rgb("#2B2D31"))
        d.rounded_rectangle((X(x0), Y(y0 + h - 8), X(x0 + w), Y(y0 + h)), radius=px(8), fill=rgb("#2B2D31"))
        d.text((X(x0 + w - 150), Y(by + 20)), "Отмена", font=font(14, "semibold"), fill=rgb(C["header"]), anchor="mm")
        pt = s["pressed"].get(mdl["submit"])
        col = BTN[1][1] if pt is not None and 0 <= s["now"] - pt < 0.25 else BTN[1][0]
        d.rounded_rectangle((X(x0 + w - 112), Y(by + 4), X(x0 + w - 16), Y(by + 36)), radius=px(4), fill=rgb(col))
        d.text((X(x0 + w - 64), Y(by + 20)), "Отправить", font=font(14, "semibold"), fill=(255, 255, 255), anchor="mm")
        self.rects[mdl["submit"]] = (X(x0 + w - 112), Y(by + 4), X(x0 + w - 16), Y(by + 36))

    # ---------- правая колонка ----------
    def panel(self, c, d, s):
        x = PANEL_X
        th = img(getattr(self.v, "thumb", f"panel_art/thumbnail.{self.v.name}.png"), 120)
        c.alpha_composite(th, (x, 74))
        d.text((x + 136, 104), self.v.title, font=ImageFont.truetype(str(ROOT / "fonts" / FONTS["bold"]), 58),
               fill=rgb("#F2EEEA"), anchor="lm")
        sub_f = ImageFont.truetype(str(ROOT / "fonts" / FONTS["semibold"]), 22)
        yy = 150
        for line in _wrap(s.get("chapter") or self.v.subtitle, sub_f, 480 - 136):
            d.text((x + 136, yy), line, font=sub_f, fill=rgb("#E4AEC4"), anchor="lm")
            yy += 28
        y = 236
        step_f = ImageFont.truetype(str(ROOT / "fonts" / FONTS["semibold"]), 25)
        small_f = ImageFont.truetype(str(ROOT / "fonts" / FONTS["regular"]), 19)
        for n, (head, note) in enumerate(s.get("steps") or self.v.steps):
            active = n == s["step"]
            done = n < s["step"]
            lines = _wrap(note, small_f, 410) if active else []
            hh = 54 + len(lines) * 25 + (10 if lines else 0)
            if active:
                card = Image.new("RGBA", c.size, (0, 0, 0, 0))
                ImageDraw.Draw(card).rounded_rectangle((x - 14, y - 8, x + 500, y + hh - 8), radius=18,
                                                       fill=(255, 255, 255, 34), outline=(255, 255, 255, 70), width=1)
                c.alpha_composite(card)
            icon = "panel_art/emoji/ui3d/u3_confirm.png" if done else (
                "panel_art/emoji/ui3d/u3_next.png" if active else None)
            if icon:
                c.alpha_composite(img(icon, 38), (x, y + 2))
            else:
                d.ellipse((x + 10, y + 12, x + 26, y + 28), outline=rgb("#9C8E96"), width=2)
            d.text((x + 52, y + 21), head, font=step_f, fill=rgb("#F2EEEA") if (active or done) else rgb("#B9AAB2"), anchor="lm")
            for k2, line in enumerate(lines):
                d.text((x + 52, y + 52 + k2 * 25), line, font=small_f, fill=rgb("#E9DEE3"), anchor="lm")
            y += hh

    # ---------- оверлеи ----------
    def draw_overlay(self, c, s):
        o = s["overlay"]
        p = o["p"]
        fade = min(1.0, p / 0.12, (1 - p) / 0.12) if o.get("fade", True) else 1.0
        layer = Image.new("RGBA", c.size, (0, 0, 0, 0))
        if o["kind"] == "card":
            o["draw"](layer, p, s["now"])
        a = layer.getchannel("A").point(lambda v: int(v * max(0.0, min(1.0, fade))))
        layer.putalpha(a)
        c.alpha_composite(layer)

    # ---------- курсор ----------
    def cursor_pos(self, t, s):
        """
        Позиция курсора. Пока курсор едет, он следит за целью в каждом кадре
        (кнопка могла сдвинуться: пришло сообщение, доиграла анимация);
        приехав, останавливается там, где цель была в последний момент пути.
        """
        v = self.v
        pos = None
        for i, (t0, t1, target) in enumerate(v.moves):
            if t0 > t:
                break
            if t < t1:
                seen = self._resolve(target)
                if seen is not None:
                    self._move_seen[i] = seen
                end = self._move_seen.get(i) or pos or (W // 2, H // 2)
            else:
                end = self._move_cache.get(i)
                if end is None:
                    end = self._move_seen.get(i) or self._resolve(target) or pos or (W // 2, H // 2)
                    self._move_cache[i] = end
            start = pos or (self.X(WIN_W - 60), self.Y(WIN_H - 40))
            p = 1 if t >= t1 or t1 <= t0 else ease((t - t0) / (t1 - t0))
            # лёгкая дуга
            mx = start[0] + (end[0] - start[0]) * p
            my = start[1] + (end[1] - start[1]) * p - math.sin(math.pi * p) * 18
            pos = (mx, my)
        return pos

    def _resolve(self, target):
        if isinstance(target, tuple):
            return target
        r = self.rects.get(target)
        if not r:
            return None
        return ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2 + 2)

    def draw_cursor(self, c, pos, t, label=None):
        x, y = pos
        if label:
            f = ImageFont.truetype(str(ROOT / "fonts" / FONTS["semibold"]), 20)
            w = f.getlength(label)
            d0 = ImageDraw.Draw(c)
            d0.rounded_rectangle((x + 26, y + 34, x + 46 + w, y + 66), radius=16, fill=rgb("#E4AEC4"))
            d0.text((x + 36, y + 50), label, font=f, fill=rgb("#2A1A22"), anchor="lm")
        for tc, target in self.v.clicks:
            age = t - tc
            if 0 <= age < 0.45:
                r = 10 + age * 90
                ring = Image.new("RGBA", c.size, (0, 0, 0, 0))
                ImageDraw.Draw(ring).ellipse((x - r, y - r, x + r, y + r), outline=(182, 236, 52, int(220 * (1 - age / 0.45))),
                                             width=4)
                c.alpha_composite(ring)
        s = 1.25
        pts = [(0, 0), (0, 26), (7, 20), (12, 31), (17, 29), (12, 18), (21, 18)]
        pts = [(x + px_ * s, y + py_ * s) for px_, py_ in pts]
        sh = Image.new("RGBA", c.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).polygon([(a + 3, b + 4) for a, b in pts], fill=(0, 0, 0, 110))
        c.alpha_composite(sh.filter(ImageFilter.GaussianBlur(3)))
        d = ImageDraw.Draw(c)
        d.polygon(pts, fill=(255, 255, 255), outline=(20, 18, 20))
        d.line(pts + [pts[0]], fill=(20, 18, 20), width=2)


def _wrap(text, f, width):
    words, lines, cur = text.split(), [], ""
    for w_ in words:
        test = (cur + " " + w_).strip()
        if f.getlength(test) > width and cur:
            lines.append(cur)
            cur = w_
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


# ============================================================
# СБОРКА
# ============================================================

def background():
    import render_profile as rp
    return rp.brand_bg(W, H, "wine", seed=7)


def build(video, out_path, preview_frames=None):
    """Рендер в MP4 (H.264 + AAC). preview_frames — только эти секунды в PNG (для проверки)."""
    import imageio_ffmpeg
    bg = background()
    frame = Frame(video, bg)
    total = video.duration()
    n = int(total * FPS)
    if preview_frames:
        outs = []
        # проигрываем подряд до нужных секунд, чтобы курсор знал цели
        wanted = sorted(int(t * FPS) for t in preview_frames)
        # кадры начала движений курсора — чтобы он знал цели
        need = set(wanted) | {int(math.ceil(t0 * FPS)) for t0, _, _ in video.moves if t0 * FPS <= max(wanted)}
        for i in sorted(need):
            im = frame.render(i / FPS)
            if i in wanted:
                outs.append(im)
        return outs
    wav = str(out_path) + ".wav"
    sounds.mix(video.sounds, total, wav)
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-i", wav, "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(n):
        im = frame.render(i / FPS)
        proc.stdin.write(im.convert("RGB").tobytes())
        if i % (FPS * 10) == 0:
            print(f"  {i // FPS}/{int(total)} с", flush=True)
    proc.stdin.close()
    proc.wait()
    os.remove(wav)
    print(f"{out_path}: {total:.1f} с")


def check_clicks(video):
    """
    Проверка: в момент каждого клика курсор стоит внутри своей кнопки/поля.
    Рендерит подряд все кадры движений курсора и кадры кликов. -> [(время, цель, что не так)].
    """
    frame = Frame(video, background())
    need = set()
    for t0, t1, _ in video.moves:
        need.update(range(int(math.ceil(t0 * FPS)), int(math.ceil(t1 * FPS)) + 1))
    clicks = {}
    for tc, target in video.clicks:
        i = int(math.ceil(tc * FPS))
        need.add(i)
        clicks.setdefault(i, []).append((tc, target))
    problems = []
    for i in sorted(need):
        t = i / FPS
        s = video.state_at(t)
        frame.render(t)
        if i not in clicks:
            continue
        pos = frame.cursor_pos(t, s)
        for tc, target in clicks[i]:
            if target is None or pos is None:
                continue
            r = frame.rects.get(target)
            if r is None:
                problems.append((round(tc, 2), target, "цели нет на экране"))
            elif not (r[0] <= pos[0] <= r[2] and r[1] <= pos[1] <= r[3]):
                problems.append((round(tc, 2), target, f"мимо: курсор {tuple(int(v) for v in pos)}, кнопка {r}"))
    return problems
