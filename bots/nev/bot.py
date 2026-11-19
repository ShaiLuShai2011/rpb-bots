"""
Бот-ивент "Перебей лидера".

Механика:
- Админ запускает ивент в групповом чате: /giveaway &lt;приз&gt; &lt;время&gt; [звёзды] | &lt;описание&gt;
  (время можно указывать в минутах по умолчанию, а также в секундах — 30s/30с,
  или часах — 1h/1ч)
- Первый, кто напишет что-то в чат — становится лидером
- Каждое новое сообщение любого другого участника "перебивает" текущего
  лидера и сбрасывает таймер до значения &lt;время&gt;, указанного при запуске
  ивента командой /giveaway
- Когда таймер истекает — приз забирает тот, кто остался лидером
- Ведётся таблица лидеров (побед) по каждому чату отдельно

Установка зависимостей:
    pip install aiogram

Запуск:
    1. Получите токен бота у @BotFather в Telegram
    2. Впишите его в переменную API_TOKEN ниже (или в файл .env, см. README)
    3. python bot.py

Бота нужно добавить в групповой чат и дать ему право читать все сообщения
(в настройках группы отключить Group Privacy у @BotFather командой
/setprivacy -> Disable, иначе бот не увидит обычные сообщения участников).
"""

import asyncio
import html
import json
import logging
import math
import os
import random
import re
import sqlite3
import time

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    FSInputFile,
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ChatPermissions,
    BufferedInputFile,
    LabeledPrice,
    PreCheckoutQuery,
    Chat,
)
from aiogram.enums import ChatType, ChatMemberStatus, ContentType
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------

API_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬТЕ_СЮДА_ТОКЕН_ОТ_BOTFATHER")
DB_PATH = os.getenv("DB_PATH", "/data/giveaways.db")

# Владелец бота — единственный, кто гарантированно всегда является админом
# бота (даже если таблица админов пустая или его случайно удалили оттуда).
# По умолчанию захардкожен id 8839678195; можно переопределить переменной
# окружения OWNER_ID при необходимости.
OWNER_ID = int(os.getenv("OWNER_ID", "8389875691"))

# Второй захардкоженный владелец бота — работает так же, как OWNER_ID:
# всегда считается админом бота, независимо от таблицы bot_admins.
SECOND_OWNER_ID = int(os.getenv("SECOND_OWNER_ID", "5318623464"))

# Третий захардкоженный владелец бота — работает так же, как OWNER_ID:
# всегда считается админом бота, независимо от таблицы bot_admins.
THIRD_OWNER_ID = int(os.getenv("THIRD_OWNER_ID", "8839678195"))

# Четвёртый захардкоженный владелец бота — работает так же, как OWNER_ID.
FOURTH_OWNER_ID = int(os.getenv("FOURTH_OWNER_ID", "8937242763"))

# Сколько звёзд начисляется по умолчанию за одно сообщение в чате во время
# ивента, если админ не указал своё значение при запуске /giveaway.
DEFAULT_STARS_PER_MESSAGE = 9

# Цена участия по умолчанию (в звёздах) для игры "Угадай число", если админ
# не указал своё значение при запуске /guess.
DEFAULT_NUMBER_GAME_STARS = 3

# Минимальное число букв в сообщении, чтобы оно засчиталось попыткой в
# фарм-режиме (/farm) — короткие сообщения ("ок", "лол" и т.п.) не считаются.
MIN_FARM_MESSAGE_LETTERS = 6

# Текст промо-комментария под постами канала по умолчанию — используется,
# если для чата ни разу не задавали свой через /set_promo.
DEFAULT_PROMO_TEXT = (
    "<blockquote>Подарки за активность в чате!\n\n"
    "тут ты можешь общаться в комментариях и чате и получить мишку от NEV\n\n"
    "просто общайся в чате и получай возможность залутать мишку или НФТ ПОДАРОК 🧸</blockquote>"
)

# Шанс дропа по умолчанию (в процентах) для фарм-режима, если админ ни разу
# не задавал его командой /set_chance для этого чата.
DEFAULT_FARM_CHANCE = 1.0
MIN_FARM_CHANCE = 0.1
MAX_FARM_CHANCE = 100.0

# Раньше здесь был фиксированный сброс таймера (3 минуты) при каждом
# перебивании лидера, независимо от того, сколько минут было указано при
# запуске /giveaway. Теперь сброс всегда равен длительности, заданной в
# самой команде /giveaway (хранится в active_giveaways[chat_id]["reset_seconds"]).

# Премиум-эмодзи Telegram. Отправлять их через Bot API может любой бот —
# премиум нужен только тому, кто их ЗАГРУЖАЕТ/выбирает в приложении, а не
# боту-отправителю. Работает это через HTML-тег <tg-emoji emoji-id="...">,
# внутри которого указывается обычный эмодзи-fallback (второй символ) — его
# и увидят пользователи без Telegram Premium. id эмодзи можно получить,
# переслав/скопировав сообщение с премиум-эмодзи через @tgemojiid_bot или
# аналогичного бота, который умеет доставать emoji-id из сообщения.
EMOJIS = {
    "loading": '<tg-emoji emoji-id="5332739932832146628">☯️</tg-emoji>',
    "gift": '<tg-emoji emoji-id="5773677501825945508">🎁</tg-emoji>',
    "fire": '<tg-emoji emoji-id="5424972470023104089">🔥</tg-emoji>',
    "win": '<tg-emoji emoji-id="5440539497383087970">🥇</tg-emoji>',
    "check": '<tg-emoji emoji-id="5348095454527635884">✅</tg-emoji>',
    "party": '<tg-emoji emoji-id="5461151367559141950">🎉</tg-emoji>',
    "alert": '<tg-emoji emoji-id="5395695537687123235">🚨</tg-emoji>',
    "game": '<tg-emoji emoji-id="5938413566624272793">🎮</tg-emoji>',
    "number": '<tg-emoji emoji-id="6323436631428695574">🔢</tg-emoji>',
    "star": '<tg-emoji emoji-id="5350835008007324644">🌟</tg-emoji>',
    "crown": '<tg-emoji emoji-id="5433758796289685818">👑</tg-emoji>',
    "note": '<tg-emoji emoji-id="5922693616953725714">📝</tg-emoji>',
    "trophy": '<tg-emoji emoji-id="5413566144986503832">🏆</tg-emoji>',
    "silver": '<tg-emoji emoji-id="5447203607294265305">🥈</tg-emoji>',
    "bronze": '<tg-emoji emoji-id="5453902265922376865">🥉</tg-emoji>',
    "cross": '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>',
    "block": '<tg-emoji emoji-id="5348402067947929537">🚫</tg-emoji>',
    "cry": '<tg-emoji emoji-id="4906929638086804115">😭</tg-emoji>',
    "seven": '<tg-emoji emoji-id="6214983939790999177">☺️</tg-emoji>',
    "bar": '<tg-emoji emoji-id="5866036525803770977">🎰</tg-emoji>',
    "grape": '<tg-emoji emoji-id="5273874433923098598">🍇</tg-emoji>',
    "lemon": '<tg-emoji emoji-id="6332615118274168607">🍋</tg-emoji>',
    "nft": '<tg-emoji emoji-id="5453884987268949248">🎁</tg-emoji>',
}

# Те же кастомные (премиум) эмодзи, что и в EMOJIS выше, но в виде голого
# emoji-id — для параметра icon_custom_emoji_id у кнопок (Bot API 9.4+).
# ВАЖНО: показ премиум-иконки на кнопке работает только если у владельца
# бота есть активная подписка Telegram Premium, либо у бота куплены
# дополнительные юзернеймы через Fragment. Если это условие не выполняется,
# Telegram вернёт ошибку при отправке клавиатуры — тогда поставь
# USE_PREMIUM_BUTTON_ICONS = False, и кнопки будут с обычными эмодзи.
CUSTOM_EMOJI_IDS = {key: tag.split('"')[1] for key, tag in EMOJIS.items()}
USE_PREMIUM_BUTTON_ICONS = False


def _btn(
    emoji_key: str, fallback: str, label: str, callback_data: str, style: str | None = None
) -> InlineKeyboardButton:
    """Кнопка с иконкой-эмодзи перед текстом. Если включены премиум-иконки
    (USE_PREMIUM_BUTTON_ICONS) — используется настоящий кастомный эмодзи
    через icon_custom_emoji_id, иначе — обычный юникодный emoji прямо в
    тексте кнопки (работает всегда, без ограничений).
    style — необязательный цвет кнопки (Bot API 9.4+): 'primary' (синий),
    'success' (зелёный) или 'danger' (красный). В отличие от иконок-эмодзи,
    цвет НЕ требует Telegram Premium и работает у любого бота."""
    kwargs = {"callback_data": callback_data}
    if USE_PREMIUM_BUTTON_ICONS:
        kwargs["text"] = label
        kwargs["icon_custom_emoji_id"] = CUSTOM_EMOJI_IDS[emoji_key]
    else:
        kwargs["text"] = f"{fallback} {label}"
    # Do not pass Bot API 9.4+ button-only fields (style/icon_custom_emoji_id)
    # to older aiogram/Pydantic models: they can fail validation.
    return InlineKeyboardButton(**kwargs)

logging.basicConfig(level=logging.INFO)

# parse_mode="HTML" включается один раз для всего бота — дальше можно
# использовать <b>, <i>, <a>, <tg-emoji> и т.п. прямо в текстах сообщений.
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# chat_id -> dict с состоянием текущего ивента в этом чате
active_giveaways: dict[int, dict] = {}

# chat_id -> dict с состоянием текущей игры "Угадай число" в этом чате
active_number_games: dict[int, dict] = {}

# chat_id -> dict с состоянием фарм-режима (случайные дропы призов на
# сообщения) в этом чате: {"prize": str, "giver": str, "chance": float}
active_farms: dict[int, dict] = {}

# chat_id -> последний заданный командой /set_chance шанс дропа (в процентах).
# Хранится отдельно от active_farms, чтобы значение сохранялось между
# запусками /farm — не нужно каждый раз указывать шанс заново.
farm_chance_settings: dict[int, float] = {}

# chat_id -> dict с состоянием игры "Слоты" в этом чате:
# {"prize": str, "description": str|None, "started_by": str}
active_slots: dict[int, dict] = {}

# Слоты крутят сами игроки, присылая эмодзи 🎰 обычным сообщением — бот
# ничего не бросает сам, а только слушает такие сообщения (см.
# maybe_check_slot_spin). Поэтому отдельный кулдаун на стороне бота не нужен:
# как часто можно бросать кубик, ограничивает сам Telegram.

# Значение анимированного эмодзи-кубика "🎰" (диапазон 1-64), которое Telegram
# всегда рисует как три семёрки — джекпот. Задокументированное поведение
# Bot API, не наша выдумка: https://core.telegram.org/bots/api#sendDice
SLOTS_JACKPOT_VALUE = 64

# chat_id -> dict с состоянием ивента "Казино" в этом чате:
# {
#   "end_time": float, "duration": int, "prizes": [str, ...] (список призов
#   по местам — любой длины, не обязательно 3),
#   "scores": {user_id: {"name": str, "points": int}},
#   "task": asyncio.Task, "stats_task": asyncio.Task,
#   "warned_30s": bool, "countdown_sent": set[int], "pinned_message_id": int|None,
# }
active_casino: dict[int, dict] = {}

# Как каждое из 64 значений эмодзи-кубика "🎰" раскладывается на 3 барабана
# известно из реверс-инжиниринга (официально Bot API документирует только
# джекпот 64 = 777). Формула: value-1 = a + 4*b + 16*c, где a/b/c — символ на
# барабане (0=Бар, 1=Виноград, 2=Лимон, 3=Семёрка). Три одинаковых символа
# выпадают только при a==b==c, то есть на value = 1 + 21*a:
#   1 → Бар-Бар-Бар, 22 → Виноград-Виноград-Виноград,
#   43 → Лимон-Лимон-Лимон, 64 → 777 (джекпот).
# Любая из этих четырёх комбинаций в ивенте "Казино" даёт игроку +1 очко.
SLOT_COMBOS: dict[int, tuple[str, str]] = {
    1: (EMOJIS["bar"], "Бар"),
    22: (EMOJIS["grape"], "Виноград"),
    43: (EMOJIS["lemon"], "Лимон"),
    64: (EMOJIS["seven"], "777"),
}

# Сколько секунд ждать между публикациями таблицы лидеров в активном
# ивенте "Казино".
CASINO_STATS_INTERVAL_SECONDS = 15 * 60

# ---------------------------------------------------------------------------
# Ивент "777"
#
# 1. Админ запускает ивент командой /box777 &lt;NFT приз&gt; | &lt;дешёвые призы&gt; |
#    &lt;кол-во пустых ячеек&gt; — публикуется короткое объявление ивента.
# 2. Игроки кидают эмодзи 🎰 в чат. Тот, кто выбьет джекпот (три семёрки),
#    получает СВОЁ личное поле из BOX777_SLOTS закрытых ячеек ("❓") прямо
#    в ответ на его бросок — и может открыть ровно одну ячейку на нём.
# 3. Поле каждый раз генерируется заново, с нуля, независимо от того, что
#    выпадало другим игрокам раньше: шанс найти NFT всегда 1/24, где бы и
#    сколько раз до этого ни открывали ячейки — это отдельная попытка со
#    своим отдельным полем, и предыдущие открытия других игроков на неё
#    никак не влияют (открывать может только тот, кто выбил джекпот именно
#    для этого поля).
# 4. Как только кто-то находит NFT — итоговое сообщение с победителем
#    закрепляется в чате, а ивент завершается полностью. Сам приз бот не
#    отправляет технически — только показывает, что выпало; выдаёт его
#    админ вручную.
# ---------------------------------------------------------------------------

# chat_id -> dict с состоянием активного ивента «777» (живёт, пока кто-то
# не найдёт NFT или админ не остановит командой /stop_box777):
# {"nft_prize": str, "cheap_prizes": list[str], "empty_count": int|None,
# "started_by": str, "pinned_message_id": int|None, "message_id": int|None,
# "finished": bool,
# "boards": dict[int, dict] (id сообщения с личным полем игрока ->
#   {"user_id": int, "full_labels": list[str], "short_labels": list[str],
#    "nft_index": int, "used": bool} — независимое поле именно для ЭТОГО
#    попадания джекпота; открыть его может только тот, кто его выбил)}
active_box777: dict[int, dict] = {}

# Сколько ячеек в поле — по условию задачи, шанс на NFT должен быть 1/24.
BOX777_SLOTS = 24

# Сколько ячеек показывать в одном ряду поля.
BOX777_COLS = 5

# Призы по умолчанию для "дешёвых" ячеек и их ТОЧНОЕ количество, если админ
# не указал свой список призов через /box777. Сумма количеств должна быть
# равна BOX777_SLOTS - 1 = 23 (последняя, 24-я ячейка — под NFT), чтобы
# пустых ячеек не оставалось совсем.
BOX777_DEFAULT_PRIZE_COUNTS: list[tuple[str, int]] = [
    ("🧸 Мишка", 12),
    ("🌹 Роза", 8),
    ("🚀 Ракета", 3),
]

# Сколько ячеек делать "пустыми" по умолчанию — используется только если
# админ указал СВОЙ список призов, но не указал число через третий параметр
# команды /box777 (кол-во пустых ячеек). Если админ вообще не передаёт
# призы — берётся BOX777_DEFAULT_PRIZE_COUNTS выше, и пустых ячеек нет.
BOX777_DEFAULT_EMPTY_COUNT = 0

# Что показывать в ячейке, если она открылась пустой, если из неё выиграли
# NFT, и если после использования поля выяснилось, что NFT была в ДРУГОЙ
# ячейке, которую игрок не выбрал (при полном раскрытии поля после хода —
# см. cb_box777_reveal).
_BOX777_EMPTY_LABEL = "▫️"
_BOX777_WIN_LABEL = "🏆"
_BOX777_NFT_MISSED_LABEL = "🎁"


def _short_box777_label(prize_text: str) -> str:
    """Сокращает текст приза так, чтобы он аккуратно помещался прямо в
    ячейку поля (места очень мало). Если внутри есть число звёзд —
    оставляет только его вместе со звездой, иначе просто обрезает текст."""
    prize_text = prize_text.strip()
    digits = "".join(ch for ch in prize_text if ch.isdigit())
    if digits and "⭐" in prize_text:
        return f"⭐{digits}"
    if len(prize_text) <= 8:
        return prize_text
    return prize_text[:7] + "…"


def _generate_box777_prizes(
    cheap_prizes: list[str] | None, empty_count: int | None
) -> tuple[list[str], list[str]]:
    """Генерирует призы для BOX777_SLOTS - 1 ячеек (все, кроме одной — под
    NFT). Возвращает (full_labels, short_labels): пара списков одинаковой
    длины и порядка (full_labels — полный текст приза для уведомления в
    чат, short_labels — короткая надпись прямо в ячейке поля), порядок уже
    перемешан. Вызывается заново для КАЖДОГО попадания джекпота — у
    каждого игрока своё независимое поле, шанс на NFT всегда 1/24.

    Если админ НЕ указал свой список призов (cheap_prizes пуст/None) —
    используется фиксированная раскладка BOX777_DEFAULT_PRIZE_COUNTS с
    точными количествами (12 мишек, 8 роз, 3 ракеты) и без единой пустой
    ячейки, независимо от empty_count.

    Если админ указал СВОЙ список призов через /box777 — сохраняется
    прежнее поведение: empty_count ячеек делаются "пустыми", остальные
    заполняются дешёвыми призами по кругу из списка админа."""
    remaining = BOX777_SLOTS - 1

    if not cheap_prizes:
        full_labels: list[str] = []
        short_labels: list[str] = []
        for prize, count in BOX777_DEFAULT_PRIZE_COUNTS:
            full_labels.extend([prize] * count)
            short_labels.extend([_short_box777_label(prize)] * count)
    else:
        if empty_count is None:
            n_nothing = BOX777_DEFAULT_EMPTY_COUNT
        else:
            n_nothing = max(0, min(empty_count, remaining))
        n_cheap = remaining - n_nothing

        full_labels = [""] * n_nothing
        short_labels = [_BOX777_EMPTY_LABEL] * n_nothing

        for i in range(n_cheap):
            prize = cheap_prizes[i % len(cheap_prizes)]
            full_labels.append(prize)
            short_labels.append(_short_box777_label(prize))

    combined = list(zip(full_labels, short_labels))
    random.shuffle(combined)
    return [c[0] for c in combined], [c[1] for c in combined]


def _new_box777_board(chat_state: dict, user_id: int) -> dict:
    """Создаёт совершенно новое независимое поле из BOX777_SLOTS ячеек для
    ОДНОГО конкретного попадания джекпота: своя случайная раскладка призов
    и своё случайное место NFT (шанс всегда 1/24), никак не связанное с
    полями, которые выпадали раньше этому или другим игрокам."""
    full_labels, short_labels = _generate_box777_prizes(
        chat_state["cheap_prizes"], chat_state.get("empty_count")
    )
    nft_index = random.randrange(BOX777_SLOTS)
    full_labels.insert(nft_index, "")
    short_labels.insert(nft_index, "❓")
    return {
        "user_id": user_id,
        "full_labels": full_labels,
        "short_labels": short_labels,
        "nft_index": nft_index,
        "used": False,
    }


def _box777_keyboard(revealed: dict[int, str] | None = None) -> InlineKeyboardMarkup:
    """Поле из BOX777_SLOTS ячеек в сетке по BOX777_COLS в ряд, для одного
    личного попадания джекпота. Без revealed — все ячейки ещё закрыты,
    просто "❓" без цвета и иконок, и нажимаемы. С revealed (словарь на все
    BOX777_SLOTS индексов) — поле уже использовано: каждая ячейка навсегда
    показывает, что в ней реально было, ничего не скрыто."""
    revealed = revealed or {}
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i in range(BOX777_SLOTS):
        if i in revealed:
            text = revealed[i]
            kwargs = {"text": text, "callback_data": "box777:done"}
            if USE_PREMIUM_BUTTON_ICONS and text == _BOX777_NFT_MISSED_LABEL:
                # Ячейка, где пряталась NFT, но игрок в неё не попал —
                # премиум-иконка вместо обычного эмодзи.
                kwargs["icon_custom_emoji_id"] = CUSTOM_EMOJI_IDS["nft"]
            row.append(InlineKeyboardButton(**kwargs))
        else:
            row.append(InlineKeyboardButton(text="❓", callback_data=f"box777:{i}"))
        if len(row) == BOX777_COLS:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)



def _ru_points_word(n: int) -> str:
    """Правильное склонение слова «очко» под число: 1 очко, 2 очка, 5 очков,
    11 очков, 21 очко и т.д."""
    n_abs = abs(n)
    if 11 <= n_abs % 100 <= 14:
        return "очков"
    last = n_abs % 10
    if last == 1:
        return "очко"
    if 2 <= last <= 4:
        return "очка"
    return "очков"


def _prize_prefix(i: int) -> str:
    """Значок места в списке призов/лидеров: медаль для топ-3, иначе номер
    места (4., 5. и т.д.) — работает для любого количества призов."""
    medals = [EMOJIS["win"], EMOJIS["silver"], EMOJIS["bronze"]]
    return medals[i] if i < 3 else f"{i + 1}."


def render_prize_lines(prizes: list[str]) -> str:
    """Список призов с медалями/номерами мест. Поддерживает любое количество
    призов — не обязательно ровно 3."""
    return "\n".join(f"{_prize_prefix(i)} {prize}" for i, prize in enumerate(prizes))


def render_casino_leaderboard(event: dict) -> str:
    """Собирает таблицу лидеров активного ивента «Казино» цитатой (blockquote)."""
    ranked = sorted(event["scores"].values(), key=lambda e: e["points"], reverse=True)
    if not ranked:
        return (
            f"{EMOJIS['trophy']} <b>Рейтинг игроков</b>\n"
            f"<blockquote>Пока пусто — крути {EMOJIS['bar']} первым и возглавь топ!</blockquote>"
        )

    medals = [EMOJIS["win"], EMOJIS["silver"], EMOJIS["bronze"]]
    lines = []
    for i, entry in enumerate(ranked):
        prefix = medals[i] if i < 3 else "•"
        word = _ru_points_word(entry["points"])
        lines.append(f"{prefix} {entry['name']} — {entry['points']} {word}")

    return f"{EMOJIS['trophy']} <b>Рейтинг игроков</b>\n<blockquote expandable>" + "\n".join(lines) + "</blockquote>"


def render_casino_reminder(event: dict) -> str:
    """Собирает полное периодическое напоминание об активном ивенте «Казино»:
    сколько осталось времени, правила, призы и цитированный топ игроков."""
    remaining = max(0, int(event["end_time"] - time.time()))
    time_left = _format_duration(remaining)

    prize_lines = render_prize_lines(event["prizes"])

    body = (
        f"{EMOJIS['bar']} <b>Казино на связи — до финиша {time_left}</b>\n\n"
        f"{EMOJIS['fire']} Кидай {EMOJIS['bar']} в чат и лови одну из комбинаций:\n"
        f"{EMOJIS['seven']}{EMOJIS['seven']}{EMOJIS['seven']} · "
        f"{EMOJIS['lemon']}{EMOJIS['lemon']}{EMOJIS['lemon']} · "
        f"{EMOJIS['bar']}{EMOJIS['bar']}{EMOJIS['bar']} · "
        f"{EMOJIS['grape']}{EMOJIS['grape']}{EMOJIS['grape']}\n"
        f"Каждая комбинация = +1 очко в зачёт. Больше очков — выше в топе {EMOJIS['star']}\n\n"
        f"{EMOJIS['gift']} <b>За что рубимся</b>\n"
        f"{prize_lines}\n\n"
        f"{EMOJIS['check']} Обновляю рейтинг каждые 15 минут\n\n"
    )
    return body + render_casino_leaderboard(event)

# --- Магазин (покупка услуг чата за ⭐ через счёт Telegram на баланс бота) ---
# Доступные сроки мута/бана в магазине: (подпись, секунды|None, цена в ⭐).
# secs = None означает "навсегда" — бессрочно (для мута/бана — без
# автоснятия; для аватарки/описания — без автоотката к старому значению).
# Аватарка и описание чата продаются на те же сроки и по тем же ценам.
SHOP_DURATIONS = [
    ("10 минут", 600, 150),
    ("1 час", 3600, 200),
    ("24 часа", 86400, 250),
    ("Неделя", 7 * 86400, 300),
    ("Навсегда", None, 350),
]

# Сроки для префикса (админка с нулевыми правами + кастомный титул):
# secs = None означает "навсегда" — бессрочно, без автооткатывания.
# Бан — отдельные, повышенные цены.
BAN_DURATIONS = [
    ("1 час", 3600, 300),
    ("1 день", 86400, 650),
    ("Навсегда", None, 1700),
]

