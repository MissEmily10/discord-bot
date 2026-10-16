# Промо-ролики команд

Ролики «как в Discord» для сайта: `/embed`, `/interactive`, `/forms`, `/library`,
`/logo`, `/webhook`, `/pictures`. Готовые MP4 (1080p, со звуком) — в `promo/`.

Картинки для `/pictures` (шаблоны живых картинок, баннеры сервера) рисует настоящий
код бота из ветки main: `python3 scripts/promo/make_pictures_assets.py /путь/к/копии/main`
→ `scripts/promo/assets/pictures/`.

Проверка, что курсор попадает в кнопки: `python3 scripts/promo/check_clicks.py`.

## Пересобрать

```bash
python3 scripts/promo/video_embed.py          # один ролик -> promo/embed.mp4
python3 scripts/promo/video_embed.py frames 10 30   # только кадры (PNG в scripts/promo/cache/) для проверки
```

Нужен пакет `imageio-ffmpeg` (`python3 -m pip install --user imageio-ffmpeg`) — в нём ffmpeg.

## Ник, аватарка, имя бота

`scripts/promo/config.py`: `USER_NAME`, `USER_AVATAR` (путь к картинке в репозитории),
`BOT_NAME` (Purr), `BOT_AVATAR` (анимированная `avatar_crack.gif`).

## Звуки

Синтезированы в `sounds.py`. Свой звук: WAV 16 бит в `scripts/promo/sfx/<имя>.wav`
(`click`, `key`, `pop`, `send`, `success`, `error`, `notify`, `tick`, `whoosh`) — заменит синтезированный.

## Как устроено

- `engine.py` — окно Discord, сообщения, embed'ы, кнопки, меню, модалки, курсор, сборка MP4;
- `kit.py` — заставка, части, финальная карточка «Максимум», вызов команды, модалки;
- `video_*.py` — сценарии. `v.tempo` растягивает паузы на чтение.
