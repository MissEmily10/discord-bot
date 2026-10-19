"""
Картинки для ролика /pictures — настоящим кодом бота (card_templates, cards,
banner_styles). Эти модули есть в ветке main; укажи путь к её копии:

    python3 scripts/promo/make_pictures_assets.py /путь/к/копии/main

Сохраняет PNG в scripts/promo/assets/pictures/.
"""

import io
import pathlib
import sys
from datetime import timedelta

from PIL import Image, ImageDraw, ImageFont

OUT = pathlib.Path(__file__).resolve().parent / "assets" / "pictures"


def main(src):
    sys.path.insert(0, str(src))
    import banner_styles
    import card_templates as ct
    OUT.mkdir(parents=True, exist_ok=True)

    def save(name, png):
        (OUT / f"{name}.png").write_bytes(png)

    # прогресс: казна клана в разных состояниях и темах
    base = {"unit": "золота", "note": "собираем на замок до пятницы", "goal": 500}
    for value, theme in ((340, "cream"), (340, "night"), (390, "night"), (450, "night")):
        data = ct.default_data("progress")
        data.update(base, value=value, theme=theme)
        save(f"progress_{value}_{theme}", ct.render({"template": "progress"}, "Казна клана", data))

    # обратный отсчёт и голосование
    data = ct.default_data("countdown")
    data.update(caption="хэллоуин-ивент", done="начинаем!", theme="maroon",
                when=(ct.now() + timedelta(days=2, hours=5, minutes=30)).strftime("%d.%m.%Y %H:%M"))
    save("countdown", ct.render({"template": "countdown"}, "До ивента", data))
    for n, votes in enumerate(([14, 9, 5], [15, 9, 5])):
        data = ct.default_data("poll")
        data.update(question="Какой ивент в субботу?", theme="pink",
                    rows=[["Мафия", votes[0]], ["Кино", votes[1]], ["Караоке", votes[2]]])
        save(f"poll_{n}", ct.render({"template": "poll"}, "Голосование", data))

    # «все шаблоны» — сетка примеров
    keys = list(ct.TEMPLATES)
    tiles = []
    for n, key in enumerate(keys):
        if key == "rating":
            import cards
            png = cards.render_rating("Рейтинг клана", [{"name": "Аня", "score": 120}, {"name": "Кот", "score": 95},
                                                        {"name": "Пёс", "score": 60}])
        else:
            png = ct.sample(key, list(ct.THEMES)[n % len(ct.THEMES)])
        tile = Image.open(io.BytesIO(png)).convert("RGB")
        tile.thumbnail((300, 300))
        tiles.append(tile)
    cols, cell = 6, 310
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell + 10, rows * cell + 10), (43, 45, 49))
    for n, tile in enumerate(tiles):
        x, y = 10 + (n % cols) * cell, 10 + (n // cols) * cell
        sheet.paste(tile, (x + (300 - tile.width) // 2, y + (300 - tile.height) // 2))
    buf = io.BytesIO()
    sheet.save(buf, "PNG", optimize=True)
    save("templates_sheet", buf.getvalue())

    # баннер сервера: стиль + строки с цифрами
    font_dir = pathlib.Path(src) / "fonts"
    big = ImageFont.truetype(str(font_dir / "Inter-Bold.otf"), 76)
    small = ImageFont.truetype(str(font_dir / "Inter-SemiBold.otf"), 36)
    for key in ("stickers", "lime_pop", "night"):
        im = banner_styles.render_base(key).convert("RGBA")
        title, sub = banner_styles.STYLES[key][3], banner_styles.STYLES[key][4]
        d = ImageDraw.Draw(im)
        d.text((52, 410), "eminemovna", font=big, fill=title, anchor="ls")
        d.text((56, 470), "участников 1 248 · в войсе 17", font=small, fill=sub, anchor="ls")
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "PNG", optimize=True)
        save(f"banner_{key}", buf.getvalue())
    print("готово:", OUT)


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1]).resolve())
