"""
Кто переписывается в роликах. Свой ник и аватарку поставь сюда:
USER_NAME и USER_AVATAR (путь к картинке в репозитории; пусто — кружок с буквой).
"""

import io
import pathlib
import sys

from PIL import Image

import engine

ROOT = engine.ROOT
CACHE = pathlib.Path(__file__).resolve().parent / "cache"

USER_NAME = "emily"            # TODO: ник — пришлёт создатель бота
USER_AVATAR = ""               # TODO: путь к аватарке, например "scripts/promo/assets/me.png"
USER_COLOR = "#E4AEC4"

BOT_NAME = "Purr"
BOT_AVATAR = "panel_art/profile/avatar_crack.gif"     # анимированная
BOT_AVATAR_STILL = "panel_art/profile/avatar.png"     # для картинок-превью


_round = {}


def _avatar(path, letter, color):
    def make(size, t=0.0):
        if path and path.endswith(".gif"):
            frame = engine.gif_frame(path, t, size)
            key = (id(frame), size)
            if key not in _round:
                _round[key] = engine.circle_crop(frame)
            return _round[key]
        if path:
            key = (path, size)
            if key not in _round:
                _round[key] = engine.circle_crop(engine.img(path, size))
            return _round[key]
        key = (letter, color, size)
        if key not in _round:
            _round[key] = engine.letter_avatar(letter, color, size)
        return _round[key]
    return make


USER = {"name": USER_NAME, "color": USER_COLOR, "avatar": _avatar(USER_AVATAR, USER_NAME[:1].upper(), (200, 120, 150))}
BOT = {"name": BOT_NAME, "color": "#F2F3F5", "bot": True, "avatar": _avatar(BOT_AVATAR, "E", (150, 28, 58))}


def member(name, color, rgb_bg):
    """Другой участник сервера (заглушка с буквой)."""
    return {"name": name, "color": color, "avatar": _avatar("", name[:1].upper(), rgb_bg)}


def webhook_author(name, avatar):
    return {"name": name, "color": "#F2F3F5", "bot": True, "avatar": _avatar(avatar, name[:1], (90, 90, 90))}


def preview_png(embeds, rows, name, theme="dark", author_name=BOT_NAME, author_avatar=BOT_AVATAR_STILL):
    """Настоящий рендер превью бота (preview_render) -> путь к PNG в cache/."""
    sys.path.insert(0, str(ROOT))
    import preview_render
    CACHE.mkdir(exist_ok=True)
    assets = {"avatar": (ROOT / author_avatar).read_bytes()}
    for e in embeds:
        for key, field in (("image", "url"), ("thumbnail", "url"), ("author", "icon_url"), ("footer", "icon_url")):
            url = (e.get(key) or {}).get(field)
            if url:
                assets[url] = (ROOT / url).read_bytes()
    png = preview_render.render([{"content": "", "embeds": embeds, "components": rows}],
                                {"name": author_name, "avatar": "avatar", "time": "18:42"}, theme=theme, assets=assets)
    path = CACHE / f"{name}.png"
    path.write_bytes(png)
    im = Image.open(io.BytesIO(png))
    return str(path.relative_to(ROOT)), im.height / im.width
