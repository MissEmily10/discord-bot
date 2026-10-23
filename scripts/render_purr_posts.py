"""
100 баннеров про бота Purr (1200×600) — на 20 композициях из
render_news.py, у каждого свой текст, надпись, фон, смайлик, иконки и дудлы.
Акцент — клановая система (около половины баннеров).

Запуск из корня проекта:
    python3 scripts/render_purr_posts.py                 # все 100
    python3 scripts/render_purr_posts.py 1 7 42          # выборочно (номера)
    python3 scripts/render_purr_posts.py --figma <папка> # + слои для Figma

Рисует panel_art/purr/purr.NNN.<шаблон>.png и листы _sheet_1…5.png (по 20).
Раскладку каждого баннера проверяет render_news (ничего не наезжает), дудлы
ставятся только в свободные места.
"""

import json
import pathlib
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import render_news as rn  # noqa: E402
from render_news import ICE_WAVE, INK_WAVE, LIME_WAVE, PINK_WAVE  # noqa: E402

ROOT = rn.ROOT
OUT_DIR = ROOT / "panel_art" / "purr"
D3 = "doodle3d/d3_"

CLAN_DOODLES = ["clan_chest", "clan_coin", "clan_swords", "clan_crest", "clan_pennant", "clan_castle", "clan_coins",
                "clan_scroll", "clan_helmet", "clan_sack", "clan_torch", "clan_podium", "clan_respect", "clan_tag",
                "clan_ledger", "clan_calendar"]
BOT_DOODLES = ["sparkles", "heart", "star", "chat", "bell", "palette", "music", "cat", "cloud", "bolt"]


def clan(t, **d):
    return (t, "clan", d)


def bot(t, **d):
    return (t, "bot", d)