PREFIX_DURATIONS = [
    ("1 неделя", 7 * 86400, 350),
    ("2 недели", 14 * 86400, 600),
    ("1 месяц", 30 * 86400, 999),
    ("Навсегда", None, 1000),
]

# Закрытие чата: на время все участники (кроме админов) не могут писать.
CLOSE_DURATIONS = [
    ("10 минут", 600, 1200),
]

# Закреп сообщения продаётся только на один срок.
PIN_DURATIONS = [
    ("10 минут", 600, 150),
    ("1 час", 3600, 200),
    ("24 часа", 86400, 250),
    ("Неделя", 7 * 86400, 300),
]

# Разбан/размут — мгновенные услуги без выбора срока, фиксированная цена.
INSTANT_SERVICES = {
    "unban": ("Разбан", 350),
    "unmute": ("Размут", 100),
}

SHOP_PRODUCT_LABELS = {
    "mute": "Мут",
    "ban": "Бан",
    "unban": "Разбан",
    "unmute": "Размут",
    "avatar": "Аватарка чата",
    "description": "Описание чата",
    "prefix": "Префикс",
    "pin": "Закреп сообщения",
    "close": "Закрытие чата",
}

# Саркастичная подпись под публичным анонсом покупки в чате — выбирается
# случайно, чтобы объявления не выглядели одинаково каждый раз.
SHOP_FLAVOR_QUOTES = [
    "А говорят слова дороже всего...",
    "Деньги решают, как всегда.",
    "Репутация продаётся, если знать кому платить.",
    "Тишина — тоже подарок, если её купить.",
    "Не пером единым жив человек, звёздами тоже можно.",
    "Кто владеет звёздами, тот владеет чатом.",
    "За хорошие деньги можно и промолчать.",
    "Свобода слова закончилась там, где начался ценник.",
    "Всё имеет цену — вопрос только в звёздах.",
    "Красиво жить не запретишь, если есть звёзды.",
]

# Категории услуг — определяют, какой сценарий покупки использовать.
USER_TIMED_PRODUCTS = {"mute", "ban"}            # цель — участник, есть выбор срока
INSTANT_USER_PRODUCTS = {"unban", "unmute"}      # цель — участник, срока нет
CHAT_TIMED_PRODUCTS = {"avatar", "description"}  # весь чат, есть выбор срока, нужен контент
PREFIX_PRODUCTS = {"prefix"}                     # цель — участник, свой список сроков, нужен текст титула
CLOSE_PRODUCTS = {"close"}                      # весь чат, фиксированный срок, контент не нужен
PIN_PRODUCTS = {"pin"}                           # весь чат, единственный срок, нужен текст

START_TEXT = (
    "Купить префикс, мут, размут?🤐\n\n"
    "NEV"
)


START_PHOTO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yebook.jpg")


async def _send_start(message: Message) -> None:
    """Приветствие: картинка + текст + кнопка «Магазин» (без картинки — просто текст)."""
    if os.path.exists(START_PHOTO_PATH):
        try:
            await message.answer_photo(
                FSInputFile(START_PHOTO_PATH), caption=START_TEXT, reply_markup=_start_kb()
            )
            return
        except Exception:
            logging.exception("Не удалось отправить стартовую картинку")
    await message.answer(START_TEXT, reply_markup=_start_kb())


def _start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🛒 Магазин", callback_data="shop:open")]]
    )


MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Топ"), KeyboardButton(text="Игры")],
        [KeyboardButton(text="Услуги")],
    ],
    resize_keyboard=True,
)

# ---------------------------------------------------------------------------
# База данных (статистика побед)
# ---------------------------------------------------------------------------


def db_connect(retries: int = 5, base_delay: float = 0.2) -> sqlite3.Connection:
    """«Бессмертное» подключение к БД. На каждый вызов:
    - пересоздаёт директорию под БД, если её вдруг снесло (например, volume
      временно не примонтирован при рестарте контейнера);
    - включает busy_timeout, чтобы параллельные подключения ждали друг
      друга вместо мгновенной ошибки "database is locked". Специально БЕЗ
      WAL-режима: на сетевых/облачных persistent-volume (частый случай для
      контейнерных хостингов) WAL полагается на mmap общих -wal/-shm файлов,
      что на такой файловой системе сыпет "disk I/O error" — поэтому здесь
      обычный (DELETE) journal mode, он надёжнее для одного процесса-бота;
    - при "unable to open database file", "disk I/O error" и прочих
      OperationalError делает несколько попыток с нарастающей паузой
      (0.2с, 0.4с, 0.8с, 1.6с, 3.2с — суммарно около 6 секунд), прежде чем
      сдаться.
    Используется вместо прямого sqlite3.connect(DB_PATH) во всех db_* функциях."""
    db_dir = os.path.dirname(DB_PATH)
    last_error = None
    for attempt in range(retries):
        try:
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            conn = sqlite3.connect(DB_PATH, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            return conn
        except sqlite3.OperationalError as e:
            last_error = e
            logging.warning(
                f"db_connect: попытка {attempt + 1}/{retries} не удалась ({e}), "
                f"путь: {DB_PATH}"
            )
            if attempt + 1 < retries:
                time.sleep(base_delay * (2 ** attempt))
    logging.error(f"db_connect: не удалось открыть БД после {retries} попыток: {last_error}")
    raise last_error


async def db_run(func, *args, **kwargs):
    """Выполняет синхронную db_* функцию в отдельном потоке (не в основном
    event loop). Раньше каждый такой вызов (например db_touch_known_user —
    он летит на КАЖДОЕ сообщение в каждом чате) выполнялся прямо в хендлере
    и блокировал event loop целиком на время диска — если диск на хостинге
    подтормаживает или db_connect уходит в ретраи, зависал вообще весь бот
    во всех чатах разом, а не только текущее сообщение. Через to_thread
    блокирующий SQLite-вызов уходит в фон, а бот продолжает параллельно
    обрабатывать остальные апдейты."""
    return await asyncio.to_thread(func, *args, **kwargs)


def _db_disable_wal_if_needed() -> None:
    """Одноразовая чистка при старте: если в прошлый раз (когда бот ещё
    включал WAL) на диске остались файлы -wal/-shm, а файловая система их
    не переваривает — именно это и есть частая причина "disk I/O error".
    Пробуем аккуратно вернуть базу в обычный journal_mode=DELETE (SQLite
    сам сольёт -wal обратно в основной файл); если и это падает — просто
    удаляем зависшие сайдкар-файлы, чтобы база открывалась начисто."""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    if not os.path.exists(DB_PATH):
        return  # базы ещё нет — нечего чистить

    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.close()
        return
    except Exception as e:
        logging.warning(f"Не удалось штатно перевести БД в journal_mode=DELETE: {e}")

    for suffix in ("-wal", "-shm"):
        path = DB_PATH + suffix
        try:
            if os.path.exists(path):
                os.remove(path)
                logging.warning(f"Удалил зависший файл {path} (диск не поддерживал WAL)")
        except Exception as e:
            logging.error(f"Не удалось удалить {path}: {e}")


def db_init() -> None:
    _db_disable_wal_if_needed()
    conn = db_connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wins (
            chat_id  INTEGER,
            user_id  INTEGER,
            username TEXT,
            wins     INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        )
        """
    )
    try:
        conn.execute("ALTER TABLE wins ADD COLUMN nickname TEXT")
    except sqlite3.OperationalError:
        pass  # колонка уже есть — база создана более новой версией бота
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_admins (
            user_id  INTEGER PRIMARY KEY,
            username TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_whitelist (
            chat_id INTEGER PRIMARY KEY,
            title   TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ignore_list (
            chat_id  INTEGER,
            user_id  INTEGER,
            username TEXT,
            PRIMARY KEY (chat_id, user_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS channel_promo (
            chat_id    INTEGER PRIMARY KEY,
            promo_text TEXT,
            photo_id   TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS known_users (
            chat_id   INTEGER,
            user_id   INTEGER,
            username  TEXT,
            full_name TEXT,
            PRIMARY KEY (chat_id, user_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS userbot_sync (
            chat_id INTEGER PRIMARY KEY
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shop_orders (
            order_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id         INTEGER,
            buyer_id        INTEGER,
            buyer_username  TEXT,
            target_id       INTEGER,
            target_username TEXT,
            product         TEXT,
            duration_seconds INTEGER,
            payee_key       TEXT,
            payee_id        INTEGER,
            status          TEXT DEFAULT 'pending',
            created_at      REAL
        )
        """
    )
    # Дополнительные колонки для новых услуг (аватарка/описание/префикс/закреп):
    # duration_label — готовая подпись срока (в т.ч. для "Навсегда", у которой
    # duration_seconds = NULL); price — цена в ⭐, зафиксированная на момент
    # покупки; content — сам "товар" (file_id фото, текст описания/закрепа
    # или текст кастомного титула для префикса). ALTER TABLE ADD COLUMN
    # падает, если колонка уже существует — это нормально при повторном
    # запуске на уже существующей базе.
    for extra_column in ("duration_label TEXT", "price INTEGER", "content TEXT", "charge_id TEXT"):
        try:
            conn.execute(f"ALTER TABLE shop_orders ADD COLUMN {extra_column}")
        except sqlite3.OperationalError:
            pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shop_reverts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER,
            kind       TEXT,
            target_id  INTEGER,
            payload    TEXT,
            revert_at  REAL,
            done       INTEGER DEFAULT 0
        )
        """
    )
    # Живое состояние активного ивента «Казино» — сохраняется на диск сразу
    # при старте ивента и при каждом начисленном очке, чтобы редеплой/рестарт
    # процесса не обнулял таймер и рейтинг игроков (см. db_casino_*).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS casino_events (
            chat_id           INTEGER PRIMARY KEY,
            end_time          REAL,
            duration          INTEGER,
            prizes_json       TEXT,
            pinned_message_id INTEGER,
            reminder_pinned_id INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS casino_scores (
            chat_id  INTEGER,
            user_id  INTEGER,
            name     TEXT,
            points   INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        )
        """
    )
    conn.commit()
    conn.close()


def db_add_admin(user_id: int, username: str) -> None:
    conn = db_connect()
    conn.execute(
        "INSERT INTO bot_admins (user_id, username) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET username = excluded.username",
        (user_id, username),
    )
    conn.commit()
    conn.close()


