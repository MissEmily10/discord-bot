# Шрифты баннеров

Бот дописывает ими текст на баннерах форм (banner_render.py):

- **Coolvetica Regular** (`Coolvetica-Regular.otf`) — крупное название на карточке формы. Есть кириллица.
- **DRUNK FONTS Regular** (`DrunkFonts-Regular.otf`) — надпись сверху карточки формы. Только латиница.
- **Dunkin Sans Regular** (`DunkinSans-Regular.otf`) — строка «your … form has been». Только латиница.

Остальные начертания (Italic, Bold, Dunkin) лежат про запас.

Если в тексте есть буквы, которых нет в шрифте (например, русские в DRUNK FONTS
или Dunkin Sans), эту строку бот рисует похожим Inter — вместо пустых квадратиков.