POSTS = [
    # ================= КЛАНЫ =================
    clan("news", tag="#кланы", date="скоро", word="claaaans", mood="cool", wave=PINK_WAVE,
         title="Кланы переезжают в Purr",
         text="Создание, казна, биллборд, квесты и магазин — всё в одном боте и с кнопками."),
    clan("split", tag="#кланы", word="claaans", mood="smile_lime", ic_огонь=D3 + "clan_castle.png",
         title="Свой клан — впятером",
         text="Глава, 2 зама и 2 представителя. По 1000 голды с каждого — и клан готов."),
    clan("rules", title="Как создать клан", word="claaaans", ic_щит=D3 + "clan_pennant.png",
         items="Собери 5 основателей\nКаждый подтверждает 1000 голды\nНа сбор — 1 час\nВсе подтвердили — клан создан"),
    clan("feature", name="Голда замораживается сразу", badge="safe",
         text="Каждый основатель подтверждает — его 1000 уходит в «заморозку». Кто-то отказался — всем вернут.",
         how="/clan → Создать клан", word="saaafe"),
    clan("milestone", number="50", unit="membeeeers", text="столько человек может быть в одном клане"),
    clan("milestone", number="2–5", unit="leeetters", text="длина тега клана — и он уникален на сервере"),
    clan("update", version="×5", title="Создание клана", word="foundeeers", date="кланы",
         items="Глава + 2 зама + 2 представителя\nНазвание 3–24 знака, тег 2–5\nНазвание и тег не повторяются"),
    clan("news", tag="#казна", date="кланы", word="treaaasury", mood="stars", bg="lime", seed=8, wave=LIME_WAVE,
         title="Казна клана растёт от активности",
         text="75% награды за активность уходит в казну, а за каждые 1000 — +1 респект."),
    clan("milestone", number="75%", unit="treaaasury", text="награды за активность — сразу в казну клана"),
    clan("breaking", top_word="treasury", bottom_line="казна · кланы · purr", mood="laugh",
         title="Награды падают в казну без ручных подтверждений",
         text="Каждая награда — запись в журнале: дважды одна не зачтётся."),
    clan("digest", period="казна клана", word="moneeey",
         cards="Пополнение|75% награды за активность|" + D3 + "clan_coins.png\n"
               "Респект|+1 за каждые 1000 в казне|" + D3 + "clan_respect.png\n"
               "Журнал|каждое движение денег записано|" + D3 + "clan_ledger.png"),
    clan("soon", caption="до продления клана", days="03", hours="11", minutes="20", word="reneeew",
         ic_часы=D3 + "hourglass.png", ic_ракета=D3 + "clan_calendar.png"),
    clan("alert", word="nooote!", mood="shock", ic_мегафон=D3 + "clan_calendar.png",
         title="Продление: −5000 из казны раз в 30 дней",
         text="Purr напомнит главе и замам за 3 дня и за 1 день — с суммой, которой не хватает."),
    clan("maintenance", word="oh nooo", mood="sad", ic_ключ=D3 + "clan_sack.png", ic_шестерёнка=D3 + "clan_coin.png",
         title="Не хватило на продление — долг 7 дней", window="7 дней на погашение",
         text="Долг гасится сам, как только казна наберёт сумму. Не успели — роспуск."),
    clan("news", tag="#огонёк", date="топ-3", word="fiiiire", mood="cool", bg="wine", seed=12,
         title="Огонёк: ×1.1–1.5 к казне при продлении",
         text="Сколько недель подряд клан в топ-3 биллборда — столько и огонька."),
    clan("milestone", number="×1.5", unit="fiiiire", text="максимальный огонёк — за серию недель в топ-3"),
    clan("split", tag="#огонёк", word="fiiire", mood="cool", ic_огонь=D3 + "clan_torch.png",
         title="Серия держится, пока ты в топ-3",
         text="Выпал из топа на неделю — огонёк сгорает. Серия видна в карточке клана."),
    clan("news", tag="#биллборд", date="неделя", word="biiillboard", mood="stars", bg="dark", seed=4, wave=LIME_WAVE,
         title="Недельный биллборд кланов",
         text="Войс, сообщения и респект — каждую неделю новый зачёт и снимок мест навсегда."),
    clan("rules", title="Как считаются баллы", word="pooooints", ic_щит=D3 + "clan_podium.png",
         items="Час в войсе от 3 участников — 5\n900 сообщений — 5\nРеспект — 5\nОстатки переносятся внутри недели"),
    clan("winners", title="Топ кланов недели", first="Волки", second="Котики", third="Совы", word="claaaans",
         ic_кубок=D3 + "clan_crest.png", ic_звезда=D3 + "clan_pennant.png"),
    clan("winners", title="Больше всех часов в войсе", first="Совы", second="Лисы", third="Волки", word="voiiice",
         ic_кубок=D3 + "headset.png", ic_звезда=D3 + "mic.png"),
    clan("winners", title="Самая большая казна", first="Котики", second="Волки", third="Ежи", word="treaaasury",
         ic_кубок=D3 + "clan_chest.png", ic_звезда=D3 + "clan_coins.png"),
    clan("poll", question="Во сколько закрывать неделю биллборда?",
         options="Пн 00:00:52\nВс 21:00:31\nПт 18:00:17", hint="кланы голосуют кнопками", word="voooote", mood="think"),
    clan("breaking", top_word="billboard", bottom_line="топ-3 · огонёк · кланы", mood="shock",
         title="Войс считается честно: от трёх участников клана",
         text="Зашёл, вышел, перешёл — бот закрывает отрезок и считает секунды."),
    clan("news", tag="#квесты", date="30 дней", word="queeests", mood="monocle", bg="ice", seed=21, wave=ICE_WAVE,
         title="3 квеста на каждый срок клана",
         text="Стипендия в казну — за день до продления. Чем больше квестов, тем больше."),
    clan("update", version="5000", title="Стипендия за квесты", word="queeests", date="за срок",
         items="1 квест — 1000 в казну\n2 квеста — 2500\n3 квеста — 5000"),
    clan("soon", caption="до конца срока квестов", days="12", hours="06", minutes="45", word="queeests",
         ic_часы=D3 + "clan_scroll.png", ic_ракета=D3 + "star.png"),
    clan("event", day="01", month="НОЯ", weekday="суббота", title="Турнир кланов",
         text="Purr сам опубликует анонс и напомнит за час. Очки идут в биллборд.",
         time="18:00 МСК", place="#кланы", word="tournameeent", ic_шарик=D3 + "clan_swords.png"),
    clan("event", day="15", month="НОЯ", weekday="суббота", title="Войс-марафон кланов",
         text="Сидим в войсе от трёх человек — часы идут в зачёт недели.",
         time="весь день", place="#войс-кланов", word="voiiice", ic_шарик=D3 + "headset.png"),
    clan("news", tag="#магазин", date="казна", word="shoooop", mood="love", bg="wine", seed=30,
         title="Магазин клана",
         text="Глава и замы покупают из казны: новое название, герб и цвет роли."),
    clan("feature", name="Покупки без минуса в казне", badge="new!",
         text="Два зама нажали «Купить» одновременно — второй увидит «не хватает», казна в минус не уйдёт.",
         how="/clan → Магазин", word="shoooop"),
    clan("digest", period="магазин клана", word="shoooop",
         cards="Название|новое имя клана из казны|" + D3 + "clan_tag.png\n"
               "Герб|иконка клана в карточке|" + D3 + "clan_crest.png\n"
               "Цвет роли|клан узнают по цвету|" + D3 + "palette.png"),
    clan("giveaway", prize="5000 в казну клана", text="Получит клан с лучшей активностью недели.",
         until="до воскресенья", winners="1 клан", word="giveaaaaway", ic_подарок=D3 + "clan_chest.png", mood="cool"),
    clan("giveaway", prize="Смена герба бесплатно", text="Для клана, который первым выполнит 3 квеста.",
         until="до 30.11", winners="1 клан", word="priiiize", ic_подарок=D3 + "clan_crest.png", mood="stars"),
    clan("rules", title="Кто что может в клане", word="rooooles", ic_щит=D3 + "clan_helmet.png",
         items="Приглашают глава, замы, представители\nГлава исключает любого\nЗам — только рядовых\nПокупки — глава и замы"),
    clan("split", tag="#иерархия", word="leaaader", mood="monocle", ic_огонь=D3 + "crown.png",
         title="Клан не останется без главы",
         text="Глава ушёл с сервера — главой станет зам с самым большим стажем."),
    clan("feature", name="Передать главу", badge="pro",
         text="Глава выбирает зама или участника — новый глава подтверждает. Уйти, не передав, нельзя.",
         how="/clan → Участники → Передать главу", word="leaaader"),
    clan("welcome", word="joooin", count="+1", text="новый участник в клане — заявка пришла главе в ЛС"),
    clan("welcome", word="weeelcome", count="+5", text="основателей подтвердили — клан создан!"),
    clan("news", tag="#карточки", date="10 стилей", word="caaaards", mood="stars", bg="ice", seed=40, wave=ICE_WAVE,
         title="Карточка клана живёт сама",
         text="Состав, казна и огонёк обновляются, а кнопка «Вступить» отправит заявку главе."),
    clan("photo", title="Карточка клана", caption="10 стилей карточек", word="caaards", ic_камера=D3 + "clan_crest.png",
         text="Название, тег, глава, казна, огонёк — в фирменном стиле.",
         photo=str(ROOT / "panel_art" / "emoji" / "doodle3d" / "d3_clan_castle.png")),
    clan("quote", quote="Я помню каждую монету в казне — и кто её положил.", author="Purr",
         role="журнал казны клана"),
    clan("quote", quote="Пока клан в топ-3, я подкидываю дров в огонёк.", author="Purr", role="биллборд кланов"),
    clan("collab", left="Волки", right="Котики", word="claaan waaar",
         text="Неделя противостояния: чей клан наберёт больше баллов биллборда?"),
    clan("collab", left="Глава", right="Замы", word="teaaam",
         text="Покупки, приглашения и продление — вместе, по правам из настроек клана."),
    clan("live", badge="ЭФИР", title="Создаём клан в прямом эфире", time="пятница, 19:00", where="#кланы",
         word="liiive", mood="cool"),
    clan("live", badge="ИТОГИ", title="Подводим итоги недели кланов", time="воскресенье, 21:00",
         where="#биллборд", word="resuuults", mood="stars"),
    clan("alert", word="careful!", mood="angry", ic_мегафон=D3 + "clan_sack.png",
         title="Личную голду в казну не перевести",
         text="Казна пополняется только наградами за активность — так честно для всех кланов."),
    clan("maintenance", word="looook", mood="monocle", ic_ключ=D3 + "clan_ledger.png", ic_шестерёнка=D3 + "clan_coins.png",
         title="Журнал казны — в админ-панели", window="только для админов",
         text="Найти клан, посмотреть все движения денег и поправить вручную — с записью в журнал."),
    clan("poll", question="Что купить в магазине клана первым?",
         options="Герб:46\nЦвет роли:34\nНазвание:20", hint="голосуют участники клана", word="shoooop", mood="love"),
    clan("breaking", top_word="clans", bottom_line="кланы · скоро в purr", mood="laugh",
         title="Перезапуск бота не ломает сбор клана",
         text="Таймеры и заявки живут в базе — сбор продолжится с того же места."),

    # ================= БОТ =================
    bot("news", tag="#purr", date="привет", word="puuuurr", mood="smile_lime",
        title="Знакомьтесь: Purr",
        text="Посты, кнопки, формы, роли, вебхуки и живые картинки — в одном боте и в одном стиле."),
    bot("split", tag="#purr", word="puuurr", mood="smile_lime", ic_огонь=D3 + "cat.png",
        title="Бот, который рисует сам",
        text="3D-смайлики на кнопках, анимированные баннеры экранов и живые картинки."),
    bot("news", tag="#embed", date="без кода", word="embeeeeds", mood="cool", bg="ice", seed=3, wave=ICE_WAVE,
        title="Посты без кода и JSON",
        text="Заголовок, цвет, картинки и поля — в окошках, с предпросмотром в тёмной и светлой теме."),
    bot("milestone", number="10", unit="embeeeds", text="карточек в одном сообщении — переключайся между ними"),
    bot("milestone", number="12", unit="actiooons", text="действий у одной кнопки: роль, форма, ответ, embed…"),
    bot("feature", name="Шаги-страницы", badge="new!",
        text="Кнопка «Дальше» заменяет сообщение следующим: гиды, меню, квесты.",
        how="/embed → Кнопки → Шаг", word="steeeps"),
    bot("feature", name="Разным ролям — разное", badge="pro",
        text="Одна кнопка: участнику — меню, модератору — панель.",
        how="/embed → Кнопка → Видимость по ролям", word="rooooles"),
    bot("feature", name="Родитель стиля", badge="tip",
        text="Поменял цвет у главного поста — обновились все «дети».",
        how="/embed → Стиль → Родитель", word="styyyle"),
    bot("update", version="v1.1", title="Кнопки стали умнее", word="buttooons", date="/embed",
        items="12 действий на одну кнопку\nШаги-страницы «Дальше»\nСчётчик «+1» обновляет все посты"),
    bot("update", version="v1.2", title="Расписание и автопосты", word="scheduuule", date="/embed",
        items="Один раз или с повтором\nПриветствие нового участника\nОтвет на ключевое слово"),
    bot("breaking", top_word="embed", bottom_line="embed · purr · без кода", mood="laugh",
        title="Один пост — сразу в 5 каналов",
        text="Каналы, ветки и форумы — одной отправкой."),
    bot("breaking", top_word="update", bottom_line="правка · везде · сразу", mood="cool",
        title="Поправил пост — изменились все его копии",
        text="Бот помнит, где отправлено, и обновит всё разом."),
    bot("digest", period="что умеет /embed", word="embeeeds",
        cards="Переменные|{member_count} и {online} — живые числа|" + D3 + "sparkles.png\n"
              "Расписание|раз или с повтором|" + D3 + "hourglass.png\n"
              "Версии|25 последних и откат|" + D3 + "book.png"),
    bot("news", tag="#forms", date="заявки", word="fooooorms", mood="monocle", bg="wine", seed=5,
        title="Заявки приходят сами",
        text="Участник заполнил анкету — бот сразу присылает её в канал проверки."),
    bot("split", tag="#forms", word="fooorms", mood="wink", ic_огонь=D3 + "mail.png",
        title="Одобрил — роль выдана",
        text="А заявителю в ЛС — баннер с названием должности."),
    bot("rules", title="Как работают заявки", word="fooorms", ic_щит=D3 + "mail.png",
         items="Участник заполняет анкету\nЗаявка приходит в канал проверки\nОдобрить — роль выдаётся сама\nОтклонить — с причиной"),
    bot("event", day="03", month="НОЯ", weekday="понедельник", title="Набор модераторов",
        text="Форма-заявка открыта: Purr выдаст роль сразу после одобрения.",
        time="до 10.11", place="#заявки", word="staaaff", ic_шарик=D3 + "shield.png"),
    bot("news", tag="#роли", date="меню", word="roooooles", mood="love", bg="lime", seed=14, wave=LIME_WAVE,
        title="Меню ролей до 25 штук",
        text="Участники сами выбирают роли из списка — модераторам меньше работы."),
    bot("split", tag="#кнопки", word="cliiick", mood="laugh", ic_огонь=D3 + "bolt.png",
        title="Счётчик «+1»",
        text="Нажали — число выросло во всех постах сразу."),
    bot("news", tag="#webhook", date="одна команда", word="webhoooks", mood="wink", bg="ice", seed=15, wave=ICE_WAVE,
        title="Вебхук одной командой",
        text="/webhook — канал, имя и аватарка. Секретная ссылка только у тебя."),
    bot("alert", word="nooope!", mood="angry", ic_мегафон=D3 + "lock.png",
        title="Ссылка вебхука — это секрет",
        text="Утекла? Нажми «Пересоздать URL» — старая сразу перестанет работать."),
    bot("rules", title="Вебхуки без утечек", word="saaafe", ic_щит=D3 + "lock.png",
        items="Не отправляй ссылку в чат\nУтекла — «Пересоздать URL»\nСначала «Тест», потом пост\nВсе вебхуки — в /webhook"),
    bot("news", tag="#logo", date="иконки", word="logooooos", mood="cool", bg="dark", seed=17, wave=PINK_WAVE,
        title="Иконки ролей в стиле сервера",
        text="Опиши словами или прикрепи набросок — /logo нарисует и поставит на роль."),
    bot("feature", name="Иконка из наброска", badge="wow",
        text="Прикрепи свой макет — /logo перерисует его в стиле сервера.",
        how="/logo → Из наброска", word="logooos"),
    bot("news", tag="#pictures", date="21 шаблон", word="pictuuures", mood="stars", bg="wine", seed=19,
        title="Картинки, которые живут сами",
        text="Рейтинг, прогресс, отсчёт, голосование — бот перерисует картинку в канале."),
    bot("milestone", number="21", unit="templaaates", text="шаблон живых картинок в /pictures"),
    bot("milestone", number="20", unit="styyyles", text="стилей баннера сервера с живыми цифрами"),
    bot("update", version="v1.3", title="Живые картинки", word="pictuuures", date="/pictures",
        items="21 шаблон и 7 тем\nСвоя слэш-команда для картинки\nСтекло, 3D-наклейки и глитч"),
    bot("update", version="v1.4", title="3D-оформление", word="styyyle", date="весь бот",
        items="3D-смайлики на всех кнопках\nАнимированные баннеры экранов\nНовые аватарки Purr"),
    bot("poll", question="Какой стиль картинки ваш любимый?",
        options="Стекло:41\n3D:37\nГлитч:22", hint="голосуй кнопками под постом", word="chooose", mood="think"),
    bot("poll", question="Какую функцию Purr ждёте?",
        options="Кланы:58\nМини-игры:27\nМагазин:15", hint="голосуй кнопками под постом", word="voooote",
        mood="wait"),
    bot("soon", caption="до запуска кланов в Purr", days="05", hours="12", minutes="00", word="sooooon"),
    bot("soon", caption="до обновления баннера сервера", days="00", hours="00", minutes="09", word="tiiick",
        ic_часы=D3 + "hourglass.png", ic_ракета=D3 + "sparkles.png"),
    bot("giveaway", prize="Своя иконка роли", text="/logo нарисует иконку победителю в стиле сервера.",
        until="до 05.11", winners="3 победителя", word="priiiize", mood="stars", ic_подарок=D3 + "palette.png"),
    bot("giveaway", prize="Баннер для сервера", text="Purr поставит баннер с живыми цифрами: участники, войс, бусты.",
        until="до 10.11", winners="1 сервер", word="baaanner", mood="laugh", bg="ice", seed=6, wave=ICE_WAVE),
    bot("welcome", word="hellooo", count="Purr", text="уже на сервере — набери /embed, чтобы начать"),
    bot("welcome", word="neeew bot", count="+1", text="в команде — привет от Purr!"),
    bot("maintenance", word="loooading", mood="wait",
        title="Ставим новые 3D-эмодзи", window="5 минут",
        text="Кнопки могут на пару минут показать старые иконки — это нормально."),
    bot("maintenance", word="dooone", mood="cool", ic_ключ=D3 + "wrench.png", ic_шестерёнка=D3 + "sparkles.png",
        title="Кнопки живут после перезапуска", window="всегда",
        text="Все кнопки бота постоянные — нажимай хоть через месяц."),
    bot("alert", word="looook!", mood="monocle", ic_мегафон=D3 + "eye.png",
        title="Под картинкой — кнопка «Изменить»",
        text="Меняют ты, админы и те, кого ты выберешь. Остальные просто смотрят."),
    bot("live", badge="УРОК", title="Собираем меню ролей за 5 минут", time="сегодня в 19:00", where="#стрим",
        word="leaaarn", mood="smile"),
    bot("live", badge="ДЕМО", title="Показываем живые картинки", time="суббота, 18:00", where="#стрим",
        word="deeemo", mood="laugh"),
    bot("photo", title="Так выглядит Purr", caption="аватарка Purr", word="loooook", ic_камера=D3 + "camera.png",
        text="Фирменный смайлик в 3D — на аватарке, кнопках и баннерах.",
        photo=str(ROOT / "panel_art" / "profile" / "avatar.png")),
    bot("photo", title="Иконки ролей", caption="/logo", word="logooos", ic_камера=D3 + "brush.png",
        text="Словами, по наброску или в своём стиле — и сразу на роль.",
        photo=str(ROOT / "panel_art" / "emoji" / "doodle3d" / "d3_palette.png")),
    bot("quote", quote="Я помню, где отправлен каждый пост, — поправь один раз.", author="Purr",
        role="бот сервера"),
    bot("quote", quote="Нажми кнопку — остальное я сделаю сам.", author="Purr", role="бот сервера"),
    bot("collab", left="Purr", right="Твой сервер", word="besties",
        text="Посты, роли, заявки и картинки — в одном стиле, без кода."),
    bot("winners", title="Лучшие иконки /logo месяца", first="Лиса", second="Кот", third="Ёжик", word="logooos",
        ic_кубок=D3 + "palette.png", ic_звезда=D3 + "brush.png"),
    bot("digest", period="Purr за неделю", word="weeeekly",
        cards="Кланы|скоро: казна и биллборд|" + D3 + "clan_chest.png\n"
              "Картинки|стекло, 3D и глитч|" + D3 + "palette.png\n"
              "3D|новые иконки на кнопках|" + D3 + "sparkles.png"),
]