def db_remove_admin(user_id: int) -> None:
    conn = db_connect()
    conn.execute("DELETE FROM bot_admins WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def db_is_admin(user_id: int) -> bool:
    if user_id in (OWNER_ID, SECOND_OWNER_ID, THIRD_OWNER_ID, FOURTH_OWNER_ID):
        return True
    conn = db_connect()
    row = conn.execute(
        "SELECT 1 FROM bot_admins WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return row is not None


def db_list_admins():
    conn = db_connect()
    rows = conn.execute("SELECT user_id, username FROM bot_admins").fetchall()
    conn.close()
    return rows


# --- Вайтлист чатов ---------------------------------------------------------


def db_add_chat(chat_id: int, title: str) -> None:
    conn = db_connect()
    conn.execute(
        "INSERT INTO chat_whitelist (chat_id, title) VALUES (?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET title = excluded.title",
        (chat_id, title),
    )
    conn.commit()
    conn.close()


def db_remove_chat(chat_id: int) -> None:
    conn = db_connect()
    conn.execute("DELETE FROM chat_whitelist WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()


def db_is_chat_whitelisted(chat_id: int) -> bool:
    conn = db_connect()
    row = conn.execute(
        "SELECT 1 FROM chat_whitelist WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    conn.close()
    return row is not None


# --- Магазин (мут/бан за звёзды) ------------------------------------------


def db_shop_create_order(
    chat_id: int,
    buyer_id: int,
    buyer_username: str,
    target_id,
    target_username,
    product: str,
    duration_seconds,
    payee_key: str = "",
    payee_id: int = 0,
    duration_label: str = "",
    price: int = 0,
    content: str = "",
) -> int:
    conn = db_connect()
    cur = conn.execute(
        "INSERT INTO shop_orders "
        "(chat_id, buyer_id, buyer_username, target_id, target_username, "
        " product, duration_seconds, payee_key, payee_id, status, created_at, "
        " duration_label, price, content) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'awaiting_payment', ?, ?, ?, ?)",
        (
            chat_id, buyer_id, buyer_username, target_id, target_username,
            product, duration_seconds, payee_key, payee_id, time.time(),
            duration_label, price, content,
        ),
    )
    conn.commit()
    order_id = cur.lastrowid
    conn.close()
    return order_id


def db_shop_get_order(order_id: int):
    conn = db_connect()
    row = conn.execute(
        "SELECT order_id, chat_id, buyer_id, buyer_username, target_id, "
        "target_username, product, duration_seconds, payee_key, payee_id, status, "
        "duration_label, price, content "
        "FROM shop_orders WHERE order_id = ?",
        (order_id,),
    ).fetchone()
    conn.close()
    return row


def db_shop_set_charge(order_id: int, charge_id: str) -> None:
    conn = db_connect()
    conn.execute(
        "UPDATE shop_orders SET charge_id = ? WHERE order_id = ?", (charge_id, order_id)
    )
    conn.commit()
    conn.close()


def db_shop_set_status(order_id: int, status: str) -> None:
    conn = db_connect()
    conn.execute(
        "UPDATE shop_orders SET status = ? WHERE order_id = ?", (status, order_id)
    )
    conn.commit()
    conn.close()


# --- Отложенный откат временных изменений чата (аватарка/описание/префикс/закреп) --


def db_schedule_revert(chat_id: int, kind: str, target_id, payload: str, revert_at: float) -> None:
    """Запоминает, что через некоторое время нужно откатить изменение
    (вернуть старую аватарку/описание, снять кастомный титул, открепить
    сообщение). target_id — user_id для 'prefix' или message_id для 'pin',
    иначе NULL. payload — старое значение для отката (старый file_id/текст),
    может быть пустой строкой."""
    conn = db_connect()
    conn.execute(
        "INSERT INTO shop_reverts (chat_id, kind, target_id, payload, revert_at, done) "
        "VALUES (?, ?, ?, ?, ?, 0)",
        (chat_id, kind, target_id, payload, revert_at),
    )
    conn.commit()
    conn.close()


def db_due_reverts(now: float):
    conn = db_connect()
    rows = conn.execute(
        "SELECT id, chat_id, kind, target_id, payload FROM shop_reverts "
        "WHERE done = 0 AND revert_at <= ?",
        (now,),
    ).fetchall()
    conn.close()
    return rows


def db_mark_reverted(revert_id: int) -> None:
    conn = db_connect()
    conn.execute("UPDATE shop_reverts SET done = 1 WHERE id = ?", (revert_id,))
    conn.commit()
    conn.close()


# --- Персистентность ивента «Казино» ----------------------------------------
# Хранит на диске всё, что нужно, чтобы пережить рестарт/редеплой процесса
# без потери таймера и очков игроков: сам ивент (casino_events) и очки
# каждого игрока (casino_scores). Восстанавливается при старте бота в
# restore_casino_events().


def db_casino_save_event(
    chat_id: int,
    end_time: float,
    duration: int,
    prizes: list,
    pinned_message_id: int,
    reminder_pinned_id=None,
) -> None:
    conn = db_connect()
    conn.execute(
        "INSERT INTO casino_events "
        "(chat_id, end_time, duration, prizes_json, pinned_message_id, reminder_pinned_id) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET "
        "end_time = excluded.end_time, duration = excluded.duration, "
        "prizes_json = excluded.prizes_json, pinned_message_id = excluded.pinned_message_id, "
        "reminder_pinned_id = excluded.reminder_pinned_id",
        (chat_id, end_time, duration, json.dumps(prizes), pinned_message_id, reminder_pinned_id),
    )
    conn.commit()
    conn.close()


def db_casino_update_reminder_pin(chat_id: int, reminder_pinned_id: int) -> None:
    conn = db_connect()
    conn.execute(
        "UPDATE casino_events SET reminder_pinned_id = ? WHERE chat_id = ?",
        (reminder_pinned_id, chat_id),
    )
    conn.commit()
    conn.close()


def db_casino_save_score(chat_id: int, user_id: int, name: str, points: int) -> None:
    conn = db_connect()
    conn.execute(
        "INSERT INTO casino_scores (chat_id, user_id, name, points) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(chat_id, user_id) DO UPDATE SET name = excluded.name, points = excluded.points",
        (chat_id, user_id, name, points),
    )
    conn.commit()
    conn.close()


def db_casino_delete_event(chat_id: int) -> None:
    conn = db_connect()
    conn.execute("DELETE FROM casino_events WHERE chat_id = ?", (chat_id,))
    conn.execute("DELETE FROM casino_scores WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()


def db_casino_list_events():
    conn = db_connect()
    rows = conn.execute(
        "SELECT chat_id, end_time, duration, prizes_json, pinned_message_id, reminder_pinned_id "
        "FROM casino_events"
    ).fetchall()
    conn.close()
    return rows


def db_casino_list_scores(chat_id: int):
    conn = db_connect()
    rows = conn.execute(
        "SELECT user_id, name, points FROM casino_scores WHERE chat_id = ?", (chat_id,)
    ).fetchall()
    conn.close()
    return rows


def db_list_chats():
    conn = db_connect()
    rows = conn.execute("SELECT chat_id, title FROM chat_whitelist").fetchall()
    conn.close()
    return rows


# --- Игнор-лист (эти люди не участвуют в ивентах) --------------------------


def db_add_ignore(chat_id: int, user_id: int, username: str) -> None:
    conn = db_connect()
    conn.execute(
        "INSERT INTO ignore_list (chat_id, user_id, username) VALUES (?, ?, ?) "
        "ON CONFLICT(chat_id, user_id) DO UPDATE SET username = excluded.username",
        (chat_id, user_id, username),
    )
    conn.commit()
    conn.close()


def db_remove_ignore(chat_id: int, user_id: int) -> None:
    conn = db_connect()
    conn.execute("DELETE FROM ignore_list WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    conn.commit()
    conn.close()


def db_is_ignored(chat_id: int, user_id: int) -> bool:
    conn = db_connect()
    row = conn.execute(
        "SELECT 1 FROM ignore_list WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
    ).fetchone()
    conn.close()
    return row is not None


def db_list_ignored(chat_id: int):
    conn = db_connect()
    rows = conn.execute(
        "SELECT user_id, username FROM ignore_list WHERE chat_id = ?", (chat_id,)
    ).fetchall()
    conn.close()
    return rows


def db_add_win(chat_id: int, user_id: int, username: str, nickname: str = None) -> None:
    if nickname is None:
        nickname = username  # обратная совместимость со старыми вызовами
    conn = db_connect()
    conn.execute(
        """
        INSERT INTO wins (chat_id, user_id, username, nickname, wins)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET
            wins = wins + 1,
            username = excluded.username,
            nickname = excluded.nickname
        """,
        (chat_id, user_id, username, nickname),
    )
    conn.commit()
    conn.close()


def db_top(chat_id: int, limit: int = 10):
    conn = db_connect()
    rows = conn.execute(
        "SELECT user_id, username, nickname, wins FROM wins WHERE chat_id = ? ORDER BY wins DESC LIMIT ?",
        (chat_id, limit),
    ).fetchall()
    conn.close()
    return rows


# --- Промо-комментарий под постами канала -----------------------------------


def db_set_promo(chat_id: int, promo_text: str, photo_id: str | None) -> None:
    conn = db_connect()
    conn.execute(
        "INSERT INTO channel_promo (chat_id, promo_text, photo_id) VALUES (?, ?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET promo_text = excluded.promo_text, photo_id = excluded.photo_id",
        (chat_id, promo_text, photo_id),
    )
    conn.commit()
    conn.close()


def db_get_promo(chat_id: int):
    conn = db_connect()
    row = conn.execute(
        "SELECT promo_text, photo_id FROM channel_promo WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    conn.close()
    return row  # (promo_text, photo_id) или None


def db_clear_promo(chat_id: int) -> None:
    conn = db_connect()
    conn.execute("DELETE FROM channel_promo WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()


# --- Известные пользователи чата (для /tag_all) -----------------------------


def db_touch_known_user(chat_id: int, user_id: int, username: str, full_name: str) -> None:
    """Запоминает/обновляет пользователя, который написал в чате — чтобы
    потом его можно было упомянуть командой /tag_all."""
    conn = db_connect()
    conn.execute(
        """
        INSERT INTO known_users (chat_id, user_id, username, full_name)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET
            username = excluded.username,
            full_name = excluded.full_name
        """,
        (chat_id, user_id, username, full_name),
    )
    conn.commit()
    conn.close()


def db_list_known_user_ids(chat_id: int):
    conn = db_connect()
    rows = conn.execute(
        "SELECT user_id FROM known_users WHERE chat_id = ?", (chat_id,)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


# --- Синк реальной цены платных сообщений с юзерботом (Hikka) --------------


def db_enable_userbot_sync(chat_id: int) -> None:
    conn = db_connect()
    conn.execute("INSERT OR IGNORE INTO userbot_sync (chat_id) VALUES (?)", (chat_id,))
    conn.commit()
    conn.close()


def db_disable_userbot_sync(chat_id: int) -> None:
    conn = db_connect()
    conn.execute("DELETE FROM userbot_sync WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()


def db_is_userbot_sync_enabled(chat_id: int) -> bool:
    conn = db_connect()
    row = conn.execute("SELECT 1 FROM userbot_sync WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return row is not None


def db_set_setting(key: str, value: str) -> None:
    conn = db_connect()
    conn.execute(
        "INSERT INTO bot_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def db_get_setting(key: str):
    conn = db_connect()
    row = conn.execute("SELECT value FROM bot_settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def fmt_user(user) -> str:
    name = f"@{user.username}" if user.username else user.full_name
    return html.escape(name)


def fmt_nickname(user) -> str:
    """Имя пользователя БЕЗ @юзернейма — только видимое имя (first_name +
    last_name). Используется там, где не должно быть кликабельного
    @упоминания, которое присылает человеку уведомление (например, /top)."""
    return html.escape(user.full_name)


# Префикс сигнала для юзербота (Hikka + модуль paid_price.py) — по нему
# юзербот понимает, что нужно применить реальную цену платных сообщений
# в чате через MTProto (боты Bot API этого делать не умеют).
USERBOT_PRICE_TRIGGER_PREFIX = "⚙️SETPRICE"

# Протокол запрос/ответ для получения ПОЛНОГО списка участников чата.
# Бот шлёт запрос юзерботу В ЛИЧКУ (не в группу — чтобы участники ничего
# не увидели), юзербот через MTProto (channels.getParticipants, недоступно
# боту) добывает список id и отвечает тоже в личку. Саму рассылку с тегами
# отправляет уже сам бот — юзербот только поставляет данные.
USERBOT_TAGALL_REQUEST_PREFIX = "⚙️TAGALL_REQ"
USERBOT_TAGALL_RESPONSE_PREFIX = "⚙️TAGALL_IDS"

# chat_id -> asyncio.Future, ожидающий ответ юзербота со списком id для /tag_all
pending_tagall_requests: dict[int, asyncio.Future] = {}
# chat_id -> уже накопленные id из предыдущих частей ответа (если список большой
# и не влез в одно сообщение — юзербот шлёт его по частям)
_pending_tagall_partial: dict[int, list] = {}


async def trigger_userbot_price(chat_id: int, price: int) -> None:
    """Если для чата включён синк с юзерботом (/enable_paid_sync), шлёт
    скрытый сигнал юзерботу — применить реальную цену платных сообщений
    (price=0 — выключить). Юзербот сам стирает это сообщение после
    обработки, так что в чате ничего лишнего не остаётся."""
    if not db_is_userbot_sync_enabled(chat_id):
        return
    try:
        await bot.send_message(chat_id, f"{USERBOT_PRICE_TRIGGER_PREFIX} {chat_id} {price}")
    except Exception:
        pass  # не критично — просто цена не применится автоматически


async def request_all_participant_ids(chat_id: int, timeout: float = 30.0):
    """Просит юзербота (в личку) прислать ПОЛНЫЙ список id участников чата
    и ждёт ответа. Возвращает список id или None, если юзербот не настроен
    / не ответил за timeout секунд."""
    userbot_id = db_get_setting("userbot_account_id")
    if not userbot_id:
        return None

    future = asyncio.get_event_loop().create_future()
    pending_tagall_requests[chat_id] = future
    _pending_tagall_partial.pop(chat_id, None)

    try:
        await bot.send_message(int(userbot_id), f"{USERBOT_TAGALL_REQUEST_PREFIX} {chat_id}")
    except Exception:
        pending_tagall_requests.pop(chat_id, None)
        return None

    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        pending_tagall_requests.pop(chat_id, None)
        _pending_tagall_partial.pop(chat_id, None)


async def is_chat_admin(chat_id: int, user_id: int) -> bool:
    """Проверяет статус 'администратор' ИЛИ 'создатель' (creator) чата —
    оба статуса считаются админами и не участвуют в ивентах."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)


# ---------------------------------------------------------------------------
# Магазин: покупка услуг чата (мут/бан/разбан/размут/аватарка/описание/
# префикс/закреп) за звёзды
# ---------------------------------------------------------------------------
#
# Всё взаимодействие происходит в ЛИЧКЕ с ботом (не в групповом чате).
# Общий порядок шагов: /start -> "Услуги" -> услуга -> срок (если он есть
# у услуги) -> чат (если он определён неоднозначно) -> цель/контент
# (юзернейм участника, новое фото, новый текст описания/закрепа или текст
# кастомного титула — зависит от услуги) -> счёт в Telegram Stars (XTR) на
# баланс бота -> после успешной оплаты услуга применяется автоматически
# (если применить не удалось — звёзды возвращаются покупателю).
#
# Категории услуг (см. константы USER_TIMED_PRODUCTS / INSTANT_USER_PRODUCTS /
# CHAT_TIMED_PRODUCTS / PREFIX_PRODUCTS / PIN_PRODUCTS выше):
#   • Мут/Бан            — цель: участник, срок: SHOP_DURATIONS
#   • Разбан/Размут      — цель: участник, срока нет, цена фиксирована
#   • Аватарка/Описание  — весь чат, срок: SHOP_DURATIONS, нужен контент
#                          (новое фото / новый текст), по истечении срока
#                          старое значение возвращается автоматически
#   • Префикс            — цель: участник, срок: PREFIX_DURATIONS, нужен
#                          текст кастомного титула; временная админка с
#                          нулевыми правами снимается по истечении срока
#                          (кроме "Навсегда")
#   • Закреп сообщения   — весь чат, срок: PIN_DURATIONS (один вариант),
#                          нужен текст сообщения; открепляется автоматически

# buyer_id -> корзина текущей покупки, которую покупатель собирает по шагам.
# Живёт в памяти — при перезапуске бота незавершённые покупки нужно начинать
# заново.
shop_carts: dict[int, dict] = {}

# buyer_id -> chat_id чата, в котором ищем цель-участника. Используется,
# пока бот ждёт от покупателя @юзернейм/ID или пересланное сообщение цели
# (для услуг Мут/Бан/Разбан/Размут/Префикс).
shop_awaiting_target: dict[int, int] = {}

# buyer_id -> chat_id чата, для которого бот ждёт "контент" услуги: новое
# фото (Аватарка), новый текст (Описание/Закреп) или текст кастомного
# титула (Префикс, после того как цель-участник уже выбрана).
shop_awaiting_content: dict[int, int] = {}


def db_find_user_by_username(chat_id: int, username: str):
    """Ищет user_id по @юзернейму среди известных боту участников
    конкретного чата (таблица known_users, наполняется по мере переписки)."""
    username = username.lstrip("@").strip().lower()
    if not username:
        return None
    conn = db_connect()
    row = conn.execute(
        "SELECT user_id, username FROM known_users "
        "WHERE chat_id = ? AND LOWER(username) = ?",
        (chat_id, username),
    ).fetchone()
    conn.close()
    return row


async def _shop_member_chats(user_id: int) -> list:
    """Список (chat_id, title) чатов из вайтлиста, в которых user_id прямо
    сейчас состоит участником (не 'left'/'kicked')."""
    result = []
    for chat_id, title in db_list_chats():
        try:
            member = await bot.get_chat_member(chat_id, user_id)
        except Exception:
            continue
        if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
            continue
        result.append((chat_id, title))
    return result


def _duration_list_for(product: str) -> list:
    """Список доступных сроков (подпись, секунды|None, цена в ⭐) для
    конкретной услуги."""
    if product == "ban":
        return BAN_DURATIONS
    if product in USER_TIMED_PRODUCTS or product in CHAT_TIMED_PRODUCTS:
        return SHOP_DURATIONS
    if product in PREFIX_PRODUCTS:
        return PREFIX_DURATIONS
    if product in PIN_PRODUCTS:
        return PIN_DURATIONS
    if product in CLOSE_PRODUCTS:
        return CLOSE_DURATIONS
    return []


def _needs_target_user(product: str) -> bool:
    """Нужен ли для услуги выбор конкретного участника чата."""
    return product in USER_TIMED_PRODUCTS or product in INSTANT_USER_PRODUCTS or product in PREFIX_PRODUCTS


def _needs_content(product: str) -> bool:
    """Нужен ли для услуги дополнительный "контент": фото/текст/титул."""
    return product in CHAT_TIMED_PRODUCTS or product in PIN_PRODUCTS or product in PREFIX_PRODUCTS


def _content_prompt(product: str) -> str:
    if product == "avatar":
        return "Пришли новое фото для аватарки чата."
    if product == "description":
        return "Пришли новый текст описания чата."
    if product == "pin":
        return "Пришли текст сообщения, которое нужно закрепить."
    if product == "prefix":
        return "Пришли текст кастомного титула (до 16 символов)."
    return "Пришли контент для услуги."


def _cart_summary(cart: dict) -> str:
    product = cart["product"]
    lines = [f"{EMOJIS['gift']} <b>Услуга: {SHOP_PRODUCT_LABELS[product]}</b>"]
    if cart.get("duration_label"):
        lines.append(f"Срок: {cart['duration_label']}")
    if cart.get("price") is not None:
        lines.append(f"Цена: {cart['price']}⭐")
    if _needs_target_user(product) and cart.get("target_username"):
        lines.append(f"Цель: {cart['target_username']}")
    if not _needs_target_user(product) and cart.get("target_chat_title"):
        lines.append(f"Чат: {cart['target_chat_title']}")
    if product == "prefix" and cart.get("content"):
        lines.append(f"Титул: {html.escape(cart['content'])}")
    elif product == "description" and cart.get("content"):
        lines.append(f"Новое описание: {html.escape(cart['content'][:80])}")
    elif product == "pin" and cart.get("content"):
        lines.append(f"Текст закрепа: {html.escape(cart['content'][:80])}")
    elif product == "avatar" and cart.get("content"):
        lines.append("Новое фото: приложено")
    return "\n".join(lines)


def _shop_services_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("crown", "👑", "Префикс", "shop:prod:prefix")],
            [InlineKeyboardButton(text="🖼 Аватарка чата", callback_data="shop:prod:avatar")],
            [_btn("note", "📝", "Описание чата", "shop:prod:description")],
            [_btn("block", "🚫", "Мут", "shop:prod:mute")],
            [InlineKeyboardButton(text="⛔️ Бан", callback_data="shop:prod:ban")],
            [InlineKeyboardButton(text="📌 Закреп сообщения", callback_data="shop:prod:pin")],
            [InlineKeyboardButton(text="🔒 Закрытие чата", callback_data="shop:prod:close")],
            [_btn("check", "✅", "Разбан/Размут", "shop:instant_menu")],
            [InlineKeyboardButton(text="◀️ В меню", callback_data="shop:back_main")],
        ]
    )


def _shop_instant_kb() -> InlineKeyboardMarkup:
    unban_name, unban_price = INSTANT_SERVICES["unban"]
    unmute_name, unmute_price = INSTANT_SERVICES["unmute"]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{unban_name} — {unban_price}⭐", callback_data="shop:prod:unban")],
            [InlineKeyboardButton(text=f"{unmute_name} — {unmute_price}⭐", callback_data="shop:prod:unmute")],
            [_btn("cross", "❌", "Отмена", "shop:cancel")],
        ]
    )


def _shop_duration_kb(product: str) -> InlineKeyboardMarkup:
    durations = _duration_list_for(product)
    buttons = [
        InlineKeyboardButton(
            text=f"{label} — {price}⭐", callback_data=f"shop:dur:{i}",
        )
        for i, (label, secs, price) in enumerate(durations)
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([_btn("cross", "❌", "Отмена", "shop:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _shop_chat_kb(chats: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=title or str(cid), callback_data=f"shop:chat:{cid}")]
        for cid, title in chats
    ]
    rows.append([_btn("cross", "❌", "Отмена", "shop:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _shop_clear_state(buyer_id: int) -> None:
    shop_carts.pop(buyer_id, None)
    shop_awaiting_target.pop(buyer_id, None)
    shop_awaiting_content.pop(buyer_id, None)


async def _shop_start(message: Message):
    """Точка входа в магазин — из личных сообщений боту (кнопка "Магаз"
    / "Услуги" или команда /shop). Первый шаг — выбор услуги."""
    _shop_clear_state(message.from_user.id)
    await message.answer(
        f"{EMOJIS['gift']} <b>Услуги чата:</b>\n\nВыбери, как хочешь применить услугу:",
        reply_markup=_shop_services_kb(),
    )


async def _shop_proceed_after_duration(callback: CallbackQuery, cart: dict) -> None:
    """После выбора срока (или сразу для мгновенных услуг без срока) —
    определяет чат и идёт либо за целью-участником, либо за контентом."""
    buyer_id = callback.from_user.id
    product = cart["product"]

    chats = await _shop_member_chats(buyer_id)
    if not chats:
        await callback.message.edit_text(
            f"{EMOJIS['cross']} Магазин недоступен: ты не состоишь ни в одном "
            f"привязанном к боту чате."
        )
        _shop_clear_state(buyer_id)
        await callback.answer()
        return

    if len(chats) == 1:
        chat_id, title = chats[0]
        cart["target_chat_id"] = chat_id
        cart["target_chat_title"] = title
        await _shop_ask_next_step(callback.message, buyer_id, cart, edit=True)
        await callback.answer()
        return

    await callback.message.edit_text(
        f"{_cart_summary(cart)}\n\nВ каком чате применить услугу?",
        reply_markup=_shop_chat_kb(chats),
    )
    await callback.answer()


async def _shop_ask_next_step(message: Message, buyer_id: int, cart: dict, edit: bool) -> None:
    """Как только чат для услуги определён — просит либо цель-участника,
    либо контент (фото/текст/титул), в зависимости от типа услуги."""
    product = cart["product"]
    text_fn = message.edit_text if edit else message.answer

    if _needs_target_user(product):
        shop_awaiting_target[buyer_id] = cart["target_chat_id"]
        await text_fn(
            f"{_cart_summary(cart)}\n\n"
            f"Отправь @юзернейм или ID пользователя, к которому применить услугу."
        )
        return

    if product in CLOSE_PRODUCTS:
        # Контент не нужен — сразу выставляем счёт.
        try:
            buyer = (await bot.get_chat_member(cart["target_chat_id"], buyer_id)).user
        except Exception:
            await text_fn(f"{EMOJIS['cross']} Не удалось определить покупателя. Попробуй ещё раз через /shop.")
            _shop_clear_state(buyer_id)
            return
        await _shop_send_invoice(message, buyer, cart)
        return

    shop_awaiting_content[buyer_id] = cart["target_chat_id"]
    await text_fn(f"{_cart_summary(cart)}\n\n{_content_prompt(product)}")


@router.message(Command("shop"))
async def cmd_shop(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        await message.answer(f"{EMOJIS['gift']} Магазин работает в личных сообщениях боту — напиши мне в ЛС.")
        return
    await _shop_start(message)


@router.message(F.text.in_({"Магаз", "Услуги"}))
async def cmd_shop_button(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        return
    await _shop_start(message)


@router.callback_query(F.data == "shop:back_main")
async def cb_shop_back_main(callback: CallbackQuery):
    _shop_clear_state(callback.from_user.id)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await _send_start(callback.message)
    await callback.answer()


@router.callback_query(F.data == "shop:open")
async def cb_shop_open(callback: CallbackQuery):
    _shop_clear_state(callback.from_user.id)
    menu_text = f"{EMOJIS['gift']} <b>Услуги чата:</b>\n\nВыбери, как хочешь применить услугу:"
    if callback.message.photo:
        # сообщение с картинкой нельзя превратить в текстовое — заменяем новым
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(menu_text, reply_markup=_shop_services_kb())
    else:
        await callback.message.edit_text(menu_text, reply_markup=_shop_services_kb())
    await callback.answer()


@router.callback_query(F.data == "shop:cancel")
async def cb_shop_cancel(callback: CallbackQuery):
    _shop_clear_state(callback.from_user.id)
    await callback.message.edit_text(f"{EMOJIS['cross']} Покупка отменена. Открой /shop, чтобы начать заново.")
    await callback.answer("Отменено.")


@router.callback_query(F.data == "shop:instant_menu")
async def cb_shop_instant_menu(callback: CallbackQuery):
    _shop_clear_state(callback.from_user.id)
    await callback.message.edit_text(
        f"{EMOJIS['gift']} <b>Услуги чата:</b>\n\nВыбери услугу:",
        reply_markup=_shop_instant_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("shop:prod:"))
async def cb_shop_product(callback: CallbackQuery):
    buyer_id = callback.from_user.id
    shop_awaiting_target.pop(buyer_id, None)
    shop_awaiting_content.pop(buyer_id, None)
    cart = shop_carts.setdefault(buyer_id, {})
    product = callback.data.split(":")[2]
    cart.clear()
    cart["product"] = product

    if product in INSTANT_SERVICES:
        name, price = INSTANT_SERVICES[product]
        cart["duration_seconds"] = None
        cart["duration_label"] = "—"
        cart["price"] = price
        await _shop_proceed_after_duration(callback, cart)
        return

    await callback.message.edit_text(
        f"{EMOJIS['gift']} <b>Услуга: {SHOP_PRODUCT_LABELS[product]}</b>\n\nВыбери срок:",
        reply_markup=_shop_duration_kb(product),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("shop:dur:"))
async def cb_shop_duration(callback: CallbackQuery):
    buyer_id = callback.from_user.id
    cart = shop_carts.get(buyer_id)
    if not cart or "product" not in cart:
        await callback.answer("Сессия покупки истекла, начни заново через /shop.", show_alert=True)
        return
    idx = int(callback.data.split(":")[2])
    durations = _duration_list_for(cart["product"])
    if idx >= len(durations):
        await callback.answer("Сессия покупки истекла, начни заново через /shop.", show_alert=True)
        return
    label, secs, price = durations[idx]
    cart["duration_seconds"] = secs
    cart["duration_label"] = label
    cart["price"] = price
    await _shop_proceed_after_duration(callback, cart)


@router.callback_query(F.data.startswith("shop:chat:"))
async def cb_shop_chat(callback: CallbackQuery):
    buyer_id = callback.from_user.id
    cart = shop_carts.get(buyer_id)
    if not cart or "product" not in cart:
        await callback.answer("Сессия покупки истекла, начни заново через /shop.", show_alert=True)
        return
    chat_id = int(callback.data.split(":")[2])
    title = next((t for cid, t in await _shop_member_chats(buyer_id) if cid == chat_id), None)
    cart["target_chat_id"] = chat_id
    cart["target_chat_title"] = title
    await _shop_ask_next_step(callback.message, buyer_id, cart, edit=True)
    await callback.answer()


@router.message(F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(shop_awaiting_target))
async def on_shop_target_input(message: Message):
    buyer = message.from_user
    target_chat_id = shop_awaiting_target[buyer.id]
    cart = shop_carts.get(buyer.id) or {}

    target_id = None
    target_display = None

    origin = getattr(message, "forward_origin", None)
    raw_text = (message.text or "").strip()

    if origin is not None and getattr(origin, "sender_user", None) is not None:
        u = origin.sender_user
        target_id = u.id
        target_display = fmt_user(u)
    elif raw_text.startswith("@"):
        row = db_find_user_by_username(target_chat_id, raw_text)
        if row:
            target_id, uname = row
            target_display = f"@{uname}"
    elif raw_text.isdigit() and 4 <= len(raw_text) <= 10:
        candidate_id = int(raw_text)
        try:
            member = await bot.get_chat_member(target_chat_id, candidate_id)
            target_id = candidate_id
            target_display = fmt_user(member.user)
        except Exception:
            target_id = candidate_id
            target_display = f"id{candidate_id}"

    if target_id is None:
        await message.answer(
            f"{EMOJIS['cross']} Не нашёл такого пользователя. Пришли @юзернейм, "
            f"ID (4-10 цифр) или перешли его сообщение из чата."
        )
        return

    if target_id == buyer.id and cart.get("product") not in PREFIX_PRODUCTS:
        await message.answer("Себя нельзя выбрать целью покупки 🙂")
        return

    shop_awaiting_target.pop(buyer.id, None)
    cart["target_id"] = target_id
    cart["target_username"] = target_display
    shop_carts[buyer.id] = cart

    if cart["product"] in PREFIX_PRODUCTS:
        # Для префикса дальше нужен текст кастомного титула.
        shop_awaiting_content[buyer.id] = target_chat_id
        await message.answer(f"{_cart_summary(cart)}\n\n{_content_prompt(cart['product'])}")
        return

    await _shop_send_invoice(message, buyer, cart)


@router.message(F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(shop_awaiting_content))
async def on_shop_content_input(message: Message):
    buyer = message.from_user
    cart = shop_carts.get(buyer.id) or {}
    product = cart.get("product")
    if not product:
        shop_awaiting_content.pop(buyer.id, None)
        return

    if product == "avatar":
        if not message.photo:
            await message.answer(f"{EMOJIS['cross']} Пришли именно фото для новой аватарки чата.")
            return
        cart["content"] = message.photo[-1].file_id
    elif product == "prefix":
        title = (message.text or "").strip()
        if not title:
            await message.answer(f"{EMOJIS['cross']} Пришли текстом название титула.")
            return
        cart["content"] = title[:16]
    else:  # description / pin
        text = (message.text or "").strip()
        if not text:
            await message.answer(f"{EMOJIS['cross']} Пришли текстом новое содержимое.")
            return
        cart["content"] = text

    shop_awaiting_content.pop(buyer.id, None)
    shop_carts[buyer.id] = cart

    await _shop_send_invoice(message, buyer, cart)


def _shop_invoice_description(cart: dict) -> str:
    product = cart["product"]
    desc = SHOP_PRODUCT_LABELS[product]
    if cart.get("duration_label") and cart["duration_label"] != "—":
        desc += f", {cart['duration_label']}"
    if _needs_target_user(product) and cart.get("target_username"):
        desc += f" — {cart['target_username']}"
    elif cart.get("target_chat_title"):
        desc += f" — чат «{cart['target_chat_title']}»"
    return desc[:255]


async def _shop_send_invoice(message: Message, buyer, cart: dict) -> None:
    """Создаёт заказ и отправляет покупателю счёт в Telegram Stars (XTR).
    Оплата идёт на баланс бота; услуга применяется автоматически после
    успешной оплаты."""
    product = cart["product"]
    price = int(cart["price"])
    target_username = cart.get("target_username") or cart.get("target_chat_title") or ""
    order_id = db_shop_create_order(
        chat_id=cart["target_chat_id"],
        buyer_id=buyer.id,
        buyer_username=fmt_user(buyer),
        target_id=cart.get("target_id"),
        target_username=target_username,
        product=product,
        duration_seconds=cart["duration_seconds"],
        duration_label=cart["duration_label"],
        price=price,
        content=cart.get("content", ""),
    )
    description = _shop_invoice_description(cart)
    _shop_clear_state(buyer.id)
    try:
        await bot.send_invoice(
            chat_id=buyer.id,
            title=SHOP_PRODUCT_LABELS[product][:32],
            description=description,
            payload=f"shop:{order_id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=SHOP_PRODUCT_LABELS[product][:32], amount=price)],
        )
    except Exception as e:
        logging.exception("send_invoice failed for order %s", order_id)
        db_shop_set_status(order_id, "invoice_failed")
        await message.answer(
            f"{EMOJIS['cross']} Не удалось создать счёт: {html.escape(str(e))}\n"
            f"Попробуй ещё раз через /shop."
        )


def _shop_order_id_from_payload(payload: str):
    if payload and payload.startswith("shop:"):
        return _parse_int(payload.split(":", 1)[1])
    return None


@router.pre_checkout_query()
async def on_shop_pre_checkout(query: PreCheckoutQuery):
    order_id = _shop_order_id_from_payload(query.invoice_payload)
    order = db_shop_get_order(order_id) if order_id else None
    if (
        not order
        or order[10] != "awaiting_payment"
        or order[6] not in SHOP_PRODUCT_LABELS
        or order[2] != query.from_user.id
        or query.currency != "XTR"
        or query.total_amount != order[12]
    ):
        await query.answer(ok=False, error_message="Заказ недействителен или уже оплачен. Начни заново через /shop.")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_shop_paid(message: Message):
    sp = message.successful_payment
    order_id = _shop_order_id_from_payload(sp.invoice_payload)
    order = db_shop_get_order(order_id) if order_id else None
    if not order or order[10] != "awaiting_payment":
        # Платёж прошёл, но заказ не найден/уже обработан — возвращаем звёзды.
        try:
            await bot.refund_star_payment(
                user_id=message.from_user.id,
                telegram_payment_charge_id=sp.telegram_payment_charge_id,
            )
        except Exception:
            logging.exception("refund of orphan payment failed")
        return
    db_shop_set_charge(order_id, sp.telegram_payment_charge_id)
    db_shop_set_status(order_id, "paid")
    await message.answer(f"{EMOJIS['loading']} Оплата получена, выполняю заказ #{order_id}…")
    await _shop_fulfill(order_id, sp.telegram_payment_charge_id)


async def _download_bytes(file_id: str) -> bytes:
    file = await bot.get_file(file_id)
    buf = await bot.download_file(file.file_path)
    return buf.read()


async def _apply_chat_photo(chat_id: int, new_file_id) -> None:
    """Ставит новое фото чата (или удаляет фото, если new_file_id пуст).
    setChatPhoto принимает только загруженный файл, а не file_id — поэтому
    старое/новое фото сперва скачивается и заливается заново."""
    if new_file_id:
        data = await _download_bytes(new_file_id)
        await bot.set_chat_photo(chat_id, BufferedInputFile(data, filename="avatar.jpg"))
    else:
        await bot.delete_chat_photo(chat_id)


async def _demote_prefix(chat_id: int, user_id: int) -> None:
    """Снимает временную админку с нулевыми правами (кастомный титул уходит
    вместе с правами админа). Согласно Bot API, чтобы разжаловать
    администратора, promoteChatMember вызывается со всеми правами = False."""
    await bot.promote_chat_member(
        chat_id,
        user_id,
        is_anonymous=False,
        can_manage_chat=False,
        can_delete_messages=False,
        can_manage_video_chats=False,
        can_restrict_members=False,
        can_promote_members=False,
        can_change_info=False,
        can_invite_users=False,
        can_pin_messages=False,
        can_post_messages=False,
        can_edit_messages=False,
    )


async def _shop_fulfill(order_id: int, charge_id: str) -> None:
    """Выполняет оплаченный заказ автоматически. Если применить услугу не
    удалось — оплата возвращается покупателю (refundStarPayment)."""
    order = db_shop_get_order(order_id)
    if not order:
        return
    (
        _, chat_id, buyer_id, buyer_username, target_id, target_username,
        product, duration_seconds, payee_key, payee_id, status,
        duration_label, price, content,
    ) = order
    target_label = target_username or "весь чат"

    try:
        if product == "mute":
            # duration_seconds может быть None ("Навсегда") — тогда until_date
            # не передаём, и Telegram применяет ограничение бессрочно.
            until_dt = (
                datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
                if duration_seconds else None
            )
            await bot.restrict_chat_member(
                chat_id,
                target_id,
                permissions=ChatPermissions(
                    can_send_messages=False,
                    can_send_audios=False,
                    can_send_documents=False,
                    can_send_photos=False,
                    can_send_videos=False,
                    can_send_video_notes=False,
                    can_send_voice_notes=False,
                    can_send_polls=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False,
                ),
                until_date=until_dt,
            )
        elif product == "ban":
            until_dt = (
                datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
                if duration_seconds else None
            )
            await bot.ban_chat_member(chat_id, target_id, until_date=until_dt)
        elif product == "unban":
            await bot.unban_chat_member(chat_id, target_id, only_if_banned=True)
        elif product == "unmute":
            await bot.restrict_chat_member(
                chat_id,
                target_id,
                permissions=ChatPermissions(
                    can_send_messages=True,
                    can_send_audios=True,
                    can_send_documents=True,
                    can_send_photos=True,
                    can_send_videos=True,
                    can_send_video_notes=True,
                    can_send_voice_notes=True,
                    can_send_polls=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                ),
            )
        elif product == "avatar":
            old_chat = await bot.get_chat(chat_id)
            old_file_id = old_chat.photo.big_file_id if old_chat.photo else ""
            await _apply_chat_photo(chat_id, content)
            if duration_seconds:
                db_schedule_revert(chat_id, "avatar", None, old_file_id, time.time() + duration_seconds)
        elif product == "description":
            old_chat = await bot.get_chat(chat_id)
            old_description = old_chat.description or ""
            await bot.set_chat_description(chat_id, content or None)
            if duration_seconds:
                db_schedule_revert(chat_id, "description", None, old_description, time.time() + duration_seconds)
        elif product == "prefix":
            # ВАЖНО: если передать во все параметры False, Telegram трактует
            # это не как повышение, а как ПОНИЖЕНИЕ (см. доки Bot API:
            # "Pass False for all boolean parameters to demote a user").
            # Из-за этого пользователь реально не становился админом, и
            # следующий вызов set_chat_administrator_custom_title падал с
            # ошибкой "user is not an administrator". Поэтому оставляем
            # включённым минимально-инвазивное право can_manage_chat (даёт
            # только доступ к журналу событий чата, никаких реальных
            # прав модерации не даёт), чтобы статус админа применился.
            await bot.promote_chat_member(
                chat_id,
                target_id,
                is_anonymous=False,
                can_manage_chat=True,
                can_delete_messages=False,
                can_manage_video_chats=False,
                can_restrict_members=False,
                can_promote_members=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False,
                can_post_messages=False,
                can_edit_messages=False,
            )
            await bot.set_chat_administrator_custom_title(chat_id, target_id, content or "")
            if duration_seconds:
                db_schedule_revert(chat_id, "prefix", target_id, "", time.time() + duration_seconds)
        elif product == "close":
            old_chat = await bot.get_chat(chat_id)
            old_perms = old_chat.permissions
            old_payload = json.dumps(old_perms.model_dump(exclude_none=True)) if old_perms else ""
            await bot.set_chat_permissions(
                chat_id,
                permissions=ChatPermissions(
                    can_send_messages=False,
                    can_send_audios=False,
                    can_send_documents=False,
                    can_send_photos=False,
                    can_send_videos=False,
                    can_send_video_notes=False,
                    can_send_voice_notes=False,
                    can_send_polls=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False,
                ),
            )
            if duration_seconds:
                db_schedule_revert(chat_id, "close", None, old_payload, time.time() + duration_seconds)
        elif product == "pin":
            sent = await bot.send_message(chat_id, content)
            await bot.pin_chat_message(chat_id, sent.message_id)
            if duration_seconds:
                db_schedule_revert(chat_id, "pin", sent.message_id, "", time.time() + duration_seconds)
    except Exception as e:
        logging.exception("shop order %s failed", order_id)
        db_shop_set_status(order_id, "failed")
        refunded = False
        try:
            await bot.refund_star_payment(user_id=buyer_id, telegram_payment_charge_id=charge_id)
            refunded = True
            db_shop_set_status(order_id, "refunded")
        except Exception:
            logging.exception("refund for shop order %s failed", order_id)
        try:
            await bot.send_message(
                buyer_id,
                f"{EMOJIS['cross']} Не удалось выполнить заказ #{order_id} "
                f"({SHOP_PRODUCT_LABELS[product]} {target_label}).\n"
                + ("Звёзды возвращены на твой баланс." if refunded
                   else "Напиши администратору — он вернёт оплату.")
            )
        except Exception:
            pass
        for owner in {OWNER_ID, SECOND_OWNER_ID, THIRD_OWNER_ID, FOURTH_OWNER_ID}:
            try:
                await bot.send_message(
                    owner,
                    f"{EMOJIS['cross']} Заказ #{order_id} (магазин) не выполнен: {html.escape(str(e))}\n"
                    f"Покупатель: {buyer_username}. "
                    + ("Оплата возвращена автоматически." if refunded else f"Автовозврат не удался, charge_id: {charge_id}")
                )
            except Exception:
                pass
        return

    db_shop_set_status(order_id, "done")
    action_labels = {
        "mute": "замучен", "ban": "забанен", "unban": "разбанен", "unmute": "размучен",
        "avatar": "аватарка чата изменена", "description": "описание чата изменено",
        "prefix": "выдан префикс", "pin": "сообщение закреплено",
        "close": "чат закрыт",
    }
    action_label = action_labels.get(product, "выполнено")
    if duration_label == "Навсегда":
        duration_suffix = " навсегда"
    elif duration_label and duration_label != "—":
        duration_suffix = f" на {duration_label}"
    else:
        duration_suffix = ""
    if product in ("avatar", "description", "pin", "close"):
        summary_line = f"{action_label}{duration_suffix}"
    else:
        summary_line = f"{target_label} {action_label}{duration_suffix}"

    try:
        await bot.send_message(
            buyer_id,
            f"{EMOJIS['check']} Оплата получена, заказ #{order_id} выполнен: {summary_line}.",
        )
    except Exception:
        pass
    try:
        if product in (USER_TIMED_PRODUCTS | INSTANT_USER_PRODUCTS | PREFIX_PRODUCTS):
            purchase_line = f"{buyer_username} Купил {SHOP_PRODUCT_LABELS[product]} для {target_label}"
        else:
            purchase_line = f"{buyer_username} Купил {SHOP_PRODUCT_LABELS[product]}"
        flavor = random.choice(SHOP_FLAVOR_QUOTES)
        await bot.send_message(chat_id, f"{purchase_line}\n<i>{flavor}</i>")
    except Exception:
        pass


async def shop_revert_loop() -> None:
    """Фоновая задача: раз в 30 секунд проверяет, не пора ли откатить
    временные изменения чата (аватарку/описание/префикс/закреп) обратно."""
    while True:
        try:
            due = db_due_reverts(time.time())
            for revert_id, chat_id, kind, target_id, payload in due:
                try:
                    if kind == "avatar":
                        await _apply_chat_photo(chat_id, payload or None)
                    elif kind == "description":
                        await bot.set_chat_description(chat_id, payload or None)
                    elif kind == "prefix":
                        await _demote_prefix(chat_id, target_id)
                    elif kind == "pin":
                        await bot.unpin_chat_message(chat_id, message_id=target_id)
                    elif kind == "close":
                        restored = json.loads(payload) if payload else {}
                        if not restored:
                            restored = {"can_send_messages": True}
                        await bot.set_chat_permissions(chat_id, permissions=ChatPermissions(**restored))
                        await bot.send_message(chat_id, f"{EMOJIS['check']} Чат снова открыт.")
                except Exception:
                    pass  # права могли отозвать/чат мог измениться — пропускаем
                db_mark_reverted(revert_id)
        except Exception:
            pass
        await asyncio.sleep(30)

def _parse_int(s: str):
    """int(s), но без падения на строках вроде '②'/'③' — для них
    str.isdigit() возвращает True, а int() кидает ValueError. Возвращает
    None вместо исключения, если строку не удалось превратить в число."""
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


# Единицы измерения длительности ивента, которые можно указывать суффиксом
# после числа: 30s / 30с — секунды, 5m / 5мин — минуты, 1h / 1ч — часы.
_DURATION_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "с": 1, "сек": 1, "секунда": 1, "секунды": 1, "секунд": 1, "секунду": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "м": 60, "мин": 60, "минута": 60, "минуты": 60, "минут": 60, "минуту": 60,
    "h": 3600, "hour": 3600, "hours": 3600,
    "ч": 3600, "час": 3600, "часа": 3600, "часов": 3600,
}


def _parse_duration(token: str):
    """Разбирает длительность ивента из токена команды /giveaway.
    Поддерживает суффиксы единиц (30s, 90с, 5m, 5мин, 1h, 1ч) и голое
    число, которое по умолчанию трактуется как минуты (старый формат
    команды остаётся рабочим). Возвращает длительность в секундах
    (int, > 0) или None, если токен не удалось распознать."""
    token = token.strip().lower().replace(",", ".")
    if not token:
        return None

    plain = _parse_int(token)
    if plain is not None:
        return plain * 60 if plain > 0 else None

    i = 0
    while i < len(token) and (token[i].isdigit() or token[i] == "."):
        i += 1
    number_part, unit_part = token[:i], token[i:]
    if not number_part or not unit_part:
        return None
    try:
        value = float(number_part)
    except ValueError:
        return None
    multiplier = _DURATION_UNITS.get(unit_part)
    if multiplier is None or value <= 0:
        return None
    seconds = int(round(value * multiplier))
    return seconds if seconds > 0 else None


def _parse_chance(token: str):
    """Разбирает шанс дропа из аргумента команды /set_chance или /farm.
    Поддерживает запятую и точку как разделитель дробной части, а также
    необязательный знак '%' на конце ('0,1', '0.1%', '5%', '100').
    Возвращает float в диапазоне [MIN_FARM_CHANCE, MAX_FARM_CHANCE] или
    None, если токен не удалось распознать или значение вне диапазона."""
    token = token.strip().replace(",", ".").rstrip("%").strip()
    if not token:
        return None
    try:
        value = float(token)
    except ValueError:
        return None
    if value < MIN_FARM_CHANCE or value > MAX_FARM_CHANCE:
        return None
    return round(value, 4)


def _format_duration(seconds: int) -> str:
    """Человекочитаемое представление длительности: '2 мин', '90 сек',
    '2 мин 30 сек', '1 ч 5 мин' и т.п."""
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if secs or not parts:
        parts.append(f"{secs} сек")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------


@router.message(CommandStart())
async def cmd_start(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        await _send_start(message)
        return

    return


@router.message(F.text == "Топ")
@router.message(Command("top"))
async def cmd_top(message: Message):
    if message.chat.type != ChatType.PRIVATE and not db_is_chat_whitelisted(message.chat.id):
        await message.answer("Этот чат не в вайтлисте.")
        return
    rows = db_top(message.chat.id)
    if not rows:
        await message.answer("Пока никто ничего не выигрывал в этом чате.")
        return
    medals = [EMOJIS["win"], EMOJIS["silver"], EMOJIS["bronze"]]
    lines = [f"{EMOJIS['trophy']} Топ по победам в этом чате:\n"]
    for i, (user_id, username, nickname, wins) in enumerate(rows):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        display_name = nickname or username or f"id{user_id}"
        name_link = f'<a href="tg://openmessage?user_id={user_id}">{display_name}</a>'
        lines.append(f"{prefix} {name_link} — {wins} побед")
    await message.answer("\n".join(lines))


@router.message(F.text == "Игры")
async def cmd_games(message: Message):
    if message.chat.type != ChatType.PRIVATE and not db_is_chat_whitelisted(message.chat.id):
        await message.answer("Этот чат не в вайтлисте.")
        return
    g = active_giveaways.get(message.chat.id)
    ng = active_number_games.get(message.chat.id)
    farm = active_farms.get(message.chat.id)
    slots = active_slots.get(message.chat.id)
    casino = active_casino.get(message.chat.id)

    if not g and not ng and not farm and not slots and not casino:
        await message.answer("Сейчас в этом чате нет активных игр.")
        return

    if g:
        remaining = max(0, int(g["end_time"] - time.time()))
        mins, secs = divmod(remaining, 60)
        leader = g["leader_name"] or "пока никто"
        description_line = f"{EMOJIS['note']} {g['description']}\n" if g.get("description") else ""
        stars = g.get("stars", DEFAULT_STARS_PER_MESSAGE)
        real_price_seen = g.get("real_price_seen")
        price_line = (
            f"{EMOJIS['star']} 1 сообщение в чате = {real_price_seen} звёзд (подтверждено Telegram).\n"
            if real_price_seen is not None
            else f"{EMOJIS['star']} 1 сообщение в чате = {stars} звёзд (задано при старте, Telegram ещё не подтвердил).\n"
        )
        await message.answer(
            f"{EMOJIS['gift']} Активный ивент: {g['prize']}\n"
            f"{description_line}"
            f"{price_line}"
            f"Текущий лидер: {leader}\n"
            f"{EMOJIS['loading']} До конца: {mins} мин {secs} сек"
        )

    if ng:
        remaining_ng = max(0, int(ng["end_time"] - time.time()))
        mins_ng, secs_ng = divmod(remaining_ng, 60)
        description_line = f"{EMOJIS['note']} {ng['description']}\n" if ng.get("description") else ""
        await message.answer(
            f"{EMOJIS['number']} Активная игра «Угадай число»: {ng['prize']}\n"
            f"{description_line}"
            f"Диапазон: от {ng['min']} до {ng['max']}\n"
            f"{EMOJIS['star']} Цена: {ng['stars']} звёзд\n"
            f"{EMOJIS['loading']} До конца: {mins_ng} мин {secs_ng} сек\n\n"
            f"Напишите число — первый угадавший побеждает!"
        )

    if farm:
        farm_prizes = farm.get("prizes") or [farm["prize"]]
        if len(farm_prizes) > 1:
            farm_line = f"🏆 Призы ({len(farm_prizes)}): {', '.join(farm_prizes)}"
        else:
            farm_line = f"🏆 Приз: {farm_prizes[0]}"
        await message.answer(
            f"{EMOJIS['alert']} Активный фарм: {farm_line} от {farm['giver']}\n"
            f"🎲 Шанс дропа: {farm['chance']}%\n\n"
            f"Подарки дарятся за активность — пишите сообщения в чате, и получайте шанс мгновенно выиграть приз!"
        )

    if slots:
        description_line = f"{EMOJIS['note']} {slots['description']}\n\n" if slots.get("description") else ""
        await message.answer(
            f"{EMOJIS['game']} <b>Активные слоты</b>\n"
            f"{EMOJIS['gift']} Приз: {slots['prize']}\n\n"
            f"{description_line}"
            f"{EMOJIS['fire']} Отправь эмодзи 🎰 в чат и лови джекпот {EMOJIS['crown']} <b>{EMOJIS['seven']}{EMOJIS['seven']}{EMOJIS['seven']}</b> {EMOJIS['crown']}"
        )

    if casino:
        remaining_c = max(0, int(casino["end_time"] - time.time()))
        duration_label = _format_duration(remaining_c)
        medal_lines = render_prize_lines(casino["prizes"])
        await message.answer(
            f"{EMOJIS['crown']} <b>Казино-ивент активен</b> {EMOJIS['crown']}\n"
            f"{EMOJIS['loading']} Осталось: {duration_label}\n\n"
            f"{EMOJIS['star']} Кидай 🎰 в чат — {EMOJIS['bar']}{EMOJIS['bar']}{EMOJIS['bar']}, {EMOJIS['grape']}{EMOJIS['grape']}{EMOJIS['grape']}, {EMOJIS['lemon']}{EMOJIS['lemon']}{EMOJIS['lemon']} или {EMOJIS['seven']}{EMOJIS['seven']}{EMOJIS['seven']} дают +1 очко\n\n"
            f"{EMOJIS['gift']} <b>Награда победителям</b>\n"
            f"{medal_lines}\n\n"
            + render_casino_leaderboard(casino)
        )


def _resolve_target_user(message: Message):
    """Возвращает (user_id, username) из ответа на сообщение или из аргумента-числа."""
    if message.reply_to_message:
        u = message.reply_to_message.from_user
        return u.id, fmt_user(u)
    args = message.text.split(maxsplit=1)
    if len(args) == 2:
        uid = _parse_int(args[1].strip())
        if uid is not None:
            return uid, f"id{uid}"
    return None, None


@router.message(Command("add_adm"))
async def cmd_add_adm(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Добавлять админов может только владелец или существующий админ бота.")
        return
    target_id, target_name = _resolve_target_user(message)
    if target_id is None:
        await message.answer(
            "Использование: ответьте командой /add_adm на сообщение пользователя,\n"
            "либо укажите его id: /add_adm 123456789"
        )
        return
    db_add_admin(target_id, target_name)
    await message.answer(f"{EMOJIS['check']} {target_name} добавлен(а) в админы бота.")


@router.message(Command("del_adm"))
async def cmd_del_adm(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Удалять админов может только владелец или существующий админ бота.")
        return
    target_id, target_name = _resolve_target_user(message)
    if target_id is None:
        await message.answer(
            "Использование: ответьте командой /del_adm на сообщение пользователя,\n"
            "либо укажите его id: /del_adm 123456789"
        )
        return
    if target_id in (OWNER_ID, SECOND_OWNER_ID, THIRD_OWNER_ID, FOURTH_OWNER_ID):
        await message.answer("Владельца бота нельзя удалить из админов.")
        return
    db_remove_admin(target_id)
    await message.answer(f"{EMOJIS['cross']} {target_name} удалён(а) из админов бота.")


@router.message(Command("admins"))
async def cmd_admins(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Список админов доступен только админам бота.")
        return
    rows = db_list_admins()
    lines = [f"{EMOJIS['crown']} Владелец бота (id: {OWNER_ID})" if OWNER_ID else f"{EMOJIS['crown']} Владелец бота не задан (OWNER_ID)"]
    if SECOND_OWNER_ID:
        lines.append(f"{EMOJIS['crown']} Второй владелец бота (id: {SECOND_OWNER_ID})")
    if THIRD_OWNER_ID:
        lines.append(f"{EMOJIS['crown']} Третий владелец бота (id: {THIRD_OWNER_ID})")
    if FOURTH_OWNER_ID:
        lines.append(f"{EMOJIS['crown']} Четвёртый владелец бота (id: {FOURTH_OWNER_ID})")
    if rows:
        lines.append("\nАдмины:")
        lines += [f"— {username} (id: {uid})" for uid, username in rows]
    else:
        lines.append("\nДополнительных админов пока нет.")
    await message.answer("\n".join(lines))


# ---------------------------------------------------------------------------
# /panel — запуск и остановка ивентов в чатах из ЛС с ботом
# ---------------------------------------------------------------------------
#
# Админ бота открывает /panel в личке, выбирает чат из вайтлиста, потом ивент
# и присылает параметры (в том же формате, что после команды в чате). Дальше
# бот вызывает обычный обработчик команды так, будто её написали в выбранном
# чате — вся логика ивентов остаётся одной и той же.

# cmd -> (кнопка, формат параметров, пример)
PANEL_EVENTS = {
    "giveaway": (
        "🐸 Ивент (giveaway)",
        "<приз> <время> [звёзды] | <описание>",
        "Steam-ключ 3 15 | Кто продержится дольше всех!",
    ),
    "guess": (
        "🔢 Угадай число",
        "<приз> <от> <до> <время> [звёзды] [=своё число] | <описание>",
        "https://t.me/nft/LunarSnake-16936 0 444 5",
    ),
    "farm": (
        "🌾 Фарм",
        "<приз1> | <приз2> | ... | <от кого> [шанс]",
        "Мишка 🧸 | Сердце 💝 | NEV 0,1",
    ),
    "slots": (
        "🎰 Слоты",
        "<приз> | <описание>",
        "https://t.me/nft/LunarSnake-16936",
    ),
    "casino": (
        "🎲 Казино",
        "<время> | <приз 1 место> | <приз 2 место> | ...",
        "15h | https://t.me/nft/LolPop-344050 | 3 мишки | 1 мишка",
    ),
    "box777": (
        "📦 Box777",
        "<NFT приз> | <дешёвые призы через запятую> | <кол-во пустых ячеек>",
        "Bonded Ring #5048 | 15⭐, 25⭐, 50⭐ | 10",
    ),
}

# cmd остановки -> подпись кнопки
PANEL_STOPS = {
    "stop": "Ивент / Угадай число",
    "stop_farm": "Фарм",
    "stop_slots": "Слоты",
    "stop_casino": "Казино",
    "stop_box777": "Box777",
}

# user_id админа -> (chat_id, cmd): бот ждёт от него параметры ивента
panel_awaiting: dict[int, tuple] = {}

# Быстрый запуск: user_id -> {chat_id, cmd, step, prizes/prize, giver}
quick_state: dict[int, dict] = {}

QUICK_EVENTS = {"farm", "giveaway"}

QUICK_CHANCES = [("0,1%", "0.1"), ("1%", "1"), ("5%", "5"), ("100%", "100")]
QUICK_TIMES = [("1 мин", 60), ("5 мин", 300), ("10 мин", 600), ("30 мин", 1800), ("1 час", 3600)]


def _last_args_key(chat_id: int, cmd: str) -> str:
    return f"last_args:{chat_id}:{cmd}"


def _panel_handlers() -> dict:
    return {
        "giveaway": cmd_giveaway, "guess": cmd_guess, "farm": cmd_farm,
        "slots": cmd_slots, "casino": cmd_casino, "box777": cmd_box777,
        "stop": cmd_stop, "stop_farm": cmd_stop_farm, "stop_slots": cmd_stop_slots,
        "stop_casino": cmd_stop_casino, "stop_box777": cmd_stop_box777,
    }


def _panel_chats_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=(title or str(cid))[:40], callback_data=f"pn:c:{cid}")]
        for cid, title in db_list_chats()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _panel_menu_kb(chat_id: int) -> InlineKeyboardMarkup:
    rows = []
    for cmd, (label, _, _) in PANEL_EVENTS.items():
        if cmd in QUICK_EVENTS:
            rows.append([InlineKeyboardButton(
                text=f"⚡ {label}", callback_data=f"pn:q:{chat_id}:{cmd}"
            )])
        else:
            rows.append([InlineKeyboardButton(
                text=label, callback_data=f"pn:e:{chat_id}:{cmd}"
            )])
    repeat_rows = []
    for cmd, (label, _, _) in PANEL_EVENTS.items():
        try:
            last = db_get_setting(_last_args_key(chat_id, cmd))
        except Exception:
            last = None
        if last:
            repeat_rows.append([InlineKeyboardButton(
                text=f"🔁 {label}", callback_data=f"pn:r:{chat_id}:{cmd}"
            )])
    if repeat_rows:
        rows.append([InlineKeyboardButton(text="— Повторить —", callback_data="pn:noop")])
        rows.extend(repeat_rows)
    rows.append([InlineKeyboardButton(text="— Остановить —", callback_data="pn:noop")])
    stops = list(PANEL_STOPS.items())
    for i in range(0, len(stops), 2):
        rows.append([
            InlineKeyboardButton(text=f"⛔ {label}", callback_data=f"pn:s:{chat_id}:{cmd}")
            for cmd, label in stops[i:i + 2]
        ])
    rows.append([InlineKeyboardButton(text="⬅️ К списку чатов", callback_data="pn:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _panel_run(admin_message: Message, chat_id: int, cmd: str, args: str = "") -> bool:
    """Вызывает обычный обработчик команды /<cmd> так, будто её написали в
    чате chat_id. Ответы бота (в т.ч. ошибки вроде «уже идёт игра») уходят
    в сам чат."""
    try:
        info = await bot.get_chat(chat_id)
    except Exception as e:
        await admin_message.answer(
            f"{EMOJIS['cross']} Не могу получить доступ к чату: {html.escape(str(e))}\n"
            f"Проверь, что бот всё ещё в этом чате."
        )
        return False
    chat_obj = Chat(id=info.id, type=info.type, title=info.title)
    text = f"/{cmd} {args}".strip()
    fake = admin_message.model_copy(
        update={"chat": chat_obj, "text": text, "entities": None, "reply_to_message": None}
    ).as_(bot)
    try:
        await _panel_handlers()[cmd](fake)
    except Exception as e:
        logging.exception("panel command %s failed", cmd)
        await admin_message.answer(f"{EMOJIS['cross']} Ошибка при запуске: {html.escape(str(e))}")
        return False
    try:
        db_set_setting(_last_args_key(chat_id, cmd), args or "")
    except Exception:
        logging.exception("failed to store last args")
    return True


@router.message(Command("panel"))
async def cmd_panel(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("Панель открывается в личных сообщениях с ботом.")
        return
    if not db_is_admin(message.from_user.id):
        await message.answer("Панель доступна только админам бота.")
        return
    panel_awaiting.pop(message.from_user.id, None)
    if not db_list_chats():
        await message.answer("Нет ни одного чата в вайтлисте. Добавь чат командой /add_chat в самом чате.")
        return
    await message.answer(
        "🎛 <b>Панель</b>\n\nВыбери чат. Дальше всё кнопками: ⚡ — быстрый запуск (спросит только приз), "
        "🔁 — повторить прошлый ивент.", reply_markup=_panel_chats_kb())


@router.callback_query(F.data == "pn:noop")
async def cb_panel_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "pn:back")
async def cb_panel_back(callback: CallbackQuery):
    if not db_is_admin(callback.from_user.id):
        await callback.answer("Только для админов бота.", show_alert=True)
        return
    panel_awaiting.pop(callback.from_user.id, None)
    quick_state.pop(callback.from_user.id, None)
    await callback.message.edit_text("🎛 <b>Панель</b>\n\nВыбери чат:", reply_markup=_panel_chats_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("pn:c:"))
async def cb_panel_chat(callback: CallbackQuery):
    if not db_is_admin(callback.from_user.id):
        await callback.answer("Только для админов бота.", show_alert=True)
        return
    chat_id = int(callback.data.split(":")[2])
    if not db_is_chat_whitelisted(chat_id):
        await callback.answer("Этот чат уже не в вайтлисте.", show_alert=True)
        return
    panel_awaiting.pop(callback.from_user.id, None)
    quick_state.pop(callback.from_user.id, None)
    title = next((t for cid, t in db_list_chats() if cid == chat_id), None) or str(chat_id)
    await callback.message.edit_text(
        f"🎛 <b>{html.escape(title)}</b>\n\n⚡ — запуск в 2 клика (только приз), 🔁 — как в прошлый раз\n"
        f"Запустить ивент или остановить текущий:",
        reply_markup=_panel_menu_kb(chat_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pn:e:"))
async def cb_panel_event(callback: CallbackQuery):
    if not db_is_admin(callback.from_user.id):
        await callback.answer("Только для админов бота.", show_alert=True)
        return
    quick_state.pop(callback.from_user.id, None)
    _, _, chat_id_str, cmd = callback.data.split(":")
    chat_id = int(chat_id_str)
    label, usage, example = PANEL_EVENTS[cmd]
    panel_awaiting[callback.from_user.id] = (chat_id, cmd)
    await callback.message.edit_text(
        f"<b>{label}</b>\n\n"
        f"Пришли параметры одним сообщением:\n<code>{html.escape(usage)}</code>\n\n"
        f"Например:\n<code>{html.escape(example)}</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"pn:c:{chat_id}")]
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pn:s:"))
async def cb_panel_stop(callback: CallbackQuery):
    if not db_is_admin(callback.from_user.id):
        await callback.answer("Только для админов бота.", show_alert=True)
        return
    _, _, chat_id_str, cmd = callback.data.split(":")
    ok = await _panel_run(callback.message.model_copy(update={"from_user": callback.from_user}).as_(bot),
                          int(chat_id_str), cmd)
    await callback.answer("Команда остановки отправлена в чат." if ok else "Не удалось.", show_alert=not ok)


def _quick_cancel_kb(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"pn:c:{chat_id}")]
    ])


async def _quick_finish(admin_msg: Message, uid: int, st: dict, args: str) -> bool:
    quick_state.pop(uid, None)
    ok = await _panel_run(admin_msg, st["chat_id"], st["cmd"], args)
    await admin_msg.answer(
        f"{EMOJIS['check']} Запущено! Проверь чат." if ok
        else f"{EMOJIS['cross']} Не удалось — причина в сообщении чата.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ В меню чата", callback_data=f"pn:c:{st['chat_id']}")]
        ]),
    )
    return ok


@router.callback_query(F.data.startswith("pn:q:"))
async def cb_panel_quick(callback: CallbackQuery):
    if not db_is_admin(callback.from_user.id):
        await callback.answer("Только для админов бота.", show_alert=True)
        return
    _, _, chat_id_str, cmd = callback.data.split(":")
    chat_id = int(chat_id_str)
    if cmd not in QUICK_EVENTS or not db_is_chat_whitelisted(chat_id):
        await callback.answer("Недоступно.", show_alert=True)
        return
    panel_awaiting.pop(callback.from_user.id, None)
    user = callback.from_user
    giver = f"@{user.username}" if user.username else (user.full_name or "админ")
    quick_state[callback.from_user.id] = {
        "chat_id": chat_id, "cmd": cmd, "step": "prize", "giver": giver,
    }
    label = PANEL_EVENTS[cmd][0]
    if cmd == "farm":
        hint = (
            "Пришли призы одним сообщением — можно несколько через запятую:\n"
            "<code>Мишка 🧸, Сердце 💝, Звезда ⭐</code>"
        )
    else:
        hint = "Пришли приз одним сообщением:\n<code>Steam-ключ</code>"
    rows = []
    try:
        last = db_get_setting(_last_args_key(chat_id, cmd))
    except Exception:
        last = None
    if last:
        rows.append([InlineKeyboardButton(
            text="🔁 Запустить как в прошлый раз", callback_data=f"pn:r:{chat_id}:{cmd}"
        )])
    rows.append([InlineKeyboardButton(text="✏️ Расширенный режим", callback_data=f"pn:e:{chat_id}:{cmd}")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"pn:c:{chat_id}")])
    await callback.message.edit_text(
        f"⚡ <b>{label} — быстрый запуск</b>\n\n{hint}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pn:r:"))
async def cb_panel_repeat(callback: CallbackQuery):
    if not db_is_admin(callback.from_user.id):
        await callback.answer("Только для админов бота.", show_alert=True)
        return
    _, _, chat_id_str, cmd = callback.data.split(":")
    chat_id = int(chat_id_str)
    try:
        args = db_get_setting(_last_args_key(chat_id, cmd)) or ""
    except Exception:
        args = ""
    if not args:
        await callback.answer("Нет сохранённых параметров — запусти через кнопку ивента.", show_alert=True)
        return
    quick_state.pop(callback.from_user.id, None)
    panel_awaiting.pop(callback.from_user.id, None)
    ok = await _panel_run(
        callback.message.model_copy(update={"from_user": callback.from_user}).as_(bot),
        chat_id, cmd, args,
    )
    await callback.answer("✅ Запущено!" if ok else "Не удалось — смотри чат.", show_alert=not ok)


def _quick_filter(message: Message) -> bool:
    if message.from_user is None or not message.text:
        return False
    if message.chat.type != ChatType.PRIVATE:
        return False
    if message.text.lstrip().startswith("/"):
        return False
    return message.from_user.id in quick_state


@router.message(_quick_filter)
async def on_quick_args(message: Message):
    uid = message.from_user.id
    st = quick_state.get(uid)
    if not st:
        return
    if not db_is_admin(uid):
        quick_state.pop(uid, None)
        return
    text = message.text.strip()
    chat_id, cmd, step = st["chat_id"], st["cmd"], st["step"]

    if step == "prize":
        if "|" in text:
            quick_state.pop(uid, None)
            await _quick_finish(message, uid, st, text)
            return
        if cmd == "farm":
            prizes = [p.strip().replace("|", "/") for p in text.replace("\n", ",").split(",")]
            prizes = [p for p in prizes if p]
            if not prizes:
                await message.answer("Пришли хотя бы один приз текстом.")
                return
            st["prizes"] = prizes
            st["step"] = "chance"
            rows = [
                [InlineKeyboardButton(text=f"🎲 Шанс {lbl}", callback_data=f"pn:q2:c:{val}")]
                for lbl, val in QUICK_CHANCES
            ]
            rows.append([InlineKeyboardButton(text="⚙️ Свой шанс", callback_data="pn:q2:c:custom")])
            rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"pn:c:{chat_id}")])
            await message.answer(
                f"🌾 Призы ({len(prizes)}): {', '.join(html.escape(p) for p in prizes)}\n\n"
                "Выбери шанс дропа на каждое сообщение:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
        else:
            prize = text.replace("\n", " ").replace("|", "/").strip()
            if not prize:
                await message.answer("Пришли приз текстом.")
                return
            st["prize"] = prize
            st["step"] = "time"
            rows = [
                [InlineKeyboardButton(text=f"⏳ {lbl}", callback_data=f"pn:q2:t:{secs}")]
                for lbl, secs in QUICK_TIMES
            ]
            rows.append([InlineKeyboardButton(text="⚙️ Своё время", callback_data="pn:q2:t:custom")])
            rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"pn:c:{chat_id}")])
            await message.answer(
                f"🐸 Приз: {html.escape(prize)}\n\nСколько держаться без перебива?",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
        return

    if step == "chance_custom":
        chance = _parse_chance(text)
        if chance is None:
            await message.answer(
                f"Не понял шанс. Введи число от {MIN_FARM_CHANCE} до {MAX_FARM_CHANCE} "
                "(например: 0,1 или 5)."
            )
            return
        prizes = st.get("prizes") or []
        args = " | ".join(prizes) + f" | {st['giver']} {str(chance).replace('.', ',')}"
        await _quick_finish(message, uid, st, args)
        return

    if step == "time_custom":
        duration = _parse_duration(text)
        if not duration:
            await message.answer("Не понял время. Например: 10 (минут), 30s, 1h.")
            return
        await _quick_finish(message, uid, st, f"{st['prize']} {duration}s")


@router.callback_query(F.data.startswith("pn:q2:"))
async def cb_quick_choice(callback: CallbackQuery):
    if not db_is_admin(callback.from_user.id):
        await callback.answer("Только для админов бота.", show_alert=True)
        return
    uid = callback.from_user.id
    st = quick_state.get(uid)
    if not st:
        await callback.answer("Сессия устарела — начни заново из меню.", show_alert=True)
        return
    parts = callback.data.split(":")
    kind = parts[2]
    val = parts[3]
    admin_msg = callback.message.model_copy(update={"from_user": callback.from_user}).as_(bot)

    if kind == "c":
        if val == "custom":
            st["step"] = "chance_custom"
            await callback.message.edit_text(
                f"Введи шанс дропа числом от {MIN_FARM_CHANCE} до {MAX_FARM_CHANCE} (например: 0,1 или 5):",
                reply_markup=_quick_cancel_kb(st["chat_id"]),
            )
            await callback.answer()
            return
        try:
            chance = float(val)
        except ValueError:
            await callback.answer("Ошибка шанса", show_alert=True)
            return
        prizes = st.get("prizes") or []
        args = " | ".join(prizes) + f" | {st['giver']} {str(chance).replace('.', ',')}"
        await callback.message.edit_text("⚡ Запускаю фарм...")
        await _quick_finish(admin_msg, uid, st, args)
    else:
        if val == "custom":
            st["step"] = "time_custom"
            await callback.message.edit_text(
                "Введи время: например 10 (минут), 30s или 1h:",
                reply_markup=_quick_cancel_kb(st["chat_id"]),
            )
            await callback.answer()
            return
        try:
            duration = int(val)
        except ValueError:
            await callback.answer("Ошибка времени", show_alert=True)
            return
        await callback.message.edit_text("⚡ Запускаю ивент...")
        await _quick_finish(admin_msg, uid, st, f"{st['prize']} {duration}s")
    await callback.answer("Готово")


def _panel_args_filter(message: Message) -> bool:
    """Сообщение в личке от админа, которого бот ждёт после нажатия кнопки ивента.
    Принимаем и просто параметры, и параметры с командой в начале
    (например «/casino 15h | приз | приз»), если команда совпадает с кнопкой."""
    if message.from_user is None or not message.text:
        return False
    pending = panel_awaiting.get(message.from_user.id)
    if not pending:
        return False
    text = message.text.strip()
    if not text.startswith("/"):
        return True
    first = text.split(maxsplit=1)[0][1:].split("@")[0].lower()
    return first == pending[1]


@router.message(F.chat.type == ChatType.PRIVATE, _panel_args_filter)
async def on_panel_args(message: Message):
    pending = panel_awaiting.pop(message.from_user.id, None)
    if not pending:
        return
    chat_id, cmd = pending
    if not db_is_admin(message.from_user.id):
        return
    args_text = message.text.strip()
    if args_text.startswith("/"):
        parts = args_text.split(maxsplit=1)
        args_text = parts[1].strip() if len(parts) > 1 else ""
    if await _panel_run(message, chat_id, cmd, args_text):
        await message.answer(
            f"{EMOJIS['check']} Команда /{cmd} отправлена в чат. Если что-то не так "
            f"(например, уже идёт игра) — сообщение об этом будет в самом чате.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ В меню чата", callback_data=f"pn:c:{chat_id}")]
            ]),
        )


@router.message(Command("add_chat"))
async def cmd_add_chat(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Добавлять чаты в вайтлист может только админ бота.")
        return
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(
            "Отправьте эту команду прямо в том групповом чате, который нужно добавить в вайтлист."
        )
        return
    db_add_chat(message.chat.id, message.chat.title or str(message.chat.id))
    await message.answer(
        f"{EMOJIS['check']} Чат «{html.escape(message.chat.title or str(message.chat.id))}» добавлен в вайтлист. "
        f"Теперь здесь можно запускать ивенты."
    )


@router.message(Command("del_chat"))
async def cmd_del_chat(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Удалять чаты из вайтлиста может только админ бота.")
        return
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Отправьте эту команду в том групповом чате, который нужно убрать из вайтлиста.")
        return
    db_remove_chat(message.chat.id)
    await message.answer(f"{EMOJIS['cross']} Чат «{html.escape(message.chat.title or str(message.chat.id))}» удалён из вайтлиста.")


@router.message(Command("chats"))
async def cmd_chats(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Список чатов доступен только админам бота.")
        return
    rows = db_list_chats()
    if not rows:
        await message.answer("Вайтлист чатов пуст.")
        return
    lines = ["📋 Чаты в вайтлисте:\n"]
    lines += [f"— {html.escape(title)} (id: {cid})" for cid, title in rows]
    await message.answer("\n".join(lines))


@router.message(Command("add_ignore"))
async def cmd_add_ignore(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Добавлять людей в игнор-лист может только админ бота.")
        return
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(
            "Отправьте эту команду прямо в нужном групповом чате, ответом на "
            "сообщение человека, которого нужно игнорировать в ивентах."
        )
        return
    target_id, target_name = _resolve_target_user(message)
    if target_id is None:
        await message.answer(
            "Использование: ответьте командой /add_ignore на сообщение человека,\n"
            "либо укажите его id: /add_ignore 123456789"
        )
        return
    db_add_ignore(message.chat.id, target_id, target_name)
    await message.answer(f"{EMOJIS['block']} {target_name} добавлен(а) в игнор-лист этого чата — не участвует в ивентах.")


@router.message(Command("del_ignore"))
async def cmd_del_ignore(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Убирать людей из игнор-листа может только админ бота.")
        return
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Отправьте эту команду прямо в нужном групповом чате.")
        return
    target_id, target_name = _resolve_target_user(message)
    if target_id is None:
        await message.answer(
            "Использование: ответьте командой /del_ignore на сообщение человека,\n"
            "либо укажите его id: /del_ignore 123456789"
        )
        return
    db_remove_ignore(message.chat.id, target_id)
    await message.answer(f"{EMOJIS['check']} {target_name} убран(а) из игнор-листа этого чата.")


@router.message(Command("ignore_list"))
async def cmd_ignore_list(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Список доступен только админам бота.")
        return
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Отправьте эту команду прямо в нужном групповом чате.")
        return
    rows = db_list_ignored(message.chat.id)
    if not rows:
        await message.answer("Игнор-лист этого чата пуст.")
        return
    lines = [f"{EMOJIS['block']} Игнор-лист этого чата:\n"]
    lines += [f"— {username} (id: {uid})" for uid, username in rows]
    await message.answer("\n".join(lines))


@router.message(Command("giveaway"))
async def cmd_giveaway(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Эту команду нужно использовать в групповом чате.")
        return

    if not db_is_chat_whitelisted(message.chat.id):
        await message.answer("Этот чат не в вайтлисте. Попросите админа бота добавить его командой /add_chat.")
        return

    if not db_is_admin(message.from_user.id):
        await message.answer("Запускать ивент могут только админы бота.")
        return

    if message.chat.id in active_giveaways or message.chat.id in active_number_games:
        await message.answer("В этом чате уже идёт игра. Сначала завершите её командой /stop.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Использование: /giveaway &lt;приз&gt; &lt;время&gt; [звёзды] | &lt;описание&gt;\n"
            "Время можно указывать в минутах (по умолчанию), секундах (30s / 30с) "
            "или часах (1h / 1ч).\n"
            "Например: /giveaway Steam-ключ 3\n"
            "На секунды: /giveaway Steam-ключ 90s\n"
            "Со своей ценой в звёздах: /giveaway Steam-ключ 3 15\n"
            "С описанием: /giveaway Steam-ключ 3 15 | Кто продержится дольше всех!"
        )
        return

    raw = args[1]
    if "|" in raw:
        main_part, description_part = raw.split("|", 1)
        description = description_part.strip() or None
    else:
        main_part = raw
        description = None

    tokens = main_part.strip().split()
    if not tokens:
        await message.answer(
            "Не указано время. Использование: /giveaway &lt;приз&gt; &lt;время&gt; [звёзды] | &lt;описание&gt;"
        )
        return

    # Если токенов 3+ и предпоследний — длительность, а последний — целое
    # число (звёзды не имеют единиц измерения), считаем это форматом
    # &lt;приз&gt; &lt;время&gt; &lt;звёзды&gt;. Иначе последний токен — это просто &lt;время&gt;,
    # а звёзды берутся по умолчанию.
    duration = None
    stars = DEFAULT_STARS_PER_MESSAGE
    prize_tokens = tokens

    if len(tokens) >= 3:
        duration_candidate = _parse_duration(tokens[-2])
        stars_candidate = _parse_int(tokens[-1])
        if duration_candidate is not None and stars_candidate is not None and stars_candidate >= 0:
            duration = duration_candidate
            stars = stars_candidate
            prize_tokens = tokens[:-2]

    if duration is None:
        duration = _parse_duration(tokens[-1])
        prize_tokens = tokens[:-1]

    if len(tokens) < 2 or duration is None:
        await message.answer(
            "Не указано время. Использование: /giveaway &lt;приз&gt; &lt;время&gt; [звёзды] | &lt;описание&gt;\n"
            "Время можно указывать в минутах (по умолчанию), секундах (30s / 30с) "
            "или часах (1h / 1ч)."
        )
        return

    prize = " ".join(prize_tokens)
    if not prize:
        await message.answer(
            "Не указан приз. Использование: /giveaway &lt;приз&gt; &lt;время&gt; [звёзды] | &lt;описание&gt;"
        )
        return

    duration_label = _format_duration(duration)
    prize_safe = html.escape(prize)
    description_safe = html.escape(description) if description else None

    description_line = f"{EMOJIS['note']} {description_safe}\n\n" if description_safe else ""

    start_message = await message.answer(
        f"🐸 <b>Ивент начался!</b>\n\n"
        f"{description_line}"
        f"{EMOJIS['star']} 1 сообщение в чате = {stars} звёзд.\n"
        f"⏳ Цель: продержаться {duration_label} без перебива.\n\n"
        f"{EMOJIS['gift']} Приз: {prize_safe}"
    )

    try:
        await bot.pin_chat_message(
            message.chat.id, start_message.message_id, disable_notification=False
        )
    except Exception:
        pass  # боту может не хватать прав на закрепление сообщений в этом чате

    active_giveaways[message.chat.id] = {
        "prize": prize_safe,
        "description": description_safe,
        "stars": stars,
        "duration": duration,
        "reset_seconds": duration,
        "end_time": time.time() + duration,
        "leader_id": None,
        "leader_name": None,
        "leader_nickname": None,
        "task": None,
        "countdown_sent": set(),
        "participants": {},  # user_id -> {"name": str, "count": int, "spent": int (реальные звёзды)}
        "real_price_seen": None,  # последняя реально списанная Telegram цена за сообщение (paid_star_count)
        "warned_30s": False,  # предупреждение "осталось 30 секунд" ещё не присылали
        "pinned_message_id": start_message.message_id,
        "status_message_id": None,
    }
    active_giveaways[message.chat.id]["task"] = asyncio.create_task(
        giveaway_watcher(message.chat.id)
    )

    if stars > 0:
        await trigger_userbot_price(message.chat.id, stars)


@router.message(Command("set_reset_time"))
async def cmd_set_reset_time(message: Message):
    """Меняет время до конца ивента «перебив» (active_giveaways) прямо во
    время игры — без остановки и перезапуска. Новое значение применяется
    сразу: остаток текущего таймера пересчитывается от текущего момента."""
    if not db_is_admin(message.from_user.id):
        await message.answer("Менять время перебива могут только админы бота.")
        return

    g = active_giveaways.get(message.chat.id)
    if not g:
        await message.answer(
            "В этом чате сейчас нет активного ивента «перебив». "
            "Запустите его командой /giveaway."
        )
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Использование: /set_reset_time &lt;время&gt;\n"
            "Время можно указывать в минутах (по умолчанию), секундах (30s / 30с) "
            "или часах (1h / 1ч).\n"
            "Например: /set_reset_time 2   или   /set_reset_time 90s"
        )
        return

    new_reset = _parse_duration(args[1])
    if new_reset is None or new_reset <= 0:
        await message.answer(
            "Не удалось распознать время. Укажите число минут, либо с суффиксом "
            "(30s / 30с — секунды, 1h / 1ч — часы)."
        )
        return

    old_reset = g["reset_seconds"]
    g["reset_seconds"] = new_reset
    # Пересчитываем остаток ТЕКУЩЕГО отсчёта так, будто перебив произошёл
    # только что — иначе новое значение применится только со следующего
    # перебива, а до тех пор досчитывает по старому таймеру.
    g["end_time"] = time.time() + new_reset
    g["countdown_sent"] = set()
    g["warned_30s"] = False

    old_label = _format_duration(old_reset)
    new_label = _format_duration(new_reset)
    direction = "увеличено" if new_reset > old_reset else "уменьшено" if new_reset < old_reset else "не изменилось"
    await message.answer(
        f"{EMOJIS['check']} Время перебива {direction}: {old_label} → {new_label}.\n"
        f"Применено сразу — остаток текущего отсчёта пересчитан."
    )


@router.message(Command("guess"))
async def cmd_guess(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Эту команду нужно использовать в групповом чате.")
        return

    if not db_is_chat_whitelisted(message.chat.id):
        await message.answer("Этот чат не в вайтлисте. Попросите админа бота добавить его командой /add_chat.")
        return

    if not db_is_admin(message.from_user.id):
        await message.answer("Запускать игру могут только админы бота.")
        return

    if message.chat.id in active_giveaways or message.chat.id in active_number_games:
        await message.answer("В этом чате уже идёт игра. Сначала завершите её командой /stop.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Использование: /guess &lt;приз&gt; &lt;от&gt; &lt;до&gt; &lt;время&gt; [звёзды] | &lt;описание&gt;\n"
            "Время можно указывать в минутах (по умолчанию), секундах (30s / 30с) "
            "или часах (1h / 1ч).\n"
            "Например: /guess https://t.me/nft/LunarSnake-16936 0 444 5\n"
            "Со своей ценой в звёздах: /guess https://t.me/nft/LunarSnake-16936 0 444 5 3\n"
            "С описанием: /guess Подарок 0 444 5 3 | Кто быстрее угадает число!\n"
            "Своё число: добавь =число, например /guess Подарок 0 444 5 =123 "
            "(без =число бот загадает случайное)"
        )
        return

    raw = args[1]
    if "|" in raw:
        main_part, description_part = raw.split("|", 1)
        description = description_part.strip() or None
    else:
        main_part = raw
        description = None

    tokens = main_part.strip().split()

    # Своё загаданное число: токен вида =777, #777, :777 или число=777.
    custom_secret = None
    for t in list(tokens):
        m = re.fullmatch(r"(?:число|секрет|n)?[=:#](-?\d+)", t, flags=re.IGNORECASE)
        if m:
            custom_secret = int(m.group(1))
            tokens.remove(t)
            break

    # Если токенов 5+ и последние четыре — это &lt;от&gt; &lt;до&gt; &lt;время&gt; &lt;звёзды&gt;,
    # используем их. Иначе пробуем последние три как &lt;от&gt; &lt;до&gt; &lt;время&gt;,
    # а звёзды берём по умолчанию.
    min_val = max_val = duration = stars = None
    prize_tokens = tokens

    if len(tokens) >= 5:
        a, b, c, d = tokens[-4:]
        ia, ib = _parse_int(a), _parse_int(b)
        dur = _parse_duration(c)
        istars = _parse_int(d)
        if (
            ia is not None and ib is not None and ia < ib
            and dur is not None
            and istars is not None and istars >= 0
        ):
            min_val, max_val, duration, stars = ia, ib, dur, istars
            prize_tokens = tokens[:-4]

    if min_val is None and len(tokens) >= 4:
        a, b, c = tokens[-3:]
        ia, ib = _parse_int(a), _parse_int(b)
        dur = _parse_duration(c)
        if ia is not None and ib is not None and ia < ib and dur is not None:
            min_val, max_val, duration = ia, ib, dur
            stars = DEFAULT_NUMBER_GAME_STARS
            prize_tokens = tokens[:-3]

    if min_val is None:
        await message.answer(
            "Не удалось распознать параметры. Использование:\n"
            "/guess &lt;приз&gt; &lt;от&gt; &lt;до&gt; &lt;время&gt; [звёзды] | &lt;описание&gt;\n"
            "&lt;от&gt; и &lt;до&gt; — целые числа (от &lt; до). Время можно указывать в "
            "минутах (по умолчанию), секундах (30s / 30с) или часах (1h / 1ч)."
        )
        return

    prize = " ".join(prize_tokens)
    if not prize:
        await message.answer(
            "Не указан приз. Использование: /guess &lt;приз&gt; &lt;от&gt; &lt;до&gt; &lt;время&gt; [звёзды] | &lt;описание&gt;"
        )
        return

    prize_safe = html.escape(prize)
    description_safe = html.escape(description) if description else None
    description_line = f"{EMOJIS['note']} {description_safe}\n\n" if description_safe else ""
    duration_label = _format_duration(duration)

    if custom_secret is not None:
        if not (min_val <= custom_secret <= max_val):
            await message.answer(
                f"Загаданное число {custom_secret} вне диапазона от {min_val} до {max_val}."
            )
            return
        secret_number = custom_secret
        try:
            await message.delete()  # чтобы участники не увидели число в команде
        except Exception:
            pass
    else:
        secret_number = random.randint(min_val, max_val)

    start_message = await message.answer(
        f"{EMOJIS['number']} <b>Угадай число запущено!</b>\n"
        f"{EMOJIS['gift']} Приз: {prize_safe}\n\n"
        f"{description_line}"
        f"Диапазон: от {min_val} до {max_val}\n"
        f"{EMOJIS['star']} Цена: {stars} звёзд\n"
        f"⏳ Время на игру: {duration_label}\n\n"
        f"Напишите число — первый угадавший побеждает!"
    )

    try:
        await bot.pin_chat_message(
            message.chat.id, start_message.message_id, disable_notification=False
        )
    except Exception:
        pass  # боту может не хватать прав на закрепление сообщений в этом чате

    active_number_games[message.chat.id] = {
        "prize": prize_safe,
        "description": description_safe,
        "stars": stars,
        "min": min_val,
        "max": max_val,
        "secret": secret_number,
        "duration": duration,
        "end_time": time.time() + duration,
        "countdown_sent": set(),
        "task": None,
        "pinned_message_id": start_message.message_id,
    }
    active_number_games[message.chat.id]["task"] = asyncio.create_task(
        number_game_watcher(message.chat.id)
    )
    stats_start(message.chat.id, "guess", prize_safe)


@router.message(Command("farm"))
async def cmd_farm(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Эту команду нужно использовать в групповом чате.")
        return

    if not db_is_chat_whitelisted(message.chat.id):
        await message.answer("Этот чат не в вайтлисте. Попросите админа бота добавить его командой /add_chat.")
        return

    if not db_is_admin(message.from_user.id):
        await message.answer("Запускать фарм могут только админы бота.")
        return

    if message.chat.id in active_farms:
        await message.answer(
            "В этом чате фарм уже запущен. Чтобы остановить его — /stop_farm, "
            "чтобы поменять шанс — /set_chance."
        )
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2 or "|" not in args[1]:
        await message.answer(
            "Использование: /farm &lt;приз1&gt; | &lt;приз2&gt; | ... | &lt;от кого&gt; [шанс]\n"
            "Один приз: /farm Мишка 🧸 | NEV\n"
            "Несколько: /farm Мишка 🧸 | Сердце 💝 | Звезда ⭐ | NEV 0,1\n"
            "Призы можно разделять и запятыми: /farm Мишка, Сердце, Звезда | NEV 0,1\n"
            "Последний элемент — «от кого», после него можно указать шанс (%)."
        )
        return

    segments = [s.strip() for s in args[1].split("|")]
    if len(segments) < 2:
        await message.answer(
            "Не хватает данных. Использование: /farm &lt;приз1&gt; | &lt;приз2&gt; | ... | &lt;от кого&gt; [шанс]"
        )
        return

    giver_part = segments[-1]
    prize_segments = segments[:-1]

    prizes = []
    for seg in prize_segments:
        for piece in seg.split(","):
            piece = piece.strip()
            if piece:
                prizes.append(piece)

    rest_tokens = giver_part.split()
    if not prizes or not rest_tokens:
        await message.answer(
            "Не хватает данных. Использование: /farm &lt;приз1&gt; | &lt;приз2&gt; | ... | &lt;от кого&gt; [шанс]"
        )
        return

    giver = rest_tokens[0]
    chance = None
    if len(rest_tokens) >= 2:
        chance = _parse_chance(rest_tokens[1])
        if chance is None:
            await message.answer(
                f"Не удалось распознать шанс. Укажите число от {MIN_FARM_CHANCE} до {MAX_FARM_CHANCE} "
                f"(например: 0,1 или 5 или 100)."
            )
            return

    if chance is None:
        chance = farm_chance_settings.get(message.chat.id, DEFAULT_FARM_CHANCE)

    prizes_safe = [html.escape(p) for p in prizes]
    giver_safe = html.escape(giver)

    active_farms[message.chat.id] = {
        "prize": ", ".join(prizes_safe),
        "prizes": prizes_safe,
        "giver": giver_safe,
        "chance": chance,
    }
    farm_chance_settings[message.chat.id] = chance

    if len(prizes_safe) == 1:
        prizes_line = prizes_safe[0]
    else:
        prizes_line = "\n" + "\n".join(
            f"   {i}. {p}" for i, p in enumerate(prizes_safe, 1)
        )
    await message.answer(
        f"{EMOJIS['alert']} Фарм запущен!\n"
        f"{EMOJIS['gift']} Призы ({len(prizes_safe)}) от {giver_safe}:{prizes_line}\n"
        f"🎲 Шанс дропа: {chance}%\n"
        f"🎁 При дропе выпадает случайный приз из списка\n\n"
        f"Подарки дарятся за активность — пишите сообщения в чате, и получайте шанс мгновенно выиграть приз!"
    )


@router.message(Command("set_chance"))
async def cmd_set_chance(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Менять шанс дропа могут только админы бота.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            f"Использование: /set_chance &lt;значение&gt;\n"
            f"Диапазон: от {MIN_FARM_CHANCE} до {MAX_FARM_CHANCE} (в процентах).\n"
            f"Например: /set_chance 0,1  или  /set_chance 5  или  /set_chance 100"
        )
        return

    chance = _parse_chance(args[1])
    if chance is None:
        await message.answer(
            f"Не удалось распознать значение. Укажите число от {MIN_FARM_CHANCE} до {MAX_FARM_CHANCE}, "
            f"через точку или запятую, знак % необязателен."
        )
        return

    farm_chance_settings[message.chat.id] = chance
    applied_note = ""
    if message.chat.id in active_farms:
        active_farms[message.chat.id]["chance"] = chance
        applied_note = " Применено к активному фарму сразу."

    await message.answer(f"{EMOJIS['check']} Шанс дропа в этом чате установлен: {chance}%.{applied_note}")


@router.message(Command("stop"))
async def cmd_stop(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Останавливать игру могут только админы бота.")
        return

    stopped_something = False

    if message.chat.id in active_giveaways:
        g = active_giveaways.pop(message.chat.id)
        if g["task"]:
            g["task"].cancel()
        if g.get("pinned_message_id"):
            try:
                await bot.unpin_chat_message(message.chat.id, g["pinned_message_id"])
            except Exception:
                pass
        await message.answer("Ивент остановлен без победителя.")
        await message.answer(build_giveaway_report(message.chat.id, g, None))
        await trigger_userbot_price(message.chat.id, 0)
        stopped_something = True

    if message.chat.id in active_number_games:
        g = active_number_games.pop(message.chat.id)
        if g.get("task"):
            g["task"].cancel()
        if g.get("pinned_message_id"):
            try:
                await bot.unpin_chat_message(message.chat.id, g["pinned_message_id"])
            except Exception:
                pass
        await message.answer("Игра «Угадай число» остановлена без победителя.")
        await send_event_report(message.chat.id, "guess", None)
        stopped_something = True

    if not stopped_something:
        await message.answer("В этом чате сейчас нет активной игры.")


@router.message(Command("stop_farm"))
async def cmd_stop_farm(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Останавливать фарм могут только админы бота.")
        return
    if message.chat.id not in active_farms:
        await message.answer("В этом чате сейчас нет активного фарма.")
        return
    active_farms.pop(message.chat.id)
    await message.answer("Фарм остановлен.")


@router.message(Command("slots"))
async def cmd_slots(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Эту команду нужно использовать в групповом чате.")
        return

    if not db_is_chat_whitelisted(message.chat.id):
        await message.answer("Этот чат не в вайтлисте. Попросите админа бота добавить его командой /add_chat.")
        return

    if not db_is_admin(message.from_user.id):
        await message.answer("Запускать слоты могут только админы бота.")
        return

    if message.chat.id in active_slots:
        await message.answer(
            "В этом чате уже идёт «Слоты». Сначала остановите её командой /stop_slots."
        )
        return

    if message.chat.id in active_casino:
        await message.answer(
            "В этом чате уже идёт ивент «Казино». Сначала остановите его командой /stop_casino."
        )
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Использование: /slots &lt;приз&gt; | &lt;описание&gt;\n"
            "Например: /slots https://t.me/nft/LunarSnake-16936\n"
            "С описанием: /slots Подарок | Крути слоты и лови джекпот 777!"
        )
        return

    raw = args[1]
    if "|" in raw:
        prize_part, description_part = raw.split("|", 1)
        description = description_part.strip() or None
    else:
        prize_part = raw
        description = None

    prize = prize_part.strip()
    if not prize:
        await message.answer("Не указан приз. Использование: /slots &lt;приз&gt; | &lt;описание&gt;")
        return

    prize_safe = html.escape(prize)
    description_safe = html.escape(description) if description else None
    description_line = f"{EMOJIS['note']} {description_safe}\n\n" if description_safe else ""

    stats_start(message.chat.id, "slots", prize_safe)
    active_slots[message.chat.id] = {
        "prize": prize_safe,
        "description": description_safe,
        "started_by": fmt_user(message.from_user),
        "pinned_message_id": None,
    }

    start_message = await message.answer(
        f"{EMOJIS['game']} <b>🎰 СЛОТЫ ЗАПУЩЕНЫ 🎰</b>\n"
        f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n\n"
        f"{EMOJIS['gift']} <b>Приз:</b> {prize_safe}\n\n"
        f"{description_line}"
        f"{EMOJIS['fire']} Отправь эмодзи 🎰 отдельным сообщением в чат — и лови джекпот {EMOJIS['crown']} <b>{EMOJIS['seven']}{EMOJIS['seven']}{EMOJIS['seven']}</b> {EMOJIS['crown']}\n"
        f"{EMOJIS['loading']} Я слежу за каждым броском и сам объявлю победителя.\n\n"
        f"{EMOJIS['star']} Каждый бросок — твой шанс сорвать куш. Удачи! {EMOJIS['party']}"
    )

    try:
        await bot.pin_chat_message(message.chat.id, start_message.message_id, disable_notification=False)
        active_slots[message.chat.id]["pinned_message_id"] = start_message.message_id
    except Exception:
        pass  # боту может не хватать прав на закрепление сообщений в этом чате


@router.message(Command("spin"))
async def cmd_spin(message: Message):
    """Слоты теперь крутят сами игроки — присылая эмодзи 🎰 обычным
    сообщением (Telegram сам анимированно бросает барабан на его стороне).
    Бот ничего не бросает — команда /spin оставлена только как подсказка,
    как правильно играть."""
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Крутить слоты можно только в групповом чате.")
        return

    event = active_slots.get(message.chat.id)
    if not event:
        await message.answer("Сейчас в этом чате никто не крутит слоты. Попросите админа запустить /slots.")
        return

    await message.answer(
        f"{EMOJIS['fire']} Просто отправь эмодзи 🎰 отдельным сообщением в чат — "
        f"крутить будешь сам, а я слежу за результатом и объявлю, если выпадет "
        f"джекпот {EMOJIS['crown']} {EMOJIS['seven']}{EMOJIS['seven']}{EMOJIS['seven']} {EMOJIS['crown']}."
    )


async def maybe_check_slot_spin(message: Message) -> None:
    """Если в чате идёт активная игра «Слоты» и участник сам отправил в чат
    эмодзи-кубик 🎰 обычным сообщением — Telegram сам анимированно крутит
    барабан на своей стороне и присылает боту результат в message.dice.
    Бот НИЧЕГО не бросает сам — он только следит за такими сообщениями от
    игроков и объявляет победителя, если выпал джекпот (три семёрки)."""
    event = active_slots.get(message.chat.id)
    if not event:
        return
    if message.dice is None or message.dice.emoji != "🎰":
        return  # не бросок слотов — не наше сообщение
    if message.sender_chat is not None:
        return  # анонимный пост канала — не реальный участник
    if getattr(message, "forward_origin", None) is not None:
        return  # пересланное сообщение — значение кубика зафиксировано с
        # исходного броска, иначе один честный джекпот можно пересылать
        # сколько угодно раз

    user = message.from_user
    if user is None or user.id == 777000:
        return
    if await is_chat_admin(message.chat.id, user.id):
        return  # владелец и админы чата не участвуют в слотах

    try:
        if await db_run(db_is_ignored, message.chat.id, user.id):
            return  # человек в игнор-листе этого чата
    except Exception:
        logging.exception("db_is_ignored недоступна — пропускаем проверку, не блокируем игру")

    if message.dice.value != SLOTS_JACKPOT_VALUE:
        return  # не джекпот — ждём следующего броска

    # Джекпот! Забираем приз атомарно через pop, чтобы при гонке (два игрока
    # выбили 777 почти одновременно) выиграл только тот, чей pop сработал первым.
    won_event = active_slots.pop(message.chat.id, None)
    if won_event is None:
        return  # приз уже забрал кто-то другой долями секунды раньше

    pinned_id = won_event.get("pinned_message_id")
    if pinned_id:
        try:
            await bot.unpin_chat_message(message.chat.id, pinned_id)
        except Exception:
            pass  # уже откреплено вручную или боту не хватает прав

    description_line = (
        f"{EMOJIS['note']} {won_event['description']}\n" if won_event.get("description") else ""
    )
    await message.reply(
        f"{EMOJIS['crown']} <b>🎰 ДЖЕКПОТ {EMOJIS['seven']}{EMOJIS['seven']}{EMOJIS['seven']}! 🎰</b> {EMOJIS['crown']}\n"
        f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n\n"
        f"{EMOJIS['trophy']} {fmt_user(user)} сорвал куш и забирает приз!\n"
        f"{EMOJIS['gift']} Приз: {won_event['prize']}\n"
        f"{description_line}\n"
        f"{EMOJIS['party']} Поздравляем победителя! {EMOJIS['party']}"
    )
    await send_event_report(message.chat.id, "slots", fmt_user(user))


async def maybe_check_casino_spin(message: Message) -> None:
    """Если в чате идёт активный ивент «Казино» и участник сам отправил в
    чат эмодзи-кубик 🎰 обычным сообщением, проверяем выпавшую комбинацию.
    Бар, Виноград, Лимон и тройная семёрка (777) — любая из них даёт +1
    очко к личному счёту игрока. Бот ничего не бросает сам, только слушает
    такие сообщения от игроков (см. SLOT_COMBOS)."""
    event = active_casino.get(message.chat.id)
    if not event:
        return
    if message.dice is None or message.dice.emoji != "🎰":
        return  # не бросок слотов — не наше сообщение
    if message.sender_chat is not None:
        return  # анонимный пост канала — не реальный участник
    if getattr(message, "forward_origin", None) is not None:
        return  # пересланное сообщение — значение кубика зафиксировано с
        # исходного броска, иначе можно накрутить очки, пересылая один и
        # тот же выигрышный бросок

    user = message.from_user
    if user is None or user.id == 777000:
        return
    if await is_chat_admin(message.chat.id, user.id):
        return  # владелец и админы чата не участвуют в казино

    try:
        if await db_run(db_is_ignored, message.chat.id, user.id):
            return  # человек в игнор-листе этого чата
    except Exception:
        logging.exception("db_is_ignored недоступна — пропускаем проверку, не блокируем очко")

    combo = SLOT_COMBOS.get(message.dice.value)
    if combo is None:
        return  # выпала комбинация, которая не даёт очков

    emoji, label = combo

    scores = event["scores"]
    entry = scores.setdefault(user.id, {"name": fmt_user(user), "points": 0})
    entry["name"] = fmt_user(user)  # обновляем на случай смены ника/юзернейма
    entry["points"] += 1
    points = entry["points"]

    try:
        await db_run(db_casino_save_score, message.chat.id, user.id, entry["name"], points)
    except Exception:
        logging.exception("Не удалось сохранить очко «Казино» в БД — очко всё равно засчитано в памяти")

    rank = 1 + sum(1 for other in scores.values() if other["points"] > points)

    await message.reply(
        f"{emoji} <b>{label}</b> — +1 очко, {entry['name']}!\n"
        f"Всего у тебя: <b>{points}</b> {_ru_points_word(points)} · {rank} место"
    )


@router.message(Command("stop_slots"))
async def cmd_stop_slots(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Останавливать слоты могут только админы бота.")
        return
    event = active_slots.get(message.chat.id)
    if not event:
        await message.answer("В этом чате сейчас нет активной игры «Слоты».")
        return

    pinned_id = event.get("pinned_message_id")
    if pinned_id:
        try:
            await bot.unpin_chat_message(message.chat.id, pinned_id)
        except Exception:
            pass  # уже откреплено вручную или боту не хватает прав

    active_slots.pop(message.chat.id)
    await message.answer("«Слоты» остановлены.")
    await send_event_report(message.chat.id, "slots", None)


@router.message(Command("casino"))
async def cmd_casino(message: Message):
    """Запускает ивент «Казино» — расширенную версию слотов с очками. Игроки
    сами кидают эмодзи 🎰 в чат; за 777, Бар, Виноград или Лимон начисляется
    +1 очко. По истечении времени топ-3 по очкам забирают призы."""
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Эту команду нужно использовать в групповом чате.")
        return

    if not db_is_chat_whitelisted(message.chat.id):
        await message.answer("Этот чат не в вайтлисте. Попросите админа бота добавить его командой /add_chat.")
        return

    if not db_is_admin(message.from_user.id):
        await message.answer("Запускать ивент могут только админы бота.")
        return

    if message.chat.id in active_casino:
        await message.answer(
            "В этом чате уже идёт ивент «Казино». Сначала остановите его командой /stop_casino."
        )
        return

    if message.chat.id in active_slots:
        await message.answer(
            "В этом чате уже идёт «Слоты». Сначала остановите её командой /stop_slots."
        )
        return

    usage = (
        "Использование: /casino &lt;время&gt; | &lt;приз 1 место&gt; | &lt;приз 2 место&gt; | ...\n"
        "Призов можно указать сколько угодно — хоть один, хоть десять.\n"
        "Время можно указывать в минутах (по умолчанию), секундах (30s / 30с) или часах (1h / 1ч).\n"
        "Например:\n"
        "/casino 5h | http://t.me/nft/FinePen-2990 | http://t.me/nft/SurgeBoard-5960 | http://t.me/nft/BowTie-35707"
    )

    args = message.text.split(maxsplit=1)
    if len(args) < 2 or "|" not in args[1]:
        await message.answer(usage)
        return

    parts = [p.strip() for p in args[1].split("|")]
    duration_token, prize_tokens = parts[0], [p for p in parts[1:] if p]

    duration = _parse_duration(duration_token)
    if duration is None:
        await message.answer(f"Не удалось распознать время. {usage}")
        return

    if not prize_tokens:
        await message.answer(f"Нужно указать хотя бы один приз. {usage}")
        return

    prizes = [html.escape(p) for p in prize_tokens]

    duration_label = _format_duration(duration)
    prize_lines = render_prize_lines(prizes)

    start_message = await message.answer(
        f"{EMOJIS['crown']} <b>КАЗИНО-ИВЕНТ ЗАПУЩЕН</b> {EMOJIS['crown']}\n"
        f"{EMOJIS['loading']} Активен: {duration_label}\n\n"
        f"{EMOJIS['star']} <b>Как играть</b>\n"
        f"Кидай эмодзи 🎰 прямо в чат — барабан крутит сам Telegram.\n"
        f"Собрал {EMOJIS['bar']}{EMOJIS['bar']}{EMOJIS['bar']}, {EMOJIS['grape']}{EMOJIS['grape']}{EMOJIS['grape']}, {EMOJIS['lemon']}{EMOJIS['lemon']}{EMOJIS['lemon']} или {EMOJIS['seven']}{EMOJIS['seven']}{EMOJIS['seven']} — заносим +1 очко в зачёт.\n\n"
        f"{EMOJIS['gift']} <b>Награда победителям</b>\n"
        f"{prize_lines}\n\n"
        f"{EMOJIS['check']} Рейтинг обновляется прямо в чате каждые 15 минут"
    )

    try:
        await bot.pin_chat_message(message.chat.id, start_message.message_id, disable_notification=False)
    except Exception:
        pass  # боту может не хватать прав на закрепление сообщений в этом чате

    stats_start(message.chat.id, "casino", ", ".join(html.escape(str(x)) for x in prizes[:1]))
    active_casino[message.chat.id] = {
        "end_time": time.time() + duration,
        "duration": duration,
        "prizes": prizes,
        "scores": {},  # user_id -> {"name": str, "points": int}
        "warned_30s": False,
        "countdown_sent": set(),
        "pinned_message_id": start_message.message_id,
        # id последнего закреплённого периодического напоминания (обновляется
        # раз в CASINO_STATS_INTERVAL_SECONDS в casino_stats_loop)
        "reminder_pinned_id": None,
    }
    active_casino[message.chat.id]["task"] = asyncio.create_task(casino_watcher(message.chat.id))
    active_casino[message.chat.id]["stats_task"] = asyncio.create_task(casino_stats_loop(message.chat.id))

    # Сохраняем на диск сразу при старте — если процесс перезапустят/передеплоят,
    # restore_casino_events() при следующем старте бота поднимет ивент заново.
    try:
        await db_run(
            db_casino_save_event,
            message.chat.id,
            active_casino[message.chat.id]["end_time"],
            duration,
            prizes,
            start_message.message_id,
        )
    except Exception:
        logging.exception("Не удалось сохранить старт ивента «Казино» в БД")


@router.message(Command("box777"))
async def cmd_box777(message: Message):
    """Запускает ивент «777»: публикуется короткое объявление и общее поле
    из BOX777_SLOTS ячеек. Раскрыть одну случайную ещё закрытую ячейку
    можно, выбив джекпот (777) в /slots-броске — см. maybe_check_box777_spin."""
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Эту команду нужно использовать в групповом чате.")
        return

    if not db_is_chat_whitelisted(message.chat.id):
        await message.answer("Этот чат не в вайтлисте. Попросите админа бота добавить его командой /add_chat.")
        return

    if not db_is_admin(message.from_user.id):
        await message.answer("Запускать ивент могут только админы бота.")
        return

    if message.chat.id in active_box777:
        await message.answer(
            "В этом чате уже идёт ивент «777». Сначала остановите его командой /stop_box777."
        )
        return
    if message.chat.id in active_slots:
        await message.answer(
            "В этом чате уже идёт «Слоты». Сначала остановите её командой /stop_slots."
        )
        return
    if message.chat.id in active_casino:
        await message.answer(
            "В этом чате уже идёт ивент «Казино». Сначала остановите его командой /stop_casino."
        )
        return

    usage = (
        "Использование: /box777 &lt;NFT приз&gt; | &lt;дешёвые призы через запятую&gt; | &lt;кол-во пустых ячеек&gt;\n"
        "Второй и третий параметр можно не указывать — тогда поле соберётся по умолчанию: "
        "12 🧸 Мишка, 8 🌹 Роза, 3 🚀 Ракета и 1 NFT — совсем без пустых ячеек.\n"
        f"Всего ячеек: {BOX777_SLOTS} (значит, кол-во пустых — число от 0 до {BOX777_SLOTS - 1}).\n"
        "Например:\n"
        "/box777 Bonded Ring #5048 | 15⭐, 25⭐, 50⭐ | 10"
    )
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(usage)
        return

    parts = args[1].split("|")
    nft_part = parts[0]
    cheap_part = parts[1] if len(parts) > 1 else ""
    empty_part = parts[2] if len(parts) > 2 else ""

    cheap_prizes = [p.strip() for p in cheap_part.split(",") if p.strip()]

    nft_prize = nft_part.strip()
    if not nft_prize:
        await message.answer(f"Не указан NFT-приз. {usage}")
        return

    empty_count = None
    if empty_part.strip():
        try:
            empty_count = int(empty_part.strip())
        except ValueError:
            await message.answer(f"Количество пустых ячеек должно быть числом. {usage}")
            return
        if empty_count < 0 or empty_count > BOX777_SLOTS - 1:
            await message.answer(
                f"Количество пустых ячеек должно быть от 0 до {BOX777_SLOTS - 1}."
            )
            return

    nft_prize_safe = html.escape(nft_prize)
    cheap_prizes_safe = [html.escape(p) for p in cheap_prizes] if cheap_prizes else None

    stats_start(message.chat.id, "box777", nft_prize_safe)
    active_box777[message.chat.id] = {
        "nft_prize": nft_prize_safe,
        "cheap_prizes": cheap_prizes_safe,
        "empty_count": empty_count,
        "started_by": fmt_user(message.from_user),
        "pinned_message_id": None,
        "message_id": None,
        "boards": {},
        "finished": False,
    }

    start_message = await message.answer(
        f"{EMOJIS['crown']} <b>ИВЕНТ «777»</b> {EMOJIS['crown']}\n\n"
        f"{EMOJIS['bar']} Кидай эмодзи 🎰 в чат!\n"
        f"{EMOJIS['seven']} Собери {EMOJIS['seven']}{EMOJIS['seven']}{EMOJIS['seven']} — и прямо в ответ на твой бросок появится поле, где можно открыть любую ячейку.\n\n"
        f"{EMOJIS['trophy']} <b>NFT:</b> {nft_prize_safe}\n\n"
        f"{EMOJIS['check']} Приз выдаёт админ вручную. Удачи! {EMOJIS['party']}"
    )
    active_box777[message.chat.id]["message_id"] = start_message.message_id

    try:
        await bot.pin_chat_message(message.chat.id, start_message.message_id, disable_notification=False)
        active_box777[message.chat.id]["pinned_message_id"] = start_message.message_id
    except Exception:
        pass  # боту может не хватать прав на закрепление сообщений в этом чате


async def maybe_check_box777_spin(message: Message) -> None:
    """Если в чате идёт активный ивент «777» и участник сам отправил в чат
    эмодзи-кубик 🎰 обычным сообщением — при выпадении джекпота (три
    семёрки) он получает право САМ открыть одну любую ещё закрытую ячейку
    на общем поле (кнопкой, см. cb_box777_reveal). Право одноразовое: одно
    попадание 777 — одно открытие."""
    state = active_box777.get(message.chat.id)
    if not state or state.get("finished"):
        return
    if message.dice is None or message.dice.emoji != "🎰":
        return  # не бросок слотов — не наше сообщение
    if message.sender_chat is not None:
        return  # анонимный пост канала — не реальный участник
    if getattr(message, "forward_origin", None) is not None:
        return  # пересланное сообщение — значение кубика зафиксировано с
        # исходного броска (Telegram не переигрывает анимацию при
        # пересылке), иначе один честный джекпот можно пересылать сколько
        # угодно раз и каждый раз получать новое поле

    user = message.from_user
    if user is None or user.id == 777000:
        return
    if await is_chat_admin(message.chat.id, user.id):
        return  # владелец и админы чата не участвуют в ивенте

    try:
        if await db_run(db_is_ignored, message.chat.id, user.id):
            return  # человек в игнор-листе этого чата
    except Exception:
        logging.exception("db_is_ignored недоступна — пропускаем проверку, не блокируем ивент")

    if message.dice.value != SLOTS_JACKPOT_VALUE:
        return  # не джекпот — ждём следующего броска

    # Пока мы ждали ответ от Telegram — ивент мог быть остановлен или уже
    # завершиться (кто-то нашёл NFT) — перепроверяем перед тем, как менять
    # состояние поля.
    state = active_box777.get(message.chat.id)
    if state is None or state.get("finished"):
        return

    # Каждое попадание джекпота — отдельная независимая попытка со своим
    # полностью новым полем (шанс на NFT всегда 1/24), никак не связанным
    # с полями других игроков или прошлыми попытками этого же игрока.
    board = _new_box777_board(state, user.id)

    try:
        reply_message = await message.reply(
            f"{EMOJIS['fire']} {fmt_user(user)} выбил 777! Открывай {EMOJIS['party']}",
            reply_markup=_box777_keyboard(),
        )
    except Exception:
        return  # не критично, но без сообщения поле негде разместить

    # Перепроверяем ещё раз перед сохранением — ивент мог завершиться, пока
    # мы ждали отправки сообщения.
    state = active_box777.get(message.chat.id)
    if state is None or state.get("finished"):
        try:
            await bot.edit_message_reply_markup(
                message.chat.id, reply_message.message_id, reply_markup=None
            )
        except Exception:
            pass
        return

    state["boards"][reply_message.message_id] = board


@router.callback_query(F.data.startswith("box777:"))
async def cb_box777_reveal(callback: CallbackQuery):
    """Обрабатывает нажатие на одну из ячеек ЛИЧНОГО поля игрока, которое
    появилось у него после попадания 777 (см. maybe_check_box777_spin).
    Открыть можно только своё поле и только одну ячейку — после этого поле
    считается использованным и больше не активно, независимо от результата.
    Каждое поле — отдельная попытка с шансом на NFT 1/24; предыдущие
    открытия (свои или чужие, на этом или на других полях) на неё никак
    не влияют."""
    if callback.data == "box777:done":
        await callback.answer("Это поле уже использовано.")
        return

    try:
        slot = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer()
        return

    chat_id = callback.message.chat.id
    message_id = callback.message.message_id
    user = callback.from_user

    state = active_box777.get(chat_id)
    if state is None or state.get("finished"):
        await callback.answer("Ивент уже завершён.", show_alert=True)
        return

    board = state["boards"].get(message_id)
    if board is None or board["used"]:
        await callback.answer("Это поле уже использовано или устарело.", show_alert=True)
        return
    if board["user_id"] != user.id:
        await callback.answer(
            f"{EMOJIS['bar']} Это поле не твоё — выбей свои 777, чтобы получить своё!", show_alert=True
        )
        return

    # Перепроверяем состояние прямо перед списанием права — вдруг это же
    # поле уже открыли (два быстрых нажатия подряд) или ивент только что
    # завершился.
    state = active_box777.get(chat_id)
    if state is None or state.get("finished"):
        await callback.answer("Ивент уже завершён.", show_alert=True)
        return
    board = state["boards"].get(message_id)
    if board is None or board["used"] or board["user_id"] != user.id:
        await callback.answer("Это поле уже использовано или устарело.", show_alert=True)
        return

    # Списываем право хода атомарно (до этой строки не было ни одного await
    # без немедленной повторной проверки, так что гонка исключена).
    board["used"] = True

    won = slot == board["nft_index"]
    full_label = "" if won else board["full_labels"][slot]

    # Поле одноразовое и после этого хода всё равно выбрасывается — прятать
    # остальные ячейки больше незачем. Раскрываем целиком: каждая ячейка
    # показывает, что в ней реально было, а если игрок не попал на NFT —
    # её ячейка тоже подсвечивается отдельным значком, чтобы было видно,
    # где она пряталась.
    revealed: dict[int, str] = {}
    for i in range(BOX777_SLOTS):
        if i == slot:
            revealed[i] = _BOX777_WIN_LABEL if won else board["short_labels"][slot]
        elif i == board["nft_index"]:
            revealed[i] = _BOX777_NFT_MISSED_LABEL
        else:
            revealed[i] = board["short_labels"][i]

    if not won and full_label:
        # Приз найден (не NFT) — раньше сюда отдельным сообщением в чат
        # писалось "{user} забрал {приз} / Подарок отправлен". Теперь вместо
        # нового сообщения просто дописываем результат прямо в то сообщение,
        # на котором открывали ячейку, и тем же вызовом сразу проставляем
        # раскрытую клавиатуру — кнопки остаются на месте.
        original_text = f"{EMOJIS['fire']} {fmt_user(user)} выбил 777! Открывай {EMOJIS['party']}"
        new_text = (
            f"{original_text}\n\n"
            f"{EMOJIS['party']} {fmt_user(user)} забрал {full_label}\n"
            f"{EMOJIS['check']} Подарок будет отправлен в ближайшее время (от NEV)"
        )
        try:
            await callback.message.edit_text(new_text, reply_markup=_box777_keyboard(revealed))
        except Exception:
            # запасной вариант, если редактирование текста не удалось —
            # хотя бы раскрываем клавиатуру, как раньше
            try:
                await callback.message.edit_reply_markup(reply_markup=_box777_keyboard(revealed))
            except Exception:
                pass  # сообщение могли удалить вручную — не критично
    else:
        try:
            await callback.message.edit_reply_markup(reply_markup=_box777_keyboard(revealed))
        except Exception:
            pass  # сообщение могли удалить вручную — не критично

    if won:
        active_box777.pop(chat_id, None)
        try:
            win_message = await bot.send_message(
                chat_id,
                f"{EMOJIS['trophy']} <b>NFT найден, ивент завершён</b>\n\n"
                f"{fmt_user(user)} открыл ячейку №{slot + 1} и забрал:\n"
                f"{state['nft_prize']}\n\n"
                f"Приз выдаёт админ вручную — ожидайте",
                reply_to_message_id=state.get("message_id"),
            )
            await bot.pin_chat_message(chat_id, win_message.message_id, disable_notification=False)
        except Exception:
            pass  # сообщение могли удалить вручную, либо не хватает прав на закреп

        pinned_id = state.get("pinned_message_id")
        if pinned_id:
            try:
                await bot.unpin_chat_message(chat_id, pinned_id)
            except Exception:
                pass  # уже откреплено вручную или боту не хватает прав

        await callback.answer(f"{EMOJIS['party']} Поздравляем, это NFT!", show_alert=True)
        await send_event_report(chat_id, "box777", fmt_user(user))
        return

    if full_label:
        await callback.answer(f"{EMOJIS['gift']} Твой приз: {full_label}")
    else:
        await callback.answer(f"{EMOJIS['cross']} Пусто — повезёт в следующий раз!")


@router.message(Command("stop_box777"))
async def cmd_stop_box777(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Останавливать ивент могут только админы бота.")
        return

    state = active_box777.pop(message.chat.id, None)
    if state is None:
        await message.answer("В этом чате сейчас нет активного ивента «777».")
        return

    pinned_id = state.get("pinned_message_id")
    if pinned_id:
        try:
            await bot.unpin_chat_message(message.chat.id, pinned_id)
        except Exception:
            pass  # уже откреплено вручную или боту не хватает прав

    await message.answer(f"{EMOJIS['block']} Ивент «777-бокс» остановлен админом.")
    await send_event_report(message.chat.id, "box777", None)


@router.message(Command("casino_top"))
async def cmd_casino_top(message: Message):
    event = active_casino.get(message.chat.id)
    if not event:
        await message.answer("В этом чате сейчас нет активного ивента «Казино».")
        return
    await message.answer(render_casino_leaderboard(event))


@router.message(Command("stop_casino"))
async def cmd_stop_casino(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Останавливать «Казино» могут только админы бота.")
        return
    event = active_casino.get(message.chat.id)
    if not event:
        await message.answer("В этом чате сейчас нет активного ивента «Казино».")
        return

    task = event.get("task")
    if task:
        task.cancel()
    stats_task = event.get("stats_task")
    if stats_task:
        stats_task.cancel()

    pinned_id = event.get("pinned_message_id")
    if pinned_id:
        try:
            await bot.unpin_chat_message(message.chat.id, pinned_id)
        except Exception:
            pass

    active_casino.pop(message.chat.id, None)
    try:
        await db_run(db_casino_delete_event, message.chat.id)
    except Exception:
        logging.exception("Не удалось удалить остановленный ивент «Казино» из БД")
    await message.answer("Ивент «Казино» остановлен.")
    await send_event_report(message.chat.id, "casino", None)


@router.message(Command("set_promo"))
async def cmd_set_promo(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Настраивать промо-комментарий могут только админы бота.")
        return

    photo_id = None
    raw_text = None

    if message.photo:
        photo_id = message.photo[-1].file_id
        if message.caption:
            parts = message.caption.split(maxsplit=1)
            raw_text = parts[1] if len(parts) > 1 else None
    else:
        if message.reply_to_message and message.reply_to_message.photo:
            photo_id = message.reply_to_message.photo[-1].file_id
        if message.text:
            parts = message.text.split(maxsplit=1)
            raw_text = parts[1] if len(parts) > 1 else None

    if not raw_text:
        await message.answer(
            "Использование:\n"
            "• /set_promo &lt;текст&gt; — текстовый комментарий\n"
            "• Отправьте фото с подписью «/set_promo &lt;текст&gt;» — комментарий с картинкой\n"
            "• Или ответьте на фото командой «/set_promo &lt;текст&gt;» — картинка возьмётся из ответа\n\n"
            "Команду нужно выполнять в группе обсуждений канала (куда Telegram пересылает "
            "посты канала для комментариев) — именно туда бот будет присылать этот комментарий."
        )
        return

    promo_html = f"<blockquote>{html.escape(raw_text)}</blockquote>"
    db_set_promo(message.chat.id, promo_html, photo_id)

    photo_note = " (с картинкой)" if photo_id else ""
    await message.answer(
        f"{EMOJIS['check']} Промо-комментарий сохранён{photo_note}. Теперь бот будет "
        f"присылать его под каждым новым постом канала, приходящим в этот чат."
    )


@router.message(Command("show_promo"))
async def cmd_show_promo(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Доступно только админам бота.")
        return
    promo = db_get_promo(message.chat.id)
    if promo is None:
        await message.answer("Сейчас используется текст по умолчанию:")
        await message.answer(DEFAULT_PROMO_TEXT)
        return
    text, photo_id = promo
    if not text:
        await message.answer("Промо-комментарий отключён командой /clear_promo — под постами ничего не появляется.")
        return
    if photo_id:
        await message.answer_photo(photo_id, caption=text)
    else:
        await message.answer(text)


@router.message(Command("clear_promo"))
async def cmd_clear_promo(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Доступно только админам бота.")
        return
    promo = db_get_promo(message.chat.id)
    if promo is not None and not promo[0]:
        await message.answer("Промо-комментарий и так уже отключён.")
        return
    db_set_promo(message.chat.id, "", None)
    await message.answer(
        "Промо-комментарий отключён — под новыми постами больше ничего не появится "
        "(даже стандартный текст). Чтобы включить заново — /set_promo &lt;текст&gt;."
    )


async def send_invisible_tagall(chat_id: int, intro: str, user_ids: list, reply_to_message_id: int = None) -> int:
    """Рассылает невидимые упоминания <a href="tg://user?id=...">символ</a>
    от имени БОТА — создаёт настоящее упоминание (придёт пуш). Строка intro
    видна в КАЖДОМ сообщении-куске (не только в первом), чтобы человек,
    получивший пуш и открывший чат, сразу понимал, зачем его затегали, а не
    видел почти пустое сообщение. Если передан reply_to_message_id — каждый
    кусок отправляется ответом на это сообщение (тег под конкретным постом).
    Возвращает число реально протегованных."""
    CHUNK_SIZE = 80  # ограничение на число entity в одном сообщении Telegram
    sent_count = 0
    for i in range(0, len(user_ids), CHUNK_SIZE):
        chunk = user_ids[i : i + CHUNK_SIZE]
        invisible_mentions = "".join(
            f'<a href="tg://user?id={uid}">\u2800</a>' for uid in chunk
        )
        text = f"{intro}\n{invisible_mentions}"
        try:
            if reply_to_message_id:
                await bot.send_message(chat_id, text, reply_to_message_id=reply_to_message_id)
            else:
                await bot.send_message(chat_id, text)
        except Exception:
            pass  # Telegram мог отклонить часть — например, флуд-контроль или само сообщение удалили
        sent_count += len(chunk)
        if i + CHUNK_SIZE < len(user_ids):
            await asyncio.sleep(1.5)  # не попасть под антифлуд Telegram
    return sent_count


@router.message(Command("tag_all"))
async def cmd_tag_all(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Эту команду нужно использовать в групповом чате.")
        return

    if not db_is_chat_whitelisted(message.chat.id):
        await message.answer("Этот чат не в вайтлисте. Попросите админа бота добавить его командой /add_chat.")
        return

    if not db_is_admin(message.from_user.id):
        await message.answer("Массово тегать могут только админы бота.")
        return

    args = message.text.split(maxsplit=1)
    intro = args[1].strip() if len(args) > 1 else f"{EMOJIS['alert']} Новый ивент в чате! Залетайте скорее!"

    # Если /tag_all отправили ответом на чьё-то сообщение — теги уходят
    # тоже ответом на это сообщение, чтобы все увидели именно его.
    reply_to_message_id = message.reply_to_message.message_id if message.reply_to_message else None

    if db_is_userbot_sync_enabled(message.chat.id):
        # Юзербот подключён — просим у него ПОЛНЫЙ список участников через
        # MTProto (Bot API такого не отдаёт даже боту-админу), но саму
        # рассылку с тегами всё равно делает бот, а не аккаунт юзербота.
        waiting_msg = await message.answer(
            "Запрашиваю у юзербота полный список участников… обычно занимает пару секунд."
        )
        user_ids = await request_all_participant_ids(message.chat.id)
        if not user_ids:
            await waiting_msg.edit_text(
                f"{EMOJIS['cross']} Юзербот не ответил за 30 секунд. Проверь: он в этом чате, "
                f"модуль paid_price.py загружен в Hikka, и он привязан через /set_userbot "
                f"(должен был хотя бы раз написать /start этому боту)."
            )
            return
        sent_count = await send_invisible_tagall(message.chat.id, intro, user_ids, reply_to_message_id)
        await waiting_msg.edit_text(
            f"{EMOJIS['check']} Готово — невидимо уведомил {sent_count} чел. "
            f"(полный список участников, не только писавшие)."
        )
        return

    user_ids = db_list_known_user_ids(message.chat.id)
    if not user_ids:
        await message.answer(
            "Бот пока никого не запомнил в этом чате — люди должны хотя бы раз написать "
            "сообщение, чтобы бот начал их учитывать для /tag_all.\n\n"
            "Чтобы тегать вообще ВСЕХ участников (даже тех, кто не писал) — подключи "
            "юзербота через /enable_paid_sync и /set_userbot (см. описание в документации по ним)."
        )
        return

    sent_count = await send_invisible_tagall(message.chat.id, intro, user_ids, reply_to_message_id)
    await message.answer(
        f"{EMOJIS['check']} Готово — невидимо уведомил {sent_count} чел., кого бот "
        f"видел пишущими в этом чате."
    )


@router.message(Command("enable_paid_sync"))
async def cmd_enable_paid_sync(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Доступно только админам бота.")
        return
    if db_is_userbot_sync_enabled(message.chat.id):
        await message.answer("Синк с юзерботом в этом чате уже включён.")
        return
    db_enable_userbot_sync(message.chat.id)
    await message.answer(
        f"{EMOJIS['check']} Синк с юзерботом включён для этого чата.\n\n"
        f"Требования: в этом чате должен быть твой Hikka-юзербот с загруженным "
        f"модулем paid_price.py и правами админа (менять инфо о группе + удаление сообщений). "
        f"Теперь:\n"
        f"• /giveaway с ценой в звёздах автоматически включает реальные платные "
        f"сообщения в чате на время ивента и выключает по завершении;\n"
        f"• /tag_all будет тегать вообще ВСЕХ участников чата (не только писавших), "
        f"через юзербота."
    )


@router.message(Command("disable_paid_sync"))
async def cmd_disable_paid_sync(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Доступно только админам бота.")
        return
    if not db_is_userbot_sync_enabled(message.chat.id):
        await message.answer("Синк с юзерботом в этом чате и так не включён.")
        return
    db_disable_userbot_sync(message.chat.id)
    await message.answer("Синк с юзерботом отключён для этого чата.")


@router.message(Command("set_userbot"))
async def cmd_set_userbot(message: Message):
    if not db_is_admin(message.from_user.id):
        await message.answer("Доступно только админам бота.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().lstrip("-").isdigit():
        current = db_get_setting("userbot_account_id")
        note = f"\nСейчас привязан: {current}" if current else "\nСейчас не привязан."
        await message.answer(
            "Использование: /set_userbot &lt;telegram id аккаунта юзербота&gt;\n"
            "Узнать свой id можно, например, у @userinfobot." + note
        )
        return

    userbot_id = args[1].strip()
    db_set_setting("userbot_account_id", userbot_id)

    warning = ""
    try:
        await bot.send_message(int(userbot_id), "✅ Этот аккаунт привязан как юзербот для тега всех участников.")
    except Exception:
        warning = (
            f"\n\n{EMOJIS['cross']} Не удалось написать этому id в личку — скорее всего, "
            f"аккаунт ни разу не запускал этого бота командой /start. Запусти /start от "
            f"его имени, иначе /tag_all не сможет получить от него ответ."
        )

    await message.answer(f"{EMOJIS['check']} Юзербот привязан: {userbot_id}{warning}")


@router.message(F.chat.type == ChatType.PRIVATE)
async def on_private_message(message: Message):
    """Ловит ответы юзербота (Hikka) со списком участников для /tag_all —
    юзербот присылает их себе в личку с ботом, отдельно от группы."""
    userbot_id = db_get_setting("userbot_account_id")
    if not userbot_id or message.from_user is None or str(message.from_user.id) != str(userbot_id):
        return
    text = message.text or ""
    if not text.startswith(USERBOT_TAGALL_RESPONSE_PREFIX):
        return

    lines = text.split("\n", 1)
    header_parts = lines[0].split()
    if len(header_parts) != 3:
        return
    _, chat_id_str, part_info = header_parts
    try:
        chat_id = int(chat_id_str)
        part_index_str, total_parts_str = part_info.split("/")
        part_index, total_parts = int(part_index_str), int(total_parts_str)
    except ValueError:
        return

    body = lines[1] if len(lines) > 1 else ""
    ids_chunk = [int(x) for x in body.split(",") if x.strip().lstrip("-").isdigit()]

    accumulated = _pending_tagall_partial.setdefault(chat_id, [])
    accumulated.extend(ids_chunk)

    if part_index >= total_parts:
        future = pending_tagall_requests.get(chat_id)
        if future and not future.done():
            future.set_result(accumulated)


# ---------------------------------------------------------------------------
# Логика ивента
# ---------------------------------------------------------------------------


giveaway_send_queues: dict[int, str] = {}
giveaway_queue_tasks: dict[int, asyncio.Task] = {}


async def _giveaway_queue_worker(chat_id: int) -> None:
    try:
        while True:
            text = giveaway_send_queues.get(chat_id)
            if text is None:
                giveaway_queue_tasks.pop(chat_id, None)
                return
            giveaway_send_queues.pop(chat_id, None)
            try:
                await bot.send_message(chat_id, text, disable_notification=True)
            except TelegramRetryAfter as exc:
                giveaway_send_queues[chat_id] = text
                await asyncio.sleep(exc.retry_after + 1)
            except Exception:
                logging.exception("giveaway queued send failed")
            await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        pass


def giveaway_queue_send(chat_id: int, text: str) -> None:
    giveaway_send_queues[chat_id] = text
    if chat_id not in giveaway_queue_tasks or giveaway_queue_tasks[chat_id].done():
        giveaway_queue_tasks[chat_id] = asyncio.create_task(_giveaway_queue_worker(chat_id))


async def giveaway_safe_send(chat_id: int, text: str, silent: bool = False):
    for attempt in range(3):
        try:
            return await bot.send_message(chat_id, text, disable_notification=silent)
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
        except Exception:
            logging.exception("giveaway send failed")
            await asyncio.sleep(1)
    return None


async def giveaway_watcher(chat_id: int):
    try:
        while True:
            g = active_giveaways.get(chat_id)
            if not g:
                return

            remaining = g["end_time"] - time.time()
            if remaining <= 0:
                await finish_giveaway(chat_id)
                return

            if remaining <= 30 and g.get("reset_seconds", 0) > 30 and not g.get("warned_30s"):
                g["warned_30s"] = True
                await giveaway_safe_send(
                    chat_id,
                    f"{EMOJIS['alert']} <b>Осталось 30 секунд!</b>\n"
                    f"Текущий лидер: {g['leader_name'] or 'никто'}. Успей перебить!",
                    silent=True,
                )

            if remaining <= 10 and g.get("reset_seconds", 0) >= 5:
                sec = math.ceil(remaining)
                if sec >= 1 and sec not in g["countdown_sent"]:
                    g["countdown_sent"].add(sec)
                    await giveaway_safe_send(chat_id, f"{EMOJIS['fire']} <b>{sec}</b>", silent=True)
                await asyncio.sleep(min(0.25, max(0.05, remaining)))
            else:
                if g["countdown_sent"]:
                    g["countdown_sent"].clear()
                next_checkpoint = 30 if remaining > 30 else 10
                delay = remaining - next_checkpoint if remaining > next_checkpoint else remaining
                await asyncio.sleep(min(1.0, max(0.05, delay)))
    except asyncio.CancelledError:
        return
    except Exception:
        logging.exception("giveaway watcher crashed")
        await asyncio.sleep(1)
        await finish_giveaway(chat_id)


async def number_game_watcher(chat_id: int):
    """Следит за таймером игры "Угадай число". В последние 10 секунд до
    конца присылает обратный отсчёт 10, 9, 8 ... 1, после чего завершает
    игру без победителя (никто не угадал число вовремя)."""
    try:
        while True:
            g = active_number_games.get(chat_id)
            if not g:
                return

            remaining = g["end_time"] - time.time()

            if remaining <= 0:
                await finish_number_game_timeout(chat_id)
                return

            if remaining <= 10:
                sec = math.ceil(remaining)
                if sec >= 1 and sec not in g["countdown_sent"]:
                    g["countdown_sent"].add(sec)
                    await bot.send_message(chat_id, f"{EMOJIS['fire']} <b>{sec}</b>")
                await asyncio.sleep(0.5)
            else:
                if g["countdown_sent"]:
                    g["countdown_sent"].clear()
                await asyncio.sleep(min(3, remaining - 10))
    except asyncio.CancelledError:
        return


async def finish_number_game_timeout(chat_id: int):
    g = active_number_games.pop(chat_id, None)
    if not g:
        return
    if g.get("pinned_message_id"):
        try:
            await bot.unpin_chat_message(chat_id, g["pinned_message_id"])
        except Exception:
            pass  # уже откреплено вручную или боту не хватает прав
    await bot.send_message(
        chat_id,
        f"⏳ Время вышло! Число ({g['secret']}) никто не угадал в игре «{g['prize']}». Приз сгорел {EMOJIS['cry']}",
    )
    await send_event_report(chat_id, "guess", None)


async def finish_number_game(chat_id: int, winner_id: int, winner_name: str, winner_nickname: str):
    g = active_number_games.pop(chat_id, None)
    if not g:
        return
    if g.get("task"):
        g["task"].cancel()
    db_add_win(chat_id, winner_id, winner_name, winner_nickname)
    if g.get("pinned_message_id"):
        try:
            await bot.unpin_chat_message(chat_id, g["pinned_message_id"])
        except Exception:
            pass  # уже откреплено вручную или боту не хватает прав
    win_message = await bot.send_message(
        chat_id,
        f"{EMOJIS['win']} <b>Число угадано!</b>\n"
        f"Загаданное число было: {g['secret']}\n"
        f"Приз «{g['prize']}» забирает {winner_name}! Поздравляем {EMOJIS['party']}",
    )
    try:
        await bot.pin_chat_message(chat_id, win_message.message_id, disable_notification=False)
    except Exception:
        pass  # боту может не хватать прав на закрепление сообщений в этом чате
    await send_event_report(chat_id, "guess", winner_name)

# ---------------------------------------------------------------------------
# Итоги ивентов: сколько сообщений написали участники и сколько звёзд реально
# списал Telegram (paid_star_count). Работает для «Угадай число», «Казино»,
# «Слоты» и «777-бокс»; для перебива есть свой отчёт (build_giveaway_report).
# ---------------------------------------------------------------------------

EVENT_TITLES = {
    "guess": "Угадай число",
    "casino": "Казино",
    "slots": "Слоты",
    "box777": "777-бокс",
}

# (chat_id, kind) -> {"prize": str, "participants": {user_id: {...}}, "real_price_seen": int|None}
event_stats: dict[tuple[int, str], dict] = {}


def stats_start(chat_id: int, kind: str, prize: str = "") -> None:
    """Начинает подсчёт сообщений и потраченных звёзд для ивента."""
    event_stats[(chat_id, kind)] = {
        "prize": prize,
        "participants": {},
        "real_price_seen": None,
    }


async def stats_record_message(message: Message) -> None:
    """Учитывает сообщение участника во всех активных подсчётах этого чата."""
    chat_id = message.chat.id
    keys = [k for k in event_stats if k[0] == chat_id]
    if not keys:
        return
    if message.content_type not in COUNTABLE_CONTENT_TYPES:
        return
    if message.text and message.text.startswith("/"):
        return
    if message.sender_chat is not None:
        return
    user = message.from_user
    if user is None or user.id == 777000:
        return
    if await is_chat_admin(chat_id, user.id):
        return  # владелец и админы чата не участвуют в ивентах
    try:
        if await db_run(db_is_ignored, chat_id, user.id):
            return
    except Exception:
        logging.exception("db_is_ignored недоступна — продолжаем подсчёт итогов")

    paid = getattr(message, "paid_star_count", None) or 0
    for key in keys:
        st = event_stats.get(key)
        if st is None:
            continue
        part = st["participants"].get(user.id)
        if part is None:
            part = {"name": fmt_user(user), "count": 0, "spent": 0}
            st["participants"][user.id] = part
        part["name"] = fmt_user(user)
        part["count"] += 1
        if paid:
            part["spent"] += paid
            st["real_price_seen"] = paid


def build_event_report(chat_id: int, kind: str, st: dict, winner_text: str | None) -> str:
    participants = st.get("participants", {}) or {}
    total_msgs = sum(p["count"] for p in participants.values())
    total_spent = sum(p.get("spent", 0) for p in participants.values())
    ranked = sorted(participants.values(), key=lambda p: (p.get("spent", 0), p["count"]), reverse=True)

    lines = [f"{EMOJIS['game']} <b>Итог ивента «{EVENT_TITLES.get(kind, kind)}»</b>"]
    if st.get("prize"):
        lines.append(f"Приз: {st['prize']}")
    lines.append(f"Победитель: {winner_text or 'нет (без победителя)'}")
    lines.append(f"Сообщений от участников: {total_msgs}")
    if st.get("real_price_seen") is not None:
        lines.append(f"Цена сообщения: {st['real_price_seen']} зв. (реально списано Telegram)")
    lines.append(f"Всего потрачено (реально): {total_spent} зв.")

    if ranked:
        lines.append("")
        lines.append("По участникам:")
        top_limit = 20
        rows = [f"• {p['name']}: {p.get('spent', 0)} зв. ({p['count']} сообщ.)" for p in ranked[:top_limit]]
        rest = len(ranked) - top_limit
        if rest > 0:
            rows.append(f"• ...и ещё {rest}")
        lines.append("<blockquote expandable>" + "\n".join(rows) + "</blockquote>")
    return "\n".join(lines)


async def send_event_report(chat_id: int, kind: str, winner_text: str | None = None) -> None:
    """Подводит итог подсчёта и отправляет сообщение в чат (один раз на ивент)."""
    st = event_stats.pop((chat_id, kind), None)
    if st is None:
        return
    try:
        await bot.send_message(chat_id, build_event_report(chat_id, kind, st, winner_text))
    except Exception:
        logging.exception("Не удалось отправить итог ивента «%s»", kind)



def build_giveaway_report(chat_id: int, g: dict, winner_name: str | None) -> str:
    """Формирует текстовый отчёт "Итог перебива": сколько всего ставок
    (сообщений) отправлено и сколько звёзд РЕАЛЬНО списал Telegram (поле
    paid_star_count на каждом сообщении, Bot API 9.0+) — а не расчётное
    число по введённой при старте цене. Если в чате платные сообщения не
    были включены (или включились не сразу) — реальная трата будет меньше
    или равна нулю для соответствующих сообщений, это ожидаемо и честно."""
    configured_price = g.get("stars", DEFAULT_STARS_PER_MESSAGE)
    real_price_seen = g.get("real_price_seen")
    participants = g.get("participants", {}) or {}
    total_bets = sum(p["count"] for p in participants.values())
    total_spent = sum(p.get("spent", 0) for p in participants.values())
    mode = "платный" if configured_price > 0 else "бесплатный"

    if real_price_seen is not None:
        price_line = f"Текущая цена ставки: {real_price_seen} зв. (реально списано Telegram)"
    else:
        price_line = (
            f"Текущая цена ставки: не подтверждена Telegram (задано {configured_price} зв., "
            f"но ни одно сообщение ещё не было платным)"
        )

    ranked = sorted(participants.values(), key=lambda p: p.get("spent", 0), reverse=True)

    lines = [
        f"{EMOJIS['game']} <b>Итог перебива</b>",
        f"Чат: {chat_id}",
        f"Приз: {g['prize']}",
        f"Победитель: {winner_name or 'нет (без победителя)'}",
        f"Режим: {mode}",
        f"Ставок: {total_bets}",
        price_line,
        f"Всего потрачено (реально): {total_spent} зв.",
    ]

    if ranked:
        lines.append("")
        lines.append("По участникам:")
        top_limit = 20
        rows = [
            f"• {p['name']}: {p.get('spent', 0)} зв. ({p['count']} сообщ.)"
            for p in ranked[:top_limit]
        ]
        rest = len(ranked) - top_limit
        if rest > 0:
            rows.append(f"• ...и ещё {rest}")
        lines.append("<blockquote expandable>" + "\n".join(rows) + "</blockquote>")

    return "\n".join(lines)


async def finish_giveaway(chat_id: int):
    g = active_giveaways.pop(chat_id, None)
    if not g:
        return

    giveaway_send_queues.pop(chat_id, None)
    queue_task = giveaway_queue_tasks.pop(chat_id, None)
    if queue_task and not queue_task.done():
        queue_task.cancel()

    async def _unpin_start_message() -> None:
        if g.get("pinned_message_id"):
            try:
                await bot.unpin_chat_message(chat_id, g["pinned_message_id"])
            except Exception:
                pass  # уже откреплено вручную или боту не хватает прав

    if g["leader_id"] is None:
        await _unpin_start_message()
        await trigger_userbot_price(chat_id, 0)
        await giveaway_safe_send(
            chat_id, f"Ивент «{g['prize']}» завершён — никто так и не написал в чат. Приз сгорел {EMOJIS['cry']}"
        )
        return
    db_add_win(chat_id, g["leader_id"], g["leader_name"], g.get("leader_nickname"))
    await _unpin_start_message()
    await trigger_userbot_price(chat_id, 0)
    win_message = await giveaway_safe_send(
        chat_id,
        f"{EMOJIS['win']} <b>Ивент завершён!</b>\nПриз «{g['prize']}» забирает {g['leader_name']}! Поздравляем {EMOJIS['party']}",
    )
    try:
        await bot.pin_chat_message(chat_id, win_message.message_id, disable_notification=False)
    except Exception:
        pass  # боту может не хватать прав на закрепление сообщений в этом чате

    await giveaway_safe_send(chat_id, build_giveaway_report(chat_id, g, g["leader_name"]))


# ---------------------------------------------------------------------------
# Логика ивента «Казино» (мульти-комбо слоты с очками и лидербордом)
# ---------------------------------------------------------------------------


async def casino_watcher(chat_id: int):
    """Следит за таймером ивента «Казино». За 30 секунд до конца присылает
    предупреждение, в последние 10 секунд — обратный отсчёт, затем подводит
    итоги."""
    try:
        while True:
            event = active_casino.get(chat_id)
            if not event:
                return

            remaining = event["end_time"] - time.time()

            if remaining <= 0:
                await finish_casino(chat_id)
                return

            if remaining <= 30 and not event.get("warned_30s"):
                event["warned_30s"] = True
                await bot.send_message(
                    chat_id,
                    f"{EMOJIS['alert']} <b>До конца ивента «Казино» осталось 30 секунд!</b>\n"
                    f"Успей выбить свою комбинацию {EMOJIS['fire']}",
                )

            if remaining <= 10:
                sec = math.ceil(remaining)
                if sec >= 1 and sec not in event["countdown_sent"]:
                    event["countdown_sent"].add(sec)
                    await bot.send_message(chat_id, f"{EMOJIS['fire']} <b>{sec}</b>")
                await asyncio.sleep(0.5)
            else:
                if event["countdown_sent"]:
                    event["countdown_sent"].clear()
                next_checkpoint = 30 if remaining > 30 else 10
                await asyncio.sleep(min(3, remaining - next_checkpoint))
    except asyncio.CancelledError:
        return


async def casino_stats_loop(chat_id: int):
    """Пока ивент «Казино» активен, раз в CASINO_STATS_INTERVAL_SECONDS
    публикует в чат напоминание с таймером, правилами, призами и топом,
    закрепляет его и открепляет предыдущее напоминание."""
    try:
        while True:
            await asyncio.sleep(CASINO_STATS_INTERVAL_SECONDS)
            event = active_casino.get(chat_id)
            if not event:
                return

            sent = await bot.send_message(chat_id, render_casino_reminder(event))

            previous_reminder_id = event.get("reminder_pinned_id")
            try:
                await bot.pin_chat_message(chat_id, sent.message_id, disable_notification=True)
                event["reminder_pinned_id"] = sent.message_id
                try:
                    await db_run(db_casino_update_reminder_pin, chat_id, sent.message_id)
                except Exception:
                    logging.exception("Не удалось сохранить reminder_pinned_id «Казино» в БД")
            except Exception:
                pass  # боту может не хватать прав на закрепление сообщений в этом чате

            if previous_reminder_id and previous_reminder_id != event.get("pinned_message_id"):
                try:
                    await bot.unpin_chat_message(chat_id, previous_reminder_id)
                except Exception:
                    pass  # уже откреплено вручную или боту не хватает прав
    except asyncio.CancelledError:
        return


async def finish_casino(chat_id: int):
    """Подводит итоги ивента «Казино»: объявляет топ-3 по очкам и выдаёт им
    призы, останавливает фоновую публикацию статы и чистит состояние чата."""
    event = active_casino.pop(chat_id, None)
    if not event:
        return

    try:
        await db_run(db_casino_delete_event, chat_id)
    except Exception:
        logging.exception("Не удалось удалить завершённый ивент «Казино» из БД")

    stats_task = event.get("stats_task")
    if stats_task:
        stats_task.cancel()

    pinned_id = event.get("pinned_message_id")
    if pinned_id:
        try:
            await bot.unpin_chat_message(chat_id, pinned_id)
        except Exception:
            pass  # уже откреплено вручную или боту не хватает прав

    reminder_pinned_id = event.get("reminder_pinned_id")
    if reminder_pinned_id and reminder_pinned_id != pinned_id:
        try:
            await bot.unpin_chat_message(chat_id, reminder_pinned_id)
        except Exception:
            pass  # уже откреплено вручную или боту не хватает прав

    ranked = sorted(event["scores"].values(), key=lambda e: e["points"], reverse=True)
    prizes = event["prizes"]

    if not ranked:
        await bot.send_message(
            chat_id,
            f"{EMOJIS['cry']} <b>Ивент «Казино» завершён</b>\n\n"
            f"Никто не набрал очков — призы остались невостребованными.",
        )
        await send_event_report(chat_id, "casino", None)
        return

    winners_count = min(len(prizes), len(ranked))
    lines = []
    for i in range(winners_count):
        entry = ranked[i]
        word = _ru_points_word(entry["points"])
        lines.append(f"{_prize_prefix(i)} {entry['name']} — {entry['points']} {word}\n{EMOJIS['gift']} {prizes[i]}")

    await bot.send_message(
        chat_id,
        f"{EMOJIS['trophy']} <b>Казино-ивент завершён</b> {EMOJIS['trophy']}\n\n" + "\n\n".join(lines) +
        f"\n\n{EMOJIS['party']} Поздравляем победителей! {EMOJIS['party']}",
    )
    await send_event_report(chat_id, "casino", ranked[0]["name"])


# Только эти типы контента считаются "живым" сообщением от участника и могут
# перебить лидера. Всё остальное — служебные сообщения Telegram (приглашение
# в видеочат, вход/выход участников, смена фото/названия чата, закреп и т.п.)
# и не должно засчитываться.
COUNTABLE_CONTENT_TYPES = {
    ContentType.TEXT,
    ContentType.PHOTO,
    ContentType.VIDEO,
    ContentType.ANIMATION,
    ContentType.STICKER,
    ContentType.VOICE,
    ContentType.VIDEO_NOTE,
    ContentType.DOCUMENT,
    ContentType.AUDIO,
    ContentType.POLL,
    ContentType.CONTACT,
    ContentType.LOCATION,
    ContentType.VENUE,
    ContentType.GAME,
    ContentType.DICE,
}


async def handle_number_guess(message: Message, g: dict) -> None:
    """Проверяет, угадал ли участник число в активной игре "Угадай число"
    в этом чате. Реагирует только на верный ответ — неверные догадки
    игнорируются молча, чтобы не засорять чат."""
    if message.content_type != ContentType.TEXT:
        return
    text = message.text.strip()
    if text.startswith("/"):
        return  # команды не считаются попыткой угадать

    guess = _parse_int(text)
    if guess is None:
        return  # это не число — обычное сообщение в чате

    if message.sender_chat is not None:
        return  # анонимные посты канала не считаются

    user = message.from_user
    if user is None or user.id == 777000:
        return
    if await is_chat_admin(message.chat.id, user.id):
        return  # владелец и админы чата не участвуют в играх

    # Пока мы ждали ответ от Telegram (await выше), игра могла быть
    # остановлена командой /stop — перепроверяем, что это всё ещё та самая
    # активная игра, прежде чем объявлять победителя.
    if active_number_games.get(message.chat.id) is not g:
        return

    try:
        if await db_run(db_is_ignored, message.chat.id, user.id):
            return  # человек в игнор-листе этого чата
    except Exception:
        logging.exception("db_is_ignored недоступна — пропускаем проверку, не блокируем игру")

    if guess < g["min"] or guess > g["max"]:
        return  # число вне диапазона игры — не засчитываем и не отвечаем

    if guess != g["secret"]:
        return  # неверная попытка — молча пропускаем

    await finish_number_game(message.chat.id, user.id, fmt_user(user), fmt_nickname(user))


async def maybe_trigger_farm_drop(message: Message) -> None:
    """Если в чате включен фарм-режим (/farm), для каждого подходящего
    сообщения участника с заданным шансом мгновенно выпадает приз.
    Работает независимо от ивента /giveaway и игры /guess — может идти
    параллельно с ними."""
    farm = active_farms.get(message.chat.id)
    if not farm:
        return
    if message.content_type not in COUNTABLE_CONTENT_TYPES:
        return  # служебное сообщение — не считается попыткой
    if message.text and message.text.startswith("/"):
        return  # команды не участвуют в фарме
    if message.sender_chat is not None:
        return  # анонимный пост канала — не реальный участник

    text_content = message.text or message.caption or ""
    letters_count = sum(1 for ch in text_content if ch.isalpha())
    if letters_count < MIN_FARM_MESSAGE_LETTERS:
        return  # слишком короткое сообщение — не считается попыткой

    user = message.from_user
    if user is None or user.id == 777000:
        return
    if await is_chat_admin(message.chat.id, user.id):
        return  # владелец и админы чата не участвуют в фарме

    try:
        if await db_run(db_is_ignored, message.chat.id, user.id):
            return  # человек в игнор-листе этого чата
    except Exception:
        logging.exception("db_is_ignored недоступна — пропускаем проверку, не блокируем фарм")

    # Пока мы ждали ответ от Telegram (await выше), фарм могли остановить
    # командой /stop — перепроверяем, что он всё ещё активен.
    farm = active_farms.get(message.chat.id)
    if not farm:
        return

    if random.random() * 100 >= (farm["chance"] / 3):
        return  # не повезло в этот раз

    db_add_win(message.chat.id, user.id, fmt_user(user), fmt_nickname(user))
    prizes = farm.get("prizes") or [farm["prize"]]
    won_prize = random.choice(prizes)
    await message.reply(
        f"{EMOJIS['party']} <b>Поздравляю!</b>\n"
        f"{EMOJIS['gift']} Ты выиграл {won_prize} от {farm['giver']}\n"
        f"{EMOJIS['check']} Подарок будет отправлен в ближайшее время.\n\n\n"
        f"{EMOJIS['alert']} Подарки дарятся за активность — пишите сообщения в чате, и получайте возможность так же залутать подарки"
    )


async def maybe_promo_channel_post(message: Message) -> None:
    """Если в этот чат (группу обсуждений канала) прилетел автоматически
    пересланный пост канала — бот отвечает на этот пост промо-комментарием,
    чтобы он появился под каждым новым постом. Если для чата свой текст
    не задавали через /set_promo — используется текст по умолчанию.
    Если промо явно отключили через /clear_promo — ничего не отправляется."""
    if not message.is_automatic_forward:
        return
    promo = db_get_promo(message.chat.id)
    if promo is None:
        text, photo_id = DEFAULT_PROMO_TEXT, None
    else:
        text, photo_id = promo
        if not text:
            return  # промо явно отключили командой /clear_promo
    try:
        if photo_id:
            await bot.send_photo(
                message.chat.id, photo_id, caption=text, reply_to_message_id=message.message_id
            )
        else:
            await bot.send_message(message.chat.id, text, reply_to_message_id=message.message_id)
    except Exception:
        pass  # бот не состоит в группе обсуждений или не хватает прав на отправку


@router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def on_group_message(message: Message):
    if message.from_user is not None and not message.from_user.is_bot and message.sender_chat is None:
        try:
            await db_run(
                db_touch_known_user,
                message.chat.id, message.from_user.id, message.from_user.username, message.from_user.full_name,
            )
        except Exception:
            logging.exception("db_touch_known_user не удалась — продолжаем без учёта юзера для /tag_all")

    await maybe_promo_channel_post(message)
    await stats_record_message(message)
    await maybe_check_slot_spin(message)
    await maybe_check_casino_spin(message)
    await maybe_check_box777_spin(message)
    await maybe_trigger_farm_drop(message)

    number_game = active_number_games.get(message.chat.id)
    if number_game is not None:
        await handle_number_guess(message, number_game)
        return

    g = active_giveaways.get(message.chat.id)
    if not g:
        return
    if message.content_type not in COUNTABLE_CONTENT_TYPES:
        return  # служебное сообщение (видеочат, вход/выход и т.п.) — не перебивание
    if message.text and message.text.startswith("/"):
        return  # команды не считаются "перебиванием"

    # Посты из привязанного канала, автоматически пересланные в группу,
    # приходят либо от псевдо-пользователя "Telegram" (id 777000), либо
    # как анонимное сообщение от имени самого канала (message.sender_chat).
    # Это не реальное сообщение участника — не считаем перебиванием.
    if message.sender_chat is not None:
        return
    user = message.from_user
    if user is None or user.id == 777000:
        return
    if await is_chat_admin(message.chat.id, user.id):
        return  # владелец и админы чата не участвуют в ивентах

    # Пока мы ждали ответ от Telegram (await выше), ивент мог быть остановлен
    # командой /stop или завершиться сам — перепроверяем, что это всё ещё
    # тот самый активный ивент, прежде чем его менять.
    if active_giveaways.get(message.chat.id) is not g:
        return

    try:
        if await db_run(db_is_ignored, message.chat.id, user.id):
            return  # человек в игнор-листе этого чата
    except Exception:
        logging.exception("db_is_ignored недоступна — пропускаем проверку, не блокируем ивент")

    # Реальное число звёзд, списанных Telegram за ЭТО конкретное сообщение —
    # появляется только если в чате реально включены платные сообщения
    # (Bot API 9.0+, поле paid_star_count). Считаем ставку и трату здесь,
    # ДО проверки "сам себя не перебивает" — Telegram списывает звёзды за
    # само сообщение независимо от того, меняется лидер или нет.
    real_paid_stars = getattr(message, "paid_star_count", None) or 0
    participant = g["participants"].get(user.id)
    if participant is None:
        participant = {"name": fmt_user(user), "count": 0, "spent": 0}
        g["participants"][user.id] = participant
    participant["name"] = fmt_user(user)  # на случай смены ника/юзернейма
    participant["count"] += 1
    if real_paid_stars:
        participant["spent"] += real_paid_stars
        g["real_price_seen"] = real_paid_stars

    if g["leader_id"] == user.id:
        return  # сам себя не перебивает (ставка выше уже учтена)

    g["leader_id"] = user.id
    g["leader_name"] = fmt_user(user)
    g["leader_nickname"] = fmt_nickname(user)
    g["end_time"] = time.time() + g["reset_seconds"]
    g["countdown_sent"] = set()  # сбрасываем отсчёт последних секунд
    g["warned_30s"] = False  # таймер продлён — предупреждение можно прислать снова

    remaining_label = _format_duration(g["reset_seconds"])
    giveaway_queue_send(
        message.chat.id,
        f"{EMOJIS['fire']} <b>Перебито!</b>\nНовый лидер: {g['leader_name']}\nДо конца: {remaining_label}.",
    )


# ---------------------------------------------------------------------------


async def restore_casino_events() -> None:
    """Поднимает активные ивенты «Казино» из БД после рестарта/редеплоя
    процесса — таймер и очки игроков не обнуляются. Если бот был выключен
    дольше, чем оставалось до конца ивента, итоги подводятся сразу при
    старте (как будто бот не пропустил момент окончания, просто с опозданием)."""
    try:
        rows = db_casino_list_events()
    except Exception:
        logging.exception("Не удалось прочитать сохранённые ивенты «Казино» из БД")
        return

    for chat_id, end_time, duration, prizes_json, pinned_message_id, reminder_pinned_id in rows:
        try:
            prizes = json.loads(prizes_json) if prizes_json else []
        except Exception:
            prizes = []

        scores = {}
        try:
            for user_id, name, points in db_casino_list_scores(chat_id):
                scores[user_id] = {"name": name, "points": points}
        except Exception:
            logging.exception(f"Не удалось прочитать очки «Казино» для чата {chat_id}")

        stats_start(chat_id, "casino", "")
        active_casino[chat_id] = {
            "end_time": end_time,
            "duration": duration,
            "prizes": prizes,
            "scores": scores,
            "warned_30s": False,
            "countdown_sent": set(),
            "pinned_message_id": pinned_message_id,
            "reminder_pinned_id": reminder_pinned_id,
        }

        if end_time <= time.time():
            # Время ивента истекло, пока бот был выключен — подводим итоги
            # сейчас же, чтобы приз не завис навсегда неразыгранным.
            await finish_casino(chat_id)
            continue

        active_casino[chat_id]["task"] = asyncio.create_task(casino_watcher(chat_id))
        active_casino[chat_id]["stats_task"] = asyncio.create_task(casino_stats_loop(chat_id))
        logging.info(
            f"Восстановлен ивент «Казино» в чате {chat_id}: "
            f"{len(scores)} игроков, до конца {_format_duration(max(0, int(end_time - time.time())))}"
        )


async def main():
    db_init()
    await restore_casino_events()
    asyncio.create_task(shop_revert_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