# текст на баннере в ту же строку (для листов) — номер, тип, тема
TOPIC = {"clan": "кланы", "bot": "бот"}


def build(n):
    key, theme, data = POSTS[n - 1]
    b = rn.build(key, **data)
    doodles = CLAN_DOODLES if theme == "clan" else BOT_DOODLES
    # свой порядок дудлов у каждого баннера
    k = n % len(doodles)
    rn.scatter_doodles(b, doodles[k:] + doodles[:k], count=5, seed=n * 7 + 1)
    return b


def sheet(items, title):
    cols, tw = 4, 560
    th = tw * rn.H // rn.W
    pad, label = 24, 40
    rows = (len(items) + cols - 1) // cols
    img = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + label + pad) + pad + 50), (30, 26, 30))
    d = ImageDraw.Draw(img)
    d.text((pad, 30), title, font=rn.font(28), fill=rn.MILK, anchor="lm")
    for i, (n, im) in enumerate(items):
        key, theme, _ = POSTS[n - 1]
        x, y = pad + (i % cols) * (tw + pad), 50 + pad + (i // cols) * (th + label + pad)
        img.paste(im.resize((tw, th), Image.LANCZOS), (x, y + label))
        d.text((x, y + label / 2), f"{n}. {TOPIC[theme]} · {rn.T[key][0]}", font=rn.font(22, "SemiBold"),
               fill=rn.LIME if theme == "clan" else rn.MILK, anchor="lm")
    return img


def main():
    args = sys.argv[1:]
    figma_dir = None
    if "--figma" in args:
        i = args.index("--figma")
        figma_dir = pathlib.Path(args[i + 1]).resolve()
        del args[i:i + 2]
    nums = [int(a) for a in args] or list(range(1, len(POSTS) + 1))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    done, errors = [], []
    for n in nums:
        key = POSTS[n - 1][0]
        try:
            b = build(n)
        except ValueError as e:
            errors.append(f"{n}: {e}")
            continue
        img = rn.to_png(b.scene)
        img.save(OUT_DIR / f"purr.{n:03d}.{key}.png", optimize=True)
        done.append((n, img))
        if figma_dir:
            spec = rn.figma_spec(b.scene, figma_dir / "assets")
            (figma_dir / f"{n:03d}.json").write_text(json.dumps({"key": f"{n:03d}", "label": rn.T[key][0],
                                                                 "nodes": spec}, ensure_ascii=False))
    print(f"готово: {len(done)}, ошибок раскладки: {len(errors)}")
    for e in errors:
        print("  ", e)
    if not args and not errors:
        for s in range(0, len(done), 20):
            part = done[s:s + 20]
            sheet(part, f"Purr · баннеры {part[0][0]}–{part[-1][0]}").save(OUT_DIR / f"_sheet_{s // 20 + 1}.png",
                                                                          optimize=True)


if __name__ == "__main__":
    main()
