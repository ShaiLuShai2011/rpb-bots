import re
"""
bot.py — Бот "Страны РП" (единый файл)

Запуск:
    pip install aiogram aiosqlite pillow
    python bot.py

Переменные окружения:
    BOT_TOKEN      — токен Telegram бота (обязательно)
    ANTHROPIC_KEY  — API ключ Anthropic для газеты (опционально, без него
                     газета публикуется в упрощённом виде без AI-текста)

Добавлено:
- 📰 Газета: автоматически публикуется каждые NEWSPAPER_INTERVAL_H часов.
  Собирает события (войны, захваты, союзы) и генерирует статью через Claude API.
  Команда /newspaper — выпустить вручную (только для теста / администратора).
- ⚔️  Живые войны: каждые WAR_TICK_INTERVAL_H часов по всем активным войнам
  происходит автобой — армии дерутся, результат публикуется в чат.
  Потери масштабируются на «моральный дух» (чем меньше территорий, тем хуже).
  При обнулении армии — автокапитуляция, победитель забирает все территории проигравшего.
- /reinforce <кол-во> — экстренная закупка солдат во время войны (цена ×2).
- log_event() — внутренняя функция записи событий в таблицу event_log.

Исправлено ранее:
- flood_fill: проверка bounds ДО обращения к pixels
- flood_fill: не перетекает через тёмные границы
- get_country_by_id принимает chat_id
- peace_decline: проверяет что нажимает защитник
- expand: смежность проверяется корректно
- Seed-координаты UA, BY, MD, RO, BG и других европейских стран исправлены
- _find_nearest_land: страховка от попадания seed на пиксель границы
"""

import asyncio
import functools
import html as _html
import os
import logging
import random
import aiosqlite
import time
from typing import Optional
from io import BytesIO

import aiohttp
from PIL import Image, ImageDraw, ImageFont

from aiogram import Bot, Dispatcher, Router, F
from aiogram.exceptions import TelegramRetryAfter
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton, BufferedInputFile, BotCommand, MessageEntity
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext

from aiogram.fsm.state import State, StatesGroup


async def _safe_send(bot: "Bot", chat_id: int, text: str, **kwargs) -> bool:
    """Отправляет сообщение с автоматической обработкой FloodWait и ретраем.
    Возвращает True при успехе, False при неустранимой ошибке.
    """
    for _attempt in range(3):
        try:
            await bot.send_message(chat_id, text, **kwargs)
            return True
        except TelegramRetryAfter as e:
            wait = e.retry_after + 1
            logging.warning(f"FloodWait {wait}s для чата {chat_id}, ждём...")
            await asyncio.sleep(wait)
        except Exception as e:
            logging.warning(f"_safe_send chat {chat_id}: {e}")
            return False
    return False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except Exception:
    pass

BOT_TOKEN      = os.environ.get("BOT_TOKEN")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_KEY", "")

# ── Интервалы фоновых задач ──────────────────────────────────────
NEWSPAPER_INTERVAL_H  = 6     # раз в 6 часов выходит газета
WAR_TICK_INTERVAL_H   = 1     # раз в час автобой в каждой активной войне
# ═══════════════════════════════════════════════════════════════
#  MAP GENERATOR  (GeoJSON-based, map.png не нужен)
# ═══════════════════════════════════════════════════════════════

import json

# Предпочитаем Roboto-Bold если есть, иначе DejaVu (поддерживает кириллицу)
_FONT_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "Roboto-Bold.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
FONT_PATH = next((p for p in _FONT_CANDIDATES if os.path.exists(p)), None)

# NotoColorEmoji — для рисования звезды на карте у столиц
_EMOJI_FONT_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "NotoColorEmoji.ttf"),
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/noto-color-emoji/NotoColorEmoji.ttf",
]
EMOJI_FONT_PATH = next((p for p in _EMOJI_FONT_CANDIDATES if os.path.exists(p)), None)

def _ensure_emoji_font_available():
    """Логирует отсутствие emoji-шрифта без сетевых запросов при старте."""
    if not EMOJI_FONT_PATH:
        logging.info("NotoColorEmoji.ttf не найден, звёзды на карте будут нарисованы fallback-шрифтом")


# ── Премиум эмодзи (document_id из Telegram) ─────────────────────────────────
def pe(emoji_id: str, fallback: str) -> str:
    """Возвращает тег премиум эмодзи для HTML parse_mode."""
    return f"<tg-emoji emoji-id=\"{emoji_id}\">{fallback}</tg-emoji>"

PE = {
    "sword":       pe("5228907882975608314", "⚔️"),   # меч — атака
    "shield":      pe("5256136584062838935", "🛡"),    # щит — гарнизон
    "skull":       pe("5355161397284063986", "💀"),    # череп — потери
    "trophy":      pe("5188344996356448758", "🏆"),    # трофей — победа
    "flag":        pe("5278547053398465414", "🚩"),    # флаг — захват
    "castle":      pe("5316932602051977742", "🏰"),    # замок — гарнизон
    "star":        pe("6005661956931850799", "⭐"),    # звезда — столица
    "map":         pe("6323305098055255968", "🗺"),    # карта
    "coin":        pe("4997177479480214157", "💰"),    # монета — казна
    "fire":        pe("5458908492687497206", "🔥"),    # огонь — война/усталость
    "alert":       pe("5213285132709929474", "🚨"),    # сирена — атака
    "warning":     pe("6021409772350215140", "⚠️"),   # предупреждение
    "gun":         pe("5354985531258195403", "🔫"),    # пистолет — бой
    "cross":       pe("5328093774650293878", "❌"),    # крест — поражение/ошибка
    "arm":         pe("5256136584062838935", "💪"),    # щит/броня — гарнизон
}

GEO_PATH   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "custom_geo.json")

# Размер итогового изображения
_MAP_W, _MAP_H = 1677, 938

# Проекция: меркатор-ish (линейная)
_LON_MIN, _LON_MAX = -180.0, 180.0
_LAT_MIN, _LAT_MAX = -56.0,   84.0

def _lon_to_x(lon: float) -> int:
    return int((lon - _LON_MIN) / (_LON_MAX - _LON_MIN) * _MAP_W)

def _lat_to_y(lat: float) -> int:
    return int((_LAT_MAX - lat) / (_LAT_MAX - _LAT_MIN) * _MAP_H)

def _pts(ring) -> list:
    return [(_lon_to_x(pt[0]), _lat_to_y(pt[1])) for pt in ring]

def _rings(geom: dict) -> list:
    """Возвращает список внешних колец полигонов для любой геометрии."""
    if geom["type"] == "Polygon":
        return [geom["coordinates"][0]]
    elif geom["type"] == "MultiPolygon":
        return [poly[0] for poly in geom["coordinates"]]
    return []


# ── Загружаем GeoJSON один раз при старте ─────────────────────
_GEO_FEATURES: list = []
_ISO_TO_FEATURE: dict = {}
_ISO_FEATURE_RANK: dict = {}

def _geo_prop_str(props: dict, key: str) -> str:
    value = props.get(key, "")
    return value.strip() if isinstance(value, str) else ""

def _normalize_geo_iso(props: dict) -> Optional[str]:
    """Возвращает игровой ISO-код для GeoJSON feature."""
    iso2 = _geo_prop_str(props, "iso_a2")
    if iso2 and iso2 != "-99":
        iso2 = iso2.upper()
        if iso2 == "GB":
            return "UK"
        if iso2 == "CN-TW":
            return "TW"
        return iso2

    postal = _geo_prop_str(props, "postal").upper()
    adm = _geo_prop_str(props, "adm0_a3")
    # Используем postal только если adm0_a3 начинается с postal (например KOS→KO),
    # иначе возможны ложные совпадения: N. Cyprus имеет postal='CN', Siachen→'SG' и т.д.
    if len(postal) == 2 and adm.upper().startswith(postal):
        return postal
    admin = _geo_prop_str(props, "admin").lower()
    name = _geo_prop_str(props, "name").lower()
    ftype = _geo_prop_str(props, "type").lower()
    is_dependency = "dependency" in ftype

    if adm == "FRA" and not is_dependency and (admin == "france" or name == "france"):
        return "FR"
    if adm == "NOR" and not is_dependency and (admin == "norway" or name == "norway"):
        return "NO"
    return None

def _geo_feature_rank(feat: dict, key: str) -> int:
    """Приоритет feature при дублях ISO: материковая страна важнее зависимостей."""
    p = feat.get("properties", {})
    iso2 = _geo_prop_str(p, "iso_a2").upper()
    postal = _geo_prop_str(p, "postal").upper()
    mapped_iso = "UK" if iso2 == "GB" else "TW" if iso2 == "CN-TW" else iso2.upper()
    if not mapped_iso or mapped_iso == "-99":
        mapped_iso = postal
    adm0_a3 = _geo_prop_str(p, "adm0_a3").upper()
    sov_a3 = _geo_prop_str(p, "sov_a3").upper()
    gu_a3 = _geo_prop_str(p, "gu_a3").upper()
    admin = _geo_prop_str(p, "admin").lower()
    name = _geo_prop_str(p, "name").lower()
    ftype = _geo_prop_str(p, "type").lower()
    homepart = p.get("homepart")
    adm0_dif = p.get("adm0_dif")
    tiny = p.get("tiny")

    score = 0
    if mapped_iso == key:
        score += 100
    if "sovereign" in ftype:
        score += 50
    if "dependency" in ftype:
        score -= 120
    if homepart == 1:
        score += 80
    elif homepart == -99:
        score -= 25
    if adm0_dif == 0:
        score += 40
    elif adm0_dif == 1:
        score -= 15
    if adm0_a3 and adm0_a3 == sov_a3 == gu_a3:
        score += 35
    if admin == name:
        score += 10
    if isinstance(tiny, int) and tiny > 0:
        score -= tiny * 10
    return score

def _load_geo():
    global _GEO_FEATURES, _ISO_TO_FEATURE, _ISO_FEATURE_RANK
    try:
        with open(GEO_PATH, encoding="utf-8") as f:
            data = json.load(f)
        _GEO_FEATURES = data["features"]
        _ISO_TO_FEATURE = {}
        _ISO_FEATURE_RANK = {}
        for feat in _GEO_FEATURES:
            p = feat.get("properties", {})
            key = _normalize_geo_iso(p)
            if not key:
                continue
            rank = _geo_feature_rank(feat, key)
            if key not in _ISO_TO_FEATURE or rank > _ISO_FEATURE_RANK.get(key, -10000):
                _ISO_TO_FEATURE[key] = feat
                _ISO_FEATURE_RANK[key] = rank
        logging.info(f"GeoJSON загружен: {len(_GEO_FEATURES)} объектов, {len(_ISO_TO_FEATURE)} ISO-кодов")
    except Exception as e:
        logging.error(f"Не удалось загрузить GeoJSON: {e}")

def is_valid_iso(iso: str) -> bool:
    """Проверяет, что ISO можно отрисовать на GeoJSON-карте."""
    if not _GEO_FEATURES:
        _load_geo()
    return iso.upper() in _ISO_TO_FEATURE


COUNTRY_NAMES: dict[str, str] = {
    "CA": "Канада",
    "US": "США",
    "MX": "Мексика",
    "CU": "Куба",
    "JM": "Ямайка",
    "HT": "Гаити",
    "DO": "Доминиканская Республика",
    "TT": "Тринидад и Тобаго",
    "CO": "Колумбия",
    "VE": "Венесуэла",
    "SR": "Суринам",
    "BR": "Бразилия",
    "PE": "Перу",
    "BO": "Боливия",
    "PY": "Парагвай",
    "AR": "Аргентина",
    "UY": "Уругвай",
    "IS": "Исландия",
    "UK": "Великобритания",
    "IE": "Ирландия",
    "ES": "Испания",
    "PT": "Португалия",
    "FR": "Франция",
    "BE": "Бельгия",
    "NL": "Нидерланды",
    "DE": "Германия",
    "CH": "Швейцария",
    "AT": "Австрия",
    "IT": "Италия",
    "SM": "Сан-Марино",
    "DK": "Дания",
    "NO": "Норвегия",
    "SE": "Швеция",
    "FI": "Финляндия",
    "LV": "Латвия",
    "LT": "Литва",
    "PL": "Польша",
    "CZ": "Чехия",
    "SK": "Словакия",
    "HU": "Венгрия",
    "HR": "Хорватия",
    "RS": "Сербия",
    "SI": "Словения",
    "BG": "Болгария",
    "RO": "Румыния",
    "GR": "Греция",
    "CY": "Кипр",
    "UA": "Украина",
    "BY": "Беларусь",
    "RU": "Россия",
    "GE": "Грузия",
    "AM": "Армения",
    "AZ": "Азербайджан",
    "TR": "Турция",
    "SY": "Сирия",
    "LB": "Ливан",
    "IL": "Израиль",
    "JO": "Иордания",
    "IQ": "Ирак",
    "IR": "Иран",
    "KW": "Кувейт",
    "SA": "Саудовская Аравия",
    "AE": "ОАЭ",
    "QA": "Катар",
    "BH": "Бахрейн",
    "OM": "Оман",
    "YE": "Йемен",
    "TM": "Туркменистан",
    "UZ": "Узбекистан",
    "KZ": "Казахстан",
    "KG": "Кыргызстан",
    "TJ": "Таджикистан",
    "AF": "Афганистан",
    "PK": "Пакистан",
    "IN": "Индия",
    "NP": "Непал",
    "BD": "Бангладеш",
    "LK": "Шри-Ланка",
    "MM": "Мьянма",
    "TH": "Таиланд",
    "LA": "Лаос",
    "VN": "Вьетнам",
    "KH": "Камбоджа",
    "MY": "Малайзия",
    "SG": "Сингапур",
    "ID": "Индонезия",
    "PH": "Филиппины",
    "TW": "Тайвань",
    "CN": "Китай",
    "MN": "Монголия",
    "KR": "Южная Корея",
    "JP": "Япония",
    "HK": "Гонконг",
    "AU": "Австралия",
    "PG": "Папуа — Новая Гвинея",
    "NZ": "Новая Зеландия",
    "NC": "Новая Каледония",
    "SB": "Соломоновы Острова",
    "DZ": "Алжир",
    "LY": "Ливия",
    "EG": "Египет",
    "ML": "Мали",
    "NG": "Нигерия",
    "NE": "Нигер",
    "BJ": "Бенин",
    "CM": "Камерун",
    "CF": "ЦАР",
    "TD": "Чад",
    "SD": "Судан",
    "SS": "Южный Судан",
    "ET": "Эфиопия",
    "DJ": "Джибути",
    "SO": "Сомали",
    "GA": "Габон",
    "GQ": "Экваториальная Гвинея",
    "CG": "Республика Конго",
    "CD": "ДР Конго",
    "AO": "Ангола",
    "UG": "Уганда",
    "KE": "Кения",
    "RW": "Руанда",
    "BI": "Бурунди",
    "TZ": "Танзания",
    "MZ": "Мозамбик",
    "MW": "Малави",
    "ZM": "Замбия",
    "ZW": "Зимбабве",
    "BW": "Ботсвана",
    "NA": "Намибия",
    "ZA": "ЮАР",
    "SZ": "Эсватини",
    "MG": "Мадагаскар",
    "MU": "Маврикий",
    "KM": "Коморы",
    "SC": "Сейшелы",
    "AD": "Андорра",
    "AG": "Антигуа и Барбуда",
    "AI": "Ангилья",
    "AL": "Албания",
    "AS": "Американское Самоа",
    "AW": "Аруба",
    "AX": "Аландские острова",
    "BA": "Босния и Герцеговина",
    "BB": "Барбадос",
    "BF": "Буркина-Фасо",
    "BL": "Сен-Бартелеми",
    "BM": "Бермудские Острова",
    "BN": "Бруней",
    "BS": "Багамские Острова",
    "BT": "Бутан",
    "BZ": "Белиз",
    "CI": "Кот-д’Ивуар",
    "CK": "Острова Кука",
    "CL": "Чили",
    "CR": "Коста-Рика",
    "CV": "Кабо-Верде",
    "CW": "Кюрасао",
    "DM": "Доминика",
    "EC": "Эквадор",
    "EE": "Эстония",
    "EH": "Западная Сахара",
    "ER": "Эритрея",
    "FJ": "Фиджи",
    "FK": "Фолклендские острова",
    "FM": "Микронезия",
    "FO": "Фарерские острова",
    "GD": "Гренада",
    "GG": "Гернси",
    "GH": "Гана",
    "GL": "Гренландия",
    "GM": "Гамбия",
    "GN": "Гвинея",
    "GT": "Гватемала",
    "GU": "Гуам",
    "GW": "Гвинея-Бисау",
    "GY": "Гайана",
    "HN": "Гондурас",
    "IM": "Остров Мэн",
    "JE": "Джерси",
    "KI": "Кирибати",
    "KN": "Сент-Китс и Невис",
    "KP": "КНДР",
    "KY": "Острова Кайман",
    "LC": "Сент-Люсия",
    "LI": "Лихтенштейн",
    "LR": "Либерия",
    "LS": "Лесото",
    "LU": "Люксембург",
    "MA": "Марокко",
    "MC": "Монако",
    "MD": "Молдова",
    "ME": "Черногория",
    "MF": "Сен-Мартен",
    "MH": "Маршалловы Острова",
    "MK": "Северная Македония",
    "MO": "Макао",
    "MP": "Северные Марианские Острова",
    "MR": "Мавритания",
    "MS": "Монтсеррат",
    "MT": "Мальта",
    "NF": "Остров Норфолк",
    "NI": "Никарагуа",
    "NR": "Науру",
    "NU": "Ниуэ",
    "PA": "Панама",
    "PF": "Французская Полинезия",
    "PM": "Сен-Пьер и Микелон",
    "PN": "Острова Питкэрн",
    "PR": "Пуэрто-Рико",
    "PS": "Палестина",
    "PW": "Палау",
    "SL": "Сьерра-Леоне",
    "SN": "Сенегал",
    "ST": "Сан-Томе и Принсипи",
    "SV": "Сальвадор",
    "SX": "Синт-Мартен",
    "TC": "Тёркс и Кайкос",
    "TG": "Того",
    "TL": "Восточный Тимор",
    "TN": "Тунис",
    "TO": "Тонга",
    "TV": "Тувалу",
    "VA": "Ватикан",
    "VC": "Сент-Винсент и Гренадины",
    "VG": "Британские Виргинские Острова",
    "VI": "Американские Виргинские Острова",
    "VU": "Вануату",
    "WF": "Уоллис и Футуна",
    "WS": "Самоа",
}


def _hex_to_rgb(h: str) -> tuple:
    try:
        h = (h or "").strip().lstrip("#")
        if len(h) != 6:
            raise ValueError
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    except Exception:
        return (136, 136, 136)



def _clean_emoji_symbol(em_str: str) -> str:
    """Возвращает стандартный Unicode эмодзи. Премиум эмодзи Telegram (tg-emoji) не рендерятся на карте."""
    if not em_str or "<tg-emoji" in em_str or "</tg-emoji>" in em_str:
        return ""
    clean = re.sub(r"<[^>]+>", "", em_str).strip()
    return clean


_EMOJI_BITMAP_CACHE = {}

def _get_emoji_bitmap(symbol: str, target_size: int = 18):
    """Рендерит эмодзи в прозрачный RGBA битмап через NotoColorEmoji (размер 109px) с ресайзом."""
    if not EMOJI_FONT_PATH:
        return None
    cache_key = (symbol, target_size)
    if cache_key in _EMOJI_BITMAP_CACHE:
        return _EMOJI_BITMAP_CACHE[cache_key]
    try:
        # NotoColorEmoji в Linux часто требует точный strike-size (109)
        base_font = ImageFont.truetype(EMOJI_FONT_PATH, 109)
        canvas = Image.new("RGBA", (150, 150), (0, 0, 0, 0))
        cdraw = ImageDraw.Draw(canvas)
        cdraw.text((75, 75), symbol, font=base_font, anchor="mm", embedded_color=True)
        bbox = canvas.getbbox()
        if bbox:
            cropped = canvas.crop(bbox)
            w, h = cropped.size
            scale = target_size / max(w, h, 1)
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            res = cropped.resize((new_w, new_h), Image.Resampling.LANCZOS)
            _EMOJI_BITMAP_CACHE[cache_key] = res
            return res
    except Exception as e:
        pass
    _EMOJI_BITMAP_CACHE[cache_key] = None
    return None

def generate_map(owned: dict, title: str = "Карта мира", map_path: str = None,
                 show_labels: bool = True, capitals: set = None) -> BytesIO:
    """
    Рисует карту мира из GeoJSON полигонов.
    owned = {iso: (hex_color, display_name)}
    show_labels — рисовать реальные русские названия стран на карте.
    capitals — набор ISO-кодов столиц, на которых рисуется звезда.
    """
    if not _GEO_FEATURES:
        _load_geo()

    W, H = _MAP_W, _MAP_H
    _capitals = {c.upper() for c in capitals} if capitals else set()

    def _font(size):
        if FONT_PATH:
            try:
                return ImageFont.truetype(FONT_PATH, size)
            except Exception:
                pass
        return ImageFont.load_default()

    def _emoji_font(size):
        if EMOJI_FONT_PATH:
            try:
                return ImageFont.truetype(EMOJI_FONT_PATH, size)
            except Exception:
                pass
        return None

    font_label = _font(9)
    font_title = _font(22)
    font_leg   = _font(14)
    font_star  = _emoji_font(18)  # шрифт для звезды

    img  = Image.new("RGB", (W, H), (52, 110, 160))
    draw = ImageDraw.Draw(img)

    for feat in _GEO_FEATURES:
        geom = feat.get("geometry")
        if not geom:
            continue
        for ring in _rings(geom):
            pts = _pts(ring)
            if len(pts) >= 3:
                draw.polygon(pts, fill=(195, 195, 185), outline=(80, 80, 80))

    owned_upper = {}
    for iso, val in owned.items():
        if isinstance(val, (tuple, list)):
            hex_c = val[0]
            name_c = val[1] if len(val) > 1 else ""
            emoji_c = val[2] if len(val) > 2 else ""
            owned_upper[iso.upper()] = (hex_c, name_c, emoji_c)
        else:
            owned_upper[iso.upper()] = (str(val), "", "")

    for iso, tpl in owned_upper.items():
        hex_color = tpl[0]
        feat = _ISO_TO_FEATURE.get(iso)
        if not feat:
            continue
        geom = feat.get("geometry")
        if not geom:
            continue
        rgb = _hex_to_rgb(hex_color)
        for ring in _rings(geom):
            pts = _pts(ring)
            if len(pts) >= 3:
                draw.polygon(pts, fill=rgb, outline=(40, 40, 40))

    if show_labels:
        for feat in _GEO_FEATURES:
            geom = feat.get("geometry")
            if not geom:
                continue
            p = feat.get("properties", {})
            key = _normalize_geo_iso(p)
            if not key:
                continue
            label = p.get("name_ru") or p.get("name", "")
            if not label:
                continue
            if geom["type"] == "MultiPolygon":
                rings_for_label = [max(geom["coordinates"], key=lambda poly: len(poly[0]))[0]]
            else:
                rings_for_label = _rings(geom)
            all_pts = [pt for ring in rings_for_label for pt in ring]
            if not all_pts:
                continue
            xs = [pt[0] for pt in all_pts]
            ys = [pt[1] for pt in all_pts]
            px_w = _lon_to_x(max(xs)) - _lon_to_x(min(xs))
            px_h = _lat_to_y(min(ys)) - _lat_to_y(max(ys))
            if px_w < 12 or px_h < 6:
                continue
            cx = _lon_to_x((min(xs) + max(xs)) / 2)
            cy = _lat_to_y((min(ys) + max(ys)) / 2)
            txt_color = (255, 255, 255) if key in owned_upper else (20, 20, 20)
            draw.text((cx+1, cy+1), label, font=font_label, fill=(0, 0, 0), anchor="mm")
            draw.text((cx, cy), label, font=font_label, fill=txt_color, anchor="mm")

    # Рисуем эмодзи-флаги на территориях и звёзды на столицах
    star_bm = _get_emoji_bitmap("⭐", 22)

    for iso, (hex_color, custom_name, em_raw) in owned_upper.items():
        feat = _ISO_TO_FEATURE.get(iso)
        if not feat:
            continue
        geom = feat.get("geometry")
        if not geom:
            continue
        if geom["type"] == "MultiPolygon":
            rings_t = [max(geom["coordinates"], key=lambda poly: len(poly[0]))[0]]
        else:
            rings_t = _rings(geom)
        all_pts_t = [pt for ring in rings_t for pt in ring]
        if not all_pts_t:
            continue
        xs_t = [pt[0] for pt in all_pts_t]
        ys_t = [pt[1] for pt in all_pts_t]
        cx_t = _lon_to_x((min(xs_t) + max(xs_t)) / 2)
        cy_t = _lat_to_y((min(ys_t) + max(ys_t)) / 2)
        
        # Если это столица или на территории достаточно места
        is_cap = iso in _capitals
        # Делаем флаги значительно крупнее и заметнее
        target_size = 32 if is_cap else 24
        
        clean_sym = _clean_emoji_symbol(em_raw) if em_raw else ""
        if clean_sym:
            flag_bm = _get_emoji_bitmap(clean_sym, target_size)
            if flag_bm:
                # Рисуем подложку-кружок/сдвигаем выше текста
                fx = cx_t - flag_bm.width // 2 - (14 if is_cap else 0)
                fy = cy_t - flag_bm.height // 2 - 16
                img.paste(flag_bm, (fx, fy), flag_bm)

        if is_cap:
            # Звезда рядом со столицей (22px)
            if star_bm:
                sx = cx_t - star_bm.width // 2 + (14 if clean_sym else 0)
                sy = cy_t - star_bm.height // 2 - 16
                img.paste(star_bm, (sx, sy), star_bm)
            else:
                star_x = cx_t + (12 if clean_sym else 0)
                star_y = cy_t - 10
                draw.text((star_x + 1, star_y + 1), "★", font=font_label, fill=(0, 0, 0), anchor="mm")
                draw.text((star_x, star_y), "★", font=font_label, fill=(255, 215, 0), anchor="mm")

    bar_h = 42
    draw.rectangle([0, H - bar_h, W, H], fill=(8, 15, 28))
    clean_title = re.sub(r"<[^>]+>", "", title).strip()
    draw.text((W // 2, H - bar_h // 2), clean_title, font=font_title, fill=(255, 245, 180), anchor="mm")

    if owned:
        # Уникальные страны для легенды: берем по одной записи на страну
        seen_names = set()
        unique_countries = []
        for iso, (hex_c, custom_name, em_raw) in owned_upper.items():
            cname = custom_name if custom_name else COUNTRY_NAMES.get(iso, iso)
            if cname not in seen_names:
                seen_names.add(cname)
                unique_countries.append((hex_c, cname, em_raw))

        lx, ly = 8, H - bar_h - 8
        for hex_color, label, em_raw in unique_countries[:14]:
            rgb = _hex_to_rgb(hex_color)
            ly -= 22
            # Цветовой квадрат
            draw.rectangle([lx, ly, lx+18, ly+18], fill=rgb)
            draw.rectangle([lx, ly, lx+18, ly+18], outline=(220, 220, 220), width=1)
            
            cur_x = lx + 26
            # Рисуем эмодзи флага в легенде
            clean_sym = _clean_emoji_symbol(em_raw) if em_raw else ""
            if clean_sym:
                bm = _get_emoji_bitmap(clean_sym, 20)
                if bm:
                    img.paste(bm, (cur_x, ly), bm)
                    cur_x += bm.width + 5
            
            draw.text((cur_x, ly), label, font=font_leg, fill=(255, 255, 255))

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


# ═══════════════════════════════════════════════════════════════
#  DATABASE
# ═══════════════════════════════════════════════════════════════

class _RowDict(dict):
    __slots__ = ("_values",)

    def __init__(self, keys, values):
        super().__init__(zip(keys, values))
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def _row_factory(cursor, row):
    return _RowDict([col[0] for col in cursor.description], row)


def _resolve_db_path() -> str:
    env_path = os.environ.get("DB_PATH")
    if env_path:
        return env_path

    default_path = "/data/countries_rp.db"
    default_dir = os.path.dirname(default_path)
    if os.path.isdir(default_dir) and os.access(default_dir, os.W_OK):
        return default_path

    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "countries_rp.db")


DB_PATH = _resolve_db_path()


def _ensure_db_dir() -> None:
    db_dir = os.path.dirname(os.path.abspath(DB_PATH))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
#  ЕДИНСТВЕННОЕ ГЛОБАЛЬНОЕ СОЕДИНЕНИЕ С БД
#
#  SQLite — файловая БД, не серверная. Пул нескольких соединений
#  даёт "грязное чтение": одно соединение пишет, другое видит
#  старые данные из своего WAL-снимка. Поэтому держим одно
#  соединение на весь процесс и сериализуем доступ через Lock.
# ═══════════════════════════════════════════════════════════════

_db_conn: aiosqlite.Connection | None = None
_db_lock: asyncio.Lock | None = None   # сериализует параллельные write-транзакции
_db_lock_owner: asyncio.Task | None = None
_db_lock_depth: int = 0


async def _init_pool() -> None:
    """Открывает единственное постоянное соединение. Вызывается из init_db()."""
    _ensure_db_dir()
    global _db_conn, _db_lock
    _db_lock = asyncio.Lock()
    _db_conn = await aiosqlite.connect(DB_PATH)
    _db_conn.row_factory = _row_factory
    await _db_conn.execute("PRAGMA busy_timeout=5000")
    await _db_conn.execute("PRAGMA synchronous=NORMAL")
    await _db_conn.execute("PRAGMA foreign_keys=ON")
    await _db_conn.execute("PRAGMA cache_size=-8000")
    logging.info("DB: единственное соединение открыто")


class _DbCtx:
    """Выдаёт глобальное соединение на время блока async with.

    Использование (оба варианта рабочие):
        db = await _open_db()
        async with db:
            await db.execute(...)

        async with await _open_db() as conn:
            await conn.execute(...)
    """
    __slots__ = ("_conn", "_owns_lock")

    def __init__(self):
        self._conn = None
        self._owns_lock = False

    def __await__(self):
        return self._acquire().__await__()

    async def _acquire(self) -> "_DbCtx":
        if self._conn is None:
            if _db_conn is not None:
                self._conn = _db_conn
            else:
                # Запасной вариант до init_db (миграции и т.п.)
                conn = await aiosqlite.connect(DB_PATH)
                conn.row_factory = _row_factory
                await conn.execute("PRAGMA busy_timeout=5000")
                await conn.execute("PRAGMA synchronous=NORMAL")
                await conn.execute("PRAGMA foreign_keys=ON")
                self._conn = conn
        return self

    async def __aenter__(self) -> aiosqlite.Connection:
        await self._acquire()
        global _db_lock_owner, _db_lock_depth
        task = asyncio.current_task()
        if _db_lock is not None and task is not None:
            if _db_lock_owner is task:
                _db_lock_depth += 1
            else:
                await _db_lock.acquire()
                _db_lock_owner = task
                _db_lock_depth = 1
            self._owns_lock = True
        return self._conn

    async def __aexit__(self, exc_type=None, exc=None, tb=None):
        # Соединение не закрываем — оно глобальное и живёт всё время
        global _db_lock_owner, _db_lock_depth
        if self._owns_lock and _db_lock is not None:
            task = asyncio.current_task()
            if _db_lock_owner is task:
                if exc_type is not None and _db_lock_depth <= 1 and self._conn is not None:
                    try:
                        await self._conn.rollback()
                    except Exception:
                        pass
                _db_lock_depth -= 1
                if _db_lock_depth <= 0:
                    _db_lock_owner = None
                    _db_lock_depth = 0
                    _db_lock.release()
        self._owns_lock = False
        self._conn = None

    def __getattr__(self, name):
        if name == "_conn":
            raise AttributeError(name)
        return getattr(self._conn, name)

    async def close(self):
        """Совместимость с паттерном try/finally: await ctx.close()"""
        await self.__aexit__(None, None, None)


def _open_db() -> "_DbCtx":
    """Возвращает контекстный менеджер для глобального соединения с БД."""
    return _DbCtx()


async def add_column_if_missing(db, table: str, column: str, col_type: str, default: str) -> None:
    cur = await db.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in await cur.fetchall()}
    if column in existing:
        return
    try:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type} NOT NULL DEFAULT {default}")
        logging.info(f"{table}: added missing column {column!r}")
    except Exception as exc:
        logging.error(f"{table}: failed to add {column!r}: {exc}")


async def init_db():
    _ensure_db_dir()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _row_factory
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS countries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id         INTEGER NOT NULL,
            owner_id        INTEGER NOT NULL,
            owner_username  TEXT NOT NULL DEFAULT '',
            name            TEXT NOT NULL,
            emoji           TEXT NOT NULL DEFAULT '🏳',
            color           TEXT NOT NULL DEFAULT '#888888',
            iso             TEXT NOT NULL,
            treasury        INTEGER NOT NULL DEFAULT 1000,
            army            INTEGER NOT NULL DEFAULT 100,
            tax_rate        INTEGER NOT NULL DEFAULT 10,
            last_tax        INTEGER NOT NULL DEFAULT 0,
            renamed         INTEGER NOT NULL DEFAULT 0,
            created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(chat_id, owner_id),
            UNIQUE(chat_id, iso)
        );

        CREATE TABLE IF NOT EXISTS territories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     INTEGER NOT NULL,
            iso         TEXT NOT NULL,
            country_id  INTEGER REFERENCES countries(id) ON DELETE SET NULL,
            UNIQUE(chat_id, iso)
        );

        CREATE TABLE IF NOT EXISTS wars (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id          INTEGER NOT NULL,
            attacker_id      INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            defender_id      INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            started_at       INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            ends_at          INTEGER NOT NULL DEFAULT 0,
            status           TEXT NOT NULL DEFAULT 'active',
            attacker_losses  INTEGER NOT NULL DEFAULT 0,
            defender_losses  INTEGER NOT NULL DEFAULT 0,
            attacker_score   INTEGER NOT NULL DEFAULT 0,
            defender_score   INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS alliances (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id      INTEGER NOT NULL,
            country_a_id INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            country_b_id INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            type         TEXT NOT NULL DEFAULT 'alliance',
            created_at   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(chat_id, country_a_id, country_b_id)
        );

        CREATE TABLE IF NOT EXISTS pacts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id      INTEGER NOT NULL,
            country_a_id INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            country_b_id INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            type         TEXT NOT NULL DEFAULT 'non_aggression',
            expires_at   INTEGER,
            UNIQUE(chat_id, country_a_id, country_b_id)
        );

        CREATE TABLE IF NOT EXISTS event_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            data       TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS newspaper_state (
            chat_id       INTEGER PRIMARY KEY,
            last_issue_at INTEGER NOT NULL DEFAULT 0
        );

        -- Ресурсы страны
        CREATE TABLE IF NOT EXISTS resources (
            country_id  INTEGER PRIMARY KEY REFERENCES countries(id) ON DELETE CASCADE,
            food        INTEGER NOT NULL DEFAULT 100,
            production  INTEGER NOT NULL DEFAULT 50,
            rare_metals INTEGER NOT NULL DEFAULT 20,
            oil         INTEGER NOT NULL DEFAULT 20,
            science     INTEGER NOT NULL DEFAULT 10,
            population  INTEGER NOT NULL DEFAULT 1000,
            last_tick   INTEGER NOT NULL DEFAULT 0
        );

        -- Здания на территориях
        CREATE TABLE IF NOT EXISTS buildings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     INTEGER NOT NULL,
            country_id  INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            iso         TEXT NOT NULL,
            type        TEXT NOT NULL,
            level       INTEGER NOT NULL DEFAULT 1,
            built_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(chat_id, iso, type)
        );

        -- Технологии стран
        CREATE TABLE IF NOT EXISTS technologies (
            country_id  INTEGER PRIMARY KEY REFERENCES countries(id) ON DELETE CASCADE,
            era         TEXT NOT NULL DEFAULT 'feudal',
            techs       TEXT NOT NULL DEFAULT '[]'
        );

        -- Виды войск
        CREATE TABLE IF NOT EXISTS military (
            country_id  INTEGER PRIMARY KEY REFERENCES countries(id) ON DELETE CASCADE,
            infantry    INTEGER NOT NULL DEFAULT 100,
            cavalry     INTEGER NOT NULL DEFAULT 0,
            artillery   INTEGER NOT NULL DEFAULT 0,
            airforce    INTEGER NOT NULL DEFAULT 0,
            navy        INTEGER NOT NULL DEFAULT 0,
            bomber      INTEGER NOT NULL DEFAULT 0,
            armored     INTEGER NOT NULL DEFAULT 0,
            tank        INTEGER NOT NULL DEFAULT 0
        );

        -- Отправка войск (прибытие через время)
        CREATE TABLE IF NOT EXISTS deployments (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id      INTEGER NOT NULL,
            attacker_id  INTEGER NOT NULL,
            target_iso   TEXT NOT NULL,
            unit_type    TEXT NOT NULL,
            unit_count   INTEGER NOT NULL,
            depart_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            arrive_at    INTEGER NOT NULL,
            resolved     INTEGER NOT NULL DEFAULT 0
        );

        -- Блоки / военные союзы
        CREATE TABLE IF NOT EXISTS blocs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     INTEGER NOT NULL,
            name        TEXT NOT NULL,
            founder_id  INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(chat_id, name)
        );

        CREATE TABLE IF NOT EXISTS bloc_members (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            bloc_id     INTEGER NOT NULL REFERENCES blocs(id) ON DELETE CASCADE,
            country_id  INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            joined_at   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(bloc_id, country_id)
        );

        -- ООН: резолюции
        CREATE TABLE IF NOT EXISTS un_resolutions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id      INTEGER NOT NULL,
            proposer_id  INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            type         TEXT NOT NULL,
            target_id    INTEGER REFERENCES countries(id) ON DELETE CASCADE,
            status       TEXT NOT NULL DEFAULT 'voting',
            votes_for    TEXT NOT NULL DEFAULT '[]',
            votes_against TEXT NOT NULL DEFAULT '[]',
            created_at   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            expires_at   INTEGER NOT NULL
        );

        -- Торговые соглашения
        CREATE TABLE IF NOT EXISTS trade_deals (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id      INTEGER NOT NULL,
            country_a_id INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            country_b_id INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            resource_a   TEXT NOT NULL,
            amount_a     INTEGER NOT NULL,
            resource_b   TEXT NOT NULL,
            amount_b     INTEGER NOT NULL,
            status       TEXT NOT NULL DEFAULT 'active',
            created_at   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(chat_id, country_a_id, country_b_id)
        );

        -- Вассалитет
        CREATE TABLE IF NOT EXISTS vassals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     INTEGER NOT NULL,
            overlord_id INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            vassal_id   INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            tribute_pct INTEGER NOT NULL DEFAULT 20,
            status      TEXT NOT NULL DEFAULT 'active',
            created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(chat_id, vassal_id)
        );

        -- Шпионаж
        CREATE TABLE IF NOT EXISTS spy_ops (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     INTEGER NOT NULL,
            attacker_id INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            target_id   INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            op_type     TEXT NOT NULL,
            result      TEXT NOT NULL DEFAULT 'pending',
            created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        -- Достижения
        CREATE TABLE IF NOT EXISTS achievements (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            country_id     INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            achievement_id TEXT NOT NULL,
            earned_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(country_id, achievement_id)
        );

        -- Гарнизоны: войска, размещённые на конкретных территориях
        CREATE TABLE IF NOT EXISTS garrisons (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     INTEGER NOT NULL,
            country_id  INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            iso         TEXT NOT NULL,
            infantry    INTEGER NOT NULL DEFAULT 0,
            cavalry     INTEGER NOT NULL DEFAULT 0,
            artillery   INTEGER NOT NULL DEFAULT 0,
            airforce    INTEGER NOT NULL DEFAULT 0,
            bomber      INTEGER NOT NULL DEFAULT 0,
            navy        INTEGER NOT NULL DEFAULT 0,
            armored     INTEGER NOT NULL DEFAULT 0,
            tank        INTEGER NOT NULL DEFAULT 0,
            updated_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(chat_id, country_id, iso)
        );

        -- Претензии на территории (casus belli)
        CREATE TABLE IF NOT EXISTS claims (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     INTEGER NOT NULL,
            claimant_id INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            iso         TEXT NOT NULL,
            claim_type  TEXT NOT NULL DEFAULT 'conquest',
            strength    INTEGER NOT NULL DEFAULT 50,
            created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(chat_id, claimant_id, iso)
        );

        -- Военная усталость стран
        CREATE TABLE IF NOT EXISTS war_weariness (
            country_id  INTEGER PRIMARY KEY REFERENCES countries(id) ON DELETE CASCADE,
            weariness   INTEGER NOT NULL DEFAULT 0,
            updated_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        -- Рынок территорий (продажа/передача)
        CREATE TABLE IF NOT EXISTS territory_market (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     INTEGER NOT NULL,
            iso         TEXT NOT NULL,
            seller_id   INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
            price       INTEGER NOT NULL DEFAULT 0,
            created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(chat_id, iso)
        );
        """)
        # Миграция для старых БД — добавляем колонки если их нет
        for col, definition in [
            ("attacker_losses", "INTEGER NOT NULL DEFAULT 0"),
            ("defender_losses", "INTEGER NOT NULL DEFAULT 0"),
            ("attacker_score",  "INTEGER NOT NULL DEFAULT 0"),
            ("defender_score",  "INTEGER NOT NULL DEFAULT 0"),
            ("ends_at",         "INTEGER NOT NULL DEFAULT 0"),
        ]:
            try:
                await db.execute(f"ALTER TABLE wars ADD COLUMN {col} {definition}")
            except Exception:
                pass  # уже есть

        # Миграция: добавляем новые колонки военных
        for mil_col in ["navy", "bomber"]:
            try:
                await db.execute(f"ALTER TABLE military ADD COLUMN {mil_col} INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass

        # Инициализируем новые таблицы для существующих стр��н
        await db.execute("""
            INSERT OR IGNORE INTO resources (country_id)
            SELECT id FROM countries
        """)
        await db.execute("""
            INSERT OR IGNORE INTO technologies (country_id)
            SELECT id FROM countries
        """)
        await db.execute("""
            INSERT OR IGNORE INTO military (country_id)
            SELECT id FROM countries
        """)
        # Миграция: колонка для почасового пассивного дохода
        try:
            await db.execute("ALTER TABLE countries ADD COLUMN last_hourly_income INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        # Миграция: username владельца (для упоминаний)
        try:
            await db.execute("ALTER TABLE countries ADD COLUMN owner_username TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        # Миграция: флаг переименования (1 раз за деньги)
        try:
            await db.execute("ALTER TABLE countries ADD COLUMN renamed INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        # Миграция: уровень счастья населения (0-100)
        try:
            await db.execute("ALTER TABLE resources ADD COLUMN happiness INTEGER NOT NULL DEFAULT 70")
        except Exception:
            pass
        # Таблица протестов / истории счастья
        await db.execute("""
            CREATE TABLE IF NOT EXISTS protest_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id     INTEGER NOT NULL,
                country_id  INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
                iso         TEXT NOT NULL,
                cause       TEXT NOT NULL DEFAULT 'unknown',
                resolved    INTEGER NOT NULL DEFAULT 0,
                created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            )
        """)
        # Миграция: таблица deployments
        await db.execute("""
            CREATE TABLE IF NOT EXISTS deployments (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id      INTEGER NOT NULL,
                attacker_id  INTEGER NOT NULL,
                target_iso   TEXT NOT NULL,
                unit_type    TEXT NOT NULL,
                unit_count   INTEGER NOT NULL,
                depart_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                arrive_at    INTEGER NOT NULL,
                resolved     INTEGER NOT NULL DEFAULT 0
            )
        """)
        # Миграция: военная усталость
        await db.execute("""
            INSERT OR IGNORE INTO war_weariness (country_id)
            SELECT id FROM countries
        """)

        # ── Дополнительные таблицы (безопасное создание) ─────────────
        _extra_tables = [
            ("coalitions", """CREATE TABLE IF NOT EXISTS coalitions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id     INTEGER NOT NULL,
                name        TEXT NOT NULL,
                leader_id   INTEGER NOT NULL,
                created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                UNIQUE(chat_id, name))"""),
            ("coalition_members", """CREATE TABLE IF NOT EXISTS coalition_members (
                coalition_id INTEGER NOT NULL,
                country_id   INTEGER NOT NULL,
                joined_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                PRIMARY KEY (coalition_id, country_id))"""),
            ("victory_points", """CREATE TABLE IF NOT EXISTS victory_points (
                country_id  INTEGER PRIMARY KEY,
                vp          INTEGER NOT NULL DEFAULT 0,
                updated_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')))"""),
            ("bombardments", """CREATE TABLE IF NOT EXISTS bombardments (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id      INTEGER NOT NULL,
                attacker_id  INTEGER NOT NULL,
                target_iso   TEXT NOT NULL,
                shells       INTEGER NOT NULL DEFAULT 0,
                created_at   INTEGER NOT NULL DEFAULT (strftime('%s','now')))"""),
            ("trade_ships", """CREATE TABLE IF NOT EXISTS trade_ships (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id      INTEGER NOT NULL,
                owner_id     INTEGER NOT NULL,
                name         TEXT NOT NULL DEFAULT 'Торговец',
                level        INTEGER NOT NULL DEFAULT 1,
                capacity     INTEGER NOT NULL DEFAULT 200,
                speed        INTEGER NOT NULL DEFAULT 3,
                status       TEXT NOT NULL DEFAULT 'idle',
                from_iso     TEXT NOT NULL DEFAULT '',
                to_iso       TEXT NOT NULL DEFAULT '',
                cargo_res    TEXT NOT NULL DEFAULT '',
                cargo_amt    INTEGER NOT NULL DEFAULT 0,
                depart_at    INTEGER NOT NULL DEFAULT 0,
                arrive_at    INTEGER NOT NULL DEFAULT 0,
                created_at   INTEGER NOT NULL DEFAULT (strftime('%s','now')))"""),
            ("ship_captures", """CREATE TABLE IF NOT EXISTS ship_captures (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id      INTEGER NOT NULL,
                ship_id      INTEGER NOT NULL,
                captor_id    INTEGER NOT NULL,
                captured_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')))"""),
        ]
        for _tname, _tsql in _extra_tables:
            try:
                await db.execute(_tsql)
            except Exception as _te:
                logging.error(f"init_db: failed to create table {_tname}: {_te}")
        try:
            await db.execute("INSERT OR IGNORE INTO victory_points (country_id) SELECT id FROM countries")
        except Exception:
            pass

        # ── НАДЁЖНАЯ миграция wars ───────────────────────────────────
        # Читаем реальные колонки таблицы wars из БД
        _pragma = await db.execute("PRAGMA table_info(wars)")
        _existing_cols = {row[1] for row in await _pragma.fetchall()}

        # Добавляем только то чего реально нет
        _needed = {
            "ends_at":       ("INTEGER", "0"),
            "ended_at":      ("INTEGER", "0"),
            "status":        ("TEXT",    "'active'"),
            "attacker_score":("INTEGER", "0"),
            "defender_score":("INTEGER", "0"),
            "attacker_losses":("INTEGER","0"),
            "defender_losses":("INTEGER","0"),
        }
        for _col, (_type, _default) in _needed.items():
            if _col not in _existing_cols:
                try:
                    await db.execute(
                        f"ALTER TABLE wars ADD COLUMN {_col} {_type} NOT NULL DEFAULT {_default}"
                    )
                    logging.info(f"wars: added missing column '{_col}'")
                except Exception as _ce:
                    logging.error(f"wars: failed to add column '{_col}': {_ce}")
        await db.commit()

        # Кулдауны расширения и удаления стран
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS cooldowns (
                    user_id    INTEGER NOT NULL,
                    chat_id    INTEGER NOT NULL,
                    action     TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, chat_id, action)
                )
            """)
        except Exception:
            pass
        _other_migrations = [
            ("vassals",  "status", "TEXT", "'active'"),
            ("spy_ops",  "status", "TEXT", "'pending'"),
            ("pacts",    "expires_at", "INTEGER", "0"),
            ("trade_ships", "troops_amt", "INTEGER", "0"),  # перевозка пехоты флотом
            ("countries", "last_nuclear", "INTEGER", "0"),
            ("countries", "last_hourly_income", "INTEGER", "0"),
            ("countries", "owner_username", "TEXT", "''"),
            ("countries", "renamed", "INTEGER", "0"),
            ("countries", "capital_iso", "TEXT", "''"),
            ("resources", "food", "INTEGER", "100"),
            ("resources", "production", "INTEGER", "50"),
            ("resources", "rare_metals", "INTEGER", "20"),
            ("resources", "oil", "INTEGER", "20"),
            ("resources", "science", "INTEGER", "10"),
            ("resources", "population", "INTEGER", "1000"),
            ("resources", "last_tick", "INTEGER", "0"),
            ("resources", "happiness", "INTEGER", "70"),
            ("military", "infantry", "INTEGER", "100"),
            ("military", "cavalry", "INTEGER", "0"),
            ("military", "artillery", "INTEGER", "0"),
            ("military", "airforce", "INTEGER", "0"),
            ("military", "navy", "INTEGER", "0"),
            ("military", "bomber", "INTEGER", "0"),
            ("military", "armored", "INTEGER", "0"),
            ("military", "tank", "INTEGER", "0"),
            ("garrisons", "infantry", "INTEGER", "0"),
            ("garrisons", "cavalry", "INTEGER", "0"),
            ("garrisons", "artillery", "INTEGER", "0"),
            ("garrisons", "airforce", "INTEGER", "0"),
            ("garrisons", "bomber", "INTEGER", "0"),
            ("garrisons", "navy", "INTEGER", "0"),
            ("garrisons", "armored", "INTEGER", "0"),
            ("garrisons", "tank", "INTEGER", "0"),
        ]
        for _tbl, _col, _type, _default in _other_migrations:
            await add_column_if_missing(db, _tbl, _col, _type, _default)
        await db.execute("UPDATE countries SET capital_iso=iso WHERE capital_iso='' OR capital_iso IS NULL")
        await db.commit()

        # ── ИНДЕКСЫ для ускорения частых SELECT/UPDATE ────────────────
        _indexes = [
            "CREATE INDEX IF NOT EXISTS idx_territories_country_id ON territories(country_id)",
            "CREATE INDEX IF NOT EXISTS idx_territories_chat_country ON territories(chat_id, country_id)",
            "CREATE INDEX IF NOT EXISTS idx_buildings_country_id ON buildings(country_id)",
            "CREATE INDEX IF NOT EXISTS idx_buildings_chat_country_iso ON buildings(chat_id, country_id, iso)",
            "CREATE INDEX IF NOT EXISTS idx_garrisons_chat_country_iso ON garrisons(chat_id, country_id, iso)",
            "CREATE INDEX IF NOT EXISTS idx_garrisons_chat_iso ON garrisons(chat_id, iso)",
            "CREATE INDEX IF NOT EXISTS idx_war_weariness_country_id ON war_weariness(country_id)",
            "CREATE INDEX IF NOT EXISTS idx_wars_chat_status ON wars(chat_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_wars_chat_id ON wars(chat_id)",
            "CREATE INDEX IF NOT EXISTS idx_countries_chat_owner ON countries(chat_id, owner_id)",
            "CREATE INDEX IF NOT EXISTS idx_countries_chat_iso ON countries(chat_id, iso)",
            "CREATE INDEX IF NOT EXISTS idx_event_log_chat_ts ON event_log(chat_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_deployments_arrive_resolved ON deployments(arrive_at, resolved)",
            "CREATE INDEX IF NOT EXISTS idx_trade_ships_status ON trade_ships(status, chat_id)",
            "CREATE INDEX IF NOT EXISTS idx_cooldowns_user_chat ON cooldowns(user_id, chat_id)",
        ]
        for _idx_sql in _indexes:
            try:
                await db.execute(_idx_sql)
            except Exception as _ie:
                logging.warning(f"init_db index: {_ie}")
        await db.commit()
        logging.info("init_db: indexes created/verified")
    # Инициализируем пул постоянных соединений после создания схемы
    await _init_pool()


async def create_country(chat_id: int, owner_id: int, name: str,
                         emoji: str, color: str, iso: str,
                         owner_username: str = "") -> Optional[dict]:
    db = await _open_db()
    async with db:
        row = await db.execute(
            "SELECT id FROM countries WHERE chat_id=? AND (owner_id=? OR iso=?)",
            (chat_id, owner_id, iso.upper())
        )
        if await row.fetchone():
            return None
        # SECURITY FIX: имя страны используется как идентификатор в /give,
        # /transfer, /attack, /ally (поиск по c["name"].lower()). Без проверки
        # уникальности можно было создать страну-двойника с чужим названием и
        # перехватывать адресованные ей переводы — поиск брал первое совпадение.
        #
        # Сравнение делаем в Python: SQLite LOWER()/UPPER() работают только с
        # ASCII и «Аурелия» == «аурелия» в SQL НЕ совпадут.
        name_c = name.strip().casefold()
        nc = await db.execute("SELECT name FROM countries WHERE chat_id=?", (chat_id,))
        for _r in await nc.fetchall():
            if (_r["name"] or "").strip().casefold() == name_c:
                return None
        trow = await db.execute(
            "SELECT country_id FROM territories WHERE chat_id=? AND iso=? AND country_id IS NOT NULL",
            (chat_id, iso.upper())
        )
        if await trow.fetchone():
            return None
        await db.execute(
            "INSERT INTO countries (chat_id, owner_id, owner_username, name, emoji, color, iso) VALUES (?,?,?,?,?,?,?)",
            (chat_id, owner_id, owner_username, name, emoji, color, iso.upper())
        )
        c = await db.execute(
            "SELECT id FROM countries WHERE chat_id=? AND owner_id=?", (chat_id, owner_id)
        )
        row = await c.fetchone()
        cid = row["id"]
        await db.execute(
            """INSERT INTO territories (chat_id, iso, country_id) VALUES (?,?,?)
               ON CONFLICT(chat_id, iso) DO UPDATE SET country_id=excluded.country_id
               WHERE territories.country_id IS NULL""",
            (chat_id, iso.upper(), cid)
        )
        own_check = await db.execute(
            "SELECT country_id FROM territories WHERE chat_id=? AND iso=?",
            (chat_id, iso.upper())
        )
        own_row = await own_check.fetchone()
        if not own_row or own_row["country_id"] != cid:
            await db.execute("DELETE FROM countries WHERE id=?", (cid,))
            await db.commit()
            return None
        # Инициализируем ресурсы, технологии, армию для новой страны
        await db.execute("INSERT OR IGNORE INTO resources (country_id) VALUES (?)", (cid,))
        await db.execute("INSERT OR IGNORE INTO technologies (country_id) VALUES (?)", (cid,))
        await db.execute("INSERT OR IGNORE INTO military (country_id, infantry) VALUES (?,100)", (cid,))
        await db.commit()
        return await get_country_by_owner(chat_id, owner_id)


async def get_country_by_owner(chat_id: int, owner_id: int) -> Optional[dict]:
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM countries WHERE chat_id=? AND owner_id=?", (chat_id, owner_id)
        )
        row = await c.fetchone()
        return dict(row) if row else None


async def get_country_by_iso(chat_id: int, iso: str) -> Optional[dict]:
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM countries WHERE chat_id=? AND iso=?", (chat_id, iso.upper())
        )
        row = await c.fetchone()
        return dict(row) if row else None


async def get_country_by_id(chat_id: int, country_id: int) -> Optional[dict]:
    """BUG FIX: добавлен chat_id — страны из разных чатов не пересекаются."""
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM countries WHERE id=? AND chat_id=?", (country_id, chat_id)
        )
        row = await c.fetchone()
        return dict(row) if row else None


async def get_all_countries(chat_id: int) -> list:
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM countries WHERE chat_id=? ORDER BY treasury DESC", (chat_id,)
        )
        return [dict(r) for r in await c.fetchall()]


async def delete_country(chat_id: int, owner_id: int):
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT id FROM countries WHERE chat_id=? AND owner_id=?", (chat_id, owner_id)
        )
        row = await c.fetchone()
        if row:
            cid = row[0]
            await db.execute("UPDATE territories SET country_id=NULL WHERE country_id=?", (cid,))
            await db.execute("DELETE FROM countries WHERE id=?", (cid,))
            await db.commit()


async def auto_delete_if_no_territories(chat_id: int, country_id: int, bot=None):
    """
    Если у страны не осталось территорий — удаляет её из БД,
    чтобы игрок мог создать новую страну. Уведомляет чат.
    """
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT COUNT(*) as cnt FROM territories WHERE chat_id=? AND country_id=?",
            (chat_id, country_id)
        )
        row = await c.fetchone()
        if row and row["cnt"] > 0:
            return  # ещё есть территории — ничего не делаем

        # Получаем данные страны перед удалением
        cc = await db.execute(
            "SELECT name, emoji, owner_username FROM countries WHERE id=? AND chat_id=?",
            (country_id, chat_id)
        )
        country = await cc.fetchone()
        if not country:
            return

        # Удаляем страну и все связанные данные
        await db.execute("DELETE FROM cooldowns WHERE chat_id=? AND user_id IN "
                         "(SELECT owner_id FROM countries WHERE id=?)", (chat_id, country_id))
        await db.execute("DELETE FROM resources WHERE country_id=?", (country_id,))
        await db.execute("DELETE FROM buildings WHERE chat_id=? AND country_id=?", (chat_id, country_id))
        await db.execute("DELETE FROM technologies WHERE country_id=?", (country_id,))
        await db.execute("DELETE FROM trade_ships WHERE chat_id=? AND owner_id=?", (chat_id, country_id))
        await db.execute("DELETE FROM countries WHERE id=?", (country_id,))
        await db.commit()

    if bot:
        mention = f"@{country['owner_username']}" if country["owner_username"] else country["name"]
        try:
            await bot.send_message(
                chat_id,
                f"🏳️ <b>Страна уничтожена!</b>\n\n"
                f"{country['emoji']} <b>{country['name']}</b> ({mention}) "
                f"потеряла все свои территории и прекратила существование.\n\n"
                f"Игрок может создать новую страну командой /create",
                parse_mode="HTML"
            )
        except Exception:
            pass


async def get_territories(chat_id: int) -> dict:
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT iso, country_id FROM territories WHERE chat_id=? AND country_id IS NOT NULL",
            (chat_id,)
        )
        return {r["iso"]: r["country_id"] for r in await c.fetchall()}


async def get_country_territories(chat_id: int, country_id: int) -> list:
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT iso FROM territories WHERE chat_id=? AND country_id=?", (chat_id, country_id)
        )
        return [r[0] for r in await c.fetchall()]


async def capture_territory(chat_id: int, iso: str, country_id: int, bot=None):
    """Захватывает территорию: переносит здания победителю.
    Если захвачена СТОЛИЦА — все здания страны переходят захватчику.
    """
    iso = iso.upper()
    db = await _open_db()
    async with db:
        # Узнаём текущего владельца территории
        cur_c = await db.execute(
            "SELECT country_id FROM territories WHERE chat_id=? AND iso=?", (chat_id, iso)
        )
        cur_row = await cur_c.fetchone()
        prev_owner_id = cur_row["country_id"] if cur_row else None

        # Переносим территорию
        await db.execute(
            """INSERT INTO territories (chat_id, iso, country_id) VALUES (?,?,?)
               ON CONFLICT(chat_id, iso) DO UPDATE SET country_id=excluded.country_id""",
            (chat_id, iso, country_id)
        )

        if prev_owner_id and prev_owner_id != country_id:
            # Проверяем — является ли захваченная территория СТОЛИЦЕЙ предыдущего владельца
            cap_c = await db.execute(
                "SELECT capital_iso FROM countries WHERE id=?", (prev_owner_id,)
            )
            cap_row = await cap_c.fetchone()
            is_capital = cap_row and cap_row["capital_iso"].upper() == iso

            if is_capital:
                # 🏯 Захват столицы — все здания страны переходят захватчику
                await db.execute(
                    "UPDATE buildings SET country_id=? WHERE chat_id=? AND country_id=?",
                    (country_id, chat_id, prev_owner_id)
                )
            else:
                # Обычный захват: только здания на этой территории
                b_c = await db.execute(
                    "SELECT * FROM buildings WHERE chat_id=? AND iso=?", (chat_id, iso)
                )
                buildings = await b_c.fetchall()
                for b in buildings:
                    orig = b["country_id"]
                    if country_id != orig:
                        await db.execute(
                            """INSERT INTO buildings (chat_id, country_id, iso, type, level, built_at)
                               VALUES (?,?,?,?,?,?)
                               ON CONFLICT(chat_id, iso, type)
                               DO UPDATE SET country_id=excluded.country_id""",
                            (chat_id, country_id, iso, b["type"], b["level"], b["built_at"])
                        )
        # Если потерянная территория была столицей — сбрасываем capital_iso на любую оставшуюся
        if prev_owner_id and prev_owner_id != country_id:
            cap_check = await db.execute(
                "SELECT capital_iso FROM countries WHERE id=?", (prev_owner_id,)
            )
            cap_check_row = await cap_check.fetchone()
            if cap_check_row and (cap_check_row["capital_iso"] or "").upper() == iso.upper():
                # Ищем любую оставшуюся территорию
                new_cap_c = await db.execute(
                    "SELECT iso FROM territories WHERE chat_id=? AND country_id=? LIMIT 1",
                    (chat_id, prev_owner_id)
                )
                new_cap_row = await new_cap_c.fetchone()
                new_cap = new_cap_row["iso"] if new_cap_row else ""
                await db.execute(
                    "UPDATE countries SET capital_iso=? WHERE id=?", (new_cap, prev_owner_id)
                )
        await db.commit()

    # Если у предыдущего владельца не осталось территорий — удаляем страну
    if prev_owner_id and prev_owner_id != country_id:
        await auto_delete_if_no_territories(chat_id, prev_owner_id, bot=bot)


async def get_capital(chat_id: int, country_id: int) -> str:
    """Возвращает ISO столицы страны."""
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT capital_iso, iso FROM countries WHERE id=? AND chat_id=?", (country_id, chat_id)
        )
        row = await c.fetchone()
        if row:
            return (row[0] or row[1]).upper()
        return ""


async def set_capital(chat_id: int, country_id: int, iso: str):
    """Устанавливает новую столицу страны (только если территория принадлежит стране)."""
    iso = iso.upper()
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT id FROM territories WHERE chat_id=? AND iso=? AND country_id=?",
            (chat_id, iso, country_id)
        )
        if not await c.fetchone():
            return False
        await db.execute(
            "UPDATE countries SET capital_iso=? WHERE id=? AND chat_id=?",
            (iso, country_id, chat_id)
        )
        await db.commit()
        return True


async def recapture_territory(chat_id: int, iso: str, original_owner_id: int):
    """Отвоевание территории: здания возвращаются оригинальному владельцу."""
    iso = iso.upper()
    db = await _open_db()
    async with db:
        # Переносим территорию обратно
        await db.execute(
            """INSERT INTO territories (chat_id, iso, country_id) VALUES (?,?,?)
               ON CONFLICT(chat_id, iso) DO UPDATE SET country_id=excluded.country_id""",
            (chat_id, iso, original_owner_id)
        )
        # Возвращаем здания оригинальному владельцу
        await db.execute(
            "UPDATE buildings SET country_id=? WHERE chat_id=? AND iso=?",
            (original_owner_id, chat_id, iso)
        )
        await db.commit()


TAX_COOLDOWN = 3600


def make_owner_mention(country: dict) -> str:
    """Возвращает упоминание владельца страны.
    Если у него есть username — кликабельная @ссылка,
    иначе — tg://user?id= ссылка (тегает без username).
    """
    uid = country.get("owner_id", 0)
    uname = country.get("owner_username", "")
    if uname:
        return f"<a href='tg://resolve?domain={uname}'>@{uname}</a>"
    return f"<a href='tg://user?id={uid}'>игрок</a>"


def make_country_str(country: dict, mention: bool = True) -> str:
    """Возвращает строку вида «Флаг <b>Название</b> (@owner)»."""
    base = f"{country['emoji']} <b>{country['name']}</b>"
    if mention:
        return base + f" ({make_owner_mention(country)})"
    return base


async def collect_taxes(chat_id: int, owner_id: int) -> tuple:
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM countries WHERE chat_id=? AND owner_id=?", (chat_id, owner_id)
        )
        country = await c.fetchone()
        if not country:
            return False, 0, "У вас нет страны"

        now = int(time.time())
        if now - country["last_tax"] < TAX_COOLDOWN:
            remaining = TAX_COOLDOWN - (now - country["last_tax"])
            m, s = divmod(remaining, 60)
            return False, 0, f"Следующий сбор через {m}м {s}с"

        ter_c = await db.execute(
            "SELECT COUNT(*) FROM territories WHERE chat_id=? AND country_id=?",
            (chat_id, country["id"])
        )
        ter_count = (await ter_c.fetchone())[0]
        # Diminishing returns: раньше доход рос линейно от числа территорий
        # (tax_rate * ter_count) без ограничений, из-за чего лидеры отрывались
        # экспоненциально. Теперь рост дохода замедляется с каждой новой землёй.
        effective_ter = max(ter_count, 1) ** 0.65
        amount = max(1, int(country["tax_rate"] * effective_ter))

        await db.execute(
            "UPDATE countries SET treasury=treasury+?, last_tax=? WHERE id=?",
            (amount, now, country["id"])
        )
        await db.commit()
        return True, amount, f"Собрано {amount}💰 ({ter_count} терр., diminishing returns)"


async def update_treasury(country_id: int, delta: int):
    db = await _open_db()
    async with db:
        await db.execute(
            "UPDATE countries SET treasury=MAX(0, treasury+?) WHERE id=?", (delta, country_id)
        )
        await db.commit()


async def update_army(country_id: int, delta: int):
    db = await _open_db()
    async with db:
        await db.execute(
            "UPDATE countries SET army=MAX(0, army+?) WHERE id=?", (delta, country_id)
        )
        await db.commit()


async def buy_army(chat_id: int, owner_id: int, amount: int) -> tuple:
    # SECURITY FIX: защита от отрицательных/гигантских значений
    if amount <= 0:
        return False, "Количество должно быть положительным"
    if amount > 1_000_000:
        return False, "Слишком большое количество (максимум 1 000 000)"
    cost = amount * 10
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM countries WHERE chat_id=? AND owner_id=?", (chat_id, owner_id)
        )
        country = await c.fetchone()
        if not country:
            return False, "У вас нет страны"
        if country["treasury"] < cost:
            return False, f"Нужно {cost}💰, у вас {country['treasury']}💰"
        # Проверяем лимит армии (infantry_cap от казарм)
        bc = await db.execute(
            "SELECT COALESCE(SUM(level),0) as lvl FROM buildings WHERE chat_id=? AND country_id=? AND type='barracks'",
            (chat_id, country["id"])
        )
        br = await bc.fetchone()
        barracks_lvl = br["lvl"] if br else 0
        infantry_cap = 500 + barracks_lvl * 200  # базово 500 + 200 за уровень казарм
        await db.execute("INSERT OR IGNORE INTO military (country_id, infantry) VALUES (?, 0)", (country["id"],))
        mc = await db.execute("SELECT infantry FROM military WHERE country_id=?", (country["id"],))
        mil = await mc.fetchone()
        current_infantry = mil["infantry"] if mil else 0
        if current_infantry + amount > infantry_cap:
            return False, f"Превышен лимит пехоты! Максимум: {infantry_cap} (стройте казармы /build barracks). Сейчас в пуле: {current_infantry}"
        await db.execute(
            "UPDATE countries SET treasury=treasury-?, army=army+? WHERE id=?",
            (cost, amount, country["id"])
        )
        await db.execute(
            "UPDATE military SET infantry=infantry+? WHERE country_id=?",
            (amount, country["id"])
        )
        await db.commit()
        return True, f"Куплено {amount} солдат за {cost}💰 (пул пехоты: {current_infantry+amount}/{infantry_cap})\n⚠️ Войска в пуле — разместите их по территориям командой /garrison ISO пехота {amount}"


async def declare_war(chat_id: int, attacker_id: int, defender_id: int) -> tuple:
    db = await _open_db()
    async with db:
        c = await db.execute(
            """SELECT id FROM alliances WHERE chat_id=? AND (
               (country_a_id=? AND country_b_id=?) OR (country_a_id=? AND country_b_id=?))""",
            (chat_id, attacker_id, defender_id, defender_id, attacker_id)
        )
        if await c.fetchone():
            return False, "Нельзя объявить войну союзнику!"

        c = await db.execute(
            """SELECT id FROM pacts WHERE chat_id=? AND type='non_aggression' AND (
               (country_a_id=? AND country_b_id=?) OR (country_a_id=? AND country_b_id=?))""",
            (chat_id, attacker_id, defender_id, defender_id, attacker_id)
        )
        if await c.fetchone():
            return False, "У вас пакт о ненападении с этой страной!"

        # Вассал не может воевать против сюзерена и наоборот
        c = await db.execute(
            """SELECT id FROM vassals WHERE chat_id=? AND status='active' AND (
               (overlord_id=? AND vassal_id=?) OR (overlord_id=? AND vassal_id=?))""",
            (chat_id, attacker_id, defender_id, defender_id, attacker_id)
        )
        if await c.fetchone():
            return False, "Нельзя объявить войну: вассалитет запрещает это!"

        c = await db.execute(
            """SELECT id FROM wars WHERE chat_id=? AND status='active' AND (
               (attacker_id=? AND defender_id=?) OR (attacker_id=? AND defender_id=?))""",
            (chat_id, attacker_id, defender_id, defender_id, attacker_id)
        )
        if await c.fetchone():
            return False, "Война уже идёт!"

        # Кулдаун 2 часа после мира между теми же странами
        _two_hours_ago = int(__import__('time').time()) - 7200
        c = await db.execute(
            """SELECT ended_at FROM wars WHERE chat_id=? AND status='ended' AND ended_at > ? AND (
               (attacker_id=? AND defender_id=?) OR (attacker_id=? AND defender_id=?))
               ORDER BY ended_at DESC LIMIT 1""",
            (chat_id, _two_hours_ago, attacker_id, defender_id, defender_id, attacker_id)
        )
        cooldown_row = await c.fetchone()
        if cooldown_row:
            remaining = (cooldown_row[0] + 7200) - int(__import__('time').time())
            mins = remaining // 60
            return False, f"После мира нельзя сразу объявить войну. Подождите ещё {mins} мин."

        _now = int(__import__('time').time())
        await db.execute(
            "INSERT INTO wars (chat_id, attacker_id, defender_id, started_at) VALUES (?,?,?,?)",
            (chat_id, attacker_id, defender_id, _now)
        )
        await db.commit()
        return True, "Война объявлена! Она длится до заключения мира (/peace)."


async def get_active_war(chat_id: int, a_id: int, b_id: int) -> Optional[dict]:
    db = await _open_db()
    async with db:
        c = await db.execute(
            """SELECT * FROM wars WHERE chat_id=? AND status='active' AND (
               (attacker_id=? AND defender_id=?) OR (attacker_id=? AND defender_id=?))""",
            (chat_id, a_id, b_id, b_id, a_id)
        )
        row = await c.fetchone()
        return dict(row) if row else None


async def end_war(war_id: int, winner_id: int, loser_id: int) -> int:
    """Военные трофеи.

    Раньше победитель забирал 30% казны проигравшего без ограничений —
    это усиливало снежный ком (сильные становятся ещё сильнее за счёт
    слабых, без какого-либо противовеса). Снижаем долю и вводим потолок
    в абсолютных величинах, чтобы захват казны одного богатого игрока не
    давал разгромного одноразового буста лидеру.
    """
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT treasury FROM countries WHERE id=?", (loser_id,))
        loser = await c.fetchone()
        loser_treasury = loser["treasury"] if loser else 0
        spoils = min(int(loser_treasury * 0.15), 2000)
        await db.execute("UPDATE countries SET treasury=MAX(0,treasury-?) WHERE id=?", (spoils, loser_id))
        await db.execute("UPDATE countries SET treasury=treasury+? WHERE id=?", (spoils, winner_id))
        await db.execute("UPDATE wars SET status='ended', ended_at=? WHERE id=?", (int(__import__('time').time()), war_id))
        await db.commit()
        return spoils


async def get_active_wars(chat_id: int) -> list:
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM wars WHERE chat_id=? AND status='active'", (chat_id,)
        )
        return [dict(r) for r in await c.fetchall()]


# ── Лог событий для газеты ───────────────────────────────────────


async def log_event(chat_id: int, event_type: str, data: dict):
    """Записывает событие в лог для последующей публикации в газете."""
    db = await _open_db()
    async with db:
        await db.execute(
            "INSERT INTO event_log (chat_id, event_type, data) VALUES (?,?,?)",
            (chat_id, event_type, json.dumps(data, ensure_ascii=False))
        )
        await db.commit()


async def get_events_since(chat_id: int, since_ts: int) -> list:
    """Возвращает все события чата после указанного timestamp."""
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM event_log WHERE chat_id=? AND created_at>? ORDER BY created_at ASC",
            (chat_id, since_ts)
        )
        rows = await c.fetchall()
        return [{"type": r["event_type"], "data": json.loads(r["data"]), "ts": r["created_at"]} for r in rows]


async def get_newspaper_last(chat_id: int) -> int:
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT last_issue_at FROM newspaper_state WHERE chat_id=?", (chat_id,))
        row = await c.fetchone()
        return row[0] if row else 0


async def set_newspaper_last(chat_id: int, ts: int):
    db = await _open_db()
    async with db:
        await db.execute(
            "INSERT INTO newspaper_state (chat_id, last_issue_at) VALUES (?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET last_issue_at=excluded.last_issue_at",
            (chat_id, ts)
        )
        await db.commit()


# ── Потери в войне ───────────────────────────────────────────────

async def add_war_losses(war_id: int, attacker_losses: int, defender_losses: int):
    """Накапливает суммарные потери в войне (хранятся в JSON поле wars.data)."""
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT attacker_losses, defender_losses FROM wars WHERE id=?", (war_id,))
        row = await c.fetchone()
        if row is None:
            return
        al = (row[0] or 0) + attacker_losses
        dl = (row[1] or 0) + defender_losses
        await db.execute(
            "UPDATE wars SET attacker_losses=?, defender_losses=? WHERE id=?",
            (al, dl, war_id)
        )
        await db.commit()


async def spend_treasury(country_id: int, amount: int) -> bool:
    """BUG FIX: атомарное списание золота.

    Раньше баланс читался одним запросом, а списывался другим (или через
    update_treasury с MAX(0, ...)). Два одновременных /transfer, /give,
    /reinforce проходили проверку по одному и тому же снимку казны: у
    отправителя баланс обрезался до нуля, а получатели получали ПОЛНУЮ сумму
    каждый — золото печаталось из воздуха.

    Здесь проверка и списание — один SQL-оператор, поэтому второй вызов
    гарантированно не найдёт строку и вернёт False.
    """
    if amount < 0:
        return False
    if amount == 0:
        return True
    db = await _open_db()
    async with db:
        cur = await db.execute(
            "UPDATE countries SET treasury=treasury-? WHERE id=? AND treasury>=?",
            (amount, country_id, amount)
        )
        await db.commit()
        return (cur.rowcount or 0) > 0


async def spend_army(country_id: int, amount: int) -> bool:
    """Атомарное списание пехоты из пула.

    BUG FIX: countries.army — это зеркало суммы military.*. Раньше /expand
    уменьшал только countries.army, а military.infantry оставался прежним:
    счётчики расходились, и «потраченные» солдаты продолжали числиться в пуле,
    из которого их можно было разместить в гарнизон ещё раз.
    """
    if amount < 0:
        return False
    if amount == 0:
        return True
    db = await _open_db()
    async with db:
        cur = await db.execute(
            "UPDATE military SET infantry=infantry-? WHERE country_id=? AND infantry>=?",
            (amount, country_id, amount)
        )
        if (cur.rowcount or 0) == 0:
            return False
        await db.execute(
            "UPDATE countries SET army=MAX(0,army-?) WHERE id=?", (amount, country_id)
        )
        await db.commit()
        return True


async def try_claim_cooldown(user_id: int, chat_id: int, action: str, seconds: int) -> int:
    """Атомарно занимает кулдаун. 0 — успех, >0 — сколько секунд осталось.

    BUG FIX: раньше get_cooldown() и set_cooldown() были двумя отдельными
    транзакциями, между которыми handler успевал сходить в БД и в Telegram.
    Двойной быстрый /expand или /attack проходил обе проверки и выполнялся
    дважды (бесплатная экспансия, двойное списание гарнизона).
    """
    now = int(time.time())
    expires_at = now + max(0, seconds)
    db = await _open_db()
    async with db:
        cur = await db.execute(
            """INSERT INTO cooldowns (user_id, chat_id, action, expires_at)
               VALUES (?,?,?,?)
               ON CONFLICT(user_id, chat_id, action) DO UPDATE SET expires_at=excluded.expires_at
               WHERE cooldowns.expires_at <= ?""",
            (user_id, chat_id, action, expires_at, now)
        )
        if (cur.rowcount or 0) > 0:
            await db.commit()
            return 0
        c = await db.execute(
            "SELECT expires_at FROM cooldowns WHERE user_id=? AND chat_id=? AND action=?",
            (user_id, chat_id, action)
        )
        row = await c.fetchone()
        await db.commit()
        return max(1, (row[0] - now)) if row else 0


async def get_cooldown(user_id: int, chat_id: int, action: str) -> int:
    """Возвращает оставшееся время кулдауна в секундах (0 = нет кулдауна)."""
    now = int(__import__('time').time())
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT expires_at FROM cooldowns WHERE user_id=? AND chat_id=? AND action=?",
            (user_id, chat_id, action)
        )
        row = await c.fetchone()
        if not row:
            return 0
        return max(0, row[0] - now)


async def set_cooldown(user_id: int, chat_id: int, action: str, seconds: int):
    """Устанавливает кулдаун."""
    expires_at = int(__import__('time').time()) + seconds
    db = await _open_db()
    async with db:
        await db.execute(
            "INSERT OR REPLACE INTO cooldowns (user_id, chat_id, action, expires_at) VALUES (?,?,?,?)",
            (user_id, chat_id, action, expires_at)
        )
        await db.commit()


async def clear_cooldown(user_id: int, chat_id: int, action: str):
    db = await _open_db()
    async with db:
        await db.execute(
            "DELETE FROM cooldowns WHERE user_id=? AND chat_id=? AND action=?",
            (user_id, chat_id, action)
        )
        await db.commit()


def fmt_duration(seconds: int) -> str:
    """Форматирует секунды в читаемый вид."""
    if seconds < 60:
        return f"{seconds}с"
    elif seconds < 3600:
        return f"{seconds // 60}м {seconds % 60}с"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}ч {m}м"


# ── Константы кулдаунов ──────────────────────────────────────────
EXPAND_COOLDOWN_SEC  = 3 * 3600   # 3 часа между экспансиями
DELETE_COOLDOWN_SEC  = 24 * 3600  # 24 часа между удалениями страны


async def get_all_chats_with_active_wars() -> list:
    """Для фонового тика — все chat_id где есть активные войны."""
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT DISTINCT chat_id FROM wars WHERE status='active'")
        return [r[0] for r in await c.fetchall()]


# ── Победные очки (Victory Points) ──────────────────────────────

async def add_vp(country_id: int, amount: int):
    db = await _open_db()
    async with db:
        await db.execute("""
            INSERT INTO victory_points (country_id, vp) VALUES (?,?)
            ON CONFLICT(country_id) DO UPDATE SET vp=MAX(0,vp+?), updated_at=strftime('%s','now')
        """, (country_id, amount, amount))
        await db.commit()


async def get_vp(country_id: int) -> int:
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT vp FROM victory_points WHERE country_id=?", (country_id,))
        row = await c.fetchone()
        return row[0] if row else 0


async def get_vp_leaderboard(chat_id: int) -> list:
    """Топ стран по VP в чате."""
    db = await _open_db()
    async with db:
        c = await db.execute("""
            SELECT c.name, c.emoji, c.owner_username, vp.vp
            FROM victory_points vp
            JOIN countries c ON c.id=vp.country_id
            WHERE c.chat_id=?
            ORDER BY vp.vp DESC LIMIT 10
        """, (chat_id,))
        return [dict(r) for r in await c.fetchall()]


async def tick_victory_points(chat_id: int):
    """Каждый час начисляем VP: территории + армия + здания.
    Оптимизировано: один запрос на весь чат вместо N отдельных.
    """
    countries = await get_all_countries(chat_id)
    if not countries:
        return
    cids = [c["id"] for c in countries]
    placeholders = ",".join("?" * len(cids))

    db = await _open_db()
    async with db:
        # Территории всех стран одним запросом
        ter_rows = await (await db.execute(
            f"SELECT country_id, COUNT(*) as cnt FROM territories "
            f"WHERE chat_id=? AND country_id IN ({placeholders}) GROUP BY country_id",
            [chat_id] + cids
        )).fetchall()
        ter_by_cid = {r[0]: r[1] for r in ter_rows}

        # Здания — сумма уровней
        bld_rows = await (await db.execute(
            f"SELECT country_id, COALESCE(SUM(level),0) as bl FROM buildings "
            f"WHERE chat_id=? AND country_id IN ({placeholders}) GROUP BY country_id",
            [chat_id] + cids
        )).fetchall()
        bld_by_cid = {r[0]: r[1] for r in bld_rows}

        # Массовый UPDATE victory_points
        for country in countries:
            cid         = country["id"]
            ter_count   = ter_by_cid.get(cid, 0)
            building_vp = bld_by_cid.get(cid, 0) // 5
            army_vp     = country["army"] // 100
            vp_gained   = ter_count + army_vp + building_vp
            if vp_gained > 0:
                await db.execute(
                    "INSERT INTO victory_points (country_id, vp) VALUES (?,?) "
                    "ON CONFLICT(country_id) DO UPDATE SET vp=MAX(0,vp+?), updated_at=strftime('%s','now')",
                    (cid, vp_gained, vp_gained)
                )
        await db.commit()


async def get_all_chats() -> list:
    """Все chat_id где есть хоть одна страна — для газеты."""
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT DISTINCT chat_id FROM countries")
        return [r[0] for r in await c.fetchall()]


async def propose_alliance(chat_id: int, a_id: int, b_id: int) -> tuple:
    db = await _open_db()
    async with db:
        c = await db.execute(
            """SELECT id FROM alliances WHERE chat_id=? AND (
               (country_a_id=? AND country_b_id=?) OR (country_a_id=? AND country_b_id=?))""",
            (chat_id, a_id, b_id, b_id, a_id)
        )
        if await c.fetchone():
            return False, "Союз уже существует!"
        war = await get_active_war(chat_id, a_id, b_id)
        if war:
            return False, "Нельзя заключить союз во время войны!"
        await db.execute(
            "INSERT OR IGNORE INTO alliances (chat_id, country_a_id, country_b_id) VALUES (?,?,?)",
            (chat_id, min(a_id, b_id), max(a_id, b_id))
        )
        await db.commit()
        return True, "Союз заключён!"


async def break_alliance(chat_id: int, a_id: int, b_id: int):
    db = await _open_db()
    async with db:
        await db.execute(
            """DELETE FROM alliances WHERE chat_id=? AND (
               (country_a_id=? AND country_b_id=?) OR (country_a_id=? AND country_b_id=?))""",
            (chat_id, a_id, b_id, b_id, a_id)
        )
        await db.commit()


async def get_allies(chat_id: int, country_id: int) -> list:
    db = await _open_db()
    async with db:
        c = await db.execute(
            """SELECT CASE WHEN country_a_id=? THEN country_b_id ELSE country_a_id END
               FROM alliances WHERE chat_id=? AND (country_a_id=? OR country_b_id=?)""",
            (country_id, chat_id, country_id, country_id)
        )
        return [r[0] for r in await c.fetchall()]


async def propose_pact(chat_id: int, a_id: int, b_id: int) -> tuple:
    db = await _open_db()
    async with db:
        c = await db.execute(
            """SELECT id FROM pacts WHERE chat_id=? AND (
               (country_a_id=? AND country_b_id=?) OR (country_a_id=? AND country_b_id=?))""",
            (chat_id, a_id, b_id, b_id, a_id)
        )
        if await c.fetchone():
            return False, "Пакт уже существует!"
        await db.execute(
            "INSERT OR IGNORE INTO pacts (chat_id, country_a_id, country_b_id, type) VALUES (?,?,?,'non_aggression')",
            (chat_id, min(a_id, b_id), max(a_id, b_id))
        )
        await db.commit()
        return True, "Пакт о ненападении заключён!"


async def get_leaderboard(chat_id: int) -> list:
    db = await _open_db()
    async with db:
        c = await db.execute(
            """SELECT c.*, COUNT(t.iso) as ter_count
               FROM countries c
               LEFT JOIN territories t ON t.country_id=c.id AND t.chat_id=c.chat_id
               WHERE c.chat_id=?
               GROUP BY c.id
               ORDER BY ter_count DESC, c.treasury DESC""",
            (chat_id,)
        )
        return [dict(r) for r in await c.fetchall()]



# ═══════════════════════════════════════════════════════════════
#  КОНСТАНТЫ: ЗДАНИЯ, ТЕХНОЛОГИИ, РЕСУРСЫ
# ═══════════════════════════════════════════════════════════════

REAL_NAMES = {
    'AD': {'en': 'Andorra', 'ru': 'Андорра'},
    'AE': {'en': 'United Arab Emirates', 'ru': 'Объединённые Арабские Эмираты'},
    'AF': {'en': 'Afghanistan', 'ru': 'Афганистан'},
    'AG': {'en': 'Antigua and Barb.', 'ru': 'Антигуа и Барбуда'},
    'AI': {'en': 'Anguilla', 'ru': 'Ангилья'},
    'AL': {'en': 'Albania', 'ru': 'Албания'},
    'AM': {'en': 'Armenia', 'ru': 'Армения'},
    'AO': {'en': 'Angola', 'ru': 'Ангола'},
    'AR': {'en': 'Argentina', 'ru': 'Аргентина'},
    'AS': {'en': 'American Samoa', 'ru': 'Американское Самоа'},
    'AT': {'en': 'Austria', 'ru': 'Австрия'},
    'AU': {'en': 'Australia', 'ru': 'Австралия'},
    'AW': {'en': 'Aruba', 'ru': 'Аруба'},
    'AX': {'en': 'Åland', 'ru': 'Аландские острова'},
    'AZ': {'en': 'Azerbaijan', 'ru': 'Азербайджан'},
    'BA': {'en': 'Bosnia and Herz.', 'ru': 'Босния и Герцеговина'},
    'BB': {'en': 'Barbados', 'ru': 'Барбадос'},
    'BD': {'en': 'Bangladesh', 'ru': 'Бангладеш'},
    'BE': {'en': 'Belgium', 'ru': 'Бельгия'},
    'BF': {'en': 'Burkina Faso', 'ru': 'Буркина-Фасо'},
    'BG': {'en': 'Bulgaria', 'ru': 'Болгария'},
    'BH': {'en': 'Bahrain', 'ru': 'Бахрейн'},
    'BI': {'en': 'Burundi', 'ru': 'Бурунди'},
    'BJ': {'en': 'Benin', 'ru': 'Бенин'},
    'BL': {'en': 'St-Barthélemy', 'ru': 'Сен-Бартелеми'},
    'BM': {'en': 'Bermuda', 'ru': 'Бермудские Острова'},
    'BN': {'en': 'Brunei', 'ru': 'Бруней'},
    'BO': {'en': 'Bolivia', 'ru': 'Боливия'},
    'BR': {'en': 'Brazil', 'ru': 'Бразилия'},
    'BS': {'en': 'Bahamas', 'ru': 'Багамские Острова'},
    'BT': {'en': 'Bhutan', 'ru': 'Бутан'},
    'BW': {'en': 'Botswana', 'ru': 'Ботсвана'},
    'BY': {'en': 'Belarus', 'ru': 'Белоруссия'},
    'BZ': {'en': 'Belize', 'ru': 'Белиз'},
    'CA': {'en': 'Canada', 'ru': 'Канада'},
    'CD': {'en': 'Dem. Rep. Congo', 'ru': 'Демократическая Республика Конго'},
    'CF': {'en': 'Central African Rep.', 'ru': 'Центральноафриканская Республика'},
    'CG': {'en': 'Congo', 'ru': 'Республика Конго'},
    'CH': {'en': 'Switzerland', 'ru': 'Швейцария'},
    'CI': {'en': 'Côte d\'Ivoire', 'ru': 'Кот-д’Ивуар'},
    'CK': {'en': 'Cook Is.', 'ru': 'Острова Кука'},
    'CL': {'en': 'Chile', 'ru': 'Чили'},
    'CM': {'en': 'Cameroon', 'ru': 'Камерун'},
    'CN': {'en': 'China', 'ru': 'Китайская Народная Республика'},
    'CO': {'en': 'Colombia', 'ru': 'Колумбия'},
    'CR': {'en': 'Costa Rica', 'ru': 'Коста-Рика'},
    'CU': {'en': 'Cuba', 'ru': 'Куба'},
    'CV': {'en': 'Cabo Verde', 'ru': 'Кабо-Верде'},
    'CW': {'en': 'Curaçao', 'ru': 'Кюрасао'},
    'CY': {'en': 'Cyprus', 'ru': 'Кипр'},
    'CZ': {'en': 'Czechia', 'ru': 'Чехия'},
    'DE': {'en': 'Germany', 'ru': 'Германия'},
    'DJ': {'en': 'Djibouti', 'ru': 'Джибути'},
    'DK': {'en': 'Denmark', 'ru': 'Дания'},
    'DM': {'en': 'Dominica', 'ru': 'Доминика'},
    'DO': {'en': 'Dominican Rep.', 'ru': 'Доминиканская Республика'},
    'DZ': {'en': 'Algeria', 'ru': 'Алжир'},
    'EC': {'en': 'Ecuador', 'ru': 'Эквадор'},
    'EE': {'en': 'Estonia', 'ru': 'Эстония'},
    'EG': {'en': 'Egypt', 'ru': 'Египет'},
    'EH': {'en': 'W. Sahara', 'ru': 'Западная Сахара'},
    'ER': {'en': 'Eritrea', 'ru': 'Эритрея'},
    'ES': {'en': 'Spain', 'ru': 'Испания'},
    'ET': {'en': 'Ethiopia', 'ru': 'Эфиопия'},
    'FI': {'en': 'Finland', 'ru': 'Финляндия'},
    'FJ': {'en': 'Fiji', 'ru': 'Фиджи'},
    'FK': {'en': 'Falkland Is.', 'ru': 'Фолклендские острова'},
    'FM': {'en': 'Micronesia', 'ru': 'Микронезия'},
    'FO': {'en': 'Faeroe Is.', 'ru': 'Фарерские острова'},
    'FR': {'en': 'France', 'ru': 'Франция'},
    'GA': {'en': 'Gabon', 'ru': 'Габон'},
    'GD': {'en': 'Grenada', 'ru': 'Гренада'},
    'GE': {'en': 'Georgia', 'ru': 'Грузия'},
    'GG': {'en': 'Guernsey', 'ru': 'Гернси'},
    'GH': {'en': 'Ghana', 'ru': 'Гана'},
    'GL': {'en': 'Greenland', 'ru': 'Гренландия'},
    'GM': {'en': 'Gambia', 'ru': 'Гамбия'},
    'GN': {'en': 'Guinea', 'ru': 'Гвинея'},
    'GQ': {'en': 'Eq. Guinea', 'ru': 'Экваториальная Гвинея'},
    'GR': {'en': 'Greece', 'ru': 'Греция'},
    'GT': {'en': 'Guatemala', 'ru': 'Гватемала'},
    'GU': {'en': 'Guam', 'ru': 'Гуам'},
    'GW': {'en': 'Guinea-Bissau', 'ru': 'Гвинея-Бисау'},
    'GY': {'en': 'Guyana', 'ru': 'Гайана'},
    'HK': {'en': 'Hong Kong', 'ru': 'Гонконг'},
    'HN': {'en': 'Honduras', 'ru': 'Гондурас'},
    'HR': {'en': 'Croatia', 'ru': 'Хорватия'},
    'HT': {'en': 'Haiti', 'ru': 'Республика Гаити'},
    'HU': {'en': 'Hungary', 'ru': 'Венгрия'},
    'ID': {'en': 'Indonesia', 'ru': 'Индонезия'},
    'IE': {'en': 'Ireland', 'ru': 'Ирландия'},
    'IL': {'en': 'Israel', 'ru': 'Израиль'},
    'IM': {'en': 'Isle of Man', 'ru': 'остров Мэн'},
    'IN': {'en': 'India', 'ru': 'Индия'},
    'IQ': {'en': 'Iraq', 'ru': 'Ирак'},
    'IR': {'en': 'Iran', 'ru': 'Иран'},
    'IS': {'en': 'Iceland', 'ru': 'Исландия'},
    'IT': {'en': 'Italy', 'ru': 'Италия'},
    'JE': {'en': 'Jersey', 'ru': 'Джерси'},
    'JM': {'en': 'Jamaica', 'ru': 'Ямайка'},
    'JO': {'en': 'Jordan', 'ru': 'Иордания'},
    'JP': {'en': 'Japan', 'ru': 'Япония'},
    'KE': {'en': 'Kenya', 'ru': 'Кения'},
    'KG': {'en': 'Kyrgyzstan', 'ru': 'Киргизия'},
    'KH': {'en': 'Cambodia', 'ru': 'Камбоджа'},
    'KI': {'en': 'Kiribati', 'ru': 'Кирибати'},
    'KM': {'en': 'Comoros', 'ru': 'Коморы'},
    'KN': {'en': 'St. Kitts and Nevis', 'ru': 'Сент-Китс и Невис'},
    'KP': {'en': 'North Korea', 'ru': 'КНДР'},
    'KR': {'en': 'South Korea', 'ru': 'Республика Корея'},
    'KW': {'en': 'Kuwait', 'ru': 'Кувейт'},
    'KY': {'en': 'Cayman Is.', 'ru': 'острова Кайман'},
    'KZ': {'en': 'Kazakhstan', 'ru': 'Казахстан'},
    'LA': {'en': 'Laos', 'ru': 'Лаос'},
    'LB': {'en': 'Lebanon', 'ru': 'Ливан'},
    'LC': {'en': 'Saint Lucia', 'ru': 'Сент-Люсия'},
    'LI': {'en': 'Liechtenstein', 'ru': 'Лихтенштейн'},
    'LK': {'en': 'Sri Lanka', 'ru': 'Шри-Ланка'},
    'LR': {'en': 'Liberia', 'ru': 'Либерия'},
    'LS': {'en': 'Lesotho', 'ru': 'Лесото'},
    'LT': {'en': 'Lithuania', 'ru': 'Литва'},
    'LU': {'en': 'Luxembourg', 'ru': 'Люксембург'},
    'LV': {'en': 'Latvia', 'ru': 'Латвия'},
    'LY': {'en': 'Libya', 'ru': 'Ливия'},
    'MA': {'en': 'Morocco', 'ru': 'Марокко'},
    'MC': {'en': 'Monaco', 'ru': 'Монако'},
    'MD': {'en': 'Moldova', 'ru': 'Молдавия'},
    'ME': {'en': 'Montenegro', 'ru': 'Черногория'},
    'MF': {'en': 'St-Martin', 'ru': 'Сен-Мартен'},
    'MG': {'en': 'Madagascar', 'ru': 'Мадагаскар'},
    'MH': {'en': 'Marshall Is.', 'ru': 'Маршалловы Острова'},
    'MK': {'en': 'North Macedonia', 'ru': 'Северная Македония'},
    'ML': {'en': 'Mali', 'ru': 'Мали'},
    'MM': {'en': 'Myanmar', 'ru': 'Мьянма'},
    'MN': {'en': 'Mongolia', 'ru': 'Монголия'},
    'MO': {'en': 'Macao', 'ru': 'Макао'},
    'MP': {'en': 'N. Mariana Is.', 'ru': 'Северные Марианские острова'},
    'MR': {'en': 'Mauritania', 'ru': 'Мавритания'},
    'MS': {'en': 'Montserrat', 'ru': 'Монтсеррат'},
    'MT': {'en': 'Malta', 'ru': 'Мальта'},
    'MW': {'en': 'Malawi', 'ru': 'Малави'},
    'MX': {'en': 'Mexico', 'ru': 'Мексика'},
    'MY': {'en': 'Malaysia', 'ru': 'Малайзия'},
    'MZ': {'en': 'Mozambique', 'ru': 'Мозамбик'},
    'NA': {'en': 'Namibia', 'ru': 'Намибия'},
    'NC': {'en': 'New Caledonia', 'ru': 'Новая Каледония'},
    'NE': {'en': 'Niger', 'ru': 'Нигер'},
    'NF': {'en': 'Norfolk Island', 'ru': 'Норфолк'},
    'NG': {'en': 'Nigeria', 'ru': 'Нигерия'},
    'NI': {'en': 'Nicaragua', 'ru': 'Никарагуа'},
    'NL': {'en': 'Netherlands', 'ru': 'Нидерланды'},
    'NO': {'en': 'Norway', 'ru': 'Норвегия'},
    'NP': {'en': 'Nepal', 'ru': 'Непал'},
    'NR': {'en': 'Nauru', 'ru': 'Науру'},
    'NU': {'en': 'Niue', 'ru': 'Ниуэ'},
    'NZ': {'en': 'New Zealand', 'ru': 'Новая Зеландия'},
    'OM': {'en': 'Oman', 'ru': 'Оман'},
    'PA': {'en': 'Panama', 'ru': 'Панама'},
    'PE': {'en': 'Peru', 'ru': 'Перу'},
    'PF': {'en': 'Fr. Polynesia', 'ru': 'Французская Полинезия'},
    'PG': {'en': 'Papua New Guinea', 'ru': 'Папуа — Новая Гвинея'},
    'PH': {'en': 'Philippines', 'ru': 'Филиппины'},
    'PK': {'en': 'Pakistan', 'ru': 'Пакистан'},
    'PL': {'en': 'Poland', 'ru': 'Польша'},
    'PM': {'en': 'St. Pierre and Miquelon', 'ru': 'Сен-Пьер и Микелон'},
    'PN': {'en': 'Pitcairn Is.', 'ru': 'Острова Питкэрн'},
    'PR': {'en': 'Puerto Rico', 'ru': 'Пуэрто-Рико'},
    'PS': {'en': 'Palestine', 'ru': 'Палестина'},
    'PT': {'en': 'Portugal', 'ru': 'Португалия'},
    'PW': {'en': 'Palau', 'ru': 'Палау'},
    'PY': {'en': 'Paraguay', 'ru': 'Парагвай'},
    'QA': {'en': 'Qatar', 'ru': 'Катар'},
    'RO': {'en': 'Romania', 'ru': 'Румыния'},
    'RS': {'en': 'Serbia', 'ru': 'Сербия'},
    'RU': {'en': 'Russia', 'ru': 'Россия'},
    'RW': {'en': 'Rwanda', 'ru': 'Руанда'},
    'SA': {'en': 'Saudi Arabia', 'ru': 'Саудовская Аравия'},
    'SB': {'en': 'Solomon Is.', 'ru': 'Соломоновы Острова'},
    'SD': {'en': 'Sudan', 'ru': 'Судан'},
    'SE': {'en': 'Sweden', 'ru': 'Швеция'},
    'SG': {'en': 'Singapore', 'ru': 'Сингапур'},
    'SI': {'en': 'Slovenia', 'ru': 'Словения'},
    'SK': {'en': 'Slovakia', 'ru': 'Словакия'},
    'SL': {'en': 'Sierra Leone', 'ru': 'Сьерра-Леоне'},
    'SM': {'en': 'San Marino', 'ru': 'Сан-Марино'},
    'SN': {'en': 'Senegal', 'ru': 'Сенегал'},
    'SO': {'en': 'Somalia', 'ru': 'Сомали'},
    'SR': {'en': 'Suriname', 'ru': 'Суринам'},
    'SS': {'en': 'S. Sudan', 'ru': 'Южный Судан'},
    'ST': {'en': 'São Tomé and Principe', 'ru': 'Сан-Томе и Принсипи'},
    'SV': {'en': 'El Salvador', 'ru': 'Сальвадор'},
    'SX': {'en': 'Sint Maarten', 'ru': 'Синт-Мартен'},
    'SY': {'en': 'Syria', 'ru': 'Сирия'},
    'SZ': {'en': 'eSwatini', 'ru': 'Эсватини'},
    'TC': {'en': 'Turks and Caicos Is.', 'ru': 'Тёркс и Кайкос'},
    'TD': {'en': 'Chad', 'ru': 'Чад'},
    'TG': {'en': 'Togo', 'ru': 'Того'},
    'TH': {'en': 'Thailand', 'ru': 'Таиланд'},
    'TJ': {'en': 'Tajikistan', 'ru': 'Таджикистан'},
    'TL': {'en': 'Timor-Leste', 'ru': 'Восточный Тимор'},
    'TM': {'en': 'Turkmenistan', 'ru': 'Туркмения'},
    'TN': {'en': 'Tunisia', 'ru': 'Тунис'},
    'TO': {'en': 'Tonga', 'ru': 'Тонга'},
    'TR': {'en': 'Turkey', 'ru': 'Турция'},
    'TT': {'en': 'Trinidad and Tobago', 'ru': 'Тринидад и Тобаго'},
    'TV': {'en': 'Tuvalu', 'ru': 'Тувалу'},
    'TW': {'en': 'Taiwan', 'ru': 'Тайвань'},
    'TZ': {'en': 'Tanzania', 'ru': 'Танзания'},
    'UA': {'en': 'Ukraine', 'ru': 'Украина'},
    'UG': {'en': 'Uganda', 'ru': 'Уганда'},
    'UK': {'en': 'United Kingdom', 'ru': 'Великобритания'},
    'US': {'en': 'United States of America', 'ru': 'США'},
    'UY': {'en': 'Uruguay', 'ru': 'Уругвай'},
    'UZ': {'en': 'Uzbekistan', 'ru': 'Узбекистан'},
    'VA': {'en': 'Vatican', 'ru': 'Ватикан'},
    'VC': {'en': 'St. Vin. and Gren.', 'ru': 'Сент-Винсент и Гренадины'},
    'VE': {'en': 'Venezuela', 'ru': 'Венесуэла'},
    'VG': {'en': 'British Virgin Is.', 'ru': 'Британские Виргинские острова'},
    'VI': {'en': 'U.S. Virgin Is.', 'ru': 'Американские Виргинские острова'},
    'VN': {'en': 'Vietnam', 'ru': 'Вьетнам'},
    'VU': {'en': 'Vanuatu', 'ru': 'Вануату'},
    'WF': {'en': 'Wallis and Futuna Is.', 'ru': 'Уоллис и Футуна'},
    'WS': {'en': 'Samoa', 'ru': 'Самоа'},
    'YE': {'en': 'Yemen', 'ru': 'Йемен'},
    'ZA': {'en': 'South Africa', 'ru': 'ЮАР'},
    'ZM': {'en': 'Zambia', 'ru': 'Замбия'},
    'ZW': {'en': 'Zimbabwe', 'ru': 'Зимбабве'},
}

RESOURCE_NAMES = {
    "food": "🌾 Еда", "production": "⚙️ Производство",
    "rare_metals": "💎 Металлы", "oil": "🛢 Нефть", "science": "📚 Наука",
}

MILITARY_TRADE_NAMES = {
    "infantry":  "🪖 Пехота",
    "cavalry":   "🐴 Кавалерия",
    "artillery": "💣 Артиллерия",
    "airforce":  "✈️ Авиация",
    "bomber":    "💥 Бомбардировщики",
    "navy":      "⚓ Флот",
    "armored":   "🚗 Бронеавтомобиль",
    "tank":      "🛡 Танк",
}

# Все допустимые типы для торговли (ресурсы + войска)
ALL_TRADE_NAMES = {**RESOURCE_NAMES, **MILITARY_TRADE_NAMES}

BUILDING_DEFS = {
    # base cost_gold / cost_prod — для уровня 1.
    # Реальная стоимость уровня N = base * (1.6 ^ (N-1)), округлённая до 10.
    # max_level увеличен до 10 для основных и 7 для специализированных.
    "farm":       {"emoji": "🌾", "name": "Ферма",          "cost_gold": 100,  "cost_prod": 20,  "provides": {"food": 50},          "max_level": 10},
    "factory":    {"emoji": "⚙️", "name": "Завод",          "cost_gold": 200,  "cost_prod": 50,  "provides": {"production": 60},    "max_level": 10},
    "bank":       {"emoji": "🏦", "name": "Банк",           "cost_gold": 150,  "cost_prod": 30,  "provides": {"treasury": 150},     "max_level": 10},
    "university": {"emoji": "🔬", "name": "Университет",    "cost_gold": 200,  "cost_prod": 40,  "provides": {"science": 60},       "max_level": 7},
    "oilrig":     {"emoji": "🛢", "name": "Нефтяная вышка", "cost_gold": 300,  "cost_prod": 80,  "provides": {"oil": 60},           "max_level": 7},
    "mine":       {"emoji": "💎", "name": "Шахта",          "cost_gold": 250,  "cost_prod": 60,  "provides": {"rare_metals": 50},   "max_level": 7},
    "fortress":   {"emoji": "🏰", "name": "Крепость",       "cost_gold": 400,  "cost_prod": 100, "provides": {"defense": 30},       "max_level": 7},
    "barracks":   {"emoji": "🪖", "name": "Казармы",        "cost_gold": 150,  "cost_prod": 40,  "provides": {"infantry_cap": 200}, "max_level": 10},
    "stable":     {"emoji": "🐴", "name": "Конюшни",        "cost_gold": 200,  "cost_prod": 50,  "provides": {"cavalry_cap": 50},   "max_level": 7},
    "airbase":    {"emoji": "✈️", "name": "Авиабаза",       "cost_gold": 600,  "cost_prod": 150, "provides": {"airforce_cap": 20},  "max_level": 7},
    "harbor":     {"emoji": "⚓", "name": "Гавань",         "cost_gold": 350,  "cost_prod": 90,  "provides": {"navy_cap": 30},      "max_level": 7},
    "market":     {"emoji": "🏪", "name": "Рынок",          "cost_gold": 320,  "cost_prod": 45,  "provides": {"treasury": 28, "food": 10}, "max_level": 10},
    "railroad":   {"emoji": "🚂", "name": "Железная дорога","cost_gold": 300,  "cost_prod": 80,  "provides": {},                   "max_level": 5},
    "palace":     {"emoji": "🏯", "name": "Дворец",         "cost_gold": 1000, "cost_prod": 200, "provides": {"treasury": 100, "happiness": 5}, "max_level": 5},
}

def building_level_cost(bdef: dict, level: int) -> tuple[int, int]:
    """Возвращает стоимость (gold, prod) для постройки/улучшения до уровня `level`.
    Каждый уровень дороже предыдущего: gold *= 1.65^(level-1), prod *= 1.5^(level-1).
    """
    import math
    gold = max(10, round(bdef["cost_gold"] * (1.65 ** (level - 1)) / 10) * 10)
    prod = max(0,  round(bdef["cost_prod"] * (1.50 ** (level - 1)) / 5)  * 5)
    return gold, prod

TECH_TREE = {
    "feudal": {
        "name": "🏰 Феодализм", "era": "feudal",
        "techs": {
            "basic_farming":  {"name": "Базовое земледелие", "cost_science": 30,  "unlocks": ["farm"], "bonus": {"food": 10}},
            "iron_weapons":   {"name": "Железное оружие",    "cost_science": 50,  "unlocks": [],       "bonus": {"infantry_attack": 10}},
            "stone_walls":    {"name": "Каменные стены",     "cost_science": 80,  "unlocks": ["fortress"], "bonus": {"defense": 15}},
            "feudal_levy":    {"name": "Феодальное ополчение","cost_science": 60, "unlocks": ["barracks"], "bonus": {"infantry_cap": 100}},
            "horse_breeding": {"name": "Коневодство",         "cost_science": 100,"unlocks": ["stable"],  "bonus": {}},
        }
    },
    "industrial": {
        "name": "⚙️ Индустриализм", "era": "industrial",
        "requires_era": "feudal",
        "requires_techs": ["basic_farming", "iron_weapons"],
        "techs": {
            "steam_engine":   {"name": "Паровой двигатель", "cost_science": 150, "unlocks": ["factory"], "bonus": {"production": 20}},
            "banking_system": {"name": "Банковская система","cost_science": 120, "unlocks": ["bank"],    "bonus": {"treasury_rate": 15}},
            "oil_drilling":   {"name": "Нефтедобыча",      "cost_science": 200, "unlocks": ["oilrig"],  "bonus": {"oil": 10}},
            "artillery_tech": {"name": "Артиллерия",       "cost_science": 180, "unlocks": [],          "bonus": {"artillery_attack": 25}},
            "rail_network":   {"name": "Железные дороги",  "cost_science": 160, "unlocks": [],          "bonus": {"expand_cost": -20}},
        }
    },
    "modern": {
        "name": "🚀 Современность", "era": "modern",
        "requires_era": "industrial",
        "requires_techs": ["steam_engine", "banking_system"],
        "techs": {
            "aviation":          {"name": "Авиация",              "cost_science": 300, "unlocks": ["airbase"], "bonus": {}},
            "nuclear_theory":    {"name": "Ядерная теория",       "cost_science": 500, "unlocks": [],          "bonus": {"war_deterrence": 50}},
            "global_trade":      {"name": "Глобальная торговля",  "cost_science": 250, "unlocks": [],          "bonus": {"trade_income": 30}},
            "rare_mining":       {"name": "Добыча редких металлов","cost_science": 200,"unlocks": ["mine"],    "bonus": {"rare_metals": 15}},
            "cyber_warfare":     {"name": "Кибервойна",           "cost_science": 400, "unlocks": [],          "bonus": {"spy_success": 20}},
            "mechanized_warfare":{"name": "Механизированная война","cost_science": 350,"unlocks": [],          "bonus": {"armored_attack": 20}},
            "tank_doctrine":     {"name": "Танковая доктрина",    "cost_science": 450, "unlocks": [],          "bonus": {"tank_attack": 30}},
        }
    },
}

UNIT_DEFS = {
    "infantry":  {"emoji": "👣", "name": "Пехота",        "cost_gold": 10,  "cost_prod": 0,   "attack": 1.0, "defense": 1.0},
    "cavalry":   {"emoji": "🐴", "name": "Кавалерия",     "cost_gold": 30,  "cost_prod": 5,   "attack": 1.5, "defense": 0.8, "expand_bonus": True},
    "artillery": {"emoji": "💣", "name": "Артиллерия",    "cost_gold": 80,  "cost_prod": 20,  "attack": 2.5, "defense": 0.5, "siege_bonus": True},
    "airforce":  {"emoji": "✈️", "name": "Авиация",       "cost_gold": 200, "cost_prod": 50,  "attack": 3.0, "defense": 1.5, "long_range": True},
    "navy":      {"emoji": "⚓", "name": "Морской флот",  "cost_gold": 150, "cost_prod": 40,  "attack": 2.0, "defense": 1.8, "can_cross_sea": True},
    "bomber":    {"emoji": "💥", "name": "Стратег. бомб.", "cost_gold": 350, "cost_prod": 80,  "attack": 4.0, "defense": 0.3, "long_range": True, "siege_bonus": True},
    "armored":   {"emoji": "🚗", "name": "Бронеавтомобиль","cost_gold": 120, "cost_prod": 30,  "attack": 8.0, "defense": 5.0},
    "tank":      {"emoji": "🛡", "name": "Танк",           "cost_gold": 300, "cost_prod": 70,  "attack": 15.0,"defense": 10.0},
}

# ISO-коды стран с морским выходом (для флота)
COASTAL_ISOS = {
    "RU","US","CN","UK","FR","DE","IT","ES","PT","NO","SE","DK","NL","BE","PL","GR","TR",
    "UA","GE","AZ","KZ","TM","PK","IN","BD","MM","TH","VN","KH","MY","ID","PH","JP","KR",
    "AU","NZ","CA","MX","CU","JM","HT","DO","TT","CO","VE","GY","SR","BR","AR","UY","CL","PE","EC",
    "MA","DZ","TN","LY","EG","MR","SN","GN","LR","CI","GH","NG","CM","GA","AO","MZ","TZ","KE",
    "SO","DJ","ER","SA","AE","QA","KW","IQ","IR","OM","YE","IL","LB","SY","JO",
    "NG","BJ","TG","GQ","CG","CD","SB","VU","NC","PG","NR","TV","FJ","WS","TO",
}

SPY_OPS = {
    "steal_gold":     {"name": "Кража золота",         "cost": 100, "base_chance": 55, "emoji": "💰"},
    "steal_resources":{"name": "Кража ресурсов",       "cost": 120, "base_chance": 50, "emoji": "📦"},
    "sabotage":       {"name": "Диверсия (здание)",    "cost": 200, "base_chance": 40, "emoji": "💥"},
    "recon":          {"name": "Разведка армии",        "cost": 50,  "base_chance": 75, "emoji": "🔭"},
    "provoke_revolt": {"name": "Спровоцировать восстание","cost": 300,"base_chance": 30,"emoji": "🔥"},
    "recruit_agent":  {"name": "Завербовать агента",   "cost": 400, "base_chance": 25, "emoji": "🕵️"},
}

RESOURCE_TICK_INTERVAL_H = 1  # ресурсы начисляются раз в час


# ═══════════════════════════════════════════════════════════════
#  DB HELPERS — РЕСУРСЫ
# ═══════════════════════════════════════════════════════════════

async def get_resources(country_id: int) -> Optional[dict]:
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT * FROM resources WHERE country_id=?", (country_id,))
        row = await c.fetchone()
        return dict(row) if row else None


async def update_resource(country_id: int, resource: str, delta: int):
    if resource not in RESOURCE_NAMES:
        raise ValueError(f"Unknown resource: {resource}")
    db = await _open_db()
    async with db:
        await db.execute(
            f"UPDATE resources SET {resource}=MAX(0,{resource}+?) WHERE country_id=?",
            (delta, country_id)
        )
        await db.commit()


async def get_buildings(chat_id: int, country_id: int) -> list:
    db = await _open_db()
    async with db:
        # Только здания на территориях которые сейчас принадлежат этой стране
        c = await db.execute(
            """SELECT b.* FROM buildings b
               INNER JOIN territories t ON t.chat_id=b.chat_id AND t.iso=b.iso
               WHERE b.chat_id=? AND b.country_id=? AND t.country_id=?
               ORDER BY b.iso, b.type""",
            (chat_id, country_id, country_id)
        )
        return [dict(r) for r in await c.fetchall()]


async def build_building(chat_id: int, country_id: int, iso: str, btype: str) -> tuple:
    bdef = BUILDING_DEFS.get(btype)
    if not bdef:
        return False, "Неизвестный тип здания"

    db = await _open_db()
    async with db:
        # Проверяем что территория принадлежит стране
        ter_c = await db.execute(
            "SELECT country_id FROM territories WHERE chat_id=? AND iso=?",
            (chat_id, iso)
        )
        ter_row = await ter_c.fetchone()
        if not ter_row or ter_row["country_id"] != country_id:
            return False, f"Территория {iso} вам не принадлежит! Нельзя строить на чужих землях."
        # Проверяем существующее здание
        c = await db.execute(
            "SELECT level FROM buildings WHERE chat_id=? AND country_id=? AND iso=? AND type=?",
            (chat_id, country_id, iso, btype)
        )
        existing = await c.fetchone()
        cur_level = existing["level"] if existing else 0
        if cur_level >= bdef["max_level"]:
            return False, f"Максимальный уровень ({bdef['max_level']}) уже достигнут"

        next_level = cur_level + 1
        cost_gold, cost_prod = building_level_cost(bdef, next_level)

        # Проверяем разблокировку через технологии
        if cur_level == 0:  # только при первом строительстве
            
            tech_c = await db.execute("SELECT techs FROM technologies WHERE country_id=?", (country_id,))
            tech_row = await tech_c.fetchone()
            known_techs = set(json.loads(tech_row["techs"]) if tech_row else [])
            # Проверяем что хоть одна технология разблокирует этот тип здания
            all_unlocked = set()
            for era_data in TECH_TREE.values():
                for tid, tdef in era_data["techs"].items():
                    if tid in known_techs:
                        all_unlocked.update(tdef.get("unlocks", []))
            # Здания без требований технологий (базовые)
            FREE_BUILDINGS = {"farm", "barracks", "market"}
            if btype not in FREE_BUILDINGS and btype not in all_unlocked:
                return False, f"❌ Для строительства {bdef['emoji']} {bdef['name']} нужна соответствующая технология"

        # Проверяем ресурсы
        c2 = await db.execute("SELECT * FROM countries WHERE id=?", (country_id,))
        country = await c2.fetchone()
        if not country or country["treasury"] < cost_gold:
            return False, f"Нужно {cost_gold}💰, у вас {country['treasury'] if country else 0}💰"

        c3 = await db.execute("SELECT production FROM resources WHERE country_id=?", (country_id,))
        res = await c3.fetchone()
        if not res or res["production"] < cost_prod:
            return False, f"Нужно {cost_prod}⚙️ производства, у вас {res['production'] if res else 0}⚙️"

        # Строим
        await db.execute(
            "UPDATE countries SET treasury=treasury-? WHERE id=?",
            (cost_gold, country_id)
        )
        if cost_prod > 0:
            await db.execute(
                "UPDATE resources SET production=MAX(0,production-?) WHERE country_id=?",
                (cost_prod, country_id)
            )
        if existing:
            await db.execute(
                "UPDATE buildings SET level=level+1 WHERE chat_id=? AND country_id=? AND iso=? AND type=?",
                (chat_id, country_id, iso, btype)
            )
        else:
            await db.execute(
                "INSERT OR IGNORE INTO buildings (chat_id, country_id, iso, type) VALUES (?,?,?,?)",
                (chat_id, country_id, iso, btype)
            )
        await db.commit()
        # Стоимость следующего уровня
        if next_level < bdef["max_level"]:
            next_gold, next_prod = building_level_cost(bdef, next_level + 1)
            next_str = f"\n💡 Следующий уровень: {next_gold}💰 + {next_prod}⚙️"
        else:
            next_str = "\n✅ Достигнут максимальный уровень!"
        return True, (
            f"{'Улучшено' if existing else 'Построено'}: {bdef['emoji']} {bdef['name']} "
            f"ур.{next_level}/{bdef['max_level']} на {iso}\n"
            f"Потрачено: {cost_gold}💰 + {cost_prod}⚙️{next_str}"
        )


async def get_technologies(country_id: int) -> dict:
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT * FROM technologies WHERE country_id=?", (country_id,))
        row = await c.fetchone()
        if not row:
            return {"era": "feudal", "techs": []}
        
        return {"era": row["era"], "techs": json.loads(row["techs"])}


async def research_tech(chat_id: int, country_id: int, tech_id: str) -> tuple:
    
    tech_data = await get_technologies(country_id)
    learned = tech_data["techs"]
    current_era = tech_data["era"]

    # Найти технологию в дереве
    tech_def = None
    era_key = None
    for era_k, era_data in TECH_TREE.items():
        if tech_id in era_data["techs"]:
            tech_def = era_data["techs"][tech_id]
            era_key = era_k
            break
    if not tech_def:
        return False, "Неизвестная технология"
    if tech_id in learned:
        return False, "Уже изучено"

    era_data = TECH_TREE[era_key]
    # Проверяем эру
    if "requires_era" in era_data:
        req_era = era_data["requires_era"]
        era_order = ["feudal", "industrial", "modern"]
        if era_order.index(current_era) < era_order.index(req_era):
            return False, f"Нужна эра: {TECH_TREE[req_era]['name']}"
    # Проверяем предусловия по технологиям
    for req in era_data.get("requires_techs", []):
        if req not in learned:
            for e in TECH_TREE.values():
                if req in e["techs"]:
                    return False, f"Сначала изучите: {e['techs'][req]['name']}"

    cost = tech_def["cost_science"]
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT science FROM resources WHERE country_id=?", (country_id,))
        res = await c.fetchone()
        if not res or res["science"] < cost:
            return False, f"Нужно {cost}📚 науки, у вас {res['science'] if res else 0}📚"

        learned.append(tech_id)
        await db.execute(
            "UPDATE resources SET science=MAX(0,science-?) WHERE country_id=?",
            (cost, country_id)
        )
        await db.execute(
            "UPDATE technologies SET techs=? WHERE country_id=?",
            (json.dumps(learned), country_id)
        )
        await db.commit()
    return True, f"✅ Изучено: {tech_def['name']}"


async def get_military(country_id: int) -> Optional[dict]:
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT * FROM military WHERE country_id=?", (country_id,))
        row = await c.fetchone()
        return dict(row) if row else None


async def buy_units(chat_id: int, owner_id: int, unit_type: str, amount: int) -> tuple:
    # Русские псевдонимы
    RU_UNIT_ALIASES = {
        "army": "infantry", "армия": "infantry", "армию": "infantry", "солдат": "infantry", "солдаты": "infantry",
        "пехота": "infantry", "пехоту": "infantry", "пехотинцев": "infantry",
        "кавалерия": "cavalry", "кавалерию": "cavalry", "кавалеристов": "cavalry",
        "артиллерия": "artillery", "артиллерию": "artillery",
        "авиация": "airforce", "авиацию": "airforce", "самолеты": "airforce", "самолёты": "airforce",
        "флот": "navy", "корабли": "navy", "кораблей": "navy",
        "морской флот": "navy", "военно-морской флот": "navy", "вмф": "navy",
        "бомбер": "bomber", "бомберы": "bomber", "бомбардировщик": "bomber", "бомбардировщики": "bomber", "бомбардировщиков": "bomber",
        "воздушные силы": "airforce", "вмс": "navy",
        "бронеавтомобиль": "armored", "броня": "armored", "бронеавто": "armored", "бтр": "armored",
        "танк": "tank", "танки": "tank", "танков": "tank",
    }
    unit_type = RU_UNIT_ALIASES.get(unit_type.lower(), unit_type.lower())
    udef = UNIT_DEFS.get(unit_type)
    if not udef:
        return False, "Неизвестный тип войск"
    # SECURITY FIX: отрицательное количество давало отрицательную цену
    # (казна росла, армия уходила в минус). Также ограничиваем верхний предел.
    if amount <= 0:
        return False, "Количество должно быть положительным"
    if amount > 1_000_000:
        return False, "Слишком большое количество (максимум 1 000 000)"

    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM countries WHERE chat_id=? AND owner_id=?", (chat_id, owner_id)
        )
        country = await c.fetchone()
        if not country:
            return False, "У вас нет страны"

        cost_gold = udef["cost_gold"] * amount
        cost_prod = udef["cost_prod"] * amount

        if country["treasury"] < cost_gold:
            return False, f"Нужно {cost_gold}💰, у вас {country['treasury']}💰"

        if cost_prod > 0:
            c2 = await db.execute("SELECT production FROM resources WHERE country_id=?", (country["id"],))
            res = await c2.fetchone()
            if not res or res["production"] < cost_prod:
                return False, f"Нужно {cost_prod}⚙️ производства, у вас {res['production'] if res else 0}⚙️"
            await db.execute(
                "UPDATE resources SET production=MAX(0,production-?) WHERE country_id=?",
                (cost_prod, country["id"])
            )

        await db.execute(
            "UPDATE countries SET treasury=treasury-? WHERE id=?", (cost_gold, country["id"])
        )
        await db.execute(
            f"UPDATE military SET {unit_type}={unit_type}+? WHERE country_id=?",
            (amount, country["id"])
        )
        # Синхронизируем army = сумма ВСЕХ войск
        c3 = await db.execute("SELECT * FROM military WHERE country_id=?", (country["id"],))
        mil = await c3.fetchone()
        total = (
            (mil["infantry"]  or 0) + (mil["cavalry"]   or 0) +
            (mil["artillery"] or 0) + (mil["airforce"]  or 0) +
            (mil["navy"]      or 0) + (mil["bomber"]    or 0) +
            (mil["armored"] or 0) + (mil["tank"] or 0)
        )
        await db.execute("UPDATE countries SET army=? WHERE id=?", (total, country["id"]))
        await db.commit()
        return True, f"✅ Куплено {amount} {udef['emoji']} {udef['name']} за {cost_gold}💰"


# ── Тик ресурсов (раз в 4 часа) ─────────────────────────────────

async def resource_tick(chat_id: int, bot=None):
    """Начисляет ресурсы всем странам чата на основе зданий и технологий."""
    countries = await get_all_countries(chat_id)
    # Буфер событий — пишем в event_log ПОСЛЕ закрытия основного соединения
    _event_buf: list = []
    db = await _open_db()
    async with db:
        for country in countries:
            cid = country["id"]

            # Считаем прирост от зданий — только на своих территориях
            c = await db.execute(
                """SELECT b.type, b.level, b.iso FROM buildings b
                   INNER JOIN territories t ON t.chat_id=b.chat_id AND t.iso=b.iso
                   WHERE b.chat_id=? AND b.country_id=? AND t.country_id=?""",
                (chat_id, cid, cid)
            )
            buildings = await c.fetchall()
            income = {"food": 15, "production": 10, "rare_metals": 3, "oil": 5, "science": 10, "treasury": 0}
            happiness_bonus = 0
            # infantry_cap, cavalry_cap, airforce_cap, navy_cap — пропускаем (не ресурсы)
            SKIP_PROVIDES = {"infantry_cap", "cavalry_cap", "airforce_cap", "navy_cap", "defense"}

            # Вербовка из казарм и конюшен за тик
            # Казармы: lv1-4 → пехота, lv5-7 → +кавалерия, lv8+ → +артиллерия
            barracks_recruit_infantry = 0
            barracks_recruit_cavalry  = 0
            barracks_recruit_artillery = 0
            stables_recruit_cavalry   = 0

            for b in buildings:
                bdef = BUILDING_DEFS.get(b["type"], {})
                provides = bdef.get("provides", {})
                for res, val in provides.items():
                    if res in SKIP_PROVIDES:
                        continue
                    elif res == "happiness":
                        happiness_bonus += val * b["level"]
                    elif res in income:
                        income[res] += val * b["level"]

                if b["type"] == "barracks":
                    lv = b["level"]
                    # Пехота: 10 * уровень за тик всегда
                    barracks_recruit_infantry += 10 * lv
                    # С 5 уровня добавляется кавалерия: 2 * (lv - 4)
                    if lv >= 5:
                        barracks_recruit_cavalry += 2 * (lv - 4)
                    # С 8 уровня добавляется артиллерия: 1 * (lv - 7)
                    if lv >= 8:
                        barracks_recruit_artillery += 1 * (lv - 7)

                elif b["type"] == "stable":
                    # Конюшни: 5 * уровень кавалерии за тик
                    stables_recruit_cavalry += 5 * b["level"]

            total_recruit_cavalry   = barracks_recruit_cavalry + stables_recruit_cavalry
            total_recruit_artillery = barracks_recruit_artillery

            # Прирост населения (1% в тик)
            c2 = await db.execute("SELECT population, food FROM resources WHERE country_id=?", (cid,))
            res_row = await c2.fetchone()
            pop  = res_row["population"] if res_row else 1000
            food_stored = res_row["food"] if res_row else 0
            pop_growth = max(10, int(pop * 0.01))
            tax_from_pop = pop // 100  # 1 золото на 100 человек

            # Текущая армия (пехота + остальные) для расчёта потребления еды
            mil_c = await db.execute(
                "SELECT infantry+cavalry+artillery+airforce+bomber+navy+COALESCE(armored,0)+COALESCE(tank,0) as total FROM military WHERE country_id=?",
                (cid,)
            )
            mil_row = await mil_c.fetchone()
            army_total = mil_row["total"] if mil_row else 0

            # Армия + население потребляют еду
            # Армия: 1 еда на 20 солдат в час, население: 1 еда на 500 человек
            food_consumed_army = max(0, army_total // 20)
            food_consumed_pop  = max(0, pop // 500)
            food_consumed      = food_consumed_army + food_consumed_pop
            net_food           = income["food"] - food_consumed

            # Если еды не хватает — штраф счастью и рост армии замедляется
            food_shortage = food_stored + net_food < 0
            if food_shortage:
                happiness_bonus -= 10  # штраф при голоде

            await db.execute("""
                UPDATE resources SET
                    food       = MAX(0, MIN(99999, food + ?)),
                    production = MIN(99999, production + ?),
                    rare_metals= MIN(99999, rare_metals + ?),
                    oil        = MIN(99999, oil + ?),
                    science    = MIN(99999, science + ?),
                    population = population + ?,
                    last_tick  = strftime('%s','now')
                WHERE country_id=?
            """, (net_food, income["production"], income["rare_metals"],
                  income["oil"], income["science"], pop_growth, cid))
            # Счастье применяется отдельно через tick_happiness (не дублировать)
            if happiness_bonus != 0:
                await db.execute(
                    "UPDATE resources SET happiness=MAX(0,MIN(100,happiness+?)) WHERE country_id=?",
                    (happiness_bonus, cid)
                )

            # Золото: здания + население.
            # ВАЖНО: раньше здесь ЕЩЁ РАЗ начислялся налог с территорий
            # (tax_rate * ter_count_n) — ровно такой же, как в /tax и в
            # hourly_income_tick. Это тройное дублирование одного и того же
            # дохода и было главной причиной взрывного роста экономики
            # у первых захватчиков. Налог с территорий теперь начисляется
            # ТОЛЬКО через ручную команду /tax (с diminishing returns) и через
            # почасовой пассивный доход (тоже с diminishing returns, см. ниже).
            ter_count_c = await db.execute(
                "SELECT COUNT(*) FROM territories WHERE chat_id=? AND country_id=?",
                (chat_id, cid)
            )
            ter_count_r = await ter_count_c.fetchone()
            ter_count_n = ter_count_r[0] if ter_count_r else 1

            # Содержание армии: раньше войска не стоили ничего в содержании,
            # поэтому накопленная армия не создавала никакого экономического
            # тормоза. Теперь чем больше войск — тем дороже их держать.
            army_upkeep = army_total // 25  # 1💰 за каждые 25 солдат в тик

            treasury_income = income["treasury"] + tax_from_pop - army_upkeep
            if treasury_income != 0:
                await db.execute(
                    "UPDATE countries SET treasury=MAX(0, treasury+?) WHERE id=?",
                    (treasury_income, cid)
                )
            # Буферизуем лог (не открываем второе соединение внутри основного)
            _event_buf.append((chat_id, "income_tick", {
                "country": country["name"], "emoji": country["emoji"],
                "treasury": treasury_income,
                "army_upkeep": army_upkeep,
                "food": net_food, "production": income["production"],
                "science": income["science"], "oil": income["oil"],
                "rare_metals": income["rare_metals"],
                "food_consumed": food_consumed,
                "pop_growth": pop_growth,
                "tax_from_pop": tax_from_pop,
            }))

            # Вербовка в армию из казарм (только если есть еда или запас)
            if not food_shortage:
                infantry_cap = 500 + barracks_recruit_infantry // 10 * 200  # приблизительный cap
                # Более точный: считаем от уровней казарм
                bc2 = await db.execute(
                    "SELECT COALESCE(SUM(level),0) as lvl FROM buildings WHERE chat_id=? AND country_id=? AND type='barracks'",
                    (chat_id, cid)
                )
                br2 = await bc2.fetchone()
                rr_cap = 500 + (br2["lvl"] if br2 else 0) * 200
                current_army_c = await db.execute("SELECT army FROM countries WHERE id=?", (cid,))
                current_army_r = await current_army_c.fetchone()
                current_army = current_army_r["army"] if current_army_r else 0
                can_recruit = max(0, rr_cap - current_army)
                actual_recruit = min(barracks_recruit_infantry, can_recruit)
                if actual_recruit > 0:
                    await db.execute(
                        "UPDATE countries SET army=army+? WHERE id=?",
                        (actual_recruit, cid)
                    )
                    await db.execute(
                        "UPDATE military SET infantry=infantry+? WHERE country_id=?",
                        (actual_recruit, cid)
                    )
                if total_recruit_cavalry > 0:
                    await db.execute(
                        "UPDATE military SET cavalry=cavalry+? WHERE country_id=?",
                        (total_recruit_cavalry, cid)
                    )
                if total_recruit_artillery > 0:
                    await db.execute(
                        "UPDATE military SET artillery=artillery+? WHERE country_id=?",
                        (total_recruit_artillery, cid)
                    )
        await db.commit()

    # Записываем буферизованные события одной транзакцией (не мешаем основному соединению)
    if _event_buf:
        _edb = await _open_db()
        async with _edb:
            for _cid, _etype, _data in _event_buf:
                await _edb.execute(
                    "INSERT INTO event_log (chat_id, event_type, data) VALUES (?,?,?)",
                    (_cid, _etype, json.dumps(_data, ensure_ascii=False))
                )
            await _edb.commit()

    # После начисления — счастье, кризисы, торговля, дань
    for country in countries:
        await tick_happiness(chat_id, country["id"], bot=bot)  # ← система счастья/протестов
        await check_economic_crisis(chat_id, country["id"])
        await check_civil_war(chat_id, country["id"])
        await check_achievements(chat_id, country["id"])
    await execute_trade_tick(chat_id)
    await collect_tribute(chat_id)
    await tick_victory_points(chat_id)
    await resolve_trade_ships(chat_id, bot)


# ═══════════════════════════════════════════════════════════════
#  DB HELPERS — ГАРНИЗОНЫ
# ═══════════════════════════════════════════════════════════════

UNIT_TYPES = ["infantry", "cavalry", "artillery", "airforce", "bomber", "navy", "armored", "tank"]


async def get_garrison(chat_id: int, country_id: int, iso: str) -> dict:
    """Возвращает гарнизон страны на территории ISO."""
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM garrisons WHERE chat_id=? AND country_id=? AND iso=?",
            (chat_id, country_id, iso)
        )
        row = await c.fetchone()
        if row:
            return dict(row)
        return {u: 0 for u in UNIT_TYPES}


async def get_all_garrisons(chat_id: int, country_id: int) -> list:
    """Все гарнизоны страны по всем территориям."""
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM garrisons WHERE chat_id=? AND country_id=? ORDER BY iso",
            (chat_id, country_id)
        )
        rows = await c.fetchall()
        return [dict(r) for r in rows]


async def garrison_total(g: dict) -> int:
    return sum(g.get(u, 0) for u in UNIT_TYPES)


async def move_to_garrison(chat_id: int, country_id: int, iso: str,
                           unit_type: str, amount: int) -> tuple[bool, str]:
    """
    Перемещает войска из общего пула страны в гарнизон территории ISO.
    Возвращает (success, message).
    """
    if unit_type not in UNIT_TYPES:
        return False, f"Неизвестный тип войск: {unit_type}"
    # SECURITY FIX: отрицательный amount в "unit=unit-?" ДОБАВЛЯЛ войска
    # из ниоткуда (x - (-N) = x + N). Разрешаем только положительные значения.
    if amount <= 0:
        return False, "Количество должно быть положительным"
    db = await _open_db()
    async with db:
        # Проверяем территорию
        c = await db.execute(
            "SELECT country_id FROM territories WHERE chat_id=? AND iso=?",
            (chat_id, iso)
        )
        ter = await c.fetchone()
        if not ter or ter["country_id"] != country_id:
            return False, f"Территория {iso} вам не принадлежит!"
        # Атомарно списываем конкретный юнит из military
        cur_mil = await db.execute(
            f"UPDATE military SET {unit_type}={unit_type}-? WHERE country_id=? AND {unit_type}>=?",
            (amount, country_id, amount)
        )
        if cur_mil.rowcount == 0:
            mm = await db.execute(f"SELECT {unit_type} FROM military WHERE country_id=?", (country_id,))
            mrow = await mm.fetchone()
            have = mrow[unit_type] if mrow else 0
            return False, f"У вас только {have} {UNIT_LABELS[unit_type]}, нужно {amount}"
        # Синхронизируем общий счётчик army
        await db.execute("UPDATE countries SET army=MAX(0,army-?) WHERE id=?", (amount, country_id))
        # Добавляем в гарнизон
        await db.execute(
            f"""INSERT INTO garrisons (chat_id, country_id, iso, {unit_type}, updated_at)
                VALUES (?,?,?,?,strftime('%s','now'))
                ON CONFLICT(chat_id, country_id, iso)
                DO UPDATE SET {unit_type}={unit_type}+?, updated_at=strftime('%s','now')""",
            (chat_id, country_id, iso, amount, amount)
        )
        await db.commit()
    return True, f"✅ {amount} {UNIT_LABELS[unit_type]} размещено в гарнизоне {iso}"


async def withdraw_from_garrison(chat_id: int, country_id: int, iso: str,
                                  unit_type: str, amount: int) -> tuple[bool, str]:
    """Выводит войска из гарнизона обратно в общий пул."""
    if unit_type not in UNIT_TYPES:
        return False, f"Неизвестный тип войск: {unit_type}"
    db = await _open_db()
    async with db:
        # SECURITY FIX: отрицательное количество вычиталось со знаком минус и
        # ДОБАВЛЯЛО войска в гарнизон из ниоткуда.
        if amount <= 0:
            return False, "Количество должно быть положительным"
        # BUG FIX: раньше наличие проверялось отдельным SELECT, а списание шло
        # безусловным UPDATE — два одновременных вывода из одного гарнизона
        # дублировали войска в пуле. Теперь проверка внутри UPDATE.
        cur_gar = await db.execute(
            f"""UPDATE garrisons SET {unit_type}={unit_type}-?, updated_at=strftime('%s','now')
                WHERE chat_id=? AND country_id=? AND iso=? AND {unit_type}>=?""",
            (amount, chat_id, country_id, iso, amount)
        )
        if (cur_gar.rowcount or 0) == 0:
            gc = await db.execute(
                f"SELECT {unit_type} FROM garrisons WHERE chat_id=? AND country_id=? AND iso=?",
                (chat_id, country_id, iso)
            )
            gar = await gc.fetchone()
            in_gar = gar[unit_type] if gar else 0
            return False, f"В гарнизоне {iso} только {in_gar} {UNIT_LABELS[unit_type]}"
        await db.execute(
            f"UPDATE military SET {unit_type}={unit_type}+? WHERE country_id=?",
            (amount, country_id)
        )
        await db.execute(
            "UPDATE countries SET army=army+? WHERE id=?",
            (amount, country_id)
        )
        # Чистим пустые записи гарнизона
        await db.execute(
            """DELETE FROM garrisons WHERE chat_id=? AND country_id=? AND iso=?
               AND infantry=0 AND cavalry=0 AND artillery=0
               AND airforce=0 AND bomber=0 AND navy=0
               AND armored=0 AND tank=0""",
            (chat_id, country_id, iso)
        )
        await db.commit()
    return True, f"✅ {amount} {UNIT_LABELS[unit_type]} выведено из гарнизона {iso} → в пул"


async def get_defense_garrison(chat_id: int, iso: str) -> tuple[int, dict]:
    """
    Суммирует всех защитников на территории ISO (все страны с гарнизоном там).
    Возвращает (total_strength, {country_id: {unit_type: count}}).
    """
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM garrisons WHERE chat_id=? AND iso=?",
            (chat_id, iso)
        )
        rows = await c.fetchall()
    total = 0
    breakdown = {}
    unit_power = {"infantry": 1.0, "cavalry": 1.5, "artillery": 20.0, "airforce": 3.0, "bomber": 5.0, "navy": 10.0, "armored": 8.0, "tank": 15.0}
    for row in rows:
        cid = row["country_id"]
        strength = sum(row[u] * unit_power.get(u, 1.0) for u in UNIT_TYPES)
        total += strength
        breakdown[cid] = dict(row)
    return total, breakdown


async def strip_garrison_on_capture(chat_id: int, iso: str, new_owner_id: int):
    """
    При захвате территории:
    - Войска прежних владельцев возвращаются в пул (отступление)
    - Войска нового владельца остаются как гарнизон
    """
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM garrisons WHERE chat_id=? AND iso=? AND country_id!=?",
            (chat_id, iso, new_owner_id)
        )
        rows = await c.fetchall()
        for row in rows:
            cid = row["country_id"]
            total_retreating = sum(row[u] if row[u] else 0 for u in UNIT_TYPES)
            if total_retreating > 0:
                # Возвращаем отступивший гарнизон в общий пул страны.
                await db.execute(
                    "UPDATE countries SET army=army+? WHERE id=?",
                    (total_retreating, cid)
                )
                for u in UNIT_TYPES:
                    cnt = row[u] if row[u] else 0
                    if cnt > 0:
                        await db.execute(
                            f"UPDATE military SET {u}={u}+? WHERE country_id=?",
                            (cnt, cid)
                        )
        await db.execute(
            "DELETE FROM garrisons WHERE chat_id=? AND iso=? AND country_id!=?",
            (chat_id, iso, new_owner_id)
        )
        await db.commit()


# ═══════════════════════════════════════════════════════════════
#  DB HELPERS — ПРЕТЕНЗИИ (CASUS BELLI)
# ═══════════════════════════════════════════════════════════════

CLAIM_TYPES = {
    "conquest":   {"name": "Завоевательная",  "emoji": "⚔️",  "war_bonus": 10, "cost_gold": 200, "cost_science": 0,   "desc": "Снижает штраф усталости при войне за эту территорию"},
    "historical": {"name": "Историческая",    "emoji": "📜",  "war_bonus": 25, "cost_gold": 100, "cost_science": 50,  "desc": "Территория «исторически ваша» — минимальные потери репутации"},
    "liberation": {"name": "Освобождение",    "emoji": "🕊",  "war_bonus": 15, "cost_gold": 150, "cost_science": 30,  "desc": "Гуманитарное основание, союзники реагируют лучше"},
    "irredenta":  {"name": "Ирредента",       "emoji": "🏳",  "war_bonus": 30, "cost_gold": 300, "cost_science": 80,  "desc": "Соотечественники за рубежом — сильнейший CB, трудно получить"},
}


async def add_claim(chat_id: int, country_id: int, iso: str, claim_type: str) -> tuple[bool, str]:
    cdef = CLAIM_TYPES.get(claim_type)
    if not cdef:
        return False, "Неизвестный тип претензии"
    # Нельзя претендовать на свою территорию
    db = await _open_db()
    async with db:
        tc = await db.execute(
            "SELECT country_id FROM territories WHERE chat_id=? AND iso=?", (chat_id, iso)
        )
        ter = await tc.fetchone()
        if ter and ter["country_id"] == country_id:
            return False, "Это уже ваша территория!"
        # Проверяем ресурсы
        cc = await db.execute("SELECT treasury FROM countries WHERE id=?", (country_id,))
        ctry = await cc.fetchone()
        if not ctry or ctry["treasury"] < cdef["cost_gold"]:
            return False, f"Нужно {cdef['cost_gold']}💰 (у вас {ctry['treasury'] if ctry else 0})"
        sc = await db.execute("SELECT science FROM resources WHERE country_id=?", (country_id,))
        res = await sc.fetchone()
        if cdef["cost_science"] > 0 and (not res or res["science"] < cdef["cost_science"]):
            return False, f"Нужно {cdef['cost_science']}📚 науки"
        # Списываем
        await db.execute("UPDATE countries SET treasury=treasury-? WHERE id=?",
                         (cdef["cost_gold"], country_id))
        if cdef["cost_science"] > 0:
            await db.execute(
                "UPDATE resources SET science=MAX(0,science-?) WHERE country_id=?",
                (cdef["cost_science"], country_id)
            )
        await db.execute(
            """INSERT INTO claims (chat_id, claimant_id, iso, claim_type, strength)
               VALUES (?,?,?,?,?)
               ON CONFLICT(chat_id, claimant_id, iso)
               DO UPDATE SET claim_type=excluded.claim_type,
                             strength=MAX(strength, excluded.strength),
                             created_at=strftime('%s','now')""",
            (chat_id, country_id, iso, claim_type, cdef["war_bonus"])
        )
        await db.commit()
    ter_name = COUNTRY_NAMES.get(iso, iso)
    return True, (
        f"{cdef['emoji']} Претензия «{cdef['name']}» на {ter_name} оформлена!\n"
        f"Бонус при войне за эту территорию: +{cdef['war_bonus']}%"
    )


async def get_claims(chat_id: int, country_id: int) -> list:
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM claims WHERE chat_id=? AND claimant_id=? ORDER BY created_at DESC",
            (chat_id, country_id)
        )
        return [dict(r) for r in await c.fetchall()]


async def get_claim(chat_id: int, country_id: int, iso: str) -> Optional[dict]:
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM claims WHERE chat_id=? AND claimant_id=? AND iso=?",
            (chat_id, country_id, iso)
        )
        row = await c.fetchone()
        return dict(row) if row else None


async def drop_claim(chat_id: int, country_id: int, iso: str):
    db = await _open_db()
    async with db:
        await db.execute(
            "DELETE FROM claims WHERE chat_id=? AND claimant_id=? AND iso=?",
            (chat_id, country_id, iso)
        )
        await db.commit()


# ═══════════════════════════════════════════════════════════════
#  DB HELPERS — ВОЕННАЯ УСТАЛОСТЬ
# ═══════════════════════════════════════════════════════════════

# Пороги усталости и их эффекты
WEARINESS_LEVELS = [
    (0,   "😊 Народ поддерживает войну",      0.00, 0),
    (20,  "😐 Лёгкая усталость",              0.05, -2),
    (40,  "😟 Недовольство растёт",            0.10, -5),
    (60,  "😠 Серьёзная усталость",            0.20, -10),
    (80,  "😡 Народ требует мира",             0.35, -15),
    (95,  "💀 КРИЗИС — армия дезертирует",    0.50, -20),
]

# Сколько усталости набирается за тик войны
WEARINESS_PER_TICK       = 3    # базовый прирост пока идёт война
WEARINESS_DECAY_PER_TICK = 2    # убывание в мирное время
WEARINESS_ON_LOSS        = 8    # доп. усталость за поражение в битве
WEARINESS_ON_WIN         = -2   # небольшой спад за победу
WEARINESS_MAX            = 100


def weariness_info(w: int) -> tuple[str, float, int]:
    """Возвращает (label, army_penalty_pct, happiness_delta) для текущего уровня."""
    result = WEARINESS_LEVELS[0]
    for threshold, label, penalty, hap in WEARINESS_LEVELS:
        if w >= threshold:
            result = (label, penalty, hap)
    return result


async def get_weariness(country_id: int) -> int:
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT weariness FROM war_weariness WHERE country_id=?", (country_id,)
        )
        row = await c.fetchone()
        return row[0] if row else 0


async def add_weariness(country_id: int, delta: int):
    """Добавляет усталость (или убирает при delta < 0). Не выходит за 0..100."""
    db = await _open_db()
    async with db:
        await db.execute(
            """INSERT INTO war_weariness (country_id, weariness, updated_at)
               VALUES (?, MAX(0, MIN(100, ?)), strftime('%s','now'))
               ON CONFLICT(country_id)
               DO UPDATE SET weariness=MAX(0, MIN(100, weariness+?)),
                             updated_at=strftime('%s','now')""",
            (country_id, delta, delta)
        )
        await db.commit()


async def tick_weariness(chat_id: int):
    """
    Вызывается каждый war_tick:
    - Воюющие страны: +WEARINESS_PER_TICK
    - Мирные: -WEARINESS_DECAY_PER_TICK
    Применяет штраф к счастью.
    """
    db = await _open_db()
    async with db:
        # Страны в активных войнах
        c = await db.execute(
            """SELECT DISTINCT attacker_id as cid FROM wars WHERE chat_id=? AND status='active'
               UNION
               SELECT DISTINCT defender_id FROM wars WHERE chat_id=? AND status='active'""",
            (chat_id, chat_id)
        )
        at_war = {row["cid"] for row in await c.fetchall()}
        # Все страны чата
        ac = await db.execute("SELECT id FROM countries WHERE chat_id=?", (chat_id,))
        all_ids = [r["id"] for r in await ac.fetchall()]
        for cid in all_ids:
            delta = WEARINESS_PER_TICK if cid in at_war else -WEARINESS_DECAY_PER_TICK
            # Обновляем усталость
            await db.execute(
                """INSERT INTO war_weariness (country_id, weariness, updated_at)
                   VALUES (?, MAX(0,MIN(100,?)), strftime('%s','now'))
                   ON CONFLICT(country_id)
                   DO UPDATE SET weariness=MAX(0,MIN(100,weariness+?)),
                                 updated_at=strftime('%s','now')""",
                (cid, delta, delta)
            )
            # Дезертирство при критической усталости (≥ 95)
            wc = await db.execute("SELECT weariness FROM war_weariness WHERE country_id=?", (cid,))
            wr = await wc.fetchone()
            w = wr[0] if wr else 0
            _, army_penalty, hap_delta = weariness_info(w)
            if army_penalty > 0:
                await db.execute(
                    "UPDATE countries SET army=MAX(10, CAST(army*(1-?) AS INTEGER)) WHERE id=?",
                    (army_penalty, cid)
                )
                await db.execute(
                    """UPDATE military SET
                           infantry=MAX(0, CAST(infantry*(1-?) AS INTEGER)),
                           cavalry=MAX(0, CAST(cavalry*(1-?) AS INTEGER)),
                           artillery=MAX(0, CAST(artillery*(1-?) AS INTEGER)),
                           airforce=MAX(0, CAST(airforce*(1-?) AS INTEGER)),
                           bomber=MAX(0, CAST(bomber*(1-?) AS INTEGER)),
                           navy=MAX(0, CAST(navy*(1-?) AS INTEGER)),
                           armored=MAX(0, CAST(armored*(1-?) AS INTEGER)),
                           tank=MAX(0, CAST(tank*(1-?) AS INTEGER))
                       WHERE country_id=?""",
                    (army_penalty,)*8 + (cid,)
                )
            # Штраф счастья за войну применяется только в tick_happiness (resource_tick),
            # чтобы не двоить эффект при разных интервалах тиков.
        await db.commit()


# ═══════════════════════════════════════════════════════════════
#  DB HELPERS — ШПИОНАЖ
# ═══════════════════════════════════════════════════════════════

async def do_spy_op(chat_id: int, attacker_id: int, target_id: int, op_type: str) -> tuple:
    """Возвращает (success: bool, result_text: str, scandal: bool) всегда три значения."""
    import random as _r
    op = SPY_OPS.get(op_type)
    if not op:
        return False, "Неизвестная операция", False

    db = await _open_db()
    async with db:

        # Получаем страны
        c = await db.execute("SELECT * FROM countries WHERE id=?", (attacker_id,))
        attacker = await c.fetchone()
        c2 = await db.execute("SELECT * FROM countries WHERE id=?", (target_id,))
        target = await c2.fetchone()
        if not attacker or not target:
            return False, "Страна не найдена", False

        if attacker["treasury"] < op["cost"]:
            return False, f"Нужно {op['cost']}💰, у вас {attacker['treasury']}💰", False

        # Списываем стоимость
        await db.execute("UPDATE countries SET treasury=treasury-? WHERE id=?", (op["cost"], attacker_id))

        # Проверяем успех с учётом кибервойны
        c3 = await db.execute("SELECT techs FROM technologies WHERE country_id=?", (attacker_id,))
        tech_row = await c3.fetchone()
        techs = json.loads(tech_row["techs"]) if tech_row else []
        bonus = 20 if "cyber_warfare" in techs else 0
        chance = op["base_chance"] + bonus
        success = _r.randint(1, 100) <= chance

        result_text = ""
        scandal = False

        if success:
            if op_type == "steal_gold":
                steal = min(target["treasury"], _r.randint(50, 200))
                await db.execute("UPDATE countries SET treasury=treasury-? WHERE id=?", (steal, target_id))
                await db.execute("UPDATE countries SET treasury=treasury+? WHERE id=?", (steal, attacker_id))
                result_text = f"Украдено {steal}💰 из казны {target['name']}"

            elif op_type == "steal_resources":
                res_type = _r.choice(["food", "production", "oil", "rare_metals", "science"])
                c4 = await db.execute("SELECT * FROM resources WHERE country_id=?", (target_id,))
                res = await c4.fetchone()
                if res:
                    steal = min(res[res_type], _r.randint(20, 80))
                    await db.execute(f"UPDATE resources SET {res_type}=MAX(0,{res_type}-?) WHERE country_id=?", (steal, target_id))
                    await db.execute(f"UPDATE resources SET {res_type}={res_type}+? WHERE country_id=?", (steal, attacker_id))
                    rname = RESOURCE_NAMES.get(res_type, res_type)
                    result_text = f"Украдено {steal} {rname} у {target['name']}"

            elif op_type == "sabotage":
                c5 = await db.execute(
                    "SELECT id, type, iso FROM buildings WHERE country_id=? ORDER BY RANDOM() LIMIT 1", (target_id,)
                )
                bld = await c5.fetchone()
                if bld:
                    await db.execute("UPDATE buildings SET level=MAX(0,level-1) WHERE id=?", (bld["id"],))
                    bname = BUILDING_DEFS.get(bld["type"], {}).get("name", bld["type"])
                    result_text = f"Диверсия: {bname} в {bld['iso']} повреждено (ур.-1)"
                else:
                    result_text = f"У {target['name']} нет зданий для диверсии"

            elif op_type == "recon":
                c6 = await db.execute("SELECT * FROM military WHERE country_id=?", (target_id,))
                mil = await c6.fetchone()
                if mil:
                    result_text = (
                        f"Разведданные о {target['name']}:\n"
                        f"  👣 Пехота: {mil['infantry']}\n"
                        f"  🐴 Кавалерия: {mil['cavalry']}\n"
                        f"  💣 Артиллерия: {mil['artillery']}\n"
                        f"  ✈️ Авиация: {mil['airforce']}\n"
                        f"  ⚓ Флот: {mil.get('navy', 0)}\n"
                        f"  💰 Казна: {target['treasury']}"
                    )
                else:
                    result_text = f"Армия {target['name']}: {target['army']} солдат"

            elif op_type == "provoke_revolt":
                # Отбираем случайную территорию у цели
                c7 = await db.execute(
                    "SELECT iso FROM territories WHERE country_id=? AND chat_id=? ORDER BY RANDOM() LIMIT 1",
                    (target_id, chat_id)
                )
                ter = await c7.fetchone()
                if ter:
                    await db.execute(
                        "UPDATE territories SET country_id=NULL WHERE iso=? AND chat_id=?",
                        (ter["iso"], chat_id)
                    )
                    result_text = f"Восстание в {ter['iso']}! Территория перешла под контроль повстанцев"
                else:
                    result_text = f"У {target['name']} нет территорий для восстания"

            elif op_type == "recruit_agent":
                result_text = f"Агент завербован в {target['name']}! Следующая шпионская операция против них +15% успеха"

        else:
            # Провал — возможен скандал
            scandal = _r.randint(1, 100) <= 40
            result_text = "Операция провалилась"
            if scandal:
                result_text += f"\n\U000026a0\ufe0f Дипломатический скандал! {target['name']} раскрыла вашего агента и узнает об операции"

        await db.execute(
            "INSERT INTO spy_ops (chat_id, attacker_id, target_id, op_type, result) VALUES (?,?,?,?,?)",
            (chat_id, attacker_id, target_id, op_type, "success" if success else "fail")
        )
        await db.commit()

    return success, result_text, (False if success else scandal)


# ═══════════════════════════════════════════════════════════════
#  DB HELPERS — БЛОКИ
# ═══════════════════════════════════════════════════════════════

async def create_bloc(chat_id: int, founder_id: int, name: str) -> tuple:
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT id FROM blocs WHERE chat_id=? AND name=?", (chat_id, name))
        if await c.fetchone():
            return False, "Блок с таким названием уже существует"
        c2 = await db.execute(
            "SELECT b.id FROM blocs b JOIN bloc_members m ON b.id=m.bloc_id WHERE b.chat_id=? AND m.country_id=?",
            (chat_id, founder_id)
        )
        if await c2.fetchone():
            return False, "Вы уже состоите в блоке"
        await db.execute(
            "INSERT INTO blocs (chat_id, name, founder_id) VALUES (?,?,?)", (chat_id, name, founder_id)
        )
        c3 = await db.execute("SELECT id FROM blocs WHERE chat_id=? AND name=?", (chat_id, name))
        bloc = await c3.fetchone()
        await db.execute("INSERT INTO bloc_members (bloc_id, country_id) VALUES (?,?)", (bloc["id"], founder_id))
        await db.commit()
        return True, f"Блок «{name}» основан!"


async def join_bloc(chat_id: int, country_id: int, bloc_name: str) -> tuple:
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT id FROM blocs WHERE chat_id=? AND name=?", (chat_id, bloc_name))
        bloc = await c.fetchone()
        if not bloc:
            return False, f"Блок «{bloc_name}» не найден"
        c2 = await db.execute(
            "SELECT id FROM bloc_members WHERE bloc_id=? AND country_id=?", (bloc["id"], country_id)
        )
        if await c2.fetchone():
            return False, "Вы уже в этом блоке"
        await db.execute("INSERT INTO bloc_members (bloc_id, country_id) VALUES (?,?)", (bloc["id"], country_id))
        await db.commit()
        return True, f"Вы вступили в блок «{bloc_name}»!"


async def leave_bloc(chat_id: int, country_id: int) -> tuple:
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT b.id, b.name, b.founder_id FROM blocs b JOIN bloc_members m ON b.id=m.bloc_id WHERE b.chat_id=? AND m.country_id=?",
            (chat_id, country_id)
        )
        row = await c.fetchone()
        if not row:
            return False, "Вы не состоите в блоке"
        await db.execute("DELETE FROM bloc_members WHERE bloc_id=? AND country_id=?", (row["id"], country_id))
        # Если основатель вышел — удаляем блок
        if row["founder_id"] == country_id:
            await db.execute("DELETE FROM blocs WHERE id=?", (row["id"],))
            await db.commit()
            return True, f"Блок «{row['name']}» распущен (основатель вышел)"
        await db.commit()
        return True, f"Вы покинули блок «{row['name']}»"


async def get_bloc_of_country(chat_id: int, country_id: int) -> Optional[dict]:
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT b.* FROM blocs b JOIN bloc_members m ON b.id=m.bloc_id WHERE b.chat_id=? AND m.country_id=?",
            (chat_id, country_id)
        )
        row = await c.fetchone()
        return dict(row) if row else None


async def get_bloc_members(bloc_id: int) -> list:
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT c.* FROM countries c JOIN bloc_members m ON c.id=m.country_id WHERE m.bloc_id=?", (bloc_id,)
        )
        return [dict(r) for r in await c.fetchall()]


async def get_all_blocs(chat_id: int) -> list:
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT * FROM blocs WHERE chat_id=?", (chat_id,))
        return [dict(r) for r in await c.fetchall()]


# ═══════════════════════════════════════════════════════════════
#  DB HELPERS — ТОРГОВЛЯ
# ═══════════════════════════════════════════════════════════════

async def propose_trade(chat_id: int, a_id: int, b_id: int,
                         res_a: str, amt_a: int, res_b: str, amt_b: int) -> tuple:
    if res_a not in ALL_TRADE_NAMES or res_b not in ALL_TRADE_NAMES:
        return False, f"Неизвестный ресурс. Доступно: {', '.join(ALL_TRADE_NAMES)}"
    # SECURITY FIX: защита на уровне API — отрицательные суммы позволяли
    # "обменом" забирать ресурсы у второй стороны.
    if amt_a <= 0 or amt_b <= 0:
        return False, "Количество должно быть положительным"
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT id FROM trade_deals WHERE chat_id=? AND ((country_a_id=? AND country_b_id=?) OR (country_a_id=? AND country_b_id=?)) AND status='pending'",
            (chat_id, a_id, b_id, b_id, a_id)
        )
        if await c.fetchone():
            return False, "Торговое соглашение уже существует или ожидает подтверждения"
        # Проверяем наличие ресурсов/войск у инициатора
        if res_a in RESOURCE_NAMES:
            c2 = await db.execute(f"SELECT {res_a} FROM resources WHERE country_id=?", (a_id,))
            row = await c2.fetchone()
            if not row or row[res_a] < amt_a:
                return False, f"Недостаточно {RESOURCE_NAMES[res_a]}"
        else:
            c2 = await db.execute(f"SELECT {res_a} FROM military WHERE country_id=?", (a_id,))
            row = await c2.fetchone()
            if not row or row[res_a] < amt_a:
                return False, f"Недостаточно {MILITARY_TRADE_NAMES[res_a]}"
        await db.execute(
            "INSERT INTO trade_deals (chat_id, country_a_id, country_b_id, resource_a, amount_a, resource_b, amount_b, status) VALUES (?,?,?,?,?,?,?,'pending')",
            (chat_id, a_id, b_id, res_a, amt_a, res_b, amt_b)
        )
        await db.commit()
        return True, "Торговое предложение отправлено! Ждём подтверждения второй стороны."


async def accept_trade(chat_id: int, deal_id: int, country_id: int) -> tuple:
    """Принять торговое предложение — ресурсы передаются немедленно."""
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM trade_deals WHERE id=? AND chat_id=? AND country_b_id=? AND status='pending'",
            (deal_id, chat_id, country_id)
        )
        deal = await c.fetchone()
        if not deal:
            return False, "Предложение не найдено или уже обработано"
        deal = dict(deal)
        a_id  = deal["country_a_id"]
        b_id  = deal["country_b_id"]   # == country_id
        res_a, amt_a = deal["resource_a"], deal["amount_a"]
        res_b, amt_b = deal["resource_b"], deal["amount_b"]

        def _tbl(r):
            return "resources" if r in RESOURCE_NAMES else "military"

        # Проверяем ресурсы у принимающей стороны (B)
        tbl_b = _tbl(res_b)
        c2 = await db.execute(f"SELECT {res_b} FROM {tbl_b} WHERE country_id=?", (b_id,))
        row_b = await c2.fetchone()
        if not row_b or row_b[res_b] < amt_b:
            label_b = ALL_TRADE_NAMES.get(res_b, res_b)
            return False, f"Недостаточно {label_b} для выполнения сделки"

        # Проверяем ресурсы у инициатора (A) — могло измениться с момента предложения
        tbl_a = _tbl(res_a)
        c3 = await db.execute(f"SELECT {res_a} FROM {tbl_a} WHERE country_id=?", (a_id,))
        row_a = await c3.fetchone()
        if not row_a or row_a[res_a] < amt_a:
            label_a = ALL_TRADE_NAMES.get(res_a, res_a)
            return False, f"Инициатор сделки больше не может предоставить {label_a}"

        # Выполняем обмен немедленно
        # A отдаёт res_a → получает res_b
        await db.execute(f"UPDATE {tbl_a} SET {res_a}=MAX(0,{res_a}-?) WHERE country_id=?", (amt_a, a_id))
        await db.execute(f"UPDATE {_tbl(res_b)} SET {res_b}={res_b}+? WHERE country_id=?", (amt_b, a_id))
        # B отдаёт res_b → получает res_a
        await db.execute(f"UPDATE {tbl_b} SET {res_b}=MAX(0,{res_b}-?) WHERE country_id=?", (amt_b, b_id))
        await db.execute(f"UPDATE {_tbl(res_a)} SET {res_a}={res_a}+? WHERE country_id=?", (amt_a, b_id))

        await db.execute("UPDATE trade_deals SET status='completed' WHERE id=?", (deal_id,))
        await db.commit()

    label_a = ALL_TRADE_NAMES.get(res_a, res_a)
    label_b = ALL_TRADE_NAMES.get(res_b, res_b)
    return True, f"✅ Сделка выполнена! {label_a} ×{amt_a} ↔ {label_b} ×{amt_b} — ресурсы начислены."


async def decline_trade(chat_id: int, deal_id: int, country_id: int) -> tuple:
    """Отклонить торговое предложение."""
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT id FROM trade_deals WHERE id=? AND chat_id=? AND country_b_id=? AND status='pending'",
            (deal_id, chat_id, country_id)
        )
        if not await c.fetchone():
            return False, "Предложение не найдено или уже обработано"
        await db.execute("UPDATE trade_deals SET status='declined' WHERE id=?", (deal_id,))
        await db.commit()
        return True, "Торговое предложение отклонено."


async def get_trade_deals(chat_id: int, country_id: int) -> list:
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM trade_deals WHERE chat_id=? AND (country_a_id=? OR country_b_id=?) AND status='pending'",
            (chat_id, country_id, country_id)
        )
        return [dict(r) for r in await c.fetchall()]


async def execute_trade_tick(chat_id: int):
    """Обмениваем ресурсы/войска по всем активным сделкам."""
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT * FROM trade_deals WHERE chat_id=? AND status='active'", (chat_id,))
        deals = [dict(r) for r in await c.fetchall()]
        for deal in deals:
            a_id, b_id = deal["country_a_id"], deal["country_b_id"]
            res_a, amt_a = deal["resource_a"], deal["amount_a"]
            res_b, amt_b = deal["resource_b"], deal["amount_b"]

            def _tbl(r): return "resources" if r in RESOURCE_NAMES else "military"
            def _cid(r): return "country_id"

            # A отдаёт res_a, получает res_b
            await db.execute(f"UPDATE {_tbl(res_a)} SET {res_a}=MAX(0,{res_a}-?) WHERE country_id=?", (amt_a, a_id))
            await db.execute(f"UPDATE {_tbl(res_b)} SET {res_b}={res_b}+? WHERE country_id=?", (amt_b, a_id))
            # B отдаёт res_b, получает res_a
            await db.execute(f"UPDATE {_tbl(res_b)} SET {res_b}=MAX(0,{res_b}-?) WHERE country_id=?", (amt_b, b_id))
            await db.execute(f"UPDATE {_tbl(res_a)} SET {res_a}={res_a}+? WHERE country_id=?", (amt_a, b_id))
        await db.commit()


# ═══════════════════════════════════════════════════════════════
#  DB HELPERS — ВАССАЛИТЕТ
# ═══════════════════════════════════════════════════════════════

async def demand_vassalage(chat_id: int, overlord_id: int, vassal_id: int, tribute_pct: int = 20) -> tuple:
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT id FROM vassals WHERE chat_id=? AND vassal_id=?", (chat_id, vassal_id))
        if await c.fetchone():
            return False, "Эта страна уже является вассалом"
        # Создаём pending-запрос, не сразу устанавливаем вассалитет
        try:
            await db.execute("ALTER TABLE vassals ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
        except Exception:
            pass
        await db.execute(
            "INSERT INTO vassals (chat_id, overlord_id, vassal_id, tribute_pct, status) VALUES (?,?,?,?,'pending')",
            (chat_id, overlord_id, vassal_id, tribute_pct)
        )
        await db.commit()
        c2 = await db.execute("SELECT id FROM vassals WHERE chat_id=? AND vassal_id=? AND status='pending' ORDER BY id DESC LIMIT 1", (chat_id, vassal_id))
        row = await c2.fetchone()
        deal_id = row["id"] if row else 0
        return True, f"Требование вассалитета отправлено (дань {tribute_pct}%). Ждём ответа.", deal_id


async def accept_vassalage(chat_id: int, vassal_id: int, deal_id: int) -> tuple:
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT * FROM vassals WHERE id=? AND chat_id=? AND vassal_id=? AND status='pending'", (deal_id, chat_id, vassal_id))
        if not await c.fetchone():
            return False, "Запрос не найден"
        await db.execute("UPDATE vassals SET status='active' WHERE id=?", (deal_id,))
        await db.commit()
        return True, "Вассалитет принят. Дань будет взиматься каждый тик."


async def decline_vassalage(chat_id: int, vassal_id: int, deal_id: int) -> tuple:
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT id FROM vassals WHERE id=? AND chat_id=? AND vassal_id=? AND status='pending'", (deal_id, chat_id, vassal_id))
        if not await c.fetchone():
            return False, "Запрос не найден"
        await db.execute("DELETE FROM vassals WHERE id=?", (deal_id,))
        await db.commit()
        return True, "Требование вассалитета отклонено."


async def collect_tribute(chat_id: int):
    """Сюзерены получают дань от вассалов каждый тик ресурсов."""
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT * FROM vassals WHERE chat_id=? AND (status='active' OR status IS NULL)", (chat_id,))
        vassals = [dict(r) for r in await c.fetchall()]
        for v in vassals:
            c2 = await db.execute("SELECT treasury FROM countries WHERE id=?", (v["vassal_id"],))
            vassal = await c2.fetchone()
            if not vassal:
                continue
            tribute = int(vassal["treasury"] * v["tribute_pct"] / 100)
            if tribute > 0:
                await db.execute("UPDATE countries SET treasury=treasury-? WHERE id=?", (tribute, v["vassal_id"]))
                await db.execute("UPDATE countries SET treasury=treasury+? WHERE id=?", (tribute, v["overlord_id"]))
        await db.commit()


# ═══════════════════════════════════════════════════════════════
#  DB HELPERS — ДОСТИЖЕНИЯ
# ═══════════════════════════════════════════════════════════════

ACHIEVEMENTS = {
    "first_blood":    {"name": "Боевое крещение",    "emoji": "⚔️",  "desc": "Выиграть первую битву"},
    "empire":         {"name": "Империя",             "emoji": "👑",  "desc": "Захватить 10+ территорий"},
    "superpower":     {"name": "Сверхдержава",        "emoji": "🌍",  "desc": "Захватить 25+ территорий"},
    "rich":           {"name": "Богач",               "emoji": "💰",  "desc": "Накопить 5000💰"},
    "scientist":      {"name": "Учёный",              "emoji": "🔬",  "desc": "Изучить 5 технологий"},
    "modern_era":     {"name": "Современная эра",     "emoji": "🚀",  "desc": "Достичь эры Современность"},
    "builder":        {"name": "Строитель",           "emoji": "🏗",  "desc": "Построить 10 зданий"},
    "spy_master":     {"name": "Мастер шпионажа",     "emoji": "🕵️", "desc": "Провести 5 успешных операций"},
    "bloc_leader":    {"name": "Лидер блока",         "emoji": "🤝",  "desc": "Основать военный блок"},
    "nuclear_power":  {"name": "Ядерная держава",     "emoji": "☢️",  "desc": "Изучить ядерную теорию"},
    "war_machine":    {"name": "Военная машина",      "emoji": "💣",  "desc": "Иметь армию 1000+ солдат"},
    "peacemaker":     {"name": "Миротворец",          "emoji": "🕊",  "desc": "Заключить 3 мирных договора"},
    "trader":         {"name": "Торговец",            "emoji": "📦",  "desc": "Заключить 3 торговых сделки"},
    "population_boom":{"name": "Демографический взрыв","emoji":"👥", "desc": "Достичь населения 10 000"},
}

async def get_achievements(country_id: int) -> list:
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT achievement_id FROM achievements WHERE country_id=?", (country_id,))
        return [r["achievement_id"] for r in await c.fetchall()]


async def grant_achievement(chat_id: int, country_id: int, ach_id: str, bot=None) -> bool:
    if ach_id not in ACHIEVEMENTS:
        return False
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT id FROM achievements WHERE country_id=? AND achievement_id=?", (country_id, ach_id))
        if await c.fetchone():
            return False
        await db.execute(
            "INSERT INTO achievements (country_id, achievement_id) VALUES (?,?)", (country_id, ach_id)
        )
        await db.commit()
    # Уведомляем в чат
    if bot:
        ach = ACHIEVEMENTS[ach_id]
        c2 = await get_country_by_id(chat_id, country_id)
        if c2:
            try:
                await bot.send_message(
                    chat_id,
                    f"🏆 <b>Достижение разблокировано!</b>\n"
                    f"{c2['emoji']} {c2['name']} — {ach['emoji']} <b>{ach['name']}</b>\n"
                    f"<i>{ach['desc']}</i>",
                    parse_mode="HTML"
                )
            except Exception:
                pass
    return True


async def check_achievements(chat_id: int, country_id: int, bot=None):
    """Проверяем и выдаём все применимые достижения."""
    country = await get_country_by_id(chat_id, country_id)
    if not country:
        return

    already = set(await get_achievements(country_id))
    ter = await get_country_territories(chat_id, country_id)
    res = await get_resources(country_id)
    tech_data = await get_technologies(country_id)
    buildings = await get_buildings(chat_id, country_id)

    async def give(ach_id):
        if ach_id not in already:
            await grant_achievement(chat_id, country_id, ach_id, bot)
            already.add(ach_id)

    if len(ter) >= 10:  await give("empire")
    if len(ter) >= 25:  await give("superpower")
    if country["treasury"] >= 5000: await give("rich")
    if len(tech_data["techs"]) >= 5: await give("scientist")
    if tech_data["era"] == "modern": await give("modern_era")
    if len(buildings) >= 10: await give("builder")
    if country["army"] >= 1000: await give("war_machine")
    if res and res["population"] >= 10000: await give("population_boom")
    if "nuclear_theory" in tech_data["techs"]: await give("nuclear_power")

    # Шпионаж
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT COUNT(*) FROM spy_ops WHERE attacker_id=? AND result='success'", (country_id,)
        )
        spy_count = (await c.fetchone())[0]
        if spy_count >= 5:
            await give("spy_master")

        # Торговля
        c2 = await db.execute(
            "SELECT COUNT(*) FROM trade_deals WHERE chat_id=? AND (country_a_id=? OR country_b_id=?) AND status='completed'",
            (chat_id, country_id, country_id)
        )
        trade_count = (await c2.fetchone())[0]
        if trade_count >= 3:
            await give("trader")


# ═══════════════════════════════════════════════════════════════
#  DB HELPERS — ИСТОРИЯ СТРАНЫ
# ═══════════════════════════════════════════════════════════════

async def get_country_history(chat_id: int, country_id: int, limit: int = 15) -> list:
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM event_log WHERE chat_id=? AND data LIKE ? ORDER BY created_at DESC LIMIT ?",
            (chat_id, f"%{country_id}%", limit)
        )
        return [dict(r) for r in await c.fetchall()]


# ═══════════════════════════════════════════════════════════════
#  DB HELPERS — ЯДЕРНОЕ ОРУЖИЕ + ГРАЖДАНСКАЯ ВОЙНА
# ═══════════════════════════════════════════════════════════════

async def nuclear_strike(chat_id: int, attacker_id: int, target_iso: str) -> tuple:
    """Ядерный удар — уничтожает территорию, огромные потери, дипломатические последствия."""
    
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT * FROM countries WHERE id=?", (attacker_id,))
        attacker = await c.fetchone()
        if not attacker:
            return False, "Страна не найдена"

        # Проверяем технологию
        c2 = await db.execute("SELECT techs FROM technologies WHERE country_id=?", (attacker_id,))
        tech = await c2.fetchone()
        techs = json.loads(tech["techs"]) if tech else []
        if "nuclear_theory" not in techs:
            return False, "Нужна технология: Ядерная теория"

        # Кулдаун — не чаще раза в 30 минут
        c_cd = await db.execute("SELECT last_nuclear FROM countries WHERE id=?", (attacker_id,))
        cd_row = await c_cd.fetchone()
        now = int(time.time())
        last_nuke = cd_row["last_nuclear"] if cd_row and "last_nuclear" in cd_row.keys() else 0
        if last_nuke and now - last_nuke < 1800:
            remaining = 1800 - (now - last_nuke)
            return False, f"Ядерное оружие перезаряжается ещё {remaining//60}м {remaining%60}с"

        # Стоимость — 2000 золота
        if attacker["treasury"] < 2000:
            return False, f"Нужно 2000💰, у вас {attacker['treasury']}💰"

        c3 = await db.execute(
            "SELECT country_id FROM territories WHERE chat_id=? AND iso=?", (chat_id, target_iso.upper())
        )
        ter = await c3.fetchone()

        await db.execute("UPDATE countries SET treasury=treasury-2000 WHERE id=?", (attacker_id,))

        victim_id = None
        victim = None
        if ter and ter["country_id"]:
            victim_id = ter["country_id"]
            c_v = await db.execute("SELECT * FROM countries WHERE id=?", (victim_id,))
            victim = await c_v.fetchone()

            # Уничтожаем территорию — становится нейтральной (радиоактивной)
            await db.execute(
                "UPDATE territories SET country_id=NULL WHERE chat_id=? AND iso=?",
                (chat_id, target_iso.upper())
            )
            # Уничтожаем все здания на территории
            await db.execute(
                "DELETE FROM buildings WHERE chat_id=? AND iso=?", (chat_id, target_iso.upper())
            )
            # Потери армии жертвы: -50% всего
            await db.execute(
                "UPDATE countries SET army=MAX(0, army/2) WHERE id=?", (victim_id,)
            )
            await db.execute(
                "UPDATE military SET infantry=MAX(0,infantry/2), cavalry=MAX(0,cavalry/2), "
                "artillery=MAX(0,artillery/2), airforce=MAX(0,airforce/2), "
                "navy=MAX(0,navy/2), bomber=MAX(0,bomber/2), "
                "armored=MAX(0,armored/2), tank=MAX(0,tank/2) WHERE country_id=?",
                (victim_id,)
            )
            # Потери ресурсов жертвы: -30%
            await db.execute(
                "UPDATE resources SET food=MAX(0,food*7/10), oil=MAX(0,oil*7/10), "
                "production=MAX(0,production*7/10) WHERE country_id=?",
                (victim_id,)
            )
            # Дипломатические последствия: -500 к казне агрессора (международные санкции)
            await db.execute(
                "UPDATE countries SET treasury=MAX(0,treasury-500) WHERE id=?", (attacker_id,)
            )

        # Записываем время последнего удара
        await db.execute("UPDATE countries SET last_nuclear=? WHERE id=?", (now, attacker_id))
        await db.commit()

    ter_name = COUNTRY_NAMES.get(target_iso.upper(), target_iso.upper())
    victim_str = f" {make_country_str(victim)} {make_owner_mention(victim)}" if victim else ""
    return True, (
        f"☢️ <b>ЯДЕРНЫЙ УДАР!</b>\n\n"
        f"{make_country_str(attacker)} {make_owner_mention(attacker)} нанёс ядерный удар по <b>{ter_name}</b>!{victim_str}\n\n"
        f"💥 Территория опустошена и стала нейтральной\n"
        f"💀 Армия противника потеряла 50%\n"
        f"📦 Ресурсы противника сокращены на 30%\n"
        f"🌐 Международные санкции: -500💰 с казны агрессора\n"
        f"⏱ Следующий удар доступен через 30 минут"
    )


async def check_civil_war(chat_id: int, country_id: int, bot=None) -> bool:
    """Проверяем условия гражданской войны — недовольство при нехватке еды."""
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT * FROM resources WHERE country_id=?", (country_id,))
        res = await c.fetchone()
        if not res:
            return False

        # Гражданская война при нулевой еде и большом населении
        if res["food"] > 0:
            return False

        c2 = await db.execute("SELECT * FROM countries WHERE id=?", (country_id,))
        country = await c2.fetchone()
        if not country:
            return False

        import random as _r
        # 30% шанс гражданской войны при голоде
        if _r.randint(1, 100) > 30:
            return False

        # Теряем случайную территорию
        c3 = await db.execute(
            "SELECT iso FROM territories WHERE country_id=? AND chat_id=? ORDER BY RANDOM() LIMIT 1",
            (country_id, chat_id)
        )
        ter = await c3.fetchone()
        if ter:
            await db.execute(
                "UPDATE territories SET country_id=NULL WHERE iso=? AND chat_id=?",
                (ter["iso"], chat_id)
            )
            # Потери армии
            await db.execute(
                """UPDATE military SET
                       infantry=MAX(0, infantry*3/4), cavalry=MAX(0, cavalry*3/4),
                       artillery=MAX(0, artillery*3/4), airforce=MAX(0, airforce*3/4),
                       bomber=MAX(0, bomber*3/4), navy=MAX(0, navy*3/4),
                       armored=MAX(0, armored*3/4), tank=MAX(0, tank*3/4)
                   WHERE country_id=?""",
                (country_id,)
            )
            await db.execute(
                """UPDATE countries SET army=COALESCE((
                       SELECT infantry+cavalry+artillery+airforce+bomber+navy+armored+tank
                       FROM military WHERE country_id=?
                   ), 0) WHERE id=?""",
                (country_id, country_id)
            )
            # Очищаем гарнизон на потерянной территории
            await db.execute(
                "DELETE FROM garrisons WHERE chat_id=? AND iso=? AND country_id=?",
                (chat_id, ter["iso"], country_id)
            )
            await db.commit()

            if bot:
                    ter_name = COUNTRY_NAMES.get(ter["iso"], ter["iso"])
                    try:
                        await bot.send_message(
                            chat_id,
                            f"🔥 <b>ГРАЖДАНСКАЯ ВОЙНА!</b>\n\n"
                            f"{make_country_str(dict(country))} {make_owner_mention(dict(country))} страдает от голода!\n"
                            f"Восстание охватило <b>{ter_name}</b> — территория потеряна!\n"
                            f"Армия потеряла 25% личного состава.",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
            return True
    return False


# ═══════════════════════════════════════════════════════════════
#  СИСТЕМА НАСЕЛЕНИЯ И СЧАСТЬЯ
# ═══════════════════════════════════════════════════════════════

POPULATION_PER_TERRITORY = 1_000_000   # 1 млн человек = 1 территория

# Факторы влияния на счастье (дельта за тик)
HAPPINESS_FACTORS = {
    "food_ok":       +3,   # есть еда
    "food_low":      -8,   # еда < 20
    "food_zero":     -20,  # еда = 0
    "war":           -5,   # идёт война
    "oil_zero":      -4,   # нет нефти
    "production_low":-3,   # производство < 10
    "rich":          +2,   # казна > 2000
    "poor":          -4,   # казна < 200
}

# Пороги протестов
PROTEST_THRESHOLD = 40    # ниже → малые протесты (штраф к казне, армии)
REVOLT_THRESHOLD  = 15    # ниже → массовые протесты (потеря территории)
REVOLUTION_THRESHOLD = 0  # = 0 → революция (территория уходит нейтральной или противнику)


async def get_happiness(country_id: int) -> int:
    """Возвращает текущий уровень счастья (0-100)."""
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT happiness FROM resources WHERE country_id=?", (country_id,))
        row = await c.fetchone()
        if not row:
            return 70
        return row[0] if row[0] is not None else 70


async def update_happiness(country_id: int, delta: int):
    """Изменяет счастье, зажимая в [0, 100]."""
    db = await _open_db()
    async with db:
        await db.execute(
            "UPDATE resources SET happiness=MAX(0,MIN(100,happiness+?)) WHERE country_id=?",
            (delta, country_id)
        )
        await db.commit()


def happiness_emoji(h: int) -> str:
    if h >= 75: return "😄"
    if h >= 55: return "🙂"
    if h >= 40: return "😐"
    if h >= 20: return "😠"
    if h >= 10: return "😡"
    return "🔥"


def happiness_label(h: int) -> str:
    if h >= 75: return "Счастливы"
    if h >= 55: return "Довольны"
    if h >= 40: return "Нейтральны"
    if h >= 20: return "Недовольны"
    if h >= 10: return "В ярости"
    return "На грани революции"


async def tick_happiness(chat_id: int, country_id: int, bot=None):
    """
    Главная функция тика счастья.
    Вычисляет дельту, применяет, проверяет пороги протестов.
    Вызывается из resource_tick.
    """
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT * FROM resources WHERE country_id=?", (country_id,))
        res = await c.fetchone()
        if not res:
            return
        c2 = await db.execute("SELECT * FROM countries WHERE id=?", (country_id,))
        country = await c2.fetchone()
        if not country:
            return

        h = res["happiness"] if res["happiness"] is not None else 70
        delta = 0
        reasons = []

        # Еда
        food = res["food"]
        if food == 0:
            delta += HAPPINESS_FACTORS["food_zero"]
            reasons.append("голод")
        elif food < 20:
            delta += HAPPINESS_FACTORS["food_low"]
            reasons.append("нехватка еды")
        else:
            delta += HAPPINESS_FACTORS["food_ok"]

        # Нефть — штраф только если страна использует технику (авиация/флот/броня/танки)
        if res["oil"] == 0:
            c_mil = await db.execute(
                "SELECT COALESCE(airforce,0)+COALESCE(navy,0)+COALESCE(bomber,0)+COALESCE(armored,0)+COALESCE(tank,0) as tech "
                "FROM military WHERE country_id=?", (country_id,)
            )
            mil_tech = await c_mil.fetchone()
            if mil_tech and mil_tech["tech"] > 0:
                delta += HAPPINESS_FACTORS["oil_zero"]
                reasons.append("нет нефти для техники")

        # Производство
        if res["production"] < 10:
            delta += HAPPINESS_FACTORS["production_low"]
            reasons.append("слабая промышленность")

        # Казна
        if country["treasury"] > 2000:
            delta += HAPPINESS_FACTORS["rich"]
        elif country["treasury"] < 200:
            delta += HAPPINESS_FACTORS["poor"]
            reasons.append("пустая казна")

        # Война
        c3 = await db.execute(
            "SELECT COUNT(*) FROM wars WHERE chat_id=? AND status='active' "
            "AND (attacker_id=? OR defender_id=?)",
            (chat_id, country_id, country_id)
        )
        war_count = (await c3.fetchone())[0]
        if war_count > 0:
            delta += HAPPINESS_FACTORS["war"] * war_count
            reasons.append("война")

        # Применяем дельту
        new_h = max(0, min(100, h + delta))
        await db.execute(
            "UPDATE resources SET happiness=? WHERE country_id=?", (new_h, country_id)
        )
        await db.commit()

    # Проверяем пороги и вызываем события
    await _check_protest_thresholds(chat_id, country_id, new_h, reasons, bot)


async def _check_protest_thresholds(chat_id: int, country_id: int, happiness: int,
                                     reasons: list, bot=None):
    """Вызывает события протестов/революций в зависимости от уровня счастья."""
    import random as _r

    db = await _open_db()
    async with db:
        c = await db.execute("SELECT * FROM countries WHERE id=?", (country_id,))
        country = await c.fetchone()
        if not country:
            return

        c2 = await db.execute(
            "SELECT COUNT(*) FROM territories WHERE chat_id=? AND country_id=?",
            (chat_id, country_id)
        )
        ter_count = (await c2.fetchone())[0]
        if ter_count == 0:
            return

        reason_str = ", ".join(reasons) if reasons else "общее недовольство"

        # ── РЕВОЛЮЦИЯ (счастье = 0) ─────────────────────────────
        if happiness == 0:
            # Теряем случайную территорию — она уходит нейтральной или ближайшему врагу
            c3 = await db.execute(
                "SELECT iso FROM territories WHERE chat_id=? AND country_id=? "
                "ORDER BY RANDOM() LIMIT 1", (chat_id, country_id)
            )
            ter = await c3.fetchone()
            if ter:
                lost_iso = ter["iso"]
                # Ищем воюющего противника чтобы отдать ему (если есть)
                c4 = await db.execute(
                    "SELECT attacker_id, defender_id FROM wars WHERE chat_id=? AND status='active' "
                    "AND (attacker_id=? OR defender_id=?) LIMIT 1",
                    (chat_id, country_id, country_id)
                )
                war_row = await c4.fetchone()
                new_owner = None
                if war_row:
                    new_owner = war_row["attacker_id"] if war_row["defender_id"] == country_id else war_row["defender_id"]

                if new_owner:
                    await db.execute(
                        "UPDATE territories SET country_id=? WHERE chat_id=? AND iso=?",
                        (new_owner, chat_id, lost_iso)
                    )
                    c5 = await db.execute("SELECT * FROM countries WHERE id=?", (new_owner,))
                    winner = await c5.fetchone()
                    winner_str = f"и перешла под контроль {winner['emoji']} <b>{winner['name']}</b>" if winner else "и стала нейтральной"
                else:
                    await db.execute(
                        "UPDATE territories SET country_id=NULL WHERE chat_id=? AND iso=?",
                        (chat_id, lost_iso)
                    )
                    winner_str = "и стала нейтральной территорией"

                # Потери армии от дезертирства
                await db.execute(
                    """UPDATE military SET
                           infantry=MAX(0, infantry*2/3), cavalry=MAX(0, cavalry*2/3),
                           artillery=MAX(0, artillery*2/3), airforce=MAX(0, airforce*2/3),
                           bomber=MAX(0, bomber*2/3), navy=MAX(0, navy*2/3),
                           armored=MAX(0, armored*2/3), tank=MAX(0, tank*2/3)
                       WHERE country_id=?""",
                    (country_id,)
                )
                await db.execute(
                    """UPDATE countries SET army=COALESCE((
                           SELECT infantry+cavalry+artillery+airforce+bomber+navy+armored+tank
                           FROM military WHERE country_id=?
                       ), 0) WHERE id=?""",
                    (country_id, country_id)
                )
                # Гарнизон на потерянной территории уничтожается (дезертиры)
                await db.execute(
                    "DELETE FROM garrisons WHERE chat_id=? AND iso=? AND country_id=?",
                    (chat_id, lost_iso, country_id)
                )
                # Сбрасываем счастье чуть выше 0 чтобы не было бесконечной потери
                await db.execute(
                    "UPDATE resources SET happiness=15 WHERE country_id=?", (country_id,)
                )
                await db.execute(
                    "INSERT INTO protest_log (chat_id, country_id, iso, cause) VALUES (?,?,?,?)",
                    (chat_id, country_id, lost_iso, "revolution")
                )
                await db.commit()

                if bot:
                    ter_name = COUNTRY_NAMES.get(lost_iso, lost_iso)
                    pop = ter_count * POPULATION_PER_TERRITORY
                    try:
                        await bot.send_message(
                            chat_id,
                            f"🔴 <b>РЕВОЛЮЦИЯ!</b>\n\n"
                            f"{country['emoji']} <b>{country['name']}</b> — счастье народа упало до нуля!\n"
                            f"👥 {pop:,} человек вышли на улицы!\n\n"
                            f"📍 Территория <b>{ter_name}</b> охвачена восстанием {winner_str}\n"
                            f"⚔️ Армия потеряла 33% из-за массового дезертирства\n"
                            f"📌 Причины: {reason_str}\n\n"
                            f"⚠️ <i>Срочно повысьте уровень счастья или потеряете ещё территории!</i>",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
            return

        # ── МАССОВЫЕ ПРОТЕСТЫ (счастье ≤ REVOLT_THRESHOLD) ──────
        if happiness <= REVOLT_THRESHOLD:
            # 50% шанс потери территории
            if _r.randint(1, 100) <= 50:
                c3 = await db.execute(
                    "SELECT iso FROM territories WHERE chat_id=? AND country_id=? "
                    "ORDER BY RANDOM() LIMIT 1", (chat_id, country_id)
                )
                ter = await c3.fetchone()
                if ter:
                    lost_iso = ter["iso"]
                    await db.execute(
                        "UPDATE territories SET country_id=NULL WHERE chat_id=? AND iso=?",
                        (chat_id, lost_iso)
                    )
                    await db.execute(
                        """UPDATE military SET
                               infantry=MAX(0, infantry*4/5), cavalry=MAX(0, cavalry*4/5),
                               artillery=MAX(0, artillery*4/5), airforce=MAX(0, airforce*4/5),
                               bomber=MAX(0, bomber*4/5), navy=MAX(0, navy*4/5),
                               armored=MAX(0, armored*4/5), tank=MAX(0, tank*4/5)
                           WHERE country_id=?""",
                        (country_id,)
                    )
                    await db.execute(
                        """UPDATE countries SET army=COALESCE((
                               SELECT infantry+cavalry+artillery+airforce+bomber+navy+armored+tank
                               FROM military WHERE country_id=?
                           ), 0) WHERE id=?""",
                        (country_id, country_id)
                    )
                    await db.execute(
                        "DELETE FROM garrisons WHERE chat_id=? AND iso=? AND country_id=?",
                        (chat_id, lost_iso, country_id)
                    )
                    await db.execute(
                        "INSERT INTO protest_log (chat_id, country_id, iso, cause) VALUES (?,?,?,?)",
                        (chat_id, country_id, lost_iso, "mass_revolt")
                    )
                    await db.commit()

                    if bot:
                        ter_name = COUNTRY_NAMES.get(lost_iso, lost_iso)
                        pop = ter_count * POPULATION_PER_TERRITORY
                        try:
                            await bot.send_message(
                                chat_id,
                                f"😡 <b>МАССОВЫЕ ПРОТЕСТЫ!</b>\n\n"
                                f"{country['emoji']} <b>{country['name']}</b> — народ восстаёт!\n"
                                f"👥 {pop:,} человек требуют перемен!\n\n"
                                f"📍 <b>{ter_name}</b> вышла из-под контроля и стала нейтральной\n"
                                f"⚔️ Армия потеряла 20% из-за дезертиров\n"
                                f"📌 Причины: {reason_str}\n"
                                f"😠 Счастье народа: {happiness}/100",
                                parse_mode="HTML"
                            )
                        except Exception:
                            pass
            return

        # ── МАЛЫЕ ПРОТЕСТЫ (счастье ≤ PROTEST_THRESHOLD) ────────
        if happiness <= PROTEST_THRESHOLD:
            # 30% шанс забастовки — теряем монеты и производство
            if _r.randint(1, 100) <= 30:
                treasury_loss = max(50, country["treasury"] // 10)
                await db.execute(
                    "UPDATE countries SET treasury=MAX(0,treasury-?) WHERE id=?",
                    (treasury_loss, country_id)
                )
                await db.execute(
                    "UPDATE resources SET production=MAX(0,production-10) WHERE country_id=?",
                    (country_id,)
                )
                await db.execute(
                    "INSERT INTO protest_log (chat_id, country_id, iso, cause) VALUES (?,?,?,?)",
                    (chat_id, country_id, country["iso"], "strike")
                )
                await db.commit()

                if bot:
                    pop = ter_count * POPULATION_PER_TERRITORY
                    try:
                        await bot.send_message(
                            chat_id,
                            f"✊ <b>Забастовки в {country['emoji']} {country['name']}!</b>\n\n"
                            f"👥 Население ({pop:,} чел.) недовольно властью\n"
                            f"📌 Причины: {reason_str}\n"
                            f"💸 Казна потеряла {treasury_loss}💰 из-за забастовок\n"
                            f"⚙️ Производство снизилось на 10\n"
                            f"{happiness_emoji(happiness)} Счастье: {happiness}/100 — {happiness_label(happiness)}",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass


async def check_economic_crisis(chat_id: int, country_id: int, bot=None):
    """Проверяем экономические кризисы: нефть → авиация не летает; еда → дезертирство."""
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT * FROM resources WHERE country_id=?", (country_id,))
        res = await c.fetchone()
        if not res:
            return

        c2 = await db.execute("SELECT * FROM countries WHERE id=?", (country_id,))
        country = await c2.fetchone()
        if not country:
            return

        c3 = await db.execute("SELECT * FROM military WHERE country_id=?", (country_id,))
        mil = await c3.fetchone()
        if not mil:
            return

        messages = []

        # Нет нефти — авиация и флот теряют 10% в тик
        if res["oil"] == 0 and (mil["airforce"] > 0 or (mil["navy"] or 0) > 0 or (mil["bomber"] or 0) > 0):
            loss_air = max(0, mil["airforce"] // 10)
            loss_navy = max(0, (mil["navy"] or 0) // 10)
            loss_bomber = max(0, (mil["bomber"] or 0) // 10)
            await db.execute(
                "UPDATE military SET airforce=MAX(0,airforce-?), navy=MAX(0,navy-?), bomber=MAX(0,bomber-?) WHERE country_id=?",
                (loss_air, loss_navy, loss_bomber, country_id)
            )
            if loss_air + loss_navy + loss_bomber > 0:
                messages.append(f"🛢 Нет нефти! Авиация и флот теряют технику ({loss_air+loss_navy+loss_bomber} ед.)")

        # Нет еды — дезертирство пехоты
        if res["food"] == 0 and mil["infantry"] > 0:
            deserters = max(5, mil["infantry"] // 8)
            await db.execute(
                "UPDATE military SET infantry=MAX(0,infantry-?) WHERE country_id=?", (deserters, country_id)
            )
            messages.append(f"🌾 Голод! {deserters} пехотинцев дезертировали")

        if messages:
            # Пересчитываем army
            c4 = await db.execute("SELECT * FROM military WHERE country_id=?", (country_id,))
            m = await c4.fetchone()
            if m:
                total = (m["infantry"] or 0) + (m["cavalry"] or 0) + (m["artillery"] or 0) + (m["airforce"] or 0) + (m["navy"] or 0) + (m["bomber"] or 0) + (m["armored"] or 0) + (m["tank"] or 0)
                await db.execute("UPDATE countries SET army=? WHERE id=?", (total, country_id))

        await db.commit()

        if messages and bot:
            try:
                await bot.send_message(
                    chat_id,
                    f"⚠️ <b>Кризис в {country['emoji']} {country['name']}!</b>\n" + "\n".join(messages),
                    parse_mode="HTML"
                )
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════
#  СОСЕДИ (для expand/attack)
# ═══════════════════════════════════════════════════════════════

NEIGHBORS: dict[str, list[str]] = {
    "RU": ["NO","FI","EE","LV","LT","BY","UA","GE","AZ","KZ","MN","CN"],
    "NO": ["RU","SE","FI"],
    "SE": ["NO","FI","DK"],
    "FI": ["RU","NO","SE","EE"],
    "EE": ["RU","FI","LV"],
    "LV": ["RU","EE","LT","BY"],
    "LT": ["RU","LV","BY","PL"],
    "BY": ["RU","LV","LT","PL","UA"],
    "UA": ["RU","BY","PL","SK","HU","RO","MD"],
    "MD": ["UA","RO"],
    "PL": ["RU","LT","BY","UA","DE","CZ","SK"],
    "DE": ["PL","CZ","AT","CH","FR","LU","BE","NL","DK"],
    "DK": ["DE","SE"],
    "NL": ["DE","BE"],
    "BE": ["NL","DE","LU","FR"],
    "LU": ["BE","DE","FR"],
    "FR": ["BE","LU","DE","CH","IT","ES","AD","MC"],
    "AD": ["FR","ES"],
    "MC": ["FR"],
    "ES": ["FR","AD","PT"],
    "PT": ["ES"],
    "CH": ["FR","DE","AT","IT","LI"],
    "LI": ["CH","AT"],
    "AT": ["DE","CZ","SK","HU","SI","IT","CH","LI"],
    "CZ": ["DE","PL","SK","AT"],
    "SK": ["CZ","PL","UA","HU","AT"],
    "HU": ["SK","UA","RO","RS","HR","SI","AT"],
    "SI": ["AT","HU","HR","IT"],
    "HR": ["SI","HU","RS","BA"],
    "BA": ["HR","RS","ME"],
    "RS": ["HU","RO","BG","MK","AL","ME","BA","HR"],
    "ME": ["RS","BA","AL"],
    "AL": ["ME","RS","MK","GR"],
    "MK": ["RS","AL","GR","BG"],
    "GR": ["AL","MK","BG","TR"],
    "BG": ["RO","RS","MK","GR","TR"],
    "RO": ["UA","MD","HU","RS","BG"],
    "TR": ["GR","BG","GE","AM","AZ","IR","IQ","SY"],
    "GE": ["RU","TR","AM","AZ"],
    "AM": ["GE","TR","AZ","IR"],
    "AZ": ["RU","GE","AM","TR","IR"],
    "SY": ["TR","IQ","JO","LB","IL"],
    "LB": ["SY","IL"],
    "IL": ["LB","SY","JO","EG"],
    "PS": ["IL","JO","EG"],
    "JO": ["SY","IL","PS","SA","IQ"],
    "IQ": ["TR","SY","JO","SA","KW","IR"],
    "KW": ["IQ","SA"],
    "SA": ["JO","IQ","KW","AE","OM","YE"],
    "AE": ["SA","OM","QA"],
    "QA": ["SA","AE"],
    "BH": ["SA","QA"],
    "OM": ["SA","AE","YE"],
    "YE": ["SA","OM"],
    "IR": ["TR","AM","AZ","TM","AF","PK","IQ"],
    "TM": ["IR","AF","UZ","KZ"],
    "UZ": ["TM","KZ","KG","TJ","AF"],
    "KZ": ["RU","TM","UZ","KG","CN"],
    "KG": ["KZ","UZ","TJ","CN"],
    "TJ": ["UZ","KG","AF","CN"],
    "AF": ["IR","TM","UZ","TJ","PK","CN"],
    "PK": ["IR","AF","IN","CN"],
    "IN": ["PK","CN","NP","BD","MM"],
    "NP": ["IN","CN"],
    "BD": ["IN","MM"],
    "MM": ["CN","IN","BD","LA","TH"],
    "TH": ["MM","LA","KH","MY"],
    "LA": ["CN","MM","TH","VN","KH"],
    "VN": ["CN","LA","KH"],
    "KH": ["TH","LA","VN"],
    "MY": ["TH","ID","SG"],
    "SG": ["MY"],
    "ID": ["MY","PG","AU"],
    "PH": ["MY"],
    "CN": ["RU","MN","KZ","KG","TJ","AF","PK","IN","NP","MM","LA","VN"],
    "MN": ["RU","CN"],
    "KR": ["CN"],
    "JP": [],
    "TW": [],
    "AU": ["ID","PG","NZ"],
    "PG": ["ID","AU"],
    "NZ": ["AU"],
    "CA": ["US"],
    "US": ["CA","MX"],
    "MX": ["US","GT","BZ"],
    "GT": ["MX","BZ","HN","SV"],
    "BZ": ["MX","GT"],
    "HN": ["GT","SV","NI"],
    "SV": ["GT","HN"],
    "NI": ["HN","CR"],
    "CR": ["NI","PA"],
    "PA": ["CR","CO"],
    "CU": [],
    "JM": [],
    "HT": ["DO"],
    "DO": ["HT"],
    "TT": [],
    "CO": ["PA","VE","BR","PE","EC"],
    "VE": ["CO","BR","GY"],
    "GY": ["VE","BR","SR"],
    "SR": ["GY","BR"],
    "BR": ["VE","GY","SR","CO","PE","BO","PY","AR","UY"],
    "EC": ["CO","PE"],
    "PE": ["EC","CO","BR","BO","CL"],
    "BO": ["PE","BR","PY","AR","CL"],
    "PY": ["BO","BR","AR"],
    "CL": ["PE","BO","AR"],
    "AR": ["CL","BO","PY","BR","UY"],
    "UY": ["AR","BR"],
    "MA": ["ES","DZ","EH","MR"],
    "EH": ["MA","DZ","MR"],
    "DZ": ["MA","EH","TN","LY","NE","ML","MR"],
    "TN": ["DZ","LY"],
    "LY": ["TN","DZ","NE","TD","SD","EG"],
    "EG": ["LY","SD","IL","PS"],
    "MR": ["MA","EH","DZ","ML","SN","GM"],
    "ML": ["MR","DZ","NE","BF","CI","GN","SN"],
    "SN": ["MR","ML","GM","GW","GN"],
    "GM": ["SN","MR"],
    "GW": ["SN","GN"],
    "GN": ["SN","GW","ML","CI","SL","LR"],
    "SL": ["GN","LR"],
    "LR": ["GN","SL","CI"],
    "CI": ["LR","GN","ML","BF","GH"],
    "BF": ["ML","NE","GH","TG","BJ","CI"],
    "GH": ["CI","BF","TG"],
    "TG": ["GH","BF","BJ","NG"],
    "BJ": ["TG","BF","NG","NE"],
    "NG": ["BJ","NE","TD","CM"],
    "NE": ["DZ","LY","TD","NG","BJ","BF","ML"],
    "TD": ["LY","NE","NG","CM","CF","SD","SS","ER"],
    "CM": ["NG","TD","CF","CG","GA","GQ"],
    "CF": ["TD","CM","CG","CD","SS","SD"],
    "SD": ["LY","EG","ET","ER","SS","CF","TD"],
    "SS": ["SD","ET","UG","CD","CF","TD"],
    "ER": ["SD","ET","DJ"],
    "ET": ["ER","SD","SS","UG","KE","SO","DJ"],
    "DJ": ["ER","ET","SO"],
    "SO": ["ET","DJ","KE"],
    "GA": ["CM","CG","GQ"],
    "GQ": ["CM","GA"],
    "CG": ["CM","CF","CD","GA","AO"],
    "CD": ["CF","SS","UG","RW","BI","TZ","ZM","AO","CG","CM"],
    "AO": ["CG","CD","ZM","NA"],
    "UG": ["SS","ET","KE","RW","TZ","CD"],
    "KE": ["ET","SO","UG","TZ"],
    "RW": ["UG","TZ","CD","BI"],
    "BI": ["RW","TZ","CD"],
    "TZ": ["UG","KE","RW","BI","CD","ZM","MZ","MW"],
    "ZM": ["AO","CD","TZ","MW","MZ","ZW","BW","NA"],
    "MW": ["TZ","ZM","MZ"],
    "MZ": ["TZ","ZM","MW","ZW","ZA","SZ"],
    "ZW": ["ZM","MZ","BW","ZA"],
    "NA": ["AO","ZM","BW","ZA"],
    "BW": ["NA","ZM","ZW","ZA"],
    "ZA": ["NA","BW","ZW","MZ","SZ","LS"],
    "SZ": ["ZA","MZ"],
    "LS": ["ZA"],
    "MG": [],
    "LK": [],
    "CY": [],
    "IS": [],
    "IE": ["UK"],
    "UK": ["IE"],
}


def is_neighbor(iso_a: str, iso_b: str) -> bool:
    a, b = iso_a.upper(), iso_b.upper()
    return b in NEIGHBORS.get(a, []) or a in NEIGHBORS.get(b, [])



# ── Рельеф территорий ──────────────────────────────────────────
TERRAIN_DATA = {
    # Горы — сильный бонус защитнику
    "AF":"горы","AM":"горы","GE":"горы","KG":"горы","TJ":"горы","NP":"горы",
    "BT":"горы","CH":"горы","AT":"горы","NO":"горы","IS":"горы","AL":"горы",
    "BA":"горы","ME":"горы","MK":"горы","XK":"горы","AZ":"горы","PG":"горы",
    "PE":"горы","BO":"горы","EC":"горы","CL":"горы","ET":"горы","ER":"горы",
    # Леса — умеренный бонус защитнику
    "RU":"лес","CA":"лес","SE":"лес","FI":"лес","BY":"лес","UA":"лес",
    "PL":"лес","CZ":"лес","SK":"лес","RO":"лес","LT":"лес","LV":"лес",
    "EE":"лес","CN":"лес","BR":"лес","CO":"лес","VE":"лес",
    "CD":"лес","CM":"лес","GA":"лес","CG":"лес","ID":"лес","MY":"лес",
    "MM":"лес","TH":"лес","LA":"лес","VN":"лес","KH":"лес",
    # Пустыня — лёгкий штраф защитнику (открытое пространство)
    "SA":"пустыня","AE":"пустыня","OM":"пустыня","YE":"пустыня","LY":"пустыня",
    "DZ":"пустыня","EG":"пустыня","SD":"пустыня","ML":"пустыня","NE":"пустыня",
    "MR":"пустыня","TD":"пустыня","KZ":"пустыня","TM":"пустыня","UZ":"пустыня",
    "AU":"пустыня","MN":"пустыня",
    # Равнины — нейтральный (по умолчанию)
}

TERRAIN_EMOJI  = {"горы": "⛰️", "лес": "🌲", "пустыня": "🏜️", "равнина": "🌾"}
TERRAIN_DEF_BONUS = {"горы": 0.40, "лес": 0.20, "пустыня": -0.10, "равнина": 0.0}

def get_terrain(iso: str) -> str:
    return TERRAIN_DATA.get(iso, "равнина")


async def run_battle_rounds(
    bot,
    chat_id: int,
    message_id: int,         # сообщение в которое шлём раунды
    attacker: dict,
    defender: dict | None,
    target_iso: str,
    attack_army: int,
    def_army: int,
    fortress_bonus: float,
    attack_mode: str,
    atk_bonus: float,
    def_bonus: float = 1.0,
) -> tuple:
    """
    Пошаговый бой. Возвращает (won, total_losses_att, total_losses_def).
    Шлёт в чат отчёт по каждому раунду.
    """
    import asyncio

    MAX_ROUNDS = 5
    ter_name   = COUNTRY_NAMES.get(target_iso, target_iso)
    terrain    = get_terrain(target_iso)
    ter_emoji  = TERRAIN_EMOJI[terrain]
    ter_def    = TERRAIN_DEF_BONUS[terrain]

    mode_emoji = {"ground": "⚔️", "air": "✈️", "naval": "⚓"}[attack_mode]
    def_name   = f"{defender['emoji']} {defender['name']}" if defender else "гарнизон"

    # Начальные значения
    att_hp    = attack_army
    def_hp    = def_army
    att_moral = 100
    def_moral = 100
    total_att_losses = 0
    total_def_losses = 0

    # Эффективная защита с учётом рельефа и крепости
    terrain_mult = 1.0 + ter_def
    fort_mult    = fortress_bonus

    round_num = 0
    won = False

    for round_num in range(1, MAX_ROUNDS + 1):
        if att_hp <= 0 or def_hp <= 0:
            break

        # Модификатор морали (0.5–1.2)
        att_moral_mod = max(0.5, att_moral / 100) * 1.2
        def_moral_mod = max(0.5, def_moral / 100) * 1.2

        # Урон атакующего по защитнику
        att_power  = att_hp * atk_bonus * att_moral_mod * random.uniform(0.8, 1.2)
        def_power  = def_hp * terrain_mult * fort_mult * max(0.25, def_bonus) * def_moral_mod * random.uniform(0.8, 1.2)

        # Потери за раунд (% от текущего состава)
        base_loss_rate = 0.12
        def_losses_r = max(1, int(def_hp * base_loss_rate * (att_power / max(def_power, 1)) * random.uniform(0.8, 1.2)))
        att_losses_r = max(1, int(att_hp * base_loss_rate * (def_power / max(att_power, 1)) * random.uniform(0.8, 1.2)))

        def_losses_r = min(def_losses_r, def_hp)
        att_losses_r = min(att_losses_r, att_hp)

        def_hp -= def_losses_r
        att_hp -= att_losses_r
        total_att_losses += att_losses_r
        total_def_losses += def_losses_r

        # Мораль падает при потерях (чем больше % потерь, тем сильнее падение)
        att_moral_drop = int((att_losses_r / max(attack_army, 1)) * 200)
        def_moral_drop = int((def_losses_r / max(def_army,    1)) * 200)
        att_moral = max(10, att_moral - att_moral_drop)
        def_moral = max(10, def_moral - def_moral_drop)

        # Победа в раунде
        att_winning = att_power > def_power

        # Линия морали в ASCII
        def moral_bar(m):
            filled = max(0, min(10, round(m / 10)))
            return "█" * filled + "░" * (10 - filled)

        round_text = (
            f"{mode_emoji} <b>Раунд {round_num}/{MAX_ROUNDS} — {ter_emoji} {ter_name}</b>\n"
            f"{'─' * 28}\n"
            f"{attacker['emoji']} <b>{attacker['name']}</b>\n"
            f"  🪖 Войска: {att_hp}  💀 Потери: -{att_losses_r}\n"
            f"  🔥 Мораль: [{moral_bar(att_moral)}] {att_moral}%\n\n"
            f"🛡 {def_name}\n"
            f"  🪖 Войска: {max(0, def_hp)}  💀 Потери: -{def_losses_r}\n"
            f"  🔥 Мораль: [{moral_bar(def_moral)}] {def_moral}%\n"
        )

        # Бонусы в первом раунде
        if round_num == 1:
            bonuses = []
            if ter_def > 0:
                bonuses.append(f"{ter_emoji} {terrain} (+{int(ter_def*100)}% защита)")
            if fortress_bonus > 1.0:
                bonuses.append(f"🏰 Крепость (+{int((fortress_bonus-1)*100)}% защита)")
            if bonuses:
                round_text += f"\n📋 Бонусы: {', '.join(bonuses)}"

        round_text += f"\n\n{'⚔️ Атакующие давят!' if att_winning else '🛡 Защита держится!'}"

        # Проверка на досрочный конец
        if def_hp <= 0:
            won = True
            round_text += "\n\n💥 <b>Оборона сломлена!</b>"
            await _safe_send(bot, chat_id, round_text, parse_mode="HTML")
            break
        if att_hp <= 0:
            won = False
            round_text += "\n\n💀 <b>Атака захлебнулась!</b>"
            await _safe_send(bot, chat_id, round_text, parse_mode="HTML")
            break

        await _safe_send(bot, chat_id, round_text, parse_mode="HTML")

        # Пауза между раундами
        if round_num < MAX_ROUNDS:
            await asyncio.sleep(2)
    else:
        # Все раунды сыграны — победитель по оставшимся войскам
        won = att_hp > def_hp

    return won, total_att_losses, total_def_losses


# ═══════════════════════════════════════════════════════════════
#  CANCEL HELPER
# ═══════════════════════════════════════════════════════════════

try:
    from pydantic import ConfigDict as _PydanticConfigDict

    class _ColoredButton(InlineKeyboardButton):
        """InlineKeyboardButton с поддержкой color (Bot API 9.0+)."""
        model_config = _PydanticConfigDict(extra="allow", populate_by_name=True)

    def colored_button(text: str, color: str, *,
                       callback_data: str = "", url: str = "") -> InlineKeyboardButton:
        """
        color: 'default' | 'red' | 'orange' | 'green'  (Bot API 9.0+)
        """
        kwargs: dict = {"text": text}
        if callback_data:
            kwargs["callback_data"] = callback_data
        if url:
            kwargs["url"] = url
        try:
            btn = _ColoredButton(**kwargs)
            # Устанавливаем color в pydantic extra — попадёт в model_dump/JSON
            object.__setattr__(btn, "__pydantic_extra__", {"color": color})
            return btn
        except Exception:
            # Fallback: обычная кнопка без цвета
            return InlineKeyboardButton(text=text, **{k: v for k, v in kwargs.items() if k != "text"})

except ImportError:
    def colored_button(text: str, color: str, *,
                       callback_data: str = "", url: str = "") -> InlineKeyboardButton:
        kwargs: dict = {}
        if callback_data:
            kwargs["callback_data"] = callback_data
        if url:
            kwargs["url"] = url
        return InlineKeyboardButton(text=text, **kwargs)


def _cancel_kb(user_id: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        colored_button("❌ Отмена", "red", callback_data=f"fsm_cancel:{user_id}|{user_id}")
    ]])


# ═══════════════════════════════════════════════════════════════
#  HANDLERS — CREATE
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
#  MIDDLEWARE — запрет чужих инлайн кнопок
# ═══════════════════════════════════════════════════════════════

try:
    from aiogram.types import InaccessibleMessage as _InaccessibleMessage
except ImportError:      # старые версии aiogram 3.x
    _InaccessibleMessage = None


def _is_inaccessible(msg) -> bool:
    """True, если сообщение под кнопкой недоступно боту.

    Telegram отдаёт InaccessibleMessage вместо Message, когда исходное сообщение
    старше 48 часов или удалено. У него нет ни текста, ни возможности
    edit_text — любая попытка обратиться к нему роняет handler.
    """
    if _InaccessibleMessage is not None and isinstance(msg, _InaccessibleMessage):
        return True
    # Страховка для версий без отдельного класса: у InaccessibleMessage date == 0
    if not getattr(msg, "date", None):
        return True
    return getattr(msg, "chat", None) is None


def _cb_owner_id(cb_data: str) -> Optional[int]:
    """Достаёт owner_user_id из хвоста callback_data ('...|12345').

    Возвращает None, если тега нет или он не число. Вызывающий код обязан
    трактовать None как отказ, а не как «проверка не нужна».
    """
    if "|" not in (cb_data or ""):
        return None
    tail = cb_data.rsplit("|", 1)[1].strip()
    if not tail.lstrip("-").isdigit():
        return None
    try:
        return int(tail)
    except ValueError:
        return None


_OWNER_BOUND_PREFIXES = (
    "action:", "confirm_delete:", "cancel_delete:",
    "color:", "fsm_cancel:", "build:", "build_page:", "buildmenu:", "buildmenu_page:",
)

class UsernameUpdateMiddleware(BaseMiddleware):
    """Обновляет owner_username в БД при каждом сообщении игрока."""
    async def __call__(self, handler, event, data):
        if isinstance(event, Message) and event.from_user and event.chat.type != "private":
            uname = event.from_user.username or ""
            uid   = event.from_user.id
            try:
                _mw_db = await _open_db()
                async with _mw_db:
                    await _mw_db.execute(
                        "UPDATE countries SET owner_username=? WHERE chat_id=? AND owner_id=?",
                        (uname, event.chat.id, uid)
                    )
                    await _mw_db.commit()
            except Exception:
                pass
        return await handler(event, data)


class OwnerOnlyCallbackMiddleware(BaseMiddleware):
    """Кнопки owner-bound могут нажимать только те, кому они адресованы.
    user_id владельца вшивается в конец callback_data через '|'.

    SECURITY FIX: раньше проверка была fail-open — если в callback_data не было
    '|' или owner_id не парсился, middleware молча пропускал нажатие дальше.
    Любая кнопка, выпущенная старой версией бота (или собранная без owner-тега),
    становилась доступна любому участнику чата. Теперь неизвестный владелец =
    отказ.
    """
    async def __call__(self, handler, event, data):
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)
        if event.from_user is None:
            return
        cb_data = event.data or ""
        if not any(cb_data.startswith(p) for p in _OWNER_BOUND_PREFIXES):
            return await handler(event, data)
        owner_id = _cb_owner_id(cb_data)
        if owner_id is None:
            await event.answer(
                "❌ Кнопка устарела — вызовите команду заново.", show_alert=True
            )
            return
        if event.from_user.id != owner_id:
            await event.answer("❌ Это не ваша кнопка!", show_alert=True)
            return
        return await handler(event, data)


class SafeContextMiddleware(BaseMiddleware):
    """Отбрасывает события, на которых handler'ы гарантированно упадут.

    BUG FIX: в коде ~90 обращений к message.from_user.id и ~35 к
    callback.message.chat.id без проверок. Оба поля опциональны:
      • from_user отсутствует у постов от имени канала и у сообщений
        анонимного администратора — любая команда роняла handler;
      • callback.message становится недоступным (InaccessibleMessage) для
        сообщений старше 48 часов, а в этой игре кнопки войны/мира/торговли
        живут в чате долго — нажатие на старую кнопку роняло handler.
    Одна централизованная проверка вместо 120 точечных.
    """
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            if event.from_user is None:
                return
        elif isinstance(event, CallbackQuery):
            if event.from_user is None:
                return
            msg = event.message
            if msg is None or _is_inaccessible(msg):
                try:
                    await event.answer(
                        "❌ Сообщение слишком старое — вызовите команду заново.",
                        show_alert=True
                    )
                except Exception:
                    pass
                return
        return await handler(event, data)


# ═══════════════════════════════════════════════════════════════
#  СИСТЕМА ОТПРАВКИ ВОЙСК
# ═══════════════════════════════════════════════════════════════

UNIT_TRAVEL_MINUTES = {
    "infantry":  3,
    "cavalry":   2,
    "artillery": 5,
    "airforce":  1,
    "bomber":    2,
    "navy":      4,
    "armored":   3,
    "tank":      4,
}

UNIT_LABELS = {
    "infantry":  "🪖 Пехота",
    "cavalry":   "🐴 Кавалерия",
    "artillery": "💣 Артиллерия",
    "airforce":  "✈️ Авиация",
    "bomber":    "💥 Бомбардировщики",
    "navy":      "⚓ Флот",
    "armored":   "🚙 Бронетехника",
    "tank":      "🛡 Танки",
}

# Сила одного юнита (для штурмов) и минимум силы для захвата чужой/нейтральной земли.
UNIT_POWER = {"infantry": 1.0, "cavalry": 1.5, "artillery": 20.0, "airforce": 3.0,
              "bomber": 5.0, "navy": 10.0, "armored": 8.0, "tank": 15.0}
MIN_ASSAULT_POWER = 10


async def _refund_units(db, country_id: int, unit_type: str, count: int) -> None:
    if count > 0:
        await db.execute(
            f"UPDATE military SET {unit_type}={unit_type}+? WHERE country_id=?",
            (count, country_id),
        )


async def send_deployment(chat_id: int, attacker_id: int, target_iso: str,
                          unit_type: str, unit_count: int) -> tuple:
    travel_min = UNIT_TRAVEL_MINUTES.get(unit_type, 60)
    now = int(time.time())

    # Бонус железной дороги: -20% времени за каждый уровень (макс -80%)
    _rdb = await _open_db()
    async with _rdb:
        _rc = await _rdb.execute(
            "SELECT COALESCE(SUM(level),0) as total_rr FROM buildings "
            "WHERE chat_id=? AND country_id=? AND type='railroad'",
            (chat_id, attacker_id)
        )
        _rr = await _rc.fetchone()
        rr_levels = _rr["total_rr"] if _rr else 0
        # Проверяем что цель — своя территория
        _tc = await _rdb.execute(
            "SELECT country_id FROM territories WHERE chat_id=? AND iso=?",
            (chat_id, target_iso.upper())
        )
        _tr = await _tc.fetchone()
        is_own = _tr and _tr["country_id"] == attacker_id
        target_owner = _tr["country_id"] if _tr else None

    # ── БАЛАНС: десант без границ запрещён ──────────────────────────────
    if target_owner is None:
        return False, (
            "Нейтральные земли захватываются командой /expand (у неё есть цена и кулдаун). "
            "Десант разрешён только на свои или вражеские территории."
        )
    if target_owner != attacker_id:
        own_territories = await get_country_territories(chat_id, attacker_id)
        if not any(is_neighbor(t, target_iso) for t in own_territories):
            return False, (
                f"{target_iso} не граничит с вашими землями. "
                "Десант возможен только на соседние территории."
            )
        if not await get_active_war(chat_id, attacker_id, target_owner):
            return False, f"Сначала объявите войну: /war {target_iso}."
        if unit_count * UNIT_POWER.get(unit_type, 1.0) < MIN_ASSAULT_POWER:
            return False, (
                f"Слишком мало войск для штурма: нужно ≥ {MIN_ASSAULT_POWER} силы "
                f"(например, {MIN_ASSAULT_POWER} пехоты)."
            )

    if is_own and rr_levels > 0:
        discount = min(0.8, rr_levels * 0.2)  # -20% за уровень, макс -80%
        travel_min = max(1, int(travel_min * (1 - discount)))

    arrive_at = now + travel_min * 60
    db = await _open_db()
    async with db:
        mil_c = await db.execute(
            f"SELECT {unit_type} FROM military WHERE country_id=?", (attacker_id,)
        )
        mil = await mil_c.fetchone()
        if not mil:
            return False, "Нет данных о войсках"
        available = mil[unit_type]
        if available < unit_count:
            return False, f"Недостаточно {UNIT_LABELS[unit_type]}: есть {available}, нужно {unit_count}"
        await db.execute(
            f"UPDATE military SET {unit_type}={unit_type}-? WHERE country_id=?",
            (unit_count, attacker_id)
        )
        await db.execute(
            """INSERT INTO deployments (chat_id, attacker_id, target_iso, unit_type, unit_count, depart_at, arrive_at)
               VALUES (?,?,?,?,?,?,?)""",
            (chat_id, attacker_id, target_iso, unit_type, unit_count, now, arrive_at)
        )
        await db.commit()
    from datetime import datetime
    arrive_str = datetime.fromtimestamp(arrive_at).strftime("%H:%M:%S")
    return True, f"Прибудет к {target_iso} в {arrive_str}"


async def resolve_deployments(chat_id: int, bot: Bot):
    now = int(time.time())
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM deployments WHERE chat_id=? AND resolved=0 AND arrive_at<=?",
            (chat_id, now)
        )
        arrived = await c.fetchall()
        if not arrived:
            return
        for dep in arrived:
            dep_id      = dep["id"]
            attacker_id = dep["attacker_id"]
            target_iso  = dep["target_iso"]
            unit_type   = dep["unit_type"]
            unit_count  = dep["unit_count"]
            await db.execute("UPDATE deployments SET resolved=1 WHERE id=?", (dep_id,))
            c2 = await db.execute("SELECT * FROM countries WHERE id=?", (attacker_id,))
            attacker = await c2.fetchone()
            if not attacker:
                continue
            c3 = await db.execute(
                "SELECT country_id FROM territories WHERE chat_id=? AND iso=?",
                (chat_id, target_iso)
            )
            ter_row = await c3.fetchone()
            owner_cid = ter_row["country_id"] if ter_row else None
            if owner_cid == attacker_id:
                # Войска прибыли на свою территорию — помещаем в гарнизон
                await db.execute(
                    f"""INSERT INTO garrisons (chat_id, country_id, iso, {unit_type}, updated_at)
                        VALUES (?,?,?,?,strftime('%s','now'))
                        ON CONFLICT(chat_id, country_id, iso)
                        DO UPDATE SET {unit_type}={unit_type}+?, updated_at=strftime('%s','now')""",
                    (chat_id, attacker_id, target_iso, unit_count, unit_count)
                )
                ter_name_own = COUNTRY_NAMES.get(target_iso, target_iso)
                msg = (f"✅ {attacker['emoji']} <b>{attacker['name']}</b>: "
                       f"{UNIT_LABELS[unit_type]} ({unit_count} ед.) <b>прибыли</b> в <b>{ter_name_own}</b> и встали в гарнизон!")
                try:
                    await _safe_send(bot, chat_id, msg, parse_mode="HTML")
                except Exception:
                    pass
                msg = None
            elif owner_cid is None:
                _power = unit_count * UNIT_POWER.get(unit_type, 1.0)
                if _power < MIN_ASSAULT_POWER:
                    await _refund_units(db, attacker_id, unit_type, unit_count)
                    await db.commit()
                    try:
                        await _safe_send(bot, chat_id,
                            f"⚠️ {attacker['emoji']} <b>{attacker['name']}</b>: слишком мало войск "
                            f"для захвата <b>{COUNTRY_NAMES.get(target_iso, target_iso)}</b> — "
                            f"войска возвращены. Захват нейтральных земель — через /expand.", parse_mode="HTML")
                    except Exception:
                        pass
                    continue
                await db.execute(
                    "INSERT OR REPLACE INTO territories (chat_id, iso, country_id) VALUES (?,?,?)",
                    (chat_id, target_iso, attacker_id)
                )
                await db.execute(
                    f"UPDATE military SET {unit_type}={unit_type}+? WHERE country_id=?",
                    (unit_count, attacker_id)
                )
                ter_name = COUNTRY_NAMES.get(target_iso, target_iso)
                msg = (f"🏴 {attacker['emoji']} <b>{attacker['name']}</b>: "
                       f"{UNIT_LABELS[unit_type]} захватили нейтральную <b>{ter_name}</b>!")
                try:
                    await _safe_send(bot, chat_id, msg, parse_mode="HTML")
                except Exception:
                    pass
                msg = None
            else:
                _war_ok = await get_active_war(chat_id, attacker_id, owner_cid)
                _own_terr = await get_country_territories(chat_id, attacker_id)
                _adjacent = any(is_neighbor(t, target_iso) for t in _own_terr)
                if not _war_ok or not _adjacent:
                    await _refund_units(db, attacker_id, unit_type, unit_count)
                    await db.commit()
                    reason = "война не объявлена" if not _war_ok else "территория не граничит с вашими землями"
                    try:
                        await _safe_send(bot, chat_id,
                            f"⚠️ {attacker['emoji']} <b>{attacker['name']}</b>: десант на "
                            f"<b>{COUNTRY_NAMES.get(target_iso, target_iso)}</b> отменён ({reason}) — "
                            f"войска возвращены.", parse_mode="HTML")
                    except Exception:
                        pass
                    continue
                c4 = await db.execute("SELECT * FROM countries WHERE id=?", (owner_cid,))
                defender = await c4.fetchone()
                # Сила защитника = ТОЛЬКО гарнизон на этой территории
                gar_c = await db.execute(
                    "SELECT infantry,cavalry,artillery,airforce,bomber,navy,armored,tank FROM garrisons "
                    "WHERE chat_id=? AND country_id=? AND iso=?",
                    (chat_id, owner_cid, target_iso)
                )
                gar_row = await gar_c.fetchone()
                unit_power_d = {"infantry": 1.0, "cavalry": 1.5, "artillery": 20.0, "airforce": 3.0, "bomber": 5.0, "navy": 10.0, "armored": 8.0, "tank": 15.0}
                if gar_row:
                    def_strength = sum(gar_row[u] * unit_power_d.get(u, 1.0) for u in UNIT_TYPES)
                else:
                    def_strength = 0
                ter_name = COUNTRY_NAMES.get(target_iso, target_iso)
                def_name = f"{defender['emoji']} {defender['name']}" if defender else "???"
                def_mention = (
                    make_owner_mention(dict(defender)) if defender else ""
                )

                # BUG FIX: если у жертвы 0 армии — автозахват без боя
                if def_strength == 0 and unit_count * UNIT_POWER.get(unit_type, 1.0) < MIN_ASSAULT_POWER:
                    await _refund_units(db, attacker_id, unit_type, unit_count)
                    await db.commit()
                    try:
                        await _safe_send(bot, chat_id,
                            f"⚠️ {attacker['emoji']} <b>{attacker['name']}</b>: слишком мало войск для "
                            f"захвата <b>{ter_name}</b> — войска возвращены (нужно ≥ {MIN_ASSAULT_POWER} силы).",
                            parse_mode="HTML")
                    except Exception:
                        pass
                    continue
                if def_strength == 0:
                    await db.execute(
                        "INSERT OR REPLACE INTO territories (chat_id, iso, country_id) VALUES (?,?,?)",
                        (chat_id, target_iso, attacker_id)
                    )
                    # Проверяем столицу
                    _cap_c = await db.execute("SELECT capital_iso FROM countries WHERE id=?", (owner_cid,))
                    _cap_row = await _cap_c.fetchone()
                    _is_cap = _cap_row and (_cap_row["capital_iso"] or "").upper() == target_iso
                    if _is_cap:
                        await db.execute(
                            "UPDATE buildings SET country_id=? WHERE chat_id=? AND country_id=?",
                            (attacker_id, chat_id, owner_cid)
                        )
                        capital_note = "\n🏯 <b>Столица захвачена!</b> Все здания перешли победителю."
                    else:
                        await db.execute(
                            "UPDATE buildings SET country_id=? WHERE chat_id=? AND iso=?",
                            (attacker_id, chat_id, target_iso)
                        )
                        capital_note = ""
                    survived = max(0, unit_count)
                    if survived > 0:
                        await db.execute(
                            f"UPDATE military SET {unit_type}={unit_type}+? WHERE country_id=?",
                            (survived, attacker_id)
                        )
                    # +1 очко в войне
                    _war = await get_active_war(chat_id, attacker_id, owner_cid)
                    if _war:
                        _sc_col = "attacker_score" if _war["attacker_id"] == attacker_id else "defender_score"
                        _sdb = await _open_db()
                        async with _sdb:
                            await _sdb.execute(
                                f"UPDATE wars SET {_sc_col}={_sc_col}+1 WHERE id=?",
                                (_war["id"],)
                            )
                            await _sdb.commit()
                    msg = (
                        f"🏴 <b>АВТОЗАХВАТ!</b>\n\n"
                        f"{attacker['emoji']} <b>{attacker['name']}</b> захватил <b>{ter_name}</b> без боя!\n"
                        f"🛡 {def_name} {def_mention} — армии не было.\n"
                        f"🏗 Здания перешли победителю.{capital_note}"
                    )
                    await db.commit()
                    try:
                        await _safe_send(bot, chat_id, msg, parse_mode="HTML")
                    except Exception:
                        pass
                    msg = None
                    # Проверяем — не осталась ли проигравшая страна без территорий
                    await auto_delete_if_no_territories(chat_id, owner_cid, bot=bot)
                else:
                    unit_power = {"infantry": 1.0, "cavalry": 1.5, "artillery": 20.0, "airforce": 3.0, "bomber": 5.0, "navy": 10.0, "armored": 8.0, "tank": 15.0}
                    atk_strength = int(unit_count * unit_power.get(unit_type,1.0) * random.uniform(0.8,1.2))
                    def_roll     = int(def_strength * random.uniform(0.7,1.3))
                    losses_atk   = max(1, int(unit_count * random.uniform(0.1,0.35)))
                    losses_def   = max(1, int(def_strength * random.uniform(0.05,0.2)))
                    if atk_strength > def_roll:
                        await db.execute(
                            "INSERT OR REPLACE INTO territories (chat_id, iso, country_id) VALUES (?,?,?)",
                            (chat_id, target_iso, attacker_id)
                        )
                        # Переносим здания победителю
                        await db.execute(
                            "UPDATE buildings SET country_id=? WHERE chat_id=? AND iso=?",
                            (attacker_id, chat_id, target_iso)
                        )
                        survived = max(0, unit_count - losses_atk)
                        if survived > 0:
                            await db.execute(
                                f"UPDATE military SET {unit_type}={unit_type}+? WHERE country_id=?",
                                (survived, attacker_id)
                            )
                        # Потери защитника — снимаем из гарнизона пропорционально
                        gar_total_u = 0
                        gar_row2 = None
                        if gar_row:
                            gar_total_u = sum(gar_row[u] for u in UNIT_TYPES)
                        if gar_total_u > 0:
                            gar_loss = min(losses_def, gar_total_u)
                            for u in UNIT_TYPES:
                                u_cnt = gar_row[u] if gar_row else 0
                                if u_cnt > 0:
                                    u_loss = min(u_cnt, int(gar_loss * u_cnt / gar_total_u))
                                    if u_loss > 0:
                                        await db.execute(
                                            f"UPDATE garrisons SET {u}=MAX(0,{u}-?) WHERE chat_id=? AND country_id=? AND iso=?",
                                            (u_loss, chat_id, owner_cid, target_iso)
                                        )
                        # +1 очко атакующей ст��роне в активной войне
                        _war = await get_active_war(chat_id, attacker_id, owner_cid)
                        if _war:
                            _sc_col = "attacker_score" if _war["attacker_id"] == attacker_id else "defender_score"
                            _sdb = await _open_db()
                            async with _sdb:
                                await _sdb.execute(
                                    f"UPDATE wars SET {_sc_col}={_sc_col}+1 WHERE id=?",
                                    (_war["id"],)
                                )
                                await _sdb.commit()
                        msg = (
                            f"⚔️ <b>БОЙ завершён!</b>\n\n"
                            f"{attacker['emoji']} <b>{attacker['name']}</b> vs {def_name} {def_mention}\n"
                            f"🏆 <b>ПОБЕДА</b> — захвачена <b>{ter_name}</b>!\n"
                            f"🏗 Здания на территории перешли победителю.\n\n"
                            f"📊 Потери атакующего: {losses_atk} {UNIT_LABELS[unit_type]}\n"
                            f"📊 Потери защитника: ~{losses_def} ед.\n"
                            f"🔙 Уцелело: {survived} {UNIT_LABELS[unit_type]}"
                        )
                        await db.commit()
                        try:
                            await _safe_send(bot, chat_id, msg, parse_mode="HTML")
                        except Exception:
                            pass
                        msg = None
                        # Уведомление защитнику
                        if defender:
                            try:
                                def_ping = make_owner_mention(dict(defender))
                                await _safe_send(
                                    bot, chat_id,
                                    f"🚨 {def_ping} — <b>{ter_name}</b> захвачена войсками "
                                    f"{attacker['emoji']} {attacker['name']} через отправку войск!",
                                    parse_mode="HTML"
                                )
                            except Exception:
                                pass
                        # Проверяем — не осталась ли проигравшая страна без территорий
                        await auto_delete_if_no_territories(chat_id, owner_cid, bot=bot)
                    else:
                        # При поражении теряем только losses_atk, не всё
                        await db.execute(
                            f"UPDATE military SET {unit_type}=MAX(0,{unit_type}-?) WHERE country_id=?",
                            (losses_atk, attacker_id)
                        )
                        # Потери защитника из гарнизона
                        if gar_row:
                            gar_total_u = sum(gar_row[u] for u in UNIT_TYPES)
                            if gar_total_u > 0:
                                gar_loss2 = min(losses_def // 2, gar_total_u)
                                for u in UNIT_TYPES:
                                    u_cnt = gar_row[u]
                                    if u_cnt > 0:
                                        u_loss2 = min(u_cnt, int(gar_loss2 * u_cnt / gar_total_u))
                                        if u_loss2 > 0:
                                            await db.execute(
                                                f"UPDATE garrisons SET {u}=MAX(0,{u}-?) WHERE chat_id=? AND country_id=? AND iso=?",
                                                (u_loss2, chat_id, owner_cid, target_iso)
                                            )
                        msg = (
                            f"⚔️ <b>БОЙ завершён!</b>\n\n"
                            f"{attacker['emoji']} <b>{attacker['name']}</b> vs {def_name} {def_mention}\n"
                            f"💀 <b>ПОРАЖЕНИЕ</b> — <b>{ter_name}</b> устояла!\n\n"
                            f"📊 Потери атакующего: {losses_atk} {UNIT_LABELS[unit_type]}\n"
                            f"📊 Потери защитника: ~{losses_def//2} ед."
                        )
            await db.commit()
            if msg:
                try:
                    await _safe_send(bot, chat_id, msg, parse_mode="HTML")
                except Exception:
                    pass


router_create = Router()


@router_create.message(CommandStart())
async def cmd_start_rpb(message: Message):
    if message.chat.type == "private":
        await message.answer(
            "🌍 <b>RP Country</b> — стратегия стран прямо в Telegram.\n\n"
            "Бот работает <b>в групповом чате</b>:\n"
            "1. Добавь меня в чат и дай право читать сообщения\n"
            "2. Отключи Group Privacy у @BotFather (<code>/setprivacy</code> → Disable)\n"
            "3. Напиши в чате <code>/create</code> — выберешь страну, флаг и цвет\n\n"
            "📖 Полный гайд: <code>/help</code>"
        )
    else:
        await message.answer(
            "🌍 <b>RP Country</b>\n\n"
            "🏛 <code>/create</code> — основать страну\n"
            "📖 <code>/help</code> — все команды\n"
            "🗺 <code>/map</code> · 🏆 <code>/top</code>"
        )

COLORS = {
    # Основные
    "🔴 Красный":      "#E74C3C",
    "🔵 Синий":        "#2980B9",
    "🟢 Зелёный":      "#27AE60",
    "🟡 Жёлтый":       "#F1C40F",
    "🟠 Оранжевый":    "#E67E22",
    "🟣 Фиолетовый":   "#8E44AD",
    "⚫ Чёрный":       "#2C3E50",
    "⚪ Серый":        "#95A5A6",
    # Розовые / красные оттенки
    "🩷 Розовый":      "#E91E63",
    "❤️ Малиновый":    "#C0392B",
    "🌸 Светло-роз.":  "#F48FB1",
    "🍷 Бордовый":     "#7B1C2D",
    # Синие / голубые оттенки
    "🩵 Голубой":      "#00BCD4",
    "🌊 Морской":      "#0277BD",
    "💙 Тёмно-синий":  "#1A237E",
    "🧊 Льдистый":     "#B3E5FC",
    # Зелёные оттенки
    "🌿 Тёмно-зел.":   "#1A5E39",
    "🫒 Оливковый":    "#558B2F",
    "🌱 Лаймовый":     "#8BC34A",
    "🌲 Хвойный":      "#2E7D32",
    # Коричневые / земляные
    "🟤 Коричневый":   "#795548",
    "🪵 Тёмно-кор.":   "#4E342E",
    "🏜️ Песочный":     "#C8A96E",
    "🧱 Кирпичный":    "#BF360C",
    # Фиолетовые / индиго
    "💜 Сиреневый":    "#CE93D8",
    "🔮 Индиго":       "#3949AB",
    "🫐 Баклажан":     "#4A148C",
    # Нейтральные / металл
    "🥈 Серебряный":   "#B0BEC5",
    "🥇 Золотой":      "#FFD600",
    "🔩 Стальной":     "#546E7A",
    "🤍 Белый":        "#ECEFF1",
}


class CreateCountry(StatesGroup):
    waiting_iso   = State()
    waiting_name  = State()
    waiting_emoji = State()
    waiting_color = State()



# ── /cancel — глобальная отмена FSM ─────────────────────────

@router_create.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current = await state.get_state()
    if current is None:
        await message.answer("Нечего отменять.")
        return
    await state.clear()
    await message.answer("✅ Действие отменено.")


@router_create.callback_query(F.data.startswith("fsm_cancel:"))
async def cb_fsm_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("✅ Действие отменено.")
    await callback.answer()


# ── /create ──────────────────────────────────────────────────

@router_create.message(Command("create"))
async def cmd_create(message: Message, state: FSMContext):
    if message.chat.type == "private":
        await message.answer("❌ Команда работает только в групповых чатах!")
        return
    existing = await get_country_by_owner(message.chat.id, message.from_user.id)
    if existing:
        await message.answer(
            f"❌ У вас уже есть страна: {existing['emoji']} <b>{existing['name']}</b>\n"
            "Используйте /mycountry для просмотра.", parse_mode="HTML"
        )
        return
    cd = await get_cooldown(message.from_user.id, message.chat.id, "create")
    if cd > 0:
        await message.answer(f"⏳ Создание страны доступно через <b>{fmt_duration(cd)}</b>.", parse_mode="HTML")
        return
    await message.answer(
        "🌍 <b>Создание страны</b>\n\n"
        "Шаг 1/4 — Введите <b>ISO-код</b> территории\n\n"
        "Примеры: <code>RU</code> — Россия, <code>US</code> — США, "
        "<code>CN</code> — Китай\n\n"
        "Полный список: /isolist\n"
        "Отмена: /cancel",
        parse_mode="HTML",
        reply_markup=_cancel_kb(message.from_user.id)
    )
    await state.set_state(CreateCountry.waiting_iso)


@router_create.message(CreateCountry.waiting_iso)
async def process_iso(message: Message, state: FSMContext):
    iso = message.text.strip().upper()
    if not is_valid_iso(iso):
        await message.answer(
            f"❌ Неизвестный ISO: <code>{iso}</code>\n"
            "Используйте /isolist или /cancel для отмены.",
            parse_mode="HTML", reply_markup=_cancel_kb(message.from_user.id)
        )
        return
    existing = await get_country_by_iso(message.chat.id, iso)
    if existing:
        await message.answer(
            f"❌ Территория <b>{COUNTRY_NAMES.get(iso, iso)}</b> уже занята "
            f"страной {existing['emoji']} <b>{existing['name']}</b>!\n"
            "Введите другой ISO или /cancel.",
            parse_mode="HTML", reply_markup=_cancel_kb(message.from_user.id)
        )
        return
    # BUG FIX: проверяем также захваченные территории (/expand)
    territories = await get_territories(message.chat.id)
    if iso in territories:
        owner = await get_country_by_id(message.chat.id, territories[iso])
        owner_str = f"{owner['emoji']} <b>{owner['name']}</b>" if owner else "другой страной"
        await message.answer(
            f"❌ Территория <b>{COUNTRY_NAMES.get(iso, iso)}</b> уже захвачена {owner_str}!\n"
            "Введите другой ISO или /cancel.",
            parse_mode="HTML", reply_markup=_cancel_kb(message.from_user.id)
        )
        return
    await state.update_data(iso=iso)
    await message.answer(
        f"✅ Территория: <b>{COUNTRY_NAMES.get(iso, iso)}</b>\n\n"
        "Шаг 2/4 — Введите <b>название</b> страны (2–30 символов):",
        parse_mode="HTML", reply_markup=_cancel_kb(message.from_user.id)
    )
    await state.set_state(CreateCountry.waiting_name)


@router_create.message(CreateCountry.waiting_name)
async def process_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not 2 <= len(name) <= 30:
        await message.answer(
            "❌ Название должно быть от 2 до 30 символов!",
            reply_markup=_cancel_kb(message.from_user.id)
        )
        return
    # SECURITY FIX: запрещаем HTML-символы — название вставляется в
    # сообщения с parse_mode=HTML, иначе возможна HTML-инъекция.
    if any(ch in name for ch in ("<", ">", "&")):
        await message.answer(
            "❌ Название не может содержать символы &lt; &gt; &amp;",
            parse_mode="HTML",
            reply_markup=_cancel_kb(message.from_user.id)
        )
        return
    await state.update_data(name=name)
    await message.answer(
        f"✅ Название: <b>{name}</b>\n\n"
        "Шаг 3/4 — Отправьте <b>эмодзи-флаг</b>:\n"
        "• Обычный эмодзи: 🦅 🐉 ⚔️ 🌙 ☀️ 🔱 👑\n"
        "• Премиум-эмодзи: просто от��равьте его (нужна подписка Telegram Premium)\n\n"
        "<i>Премиум-эмодзи отображается анимированным у всех игроков.</i>",
        parse_mode="HTML", reply_markup=_cancel_kb(message.from_user.id)
    )
    await state.set_state(CreateCountry.waiting_emoji)


def _extract_emoji_from_message(message: Message) -> tuple[str, str | None]:
    """
    Извлекает эмодзи из сообщения.
    Возвращает (display_str, custom_emoji_id).
    display_str — то что хранится в БД и выводится через <tg-emoji>.
    custom_emoji_id — ID премиум-эмодзи, или None если обычный.
    """
    if message.entities:
        for ent in message.entities:
            if ent.type == "custom_emoji" and ent.custom_emoji_id:
                # Вырезаем текстовый fallback из message.text
                text = message.text or message.caption or ""
                fallback = _html.escape(text[ent.offset: ent.offset + ent.length])
                # SECURITY FIX: emoji_id приходит от Telegram, но экранируем
                # на случай нестандартных значений; fallback экранируем всегда.
                emoji_id = _html.escape(str(ent.custom_emoji_id), quote=True)
                display = f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
                return display, ent.custom_emoji_id

    # Обычный эмодзи — просто берём текст.
    # SECURITY FIX: экранируем HTML — иначе строка вида <i></i> (7 символов)
    # проходила проверку длины и ломала/подменяла разметку сообщений.
    text = _html.escape((message.text or "").strip())
    return text, None


@router_create.message(CreateCountry.waiting_emoji)
async def process_emoji(message: Message, state: FSMContext):
    display, custom_id = _extract_emoji_from_message(message)

    # Валидация обычного эмодзи (премиум пропускаем без проверки длины)
    if custom_id is None:
        if not display or len(display) > 8:
            await message.answer(
                "❌ Введите один эмодзи!",
                reply_markup=_cancel_kb(message.from_user.id)
            )
            return

    await state.update_data(emoji=display)

    preview = display if custom_id is None else f"{display} (пре��иум)"

    buttons = []
    row = []
    for label, hex_val in COLORS.items():
        row.append(InlineKeyboardButton(text=label, callback_data=f"color:{hex_val}|{message.from_user.id}"))
        if len(row) == 2:
            buttons.append(row); row = []
    if row:
        buttons.append(row)
    buttons.append([colored_button("❌ Отмена", "red", callback_data=f"fsm_cancel:{message.from_user.id}|{message.from_user.id}")])

    await message.answer(
        f"✅ Флаг сохранён {'(премиум ✨)' if custom_id else ''}\n\n"
        f"Шаг 4/4 — Выберите <b>цвет</b> страны на карте:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await state.set_state(CreateCountry.waiting_color)


@router_create.callback_query(F.data.startswith("color:"), CreateCountry.waiting_color)
async def process_color(callback: CallbackQuery, state: FSMContext):
    # format: color:<hex>|<user_id>
    raw = callback.data.split(":", 1)[1]
    hex_color, expected_user = raw.split("|", 1)
    if str(callback.from_user.id) != expected_user:
        await callback.answer("Это не ваше создание страны.", show_alert=True)
        return
    data = await state.get_data()
    await state.clear()
    await callback.message.delete()

    country = await create_country(
        chat_id=callback.message.chat.id,
        owner_id=callback.from_user.id,
        name=data["name"], emoji=data["emoji"],
        color=hex_color, iso=data["iso"],
        owner_username=callback.from_user.username or ""
    )
    if not country:
        await callback.message.answer(
            "❌ Не удалось создать страну — территория или название уже заняты.\n"
            "Попробуйте другое название/ISO: /create"
        )
        return

    color_name = next((k for k, v in COLORS.items() if v == hex_color), hex_color)
    await callback.message.answer(
        f"🎉 <b>Страна основана!</b>\n\n"
        f"{country['emoji']} <b>{country['name']}</b>\n"
        f"🗺 Территория: {COUNTRY_NAMES.get(country['iso'], country['iso'])}\n"
        f"🎨 Цвет: {color_name}\n"
        f"{PE['coin']} Казна: {country['treasury']}💰\n"
        f"⚔️ Армия: {country['army']} солдат\n\n"
        "/map — карта мира | /mycountry — управление",
        parse_mode="HTML"
    )
    await callback.answer()


# ── /mycountry ───────────────────────────────────────────────

@router_create.message(Command("mycountry"))
async def cmd_mycountry(message: Message):
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны. Создайте командой /create")
        return

    territories = await get_country_territories(message.chat.id, country["id"])
    allies_ids  = await get_allies(message.chat.id, country["id"])
    ally_names  = []
    for aid in allies_ids:
        a = await get_country_by_id(message.chat.id, aid)
        if a:
            ally_names.append(f"{a['emoji']} {a['name']}")

    wars = await get_active_wars(message.chat.id)
    at_war = []
    for war in wars:
        if war["attacker_id"] == country["id"]:
            e = await get_country_by_id(message.chat.id, war["defender_id"])
        elif war["defender_id"] == country["id"]:
            e = await get_country_by_id(message.chat.id, war["attacker_id"])
        else:
            continue
        if e:
            at_war.append(f"{e['emoji']} {e['name']}")

    res = await get_resources(country["id"])
    happiness = res.get("happiness", 70) if res else 70
    real_pop  = len(territories) * POPULATION_PER_TERRITORY

    # Гарнизоны по территориям
    garrisons_list = await get_all_garrisons(message.chat.id, country["id"])
    gar_by_iso = {g["iso"]: g for g in garrisons_list}
    capital_iso = (country.get("capital_iso") or country["iso"]).upper()

    unit_power_mc = {"infantry": 1.0, "cavalry": 1.5, "artillery": 20.0, "airforce": 3.0, "bomber": 5.0, "navy": 10.0, "armored": 8.0, "tank": 15.0}
    gar_lines = []
    for iso in sorted(territories):
        g = gar_by_iso.get(iso)
        ter_name = COUNTRY_NAMES.get(iso, iso)
        is_cap = iso == capital_iso
        cap_mark = f" {PE['star']}" if is_cap else ""
        if g:
            total_units = sum(g.get(u, 0) for u in UNIT_TYPES)
            strength = sum(g.get(u, 0) * unit_power_mc.get(u, 1.0) for u in UNIT_TYPES)
            parts = []
            for u in UNIT_TYPES:
                cnt = g.get(u, 0)
                if cnt > 0:
                    parts.append(f"{UNIT_LABELS[u]}: {cnt}")
            unit_str = ", ".join(parts) if parts else "пусто"
            gar_lines.append(f"  🏴 <b>{ter_name}</b>{cap_mark} (<code>{iso}</code>) — {unit_str} [сила: {int(strength)}]")
        else:
            gar_lines.append(f"  {PE['warning']} <b>{ter_name}</b>{cap_mark} (<code>{iso}</code>) — <i>нет гарнизона!</i>")

    gar_block = "\n".join(gar_lines) if gar_lines else "  Нет территорий"

    text = (
        f"{country['emoji']} <b>{country['name']}</b>\n"
        f"{'─' * 25}\n"
        f"🏯 Столица: {COUNTRY_NAMES.get(capital_iso, capital_iso)} (<code>{capital_iso}</code>)\n"
        f"🗺 Стартовая территория: {COUNTRY_NAMES.get(country['iso'], country['iso'])}\n"
        f"🌍 Территорий: {len(territories)}  |  👥 {real_pop:,} чел.\n"
        f"{happiness_emoji(happiness)} Счастье: {happiness}/100 — {happiness_label(happiness)}\n"
        f"💰 Казна: {country['treasury']}💰\n"
        f"🗃 Пул (не размещены): {country['army']} солдат\n"
        f"{'─' * 25}\n"
        f"{PE['castle']} <b>Гарнизоны по территориям:</b>\n"
        f"{gar_block}\n"
        f"{'─' * 25}\n"
        f"📊 Налог: {country['tax_rate']}💰/территорию\n"
    )
    if ally_names:
        text += f"🤝 Союзники: {', '.join(ally_names)}\n"
    if at_war:
        text += f"⚔️ Войны: {', '.join(at_war)}\n"

    uid = message.from_user.id
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Собрать налоги", callback_data=f"action:tax|{uid}"),
         InlineKeyboardButton(text="⚔️ Войска",         callback_data=f"action:buy_info|{uid}")],
        [InlineKeyboardButton(text="🗺 Карта",          callback_data=f"action:map|{uid}"),
         InlineKeyboardButton(text="🏆 Рейтинг",       callback_data=f"action:top|{uid}")],
    ])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router_create.callback_query(F.data.startswith("action:"))
async def handle_action(callback: CallbackQuery, state: FSMContext):
    # format: action:<name>|<user_id>
    raw     = callback.data.split("|")[0]  # strip user_id suffix
    action  = raw.split(":")[1]
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id

    if action == "tax":
        ok, amount, msg = await collect_taxes(chat_id, user_id)
        await callback.answer(msg, show_alert=True)

    elif action == "buy_info":
        await callback.answer()
        lines = ["⚔️ <b>Купить войска</b>\n", "Использование: <code>/buy тип количество</code>\n"]
        for utype, udef in UNIT_DEFS.items():
            lines.append(f"  {udef['emoji']} <code>/buy {utype} N</code> — {udef['name']}, {udef['cost_gold']}💰/ед.")
        await callback.message.answer("\n".join(lines), parse_mode="HTML")

    elif action == "map":
        await callback.answer("Генерирую карту...")
        await send_map(callback.message, chat_id)

    elif action == "top":
        await callback.answer()
        await send_leaderboard(callback.message, chat_id)



# ── /isolist ─────────────────────────────────────────────────

@router_create.message(Command("isolist"))
async def cmd_isolist(message: Message):
    territories = await get_territories(message.chat.id)
    occupied    = set(territories.keys())

    regions = {
        "Европа":  ["RU","UA","DE","FR","UK","IT","ES","PL","SE","NO","FI",
                    "RO","GR","PT","NL","BE","AT","CZ","HU","BY","RS","BG",
                    "SK","HR","BA","GE","AM","AZ","LT","LV","EE","IS","IE",
                    "ME","AL","MK","MD","SI","DK","LU","CH"],
        "Азия":    ["CN","IN","KZ","MN","AF","IR","TR","PK","SA","IQ","SY",
                    "YE","OM","AE","IL","JO","UZ","TM","MM","TH","VN","MY",
                    "ID","PH","KR","JP","NP","BD","KH","LA","TW","KG","TJ",
                    "KW","QA","BH","LB","SG"],
        "Африка":  ["DZ","LY","EG","SD","ET","NG","CD","TZ","ZA","ZM","ZW",
                    "AO","MZ","NA","MA","ML","KE","SO","TN","GH","CI","SN",
                    "CM","NE","TD","SS","UG","MG","GA","CG","CF","GN","SL",
                    "LR","BF","TG","BJ","GM","GW","MR","ER","DJ","RW","BI",
                    "MW","BW","SZ","LS","GQ","EH"],
        "Америка": ["CA","US","MX","BR","AR","CO","VE","PE","CL","BO","PY",
                    "UY","EC","GT","CU","GY","SR","HT","DO","JM","TT","PA",
                    "CR","NI","HN","SV","BZ"],
        "Океания": ["AU","NZ","PG","FJ","SB","VU","NC"],
    }

    REGION_EMOJI = {
        "Европа": "🌍",
        "Азия":   "🌏",
        "Африка": "🌍",
        "Америка":"🌎",
        "Океания":"🌏",
    }

    await message.answer("<b>Список территорий (зелёный = свободна, красный = занята)</b>", parse_mode="HTML")

    for region, isos in regions.items():
        header = f"<b>{REGION_EMOJI.get(region, '')} {region}</b>"
        body_lines = []
        for iso in isos:
            name   = COUNTRY_NAMES.get(iso, iso)
            status = "🔴" if iso in occupied else "🟢"
            body_lines.append(f"{status} <code>{iso}</code> — {name}")
        body_text = "\n".join(body_lines)
        text = f"{header}\n<blockquote expandable>{body_text}</blockquote>"
        await message.answer(text, parse_mode="HTML")


# ── /delete ──────────────────────────────────────────────────

@router_create.message(Command("delete"))
async def cmd_delete(message: Message):
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны.")
        return
    cd = await get_cooldown(message.from_user.id, message.chat.id, "delete")
    if cd > 0:
        await message.answer(f"⏳ Удаление страны на кулдауне. Следующее через <b>{fmt_duration(cd)}</b>.", parse_mode="HTML")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        colored_button("✅ Да, удалить", "red", callback_data=f"confirm_delete:{message.from_user.id}|{message.from_user.id}"),
        colored_button("❌ Отмена", "default", callback_data=f"cancel_delete:{message.from_user.id}|{message.from_user.id}"),
    ]])
    await message.answer(
        f"⚠️ Удалить страну {country['emoji']} <b>{country['name']}</b>?\n"
        "Все территории будут освобождены.\n"
        f"<i>После удаления создать новую страну можно через 24 часа.</i>",
        parse_mode="HTML", reply_markup=kb
    )


@router_create.callback_query(F.data.startswith("confirm_delete:"))
async def confirm_delete(callback: CallbackQuery):
    country = await get_country_by_owner(callback.message.chat.id, callback.from_user.id)
    if country:
        await delete_country(callback.message.chat.id, callback.from_user.id)
        await set_cooldown(callback.from_user.id, callback.message.chat.id, "delete", DELETE_COOLDOWN_SEC)
        await set_cooldown(callback.from_user.id, callback.message.chat.id, "create", DELETE_COOLDOWN_SEC)
        await callback.message.edit_text(
            f"🗑 Страна {country['emoji']} <b>{country['name']}</b> удалена.\n"
            f"<i>Создать новую страну можно через 24 часа.</i>",
            parse_mode="HTML"
        )
    await callback.answer()


@router_create.callback_query(F.data.startswith("cancel_delete:"))
async def cancel_delete(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer("Отменено")


# ── /rename — переименование страны (1 раз за 500💰) ──────────────

RENAME_COST = 500

class RenameCountry(StatesGroup):
    waiting_new_name = State()


@router_create.message(Command("rename"))
async def cmd_rename(message: Message, state: FSMContext):
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны!")
        return
    if country.get("renamed", 0):
        await message.answer(
            f"❌ Переименование доступно только <b>1 раз</b>.\n"
            f"Вы уже переименовывали страну.",
            parse_mode="HTML"
        )
        return
    if country["treasury"] < RENAME_COST:
        await message.answer(
            f"❌ Нужно {RENAME_COST}💰, у вас {country['treasury']}💰"
        )
        return
    await message.answer(
        f"✏️ <b>Переименование страны</b>\n\n"
        f"Текущее название: {country['emoji']} <b>{country['name']}</b>\n"
        f"Стоимость: {RENAME_COST}💰 (один раз навсегда)\n\n"
        f"Введите новое название (2–30 символов):\n\n"
        f"Отмена: /cancel",
        parse_mode="HTML",
        reply_markup=_cancel_kb(message.from_user.id)
    )
    await state.set_state(RenameCountry.waiting_new_name)


@router_create.message(RenameCountry.waiting_new_name)
async def process_rename(message: Message, state: FSMContext):
    new_name = (message.text or "").strip()
    if not 2 <= len(new_name) <= 30:
        await message.answer(
            "❌ Название должно быть от 2 до 30 символов!",
            reply_markup=_cancel_kb(message.from_user.id)
        )
        return
    # SECURITY FIX: запрещаем HTML-символы (HTML-инъекция в parse_mode=HTML)
    if any(ch in new_name for ch in ("<", ">", "&")):
        await message.answer(
            "❌ Название не может содержать символы &lt; &gt; &amp;",
            parse_mode="HTML",
            reply_markup=_cancel_kb(message.from_user.id)
        )
        return
    await state.clear()
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ Страна не найдена.")
        return
    if country.get("renamed", 0):
        await message.answer("❌ Переименование уже использовано.")
        return
    # SECURITY FIX: имя страны — идентификатор в /give, /transfer, /attack,
    # /ally. Без проверки уникальности игрок мог переименоваться в чужое имя
    # и перехватывать адресованные другой стране переводы.
    for _c in await get_all_countries(message.chat.id):
        if _c["id"] != country["id"] and _c["name"].strip().casefold() == new_name.casefold():
            await message.answer("❌ Страна с таким названием уже есть в этом чате.")
            return
    old_name = country["name"]
    db = await _open_db()
    async with db:
        # BUG FIX: условный UPDATE — раньше флаг renamed и казна проверялись
        # по снимку, и два быстрых /rename списывали стоимость дважды.
        _cur = await db.execute(
            "UPDATE countries SET name=?, renamed=1, treasury=treasury-? "
            "WHERE id=? AND treasury>=? AND COALESCE(renamed,0)=0",
            (new_name, RENAME_COST, country["id"], RENAME_COST)
        )
        if (_cur.rowcount or 0) == 0:
            await db.commit()
            await message.answer(
                f"❌ Не удалось переименовать: нужно {RENAME_COST}💰 "
                "или переименование уже использовано."
            )
            return
        await db.commit()
    await message.answer(
        f"✅ <b>Страна переименована!</b>\n\n"
        f"Было: {country['emoji']} <b>{old_name}</b>\n"
        f"Стало: {country['emoji']} <b>{new_name}</b>\n"
        f"Потрачено: {RENAME_COST}💰",
        parse_mode="HTML"
    )


# ═══════════════════════════════════════════════════════════════
#  HANDLERS — MAP
# ═══════════════════════════════════════════════════════════════

router_map = Router()


async def send_map(message: Message, chat_id: int):
    territories = await get_territories(chat_id)
    countries   = await get_all_countries(chat_id)

    if not countries:
        await message.answer("🌍 Карта пуста. Используйте /create чтобы основать страну!")
        return

    id_to_c = {c["id"]: c for c in countries}
    ter_count = {}
    owned = {}
    for iso, cid in territories.items():
        c = id_to_c.get(cid)
        if c:
            owned[iso] = (c["color"], c["name"], c.get("emoji", ""))
            ter_count[cid] = ter_count.get(cid, 0) + 1

    # Столицы всех стран для отрисовки звезды
    capitals = {(c.get("capital_iso") or c["iso"]).upper() for c in countries}

    loop = asyncio.get_running_loop()
    buf = await loop.run_in_executor(
        None,
        functools.partial(generate_map, owned,
                          title="Карта мира — Страны РП",
                          capitals=capitals)
    )

    sorted_c = sorted(countries, key=lambda x: ter_count.get(x["id"], 0), reverse=True)
    lines = ["<b>🌍 Текущая карта мира</b>\n"]
    for c in sorted_c[:10]:
        lines.append(f"{c['emoji']} <b>{c['name']}</b> — {ter_count.get(c['id'], 0)} терр.")
    if len(countries) > 10:
        lines.append(f"...и ещё {len(countries)-10} стран")

    await message.answer_photo(
        BufferedInputFile(buf.read(), filename="map.png"),
        caption="\n".join(lines), parse_mode="HTML"
    )


@router_map.message(Command("map"))
async def cmd_map(message: Message):
    if message.chat.type == "private":
        await message.answer("❌ Команда работает только в группах!")
        return
    await send_map(message, message.chat.id)


# ═══════════════════════════════════════════════════════════════
#  HANDLERS — WAR
# ═══════════════════════════════════════════════════════════════

router_war = Router()


@router_war.message(Command("deploy"))
async def cmd_deploy(message: Message):
    """/deploy ISO тип количество"""
    if message.chat.type == "private":
        return
    attacker = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not attacker:
        await message.answer("❌ У вас нет страны! /create")
        return
    args = message.text.split()
    if len(args) < 4:
        lines = "\n".join(
            f"  <code>{k}</code> — {v}, время: {UNIT_TRAVEL_MINUTES[k]} мин"
            for k, v in UNIT_LABELS.items()
        )
        await message.answer(
            "🚀 <b>Отправка войск</b>\n\n"
            "Использование: <code>/deploy ISO тип количество</code>\n"
            "Пример: <code>/deploy DE infantry 200</code>\n\n"
            "ℹ️ Правила: свои земли — гарнизон; вражеские — только по соседству и после /war; "
            "нейтральные — через /expand. Для штурма нужно ≥ 10 силы.\n\n"
            f"<b>Типы войск:</b>\n{lines}",
            parse_mode="HTML"
        )
        return
    target_iso = args[1].upper()
    unit_type  = args[2].lower()
    if unit_type not in UNIT_TRAVEL_MINUTES:
        await message.answer(f"❌ Неизвестный тип: {unit_type}\nДоступные: {', '.join(UNIT_TRAVEL_MINUTES)}", parse_mode="HTML")
        return
    if not is_valid_iso(target_iso):
        await message.answer(f"❌ Неизвестный ISO: {target_iso}")
        return
    try:
        unit_count = int(args[3])
        if unit_count <= 0: raise ValueError
    except ValueError:
        await message.answer("❌ Количество должно быть положительным числом")
        return
    ok, msg = await send_deployment(message.chat.id, attacker["id"], target_iso, unit_type, unit_count)
    if ok:
        travel = UNIT_TRAVEL_MINUTES[unit_type]
        await message.answer(
            f"🚀 {attacker['emoji']} <b>{attacker['name']}</b>\n\n"
            f"{UNIT_LABELS[unit_type]}: <b>{unit_count} ед.</b> → <b>{target_iso}</b>\n"
            f"⏱ Время в пути: {travel} мин\n✅ {msg}",
            parse_mode="HTML"
        )
    else:
        await message.answer(f"❌ {msg}")


@router_war.message(Command("deployments"))
async def cmd_deployments(message: Message):
    """Показывает активные отправки войск."""
    if message.chat.type == "private":
        return
    now = int(time.time())
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM deployments WHERE chat_id=? AND resolved=0 ORDER BY arrive_at",
            (message.chat.id,)
        )
        deps = await c.fetchall()
    if not deps:
        await message.answer("🕊 Нет активных отправок войск.")
        return
    from datetime import datetime
    lines = ["🚀 <b>Активные отправки войск:</b>\n"]
    for dep in deps:
        country = await get_country_by_id(message.chat.id, dep["attacker_id"])
        cname = f"{country['emoji']} {country['name']}" if country else "???"
        arrive = datetime.fromtimestamp(dep["arrive_at"]).strftime("%H:%M:%S")
        remaining = max(0, dep["arrive_at"] - now)
        rm, rs = divmod(remaining, 60)
        lines.append(
            f"{cname} → <b>{dep['target_iso']}</b>\n"
            f"  {UNIT_LABELS.get(dep['unit_type'], dep['unit_type'])}: {dep['unit_count']} ед.\n"
            f"  🕐 Прибытие: {arrive} (через {rm}м {rs}с)"
        )
    await message.answer("\n\n".join(lines), parse_mode="HTML")


@router_war.message(Command("war"))
async def cmd_war(message: Message):
    if message.chat.type == "private":
        return
    try:
        attacker = await get_country_by_owner(message.chat.id, message.from_user.id)
        if not attacker:
            await message.answer("❌ У вас нет страны! /create")
            return

        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer(
                "Использование: <code>/war ИмяСтраны</code> или <code>/war ISO</code>",
                parse_mode="HTML"
            )
            return

        q = args[1].strip()
        defender = await get_country_by_iso(message.chat.id, q)
        if not defender:
            for c in await get_all_countries(message.chat.id):
                if c["name"].lower() == q.lower():
                    defender = c; break

        # Если ISO совпадает со страной завоевателя — возможно игрок имеет в виду
        # владельца этой территории (которую у него отняли). Ищем по territories.
        if not defender or defender["id"] == attacker["id"]:
            _db = await _open_db()
            async with _db:
                _c = await _db.execute(
                    """SELECT c.* FROM countries c
                       JOIN territories t ON t.country_id = c.id
                       WHERE t.chat_id=? AND t.iso=? AND c.id != ?
                       LIMIT 1""",
                    (message.chat.id, q.upper(), attacker["id"])
                )
                _row = await _c.fetchone()
                if _row:
                    defender = dict(_row)

        if not defender:
            await message.answer(f"❌ Страна «{q}» не найдена.")
            return
        if defender["id"] == attacker["id"]:
            await message.answer("❌ Нельзя объявить войну самому себе!")
            return
        if defender.get("owner_id") == message.from_user.id:
            await message.answer("❌ Нельзя объявить войну самому себе!")
            return

        ok, reason = await declare_war(message.chat.id, attacker["id"], defender["id"])
        if not ok:
            await message.answer(f"❌ {reason}")
            return

        await log_event(message.chat.id, "war_declared", {
            "attacker": attacker["name"], "attacker_emoji": attacker["emoji"],
            "defender": defender["name"], "defender_emoji": defender["emoji"],
        })
        def_mention = make_owner_mention(defender)
        att_mention = make_owner_mention(attacker)
        await message.answer(
            f"⚔️ <b>ВОЙНА ОБЪЯВЛЕНА!</b>\n\n"
            f"{make_country_str(attacker)} {att_mention} объявляет войну "
            f"{make_country_str(defender)} {def_mention}!\n\n"
            "Используйте /attack ОТКУДА КУДА для атаки.\nПример: /attack KR JP",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"cmd_war error: {e}", exc_info=True)
        await message.answer("❌ Внутренняя ошибка. Попробуйте ещё раз.")


async def _occupy_with_survivors(chat_id: int, country_id: int, from_iso: str, target_iso: str, fraction: float = 0.25) -> int:
    """Перебрасывает часть уцелевшего атакующего гарнизона на захваченную землю."""
    db = await _open_db()
    moved = 0
    async with db:
        c = await db.execute(
            "SELECT * FROM garrisons WHERE chat_id=? AND country_id=? AND iso=?",
            (chat_id, country_id, from_iso)
        )
        row = await c.fetchone()
        if not row:
            return 0
        total = sum(int(row[u] or 0) for u in UNIT_TYPES)
        if total <= 0:
            return 0
        move_total = min(max(1, int(total * fraction)), max(0, total - 1))
        if move_total <= 0:
            return 0
        vals = {}
        allocated = 0
        for u in UNIT_TYPES:
            cnt = int(row[u] or 0)
            take = min(cnt, int(round(move_total * cnt / total)))
            vals[u] = take
            allocated += take
        # Компенсируем округление, чтобы не создавать/терять единицы.
        while allocated < move_total:
            candidates = [u for u in UNIT_TYPES if int(row[u] or 0) - vals[u] > 0]
            if not candidates:
                break
            u = max(candidates, key=lambda x: int(row[x] or 0) - vals[x])
            vals[u] += 1
            allocated += 1
        while allocated > move_total:
            candidates = [u for u in UNIT_TYPES if vals[u] > 0]
            if not candidates:
                break
            u = min(candidates, key=lambda x: vals[x])
            vals[u] -= 1
            allocated -= 1
        for u, take in vals.items():
            if take <= 0:
                continue
            await db.execute(
                f"UPDATE garrisons SET {u}=MAX(0,{u}-?) WHERE chat_id=? AND country_id=? AND iso=?",
                (take, chat_id, country_id, from_iso)
            )
        placeholders = ",".join(["?"] * (3 + len(UNIT_TYPES)))
        # Существующий гарнизон создаём или пополняем.
        await db.execute(
            "INSERT OR IGNORE INTO garrisons (chat_id,country_id,iso,infantry,cavalry,artillery,airforce,bomber,navy,armored,tank) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (chat_id, country_id, target_iso, *([0] * len(UNIT_TYPES)))
        )
        for u, take in vals.items():
            if take > 0:
                await db.execute(
                    f"UPDATE garrisons SET {u}={u}+? WHERE chat_id=? AND country_id=? AND iso=?",
                    (take, chat_id, country_id, target_iso)
                )
        await db.commit()
        moved = allocated
    return moved


@router_war.message(Command("attack"))
async def cmd_attack(message: Message):
    """
    /attack FROM_ISO TARGET_ISO  — атака из гарнизона своей территории
    FROM_ISO должна граничить с TARGET_ISO (или иметь флот/авиацию для дальних атак).
    Войска берутся из гарнизона FROM_ISO, не из общего пула.
    """
    if message.chat.type == "private":
        return

    attacker = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not attacker:
        await message.answer("❌ У вас нет страны!")
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer(
            "⚔️ <b>Атака из гарнизона</b>\n\n"
            "Использование: <code>/attack ОТКУДА КУДА</code>\n"
            "Пример: <code>/attack KR JP</code>\n\n"
            "Войска берутся из гарнизона вашей территории ОТКУДА.\n"
            "ОТКУДА должна граничить с КУДА (или иметь авиацию/флот).",
            parse_mode="HTML"
        )
        return

    from_iso   = args[1].upper()
    target_iso = args[2].upper()

    if not is_valid_iso(from_iso):
        await message.answer(f"❌ Неизвестный ISO: {from_iso}")
        return
    if not is_valid_iso(target_iso):
        await message.answer(f"❌ Неизвестный ISO: {target_iso}")
        return
    if from_iso == target_iso:
        await message.answer("❌ Нельзя атаковать свою же территорию!")
        return

    our_territories = await get_country_territories(message.chat.id, attacker["id"])

    # from_iso должна быть нашей
    if from_iso not in our_territories:
        await message.answer(
            f"❌ <b>{COUNTRY_NAMES.get(from_iso, from_iso)}</b> (<code>{from_iso}</code>) — не ваша территория!\n"
            f"Атаковать можно только из своих территорий.",
            parse_mode="HTML"
        )
        return

    if target_iso in our_territories:
        await message.answer("❌ Цель уже ваша!")
        return

    # Проверяем гарнизон на from_iso
    atk_garrison = await get_garrison(message.chat.id, attacker["id"], from_iso)
    unit_power_atk = {"infantry": 1.0, "cavalry": 1.5, "artillery": 20.0, "airforce": 3.0, "bomber": 5.0, "navy": 10.0, "armored": 8.0, "tank": 15.0}
    atk_garrison_strength = sum(atk_garrison.get(u, 0) * unit_power_atk.get(u, 1.0) for u in UNIT_TYPES)
    atk_garrison_total    = sum(atk_garrison.get(u, 0) for u in UNIT_TYPES)

    if atk_garrison_total < 5:
        from_name = COUNTRY_NAMES.get(from_iso, from_iso)
        await message.answer(
            f"❌ В гарнизоне <b>{from_name}</b> слишком мало войск (есть {atk_garrison_total}, нужно ≥5)!\n"
            f"Разместите войска: <code>/garrison {from_iso} пехота N</code>",
            parse_mode="HTML"
        )
        return

    # Проверяем соседство from_iso → target_iso
    mil = await get_military(attacker["id"])
    gar_airforce = atk_garrison.get("airforce", 0) + atk_garrison.get("bomber", 0)
    gar_navy     = atk_garrison.get("navy", 0)
    is_adjacent  = is_neighbor(from_iso, target_iso)

    # Союз с третьей страной сам по себе не телепортирует сухопутную армию.
    # Транзит через союзника должен быть отдельной механикой, иначе игрок
    # может атаковать через весь мир из любой своей территории.

    target_coastal = target_iso in COASTAL_ISOS
    from_coastal   = from_iso in COASTAL_ISOS

    attack_mode     = "ground"
    attack_modifier = 1.0

    if is_adjacent:
        attack_mode     = "ground"
        attack_modifier = 1.0
    elif gar_airforce >= 10:
        attack_mode     = "air"
        attack_modifier = 0.75
    elif gar_navy >= 5 and target_coastal and from_coastal:
        attack_mode     = "naval"
        attack_modifier = 0.85
    else:
        from_name = COUNTRY_NAMES.get(from_iso, from_iso)
        tgt_name  = COUNTRY_NAMES.get(target_iso, target_iso)
        await message.answer(
            f"❌ <b>{from_name}</b> не граничит с <b>{tgt_name}</b>!\n\n"
            f"Чтобы атаковать отсюда:\n"
            f"  ✈️ Авиация/бомберы в гарнизоне ≥10 ед. — любая цель\n"
            f"  ⚓ Флот ≥5 ед. + обе территории прибрежные — морской десант\n"
            f"  🗺 Выберите соседнюю территорию как FROM_ISO",
            parse_mode="HTML"
        )
        return

    territories = await get_territories(message.chat.id)
    owner_cid = territories.get(target_iso)
    defender  = await get_country_by_id(message.chat.id, owner_cid) if owner_cid else None

    if not defender:
        await message.answer(
            f"❌ <b>{COUNTRY_NAMES.get(target_iso, target_iso)}</b> — нейтральная территория.\n"
            f"Для захвата нейтральных земель используйте <code>/expand {target_iso}</code>",
            parse_mode="HTML"
        )
        return

    war = await get_active_war(message.chat.id, attacker["id"], defender["id"])
    if not war:
        await message.answer(
            f"❌ Сначала объявите войну: <code>/war {defender['name']}</code>",
            parse_mode="HTML"
        )
        return

    # BUG FIX: проверка и установка кулдауна одним атомарным оператором.
    # Раньше между get_cooldown() и set_cooldown() было несколько await, и два
    # одновременных /attack читали один и тот же гарнизон, списывая войска
    # дважды и выигрывая бой «за полцены».
    cd_attack = await try_claim_cooldown(message.from_user.id, message.chat.id, "attack", 35)
    if cd_attack > 0:
        await message.answer(f"⏳ Следующая атака через {cd_attack} сек.", parse_mode="HTML")
        return

    # Боевая сила атакующего = гарнизон on from_iso
    atk_bonus = 1.0

    # Гарнизон защитника на территории
    garrison_strength, garrison_breakdown = await get_defense_garrison(message.chat.id, target_iso)
    defender_raw_army = sum(sum(int(row.get(u, 0) or 0) for u in UNIT_TYPES) for row in garrison_breakdown.values())
    defender_weighted = sum(sum(int(row.get(u, 0) or 0) * unit_power_atk.get(u, 1.0) for u in UNIT_TYPES) for row in garrison_breakdown.values())

    # Уведомление защитнику
    try:
        def_ping = make_owner_mention(dict(defender))
        ter_name_ping = COUNTRY_NAMES.get(target_iso, target_iso)
        from_name_ping = COUNTRY_NAMES.get(from_iso, from_iso)
        gar_ping = int(garrison_strength)
        gar_note_ping = f"Гарнизон: {gar_ping} силы" if gar_ping > 0 else "⚠️ Гарнизона нет!"
        await message.answer(
            f"{PE['alert']} <b>АТАКА!</b> {def_ping}\n"
            f"{attacker['emoji']} <b>{attacker['name']}</b> атакует <b>{ter_name_ping}</b> из <b>{from_name_ping}</b>\n"
            f"🛡 {gar_note_ping}",
            parse_mode="HTML"
        )
    except Exception:
        pass

    # Претензия атакующего
    claim = await get_claim(message.chat.id, attacker["id"], target_iso)
    claim_bonus = (1.0 + claim["strength"] / 100.0) if claim else 1.0
    claim_str = f"\n📜 Претензия «{CLAIM_TYPES[claim['claim_type']]['name']}»: +{claim['strength']}% к атаке" if claim else ""

    # Усталость
    att_weariness = await get_weariness(attacker["id"])
    _, att_w_pen, _ = weariness_info(att_weariness)
    wear_str = f"\n😓 Усталость: -{int(att_w_pen*100)}%" if att_w_pen > 0 else ""

    garrison_army = int(defender_raw_army)

    # Захват без боя — у защитника нет гарнизона на территории
    if garrison_army == 0:
        ter_name_q = COUNTRY_NAMES.get(target_iso, target_iso)
        from_name_q = COUNTRY_NAMES.get(from_iso, from_iso)
        await message.answer(
            f"{PE['flag']} <b>ЗАХВАТ БЕЗ БОЯ!</b>\n\n"
            f"{attacker['emoji']} <b>{attacker['name']}</b> из <b>{from_name_q}</b> занял <b>{ter_name_q}</b>!\n"
            f"Территория была без гарнизона.\n\n"
            f"💀 Потери атакующего: 0\n💀 Потери защитника: 0",
            parse_mode="HTML"
        )
        await add_weariness(attacker["id"], WEARINESS_ON_WIN // 2)
        await add_weariness(defender["id"], WEARINESS_ON_LOSS)
        try:
            def_mention_instant = make_owner_mention(dict(defender))
            await message.answer(
                f"🚨 {def_mention_instant} — <b>{ter_name_q}</b> (<code>{target_iso}</code>) захвачена без боя!\n"
                f"⚠️ Там не было гарнизона!\n"
                f"Используйте <code>/garrison {target_iso} пехота N</code> чтобы защищать территории.",
                parse_mode="HTML"
            )
        except Exception:
            pass
        await capture_territory(message.chat.id, target_iso, attacker["id"])
        await strip_garrison_on_capture(message.chat.id, target_iso, attacker["id"])
        occupied = await _occupy_with_survivors(message.chat.id, attacker["id"], from_iso, target_iso, 0.10)
        if occupied:
            await message.answer(f"🏳️ Оккупационный гарнизон: {occupied} ед. переброшено в {target_iso}.", parse_mode="HTML")
        if claim:
            await drop_claim(message.chat.id, attacker["id"], target_iso)
        await add_vp(attacker["id"], 10)
        def_territories_left = await get_country_territories(message.chat.id, defender["id"])
        if not def_territories_left:
            war2 = await get_active_war(message.chat.id, attacker["id"], defender["id"])
            if war2:
                spoils = await end_war(war2["id"], attacker["id"], defender["id"])
                await add_vp(attacker["id"], 50)
                await message.answer(
                    f"🏆 {defender['emoji']} {defender['name']} лишилась всех территорий!\n💰 Трофеи: {spoils}💰",
                    parse_mode="HTML"
                )
        await log_event(message.chat.id, "territory_captured", {
            "attacker": attacker["name"], "attacker_emoji": attacker["emoji"],
            "defender": defender["name"], "defender_emoji": defender["emoji"],
            "territory": ter_name_q, "losses_att": 0, "losses_def": 0,
        })
        return

    effective_def = garrison_army

    # Battle engine получает реальные единицы, а качество состава задаётся
    # отдельным коэффициентом. Это исключает "лишние" потери от артиллерии/танков.
    effective_attack_pool = int(atk_garrison_total)
    attack_army = max(1, int(effective_attack_pool * attack_modifier * claim_bonus * (1.0 - att_w_pen)))
    atk_bonus = max(0.25, atk_garrison_strength / max(atk_garrison_total, 1))
    def_bonus = max(0.25, defender_weighted / max(defender_raw_army, 1))

    # Крепость
    fortress_bonus = 1.0
    if defender:
        _db = await _open_db()
        async with _db:
            _c = await _db.execute(
                "SELECT level FROM buildings WHERE country_id=? AND iso=? AND type='fortress'",
                (defender["id"], target_iso)
            )
            _fort = await _c.fetchone()
            if _fort:
                fortress_bonus = 1.0 + _fort["level"] * 0.25

    ter_name  = COUNTRY_NAMES.get(target_iso, target_iso)
    from_name = COUNTRY_NAMES.get(from_iso, from_iso)
    terrain   = get_terrain(target_iso)
    ter_emoji = TERRAIN_EMOJI[terrain]
    mode_name  = {"ground": "наземная", "air": "воздушная", "naval": "морской десант"}[attack_mode]
    mode_emoji = {"ground": "⚔️", "air": "✈️", "naval": "⚓"}[attack_mode]
    def_name = f"{defender['emoji']} {defender['name']}"
    gar_note = f" (гарнизон: {int(garrison_strength)} ед.)" if garrison_strength > 0 else " (гарнизона нет — лёгкая победа!)"

    att_army_display = f"{attack_army}"
    if att_w_pen > 0:
        att_army_display += f" <i>(из {effective_attack_pool}, -{int(att_w_pen*100)}% усталость)</i>"
    await message.answer(
        f"{PE['sword']} <b>НАЧАЛО БОЯ</b>\n"
        f"{'─' * 28}\n"
        f"📍 {from_name} (<code>{from_iso}</code>) → {ter_emoji} {ter_name} (<code>{target_iso}</code>)\n"
        f"⚔️ Режим: {mode_name} | Рельеф: {terrain}\n\n"
        f"{attacker['emoji']} <b>{attacker['name']}</b>: {att_army_display} силы (гарнизон {from_iso}){claim_str}{wear_str}\n"
        f"🛡 {def_name}: {int(garrison_strength)} боевая сила ({garrison_army} ед.){gar_note}\n\n"
        f"⏳ <i>Бой начинается...</i>",
        parse_mode="HTML"
    )

    # Пошаговый бой
    won, losses_att, losses_def = await run_battle_rounds(
        bot      = message.bot,
        chat_id  = message.chat.id,
        message_id = message.message_id,
        attacker = attacker,
        defender = defender,
        target_iso   = target_iso,
        attack_army  = attack_army,
        def_army     = effective_def,   # включает гарнизон
        fortress_bonus = fortress_bonus,
        attack_mode  = attack_mode,
        atk_bonus    = atk_bonus,
        def_bonus    = def_bonus,
    )

    # Потери атакующего снимаем из гарнизона from_iso
    if losses_att > 0:
        atk_gar_total = sum(atk_garrison.get(u, 0) for u in UNIT_TYPES)
        if atk_gar_total > 0:
            _adb = await _open_db()
            async with _adb:
                for u in UNIT_TYPES:
                    u_cnt = atk_garrison.get(u, 0)
                    if u_cnt > 0:
                        u_loss = min(u_cnt, int(losses_att * u_cnt / atk_gar_total))
                        if u_loss > 0:
                            await _adb.execute(
                                f"UPDATE garrisons SET {u}=MAX(0,{u}-?) WHERE chat_id=? AND country_id=? AND iso=?",
                                (u_loss, message.chat.id, attacker["id"], from_iso)
                            )
                await _adb.commit()

    if defender:
        # Распределяем потери между всеми гарнизонами, которые участвовали
        # в обороне, пропорционально их численности.
        remaining_losses = int(max(0, losses_def))
        participants = []
        total_participants = 0
        for _cid, _row in garrison_breakdown.items():
            _n = sum(int(_row.get(u, 0) or 0) for u in UNIT_TYPES)
            if _n > 0:
                participants.append((_cid, _row, _n))
                total_participants += _n
        if remaining_losses > 0 and participants:
            _dbd = await _open_db()
            async with _dbd:
                for idx, (_cid, _row, _n) in enumerate(participants):
                    share = remaining_losses if idx == len(participants) - 1 else min(remaining_losses, int(round(losses_def * _n / max(total_participants, 1))))
                    share = min(share, _n)
                    if share <= 0:
                        continue
                    unit_total = _n
                    for u in UNIT_TYPES:
                        cnt = int(_row.get(u, 0) or 0)
                        if cnt <= 0:
                            continue
                        u_loss = min(cnt, int(round(share * cnt / max(unit_total, 1))))
                        if u_loss > 0:
                            await _dbd.execute(
                                f"UPDATE garrisons SET {u}=MAX(0,{u}-?) WHERE chat_id=? AND country_id=? AND iso=?",
                                (u_loss, message.chat.id, _cid, target_iso)
                            )
                    remaining_losses -= share
                await _dbd.commit()

    # Обновляем усталость
    if won:
        await add_weariness(attacker["id"], WEARINESS_ON_WIN)
        if defender:
            await add_weariness(defender["id"], WEARINESS_ON_LOSS)
        # capture_territory автоматически переносит здания победителю
        await capture_territory(message.chat.id, target_iso, attacker["id"])
        await add_vp(attacker["id"], 10)  # 10 VP за захват территории
        # Гарнизоны защитников отступают (войска возвращаются в пул)
        await strip_garrison_on_capture(message.chat.id, target_iso, attacker["id"])
        occupied = await _occupy_with_survivors(message.chat.id, attacker["id"], from_iso, target_iso, 0.25)
        if occupied:
            await message.answer(f"🏳️ Оккупационный гарнизон: {occupied} ед. переброшено в {target_iso}.", parse_mode="HTML")
        # Претензия реализована — снимаем
        if claim:
            await drop_claim(message.chat.id, attacker["id"], target_iso)

        # Проверяем — не является ли захваченное столицей противника
        capital_captured = False
        capital_msg = ""
        if defender:
            defender_capital = (defender.get("capital_iso") or defender["iso"]).upper()
            if target_iso == defender_capital:
                capital_captured = True
                capital_msg = (
                    f"\n\n🏯 <b>СТОЛИЦА ЗАХВАЧЕНА!</b>\n"
                    f"Все здания {defender['emoji']} {defender['name']} перешли под контроль {attacker['emoji']} {attacker['name']}!"
                )

        await log_event(message.chat.id, "territory_captured", {
            "attacker": attacker["name"], "attacker_emoji": attacker["emoji"],
            "territory": ter_name, "iso": target_iso,
            "defender": defender["name"] if defender else None,
            "defender_emoji": defender["emoji"] if defender else None,
            "losses_att": losses_att, "losses_def": losses_def,
        })
        def_mention = make_owner_mention(defender) if defender else ""
        attacker_won = True
        # Выжившие войска атакующего переходят в гарнизон захваченной территории
        survivors_count = max(0, atk_garrison_total - losses_att)
        if survivors_count > 0:
            atk_gar_total_pre = sum(atk_garrison.get(u, 0) for u in UNIT_TYPES)
            _sdb = await _open_db()
            async with _sdb:
                # Удаляем из from_iso оставшихся (они ушли в атаку)
                for u in UNIT_TYPES:
                    u_cnt = atk_garrison.get(u, 0)
                    if u_cnt > 0 and atk_gar_total_pre > 0:
                        survived_u = max(0, u_cnt - int(losses_att * u_cnt / max(1, atk_gar_total_pre)))
                        if survived_u > 0:
                            # Убираем из старого гарнизона
                            await _sdb.execute(
                                f"UPDATE garrisons SET {u}=MAX(0,{u}-?) WHERE chat_id=? AND country_id=? AND iso=?",
                                (survived_u, message.chat.id, attacker["id"], from_iso)
                            )
                            # Добавляем в новый гарнизон
                            await _sdb.execute(
                                f"""INSERT INTO garrisons (chat_id, country_id, iso, {u}, updated_at)
                                    VALUES (?,?,?,?,strftime('%s','now'))
                                    ON CONFLICT(chat_id, country_id, iso)
                                    DO UPDATE SET {u}={u}+?, updated_at=strftime('%s','now')""",
                                (message.chat.id, attacker["id"], target_iso, survived_u, survived_u)
                            )
                await _sdb.commit()
        result_text = (
            f"{PE['trophy']} <b>ПОБЕДА!</b>\n\n"
            f"{make_country_str(attacker)} захватил <b>{ter_name}</b>!\n"
            + (f"🏠 Владелец {make_country_str(defender, False)} {def_mention} потерял территорию!\n\n" if defender else "\n")
            + f"📊 Потери атакующего: {losses_att} солдат\n"
            f"📊 Потери защитника: {losses_def} солдат"
            + capital_msg
        )
        if defender:
            def_territories = await get_country_territories(message.chat.id, defender["id"])
            if not def_territories:
                war = await get_active_war(message.chat.id, attacker["id"], defender["id"])
                if war:
                    spoils = await end_war(war["id"], attacker["id"], defender["id"])
                    await add_vp(attacker["id"], 50)  # 50 VP за полную победу в войне
                    await log_event(message.chat.id, "country_defeated", {
                        "winner": attacker["name"], "winner_emoji": attacker["emoji"],
                        "loser": defender["name"], "loser_emoji": defender["emoji"],
                        "spoils": spoils,
                    })
                    result_text += (
                        f"\n\n🏆 <b>{defender['emoji']} {defender['name']} полностью разгромлена!</b>\n"
                        f"💰 Трофеи: {spoils}💰"
                    )
    else:
        attacker_won = False
        await add_weariness(attacker["id"], WEARINESS_ON_LOSS)
        if defender:
            await add_weariness(defender["id"], WEARINESS_ON_WIN)
        att_mention = make_owner_mention(attacker)
        result_text = (
            f"{PE['skull']} <b>ПОРАЖЕНИЕ!</b>\n\n"
            f"{make_country_str(attacker)} {att_mention} не смог захвати��ь <b>{ter_name}</b>!\n\n"
            f"📊 Потери атакующего: {losses_att} солдат\n"
            f"📊 Потери защитника: {losses_def} солдат"
        )

    await message.answer(result_text, parse_mode="HTML")
    # Кулдаун уже зарезервирован перед боем — защита от параллельных атак.

    # Уведомление защитнику о результате боя
    if defender and attacker_won:
        try:
            def_ping2 = make_owner_mention(dict(defender))
            ter_name_res = COUNTRY_NAMES.get(target_iso, target_iso)
            remaining_gar = int(garrison_strength) - losses_def
            await message.answer(
                f"{PE['skull']} {def_ping2} — <b>{ter_name_res}</b> потеряна!\n"
                f"Оставшийся гарнизон уничтожен. Проверьте <code>/garrison</code>.",
                parse_mode="HTML"
            )
        except Exception:
            pass
    elif defender and not attacker_won:
        try:
            def_ping3 = make_owner_mention(dict(defender))
            ter_name_res2 = COUNTRY_NAMES.get(target_iso, target_iso)
            await message.answer(
                f"{PE['shield']} {def_ping3} — атака на <b>{ter_name_res2}</b> отбита! Гарнизон устоял.",
                parse_mode="HTML"
            )
        except Exception:
            pass


@router_war.message(Command("ship"))
async def cmd_ship(message: Message):
    """
    /ship — список кораблей
    /ship buy [уровень] — купить корабль (1-5)
    /ship send ИД_корабля ISO_откуда ISO_куда ресурс количество — отправить груз
    /ship troops ИД_корабля ISO_куда количество — перевезти пехоту (макс 50)
    /ship capture ИД_корабля — захватить вражеский корабль в пути
    """
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны!")
        return

    args = message.text.split()
    sub = args[1].lower() if len(args) > 1 else "list"

    db = await _open_db()
    async with db:

        if sub == "buy":
            level = int(args[2]) if len(args) > 2 and args[2].isdigit() else 1
            level = max(1, min(5, level))
            sdef = SHIP_LEVELS[level]
            cost = sdef["cost_gold"]
            # BUG FIX: country читается ДО открытия транзакции, поэтому проверка
            # шла по устаревшему балансу — два быстрых /ship buy покупали два
            # корабля по цене одного и уводили казну в минус.
            _cur = await db.execute(
                "UPDATE countries SET treasury=treasury-? WHERE id=? AND treasury>=?",
                (cost, country["id"], cost)
            )
            if (_cur.rowcount or 0) == 0:
                _hc = await db.execute("SELECT treasury FROM countries WHERE id=?", (country["id"],))
                _hr = await _hc.fetchone()
                await message.answer(f"❌ Нужно {cost}💰, у вас {_hr['treasury'] if _hr else 0}💰")
                return
            await db.execute(
                "INSERT INTO trade_ships (chat_id, owner_id, name, level, capacity) VALUES (?,?,?,?,?)",
                (message.chat.id, country["id"], sdef["name"], level, sdef["capacity"])
            )
            await db.commit()
            lines = [f"🚢 <b>{sdef['name']}</b> (уровень {level}) куплен за {cost}💰!"]
            for lv, ld in SHIP_LEVELS.items():
                lines.append(f"  Лв{lv} {ld['name']}: вместимость {ld['capacity']}, "
                             f"скорость ×{round(SHIP_LEVELS[1]['speed_min_per_deg']/ld['speed_min_per_deg'],1)}, "
                             f"цена {ld['cost_gold']}💰")
            lines.append("\nИспользуйте <code>/ship send ИД ISO_от ISO_к ресурс количество</code>")
            await message.answer("\n".join(lines), parse_mode="HTML")

        elif sub == "send":
            if len(args) < 7:
                await message.answer(
                    "Использование: <code>/ship send ИД ISO_от ISO_к ресурс количество</code>\n"
                    "Пример: <code>/ship send 1 DE JP gold 500</code>\n"
                    "Ресурсы: food, production, oil, rare_metals, science, treasury",
                    parse_mode="HTML"
                )
                return
            try:
                ship_id = int(args[2])
            except ValueError:
                await message.answer("❌ Укажите ID корабля числом.")
                return
            from_iso = args[3].upper()
            to_iso   = args[4].upper()
            res      = args[5].lower()
            if not is_valid_iso(from_iso) or not is_valid_iso(to_iso):
                await message.answer("❌ Укажите корректные ISO-коды стран.")
                return
            # Корабль нельзя отправить из чужой территории или из никуда.
            # Это закрывает обход дипломатии/географии через подставной from_iso.
            own_territories = await get_country_territories(message.chat.id, country["id"])
            if from_iso not in own_territories:
                await message.answer(f"❌ Порт отправления {from_iso} не принадлежит вашей стране.")
                return
            RES_MAP  = {"gold": "treasury", "золото": "treasury", "еда": "food",
                        "нефть": "oil", "металлы": "rare_metals", "наука": "science",
                        "производство": "production"}
            res = RES_MAP.get(res, res)
            VALID_RES = {"food", "production", "oil", "rare_metals", "science", "treasury"}
            if res not in VALID_RES:
                await message.answer(f"❌ Неизвестный ресурс: {res}. Допустимые: {', '.join(VALID_RES)}")
                return
            try:
                amount = int(args[6])
                if amount <= 0: raise ValueError
            except ValueError:
                await message.answer("❌ Укажите положительное количество.")
                return

            # Проверяем корабль
            sc = await db.execute(
                "SELECT * FROM trade_ships WHERE id=? AND owner_id=? AND chat_id=?",
                (ship_id, country["id"], message.chat.id)
            )
            ship = await sc.fetchone()
            if not ship:
                await message.answer("❌ Корабль не найден или не ваш.")
                return
            if ship["status"] != "idle":
                eta = ship["arrive_at"] - int(__import__('time').time())
                eta_min = max(0, eta // 60)
                await message.answer(f"❌ Корабль в пути, прибудет через ~{eta_min} мин.")
                return
            if amount > ship["capacity"]:
                await message.answer(f"❌ Вместимость корабля: {ship['capacity']}. Вы пытаетесь отправить {amount}.")
                return

            # Проверяем наличие ресурса
            if res == "treasury":
                # BUG FIX: тот же устаревший снимок — золото можно было
                # «погрузить» на два корабля одновременно, удвоив сумму.
                _cur = await db.execute(
                    "UPDATE countries SET treasury=treasury-? WHERE id=? AND treasury>=?",
                    (amount, country["id"], amount)
                )
                if (_cur.rowcount or 0) == 0:
                    _hc = await db.execute("SELECT treasury FROM countries WHERE id=?", (country["id"],))
                    _hr = await _hc.fetchone()
                    await message.answer(
                        f"❌ Недостаточно золота: {_hr['treasury'] if _hr else 0}/{amount}"
                    )
                    return
            else:
                rc = await db.execute(f"SELECT {res} FROM resources WHERE country_id=?", (country["id"],))
                rr = await rc.fetchone()
                have = rr[res] if rr else 0
                if have < amount:
                    await message.answer(f"❌ Недостаточно ресурса: {have}/{amount}")
                    return
                await db.execute(f"UPDATE resources SET {res}={res}-? WHERE country_id=?", (amount, country["id"]))

            # Рассчитываем время в пути
            sdef = SHIP_LEVELS[ship["level"]]
            travel_min = _travel_minutes(from_iso, to_iso, sdef["speed_min_per_deg"])
            now = int(__import__('time').time())
            arrive_at = now + travel_min * 60

            await db.execute(
                "UPDATE trade_ships SET status='sailing', from_iso=?, to_iso=?, "
                "cargo_res=?, cargo_amt=?, depart_at=?, arrive_at=? WHERE id=?",
                (from_iso, to_iso, res, amount, now, arrive_at, ship_id)
            )
            await db.commit()

            res_label = {"food": "🌾 Еда", "production": "⚙️ Производство", "oil": "🛢 Нефть",
                         "rare_metals": "💎 Металлы", "science": "🔬 Наука", "treasury": "💰 Золото"}.get(res, res)
            from_name = COUNTRY_NAMES.get(from_iso, from_iso)
            to_name   = COUNTRY_NAMES.get(to_iso, to_iso)
            await message.answer(
                f"⛵ <b>Корабль отплыл!</b>\n\n"
                f"🚢 «{ship['name']}» везёт <b>{amount} {res_label}</b>\n"
                f"📍 Маршрут: {from_name} → {to_name}\n"
                f"⏱ Время в пути: ~{travel_min} мин\n"
                f"📦 Прибытие: через {travel_min} мин\n\n"
                f"<i>Корабль может быть захвачен врагами командой /ship capture</i>",
                parse_mode="HTML"
            )

        elif sub == "capture":
            if len(args) < 3:
                await message.answer("Использование: <code>/ship capture ИД</code>", parse_mode="HTML")
                return
            try:
                ship_id = int(args[2])
            except ValueError:
                await message.answer("❌ Укажите ID корабля числом.")
                return
            sc = await db.execute(
                "SELECT ts.*, c.name as owner_name, c.emoji as owner_emoji "
                "FROM trade_ships ts JOIN countries c ON c.id=ts.owner_id "
                "WHERE ts.id=? AND ts.chat_id=? AND ts.status='sailing'",
                (ship_id, message.chat.id)
            )
            ship = await sc.fetchone()
            if not ship:
                await message.answer("❌ Корабль не найден, не в пути или уже прибыл.")
                return
            if ship["owner_id"] == country["id"]:
                await message.answer("❌ Нельзя захватить собственный корабль!")
                return

            # Нужен флот для захвата
            mil = await get_military(country["id"])
            navy = mil.get("navy", 0) if mil else 0
            if navy < 5:
                await message.answer(f"❌ Для захвата нужно минимум 5 кораблей флота. У вас: {navy}")
                return

            # Шанс захвата: 40% базово + 2% за каждый корабль флота сверх 5 (макс 80%)
            import random as _rnd2
            capture_chance = min(0.80, 0.40 + (navy - 5) * 0.02)
            if _rnd2.random() < capture_chance:
                # Захват успешен — забираем груз
                res_label = {"food": "🌾 Еда", "production": "⚙️ Производство", "oil": "🛢 Нефть",
                             "rare_metals": "💎 Металлы", "science": "🔬 Наука", "treasury": "💰 Золото"}.get(ship["cargo_res"], "")
                if ship["cargo_res"] == "treasury":
                    await db.execute("UPDATE countries SET treasury=treasury+? WHERE id=?", (ship["cargo_amt"], country["id"]))
                elif ship["cargo_res"]:
                    await db.execute(
                        f"UPDATE resources SET {ship['cargo_res']}=MIN(99999,{ship['cargo_res']}+?) WHERE country_id=?",
                        (ship["cargo_amt"], country["id"])
                    )
                # Корабль тоже захватывается (меняет владельца)
                await db.execute(
                    "UPDATE trade_ships SET owner_id=?, status='idle', cargo_res='', cargo_amt=0, "
                    "from_iso='', to_iso='', depart_at=0, arrive_at=0 WHERE id=?",
                    (country["id"], ship_id)
                )
                await db.execute(
                    "INSERT INTO ship_captures (chat_id, ship_id, captor_id) VALUES (?,?,?)",
                    (message.chat.id, ship_id, country["id"])
                )
                await db.commit()
                await message.answer(
                    f"⚔️ <b>ЗАХВАТ КОРАБЛЯ!</b>\n\n"
                    f"{country['emoji']} <b>{country['name']}</b> захватил «{ship['name']}» "
                    f"({ship['owner_emoji']} {ship['owner_name']})!\n"
                    f"💰 Трофеи: {ship['cargo_amt']} {res_label}\n"
                    f"🚢 Корабль теперь ваш!",
                    parse_mode="HTML"
                )
            else:
                await message.answer(
                    f"💨 Корабль «{ship['name']}» ускользнул! Шанс захвата был {int(capture_chance*100)}%."
                )

        elif sub == "troops":
            # /ship troops ИД ISO_цель количество
            # Морской десант: отправить пехоту атаковать вражескую (или нейтральную) территорию
            TROOPS_MAX = 50
            if len(args) < 5:
                await message.answer(
                    "⚓ <b>Морской десант</b>\n\n"
                    "Отправьте пехоту на кораблях атаковать территорию врага!\n\n"
                    "Использование: <code>/ship troops ИД ISO_цель количество</code>\n"
                    "Пример: <code>/ship troops 1 JP 50</code>\n\n"
                    f"▪ Максимум за рейс: <b>{TROOPS_MAX} пехотинцев</b>\n"
                    "▪ Цель — прибрежная территория (COASTAL)\n"
                    "▪ Нужна активная война с владельцем цели\n"
                    "▪ По прибытии автоматически начнётся бой\n"
                    "▪ Победа → территория захвачена; поражение → пехота погибает",
                    parse_mode="HTML"
                )
                return

            try:
                ship_id = int(args[2])
            except ValueError:
                await message.answer("❌ Укажите ID корабля числом.")
                return
            to_iso = args[3].upper()
            try:
                troops = int(args[4])
                if troops <= 0:
                    raise ValueError
            except ValueError:
                await message.answer("❌ Укажите положительное количество пехоты.")
                return
            if troops > TROOPS_MAX:
                await message.answer(f"❌ Максимум {TROOPS_MAX} пехотинцев за один рейс!")
                return

            if not is_valid_iso(to_iso):
                await message.answer(f"❌ Неизвестный ISO: <code>{to_iso}</code>", parse_mode="HTML")
                return

            # Прибрежная территория — обязательно
            if to_iso not in COASTAL_ISOS:
                await message.answer(
                    f"❌ <b>{COUNTRY_NAMES.get(to_iso, to_iso)}</b> — не прибрежная территория!\n"
                    "Морской десант возможен только на прибрежные земли.",
                    parse_mode="HTML"
                )
                return

            # Нельзя атаковать свою территорию
            our_territories = await get_country_territories(message.chat.id, country["id"])
            if to_iso in our_territories:
                await message.answer("❌ Это уже ваша территория!")
                return

            # Нужна активная война с владельцем территории
            territories_map = await get_territories(message.chat.id)
            owner_cid = territories_map.get(to_iso)
            defender_country = await get_country_by_id(message.chat.id, owner_cid) if owner_cid else None

            if defender_country and defender_country["id"] != country["id"]:
                war = await get_active_war(message.chat.id, country["id"], defender_country["id"])
                if not war:
                    await message.answer(
                        f"❌ Сначала объявите войну: <code>/war {defender_country['name']}</code>",
                        parse_mode="HTML"
                    )
                    return

            # Проверяем корабль
            sc = await db.execute(
                "SELECT * FROM trade_ships WHERE id=? AND owner_id=? AND chat_id=?",
                (ship_id, country["id"], message.chat.id)
            )
            ship = await sc.fetchone()
            if not ship:
                await message.answer("❌ Корабль не найден или не ваш.")
                return
            if ship["status"] != "idle":
                eta_min = max(0, (ship["arrive_at"] - int(__import__('time').time())) // 60)
                await message.answer(f"❌ Корабль в пути, прибудет через ~{eta_min} мин.")
                return

            # Нужен флот для морского десанта (минимум 5 боевых кораблей)
            mil = await get_military(country["id"])
            navy = (mil.get("navy") or 0) if mil else 0
            if navy < 5:
                await message.answer(
                    f"❌ Для морского десанта нужно минимум <b>5 ⚓ кораблей флота</b>.\n"
                    f"У вас: {navy}. Купите флот командой <code>/buy navy количество</code>.",
                    parse_mode="HTML"
                )
                return

            # Проверяем пехоту
            have_infantry = (mil.get("infantry") or 0) if mil else 0
            if have_infantry < troops:
                await message.answer(
                    f"❌ Недостаточно пехоты!\n"
                    f"Нужно: {troops} | В армии: {have_infantry}"
                )
                return

            # Списываем пехоту из армии сразу
            await db.execute(
                "UPDATE military SET infantry=MAX(0, infantry-?) WHERE country_id=?",
                (troops, country["id"])
            )
            # Также списываем из общего army
            await db.execute(
                "UPDATE countries SET army=MAX(0, army-?) WHERE id=?",
                (troops, country["id"])
            )

            from_iso = await get_capital(message.chat.id, country["id"])
            if not from_iso:
                from_iso = our_territories[0] if our_territories else "XX"

            sdef = SHIP_LEVELS[ship["level"]]
            travel_min = _travel_minutes(from_iso, to_iso, sdef["speed_min_per_deg"])
            now = int(__import__('time').time())
            arrive_at = now + travel_min * 60

            await db.execute(
                "UPDATE trade_ships SET status='sailing', from_iso=?, to_iso=?, "
                "cargo_res='assault', cargo_amt=0, troops_amt=?, depart_at=?, arrive_at=? WHERE id=?",
                (from_iso, to_iso, troops, now, arrive_at, ship_id)
            )
            await db.commit()

            to_name   = COUNTRY_NAMES.get(to_iso, to_iso)
            from_name = COUNTRY_NAMES.get(from_iso, from_iso)
            def_str   = f"⚔️ Цель: {defender_country['emoji']} {defender_country['name']}" if defender_country else "🏳 Нейтральная территория"
            await message.answer(
                f"⚓ <b>ДЕСАНТ ОТПЛЫЛ!</b>\n\n"
                f"🚢 «{SHIP_LEVELS[ship['level']]['name']}» везёт "
                f"<b>{troops} 🪖 пехотинцев</b>\n"
                f"📍 {from_name} → <b>{to_name}</b>\n"
                f"{def_str}\n"
                f"⏱ Время в пути: ~{travel_min} мин\n\n"
                f"💥 По прибытии автоматически начнётся <b>морской бой!</b>",
                parse_mode="HTML"
            )

        else:  # list
            sc = await db.execute(
                "SELECT * FROM trade_ships WHERE owner_id=? AND chat_id=? ORDER BY level DESC",
                (country["id"], message.chat.id)
            )
            ships = [dict(r) for r in await sc.fetchall()]
            if not ships:
                lines = [
                    "🚢 <b>Торговый флот</b>\n",
                    "У вас нет торговых кораблей.\n",
                    "Купите корабль: <code>/ship buy [уровень 1-5]</code>\n",
                ]
                for lv, ld in SHIP_LEVELS.items():
                    lines.append(f"  Лв{lv} {ld['name']}: вместимость {ld['capacity']}, цена {ld['cost_gold']}💰")
            else:
                now = int(__import__('time').time())
                lines = [f"🚢 <b>Торговый флот</b> ({len(ships)} кораблей)\n"]
                for s in ships:
                    sname = SHIP_LEVELS[s["level"]]["name"]
                    if s["status"] == "sailing":
                        eta_min = max(0, (s["arrive_at"] - now) // 60)
                        if s.get("cargo_res") == "troops":
                            lines.append(
                                f"⛵ #{s['id']} «{sname}» Лв{s['level']} — В пути (десант)\n"
                                f"   {COUNTRY_NAMES.get(s['from_iso'],s['from_iso'])} → "
                                f"{COUNTRY_NAMES.get(s['to_iso'],s['to_iso'])}\n"
                                f"   Груз: {s.get('troops_amt',0)} 🪖 пехотинцев | Прибытие: ~{eta_min} мин"
                            )
                        else:
                            res_label = {"food": "🌾", "production": "⚙️", "oil": "🛢",
                                         "rare_metals": "💎", "science": "🔬", "treasury": "💰"}.get(s["cargo_res"], "📦")
                            lines.append(
                                f"⛵ #{s['id']} «{sname}» Лв{s['level']} — В пути\n"
                                f"   {COUNTRY_NAMES.get(s['from_iso'],s['from_iso'])} → "
                                f"{COUNTRY_NAMES.get(s['to_iso'],s['to_iso'])}\n"
                                f"   Груз: {s['cargo_amt']} {res_label} | Прибытие: ~{eta_min} мин"
                            )
                    elif s["status"] == "returning":
                        eta_min = max(0, (s["arrive_at"] - now) // 60)
                        lines.append(
                            f"🔄 #{s['id']} «{sname}» Лв{s['level']} — Возвращается\n"
                            f"   {COUNTRY_NAMES.get(s['from_iso'],s['from_iso'])} → "
                            f"{COUNTRY_NAMES.get(s['to_iso'],s['to_iso'])}\n"
                            f"   Прибытие домой: ~{eta_min} мин"
                        )
                    else:
                        lines.append(f"⚓ #{s['id']} «{sname}» Лв{s['level']} — Стоит в порту")
                lines.append("\n<code>/ship send ИД ISO_от ISO_к ресурс количество</code> — отправить груз")
                lines.append("<code>/ship troops ИД ISO_куда количество</code> — перевезти пехоту (макс 50)")
                lines.append("<code>/ship buy [1-5]</code> — купить новый")
                lines.append("<code>/ship capture ИД</code> — захватить вражеский")
            await message.answer("\n".join(lines), parse_mode="HTML")


@router_war.message(Command("bombard"))
async def cmd_bombard(message: Message):
    """
    /bombard ISO — артиллерийская бомбардировка перед атакой.
    Снижает гарнизон противника. Требует артиллерию в пуле.
    """
    if message.chat.type == "private":
        return
    attacker = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not attacker:
        await message.answer("❌ У вас нет страны!")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "💣 <b>Бомбардировка</b>\n\n"
            "Использование: <code>/bombard ISO</code>\n"
            "Пример: <code>/bombard DE</code>\n\n"
            "Артиллерия обстреливает вражескую территорию — снижает гарнизон до атаки.\n"
            "Расход: 10 снарядов артиллерии. Эффект: -5% гарнизона за орудие (макс -50%).",
            parse_mode="HTML"
        )
        return

    target_iso = args[1].upper()
    if not is_valid_iso(target_iso):
        await message.answer(f"❌ Неизвестный ISO: {target_iso}")
        return

    our_territories = await get_country_territories(message.chat.id, attacker["id"])
    if target_iso in our_territories:
        await message.answer("❌ Нельзя бомбардировать свою территорию!")
        return

    # Проверяем наличие артиллерии
    mil = await get_military(attacker["id"])
    artillery = mil.get("artillery", 0) if mil else 0
    if artillery < 10:
        await message.answer(f"❌ Нужно минимум 10 артиллерии. У вас: {artillery}")
        return

    # Используем артиллерию (расход 10 орудий на залп)
    shells = min(artillery, 50)  # макс 50 орудий за раз
    db = await _open_db()
    async with db:
        await db.execute(
            "UPDATE military SET artillery=MAX(0,artillery-?) WHERE country_id=?",
            (shells, attacker["id"])
        )
        # Снижаем гарнизон противника
        reduction = min(0.5, shells * 0.01)  # -1% за орудие, макс -50%
        await db.execute("""
            UPDATE garrisons SET
                infantry  = MAX(0, CAST(infantry  * ? AS INTEGER)),
                cavalry   = MAX(0, CAST(cavalry   * ? AS INTEGER)),
                artillery = MAX(0, CAST(artillery * ? AS INTEGER))
            WHERE chat_id=? AND iso=? AND country_id != ?
        """, (1-reduction, 1-reduction, 1-reduction, message.chat.id, target_iso, attacker["id"]))
        await db.execute(
            "INSERT INTO bombardments (chat_id, attacker_id, target_iso, shells) VALUES (?,?,?,?)",
            (message.chat.id, attacker["id"], target_iso, shells)
        )
        await db.commit()

    ter_name = COUNTRY_NAMES.get(target_iso, target_iso)
    await message.answer(
        f"💣 <b>Бомбардировка!</b>\n\n"
        f"{attacker['emoji']} <b>{attacker['name']}</b> открыл артиллерийский огонь по <b>{ter_name}</b>!\n"
        f"🔫 Использовано орудий: {shells}\n"
        f"💥 Гарнизон противника снижен на {int(reduction*100)}%\n\n"
        f"Теперь атакуйте: <code>/attack {target_iso}</code>",
        parse_mode="HTML"
    )


@router_war.message(Command("score"))
async def cmd_score(message: Message):
    """/score — рейтинг стран по победным очкам."""
    if message.chat.type == "private":
        return
    board = await get_vp_leaderboard(message.chat.id)
    if not board:
        await message.answer("📊 Победных очков ещё нет.")
        return
    lines = ["🏆 <b>Рейтинг победных очков</b>\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(board):
        medal = medals[i] if i < 3 else f"{i+1}."
        mention = f"@{row['owner_username']}" if row.get("owner_username") else ""
        lines.append(f"{medal} {row['emoji']} <b>{row['name']}</b> {mention} — {row['vp']} VP")
    lines.append("\n<i>VP начисляются каждый час: +1 за территорию, +1 за 100 солдат, +1 за 5 уровней зданий.</i>")
    lines.append("<i>+10 VP за захват территории, +50 VP за победу в войне.</i>")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router_war.message(Command("coalition"))
async def cmd_coalition(message: Message):
    """
    /coalition — посмотреть свою коалицию
    /coalition create Название — создать коалицию
    /coalition invite Страна — пригласить страну
    /coalition join Название — вступить в коалицию
    /coalition leave — покинуть коалицию
    /coalition list — все коалиции чата
    """
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны!")
        return

    args = message.text.split(maxsplit=2)
    sub = args[1].lower() if len(args) > 1 else ""

    db = await _open_db()
    async with db:

        # Найти коалицию игрока
        async def get_my_coalition():
            c = await db.execute("""
                SELECT co.* FROM coalitions co
                JOIN coalition_members cm ON cm.coalition_id=co.id
                WHERE cm.country_id=? AND co.chat_id=?
            """, (country["id"], message.chat.id))
            return await c.fetchone()

        if not sub or sub == "info":
            coal = await get_my_coalition()
            if not coal:
                await message.answer(
                    "🤝 <b>Коалиции</b>\n\nВы не состоите в коалиции.\n\n"
                    "<code>/coalition create Название</code> — создать\n"
                    "<code>/coalition join Название</code> — вступить\n"
                    "<code>/coalition list</code> — все коалиции",
                    parse_mode="HTML"
                )
                return
            c2 = await db.execute("""
                SELECT co2.id, co2.name, co2.emoji FROM countries co2
                JOIN coalition_members cm ON cm.country_id=co2.id
                WHERE cm.coalition_id=?
            """, (coal["id"],))
            members = await c2.fetchall()
            member_lines = [f"  {m['emoji']} {m['name']}" for m in members]
            total_vp = 0
            for m in members:
                mc = await db.execute("SELECT vp FROM victory_points WHERE country_id=?", (m["id"],))
                mr = await mc.fetchone()
                total_vp += (mr["vp"] if mr else 0)
            leader_c = await db.execute("SELECT name, emoji FROM countries WHERE id=?", (coal["leader_id"],))
            leader = await leader_c.fetchone()
            await message.answer(
                f"🤝 <b>Коалиция «{coal['name']}»</b>\n"
                f"👑 Лидер: {leader['emoji']} {leader['name']}\n"
                f"🏆 Суммарные VP: {total_vp}\n\n"
                f"<b>Участники ({len(members)}):</b>\n" + "\n".join(member_lines),
                parse_mode="HTML"
            )

        elif sub == "create":
            if len(args) < 3:
                await message.answer("Использование: <code>/coalition create Название</code>", parse_mode="HTML")
                return
            coal_name = args[2].strip()
            if await get_my_coalition():
                await message.answer("❌ Вы уже состоите в коалиции. Сначала покиньте её: /coalition leave")
                return
            try:
                c = await db.execute(
                    "INSERT INTO coalitions (chat_id, name, leader_id) VALUES (?,?,?)",
                    (message.chat.id, coal_name, country["id"])
                )
                coal_id = c.lastrowid
                await db.execute(
                    "INSERT INTO coalition_members (coalition_id, country_id) VALUES (?,?)",
                    (coal_id, country["id"])
                )
                await db.commit()
                await message.answer(
                    f"🤝 Коалиция <b>«{coal_name}»</b> основана!\n"
                    f"Пригласите союзников: <code>/coalition invite СтранаName</code>",
                    parse_mode="HTML"
                )
            except Exception:
                await message.answer("❌ Коалиция с таким названием уже существует.")

        elif sub == "invite":
            if len(args) < 3:
                await message.answer("Использование: <code>/coalition invite СтранаName</code>", parse_mode="HTML")
                return
            coal = await get_my_coalition()
            if not coal or coal["leader_id"] != country["id"]:
                await message.answer("❌ Только лидер коалиции может приглашать.")
                return
            target_name = args[2].strip()
            tc = await db.execute(
                "SELECT id, name, emoji FROM countries WHERE chat_id=? AND name=?",
                (message.chat.id, target_name)
            )
            target = await tc.fetchone()
            if not target:
                await message.answer(f"❌ Страна «{target_name}» не найдена.")
                return
            # Проверяем что цель не в другой коалиции
            ec = await db.execute("""
                SELECT 1 FROM coalition_members cm
                JOIN coalitions co ON co.id=cm.coalition_id
                WHERE cm.country_id=? AND co.chat_id=?
            """, (target["id"], message.chat.id))
            if await ec.fetchone():
                await message.answer(f"❌ {target['name']} уже состоит в коалиции.")
                return
            await db.execute(
                "INSERT OR IGNORE INTO coalition_members (coalition_id, country_id) VALUES (?,?)",
                (coal["id"], target["id"])
            )
            await db.commit()
            await message.answer(
                f"✅ {target['emoji']} <b>{target['name']}</b> добавлена в коалицию <b>«{coal['name']}»</b>!",
                parse_mode="HTML"
            )

        elif sub == "join":
            if len(args) < 3:
                await message.answer("Использование: <code>/coalition join Название</code>", parse_mode="HTML")
                return
            if await get_my_coalition():
                await message.answer("❌ Вы уже состоите в коалиции.")
                return
            coal_name = args[2].strip()
            cc = await db.execute(
                "SELECT * FROM coalitions WHERE chat_id=? AND name=?",
                (message.chat.id, coal_name)
            )
            coal = await cc.fetchone()
            if not coal:
                await message.answer(f"❌ Коалиция «{coal_name}» не найдена. /coalition list")
                return
            await db.execute(
                "INSERT OR IGNORE INTO coalition_members (coalition_id, country_id) VALUES (?,?)",
                (coal["id"], country["id"])
            )
            await db.commit()
            await message.answer(f"✅ Вы вступили в коалицию <b>«{coal['name']}»</b>!", parse_mode="HTML")

        elif sub == "leave":
            coal = await get_my_coalition()
            if not coal:
                await message.answer("❌ Вы не состоите в коалиции.")
                return
            await db.execute(
                "DELETE FROM coalition_members WHERE coalition_id=? AND country_id=?",
                (coal["id"], country["id"])
            )
            # Если лидер ушёл — передаём лидерство или удаляем коалицию
            if coal["leader_id"] == country["id"]:
                nc = await db.execute(
                    "SELECT country_id FROM coalition_members WHERE coalition_id=? LIMIT 1",
                    (coal["id"],)
                )
                new_leader = await nc.fetchone()
                if new_leader:
                    await db.execute(
                        "UPDATE coalitions SET leader_id=? WHERE id=?",
                        (new_leader["country_id"], coal["id"])
                    )
                else:
                    await db.execute("DELETE FROM coalitions WHERE id=?", (coal["id"],))
            await db.commit()
            await message.answer(f"✅ Вы покинули коалицию «{coal['name']}».")

        elif sub == "list":
            lc = await db.execute("""
                SELECT co.name, co.leader_id, COUNT(cm.country_id) as cnt
                FROM coalitions co
                LEFT JOIN coalition_members cm ON cm.coalition_id=co.id
                WHERE co.chat_id=?
                GROUP BY co.id
            """, (message.chat.id,))
            coals = await lc.fetchall()
            if not coals:
                await message.answer("Коалиций пока нет. Создайте: <code>/coalition create Название</code>", parse_mode="HTML")
                return
            lines = ["🤝 <b>Коалиции чата:</b>\n"]
            for co in coals:
                ldr = await db.execute("SELECT name, emoji FROM countries WHERE id=?", (co["leader_id"],))
                ldr_row = await ldr.fetchone()
                lines.append(f"• <b>«{co['name']}»</b> — {co['cnt']} стран, лидер: {ldr_row['emoji']} {ldr_row['name']}")
            await message.answer("\n".join(lines), parse_mode="HTML")

        else:
            await message.answer(
                "🤝 <b>Коалиции</b>\n\n"
                "<code>/coalition</code> — ваша коалиция\n"
                "<code>/coalition create Название</code> — создать\n"
                "<code>/coalition invite Страна</code> — пригласить\n"
                "<code>/coalition join Название</code> — вступить\n"
                "<code>/coalition leave</code> — покинуть\n"
                "<code>/coalition list</code> — все коалиции",
                parse_mode="HTML"
            )


@router_war.message(Command("expand"))
async def cmd_expand(message: Message):
    if message.chat.type == "private":
        return
    my = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not my:
        await message.answer("❌ У вас нет страны!")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "Использование: <code>/expand ISO</code>\nПример: <code>/expand KZ</code>",
            parse_mode="HTML"
        )
        return

    target_iso = args[1].upper()
    if not is_valid_iso(target_iso):
        await message.answer(f"❌ Неизвестный ISO: {target_iso}")
        return

    our_territories = await get_country_territories(message.chat.id, my["id"])

    if target_iso in our_territories:
        await message.answer("❌ Эта территория уже ваша!")
        return

    # BUG FIX: проверяем смежность корректно
    if not any(is_neighbor(ours, target_iso) for ours in our_territories):
        await message.answer(
            f"❌ <b>{COUNTRY_NAMES.get(target_iso, target_iso)}</b> "
            "не граничит с вашими землями!",
            parse_mode="HTML"
        )
        return

    territories = await get_territories(message.chat.id)
    if target_iso in territories:
        owner = await get_country_by_id(message.chat.id, territories[target_iso])
        if owner:
            await message.answer(
                f"❌ Территория принадлежит {make_country_str(owner)} {make_owner_mention(owner)}!\n"
                "Для захвата объявите войну: /war и /attack.",
                parse_mode="HTML"
            )
        else:
            await message.answer("❌ Территория занята. Используйте /war и /attack.")
        return

    # Кулдаун на /expand: раньше его не было вовсе, и игроки могли захватывать
    # территории подряд без пауз — это и было основной причиной того, что
    # первые активные игроки захватывали половину карты за час.
    # BUG FIX: занимаем кулдаун атомарно ДО любых проверок и списаний.
    # Раньше проверка и установка были двумя транзакциями, и двойное нажатие
    # /expand захватывало две территории по цене одной.
    _owned_now = len(our_territories)
    cd_expand = await try_claim_cooldown(
        message.from_user.id, message.chat.id, "expand", 90 + _owned_now * 8
    )
    if cd_expand > 0:
        m, s = divmod(cd_expand, 60)
        await message.answer(f"⏳ Следующая экспансия через {m}м {s}с", parse_mode="HTML")
        return

    # Растущая цена: раньше было фиксированно 100💰/20 солдат независимо от
    # размера империи, из-за чего с ростом дохода экспансия становилась ВСЁ
    # ДЕШЕВЛЕ относительно казны. Теперь цена растёт с числом уже имеющихся
    # территорий — классический механизм сдерживания в 4X-играх.
    owned_count = len(our_territories)
    COST_GOLD = int(100 * (1 + owned_count * 0.18))
    COST_ARMY = int(20 * (1 + owned_count * 0.12))

    # Свежие данные
    my_fresh = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not my_fresh:
        await clear_cooldown(message.from_user.id, message.chat.id, "expand")
        await message.answer("❌ Страна не найдена.")
        return

    # BUG FIX: платим атомарно. Если денег или солдат не хватило — снимаем
    # кулдаун, чтобы неудачная попытка не съедала ход, и откатываем золото.
    if not await spend_treasury(my["id"], COST_GOLD):
        await clear_cooldown(message.from_user.id, message.chat.id, "expand")
        await message.answer(f"❌ Нужно {COST_GOLD}💰 (у вас {my_fresh['treasury']}💰)", parse_mode="HTML")
        return
    if not await spend_army(my["id"], COST_ARMY):
        await update_treasury(my["id"], COST_GOLD)   # возвращаем деньги
        await clear_cooldown(message.from_user.id, message.chat.id, "expand")
        await message.answer(f"❌ Нужно {COST_ARMY} солдат (у вас {my_fresh['army']})")
        return

    await capture_territory(message.chat.id, target_iso, my["id"])
    # Часть войск (5) переходит в гарнизон новой территории, остальные теряются (дорога)
    garrison_for_new = min(5, COST_ARMY)
    if garrison_for_new > 0:
        _edb = await _open_db()
        async with _edb:
            await _edb.execute(
                """INSERT INTO garrisons (chat_id, country_id, iso, infantry, updated_at)
                   VALUES (?,?,?,?,strftime('%s','now'))
                   ON CONFLICT(chat_id, country_id, iso)
                   DO UPDATE SET infantry=infantry+?, updated_at=strftime('%s','now')""",
                (message.chat.id, my["id"], target_iso, garrison_for_new, garrison_for_new)
            )
            await _edb.commit()
    await log_event(message.chat.id, "territory_expanded", {
        "country": my["name"], "emoji": my["emoji"],
        "territory": COUNTRY_NAMES.get(target_iso, target_iso), "iso": target_iso,
    })
    ter_name_exp = COUNTRY_NAMES.get(target_iso, target_iso)
    await message.answer(
        f"🚩 {make_country_str(my)} расширила владения!\n\n"
        f"Захвачена территория: <b>{ter_name_exp}</b>\n"
        f"Потрачено: {COST_GOLD}💰, {COST_ARMY} солдат",
        parse_mode="HTML"
    )


@router_war.message(Command("status"))
async def cmd_status(message: Message):
    """Полная сводка по своей стране + активные войны чата."""
    if message.chat.type == "private":
        return

    import time as _t
    now = int(_t.time())
    chat_id = message.chat.id

    country = await get_country_by_owner(chat_id, message.from_user.id)

    # ── Сводка по своей стране ─────────────────────────────────
    if country:
        cid       = country["id"]
        res       = await get_resources(cid)
        weariness = await get_weariness(cid)
        wear_lbl, wear_pen, _ = weariness_info(weariness)

        happiness = res["happiness"] if res else 0
        if happiness >= 75:
            hap_icon = "😊"
        elif happiness >= 50:
            hap_icon = "😐"
        elif happiness >= 25:
            hap_icon = "😟"
        else:
            hap_icon = "😡"

        # Состав армии из таблицы military
        db = await _open_db()
        async with db:
            mc = await db.execute(
                "SELECT * FROM military WHERE country_id=?", (cid,)
            )
            mil = await mc.fetchone()
            # BUG FIX: раньше запрос обращался к несуществующим колонкам
            # territories.owner_id и territories.is_capital (в схеме есть
            # только territories.country_id, а столица хранится в
            # countries.capital_iso) — команда падала с SQL-ошибкой.
            ter_c = await db.execute(
                "SELECT COUNT(*) FROM territories WHERE country_id=? AND chat_id=?",
                (cid, chat_id)
            )
            ter_count = (await ter_c.fetchone())[0]
            cap_c = await db.execute(
                "SELECT capital_iso AS iso FROM countries WHERE id=? AND chat_id=?",
                (cid, chat_id)
            )
            cap_row = await cap_c.fetchone()
            if cap_row and not cap_row["iso"]:
                cap_row = None

        capital_str = f"🏛 Столица: <b>{COUNTRY_NAMES.get(cap_row['iso'], cap_row['iso'])}</b>\n" if cap_row else ""

        mil_parts = []
        if mil:
            for utype, udef in UNIT_DEFS.items():
                val = mil[utype] if mil[utype] else 0
                if val > 0:
                    mil_parts.append(f"{udef['emoji']}{val}")
        mil_str = "  " + "  ".join(mil_parts) if mil_parts else "  —"

        wear_bar = "▓" * (weariness // 10) + "░" * (10 - weariness // 10)
        hap_bar  = "▓" * (happiness // 10) + "░" * (10 - happiness // 10)

        lines = [
            f"{country['emoji']} <b>{country['name']}</b>  [{country['iso']}]",
            f"{capital_str}"
            f"🌍 Территорий: <b>{ter_count}</b>   💰 Казна: <b>{country['treasury']}</b>",
            f"",
            f"{hap_icon} Счастье:  {hap_bar} {happiness}/100",
            f"😩 Усталость: {wear_bar} {weariness}/100 — <i>{wear_lbl}</i>",
            f"",
            f"⚔️ Армия: <b>{country['army']}</b> общая сила",
            mil_str,
        ]
        if wear_pen > 0:
            lines.append(f"⚠️ Штраф дезертирства: -{int(wear_pen*100)}%/тик")

        await message.answer("\n".join(lines), parse_mode="HTML")

    # ── Активные войны чата ────────────────────────────────────
    wars = await get_active_wars(chat_id)
    if not wars:
        if not country:
            await message.answer("❌ У вас нет страны.")
        return

    war_lines = ["\n⚔️ <b>Активные войны</b>\n"]
    for war in wars:
        att  = await get_country_by_id(chat_id, war["attacker_id"])
        defe = await get_country_by_id(chat_id, war["defender_id"])
        if not att or not defe:
            continue

        db = await _open_db()
        async with db:
            sc = await db.execute(
                "SELECT attacker_score, defender_score, attacker_losses, defender_losses FROM wars WHERE id=?",
                (war["id"],)
            )
            sc_row = dict(await sc.fetchone() or {})

        att_sc   = sc_row.get("attacker_score", 0)
        def_sc   = sc_row.get("defender_score", 0)
        att_loss = sc_row.get("attacker_losses", 0)
        def_loss = sc_row.get("defender_losses", 0)

        started      = war.get("started_at", now)
        duration_min = max(0, (now - started) // 60)
        dur_str      = f"{duration_min // 60}ч {duration_min % 60}м" if duration_min >= 60 else f"{duration_min}м"

        my_id        = country["id"] if country else -1
        is_attacker  = war["attacker_id"] == my_id
        is_involved  = my_id in (war["attacker_id"], war["defender_id"])
        peace_hint   = ""
        if is_involved:
            peace_target = defe["name"] if is_attacker else att["name"]
            peace_hint   = f"\n  ➡️ /peace {peace_target}"

        war_lines.append(
            f"{att['emoji']} <b>{att['name']}</b> ⚔️ {defe['emoji']} <b>{defe['name']}</b>\n"
            f"  Очки: {att_sc} — {def_sc}   Потери: {att_loss}/{def_loss}\n"
            f"  Армии: {att['army']} | {defe['army']}   ⏱ {dur_str}"
            f"{peace_hint}"
        )

    await message.answer("\n".join(war_lines), parse_mode="HTML")


@router_war.message(Command("peace"))
async def cmd_peace(message: Message):
    if message.chat.type == "private":
        return
    my = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not my:
        await message.answer("❌ У вас нет страны!")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: <code>/peace ИмяСтраны</code>", parse_mode="HTML")
        return

    q = args[1].strip()
    enemy = await get_country_by_iso(message.chat.id, q)
    if not enemy:
        for c in await get_all_countries(message.chat.id):
            if c["name"].lower() == q.lower():
                enemy = c; break
    if not enemy:
        await message.answer(f"❌ Страна «{q}» не найдена.")
        return

    war = await get_active_war(message.chat.id, my["id"], enemy["id"])
    if not war:
        await message.answer("❌ Вы не воюете с этой страной.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        colored_button("✅ Принять мир", "green",
            callback_data=f"peace_accept:{war['id']}:{my['id']}:{enemy['id']}"),
        colored_button("❌ Отказать", "red", callback_data=f"peace_decline:{enemy['id']}"),
    ]])
    await message.answer(
        f"🕊 {my['emoji']} <b>{my['name']}</b> предлагает мир "
        f"{enemy['emoji']} <b>{enemy['name']}</b>!\n\n"
        f"<i>Владелец {enemy['emoji']} {enemy['name']}, примите или отклоните.</i>",
        parse_mode="HTML", reply_markup=kb
    )


@router_war.callback_query(F.data.startswith("peace_accept:"))
async def peace_accept(callback: CallbackQuery):
    parts       = callback.data.split(":")
    war_id      = int(parts[1])
    proposer_id = int(parts[2])
    enemy_id    = int(parts[3])

    my = await get_country_by_owner(callback.message.chat.id, callback.from_user.id)
    if not my or my["id"] not in (proposer_id, enemy_id):
        await callback.answer("❌ Это предложение не для вас!", show_alert=True); return
    if my["id"] == proposer_id:
        await callback.answer("❌ Вы предложили мир — ждите ответа!", show_alert=True); return

    p = await get_country_by_id(callback.message.chat.id, proposer_id)
    e = await get_country_by_id(callback.message.chat.id, enemy_id)
    if not p or not e:
        await callback.answer("❌ Одна из стран больше не существует.", show_alert=True); return

    db = await _open_db()
    async with db:
        # BUG FIX: раньше война закрывалась безусловно по одному war_id, без
        # привязки к чату и без проверки статуса. Старая кнопка «Принять мир»
        # закрывала уже завершённую (или заново начатую) войну повторно.
        _cur = await db.execute(
            "UPDATE wars SET status='ended', ended_at=? "
            "WHERE id=? AND chat_id=? AND status='active'",
            (int(time.time()), war_id, callback.message.chat.id)
        )
        await db.commit()
        if (_cur.rowcount or 0) == 0:
            await callback.answer("❌ Эта война уже завершена.", show_alert=True)
            return

    await log_event(callback.message.chat.id, "peace_signed", {
        "country_a": p["name"], "emoji_a": p["emoji"],
        "country_b": e["name"], "emoji_b": e["emoji"],
    })
    await callback.message.edit_text(
        f"🕊 <b>МИР ЗАКЛЮЧЁН!</b>\n\n"
        f"{make_country_str(p)} {make_owner_mention(p)} и "
        f"{make_country_str(e)} {make_owner_mention(e)} прекратили войну.",
        parse_mode="HTML"
    )
    await callback.answer()


@router_war.callback_query(F.data.startswith("peace_decline:"))
async def peace_decline(callback: CallbackQuery):
    """BUG FIX: проверяем что нажимает именно защитник (enemy_id)."""
    enemy_id = int(callback.data.split(":")[1])
    my = await get_country_by_owner(callback.message.chat.id, callback.from_user.id)
    if not my or my["id"] != enemy_id:
        await callback.answer("❌ Это не ваше решение!", show_alert=True); return
    await callback.message.edit_text(
        f"❌ {my['emoji']} <b>{my['name']}</b> отклонил предложение о мире!\nВойна продолжается.",
        parse_mode="HTML"
    )
    await callback.answer()


@router_war.message(Command("wars"))
async def cmd_wars(message: Message):
    if message.chat.type == "private":
        return
    wars = await get_active_wars(message.chat.id)
    if not wars:
        await message.answer("☮️ Войн нет.")
        return
    lines = ["⚔️ <b>Активные войны:</b>\n"]
    for war in wars:
        a = await get_country_by_id(message.chat.id, war["attacker_id"])
        d = await get_country_by_id(message.chat.id, war["defender_id"])
        if a and d:
            lines.append(f"• {a['emoji']} <b>{a['name']}</b> ⚔️ {d['emoji']} <b>{d['name']}</b>")
    await message.answer("\n".join(lines), parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════
#  HANDLERS — ECONOMY & DIPLOMACY
# ═══════════════════════════════════════════════════════════════

router_economy = Router()


async def send_leaderboard(message: Message, chat_id: int):
    board = await get_leaderboard(chat_id)
    if not board:
        await message.answer("🏆 Рейтинг пуст — стран ещё нет.")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines  = ["🏆 <b>Рейтинг стран</b>\n"]
    for i, c in enumerate(board):
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(
            f"{medal} {c['emoji']} <b>{c['name']}</b>\n"
            f"   🌍 {c['ter_count']} терр. | 💰 {c['treasury']} | ⚔️ {c['army']}"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router_economy.message(Command("tax"))
async def cmd_tax(message: Message):
    if message.chat.type == "private":
        return
    ok, amount, msg = await collect_taxes(message.chat.id, message.from_user.id)
    await message.answer(f"{'💰' if ok else '⏳'} {msg}")


@router_economy.message(Command("buy"))
async def cmd_buy(message: Message):
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны! /create")
        return

    args = message.text.split()
    res = await get_resources(country["id"])
    prod = res["production"] if res else 0

    if len(args) < 3:
        lines = [
            f"🏪 <b>Военный магазин — {country['emoji']} {country['name']}</b>",
            f"💰 Казна: <b>{country['treasury']}</b> | ⚙️ Производство: <b>{prod}</b>\n",
            "<b>Доступные войска:</b>",
        ]
        for utype, udef in UNIT_DEFS.items():
            cost_str = f"{udef['cost_gold']}💰"
            if udef.get("cost_prod", 0):
                cost_str += f" + {udef['cost_prod']}⚙️"
            lines.append(f"• {udef['emoji']} <b>{udef['name']}</b> (<code>{utype}</code>): {cost_str}/ед.")
        lines.append("\nИспользование: <code>/buy тип количество</code>")
        lines.append("Пример: <code>/buy bomber 5</code> или <code>/buy танки 2</code>")
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    unit_type = " ".join(args[1:-1]).lower()
    try:
        amount = int(args[-1])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите положительное число!")
        return

    ok, msg = await buy_units(message.chat.id, message.from_user.id, unit_type, amount)
    await message.answer(("✅ " if ok else "❌ ") + msg)


@router_economy.message(Command("give"))
async def cmd_give(message: Message):
    """Безвозмездная передача ресурсов или войск другой стране.
    Использование: /give страна тип количество
    Типы: food, production, rare_metals, oil, science, treasury (золото),
          infantry, cavalry, artillery, airforce, bomber, navy
    """
    if message.chat.type == "private":
        return
    sender = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not sender:
        await message.answer("❌ У вас нет страны!")
        return

    args = message.text.split()
    if len(args) < 4:
        res_list = ", ".join(list(RESOURCE_NAMES.keys()) + ["treasury"] + list(MILITARY_TRADE_NAMES.keys()))
        await message.answer(
            "🎁 <b>Передача ресурсов</b>\n\n"
            "Использование: <code>/give страна тип количество</code>\n\n"
            f"<b>Типы ресурсов:</b> <code>food</code>, <code>production</code>, "
            f"<code>rare_metals</code>, <code>oil</code>, <code>science</code>, <code>treasury</code>\n"
            f"<b>Войска:</b> <code>infantry</code>, <code>cavalry</code>, <code>artillery</code>, "
            f"<code>airforce</code>, <code>bomber</code>, <code>navy</code>\n\n"
            "Пример: <code>/give США food 500</code>\n"
            "Пример: <code>/give Россия infantry 200</code>",
            parse_mode="HTML"
        )
        return

    # Поддержка многословных названий: /give Красный Дракон food 500
    # Последний аргумент = число, предпоследний = тип, остальное = имя страны
    try:
        amount = int(args[-1])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Укажите положительное число!")
        return
    resource    = args[-2].lower()
    target_name = " ".join(args[1:-2])
    if not target_name:
        await message.answer("❌ Укажите название страны!", parse_mode="HTML")
        return

    # Нормализуем псевдонимы войск
    RU_ALIASES = {
        "золото": "treasury", "монеты": "treasury", "деньги": "treasury",
        "еда": "food", "производство": "production", "металлы": "rare_metals",
        "нефть": "oil", "наука": "science",
        "пехота": "infantry", "кавалерия": "cavalry", "артиллерия": "artillery",
        "авиация": "airforce", "бомберы": "bomber", "флот": "navy",
    }
    resource = RU_ALIASES.get(resource, resource)

    # Определяем таблицу
    is_treasury  = (resource == "treasury")
    is_resource  = resource in RESOURCE_NAMES
    is_military  = resource in MILITARY_TRADE_NAMES
    if not (is_treasury or is_resource or is_military):
        # SECURITY FIX: экранируем пользовательский ввод (HTML-инъекция)
        await message.answer(f"❌ Неизвестный тип: <code>{_html.escape(resource)}</code>", parse_mode="HTML")
        return

    # Ищем получателя (точное совпадение → частичное)
    all_countries = await get_all_countries(message.chat.id)
    receiver = next((c for c in all_countries
                     if c["name"].lower() == target_name.lower()
                     or c["iso"].upper() == target_name.upper()), None)
    if not receiver:
        receiver = next((c for c in all_countries
                         if target_name.lower() in c["name"].lower()), None)
    if not receiver:
        names = ", ".join(f"{c['emoji']} {c['name']}" for c in all_countries)
        # SECURITY FIX: экранируем пользовательский ввод (HTML-инъекция)
        await message.answer(f"❌ Страна «{_html.escape(target_name)}» не найдена.\nДоступные: {names}", parse_mode="HTML")
        return
    if receiver["id"] == sender["id"]:
        await message.answer("❌ Нельзя передавать самому себе!")
        return

    # Нельзя передавать ресурсы/войска стране, с которой идёт война
    active_war = await get_active_war(message.chat.id, sender["id"], receiver["id"])
    if active_war:
        await message.answer(
            f"❌ Вы в состоянии войны с {receiver['emoji']} <b>{receiver['name']}</b>!\n"
            "Передача ресурсов врагу запрещена.",
            parse_mode="HTML"
        )
        return

    # Проверяем наличие и списываем
    db = await _open_db()
    async with db:
        if is_treasury:
            # BUG FIX: проверка и списание одним оператором — иначе два
            # параллельных /give дарили одну и ту же сумму дважды.
            _cur = await db.execute(
                "UPDATE countries SET treasury=treasury-? WHERE id=? AND treasury>=?",
                (amount, sender["id"], amount)
            )
            if (_cur.rowcount or 0) == 0:
                _hc = await db.execute("SELECT treasury FROM countries WHERE id=?", (sender["id"],))
                _hr = await _hc.fetchone()
                await message.answer(f"❌ Недостаточно золота: {_hr['treasury'] if _hr else 0}💰")
                return
            await db.execute("UPDATE countries SET treasury=treasury+? WHERE id=?", (amount, receiver["id"]))
            type_str = f"{amount}💰 золота"

        elif is_resource:
            # BUG FIX: MAX(0, ...) молча обрезал списание, поэтому при гонке
            # получатель получал полную сумму, а у отправителя уходило меньше.
            _cur = await db.execute(
                f"UPDATE resources SET {resource}={resource}-? WHERE country_id=? AND {resource}>=?",
                (amount, sender["id"], amount)
            )
            if (_cur.rowcount or 0) == 0:
                c = await db.execute(f"SELECT {resource} FROM resources WHERE country_id=?", (sender["id"],))
                row = await c.fetchone()
                have = row[resource] if row else 0
                rname = RESOURCE_NAMES[resource]
                await message.answer(f"❌ Недостаточно {rname}: у вас {have}")
                return
            await db.execute(
                f"UPDATE resources SET {resource}={resource}+? WHERE country_id=?",
                (amount, receiver["id"])
            )
            type_str = f"{amount} {RESOURCE_NAMES[resource]}"

        else:  # military — передаём из пула military получателю в пул
            _cur = await db.execute(
                f"UPDATE military SET {resource}={resource}-? WHERE country_id=? AND {resource}>=?",
                (amount, sender["id"], amount)
            )
            if (_cur.rowcount or 0) == 0:
                m_c = await db.execute(f"SELECT {resource} FROM military WHERE country_id=?", (sender["id"],))
                m_row = await m_c.fetchone()
                have_units = m_row[resource] if m_row else 0
                mname = MILITARY_TRADE_NAMES[resource]
                await message.answer(f"❌ Недостаточно {mname} в пуле: у вас {have_units}, нужно {amount}")
                return
            await db.execute(
                f"UPDATE military SET {resource}={resource}+? WHERE country_id=?",
                (amount, receiver["id"])
            )
            await db.execute(
                "UPDATE countries SET army=MAX(0,army-?) WHERE id=?",
                (amount, sender["id"])
            )
            await db.execute(
                "UPDATE countries SET army=army+? WHERE id=?",
                (amount, receiver["id"])
            )
            type_str = f"{amount} {MILITARY_TRADE_NAMES[resource]}"

        await db.commit()

    await message.answer(
        f"🎁 {sender['emoji']} <b>{sender['name']}</b> передал(а) "
        f"{receiver['emoji']} <b>{receiver['name']}</b>:\n"
        f"  ✅ {type_str}",
        parse_mode="HTML"
    )


@router_economy.message(Command("capital"))
async def cmd_capital(message: Message):
    """Показать или изменить столицу страны. /capital [ISO]"""
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны!")
        return

    args = message.text.split()
    capital = (country.get("capital_iso") or country["iso"]).upper()

    if len(args) == 1:
        cap_name = COUNTRY_NAMES.get(capital, capital)
        await message.answer(
            f"🏯 <b>Столица {country['emoji']} {country['name']}</b>\n\n"
            f"Текущая столица: <b>{cap_name}</b> (<code>{capital}</code>)\n\n"
            "⚠️ Захват столицы врагом переводит <b>все ваши здания</b> победителю!\n\n"
            "Чтобы перенести столицу: <code>/capital ISO</code>\n"
            "Пример: <code>/capital RU</code>",
            parse_mode="HTML"
        )
        return

    new_iso = args[1].upper()
    if not is_valid_iso(new_iso):
        await message.answer(f"❌ Неизвестный ISO: {new_iso}")
        return

    ok = await set_capital(message.chat.id, country["id"], new_iso)
    if not ok:
        await message.answer(
            f"❌ Территория <b>{COUNTRY_NAMES.get(new_iso, new_iso)}</b> вам не принадлежит!\n"
            "Столица должна быть на вашей территории.",
            parse_mode="HTML"
        )
        return

    old_cap_name = COUNTRY_NAMES.get(capital, capital)
    new_cap_name = COUNTRY_NAMES.get(new_iso, new_iso)
    await message.answer(
        f"🏯 <b>Столица перенесена!</b>\n\n"
        f"{country['emoji']} {country['name']}\n"
        f"Было: <b>{old_cap_name}</b>\n"
        f"Стало: <b>{new_cap_name}</b> (<code>{new_iso}</code>)\n\n"
        "⚠️ Помните: захват столицы врагом передаёт <b>все ваши здания</b> захватчику!",
        parse_mode="HTML"
    )



# ═══════════════════════════════════════════════════════════════
#  HANDLERS — ПЕРЕДАЧА / ТОРГОВЛЯ ТЕРРИТОРИЯМИ
# ═══════════════════════════════════════════════════════════════

@router_economy.message(Command("giveterr"))
async def cmd_giveterr(message: Message):
    """/giveterr ISO Название_страны — безвозмездно передать территорию"""
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны!")
        return
    args = message.text.split()
    if len(args) < 3:
        await message.answer(
            "Использование: <code>/giveterr ISO НазваниеСтраны</code>\n"
            "Пример: <code>/giveterr DE Великобритания</code>",
            parse_mode="HTML"
        )
        return
    iso = args[1].upper()
    target_name = " ".join(args[2:])

    # Проверяем что территория наша
    our_territories = await get_country_territories(message.chat.id, country["id"])
    if iso not in our_territories:
        await message.answer(f"❌ Территория <b>{COUNTRY_NAMES.get(iso, iso)}</b> вам не принадлежит!", parse_mode="HTML")
        return

    # Нельзя отдать стартовую/столицу если это единственная территория
    capital = await get_capital(message.chat.id, country["id"])
    if iso == capital and len(our_territories) == 1:
        await message.answer("❌ Нельзя отдать единственную (столичную) территорию!")
        return

    # Найти получателя
    receiver = None
    for c in await get_all_countries(message.chat.id):
        if c["name"].lower() == target_name.lower() or c["iso"] == target_name.upper():
            receiver = c; break
    if not receiver:
        await message.answer(f"❌ Страна «{target_name}» не найдена.")
        return
    if receiver["id"] == country["id"]:
        await message.answer("❌ Нельзя передать территорию самому себе!")
        return

    # Передаём территорию
    db = await _open_db()
    async with db:
        await db.execute(
            """INSERT INTO territories (chat_id, iso, country_id) VALUES (?,?,?)
               ON CONFLICT(chat_id, iso) DO UPDATE SET country_id=excluded.country_id""",
            (message.chat.id, iso, receiver["id"])
        )
        # Убираем с рынка если была выставлена
        await db.execute(
            "DELETE FROM territory_market WHERE chat_id=? AND iso=?",
            (message.chat.id, iso)
        )
        # Гарнизон прежнего владельца: войска возвращаются ему в пул (нельзя передавать чужие войска)
        await db.execute(
            "SELECT * FROM garrisons WHERE chat_id=? AND iso=? AND country_id=?",
            (message.chat.id, iso, country["id"])
        )
        gar_c = await db.execute(
            "SELECT * FROM garrisons WHERE chat_id=? AND iso=? AND country_id=?",
            (message.chat.id, iso, country["id"])
        )
        gar_row = await gar_c.fetchone()
        if gar_row:
            returning = sum(gar_row[u] for u in UNIT_TYPES if u in dict(gar_row))
            if returning > 0:
                await db.execute(
                    "UPDATE countries SET army=army+? WHERE id=?",
                    (returning, country["id"])
                )
                for u in UNIT_TYPES:
                    cnt = gar_row[u] if u in dict(gar_row) and gar_row[u] else 0
                    if cnt > 0:
                        await db.execute(
                            f"UPDATE military SET {u}={u}+? WHERE country_id=?",
                            (cnt, country["id"])
                        )
            await db.execute(
                "DELETE FROM garrisons WHERE chat_id=? AND iso=? AND country_id=?",
                (message.chat.id, iso, country["id"])
            )
        await db.commit()

    ter_name = COUNTRY_NAMES.get(iso, iso)
    await message.answer(
        f"🤝 <b>Территория передана!</b>\n\n"
        f"{country['emoji']} <b>{country['name']}</b> безвозмездно отдаёт\n"
        f"🗺 <b>{ter_name}</b> (<code>{iso}</code>)\n"
        f"→ {receiver['emoji']} <b>{receiver['name']}</b>",
        parse_mode="HTML"
    )
    await log_event(message.chat.id, "territory_gift",
                    f"{country['name']} подарил {ter_name} → {receiver['name']}")


@router_economy.message(Command("sellterr"))
async def cmd_sellterr(message: Message):
    """/sellterr ISO цена — выставить территорию на продажу"""
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны!")
        return
    args = message.text.split()
    if len(args) < 3:
        await message.answer(
            "Использование: <code>/sellterr ISO цена</code>\n"
            "Пример: <code>/sellterr DE 500</code>\n"
            "Цена 0 = бесплатно (только с /buyterr)\n\n"
            "Отменить продажу: <code>/sellterr ISO отмена</code>",
            parse_mode="HTML"
        )
        return
    iso = args[1].upper()

    # Отмена выставления
    if args[2].lower() in ("отмена", "cancel", "снять"):
        our_territories = await get_country_territories(message.chat.id, country["id"])
        if iso not in our_territories:
            await message.answer("❌ Эта территория вам не принадлежит.")
            return
        db = await _open_db()
        async with db:
            await db.execute(
                "DELETE FROM territory_market WHERE chat_id=? AND iso=? AND seller_id=?",
                (message.chat.id, iso, country["id"])
            )
            await db.commit()
        await message.answer(f"✅ Территория <b>{COUNTRY_NAMES.get(iso, iso)}</b> снята с продажи.", parse_mode="HTML")
        return

    try:
        price = int(args[2])
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Укажите неотрицательное число в качестве цены.")
        return

    our_territories = await get_country_territories(message.chat.id, country["id"])
    if iso not in our_territories:
        await message.answer(f"❌ Территория <b>{COUNTRY_NAMES.get(iso, iso)}</b> вам не принадлежит!", parse_mode="HTML")
        return

    capital = await get_capital(message.chat.id, country["id"])
    if iso == capital and len(our_territories) == 1:
        await message.answer("❌ Нельзя продать единственную (столичную) территорию!")
        return

    db = await _open_db()
    async with db:
        await db.execute(
            """INSERT INTO territory_market (chat_id, iso, seller_id, price) VALUES (?,?,?,?)
               ON CONFLICT(chat_id, iso) DO UPDATE SET seller_id=excluded.seller_id, price=excluded.price, created_at=strftime('%s','now')""",
            (message.chat.id, iso, country["id"], price)
        )
        await db.commit()

    ter_name = COUNTRY_NAMES.get(iso, iso)
    price_str = f"{price}💰" if price > 0 else "бесплатно"
    await message.answer(
        f"🏷 <b>Территория выставлена на продажу!</b>\n\n"
        f"🗺 <b>{ter_name}</b> (<code>{iso}</code>)\n"
        f"Продавец: {country['emoji']} {country['name']}\n"
        f"💰 Цена: <b>{price_str}</b>\n\n"
        f"Купить: <code>/buyterr {iso}</code>\n"
        f"Снять с продажи: <code>/sellterr {iso} отмена</code>",
        parse_mode="HTML"
    )


@router_economy.message(Command("buyterr"))
async def cmd_buyterr(message: Message):
    """/buyterr ISO — купить территорию с рынка"""
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны!")
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "Использование: <code>/buyterr ISO</code>\n"
            "Список лотов: <code>/termarket</code>",
            parse_mode="HTML"
        )
        return
    iso = args[1].upper()

    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT tm.*, co.name as seller_name, co.emoji as seller_emoji "
            "FROM territory_market tm JOIN countries co ON co.id=tm.seller_id "
            "WHERE tm.chat_id=? AND tm.iso=?",
            (message.chat.id, iso)
        )
        lot = await c.fetchone()
        if not lot:
            await message.answer(
                f"❌ Территория <b>{COUNTRY_NAMES.get(iso, iso)}</b> не выставлена на продажу.\n"
                "Список лотов: <code>/termarket</code>",
                parse_mode="HTML"
            )
            return
        if lot["seller_id"] == country["id"]:
            await message.answer("❌ Нельзя купить свою же территорию!")
            return

        # Проверяем что продавец всё ещё владеет территорией
        tc = await db.execute(
            "SELECT country_id FROM territories WHERE chat_id=? AND iso=?",
            (message.chat.id, iso)
        )
        ter_row = await tc.fetchone()
        if not ter_row or ter_row["country_id"] != lot["seller_id"]:
            # Лот устарел — удаляем
            await db.execute("DELETE FROM territory_market WHERE chat_id=? AND iso=?", (message.chat.id, iso))
            await db.commit()
            await message.answer("❌ Этот лот устарел — продавец уже не владеет территорией.")
            return

        price = lot["price"]

        # Переводим деньги и передаём территорию.
        # BUG FIX: проверка казны шла по устаревшему снимку country — теперь
        # списание условное, и вторая параллельная покупка просто не пройдёт.
        if price > 0:
            _cur = await db.execute(
                "UPDATE countries SET treasury=treasury-? WHERE id=? AND treasury>=?",
                (price, country["id"], price)
            )
            if (_cur.rowcount or 0) == 0:
                _hc = await db.execute("SELECT treasury FROM countries WHERE id=?", (country["id"],))
                _hr = await _hc.fetchone()
                await message.answer(
                    f"❌ Недостаточно средств! Нужно {price}💰, у вас {_hr['treasury'] if _hr else 0}💰"
                )
                return
            await db.execute("UPDATE countries SET treasury=treasury+? WHERE id=?", (price, lot["seller_id"]))
        await db.execute(
            """INSERT INTO territories (chat_id, iso, country_id) VALUES (?,?,?)
               ON CONFLICT(chat_id, iso) DO UPDATE SET country_id=excluded.country_id""",
            (message.chat.id, iso, country["id"])
        )
        await db.execute("DELETE FROM territory_market WHERE chat_id=? AND iso=?", (message.chat.id, iso))
        await db.commit()

    ter_name = COUNTRY_NAMES.get(iso, iso)
    price_str = f"{price}💰" if price > 0 else "бесплатно"
    await message.answer(
        f"🛒 <b>Территория куплена!</b>\n\n"
        f"🗺 <b>{ter_name}</b> (<code>{iso}</code>)\n"
        f"Продавец: {lot['seller_emoji']} {lot['seller_name']}\n"
        f"Покупатель: {country['emoji']} {country['name']}\n"
        f"💰 Сумма сделки: <b>{price_str}</b>",
        parse_mode="HTML"
    )
    await log_event(message.chat.id, "territory_sale",
                    f"{country['name']} купил {ter_name} у {lot['seller_name']} за {price}💰")


@router_economy.message(Command("termarket"))
async def cmd_termarket(message: Message):
    """/termarket — список территорий на продажу"""
    if message.chat.type == "private":
        return
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT tm.iso, tm.price, co.name as seller_name, co.emoji as seller_emoji "
            "FROM territory_market tm JOIN countries co ON co.id=tm.seller_id "
            "WHERE tm.chat_id=? ORDER BY tm.price ASC",
            (message.chat.id,)
        )
        lots = [dict(r) for r in await c.fetchall()]

    if not lots:
        await message.answer("🏪 <b>Рынок территорий</b>\n\nНет активных лотов.", parse_mode="HTML")
        return

    lines = ["🏪 <b>Рынок территорий</b>\n"]
    for lot in lots:
        ter_name = COUNTRY_NAMES.get(lot["iso"], lot["iso"])
        price_str = f"{lot['price']}💰" if lot["price"] > 0 else "бесплатно"
        lines.append(
            f"🗺 <b>{ter_name}</b> (<code>{lot['iso']}</code>)\n"
            f"   Продавец: {lot['seller_emoji']} {lot['seller_name']}\n"
            f"   Цена: <b>{price_str}</b>  →  <code>/buyterr {lot['iso']}</code>"
        )
    await message.answer("\n\n".join(lines), parse_mode="HTML")


@router_economy.message(Command("transfer"))
async def cmd_transfer(message: Message):
    if message.chat.type == "private":
        return
    sender = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not sender:
        await message.answer("❌ У вас нет страны!")
        return
    args = message.text.split()
    if len(args) < 3:
        await message.answer(
            "Использование: <code>/transfer ИмяСтраны сумма</code>", parse_mode="HTML"
        )
        return
    try:
        amount      = int(args[-1])
        target_name = " ".join(args[1:-1])
        if amount <= 0: raise ValueError
    except ValueError:
        await message.answer("❌ Пример: <code>/transfer Китай 100</code>", parse_mode="HTML")
        return

    receiver = None
    for c in await get_all_countries(message.chat.id):
        if c["name"].lower() == target_name.lower() or c["iso"] == target_name.upper():
            receiver = c; break

    if not receiver:
        await message.answer(f"❌ Страна «{target_name}» не найдена.")
        return
    if receiver["id"] == sender["id"]:
        await message.answer("❌ Нельзя переводить самому себе!")
        return
    # BUG FIX: списываем атомарно. Раньше проверка шла по устаревшему снимку
    # казны, а update_treasury обрезал баланс до нуля — два параллельных
    # /transfer позволяли отправить одну и ту же сумму дважды.
    if not await spend_treasury(sender["id"], amount):
        fresh_sender = await get_country_by_id(message.chat.id, sender["id"])
        have = fresh_sender["treasury"] if fresh_sender else 0
        await message.answer(f"❌ Недостаточно средств! У вас {have}💰")
        return
    await update_treasury(receiver["id"],  amount)
    await message.answer(
        f"💸 {sender['emoji']} <b>{sender['name']}</b> → "
        f"{amount}💰 → {receiver['emoji']} <b>{receiver['name']}</b>",
        parse_mode="HTML"
    )


@router_economy.message(Command("top"))
async def cmd_top(message: Message):
    if message.chat.type == "private":
        return
    await send_leaderboard(message, message.chat.id)


@router_economy.message(Command("ally"))
async def cmd_ally(message: Message):
    if message.chat.type == "private":
        return
    my = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not my:
        await message.answer("❌ У вас нет страны!")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: <code>/ally ИмяСтраны</code>", parse_mode="HTML")
        return
    q = args[1].strip()
    other = None
    for c in await get_all_countries(message.chat.id):
        if c["name"].lower() == q.lower() or c["iso"] == q.upper():
            other = c; break
    if not other:
        await message.answer(f"❌ Страна «{q}» не найдена.")
        return
    if other["owner_id"] == message.from_user.id:
        await message.answer("❌ Нельзя союз с самим собой!")
        return

    # Проверяем что союза ещё нет
    existing_allies = await get_allies(message.chat.id, my["id"])
    if other["id"] in existing_allies:
        await message.answer(
            f"❌ У вас уже есть союз с {other['emoji']} <b>{other['name']}</b>!",
            parse_mode="HTML"
        )
        return

    # Проверяем pending запрос в alliances (INSERT OR IGNORE — дубль не пройдёт)
    _adb = await _open_db()
    async with _adb:
        _ac = await _adb.execute(
            """SELECT id FROM alliances WHERE chat_id=?
               AND ((country_a_id=? AND country_b_id=?) OR (country_a_id=? AND country_b_id=?))""",
            (message.chat.id, my["id"], other["id"], other["id"], my["id"])
        )
        if await _ac.fetchone():
            await message.answer(
                f"❌ Союз с {other['emoji']} <b>{other['name']}</b> уже существует!",
                parse_mode="HTML"
            )
            return

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        colored_button("✅ Принять союз", "green",
                       callback_data=f"ally_accept:{my['id']}:{other['id']}"),
        colored_button("❌ Отклонить", "red",
                       callback_data=f"ally_decline:{other['id']}"),
    ]])
    await message.answer(
        f"🤝 {my['emoji']} <b>{my['name']}</b> предлагает союз "
        f"{other['emoji']} <b>{other['name']}</b>!\n\n"
        f"<i>Владелец {other['emoji']} {other['name']}, примите или отклоните.</i>",
        parse_mode="HTML", reply_markup=kb
    )


@router_economy.callback_query(F.data.startswith("ally_accept:"))
async def ally_accept(callback: CallbackQuery):
    parts       = callback.data.split(":")
    proposer_id = int(parts[1])
    target_id   = int(parts[2])
    my = await get_country_by_owner(callback.message.chat.id, callback.from_user.id)
    if not my or my["id"] != target_id:
        await callback.answer("❌ Не ваше решение!", show_alert=True); return
    ok, reason = await propose_alliance(callback.message.chat.id, proposer_id, target_id)
    if not ok:
        await callback.answer(f"❌ {reason}", show_alert=True); return
    p = await get_country_by_id(callback.message.chat.id, proposer_id)
    t = await get_country_by_id(callback.message.chat.id, target_id)
    if not p or not t:
        # BUG FIX: страну могли удалить между предложением и принятием —
        # p['emoji'] падал с TypeError: 'NoneType' is not subscriptable.
        await callback.answer("❌ Одна из стран больше не существует.", show_alert=True); return
    await callback.message.edit_text(
        f"🤝 <b>СОЮЗ ЗАКЛЮЧЁН!</b>\n"
        f"{make_country_str(p)} {make_owner_mention(p)} и "
        f"{make_country_str(t)} {make_owner_mention(t)} теперь союзники!\n"
        "⚠️ Союзники не могут нападать друг на друга.",
        parse_mode="HTML"
    )
    await callback.answer()


@router_economy.callback_query(F.data.startswith("ally_decline:"))
async def ally_decline(callback: CallbackQuery):
    target_id = int(callback.data.split(":")[1])
    my = await get_country_by_owner(callback.message.chat.id, callback.from_user.id)
    if not my or my["id"] != target_id:
        await callback.answer("❌ Не ваше решение!", show_alert=True); return
    await callback.message.edit_text(
        f"❌ {my['emoji']} <b>{my['name']}</b> отклонил предложение союза.",
        parse_mode="HTML"
    )
    await callback.answer()


@router_economy.message(Command("breakally"))
async def cmd_breakally(message: Message):
    if message.chat.type == "private":
        return
    my = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not my:
        await message.answer("❌ У вас нет страны!")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: <code>/breakally ИмяСтраны</code>", parse_mode="HTML")
        return
    q = args[1].strip()
    other = None
    for c in await get_all_countries(message.chat.id):
        if c["name"].lower() == q.lower() or c["iso"] == q.upper():
            other = c; break
    if not other:
        await message.answer(f"❌ Страна «{q}» не найдена.")
        return
    allies = await get_allies(message.chat.id, my["id"])
    if other["id"] not in allies:
        await message.answer(f"❌ Вы не в союзе с {other['emoji']} {other['name']}.", parse_mode="HTML")
        return
    await break_alliance(message.chat.id, my["id"], other["id"])
    await message.answer(
        f"💔 {my['emoji']} <b>{my['name']}</b> разорвал союз с "
        f"{other['emoji']} <b>{other['name']}</b>.", parse_mode="HTML"
    )


@router_economy.message(Command("pact"))
async def cmd_pact(message: Message):
    if message.chat.type == "private":
        return
    my = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not my:
        await message.answer("❌ У вас нет страны!")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: <code>/pact ИмяСтраны</code>", parse_mode="HTML")
        return
    q = args[1].strip()
    other = None
    for c in await get_all_countries(message.chat.id):
        if c["name"].lower() == q.lower() or c["iso"] == q.upper():
            other = c; break
    if not other:
        await message.answer(f"❌ Страна «{q}» не найдена.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        colored_button("✅ Принять пакт", "green",
                       callback_data=f"pact_accept:{my['id']}:{other['id']}"),
        colored_button("❌ Отклонить", "red",
                       callback_data=f"pact_decline:{other['id']}"),
    ]])
    await message.answer(
        f"📜 {my['emoji']} <b>{my['name']}</b> предлагает пакт о ненападении "
        f"{other['emoji']} <b>{other['name']}</b>!\n\n"
        f"<i>Владелец {other['emoji']} {other['name']}, примите или отклоните.</i>",
        parse_mode="HTML", reply_markup=kb
    )


@router_economy.callback_query(F.data.startswith("pact_accept:"))
async def pact_accept(callback: CallbackQuery):
    parts       = callback.data.split(":")
    proposer_id = int(parts[1])
    target_id   = int(parts[2])
    my = await get_country_by_owner(callback.message.chat.id, callback.from_user.id)
    if not my or my["id"] != target_id:
        await callback.answer("❌ Не ваше решение!", show_alert=True); return
    ok, reason = await propose_pact(callback.message.chat.id, proposer_id, target_id)
    if not ok:
        await callback.answer(f"❌ {reason}", show_alert=True); return
    p = await get_country_by_id(callback.message.chat.id, proposer_id)
    t = await get_country_by_id(callback.message.chat.id, target_id)
    if not p or not t:
        await callback.answer("❌ Одна из стран больше не существует.", show_alert=True); return
    await callback.message.edit_text(
        f"📜 <b>ПАКТ О НЕНАПАДЕНИИ</b>\n"
        f"{p['emoji']} <b>{p['name']}</b> и {t['emoji']} <b>{t['name']}</b> подписали пакт!\n"
        "⚠️ Они не могут объявлять войну друг другу.",
        parse_mode="HTML"
    )
    await callback.answer()


@router_economy.callback_query(F.data.startswith("pact_decline:"))
async def pact_decline(callback: CallbackQuery):
    target_id = int(callback.data.split(":")[1])
    my = await get_country_by_owner(callback.message.chat.id, callback.from_user.id)
    if not my or my["id"] != target_id:
        await callback.answer("❌ Не ваше решение!", show_alert=True); return
    await callback.message.edit_text(
        f"❌ {my['emoji']} <b>{my['name']}</b> отклонил пакт о ненападении.",
        parse_mode="HTML"
    )
    await callback.answer()


@router_economy.message(Command("diplomacy"))
async def cmd_diplomacy(message: Message):
    if message.chat.type == "private":
        return
    my = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not my:
        await message.answer("❌ У вас нет страны!")
        return
    allies = await get_allies(message.chat.id, my["id"])
    wars   = await get_active_wars(message.chat.id)
    lines  = [f"🗺 <b>Дипломатия — {my['emoji']} {my['name']}</b>\n"]
    if allies:
        lines.append("🤝 <b>Союзники:</b>")
        for aid in allies:
            a = await get_country_by_id(message.chat.id, aid)
            if a: lines.append(f"  • {a['emoji']} {a['name']}")
    at_war = []
    for war in wars:
        if war["attacker_id"] == my["id"]:
            e = await get_country_by_id(message.chat.id, war["defender_id"])
            if e: at_war.append(f"  ⚔️ {e['emoji']} {e['name']} (вы напали)")
        elif war["defender_id"] == my["id"]:
            e = await get_country_by_id(message.chat.id, war["attacker_id"])
            if e: at_war.append(f"  🛡 {e['emoji']} {e['name']} (напали на вас)")
    if at_war:
        lines.append("\n⚔️ <b>Войны:</b>")
        lines.extend(at_war)
    if not allies and not at_war:
        lines.append("Нейтралитет — союзов и войн нет.")
    lines.append("\n/ally /pact /war /peace /breakally")
    await message.answer("\n".join(lines), parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════
#  📰 ГАЗЕТА
# ═══════════════════════════════════════════════════════════════

# Шаблоны заголовков для разных типов события
_PAPER_HEADLINES = {
    "war_declared": [
        "🔥 ВОЙНА! {attacker_emoji}{attacker} против {defender_emoji}{defender}",
        "⚔️ ОБЪЯВЛЕНА ВОЙНА: {attacker_emoji}{attacker} атакует {defender_emoji}{defender}",
        "🚨 ВОЕННЫЙ КОНФЛИКТ: {attacker_emoji}{attacker} и {defender_emoji}{defender} на пороге войны",
    ],
    "territory_captured": [
        "🚩 {attacker_emoji}{attacker} захватил территорию {territory}",
        "🗺 Карта мира меняется: {territory} теперь под флагом {attacker_emoji}{attacker}",
        "⚔️ {attacker_emoji}{attacker} одержал победу и взял {territory}",
    ],
    "country_defeated": [
        "💀 {loser_emoji}{loser} полностью разгромлена войсками {winner_emoji}{winner}!",
        "🏴 {winner_emoji}{winner} уничтожил {loser_emoji}{loser}. Трофеи: {spoils}💰",
        "🏆 Конец {loser_emoji}{loser}! {winner_emoji}{winner} торжествует победу.",
    ],
    "peace_signed": [
        "🕊 МИР: {emoji_a}{country_a} и {emoji_b}{country_b} прекратили войну",
        "🤝 {emoji_a}{country_a} и {emoji_b}{country_b} подписали мирный договор",
        "☮️ Дипломатия победила: {emoji_a}{country_a} + {emoji_b}{country_b} — мир",
    ],
    "territory_expanded": [
        "🚩 {emoji}{country} расширяет владения: захвачена {territory}",
        "🌍 {emoji}{country} присоединил {territory} к своей империи",
        "📌 Экспансия: {emoji}{country} занял {territory}",
    ],
    "war_autobattle": [
        "⚔️ АВТОБОЙ: {attacker_emoji}{attacker} vs {defender_emoji}{defender}",
    ],
}

_PAPER_INTRO = [
    "📰 <b>МИРОВЫЕ НОВОСТИ</b>",
    "🗞 <b>ВЕЧЕРНИЙ ВЕСТНИК</b>",
    "📜 <b>ВОЕННЫЙ ТЕЛЕГРАФ</b>",
    "🏛 <b>ГОЛОС ИМПЕРИЙ</b>",
    "⚡️ <b>СРОЧНЫЕ НОВОСТИ</b>",
]

_ISSUE_NUMBER_KEY = "issue_number"  # хранится в newspaper_state через отдельную строку


async def get_next_issue_number(chat_id: int) -> int:
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT last_issue_at FROM newspaper_state WHERE chat_id=?", (chat_id,)
        )
        row = await c.fetchone()
        # используем last_issue_at как счётчик тоже — храним номер отдельно
        # простое решение: считаем сколько раз уже выходила газета по кол-ву записей
        c2 = await db.execute(
            "SELECT COUNT(*) FROM event_log WHERE chat_id=? AND event_type='newspaper_issued'",
            (chat_id,)
        )
        r2 = await c2.fetchone()
        return (r2[0] if r2 else 0) + 1


def _esc(s) -> str:
    """Экранирует HTML-спецсимволы в пользовательских данных."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_event_block(event: dict) -> str:
    """Превращает одно событие в текстовый блок для газеты."""
    etype = event["type"]
    data  = event["data"]

    # Экранируем все строковые поля из data чтобы не сломать HTML
    safe = {k: _esc(v) if isinstance(v, str) else v for k, v in data.items()}

    templates = _PAPER_HEADLINES.get(etype, [])
    if not templates:
        return ""
    try:
        headline = random.choice(templates).format_map(safe)
    except (KeyError, ValueError):
        headline = f"📰 Событие: {etype}"

    lines = [f"<b>{headline}</b>"]

    if etype == "war_declared":
        lines.append(
            f"  {safe.get('attacker_emoji','')}{safe.get('attacker','?')} официально объявил войну "
            f"{safe.get('defender_emoji','')}{safe.get('defender','?')}. "
            "Мирные переговоры провалились."
        )

    elif etype == "territory_captured":
        att  = f"{safe.get('attacker_emoji','')}{safe.get('attacker','?')}"
        defe = f"{safe.get('defender_emoji','')}{safe.get('defender','нейтральных сил')}" if safe.get('defender') else "нейтральных сил"
        lines.append(
            f"  Войска {att} разгромили {defe} и заняли <b>{safe.get('territory','?')}</b>.\n"
            f"  Потери: {att} — {safe.get('losses_att', 0)} чел., {defe} — {safe.get('losses_def', 0)} чел."
        )

    elif etype == "country_defeated":
        lines.append(
            f"  {safe.get('loser_emoji','')}{safe.get('loser','?')} прекратила существование.\n"
            f"  Все территории перешли к {safe.get('winner_emoji','')}{safe.get('winner','?')}.\n"
            f"  Захваченные трофеи: {safe.get('spoils', 0)}💰"
        )

    elif etype == "peace_signed":
        lines.append(
            f"  {safe.get('emoji_a','')}{safe.get('country_a','?')} и {safe.get('emoji_b','')}{safe.get('country_b','?')} "
            "подписали мирный договор. Боевые действия прекращены."
        )

    elif etype == "territory_expanded":
        lines.append(
            f"  {safe.get('emoji','')}{safe.get('country','?')} мирным путём присоединил <b>{safe.get('territory','?')}</b> "
            "к своим владениям."
        )

    elif etype == "war_autobattle":
        att  = f"{safe.get('attacker_emoji','')}{safe.get('attacker','?')}"
        defe = f"{safe.get('defender_emoji','')}{safe.get('defender','?')}"
        lines.append(
            f"  Регулярный бой между {att} и {defe}.\n"
            f"  Потери: {att} — {safe.get('attacker_losses', 0)} чел., "
            f"{defe} — {safe.get('defender_losses', 0)} чел.\n"
            f"  Армии: {att} — {safe.get('attacker_army_left', 0)}🗡, "
            f"{defe} — {safe.get('defender_army_left', 0)}🗡"
        )

    return "\n".join(lines)


async def build_newspaper(chat_id: int) -> Optional[str]:
    """Собирает газету из событий с прошлого выпуска. Возвращает None если событий нет."""
    last_ts    = await get_newspaper_last(chat_id)
    events     = await get_events_since(chat_id, last_ts)
    # Фильтруем служебные события
    events     = [e for e in events if e["type"] != "newspaper_issued"]

    if not events:
        return None

    issue_num  = await get_next_issue_number(chat_id)
    now_str    = time.strftime("%d.%m.%Y %H:%M", time.localtime())
    header     = random.choice(_PAPER_INTRO)

    lines = [
        f"{header}  |  Выпуск №{issue_num}  |  {now_str}",
        "━" * 32,
    ]

    # Группируем по типу для красивой вёрстки
    war_events      = [e for e in events if e["type"] in ("war_declared", "war_autobattle", "country_defeated")]
    capture_events  = [e for e in events if e["type"] == "territory_captured"]
    expand_events   = [e for e in events if e["type"] == "territory_expanded"]
    peace_events    = [e for e in events if e["type"] == "peace_signed"]

    if war_events:
        lines.append("\n🔥 <b>ВОЕННАЯ ХРОНИКА</b>")
        for e in war_events:
            block = _format_event_block(e)
            if block:
                lines.append(block)

    if capture_events:
        lines.append("\n🗺 <b>ИЗМЕНЕНИЯ НА КАРТЕ</b>")
        for e in capture_events:
            block = _format_event_block(e)
            if block:
                lines.append(block)

    if expand_events:
        lines.append("\n🚩 <b>ЭКСПАНСИЯ</b>")
        for e in expand_events:
            block = _format_event_block(e)
            if block:
                lines.append(block)

    if peace_events:
        lines.append("\n🕊 <b>ДИПЛОМАТИЯ</b>")
        for e in peace_events:
            block = _format_event_block(e)
            if block:
                lines.append(block)

    # Итоговая сводка потерь в войнах за период
    total_att_losses = sum(
        e["data"].get("losses_att", 0) + e["data"].get("attacker_losses", 0)
        for e in events if e["type"] in ("territory_captured", "war_autobattle")
    )
    total_def_losses = sum(
        e["data"].get("losses_def", 0) + e["data"].get("defender_losses", 0)
        for e in events if e["type"] in ("territory_captured", "war_autobattle")
    )
    if total_att_losses + total_def_losses > 0:
        lines.append(
            f"\n📊 <b>ОБЩИЕ ПОТЕРИ ЗА ПЕРИОД</b>\n"
            f"  Всего погибло: {total_att_losses + total_def_losses} солдат"
        )

    lines.append("━" * 32)
    lines.append(f"<i>© Мировое Агентство Новостей | Следующий выпуск через {NEWSPAPER_INTERVAL_H} ч.</i>")

    return "\n".join(lines)


async def publish_newspaper(bot: Bot, chat_id: int, force: bool = False):
    """Проверяет таймер и публикует газету если пришло время."""
    now    = int(time.time())
    last   = await get_newspaper_last(chat_id)
    if not force and (now - last) < NEWSPAPER_INTERVAL_H * 3600:
        return

    text = await build_newspaper(chat_id)
    if not text:
        if force:
            await _safe_send(bot, chat_id, "📰 Нет новостей за последний период.")
        await set_newspaper_last(chat_id, now)
        return

    try:
        await _safe_send(bot, chat_id, text, parse_mode="HTML")
    except Exception as e:
        logging.warning(f"publish_newspaper HTML error: {e} — retrying without parse_mode")
        try:
            # Убираем все HTML теги и отправляем plain text
            import re as _re
            plain = _re.sub(r"<[^>]+>", "", text)
            await _safe_send(bot, chat_id, plain)
        except Exception as e2:
            logging.error(f"publish_newspaper plain fallback failed: {e2}")
    await log_event(chat_id, "newspaper_issued", {"issue": await get_next_issue_number(chat_id)})
    await set_newspaper_last(chat_id, now)


# ═══════════════════════════════════════════════════════════════
#  ⚔️  WAR TICK — ЖИВЫЕ ВОЙНЫ
# ═══════════════════════════════════════════════════════════════

async def war_tick(bot: Bot):
    """
    Каждые 2 мин: начисляет потери воюющим сторонам и публикует боевой статус.
    Война длится бессрочно — до заключения мира через /peace или полного разгрома.
    """
    import time as _t, random as _rnd
    now = int(_t.time())
    chat_ids = await get_all_chats_with_active_wars()
    for chat_id in chat_ids:
        # Тик усталости по всем странам чата
        await tick_weariness(chat_id)
        wars = await get_active_wars(chat_id)
        for war in wars:
            att  = await get_country_by_id(chat_id, war["attacker_id"])
            defe = await get_country_by_id(chat_id, war["defender_id"])
            if not att or not defe:
                continue

            # ── Один блок БД на весь тик войны (предотвращает database is locked) ──
            _up = {"infantry": 1.0, "cavalry": 1.5, "artillery": 20.0, "airforce": 3.0,
                   "bomber": 5.0, "navy": 10.0, "armored": 8.0, "tank": 15.0}

            db = await _open_db()
            async with db:
                # Очки и потери войны
                sc = await db.execute(
                    "SELECT attacker_score, defender_score, attacker_losses, defender_losses FROM wars WHERE id=?",
                    (war["id"],)
                )
                sc_row = dict(await sc.fetchone() or {})
                att_sc  = sc_row.get("attacker_score", 0)
                defe_sc = sc_row.get("defender_score", 0)

                # Гарнизоны защитника
                _gc = await db.execute(
                    "SELECT iso,infantry,cavalry,artillery,airforce,bomber,navy,"
                    "COALESCE(armored,0) as armored,COALESCE(tank,0) as tank "
                    "FROM garrisons WHERE chat_id=? AND country_id=?",
                    (chat_id, defe["id"])
                )
                _gars = await _gc.fetchall()

                def_garrison_power = sum(
                    sum(_g[u] * _up.get(u, 1.0) for u in UNIT_TYPES)
                    for _g in _gars
                )

            att_w  = await get_weariness(att["id"])
            def_w  = await get_weariness(defe["id"])
            _, att_pen, _ = weariness_info(att_w)
            _, def_pen, _ = weariness_info(def_w)

            def_territories = list(await get_country_territories(chat_id, defe["id"]))
            avg_terrain_bonus = 0.0
            if def_territories:
                avg_terrain_bonus = sum(
                    TERRAIN_DEF_BONUS.get(get_terrain(iso), 0.0)
                    for iso in def_territories[:5]
                ) / min(5, len(def_territories))

            att_army = att["army"]
            def_army = max(1, int(def_garrison_power)) if def_garrison_power > 0 else defe["army"]

            # Автокапитуляция если у кого-то уже 0 армии
            async def _do_auto_capitulation(winner, loser, loser_army_name):
                loser_territories = list(await get_country_territories(chat_id, loser["id"]))
                for iso in loser_territories:
                    await capture_territory(chat_id, iso, winner["id"], bot=bot)
                    await strip_garrison_on_capture(chat_id, iso, winner["id"])
                spoils = await end_war(war["id"], winner["id"], loser["id"])
                await log_event(chat_id, "country_defeated", {
                    "winner": winner["name"], "winner_emoji": winner["emoji"],
                    "loser": loser["name"], "loser_emoji": loser["emoji"],
                    "spoils": spoils,
                })
                try:
                    await bot.send_message(
                        chat_id,
                        f"💀 <b>АВТОКАПИТУЛЯЦИЯ!</b>\n\n"
                        f"{loser['emoji']} <b>{loser['name']}</b> осталась без армии!\n"
                        f"Все территории переходят к {winner['emoji']} <b>{winner['name']}</b>.\n"
                        f"💰 Трофеи: {spoils}💰",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass

            if att_army <= 0:
                await _do_auto_capitulation(winner=defe, loser=att, loser_army_name=att["name"])
                continue
            if def_garrison_power == 0 and defe["army"] <= 0:
                await _do_auto_capitulation(winner=att, loser=defe, loser_army_name=defe["name"])
                continue

            # Усталость снижает эффективную силу атаки
            eff_att  = att_army * (1.0 - att_pen)
            eff_def  = def_army * (1.0 + avg_terrain_bonus) * (1.0 - def_pen)
            tick_att_losses = min(att_army, max(1, int(eff_att * 0.03 * (eff_def / max(eff_att, 1)) * _rnd.uniform(0.7, 1.3))))
            tick_def_losses = min(def_army, max(1, int(eff_def * 0.03 * (eff_att / max(eff_def, 1)) * _rnd.uniform(0.7, 1.3))))
            await update_army(att["id"], -tick_att_losses)

            # Потери защитника снимаем из гарнизонов пропорционально — одно соединение
            if def_garrison_power > 0 and _gars:
                total_gar_units = sum(sum(_g[u] for u in UNIT_TYPES) for _g in _gars)
                if total_gar_units > 0:
                    _ldb = await _open_db()
                    async with _ldb:
                        for _g in _gars:
                            g_total = sum(_g[u] for u in UNIT_TYPES)
                            if g_total == 0:
                                continue
                            g_share = int(tick_def_losses * g_total / total_gar_units)
                            if g_share <= 0:
                                continue
                            for u in UNIT_TYPES:
                                u_cnt = _g[u]
                                if u_cnt > 0:
                                    u_loss = min(u_cnt, int(g_share * u_cnt / g_total))
                                    if u_loss > 0:
                                        await _ldb.execute(
                                            f"UPDATE garrisons SET {u}=MAX(0,{u}-?) "
                                            f"WHERE chat_id=? AND country_id=? AND iso=?",
                                            (u_loss, chat_id, defe["id"], _g["iso"])
                                        )
                        await _ldb.commit()

            await add_war_losses(war["id"], tick_att_losses, tick_def_losses)

            att_army_after  = att_army - tick_att_losses
            def_army_after  = def_army - tick_def_losses  # отображаемое число (сила гарнизона)

            # Проверяем: если после тика армия дошла до 0 — автокапитуляция
            if att_army_after <= 0:
                await _do_auto_capitulation(winner=defe, loser=att, loser_army_name=att["name"])
                continue
            if def_army_after <= 0:
                await _do_auto_capitulation(winner=att, loser=defe, loser_army_name=defe["name"])
                continue

            # Строка усталости
            def _wear_bar(w):
                filled = round(w / 10)
                bar = "🟥" * filled + "⬜" * (10 - filled)
                lbl, _, _ = weariness_info(w)
                return f"{bar} {w}% — {lbl}"

            terrain_note = ""
            if avg_terrain_bonus > 0.05:
                terrain_note = f"\n⛰️ Рельеф: бонус защите {defe['name']} +{int(avg_terrain_bonus*100)}%"
            elif avg_terrain_bonus < -0.05:
                terrain_note = f"\n🏜��� Открытая местность — защита {defe['name']} ослаблена"

            started = war.get("started_at", now)
            duration_min = max(0, (now - started) // 60)
            dur_str = f"{duration_min // 60}ч {duration_min % 60}м" if duration_min >= 60 else f"{duration_min}м"

            try:
                await bot.send_message(
                    chat_id,
                    f"⚔️ <b>ВОЙНА ПРОДОЛЖАЕТСЯ</b> — {dur_str}\n\n"
                    f"{att['emoji']} <b>{att['name']}</b> {make_owner_mention(att)}\n"
                    f"  🗺 Очки: {att_sc}  🪖 Армия (пул): {att_army_after}\n"
                    f"  😓 Усталость: {_wear_bar(att_w)}\n\n"
                    f"{defe['emoji']} <b>{defe['name']}</b> {make_owner_mention(defe)}\n"
                    f"  🗺 Очки: {defe_sc}  🏰 Гарнизоны (сила): {max(0, def_army_after)}\n"
                    f"  😓 Усталость: {_wear_bar(def_w)}"
                    f"{terrain_note}\n\n"
                    f"💀 Потери тика: -{tick_att_losses} / -{tick_def_losses}\n"
                    f"<i>/attack ОТКУДА КУДА | /deploy | /garrison | /peace ИмяСтраны</i>",
                    parse_mode="HTML"
                )
            except Exception:
                pass


# ── /reinforce — экстренная докупка солдат во время войны ───────


@router_war.message(Command("move"))
async def cmd_move(message: Message):
    """
    /move ISO_откуда ISO_куда тип количество
    Перемещает гарнизон между своими территориями.
    Пример: /move DE PL пехота 100
    """
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны!")
        return

    args = message.text.split()
    if len(args) < 5:
        await message.answer(
            "⚔️ <b>Перемещение гарнизона</b>\n\n"
            "Использование: <code>/move ISO_откуда ISO_куда тип количество</code>\n"
            "Пример: <code>/move DE PL пехота 100</code>\n\n"
            "Перемещает войска из гарнизона одной территории в другую.\n"
            "Территории должны граничить между собой или обе быть вашими.",
            parse_mode="HTML"
        )
        return

    from_iso  = args[1].upper()
    to_iso    = args[2].upper()
    unit_type = args[3].lower()
    RU_MAP = {
        "пехота": "infantry", "пехоту": "infantry", "пехотинцы": "infantry",
        "кавалерия": "cavalry", "кавалерию": "cavalry",
        "артиллерия": "artillery", "артиллерию": "artillery",
        "авиация": "airforce", "авиацию": "airforce",
        "бомберы": "bomber", "бомбардировщики": "bomber",
        "флот": "navy", "корабли": "navy",
        "бронеавтомобиль": "armored", "броня": "armored", "бронеавто": "armored",
        "танк": "tank", "танки": "tank",
    }
    unit_type = RU_MAP.get(unit_type, unit_type)
    if unit_type not in UNIT_TYPES:
        await message.answer(f"❌ Неизвестный тип войск: {unit_type}")
        return
    try:
        amount = int(args[4])
        if amount <= 0: raise ValueError
    except ValueError:
        await message.answer("❌ Укажите положительное количество.")
        return

    our_territories = await get_country_territories(message.chat.id, country["id"])

    if from_iso not in our_territories:
        await message.answer(f"❌ {from_iso} — не ваша территория!")
        return
    if to_iso not in our_territories:
        await message.answer(f"❌ {to_iso} — не ваша территория!")
        return
    if from_iso == to_iso:
        await message.answer("❌ Нельзя перемещать в ту же территорию!")
        return

    # Проверяем что в гарнизоне есть нужное количество
    g = await get_garrison(message.chat.id, country["id"], from_iso)
    available = g.get(unit_type, 0)
    if available < amount:
        await message.answer(
            f"❌ В гарнизоне {from_iso} только {available} {unit_type}.\n"
            f"Сначала разместите войска: /garrison {from_iso} {unit_type} {amount}"
        )
        return

    # Снимаем из исходного гарнизона и добавляем в целевой
    db = await _open_db()
    async with db:
        # Снимаем из источника
        await db.execute(
            f"UPDATE garrisons SET {unit_type}=MAX(0,{unit_type}-?) "
            f"WHERE chat_id=? AND country_id=? AND iso=?",
            (amount, message.chat.id, country["id"], from_iso)
        )
        # Убеждаемся что запись для цели существует
        await db.execute(
            "INSERT OR IGNORE INTO garrisons (chat_id, country_id, iso) VALUES (?,?,?)",
            (message.chat.id, country["id"], to_iso)
        )
        # Добавляем в цель
        await db.execute(
            f"UPDATE garrisons SET {unit_type}={unit_type}+? "
            f"WHERE chat_id=? AND country_id=? AND iso=?",
            (amount, message.chat.id, country["id"], to_iso)
        )
        await db.commit()

    from_name = COUNTRY_NAMES.get(from_iso, from_iso)
    to_name   = COUNTRY_NAMES.get(to_iso, to_iso)
    unit_label = UNIT_LABELS.get(unit_type, unit_type)
    await message.answer(
        f"✅ <b>Гарнизон перемещён</b>\n\n"
        f"🚚 {amount} {unit_label} из <b>{from_name}</b> → <b>{to_name}</b>\n"
        f"Осталось в {from_iso}: {available - amount} | Теперь в {to_iso}: используйте /garrison {to_iso}",
        parse_mode="HTML"
    )


@router_war.message(Command("garrison"))
async def cmd_garrison(message: Message):
    """
    Управление гарнизонами.
    /garrison                       — показать все гарнизоны
    /garrison ISO                   — гарнизон конкретной территории
    /garrison ISO тип кол-во        — разместить войска
    /garrison ISO тип -кол-во       — вывести войска (отрицательное число)
    """
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны!")
        return

    args = message.text.split()
    territories = await get_country_territories(message.chat.id, country["id"])
    mil = await get_military(country["id"])

    # /garrison — обзор всех гарнизонов
    if len(args) == 1:
        garrisons = await get_all_garrisons(message.chat.id, country["id"])
        if not garrisons:
            pool_lines = [f"  {UNIT_LABELS[u]}: {mil.get(u,0)}" for u in UNIT_TYPES if mil and mil.get(u,0)]
            await message.answer(
                f"{PE['castle']} <b>Гарнизоны {country['emoji']} {country['name']}</b>\n\n"
                "Гарнизонов нет — территории не защищены!\n\n"
                "<b>⚠️ Важно:</b> Войска из пула НЕ защищают территории автоматически.\n"
                "Используйте <code>/garrison ISO тип кол-во</code> чтобы разместить войска.\n"
                "Территория без гарнизона захватывается мгновенно!\n\n"
                "<b>Пул войск (не размещены):</b>\n" + "\n".join(pool_lines or ["  Нет войск"]) +
                "\n\nРазместить: <code>/garrison ISO тип кол-во</code>",
                parse_mode="HTML"
            )
            return
        lines = [f"🏰 <b>Гарнизоны {country['emoji']} {country['name']}</b>\n"]
        total_garrisoned = {u: 0 for u in UNIT_TYPES}
        for g in garrisons:
            iso = g["iso"]
            ter_name = COUNTRY_NAMES.get(iso, iso)
            unit_strs = [f"{UNIT_LABELS[u]}: {g[u]}" for u in UNIT_TYPES if g.get(u, 0) > 0]
            if not unit_strs:
                continue
            _, g_def = await get_defense_garrison(message.chat.id, iso)
            _, att_pen, _ = weariness_info(await get_weariness(country["id"]))
            def_str_raw = sum(g.get(u, 0) * {"infantry": 1.0, "cavalry": 1.5, "artillery": 20.0, "airforce": 3.0, "bomber": 5.0, "navy": 10.0, "armored": 8.0, "tank": 15.0}.get(u,1.0) for u in UNIT_TYPES)
            cap_iso = (country.get("capital_iso") or country["iso"]).upper()
            is_cap = iso == cap_iso
            lines.append(
                f"{'🏯' if is_cap else '🏴'} <b>{ter_name}</b> ({iso}){' [СТОЛИЦА]' if is_cap else ''}\n"
                f"  {'  '.join(unit_strs)}\n"
                f"  💪 Боевая сила: {int(def_str_raw)}"
            )
            for u in UNIT_TYPES:
                total_garrisoned[u] += g.get(u, 0)
        # Пул
        pool_lines = [f"{UNIT_LABELS[u]}: {mil.get(u,0)}" for u in UNIT_TYPES if mil and mil.get(u,0) > 0]
        lines.append(f"\n🗃 <b>Пул (не размещены):</b> {', '.join(pool_lines) or 'нет'}")
        lines.append("\n<i>/garrison ISO тип кол-во — разместить</i>")
        lines.append("<i>/garrison ISO тип -кол-во — вывести</i>")
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    iso = args[1].upper()
    if not is_valid_iso(iso):
        await message.answer(f"❌ Неизвестный ISO: {iso}")
        return

    # /garrison ISO — просмотр одной территории
    if len(args) == 2:
        in_claim = await get_claim(message.chat.id, country["id"], iso)
        g = await get_garrison(message.chat.id, country["id"], iso)
        is_ours = iso in territories
        ter_name = COUNTRY_NAMES.get(iso, iso)
        unit_strs = [f"{UNIT_LABELS[u]}: {g.get(u, 0)}" for u in UNIT_TYPES if g.get(u, 0) > 0]
        g_strength, g_breakdown = await get_defense_garrison(message.chat.id, iso)
        allies_str = ""
        if len(g_breakdown) > 1:
            allies_str = f"\n👥 Гарнизоны союзников: {len(g_breakdown)-1} страна(ы)"
        claim_str = f"\n⚔️ Претензия: {CLAIM_TYPES[in_claim['claim_type']]['emoji']} {CLAIM_TYPES[in_claim['claim_type']]['name']} (+{in_claim['strength']}% к атаке)" if in_claim else ""
        await message.answer(
            f"🏴 <b>{ter_name}</b> ({iso}) {'✅ Ваша' if is_ours else '❌ Чужая'}{claim_str}\n\n"
            f"<b>Ваш гарнизон:</b>\n" +
            ("\n".join(f"  {s}" for s in unit_strs) if unit_strs else "  Пусто") +
            f"\n\n💪 Общая сила обороны: {int(g_strength)}{allies_str}\n\n"
            f"<i>Разместить: /garrison {iso} пехота 500</i>",
            parse_mode="HTML"
        )
        return

    # /garrison ISO тип кол-во
    if len(args) < 4:
        await message.answer(
            "Использование: <code>/garrison ISO тип кол-во</code>\n"
            "Отрицательное кол-во — вывод войск.\n"
            f"Типы: {', '.join(UNIT_TYPES)}",
            parse_mode="HTML"
        )
        return

    unit_type = args[2].lower()
    RU_MAP = {
        "пехота": "infantry", "пехоту": "infantry", "кавалерия": "cavalry",
        "кавалерию": "cavalry", "артиллерия": "artillery", "артиллерию": "artillery",
        "авиация": "airforce", "авиацию": "airforce", "бомберы": "bomber",
        "бомбардировщики": "bomber", "флот": "navy", "корабли": "navy",
    }
    unit_type = RU_MAP.get(unit_type, unit_type)
    if unit_type not in UNIT_TYPES:
        await message.answer(f"❌ Неизвестный тип: {unit_type}\nДоступны: {', '.join(UNIT_TYPES)}")
        return
    try:
        amount = int(args[3])
    except ValueError:
        await message.answer("❌ Введите число (отрицательное — для вывода).")
        return

    if amount == 0:
        await message.answer("❌ Введите ненулевое количество.")
        return

    if amount > 0:
        ok, msg = await move_to_garrison(message.chat.id, country["id"], iso, unit_type, amount)
    else:
        ok, msg = await withdraw_from_garrison(message.chat.id, country["id"], iso, unit_type, abs(amount))
    await message.answer(("✅ " if ok else "❌ ") + msg)




@router_war.message(Command("withdraw"))
async def cmd_withdraw(message: Message):
    """
    /withdraw ISO тип количество — вывести войска из гарнизона обратно в пул.
    Пример: /withdraw RU пехота 200
    """
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны!")
        return

    args = message.text.split()

    if len(args) == 1:
        # Показать справку
        await message.answer(
            "🔙 <b>Вывод войск из гарнизона</b>\n\n"
            "Использование: <code>/withdraw ISO тип количество</code>\n"
            "Пример: <code>/withdraw RU пехота 200</code>\n\n"
            f"Типы войск: {', '.join(UNIT_TYPES)}\n\n"
            "<i>Войска вернутся в пул и их можно переместить в другой гарнизон.</i>",
            parse_mode="HTML"
        )
        return

    if len(args) < 4:
        await message.answer(
            "❌ Недостаточно аргументов.\n"
            "Использование: <code>/withdraw ISO тип количество</code>",
            parse_mode="HTML"
        )
        return

    iso = args[1].upper()
    if not is_valid_iso(iso):
        await message.answer(f"❌ Неизвестный ISO: <code>{iso}</code>", parse_mode="HTML")
        return

    unit_type = args[2].lower()
    RU_MAP = {
        "пехота": "infantry", "пехоту": "infantry", "кавалерия": "cavalry",
        "кавалерию": "cavalry", "артиллерия": "artillery", "артиллерию": "artillery",
        "авиация": "airforce", "авиацию": "airforce", "бомберы": "bomber",
        "бомбардировщики": "bomber", "флот": "navy", "корабли": "navy",
        "бронетехника": "armored", "броня": "armored", "танки": "tank", "танк": "tank",
    }
    unit_type = RU_MAP.get(unit_type, unit_type)
    if unit_type not in UNIT_TYPES:
        await message.answer(
            f"❌ Неизвестный тип войск: <code>{unit_type}</code>\n"
            f"Доступны: {', '.join(UNIT_TYPES)}",
            parse_mode="HTML"
        )
        return

    try:
        amount = int(args[3])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Количество должно быть положительным числом.")
        return

    ok, msg = await withdraw_from_garrison(message.chat.id, country["id"], iso, unit_type, amount)
    await message.answer(("✅ " if ok else "❌ ") + msg)


@router_war.message(Command("redistribute"))
async def cmd_redistribute(message: Message):
    """
    /redistribute        — показать как будет распределено
    /redistribute confirm — применить распределение
    Равномерно раскидывает войска из пула по всем своим территориям.
    """
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны!")
        return

    pool = country["army"]
    if pool <= 0:
        await message.answer("❌ В пуле нет войск для распределения.\nКупите войска: <code>/buy army N</code>", parse_mode="HTML")
        return

    territories = await get_country_territories(message.chat.id, country["id"])
    if not territories:
        await message.answer("❌ У вас нет территорий!")
        return

    n = len(territories)
    per_ter = pool // n
    remainder = pool % n

    if per_ter == 0:
        await message.answer(
            f"❌ В пуле {pool} солдат — слишком мало для {n} территорий (нужно хотя бы {n}).",
            parse_mode="HTML"
        )
        return

    args = message.text.split()
    confirm = len(args) > 1 and args[1].lower() in ("confirm", "да", "подтвердить")

    if not confirm:
        # Показываем превью
        ter_list = sorted(territories)
        cap_iso = (country.get("capital_iso") or country["iso"]).upper()
        lines = []
        for i, iso in enumerate(ter_list):
            amount = per_ter + (1 if i < remainder else 0)
            ter_name = COUNTRY_NAMES.get(iso, iso)
            cap_mark = f" {PE['star']}" if iso == cap_iso else ""
            lines.append(f"  🏴 <b>{ter_name}</b>{cap_mark} (<code>{iso}</code>) ← +{amount} 🪖 Пехота")
        await message.answer(
            f"📊 <b>Предварительное распределение</b>\n\n"
            f"Пул: {pool} солдат → {n} территорий\n"
            f"По {per_ter} на каждую{f' (+1 остаток на первые {remainder})' if remainder else ''}\n\n"
            + "\n".join(lines) +
            f"\n\n✅ Подтвердить: <code>/redistribute confirm</code>",
            parse_mode="HTML"
        )
        return

    # Применяем распределение
    ter_list = sorted(territories)
    cap_iso = (country.get("capital_iso") or country["iso"]).upper()
    total_placed = 0
    db = await _open_db()
    async with db:
        for i, iso in enumerate(ter_list):
            amount = per_ter + (1 if i < remainder else 0)
            if amount <= 0:
                continue
            await db.execute(
                """INSERT INTO garrisons (chat_id, country_id, iso, infantry, updated_at)
                   VALUES (?,?,?,?,strftime('%s','now'))
                   ON CONFLICT(chat_id, country_id, iso)
                   DO UPDATE SET infantry=infantry+?, updated_at=strftime('%s','now')""",
                (message.chat.id, country["id"], iso, amount, amount)
            )
            total_placed += amount
        # Списываем из пула
        await db.execute(
            "UPDATE countries SET army=MAX(0,army-?) WHERE id=?",
            (total_placed, country["id"])
        )
        await db.commit()

    lines2 = []
    for i, iso in enumerate(ter_list):
        amount = per_ter + (1 if i < remainder else 0)
        ter_name = COUNTRY_NAMES.get(iso, iso)
        cap_mark = f" {PE['star']}" if iso == cap_iso else ""
        lines2.append(f"  ✅ <b>{ter_name}</b>{cap_mark} (<code>{iso}</code>) +{amount} пехоты")

    await message.answer(
        f"✅ <b>Распределение выполнено!</b>\n\n"
        + "\n".join(lines2) +
        f"\n\n🗃 Пул: {pool} → {pool - total_placed} солдат",
        parse_mode="HTML"
    )

@router_war.message(Command("claim"))
async def cmd_claim(message: Message):
    """
    Претензии на территории (Casus Belli).
    /claim                        — показать свои претензии
    /claim ISO                    — претензии на территорию
    /claim ISO тип                — оформить претензию
    /claim drop ISO               — снять претензию
    Типы: conquest, historical, liberation, irredenta
    """
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны!")
        return

    args = message.text.split()

    # /claim — список претензий
    if len(args) == 1:
        claims = await get_claims(message.chat.id, country["id"])
        if not claims:
            type_lines = "\n".join(
                f"  <code>{k}</code> — {v['emoji']} {v['name']}: {v['cost_gold']}💰"
                + (f" + {v['cost_science']}📚" if v['cost_science'] else "")
                + f" → +{v['war_bonus']}% при атаке"
                for k, v in CLAIM_TYPES.items()
            )
            await message.answer(
                f"📜 <b>Претензии {country['emoji']} {country['name']}</b>\n\n"
                "Претензий нет.\n\n"
                "<b>Типы претензий:</b>\n" + type_lines +
                "\n\nОформить: <code>/claim ISO тип</code>\n"
                "Пример: <code>/claim PL historical</code>",
                parse_mode="HTML"
            )
            return
        lines = [f"📜 <b>Претензии {country['emoji']} {country['name']}</b>\n"]
        for cl in claims:
            cdef = CLAIM_TYPES.get(cl["claim_type"], {})
            ter_name = COUNTRY_NAMES.get(cl["iso"], cl["iso"])
            lines.append(
                f"{cdef.get('emoji','⚔️')} <b>{ter_name}</b> ({cl['iso']})\n"
                f"  Тип: {cdef.get('name', cl['claim_type'])} | Бонус: +{cl['strength']}%\n"
                f"  <i>/claim drop {cl['iso']} — снять</i>"
            )
        await message.answer("\n\n".join(lines), parse_mode="HTML")
        return

    # /claim drop ISO
    if args[1].lower() == "drop":
        if len(args) < 3:
            await message.answer("Использование: <code>/claim drop ISO</code>", parse_mode="HTML")
            return
        iso = args[2].upper()
        cl = await get_claim(message.chat.id, country["id"], iso)
        if not cl:
            await message.answer(f"❌ Нет претензии на {COUNTRY_NAMES.get(iso, iso)}")
            return
        await drop_claim(message.chat.id, country["id"], iso)
        await message.answer(f"🗑 Претензия на {COUNTRY_NAMES.get(iso, iso)} снята.")
        return

    iso = args[1].upper()
    if not is_valid_iso(iso):
        await message.answer(f"❌ Неизвестный ISO: {iso}")
        return

    # /claim ISO — просмотр претензий на территорию
    if len(args) == 2:
        ter_name = COUNTRY_NAMES.get(iso, iso)
        my_claim = await get_claim(message.chat.id, country["id"], iso)
        type_lines = "\n".join(
            f"  <code>{k}</code> — {v['emoji']} {v['name']}: {v['cost_gold']}💰"
            + (f"+{v['cost_science']}📚" if v['cost_science'] else "")
            + f" → +{v['war_bonus']}% атаки"
            for k, v in CLAIM_TYPES.items()
        )
        my_str = ""
        if my_claim:
            cdef = CLAIM_TYPES.get(my_claim["claim_type"], {})
            my_str = f"\n✅ <b>Ваша претензия:</b> {cdef.get('emoji','')} {cdef.get('name','')} (+{my_claim['strength']}%)\n"
        await message.answer(
            f"📜 <b>Претензии на {ter_name}</b> ({iso}){my_str}\n"
            f"<b>Доступные типы:</b>\n{type_lines}\n\n"
            f"Оформить: <code>/claim {iso} тип</code>",
            parse_mode="HTML"
        )
        return

    # /claim ISO тип — оформить претензию
    claim_type = args[2].lower()
    RU_CLAIM = {
        "завоевательная": "conquest", "завоевание": "conquest",
        "историческая": "historical", "история": "historical",
        "освобождение": "liberation",
        "ирредента": "irredenta",
    }
    claim_type = RU_CLAIM.get(claim_type, claim_type)
    ok, msg = await add_claim(message.chat.id, country["id"], iso, claim_type)
    await message.answer(("✅ " if ok else "❌ ") + msg, parse_mode="HTML")


@router_war.message(Command("weariness"))
async def cmd_weariness(message: Message):
    """Показывает военную усталость всех стран в чате."""
    if message.chat.type == "private":
        return
    countries = await get_all_countries(message.chat.id)
    if not countries:
        await message.answer("Стран нет.")
        return
    lines = ["😓 <b>Военная усталость</b>\n"]
    for c in countries:
        w = await get_weariness(c["id"])
        lbl, penalty, hap = weariness_info(w)
        filled = round(w / 10)
        bar = "🟥" * filled + "⬜" * (10 - filled)
        pen_str = f" | ⚔️ -{int(penalty*100)}% армии" if penalty > 0 else ""
        lines.append(
            f"{c['emoji']} <b>{c['name']}</b>\n"
            f"  {bar} {w}/100\n"
            f"  {lbl}{pen_str}"
        )
    lines.append(
        "\n<i>Усталость растёт каждые 2ч войны (+3).\n"
        "В мире спадает (-2/тик). Критическая (95+) — дезертирство!</i>"
    )
    await message.answer("\n\n".join(lines), parse_mode="HTML")


@router_war.message(Command("reinforce"))
async def cmd_reinforce(message: Message):
    if message.chat.type == "private":
        return
    my = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not my:
        await message.answer("❌ У вас нет страны!")
        return

    # Проверяем что страна в войне
    wars = await get_active_wars(message.chat.id)
    at_war = any(
        w["attacker_id"] == my["id"] or w["defender_id"] == my["id"]
        for w in wars
    )
    if not at_war:
        await message.answer("❌ Ваша страна не воюет. Обычная покупка: /buy")
        return

    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer(
            "⚔️ <b>Экстренное подкрепление</b>\n\n"
            "Цена: <b>3💰 за солдата</b> (вдвое дороже обычного)\n"
            "Использование: <code>/reinforce 500</code>",
            parse_mode="HTML"
        )
        return

    amount = int(args[1])
    if amount < 10:
        await message.answer("❌ Минимум 10 солдат.")
        return
    if amount > 5000:
        amount = 5000

    cost = amount * 3  # цена ×2 от обычной (обычно 1.5 за солдата)
    fresh = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not fresh:
        await message.answer("❌ Страна не найдена.")
        return
    # BUG FIX: атомарное списание. Раньше update_treasury обрезал казну по
    # MAX(0, ...), поэтому спам /reinforce давал солдат дешевле или бесплатно.
    if not await spend_treasury(fresh["id"], cost):
        _hc = await get_country_by_id(message.chat.id, fresh["id"])
        _have = _hc["treasury"] if _hc else 0
        await message.answer(
            f"❌ Нужно {cost}💰, у вас {_have}💰\n"
            f"Можете купить максимум {_have // 3} солдат."
        )
        return
    await update_army(my["id"],     +amount)

    await message.answer(
        f"⚔️ <b>Подкрепление прибыло!</b>\n\n"
        f"+{amount} солдат к армии {my['emoji']} <b>{my['name']}</b>\n"
        f"Потрачено: {cost}💰\n"
        f"Армия теперь: {fresh['army'] + amount}🗡",
        parse_mode="HTML"
    )


# ── /newspaper — ручной выпуск (для теста) ───────────────────────

router_newspaper = Router()


@router_newspaper.message(Command("newspaper"))
async def cmd_newspaper(message: Message):
    if message.chat.type == "private":
        return
    # SECURITY FIX: ручной выпуск — только для администраторов бота,
    # иначе любой игрок мог спамить газетой и расходовать AI-квоту.
    if not is_admin(message.from_user.id):
        await message.answer("❌ Команда доступна только администраторам бота.")
        return
    await message.answer("📰 Готовлю выпуск газеты...")
    await publish_newspaper(message.bot, message.chat.id, force=True)



# ═══════════════════════════════════════════════════════════════
#  HANDLERS — РЕСУРСЫ, ЗДАНИЯ, ТЕХНОЛОГИИ, ВОЙСКА, ПОИСК
# ═══════════════════════════════════════════════════════════════

router_dev = Router()

ADMIN_IDS: set[int] = {8878111383, 7973298670}
_env_admin = os.environ.get("ADMIN_ID")
if _env_admin and _env_admin.isdigit():
    ADMIN_IDS.add(int(_env_admin))
_env_admins = os.environ.get("ADMIN_IDS", "")
for _a in _env_admins.replace(" ", "").split(","):
    if _a.isdigit():
        ADMIN_IDS.add(int(_a))

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


@router_dev.message(Command("start"))
async def cmd_start(message: Message):
    if message.chat.type == "private":
        text = (
            "🌍 <b>RP Country — стратегия стран в Telegram</b>\n"
            "══════════════════════════\n\n"
            "Этот бот работает <b>в групповом чате</b>: добавь его в чат, "
            "дай право читать сообщения и напиши там <code>/create</code>.\n\n"
            "🚀 <b>С чего начать:</b>\n"
            "1. <code>/create</code> — основать страну (выбор ISO, флаг, цвет)\n"
            "2. <code>/mycountry</code> — статус страны\n"
            "3. <code>/build</code> → <code>/military</code> — экономика и армия\n"
            "4. <code>/expand</code> — захват нейтральных территорий\n"
            "5. <code>/war</code> — войны с другими игроками\n\n"
            "📖 Полный гайд: <code>/help</code>\n"
            "🗺 Карта: <code>/map</code> · 🏆 Рейтинг: <code>/top</code>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🌍 Как создать страну", callback_data="welcome:create")],
            [InlineKeyboardButton(text="⚔️ Как воевать", callback_data="welcome:war")],
            [InlineKeyboardButton(text="📖 Все команды", callback_data="welcome:help")],
        ])
        await message.answer(text, reply_markup=kb)
    else:
        await message.answer(
            "🌍 <b>RP Country</b>\n\n"
            "Основать страну: <code>/create</code>\n"
            "Статус: <code>/mycountry</code> · Карта: <code>/map</code> · "
            "Гайд: <code>/help</code>"
        )


_WELCOME_TEXTS = {
    "create": (
        "🌍 <b>Создание страны</b>\n\n"
        "Напиши в групповом чате <code>/create</code>. Бот предложит:\n"
        "1. Выбрать ISO-код страны (например <code>RU</code>, <code>DE</code>)\n"
        "2. Ввести название\n"
        "3. Выбрать эмодзи-флаг и цвет на карте\n\n"
        "После создания у тебя появятся стартовые территории, золото и армия. "
        "Развивай экономику (<code>/build</code>), армию (<code>/military</code>) "
        "и расширяйся (<code>/expand</code>)."
    ),
    "war": (
        "⚔️ <b>Войны</b>\n\n"
        "1. <code>/war &lt;ISO&gt;</code> — объявить войну стране игрока\n"
        "2. <code>/attack</code> — атаковать её территорию (армия + гарнизоны)\n"
        "3. <code>/deploy</code> и <code>/garrison</code> — держать оборону\n"
        "4. <code>/bombard</code>, <code>/nuclear</code>, <code>/spy</code> — тяжёлые опции\n"
        "5. <code>/peace</code> — мир, <code>/weariness</code> — усталость от войны\n\n"
        "Каждый час идёт автобой; при нулевой армии — капитуляция и потеря территорий."
    ),
    "help": (
        "📖 Полное руководство — команда <code>/help</code> в чате.\n\n"
        "Быстрый набор: <code>/buildmenu</code> (строительство кнопками), "
        "<code>/status</code> (сводка), <code>/top</code> (рейтинг), "
        "<code>/news</code> → <code>/newspaper</code> (газета), "
        "<code>/achievements</code> (достижения)."
    ),
}


@router_dev.callback_query(F.data.startswith("welcome:"))
async def cb_welcome(callback: CallbackQuery):
    key = (callback.data or "").split(":", 1)[-1]
    await callback.answer()
    await callback.message.answer(
        _WELCOME_TEXTS.get(key, "Смотри <code>/help</code> для всех команд.")
    )




# ── /income — лог доходов ────────────────────────────────────────

@router_economy.message(Command("income"))
async def cmd_income(message: Message):
    """Показывает подробный лог дохода за последний тик."""
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны!")
        return

    db = await _open_db()
    async with db:
        c = await db.execute(
            """SELECT data FROM event_log
               WHERE chat_id=? AND event_type='income_tick'
               AND json_extract(data,'$.country')=?
               ORDER BY created_at DESC LIMIT 1""",
            (message.chat.id, country["name"])
        )
        row = await c.fetchone()

    if not row:
        await message.answer("📊 Данных о доходе пока нет — подождите следующего тика.")
        return

    d = json.loads(row["data"])
    lines = [
        f"📊 <b>Доход за последний тик — {country['emoji']} {country['name']}</b>\n",
        f"💰 Золото:",
        f"  🏦 Здания: +{d.get('treasury',0) - d.get('tax_from_pop',0)}",
        f"  👥 Налог с населения: +{d.get('tax_from_pop',0)}",
        f"  💰 Итого: <b>+{d.get('treasury',0)}</b>\n",
        f"🌾 Еда: +{d.get('food',0)} (потреблено армией/населением: -{d.get('food_consumed',0)})",
        f"⚙️ Производство: +{d.get('production',0)}",
        f"🔬 Наука: +{d.get('science',0)}",
        f"🛢 Нефть: +{d.get('oil',0)}",
        f"💎 Металлы: +{d.get('rare_metals',0)}",
        f"👶 Прирост населения: +{d.get('pop_growth',0)}",
        f"\n<i>Тик происходит раз в {RESOURCE_TICK_INTERVAL_H} час(а).</i>",
    ]
    await message.answer("\n".join(lines), parse_mode="HTML")


# ── Админ-панель ─────────────────────────────────────────────────

router_admin = Router()


def _admin_main_kb(chat_id: int) -> InlineKeyboardMarkup:
    """Клавиатура главного меню админки с chat_id в callback_data."""
    cid = chat_id
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌍 Все страны чата",     callback_data=f"adm:{cid}:countries")],
        [InlineKeyboardButton(text="⚔️ Активные войны",      callback_data=f"adm:{cid}:wars")],
        [InlineKeyboardButton(text="💰 Топ по казне",        callback_data=f"adm:{cid}:treasury")],
        [InlineKeyboardButton(text="🏆 Топ по VP",           callback_data=f"adm:{cid}:vp")],
        [InlineKeyboardButton(text="🗑 Удалить страну",      callback_data=f"adm:{cid}:delete_list")],
        [InlineKeyboardButton(text="💸 Выдать ресурс",       callback_data=f"adm:{cid}:give_res")],
        [InlineKeyboardButton(text="🗺 Передать территорию", callback_data=f"adm:{cid}:transfer_ter")],
        [InlineKeyboardButton(text="🔄 Сбросить КД игрока",  callback_data=f"adm:{cid}:reset_cd")],
        [InlineKeyboardButton(text="📊 Статистика бота",     callback_data=f"adm:{cid}:stats")],
    ])


def _admin_back_kb(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [colored_button("🔙 Назад", "default", callback_data=f"adm:{chat_id}:back")]
    ])


@router_admin.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return

    # Из группы — сразу открываем панель для этой группы
    if message.chat.type != "private":
        chat_id = message.chat.id
        await message.answer(
            "🛡 <b>Админ-панель</b>",
            reply_markup=_admin_main_kb(chat_id),
            parse_mode="HTML"
        )
        return

    # Из личного чата — показываем список доступных групп
    chats = await get_all_chats()
    if not chats:
        await message.answer("❌ Нет групп с активными играми.")
        return

    db = await _open_db()
    async with db:
        rows = []
        for cid in chats:
            c = await db.execute("SELECT COUNT(*) FROM countries WHERE chat_id=?", (cid,))
            cnt = (await c.fetchone())[0]
            rows.append((cid, cnt))

    kb_rows = [
        [InlineKeyboardButton(
            text=f"Чат {cid} ({cnt} стран)",
            callback_data=f"adm:{cid}:back"
        )]
        for cid, cnt in rows
    ]
    await message.answer(
        "🛡 <b>Выберите чат:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="HTML"
    )


@router_admin.callback_query(F.data.startswith("adm:"))
async def cb_admin(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    # Формат: adm:{chat_id}:{action}[:{page_or_id}]
    parts  = callback.data.split(":", 3)   # ["adm", chat_id, action, ?extra]
    if len(parts) < 3:
        await callback.answer("Неверный формат", show_alert=True)
        return

    try:
        chat_id = int(parts[1])
    except ValueError:
        await callback.answer("Неверный chat_id", show_alert=True)
        return

    action = parts[2]  # e.g. "countries", "delete_list", "delete"
    extra  = parts[3] if len(parts) > 3 else ""  # page number or country_id

    if action == "wars":
        wars = await get_active_wars(chat_id)
        if not wars:
            await callback.answer("Нет активных войн", show_alert=True)
            return
        lines = [f"⚔️ <b>Активные войны ({len(wars)}):</b>\n"]
        for w in wars:
            att  = await get_country_by_id(chat_id, w["attacker_id"])
            defe = await get_country_by_id(chat_id, w["defender_id"])
            if att and defe:
                lines.append(f"{att['emoji']} {att['name']} vs {defe['emoji']} {defe['name']} (id: {w['id']})")
        await callback.message.edit_text(
            "\n".join(lines), parse_mode="HTML",
            reply_markup=_admin_back_kb(chat_id))

    elif action == "treasury":
        countries = sorted(await get_all_countries(chat_id), key=lambda x: x["treasury"], reverse=True)[:10]
        lines = ["💰 <b>Топ по казне:</b>\n"]
        for i, c in enumerate(countries, 1):
            lines.append(f"{i}. {c['emoji']} {c['name']} — {c['treasury']}💰")
        await callback.message.edit_text(
            "\n".join(lines), parse_mode="HTML",
            reply_markup=_admin_back_kb(chat_id))

    elif action == "vp":
        board = await get_vp_leaderboard(chat_id)
        lines = ["🏆 <b>Топ по VP:</b>\n"]
        for i, r in enumerate(board, 1):
            lines.append(f"{i}. {r['emoji']} {r['name']} — {r['vp']} VP")
        await callback.message.edit_text(
            "\n".join(lines), parse_mode="HTML",
            reply_markup=_admin_back_kb(chat_id))

    elif action == "countries":
        page = int(extra) if extra.isdigit() else 0
        countries = await get_all_countries(chat_id)
        if not countries:
            await callback.answer("Нет стран", show_alert=True)
            return
        PAGE_SIZE = 8
        total_pages = max(1, (len(countries) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        slice_ = countries[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        lines = [f"🌍 <b>Страны чата ({len(countries)}):</b>\n"]
        for c in slice_:
            ter     = len(await get_country_territories(chat_id, c["id"]))
            mention = f"@{c['owner_username']}" if c.get("owner_username") else f"id{c['owner_id']}"
            lines.append(f"{c['emoji']} <b>{c['name']}</b> ({mention}) | 🗺{ter} | 💰{c['treasury']} | 🪖{c['army']}")
        nav = []
        if page > 0:
            nav.append(colored_button("◀️", "default", callback_data=f"adm:{chat_id}:countries:{page-1}"))
        nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(colored_button("▶️", "default", callback_data=f"adm:{chat_id}:countries:{page+1}"))
        kb_rows = [nav] if len(nav) > 1 else []
        kb_rows.append([colored_button("🔙 Назад", "default", callback_data=f"adm:{chat_id}:back")])
        await callback.message.edit_text(
            "\n".join(lines), parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))

    elif action == "delete_list":
        page = int(extra) if extra.isdigit() else 0
        countries = await get_all_countries(chat_id)
        if not countries:
            await callback.answer("Нет стран", show_alert=True)
            return
        PAGE_SIZE = 8
        total_pages = max(1, (len(countries) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        slice_ = countries[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        rows = [[InlineKeyboardButton(
            text=f"🗑 {c['emoji']} {c['name']}",
            callback_data=f"adm:{chat_id}:delete:{c['id']}"
        )] for c in slice_]
        nav = []
        if page > 0:
            nav.append(colored_button("◀️", "default", callback_data=f"adm:{chat_id}:delete_list:{page-1}"))
        nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(colored_button("▶️", "default", callback_data=f"adm:{chat_id}:delete_list:{page+1}"))
        if len(nav) > 1:
            rows.append(nav)
        rows.append([colored_button("🔙 Назад", "default", callback_data=f"adm:{chat_id}:back")])
        await callback.message.edit_text(
            f"🗑 <b>Выберите страну для удаления:</b> (стр. {page+1}/{total_pages})",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    elif action == "delete":
        country_id = int(extra) if extra.isdigit() else 0
        db = await _open_db()
        async with db:
            c = await db.execute("SELECT * FROM countries WHERE id=? AND chat_id=?", (country_id, chat_id))
            country = await c.fetchone()
            if not country:
                await callback.answer("Страна не найдена", show_alert=True)
                return
            await db.execute("DELETE FROM countries WHERE id=?", (country_id,))
            await db.execute("UPDATE territories SET country_id=NULL WHERE country_id=?", (country_id,))
            await db.commit()
        await callback.message.edit_text(
            f"✅ Страна {country['emoji']} <b>{country['name']}</b> удалена администратором.",
            parse_mode="HTML",
            reply_markup=_admin_back_kb(chat_id))

    elif action == "give_res":
        await callback.message.edit_text(
            "💸 <b>Выдача ресурса</b>\n\n"
            "Использование: <code>/admres @username ресурс количество</code>\n\n"
            "Ресурсы: <code>treasury</code> (золото), <code>food</code>, <code>production</code>, "
            "<code>science</code>, <code>oil</code>, <code>rare_metals</code>",
            parse_mode="HTML", reply_markup=_admin_back_kb(chat_id))

    elif action == "transfer_ter":
        await callback.message.edit_text(
            "🗺 <b>Передача территории</b>\n\n"
            "Использование: <code>/admtransfer ISO @username</code>\n\n"
            "Например: <code>/admtransfer DE @player</code>\n"
            "Передаёт территорию ISO указанному игроку.",
            parse_mode="HTML", reply_markup=_admin_back_kb(chat_id))

    elif action == "reset_cd":
        await callback.message.edit_text(
            "🔄 <b>Сброс кулдауна</b>\n\nОтправьте команду:\n<code>/admresetcd @username</code>",
            parse_mode="HTML", reply_markup=_admin_back_kb(chat_id))

    elif action == "stats":
        db = await _open_db()
        async with db:
            c1 = await db.execute("SELECT COUNT(*) FROM countries    WHERE chat_id=?", (chat_id,))
            c2 = await db.execute("SELECT COUNT(*) FROM territories  WHERE chat_id=? AND country_id IS NOT NULL", (chat_id,))
            c3 = await db.execute("SELECT COUNT(*) FROM wars         WHERE chat_id=? AND status='active'", (chat_id,))
            c4 = await db.execute("SELECT COUNT(*) FROM buildings    WHERE chat_id=?", (chat_id,))
            nc = (await c1.fetchone())[0]
            nt = (await c2.fetchone())[0]
            nw = (await c3.fetchone())[0]
            nb = (await c4.fetchone())[0]
        await callback.message.edit_text(
            "���� <b>Статистика чата:</b>\n\n"
            f"🌍 Стран: {nc}\n"
            f"🗺 Занятых территорий: {nt}\n"
            f"⚔️ Активных войн: {nw}\n"
            f"🏗 Зданий построено: {nb}",
            parse_mode="HTML", reply_markup=_admin_back_kb(chat_id))

    elif action == "back":
        await callback.message.edit_text(
            "🛡 <b>Админ-панель</b>",
            reply_markup=_admin_main_kb(chat_id),
            parse_mode="HTML")

    await callback.answer()


@router_admin.message(Command("admres"))
async def cmd_admres(message: Message):
    """/admres @username ресурс количество — выдать любой ресурс"""
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 4:
        await message.answer("Использование: /admres @username ресурс количество\nРесурсы: treasury, food, production, science, oil, rare_metals")
        return
    username = args[1].lstrip("@")
    res = args[2].lower()
    RES_ALIASES = {
        "золото": "treasury", "gold": "treasury",
        "еда": "food", "производство": "production",
        "наука": "science", "нефть": "oil", "металлы": "rare_metals",
    }
    res = RES_ALIASES.get(res, res)
    VALID = {"treasury", "food", "production", "science", "oil", "rare_metals"}
    if res not in VALID:
        await message.answer(f"❌ Неизвестный ресурс: {res}\nДоступные: {', '.join(VALID)}")
        return
    try:
        amount = int(args[3])
    except ValueError:
        await message.answer("❌ Укажите число")
        return

    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM countries WHERE chat_id=? AND owner_username=? COLLATE NOCASE",
            (message.chat.id, username)
        )
        country = await c.fetchone()
        if not country:
            await message.answer(f"❌ Страна @{username} не найдена")
            return
        if res == "treasury":
            await db.execute("UPDATE countries SET treasury=treasury+? WHERE id=?", (amount, country["id"]))
        else:
            await db.execute(f"UPDATE resources SET {res}={res}+? WHERE country_id=?", (amount, country["id"]))
        await db.commit()

    res_labels = {"treasury": "💰 золото", "food": "🌾 еда", "production": "⚙️ производство",
                  "science": "🔬 наука", "oil": "🛢 нефть", "rare_metals": "💎 металлы"}
    await message.answer(f"✅ Выдано {amount} {res_labels.get(res, res)} → {country['emoji']} {country['name']} (@{username})", parse_mode="HTML")


@router_admin.message(Command("admtransfer"))
async def cmd_admtransfer(message: Message):
    """/admtransfer ISO @username — передать территорию игроку"""
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 3:
        await message.answer("Использование: /admtransfer ISO @username\nПример: /admtransfer DE @player")
        return
    iso = args[1].upper()
    username = args[2].lstrip("@")

    db = await _open_db()
    async with db:
        # Находим страну получателя
        c = await db.execute(
            "SELECT * FROM countries WHERE chat_id=? AND owner_username=? COLLATE NOCASE",
            (message.chat.id, username)
        )
        country = await c.fetchone()
        if not country:
            await message.answer(f"❌ Страна @{username} не найдена")
            return
        # Кто сейчас владеет территорией
        tc = await db.execute(
            "SELECT c.name, c.emoji FROM territories t "
            "LEFT JOIN countries c ON c.id=t.country_id "
            "WHERE t.chat_id=? AND t.iso=?",
            (message.chat.id, iso)
        )
        ter = await tc.fetchone()
        # Передаём территорию
        await db.execute(
            "INSERT OR REPLACE INTO territories (chat_id, iso, country_id) VALUES (?,?,?)",
            (message.chat.id, iso, country["id"])
        )
        await db.commit()

    ter_name = COUNTRY_NAMES.get(iso, iso)
    prev = f"{ter['emoji']} {ter['name']}" if ter and ter["name"] else "нейтральная"
    await message.answer(
        f"✅ Территория <b>{ter_name} ({iso})</b> передана {country['emoji']} {country['name']} (@{username})\n"
        f"Прежний владелец: {prev}",
        parse_mode="HTML"
    )


@router_admin.message(Command("admgold"))
async def cmd_admgold(message: Message):
    """Выдать золото игроку: /admgold @username количество"""
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 3:
        await message.answer("Использование: /admgold @username количество")
        return
    username = args[1].lstrip("@")
    try:
        amount = int(args[2])
    except ValueError:
        await message.answer("❌ Укажите число")
        return
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM countries WHERE chat_id=? AND owner_username=? COLLATE NOCASE",
            (message.chat.id, username)
        )
        country = await c.fetchone()
        if not country:
            await message.answer(f"❌ Страна @{username} не найдена")
            return
        await db.execute("UPDATE countries SET treasury=treasury+? WHERE id=?", (amount, country["id"]))
        await db.commit()
    await message.answer(f"✅ Выдано {amount}💰 стране {country['emoji']} {country['name']} (@{username})", parse_mode="HTML")


# ── СБРОС КАРТЫ (АДМИН) ─────────────────────────────────────────────

_RESET_MAP_TABLES = [
    "deployments", "bombardments", "trade_ships", "ship_captures", "spy_ops",
    "claims", "war_weariness", "wars", "pacts", "alliances", "bloc_members",
    "blocs", "un_resolutions", "coalitions", "coalition_members",
    "victory_points", "trade_deals", "vassals", "territory_market",
    "garrisons", "buildings", "technologies", "military", "resources",
    "achievements", "cooldowns", "protest_log", "event_log",
    "newspaper_state", "territories",
]


async def _reset_chat_map(chat_id: int) -> dict:
    db = await _open_db()
    removed = {}
    async with db:
        cur = await db.execute("SELECT id FROM countries WHERE chat_id=?", (chat_id,))
        ids = [r[0] for r in await cur.fetchall()]
        for table in _RESET_MAP_TABLES:
            tcur = await db.execute(f"PRAGMA table_info({table})")
            cols = [r[1] for r in await tcur.fetchall()]
            if not cols:
                continue
            if "chat_id" in cols:
                dcur = await db.execute(f"DELETE FROM {table} WHERE chat_id=?", (chat_id,))
            elif "country_id" in cols and ids:
                ph = ",".join(["?"] * len(ids))
                dcur = await db.execute(f"DELETE FROM {table} WHERE country_id IN ({ph})", ids)
            else:
                continue
            removed[table] = max(0, dcur.rowcount or 0)
        dcur = await db.execute("DELETE FROM countries WHERE chat_id=?", (chat_id,))
        removed["countries"] = max(0, dcur.rowcount or 0)
        await db.commit()
    return removed


@router_admin.message(Command("resetmap"))
async def cmd_resetmap(message: Message):
    if not is_admin(message.from_user.id):
        return
    if message.chat.type == "private":
        await message.answer("Сброс карты — только в групповом чате.")
        return
    chat_id = message.chat.id
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT COUNT(*) FROM countries WHERE chat_id=?", (chat_id,))
        n_countries = (await c.fetchone())[0]
        c = await db.execute(
            "SELECT COUNT(*) FROM territories WHERE chat_id=? AND country_id IS NOT NULL",
            (chat_id,),
        )
        n_terr = (await c.fetchone())[0]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, сбросить карту", callback_data=f"resetmap:go:{chat_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="resetmap:cancel")],
    ])
    await message.answer(
        "⚠️ <b>Сброс карты</b>\n\n"
        f"Будет удалено: <b>{n_countries}</b> стран, <b>{n_terr}</b> занятых территорий, "
        "а также войны, союзы, гарнизоны, постройки, технологии, рынок и статистика.\n\n"
        "❗️ Действие необратимо. Продолжить?",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router_admin.callback_query(F.data.startswith("resetmap:"))
async def cb_resetmap(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) >= 2 and parts[1] == "cancel":
        await callback.answer("Отменено")
        try:
            await callback.message.edit_text("❌ Сброс карты отменён.")
        except Exception:
            pass
        return
    if len(parts) < 3 or not parts[2].lstrip("-").isdigit():
        await callback.answer("Ошибка", show_alert=True)
        return
    chat_id = int(parts[2])
    removed = await _reset_chat_map(chat_id)
    total = sum(removed.values())
    await callback.answer("Карта сброшена")
    try:
        await callback.message.edit_text(
            "✅ <b>Карта сброшена!</b>\n\n"
            f"Стран удалено: <b>{removed.get('countries', 0)}</b>\n"
            f"Записей очищено: <b>{total}</b>\n\n"
            "Все могут заново основать страну: <code>/create</code>",
            parse_mode="HTML",
        )
    except Exception:
        pass


@router_admin.message(Command("admresetcd"))
async def cmd_admresetcd(message: Message):
    """Сбросить кулдауны игрока: /admresetcd @username или /admresetcd USER_ID"""
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "Использование:\n"
            "<code>/admresetcd @username</code>\n"
            "<code>/admresetcd 123456789</code> (по Telegram ID)\n\n"
            "Сбрасывает кулдауны: создание/удаление страны.",
            parse_mode="HTML"
        )
        return

    arg = args[1].lstrip("@")
    target_user_id = None

    # Пробуем найти по числовому ID
    if arg.isdigit():
        target_user_id = int(arg)
    else:
        # Ищем по username в БД (case-insensitive)
        db = await _open_db()
        async with db:
            c = await db.execute(
                "SELECT owner_id, owner_username, name, emoji FROM countries "
                "WHERE chat_id=? AND LOWER(owner_username)=LOWER(?)",
                (message.chat.id, arg)
            )
            row = await c.fetchone()
            if row:
                target_user_id = row["owner_id"]

        # Если по username не нашли — пробуем через Telegram API
        if not target_user_id:
            try:
                member = await message.bot.get_chat_member(message.chat.id, f"@{arg}")
                target_user_id = member.user.id
            except Exception:
                pass

    if not target_user_id:
        # Показываем список всех игроков чата чтобы помочь найти нужного
        db = await _open_db()
        async with db:
            c = await db.execute(
                "SELECT owner_id, owner_username, name, emoji FROM countries WHERE chat_id=? ORDER BY name",
                (message.chat.id,)
            )
            all_countries = [dict(r) for r in await c.fetchall()]
        lines = ["❌ Игрок не найден. Игроки в этом чате:\n"]
        for ct in all_countries:
            uname = f"@{ct['owner_username']}" if ct["owner_username"] else f"ID:{ct['owner_id']}"
            lines.append(f"{ct['emoji']} {ct['name']} — {uname} (ID: <code>{ct['owner_id']}</code>)")
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    # Ищем страну для красивого отображения (необязательно — КД сбросим в любом случае)
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT owner_username, name, emoji FROM countries WHERE chat_id=? AND owner_id=?",
            (message.chat.id, target_user_id)
        )
        country_row = await c.fetchone()

        # Сбрасываем все кулдауны независимо от наличия страны
        await db.execute(
            "DELETE FROM cooldowns WHERE user_id=? AND chat_id=?",
            (target_user_id, message.chat.id)
        )
        await db.commit()

    if country_row:
        display = f"@{country_row['owner_username']}" if country_row["owner_username"] else f"ID:{target_user_id}"
        await message.answer(
            f"✅ Кулдауны сброшены для {country_row['emoji']} <b>{country_row['name']}</b> ({display})\n"
            f"<i>Игрок теперь может создать страну заново.</i>",
            parse_mode="HTML"
        )
    else:
        await message.answer(
            f"✅ Кулдауны сброшены для ID <code>{target_user_id}</code>\n"
            f"<i>Страны у игрока нет — теперь он может создать её через /create.</i>",
            parse_mode="HTML"
        )


@router_dev.message(Command("resources"))
async def cmd_resources(message: Message):
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны! /create")
        return
    res = await get_resources(country["id"])
    mil = await get_military(country["id"])
    tech = await get_technologies(country["id"])
    ter_count = len(await get_country_territories(message.chat.id, country["id"]))
    era_name = TECH_TREE.get(tech["era"], {}).get("name", tech["era"])
    learned_count = len(tech["techs"])
    pop_raw = res["population"] if res else 0
    # Реальное население = территории × 1 000 000
    # Если территорий нет (старая страна) — показываем население из БД
    real_pop = ter_count * POPULATION_PER_TERRITORY if ter_count > 0 else pop_raw
    happiness = res.get("happiness", 70) if res else 70

    lines = [
        f"{country['emoji']} <b>{country['name']}</b>",
        "─" * 25,
        f"👥 Население: {real_pop:,} чел. ({ter_count} территорий × 1 млн)",
        f"  {happiness_emoji(happiness)} Счастье: {happiness}/100 — <b>{happiness_label(happiness)}</b>",
        f"🌍 Территорий: {ter_count}",
        f"💰 Казна: {country['treasury']}💰",
        "",
        "<b>📦 Ресурсы:</b>",
        f"  🌾 Еда: {res['food'] if res else 0}",
        f"  ⚙️ Производство: {res['production'] if res else 0}",
        f"  💎 Редкие металлы: {res['rare_metals'] if res else 0}",
        f"  🛢 Нефть: {res['oil'] if res else 0}",
        f"  📚 Наука: {res['science'] if res else 0}",
        "",
        "<b>⚔️ Армия:</b>",
    ]
    if mil:
        lines += [
            f"  👣 Пехота: {mil['infantry']}",
            f"  🐴 Кавалерия: {mil['cavalry']}",
            f"  💣 Артиллерия: {mil['artillery']}",
            f"  ✈️ Авиация: {mil['airforce']}",
            f"  ⚓ Флот: {mil.get('navy', 0)}",
        ]
    lines += [
        "",
        f"<b>🔬 Эра:</b> {era_name} ({learned_count} технологий изучено)",
        "",
        "/build — постройки | /research — технологии | /military — армия",
    ]
    await message.answer("\n".join(lines), parse_mode="HTML")


@router_dev.message(Command("happiness"))
async def cmd_happiness(message: Message):
    """Показывает детальную сводку счастья и протестов страны."""
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны! /create")
        return

    res = await get_resources(country["id"])
    ter_count = len(await get_country_territories(message.chat.id, country["id"]))
    happiness = res.get("happiness", 70) if res else 70
    real_pop  = ter_count * POPULATION_PER_TERRITORY

    # Считаем текущие факторы
    factors = []
    food = res["food"] if res else 0
    if food == 0:
        factors.append(f"🌾 Голод: {HAPPINESS_FACTORS['food_zero']:+d}")
    elif food < 20:
        factors.append(f"🌾 Нехватка еды: {HAPPINESS_FACTORS['food_low']:+d}")
    else:
        factors.append(f"🌾 Еда в норме: {HAPPINESS_FACTORS['food_ok']:+d}")

    oil = res["oil"] if res else 0
    if oil == 0:
        factors.append(f"🛢 Нет нефти: {HAPPINESS_FACTORS['oil_zero']:+d}")

    prod = res["production"] if res else 0
    if prod < 10:
        factors.append(f"⚙️ Слабая промышленность: {HAPPINESS_FACTORS['production_low']:+d}")

    if country["treasury"] > 2000:
        factors.append(f"💰 Богатая казна: {HAPPINESS_FACTORS['rich']:+d}")
    elif country["treasury"] < 200:
        factors.append(f"💰 Пустая казна: {HAPPINESS_FACTORS['poor']:+d}")

    # Протесты из лога (последние 5)
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT iso, cause, created_at FROM protest_log WHERE chat_id=? AND country_id=? "
            "ORDER BY created_at DESC LIMIT 5",
            (message.chat.id, country["id"])
        )
        protests = [dict(r) for r in await c.fetchall()]

    cause_labels = {
        "revolution": "🔴 Революция",
        "mass_revolt": "😡 Массовые протесты",
        "strike": "✊ Забастовка",
        "unknown": "❓ Неизвестно",
    }

    lines = [
        f"{country['emoji']} <b>{country['name']} — Счастье народа</b>",
        "─" * 28,
        f"{happiness_emoji(happiness)} <b>{happiness}/100</b> — {happiness_label(happiness)}",
        f"👥 Население: {real_pop:,} человек ({ter_count} территорий)",
        "",
        "<b>📊 Факторы (изменение за тик):</b>",
    ]
    for f in factors:
        lines.append(f"  {f}")

    lines += [
        "",
        "<b>💡 Как повысить счастье:</b>",
        "  🌾 Держите еду > 20 (стройте фермы)",
        "  🛢 Обеспечьте нефть (нефтяные вышки)",
        "  💰 Поддерживайте казну > 200",
        "  ☮️ Избегайте затяжных войн",
    ]

    if protests:
        lines += ["", "<b>📜 Последние события:</b>"]
        for p in protests:
            ts = p["created_at"]
            import time as _t
            ago = int(_t.time()) - ts
            if ago < 3600:
                when = f"{ago // 60}м назад"
            else:
                when = f"{ago // 3600}ч назад"
            ter_n = COUNTRY_NAMES.get(p["iso"], p["iso"])
            lines.append(f"  {cause_labels.get(p['cause'], p['cause'])} в {ter_n} ({when})")

    # Предупреждение
    if happiness <= REVOLT_THRESHOLD:
        lines += ["", "⚠️ <b>ОПАСНО!</b> Народ готов к массовому восстанию!"]
    elif happiness <= PROTEST_THRESHOLD:
        lines += ["", "⚠️ <b>Внимание!</b> Возможны забастовки и протесты."]

    await message.answer("\n".join(lines), parse_mode="HTML")


@router_dev.message(Command("build"))
async def cmd_build(message: Message):
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны! /create")
        return

    args = message.text.split()
    if len(args) == 1:
        res = await get_resources(country["id"])
        prod = res["production"] if res else 0
        lines = [
            f"🏗 <b>Строительство</b>  |  💰{country['treasury']}  ⚙️{prod}",
            "Использование: <code>/build ISO тип</code>",
            "",
        ]
        for btype, bdef in BUILDING_DEFS.items():
            bonus_str = ", ".join(f"+{v} {RESOURCE_NAMES.get(k, k)}" for k, v in bdef["provides"].items())
            cost_lv1_g, cost_lv1_p = building_level_cost(bdef, 1)
            cost_max_g, cost_max_p = building_level_cost(bdef, bdef["max_level"])
            lines.append(
                f"{bdef['emoji']} <b>{bdef['name']}</b> (<code>{btype}</code>)\n"
                f"  Ур.1: {cost_lv1_g}💰+{cost_lv1_p}⚙️ → Макс ур.{bdef['max_level']}: {cost_max_g}💰+{cost_max_p}⚙️\n"
                f"  Даёт: {bonus_str}"
            )
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    if len(args) < 3:
        await message.answer(
            "Использование: <code>/build ISO тип</code>\nПример: <code>/build RU farm</code>",
            parse_mode="HTML"
        )
        return

    iso = args[1].upper()
    btype = args[2].lower()
    our_territories = await get_country_territories(message.chat.id, country["id"])
    if iso not in our_territories:
        await message.answer(f"❌ Территория {iso} вам не принадлежит!")
        return

    ok, msg = await build_building(message.chat.id, country["id"], iso, btype)
    await message.answer(("✅ " if ok else "❌ ") + msg)
    if ok:
        await check_achievements(message.chat.id, country["id"])


@router_economy.callback_query(F.data.startswith("build:"))
async def cb_build(callback: CallbackQuery):
    """Обработчик кнопок строительства — /build ISO → кнопки зданий."""
    # format: build:<iso>:<btype>|<user_id>
    raw   = callback.data.split("|")[0]
    parts = raw.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных")
        return
    iso, btype = parts[1], parts[2]
    user_id = callback.from_user.id
    country = await get_country_by_owner(callback.message.chat.id, user_id)
    if not country:
        await callback.answer("У вас нет страны!", show_alert=True)
        return
    our_territories = await get_country_territories(callback.message.chat.id, country["id"])
    if iso not in our_territories:
        await callback.answer(f"Территория {iso} вам не принадлежит!", show_alert=True)
        return
    ok, msg = await build_building(callback.message.chat.id, country["id"], iso, btype)
    await callback.answer(("✅ " if ok else "❌ ") + msg[:200], show_alert=not ok)
    if ok:
        await check_achievements(callback.message.chat.id, country["id"])
        keyboard = await _build_keyboard(callback.message.chat.id, country["id"], iso, user_id, page=0)
        try:
            await callback.message.edit_reply_markup(reply_markup=keyboard)
        except Exception:
            pass


async def _build_keyboard(chat_id: int, country_id: int, iso: str, owner_id: int = 0, page: int = 0) -> InlineKeyboardMarkup:
    """Генерирует клавиатуру с кнопками зданий для территории ISO с пагинацией."""
    existing = {}
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT type, level FROM buildings WHERE chat_id=? AND country_id=? AND iso=?",
            (chat_id, country_id, iso)
        )
        for row in await c.fetchall():
            existing[row["type"]] = row["level"]

    all_btypes = list(BUILDING_DEFS.items())
    PAGE_SIZE = 6
    total_pages = max(1, (len(all_btypes) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    slice_ = all_btypes[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    rows = []
    for btype, bdef in slice_:
        cur = existing.get(btype, 0)
        max_lv = bdef["max_level"]
        if cur >= max_lv:
            label = f"{bdef['emoji']} {bdef['name']} ур.{cur} ✅"
        else:
            next_lv = cur + 1
            cost_g, cost_p = building_level_cost(bdef, next_lv)
            cost_str = f"{cost_g}💰+{cost_p}⚙️"
            label = f"{bdef['emoji']} {bdef['name']} {'ур.'+str(cur)+'→'+str(next_lv) if cur else 'построить'} ({cost_str})"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"build:{iso}:{btype}|{owner_id}")])

    # Навигация
    nav = []
    if page > 0:
        nav.append(colored_button("◀️", "default", callback_data=f"build_page:{iso}:{page-1}|{owner_id}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(colored_button("▶️", "default", callback_data=f"build_page:{iso}:{page+1}|{owner_id}"))
    if len(nav) > 1:
        rows.append(nav)
    # Кнопка назад к списку территорий
    rows.append([colored_button("🔙 К территориям", "default", callback_data=f"buildmenu_page:0|{owner_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router_economy.message(Command("buildmenu"))
async def cmd_buildmenu(message: Message):
    """Показывает меню строительства с кнопками для конкретной территории."""
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны! /create")
        return

    args = message.text.split()
    page = 0
    if len(args) >= 3 and args[-1].isdigit():
        page = int(args[-1])

    if len(args) < 2 or (len(args) == 2 and args[1].isdigit()):
        # Показать список территорий с пагинацией
        territories = sorted(await get_country_territories(message.chat.id, country["id"]))
        if not territories:
            await message.answer("❌ У вас нет территорий!")
            return
        PAGE_SIZE = 8
        total_pages = max(1, (len(territories) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        slice_ = territories[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        rows = []
        for iso in slice_:
            name = COUNTRY_NAMES.get(iso, iso)
            rows.append([InlineKeyboardButton(text=f"📍 {name} ({iso})", callback_data=f"buildmenu:{iso}|{message.from_user.id}")])
        # Кнопки навигации
        nav = []
        if page > 0:
            nav.append(colored_button("◀️", "default", callback_data=f"buildmenu_page:{page-1}|{message.from_user.id}"))
        nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(colored_button("▶️", "default", callback_data=f"buildmenu_page:{page+1}|{message.from_user.id}"))
        if len(nav) > 1:
            rows.append(nav)
        await message.answer(
            f"🏗 <b>Выберите территорию для строительства:</b> (стр. {page+1}/{total_pages})",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML"
        )
        return

    iso = args[1].upper()
    our_territories = await get_country_territories(message.chat.id, country["id"])
    if iso not in our_territories:
        await message.answer(f"❌ Территория {iso} вам не принадлежит!")
        return

    keyboard = await _build_keyboard(message.chat.id, country["id"], iso, message.from_user.id)
    ter_name = COUNTRY_NAMES.get(iso, iso)
    res = await get_resources(country["id"])
    prod = res["production"] if res else 0
    await message.answer(
        f"🏗 <b>Строительство — {ter_name} ({iso})</b>\n💰 Казна: {country['treasury']} | ⚙️ Производство: {prod}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router_economy.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


@router_economy.callback_query(F.data.startswith("build_page:"))
async def cb_build_page(callback: CallbackQuery):
    """Пагинация зданий: build_page:<iso>:<page>|<user_id>"""
    raw = callback.data.split("|")[0]
    parts = raw.split(":")
    iso  = parts[1]
    page = int(parts[2])
    user_id = callback.from_user.id
    country = await get_country_by_owner(callback.message.chat.id, user_id)
    if not country:
        await callback.answer("У вас нет страны!", show_alert=True)
        return
    keyboard = await _build_keyboard(callback.message.chat.id, country["id"], iso, user_id, page)
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception:
        pass
    await callback.answer()


@router_economy.callback_query(F.data.startswith("buildmenu_page:"))
async def cb_buildmenu_page(callback: CallbackQuery):
    """Пагинация территорий: buildmenu_page:<page>|<user_id>"""
    raw  = callback.data.split("|")[0]
    page = int(raw.split(":")[1])
    user_id = callback.from_user.id
    country = await get_country_by_owner(callback.message.chat.id, user_id)
    if not country:
        await callback.answer("У вас нет страны!", show_alert=True)
        return
    territories = sorted(await get_country_territories(callback.message.chat.id, country["id"]))
    PAGE_SIZE = 8
    total_pages = max(1, (len(territories) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    slice_ = territories[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    rows = []
    for iso in slice_:
        name = COUNTRY_NAMES.get(iso, iso)
        rows.append([InlineKeyboardButton(text=f"📍 {name} ({iso})", callback_data=f"buildmenu:{iso}|{user_id}")])
    nav = []
    if page > 0:
        nav.append(colored_button("◀️", "default", callback_data=f"buildmenu_page:{page-1}|{user_id}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(colored_button("▶️", "default", callback_data=f"buildmenu_page:{page+1}|{user_id}"))
    if len(nav) > 1:
        rows.append(nav)
    try:
        await callback.message.edit_text(
            f"🏗 <b>Выберите территорию для строительства:</b> (стр. {page+1}/{total_pages})",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer()


@router_economy.callback_query(F.data.startswith("buildmenu:"))
async def cb_buildmenu(callback: CallbackQuery):
    # format: buildmenu:<iso>|<user_id>
    raw = callback.data.split("|")[0]
    iso = raw.split(":")[1]
    user_id = callback.from_user.id
    country = await get_country_by_owner(callback.message.chat.id, user_id)
    if not country:
        await callback.answer("У вас нет страны!", show_alert=True)
        return
    keyboard = await _build_keyboard(callback.message.chat.id, country["id"], iso, user_id, page=0)
    ter_name = COUNTRY_NAMES.get(iso, iso)
    res = await get_resources(country["id"])
    prod = res["production"] if res else 0
    await callback.message.edit_text(
        f"🏗 <b>Строительство — {ter_name} ({iso})</b>\n💰 Казна: {country['treasury']} | ⚙️ Производство: {prod}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


@router_dev.message(Command("buildings"))
async def cmd_buildings(message: Message):
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны! /create")
        return

    buildings = await get_buildings(message.chat.id, country["id"])
    if not buildings:
        await message.answer("🏗 У вас нет построенных зданий. Используйте /build")
        return

    lines = [f"🏛 <b>Здания — {country['emoji']} {country['name']}</b>", ""]
    grouped: dict = {}
    for b in buildings:
        grouped.setdefault(b["iso"], []).append(b)
    for iso, blist in grouped.items():
        ter_name = COUNTRY_NAMES.get(iso, iso)
        lines.append(f"📍 <b>{ter_name}</b> ({iso})")
        for b in blist:
            bdef = BUILDING_DEFS.get(b["type"], {})
            lines.append(f"  {bdef.get('emoji', '🏗')} {bdef.get('name', b['type'])} ур.{b['level']}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router_dev.message(Command("research"))
async def cmd_research(message: Message):
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны! /create")
        return

    args = message.text.split()
    tech_data = await get_technologies(country["id"])
    res = await get_resources(country["id"])
    learned = tech_data["techs"]
    science = res["science"] if res else 0

    if len(args) == 1:
        lines = [f"🔬 <b>Дерево технологий</b>  |  📚 Наука: {science}", ""]
        for era_key, era_data in TECH_TREE.items():
            is_current = tech_data["era"] == era_key
            prefix = "▶️" if is_current else "  "
            lines.append(f"{prefix} <b>{era_data['name']}</b>")
            for tid, tdef in era_data["techs"].items():
                if tid in learned:
                    status = "✅"
                elif science >= tdef["cost_science"]:
                    status = "📚"
                else:
                    status = "🔒"
                lines.append(f"  {status} {tdef['name']} (<code>{tid}</code>) — {tdef['cost_science']}📚")
            lines.append("")
        lines.append("Использование: <code>/research tech_id</code>")
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    tech_id = args[1].lower()
    ok, msg = await research_tech(message.chat.id, country["id"], tech_id)
    await message.answer(("✅ " if ok else "❌ ") + msg)


@router_dev.message(Command("military"))
async def cmd_military(message: Message):
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны! /create")
        return

    args = message.text.split()
    mil = await get_military(country["id"])
    res = await get_resources(country["id"])

    if len(args) == 1:
        prod = res["production"] if res else 0
        lines = [
            f"⚔️ <b>Армия — {country['emoji']} {country['name']}</b>",
            f"💰 Казна: {country['treasury']}  ⚙️ Производство: {prod}",
            "",
        ]
        for utype, udef in UNIT_DEFS.items():
            count = mil.get(utype, 0) if mil else 0
            cost_str = f"{udef['cost_gold']}💰"
            if udef["cost_prod"]:
                cost_str += f" + {udef['cost_prod']}⚙️"
            special = []
            if udef.get("can_cross_sea"):
                special.append("🌊 морской десант")
            if udef.get("expand_bonus"):
                special.append("быстрая экспансия")
            if udef.get("siege_bonus"):
                special.append("бонус осады")
            if udef.get("long_range"):
                special.append("🌍 дальние атаки")
            extra = f"  [{', '.join(special)}]" if special else ""
            lines.append(
                f"{udef['emoji']} <b>{udef['name']}</b>: {count}\n"
                f"  Цена: {cost_str}  Атк: ×{udef['attack']}  Защ: ×{udef['defense']}{extra}"
            )
        lines += ["", "Использование: <code>/military тип кол-во</code>"]
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    if len(args) < 3:
        await message.answer(
            "Использование: <code>/military тип кол-во</code>",
            parse_mode="HTML"
        )
        return

    unit_type = " ".join(args[1:-1]).lower()
    try:
        amount = int(args[-1])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите положительное число!")
        return

    ok, msg = await buy_units(message.chat.id, message.from_user.id, unit_type, amount)
    await message.answer(("✅ " if ok else "❌ ") + msg)


@router_dev.message(Command("search"))
async def cmd_search(message: Message):
    """Поиск страны или территории по названию / ISO / игроку."""
    if message.chat.type == "private":
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "🔍 <b>Поиск</b>\n"
            "Использование: <code>/search запрос</code>\n\n"
            "Примеры:\n"
            "  <code>/search Россия</code> — найти страну по названию\n"
            "  <code>/search RU</code> — найти по ISO-коду территории\n"
            "  <code>/search Иван</code> — найти страну игрока",
            parse_mode="HTML"
        )
        return

    query = args[1].strip()
    query_upper = query.upper()
    query_lower = query.lower()
    results = []

    all_countries = await get_all_countries(message.chat.id)
    territories = await get_territories(message.chat.id)

    for c in all_countries:
        matched = False
        match_reason = ""

        # Совпадение по ISO столицы
        if c["iso"].upper() == query_upper:
            matched = True
            match_reason = f"ISO столицы: {c['iso']}"

        # Совпадение по названию страны
        elif query_lower in c["name"].lower():
            matched = True
            match_reason = "название страны"

        # Совпадение по ISO в COUNTRY_NAMES (ищем территорию игрока)
        else:
            for iso, cid in territories.items():
                if cid != c["id"]:
                    continue
                iso_name = COUNTRY_NAMES.get(iso, "")
                real_name = COUNTRY_NAMES.get(iso, "")
                if iso.upper() == query_upper or query_lower in iso_name.lower():
                    matched = True
                    match_reason = f"территория {iso}"
                    break

        if matched:
            ter_list = await get_country_territories(message.chat.id, c["id"])
            results.append((c, ter_list, match_reason))

    # Поиск по ISO в COUNTRY_NAMES + реальные названия из REAL_NAMES
    geo_results = []
    seen_isos = set()
    for iso in set(list(COUNTRY_NAMES.keys()) + list(REAL_NAMES.keys())):
        if iso in seen_isos:
            continue
        rp_name   = COUNTRY_NAMES.get(iso, "")
        real      = REAL_NAMES.get(iso, {})
        real_en   = real.get("en", "")
        real_ru   = real.get("ru", "")
        if (iso.upper() == query_upper
                or query_lower in rp_name.lower()
                or query_lower in real_en.lower()
                or query_lower in real_ru.lower()):
            owner_id = territories.get(iso.upper())
            owner = next((c for c in all_countries if c["id"] == owner_id), None) if owner_id else None
            display = rp_name or real_ru or real_en or iso
            geo_results.append((iso, display, real_en, real_ru, owner))
            seen_isos.add(iso)

    if not results and not geo_results:
        await message.answer(f"🔍 По запросу «{query}» ничего не найдено.")
        return

    lines = [f"🔍 <b>Результаты поиска: «{query}»</b>", ""]

    # Страны
    if results:
        lines.append("<b>🏛 Страны:</b>")
        for c, ter_list, reason in results[:5]:
            lines.append(
                f"  {c['emoji']} <b>{c['name']}</b> (ISO: {c['iso']})\n"
                f"  💰 {c['treasury']} | 👣 {c['army']} | 🌍 {len(ter_list)} территорий\n"
                f"  <i>Совпадение: {reason}</i>"
            )
        lines.append("")

    # Территории из GeoJSON
    if geo_results:
        lines.append("<b>🗺 Территории:</b>")
        for iso, rp_name, real_en, real_ru, owner in geo_results[:8]:
            if owner:
                owner_str = f"владелец: {owner['emoji']} {owner['name']}"
            else:
                owner_str = "нейтральная"
            # Показываем: рп-название (реальное рус. / англ.)
            real_str = real_ru or real_en
            if rp_name and real_str and rp_name != real_str:
                display_name = f"{rp_name} <i>({real_str})</i>"
            else:
                display_name = rp_name or real_str or iso
            lines.append(f"  📍 <b>{display_name}</b> ({iso}) — {owner_str}")

    await message.answer("\n".join(lines), parse_mode="HTML")




# ═══════════════════════════════════════════════════════════════
#  HANDLERS — ШПИОНАЖ
# ═══════════════════════════════════════════════════════════════

@router_dev.message(Command("spy"))
async def cmd_spy(message: Message):
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны! /create")
        return

    args = message.text.split()

    if len(args) == 1:
        lines = [
            "🕵️ <b>Шпионаж</b>",
            "Использование: <code>/spy страна операция</code>",
            "Пример: <code>/spy Россия steal_gold</code>",
            "",
            "<b>Доступные операции:</b>",
        ]
        for op_id, op in SPY_OPS.items():
            lines.append(f"{op['emoji']} <code>{op_id}</code> — {op['name']}\n  Цена: {op['cost']}💰 | Шанс: {op['base_chance']}%")
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    if len(args) < 3:
        await message.answer("Использование: <code>/spy название_страны операция</code>", parse_mode="HTML")
        return

    target_name = args[1]
    op_type = args[2].lower()

    all_countries = await get_all_countries(message.chat.id)
    target = next((c for c in all_countries if c["name"].lower() == target_name.lower()), None)
    if not target:
        await message.answer(f"❌ Страна «{target_name}» не найдена. Используйте /search")
        return
    if target["id"] == country["id"]:
        await message.answer("❌ Нельзя шпионить против самого себя!")
        return

    success, result, scandal = await do_spy_op(message.chat.id, country["id"], target["id"], op_type)
    emoji = "✅" if success else "❌"
    await message.answer(f"{emoji} {result}", parse_mode="HTML")

    # Уведомляем жертву при скандале
    if scandal:
        op_name = SPY_OPS.get(op_type, {}).get("name", op_type)
        await message.answer(
            f"\U0001f6a8 <b>Раскрытый шпион!</b>\n\n"
            f"{target['emoji']} <b>{target['name']}</b> — против вас была предпринята шпионская операция "
            f"«{op_name}» от {country['emoji']} <b>{country['name']}</b>, но агент провалился!\n"
            f"\U0001f6e1 Рекомендуется усилить контрразведку.",
            parse_mode="HTML"
        )
    await check_achievements(message.chat.id, country["id"])


# ═══════════════════════════════════════════════════════════════
#  HANDLERS — БЛОКИ
# ═══════════════════════════════════════════════════════════════

@router_dev.message(Command("bloc"))
async def cmd_bloc(message: Message):
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны! /create")
        return

    args = message.text.split(maxsplit=2)
    blocs = await get_all_blocs(message.chat.id)

    if len(args) == 1:
        my_bloc = await get_bloc_of_country(message.chat.id, country["id"])
        lines = ["🤝 <b>Военные блоки</b>", ""]
        if my_bloc:
            members = await get_bloc_members(my_bloc["id"])
            lines.append(f"Ваш блок: <b>{my_bloc['name']}</b> ({len(members)} членов)")
            for m in members:
                lines.append(f"  {m['emoji']} {m['name']}")
            lines.append("")
        if blocs:
            lines.append("<b>Все блоки:</b>")
            for b in blocs:
                members = await get_bloc_members(b["id"])
                lines.append(f"  🏳 <b>{b['name']}</b> — {len(members)} членов")
        else:
            lines.append("Блоков пока нет")
        lines += [
            "",
            "/bloc create Название — создать блок",
            "/bloc join Название — вступить в блок",
            "/bloc leave — покинуть блок",
        ]
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    sub = args[1].lower()

    if sub == "create":
        if len(args) < 3:
            await message.answer("Использование: <code>/bloc create Название</code>", parse_mode="HTML")
            return
        ok, msg = await create_bloc(message.chat.id, country["id"], args[2])
        await message.answer(("✅ " if ok else "❌ ") + msg)
        if ok:
            await grant_achievement(message.chat.id, country["id"], "bloc_leader")

    elif sub == "join":
        if len(args) < 3:
            await message.answer("Использование: <code>/bloc join Название</code>", parse_mode="HTML")
            return
        ok, msg = await join_bloc(message.chat.id, country["id"], args[2])
        await message.answer(("✅ " if ok else "❌ ") + msg)

    elif sub == "leave":
        ok, msg = await leave_bloc(message.chat.id, country["id"])
        await message.answer(("✅ " if ok else "❌ ") + msg)

    else:
        await message.answer("Подкоманды: create, join, leave")


# ═══════════════════════════════════════════════════════════════
#  HANDLERS — ТОРГОВЛЯ
# ═══════════════════════════════════════════════════════════════

@router_dev.message(Command("trade"))
async def cmd_trade(message: Message):
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны! /create")
        return

    args = message.text.split()
    deals = await get_trade_deals(message.chat.id, country["id"])

    if len(args) == 1:
        lines = [
            "📦 <b>Торговля</b>",
            "",
            "Использование:",
            "<code>/trade страна мой_ресурс кол-во их_ресурс кол-во</code>",
            "Пример ресурсов: <code>/trade США food 50 oil 20</code>",
            "Пример войск: <code>/trade США infantry 100 cavalry 30</code>",
            "",
            "<b>Ресурсы:</b> food, production, rare_metals, oil, science",
            "<b>Войска:</b> infantry, cavalry, artillery, airforce, bomber, navy",
        ]
        active  = [d for d in deals if d["status"] == "active"]
        pending_in  = [d for d in deals if d["status"] == "pending" and d["country_b_id"] == country["id"]]
        pending_out = [d for d in deals if d["status"] == "pending" and d["country_a_id"] == country["id"]]

        if pending_in:
            lines.append("\n📬 <b>Входящие предложения:</b>")
            for d in pending_in:
                a = await get_country_by_id(message.chat.id, d["country_a_id"])
                rn_a = ALL_TRADE_NAMES.get(d["resource_a"], d["resource_a"])
                rn_b = ALL_TRADE_NAMES.get(d["resource_b"], d["resource_b"])
                lines.append(
                    f"  #{d['id']} {a['emoji']} <b>{a['name']}</b> предлагает {d['amount_a']} {rn_a} "
                    f"за {d['amount_b']} {rn_b}\n"
                    f"  → <code>/trade_accept {d['id']}</code> | <code>/trade_decline {d['id']}</code>"
                )

        if pending_out:
            lines.append("\n📤 <b>Исходящие (ожидают):</b>")
            for d in pending_out:
                b = await get_country_by_id(message.chat.id, d["country_b_id"])
                rn_a = ALL_TRADE_NAMES.get(d["resource_a"], d["resource_a"])
                rn_b = ALL_TRADE_NAMES.get(d["resource_b"], d["resource_b"])
                lines.append(f"  #{d['id']} → {b['emoji']} <b>{b['name']}</b>: {d['amount_a']} {rn_a} за {d['amount_b']} {rn_b}")

        if active:
            lines.append("\n✅ <b>Активные сделки:</b>")
            for d in active:
                a = await get_country_by_id(message.chat.id, d["country_a_id"])
                b = await get_country_by_id(message.chat.id, d["country_b_id"])
                if a and b:
                    rn_a = ALL_TRADE_NAMES.get(d["resource_a"], d["resource_a"])
                    rn_b = ALL_TRADE_NAMES.get(d["resource_b"], d["resource_b"])
                    lines.append(f"  #{d['id']} {a['emoji']} {a['name']} даёт {d['amount_a']} {rn_a} → {b['emoji']} {b['name']} даёт {d['amount_b']} {rn_b}")

        if not active and not pending_in and not pending_out:
            lines.append("\nНет активных сделок.")

        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    if len(args) < 6:
        await message.answer(
            "Использование: <code>/trade страна мой_рес кол-во их_рес кол-во</code>",
            parse_mode="HTML"
        )
        return

    # Парсим с конца: последние 4 аргумента — res_b, amt_b, res_a, amt_a
    # Всё до них — название страны (может быть многословным)
    amt_b_s  = args[-1]
    res_b    = args[-2]
    amt_a_s  = args[-3]
    res_a    = args[-4]
    target_name = " ".join(args[1:-4])
    if not target_name:
        await message.answer(
            "Использование: <code>/trade страна мой_рес кол-во их_рес кол-во</code>",
            parse_mode="HTML"
        )
        return
    try:
        amt_a, amt_b = int(amt_a_s), int(amt_b_s)
        if amt_a <= 0 or amt_b <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Укажите положительные числа!")
        return

    all_countries = await get_all_countries(message.chat.id)
    target = next((c for c in all_countries if c["name"].lower() == target_name.lower()), None)
    if not target:
        await message.answer(f"❌ Страна «{target_name}» не найдена")
        return

    ok, msg = await propose_trade(message.chat.id, country["id"], target["id"], res_a, amt_a, res_b, amt_b)
    await message.answer(("✅ " if ok else "❌ ") + msg, parse_mode="HTML")
    if ok:
        # Уведомляем владельца целевой страны кнопками
        rn_a = ALL_TRADE_NAMES.get(res_a, res_a)
        rn_b = ALL_TRADE_NAMES.get(res_b, res_b)
        # Получаем id только что созданной сделки
        db = await _open_db()
        async with db:
            c = await db.execute(
                "SELECT id FROM trade_deals WHERE chat_id=? AND country_a_id=? AND country_b_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
                (message.chat.id, country["id"], target["id"])
            )
            row = await c.fetchone()
            deal_id = row["id"] if row else 0

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            colored_button("✅ Принять", "green", callback_data=f"trade_accept:{deal_id}:{target['id']}|{target['owner_id']}"),
            colored_button("❌ Отклонить", "red", callback_data=f"trade_decline:{deal_id}:{target['id']}|{target['owner_id']}"),
        ]])
        await message.answer(
            f"📦 {country['emoji']} <b>{country['name']}</b> предлагает торговлю "
            f"{target['emoji']} <b>{target['name']}</b>!\n\n"
            f"Они дают: {amt_a} {rn_a}\n"
            f"Хотят получить: {amt_b} {rn_b}\n\n"
            f"<i>Владелец {target['emoji']} {target['name']}, примите или отклоните:</i>",
            parse_mode="HTML", reply_markup=kb
        )
        await check_achievements(message.chat.id, country["id"])


@router_dev.message(Command("trade_accept"))
async def cmd_trade_accept(message: Message):
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны!")
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: <code>/trade_accept ID</code>", parse_mode="HTML")
        return
    try:
        deal_id = int(args[1])
    except ValueError:
        await message.answer("❌ Неверный ID сделки")
        return
    ok, msg = await accept_trade(message.chat.id, deal_id, country["id"])
    await message.answer(("✅ " if ok else "❌ ") + msg)


@router_dev.message(Command("trade_decline"))
async def cmd_trade_decline(message: Message):
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны!")
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: <code>/trade_decline ID</code>", parse_mode="HTML")
        return
    try:
        deal_id = int(args[1])
    except ValueError:
        await message.answer("❌ Неверный ID сделки")
        return
    ok, msg = await decline_trade(message.chat.id, deal_id, country["id"])
    await message.answer(("✅ " if ok else "❌ ") + msg)


@router_dev.callback_query(F.data.startswith("trade_accept:"))
async def cb_trade_accept(callback: CallbackQuery):
    # BUG FIX: int(callback.data.split("|")[1]) падал с IndexError/ValueError на
    # кнопке без owner-тега (например выпущенной прошлой версией бота), и
    # проверка владельца не выполнялась вовсе. Теперь нет тега — нет доступа.
    owner_id = _cb_owner_id(callback.data or "")
    if owner_id is None or callback.from_user.id != owner_id:
        await callback.answer("❌ Это не ваша кнопка!", show_alert=True)
        return
    parts = (callback.data or "").split("|")[0].split(":")
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await callback.answer("❌ Кнопка повреждена — вызовите команду заново.", show_alert=True)
        return
    deal_id, country_id = int(parts[1]), int(parts[2])
    ok, msg = await accept_trade(callback.message.chat.id, deal_id, country_id)
    await callback.answer(("✅ " if ok else "❌ ") + msg[:200], show_alert=True)
    if ok:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.message.answer(f"🤝 <b>Торговая сделка завершена!</b>\n{msg}", parse_mode="HTML")


@router_dev.callback_query(F.data.startswith("trade_decline:"))
async def cb_trade_decline(callback: CallbackQuery):
    # BUG FIX: int(callback.data.split("|")[1]) падал с IndexError/ValueError на
    # кнопке без owner-тега (например выпущенной прошлой версией бота), и
    # проверка владельца не выполнялась вовсе. Теперь нет тега — нет доступа.
    owner_id = _cb_owner_id(callback.data or "")
    if owner_id is None or callback.from_user.id != owner_id:
        await callback.answer("❌ Это не ваша кнопка!", show_alert=True)
        return
    parts = (callback.data or "").split("|")[0].split(":")
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await callback.answer("❌ Кнопка повреждена — вызовите команду заново.", show_alert=True)
        return
    deal_id, country_id = int(parts[1]), int(parts[2])
    ok, msg = await decline_trade(callback.message.chat.id, deal_id, country_id)
    await callback.answer(("✅ " if ok else "❌ ") + msg, show_alert=True)
    if ok:
        await callback.message.edit_reply_markup(reply_markup=None)



# ════════════════��══════════════════════════════════════════════
#  HANDLERS — ВАССАЛИТЕТ
# ═══════════════════════════════════════════════════════════════

@router_dev.message(Command("vassal"))
async def cmd_vassal(message: Message):
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны! /create")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "⛓ <b>Вассалитет</b>\n\n"
            "Использование: <code>/vassal название_страны</code>\n"
            "Вассал платит 20% казны каждый тик ресурсов сюзерену.\n"
            "Вассал не может объявлять войну без разрешения сюзерена.\n\n"
            "⚠️ Требует превосходства в армии или территориях.\n"
            "Цель получит уведомление и сможет принять или отклонить.",
            parse_mode="HTML"
        )
        return

    target_name = " ".join(args[1:])
    all_countries = await get_all_countries(message.chat.id)
    target = next((c for c in all_countries if c["name"].lower() == target_name.lower()), None)
    if not target:
        await message.answer(f"❌ Страна «{target_name}» не найдена")
        return
    if target["id"] == country["id"]:
        await message.answer("❌ Нельзя сделать себя вассалом!")
        return

    our_ter   = len(await get_country_territories(message.chat.id, country["id"]))
    their_ter = len(await get_country_territories(message.chat.id, target["id"]))
    if country["army"] <= target["army"] and our_ter <= their_ter:
        await message.answer("❌ Ваша страна недостаточно сильна. Нужно превосходство в армии или территориях.")
        return

    result = await demand_vassalage(message.chat.id, country["id"], target["id"])
    ok, msg, deal_id = result if len(result) == 3 else (result[0], result[1], 0)
    await message.answer(("✅ " if ok else "❌ ") + msg, parse_mode="HTML")

    if ok and deal_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            colored_button("✅ Принять", "green", callback_data=f"vassal_accept:{deal_id}:{target['id']}|{target['owner_id']}"),
            colored_button("❌ Отклонить", "red", callback_data=f"vassal_decline:{deal_id}:{target['id']}|{target['owner_id']}"),
        ]])
        await message.answer(
            f"⛓ {country['emoji']} <b>{country['name']}</b> требует вассалитета от "
            f"{target['emoji']} <b>{target['name']}</b>!\n\n"
            f"Условия: 20% казны каждый тик → сюзерену\n\n"
            f"<i>Владелец {target['emoji']} {target['name']}, ваш ответ:</i>",
            parse_mode="HTML", reply_markup=kb
        )


@router_dev.callback_query(F.data.startswith("vassal_accept:"))
async def cb_vassal_accept(callback: CallbackQuery):
    # BUG FIX: int(callback.data.split("|")[1]) падал с IndexError/ValueError на
    # кнопке без owner-тега (например выпущенной прошлой версией бота), и
    # проверка владельца не выполнялась вовсе. Теперь нет тега — нет доступа.
    owner_id = _cb_owner_id(callback.data or "")
    if owner_id is None or callback.from_user.id != owner_id:
        await callback.answer("❌ Это не ваша кнопка!", show_alert=True)
        return
    parts = (callback.data or "").split("|")[0].split(":")
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await callback.answer("❌ Кнопка повреждена — вызовите команду заново.", show_alert=True)
        return
    deal_id, country_id = int(parts[1]), int(parts[2])
    ok, msg = await accept_vassalage(callback.message.chat.id, country_id, deal_id)
    await callback.answer(("✅ " if ok else "❌ ") + msg, show_alert=True)
    if ok:
        await callback.message.edit_reply_markup(reply_markup=None)


@router_dev.callback_query(F.data.startswith("vassal_decline:"))
async def cb_vassal_decline(callback: CallbackQuery):
    # BUG FIX: int(callback.data.split("|")[1]) падал с IndexError/ValueError на
    # кнопке без owner-тега (например выпущенной прошлой версией бота), и
    # проверка владельца не выполнялась вовсе. Теперь нет тега — нет доступа.
    owner_id = _cb_owner_id(callback.data or "")
    if owner_id is None or callback.from_user.id != owner_id:
        await callback.answer("❌ Это не ваша кнопка!", show_alert=True)
        return
    parts = (callback.data or "").split("|")[0].split(":")
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await callback.answer("❌ Кнопка повреждена — вызовите команду заново.", show_alert=True)
        return
    deal_id, country_id = int(parts[1]), int(parts[2])
    ok, msg = await decline_vassalage(callback.message.chat.id, country_id, deal_id)
    await callback.answer(("✅ " if ok else "❌ ") + msg, show_alert=True)
    if ok:
        await callback.message.edit_reply_markup(reply_markup=None)



# ═══════════════════════════════════════════════════════════════
#  HANDLERS — ООН
# ═══════════════════════════════════════════════════════════════

@router_dev.message(Command("un"))
async def cmd_un(message: Message):
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны! /create")
        return

    args = message.text.split(maxsplit=2)

    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM un_resolutions WHERE chat_id=? AND status='voting' ORDER BY created_at DESC",
            (message.chat.id,)
        )
        active = [dict(r) for r in await c.fetchall()]

    if len(args) == 1:
        lines = ["🌐 <b>ООН — Совет Безопасности</b>", ""]
        if active:
            import time as _t
            lines.append(f"<b>Активных резолюций: {len(active)}</b>")
            for res in active:
                target = await get_country_by_id(message.chat.id, res["target_id"]) if res["target_id"] else None
                proposer = await get_country_by_id(message.chat.id, res["proposer_id"])
                votes_for = len(json.loads(res["votes_for"]))
                votes_against = len(json.loads(res["votes_against"]))
                expires = int(res["expires_at"]) - int(_t.time())
                exp_str = f"{expires//3600}ч {(expires%3600)//60}м" if expires > 0 else "истекло"
                lines.append(
                    f"📜 <b>{res['type']}</b> (#{res['id']})\n"
                    f"  Инициатор: {proposer['emoji'] if proposer else '?'} {proposer['name'] if proposer else '?'}\n"
                    f"  Цель: {target['emoji'] + ' ' + target['name'] if target else 'все'}\n"
                    f"  За: {votes_for} | Против: {votes_against} | До конца: {exp_str}\n"
                    f"  /un vote {res['id']} за|против"
                )
        else:
            lines.append("Активных резолюций нет")
        lines += [
            "",
            "<b>Подать резолюцию:</b>",
            "/un propose sanctions Страна — экономические санкции",
            "/un propose peacekeepers Страна — миротворцы (остановить войну)",
            "/un propose condemn Страна — осуждение агрессора",
        ]
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    sub = args[1].lower()

    if sub == "propose" and len(args) >= 3:
        parts = args[2].split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Использование: <code>/un propose тип Страна</code>", parse_mode="HTML")
            return
        res_type, target_name = parts[0], parts[1]
        if res_type not in ("sanctions", "peacekeepers", "condemn"):
            await message.answer("❌ Типы: sanctions, peacekeepers, condemn")
            return
        all_countries = await get_all_countries(message.chat.id)
        target = next((c for c in all_countries if c["name"].lower() == target_name.lower()), None)
        if not target:
            await message.answer(f"❌ Страна «{target_name}» не найдена")
            return
        import time as _t
        db = await _open_db()
        async with db:
            await db.execute(
                "INSERT INTO un_resolutions (chat_id, proposer_id, type, target_id, expires_at, votes_for, votes_against) VALUES (?,?,?,?,?,?,?)",
                (message.chat.id, country["id"], res_type, target["id"],
                 int(_t.time()) + 86400, json.dumps([country["id"]]), json.dumps([]))
            )
            await db.commit()
        type_names = {"sanctions": "Санкции", "peacekeepers": "Миротворцы", "condemn": "Осуждение"}
        await message.answer(
            f"📜 Резолюция <b>{type_names[res_type]}</b> против {target['emoji']} {target['name']} подана!\n"
            f"Голосование идёт 24 часа. Используйте <code>/un</code> для голосования.",
            parse_mode="HTML"
        )

    elif sub == "vote" and len(args) >= 3:
        
        parts = args[2].split()
        if len(parts) < 2:
            await message.answer("Использование: <code>/un vote ID за|против</code>", parse_mode="HTML")
            return
        try:
            res_id = int(parts[0])
        except ValueError:
            await message.answer("❌ Укажите номер резолюции")
            return
        vote_str = parts[1].lower()
        if vote_str not in ("за", "против", "for", "against"):
            await message.answer("❌ Введите: за или против")
            return
        vote_for = vote_str in ("за", "for")

        db = await _open_db()
        async with db:
            c = await db.execute("SELECT * FROM un_resolutions WHERE id=? AND chat_id=? AND status='voting'", (res_id, message.chat.id))
            res = await c.fetchone()
            if not res:
                await message.answer("❌ Резолюция не найдена или голосование завершено")
                return
            votes_for = json.loads(res["votes_for"])
            votes_against = json.loads(res["votes_against"])
            cid = country["id"]
            if cid in votes_for or cid in votes_against:
                await message.answer("❌ Вы уже голосовали по этой резолюции")
                return
            if vote_for:
                votes_for.append(cid)
            else:
                votes_against.append(cid)
            await db.execute(
                "UPDATE un_resolutions SET votes_for=?, votes_against=? WHERE id=?",
                (json.dumps(votes_for), json.dumps(votes_against), res_id)
            )
            await db.commit()
        await message.answer(f"✅ Вы проголосовали {'«За»' if vote_for else '«Против»'} резолюции #{res_id}")
    else:
        await message.answer("Подкоманды: /un | /un propose тип Страна | /un vote ID за|против")


# ═════════════════════════════════════════════���═════════════════
#  HANDLERS — ДОСТИЖЕНИЯ
# ═══════════════════════════════════════════════════════════════

@router_dev.message(Command("achievements"))
async def cmd_achievements(message: Message):
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны! /create")
        return

    await check_achievements(message.chat.id, country["id"])
    earned = set(await get_achievements(country["id"]))

    lines = [f"🏆 <b>Достижения — {country['emoji']} {country['name']}</b>", f"Получено: {len(earned)}/{len(ACHIEVEMENTS)}", ""]
    for ach_id, ach in ACHIEVEMENTS.items():
        if ach_id in earned:
            lines.append(f"✅ {ach['emoji']} <b>{ach['name']}</b>\n  <i>{ach['desc']}</i>")
        else:
            lines.append(f"🔒 {ach['emoji']} {ach['name']}\n  <i>{ach['desc']}</i>")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════
#  HANDLERS — ИСТОРИЯ СТРАНЫ
# ═══════════════════════════════════════════════════════════════

@router_dev.message(Command("history"))
async def cmd_history(message: Message):
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны! /create")
        return

    import time as _t
    events = await get_country_history(message.chat.id, country["id"])

    if not events:
        await message.answer(f"📜 История {country['emoji']} {country['name']} пока пуста.", parse_mode="HTML")
        return

    type_labels = {
        "territory_captured": "⚔️ Захват территории",
        "territory_lost": "💔 Потеря территории",
        "war_started": "🔥 Начало войны",
        "war_ended": "🕊 Конец войны",
        "country_defeated": "💀 Разгром страны",
        "alliance_formed": "🤝 Альянс",
        "pact_signed": "📜 Пакт",
        "nuclear_strike": "☢️ Ядерный удар",
        "civil_war": "🔥 Гражданская война",
    }

    lines = [f"📜 <b>История — {country['emoji']} {country['name']}</b>", ""]
    for ev in events:
        label = type_labels.get(ev["event_type"], ev["event_type"])
        ts = int(ev["created_at"])
        from datetime import datetime
        dt = datetime.utcfromtimestamp(ts).strftime("%d.%m %H:%M")
        try:
            data = json.loads(ev["data"])
        except Exception:
            data = {}
        detail = ""
        if ev["event_type"] == "territory_captured":
            detail = f": {data.get('territory', '')} ({data.get('iso', '')})"
        elif ev["event_type"] == "war_started":
            detail = f": vs {data.get('defender', '') or data.get('attacker', '')}"
        lines.append(f"[{dt}] {label}{detail}")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════
#  HANDLERS — ЯДЕРНЫЙ УДАР
# ═══════════════════════════════════════════════════════════════

@router_dev.message(Command("nuclear"))
async def cmd_nuclear(message: Message):
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны! /create")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "☢️ <b>Ядерный удар</b>\n\n"
            "Использование: <code>/nuclear ISO</code>\n"
            "Пример: <code>/nuclear DE</code>\n\n"
            "Требования:\n"
            "  🔬 Технология: Ядерная теория\n"
            "  💰 Стоимость: 2000💰\n\n"
            "⚠️ Уничтожает территорию, все здания на ней и 50% армии противника.",
            parse_mode="HTML"
        )
        return

    target_iso = args[1].upper()
    if not is_valid_iso(target_iso):
        await message.answer(f"❌ Неизвестный ISO: {target_iso}")
        return

    ok, result = await nuclear_strike(message.chat.id, country["id"], target_iso)
    await message.answer(("✅ " if ok else "❌ ") + result, parse_mode="HTML")
    if ok:
        await log_event(message.chat.id, "nuclear_strike", {
            "attacker": country["name"], "attacker_emoji": country["emoji"],
            "target_iso": target_iso, "target_name": COUNTRY_NAMES.get(target_iso, target_iso),
        })
        await check_achievements(message.chat.id, country["id"])


# ═══════════════════════════════════════════════════════════════
#  HANDLERS — МИНИ-КАРТА СТРАНЫ
# ═══════════════════════════════════════════════════════════════

@router_dev.message(Command("mymap"))
async def cmd_mymap(message: Message):
    if message.chat.type == "private":
        return
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны! /create")
        return

    territories = await get_country_territories(message.chat.id, country["id"])
    if not territories:
        await message.answer("❌ У вас нет территорий!")
        return

    # Рендерим карту только с территориями игрока
    all_territories = await get_territories(message.chat.id)
    owned_map = {iso: (country["color"], country["name"], country.get("emoji", "")) for iso in territories}
    my_capital = {(country.get("capital_iso") or country["iso"]).upper()}

    loop = asyncio.get_running_loop()
    buf = await loop.run_in_executor(
        None,
        functools.partial(generate_map, owned_map,
                          title=f"{country['name']} — {len(territories)} территорий",
                          capitals=my_capital)
    )

    from aiogram.types import BufferedInputFile
    await message.answer_photo(
        BufferedInputFile(buf.read(), filename="mymap.png"),
        caption=f"🗺 {country['emoji']} <b>{country['name']}</b> — {len(territories)} территорий",
        parse_mode="HTML"
    )






# ═══════════════════════════════════════════════════════════════
#  РУССКИЕ ТЕКСТОВЫЕ ТРИГГЕРЫ
# ═══════════════════════════════════════════════════════════════

@router_dev.message(F.text.lower().in_({
    "моя страна", "моя страна", "страна", "my country", "статус"
}))
async def trigger_mycountry(message: Message):
    if message.chat.type == "private":
        return
    # Реиспользуем хендлер /mycountry
    from aiogram.filters import Command as _Cmd
    country = await get_country_by_owner(message.chat.id, message.from_user.id)
    if not country:
        await message.answer("❌ У вас нет страны! Создайте: /create")
        return
    ter = await get_country_territories(message.chat.id, country["id"])
    res = await get_resources(country["id"])
    pop = res["population"] if res else 0
    lines = [
        f"{country['emoji']} <b>{country['name']}</b>",
        f"💰 Казна: {country['treasury']} | 👣 Армия: {country['army']}",
        f"🌍 Территорий: {len(ter)} | 👥 Население: {pop:,}",
        "",
        "Подробнее: /resources | /buildings | /military | /achievements",
    ]
    await message.answer("\n".join(lines), parse_mode="HTML")


@router_dev.message(F.text.lower().regexp(r"^купить? \S+ \d+"))
async def trigger_buy_units(message: Message):
    if message.chat.type == "private":
        return
    parts = message.text.split()
    if len(parts) < 3:
        return
    unit_word = parts[1].lower()
    try:
        amount = int(parts[2])
    except ValueError:
        return
    ok, msg = await buy_units(message.chat.id, message.from_user.id, unit_word, amount)
    await message.answer(("✅ " if ok else "❌ ") + msg)


@router_dev.message(F.text.lower().in_({"карта", "map", "показать карту"}))
async def trigger_map(message: Message):
    if message.chat.type == "private":
        return
    # Redirect to map handler
    await message.answer("🗺 Используйте /map для отображения карты.")


@router_dev.message(F.text.lower().in_({"ресурсы", "ресурс", "resources", "экономика"}))
async def trigger_resources(message: Message):
    if message.chat.type == "private":
        return
    await cmd_resources(message)


@router_dev.message(F.text.lower().in_({"армия", "войска", "military", "солдаты"}))
async def trigger_military(message: Message):
    if message.chat.type == "private":
        return
    await cmd_military(message)


@router_dev.message(F.text.lower().in_({"достижения", "achievements", "награды"}))
async def trigger_achievements(message: Message):
    if message.chat.type == "private":
        return
    await cmd_achievements(message)


@router_dev.message(F.text.lower().in_({"помощь", "помоги", "help", "что делать", "команды"}))
async def trigger_help(message: Message):
    if message.chat.type == "private":
        return
    await cmd_help(message)


# ═══════════════════════════════════════════════════════════════
#  HANDLER — ПОМОЩЬ /help
# ═══════════════════════════════════════════════════════════════

@router_dev.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "📖 <b>РУКОВОДСТВО ИГРОКА</b>\n"
        "══════════════════════════\n\n"

        "🏛 <b>ОСНОВЫ</b>\n"
        "/create — создать страну (выбрать ISO → название → эмодзи → цвет)\n"
        "/mycountry — статус вашей страны\n"
        "/resources — ресурсы, армия, технологии\n"
        "/map — карта мира\n"
        "/mymap — только ваши территории\n"
        "/search ISO/название — найти страну или территорию\n\n"

        "💰 <b>ЭКОНОМИКА</b>\n"
        "Ресурсы начисляются каждый час автоматически.\n"
        "Базовый доход без зданий: еда+5, произв.+3, металлы+1, нефть+1, наука+2\n"
        "Население растёт на 1%/тик и приносит налоги (1💰 на каждые 100 чел.)\n\n"

        "🏗 <b>ЗДАНИЯ</b> /build\n"
        "Строятся на конкретной территории: <code>/build RU farm</code>\n"
        "Каждый уровень увеличивает доход. Макс. уровень зависит от типа.\n"
        "🌾 Ферма (+15 еды) | ⚙️ Завод (+20 произв.) | 🏦 Банк (+25💰)\n"
        "🔬 Университет (+15 науки) | 🛢 Вышка (+20 нефти) | 💎 Шахта (+15 металлов)\n"
        "🏰 Крепость (+25% защиты за ур.) | 🪖 Казармы | 🐴 Конюшни | ✈️ Авиабаза\n"
        "/buildings — список всех ваших зданий\n\n"

        "🔬 <b>ТЕХНОЛОГИИ</b> /research\n"
        "3 эры: Феодализм → Индустриализм → Современность\n"
        "Технологии изучаются за 📚 Науку. Открывают здания и дают бонусы.\n"
        "Пример: <code>/research basic_farming</code>\n"
        "Переход в новую эру требует изучения ключевых технологий предыдущей.\n\n"

        "⚔️ <b>АРМИЯ</b> /military\n"
        "Покупаются за 💰 (и ⚙️ для сложных юнитов):\n"
        "👣 Пехота (10💰) | 🐴 Кавалерия (30💰) | 💣 Артиллерия (80💰)\n"
        "✈️ Авиация (200💰) | ⚓ Флот (150💰) | 💥 Бомбардировщики (350💰)\n"
        "Пример: <code>/military infantry 200</code>\n\n"

        "🗺 <b>ЭКСПАНСИЯ</b> /expand\n"
        "Захватить соседнюю <b>нейтральную</b> территорию без объявления войны.\n"
        "Не требует войны, тратит только золото и армию.\n"
        "Пример: <code>/expand DE</code>\n\n"

        "🔥 <b>ВОЙНА</b> /war /attack /deploy /status\n"
        "Атаковать территорию <b>другого игрока</b> — сначала нужно объявить войну.\n"
        "1. Объявить войну: <code>/war Страна</code>\n"
        "2. <b>/attack ОТКУДА КУДА</b> — атака из гарнизона своей территории\n"
        "   Требует: смежная граница ИЛИ ≥20 авиации/бомберов ИЛИ ≥10 флота\n"
        "3. <b>/deploy ISO тип кол-во</b> — отправить войска с задержкой прибытия.\n"
        "   Пехота прибывает через 3 мин, авиация — 1 мин, флот — 4 мин.\n"
        "   При прибытии войска автоматически атакуют или захватывают территорию.\n"
        "   Если у врага 0 армии — территория захватывается без боя.\n"
        "4. Посмотреть статус войны: /status\n"
        "🏰 Крепость на территории даёт +25%/ур. к защите\n"
        "⛰️ Горы +40%, 🌲 Леса +20%, 🏜️ Пустыня −10% к защите\n"
        "🏗 При захвате все здания на территории переходят победителю!\n\n"

        "☢️ <b>ЯДЕРНЫЙ УДАР</b> /nuclear ISO\n"
        "Требует: технология «Ядерная теория» + 2000💰\n"
        "Уничтожает территорию и здания, -50% армии противника.\n\n"

        "🕵️ <b>ШПИОНАЖ</b> /spy\n"
        "<code>/spy Страна операция</code>\n"
        "steal_gold | steal_resources | sabotage | recon | provoke_revolt | recruit_agent\n"
        "Технология «Кибервойна» даёт +20% к шансу успеха.\n\n"

        "🤝 <b>ДИПЛОМАТИЯ</b>\n"
        "/ally — предложить союз\n"
        "/pact — пакт о ненападении\n"
        "/bloc create/join/leave — военные блоки (атака на члена = война со всеми)\n"
        "/trade Страна рес1 кол рес2 кол — торговые сделки (обмен каждый тик)\n"
        "/vassal Страна — вассализировать страну (20% её казны → вам)\n"
        "/un — ООН: резолюции, санкции, миротворцы\n\n"

        "⚔️ <b>ВОЙНА И ГАРНИЗОНЫ</b>\n"
        "/garrison — управление гарнизонами\n"
        "  /garrison ISO пехота 500 — разместить войска\n"
        "  /garrison ISO пехота -200 — вывести войска\n"
        "  Гарнизоны усиливают оборону территорий!\n"
        "/claim — претензии (casus belli)\n"
        "  /claim ISO historical — историческая претензия (+25% к атаке)\n"
        "  /claim ISO conquest — завоевательная (+10%)\n"
        "/weariness — военная усталость (растёт при войне, -эффективность армии)\n"
        "  Например: <code>/give США food 500</code> или <code>/give Россия infantry 200</code>\n"
        "/capital — посмотреть/изменить столицу (<code>/capital ISO</code>)\n"
        "  ⚠️ Захват столицы → все здания страны переходят захватчику!\n"
        "/rename — переименовать страну (1 раз, 500💰)\n"
        "/achievements — ваши достижения (14 уникальных)\n"
        "/history — лог событий вашей страны\n"
        "/isolist — список всех ISO-кодов\n"
        "/newspaper — свежая газета\n"
        "/delete — удалить страну\n"
    )
    await message.answer(text, parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════
#  ФОНОВЫЕ ЗАДАЧИ
# ═══════════════════════════════════════════════════════════════

async def background_war_tick(bot: Bot):
    """Каждый час — статус войны / финальные итоги."""
    await asyncio.sleep(30)  # пауза при старте
    while True:
        try:
            await war_tick(bot)
        except Exception as e:
            logging.error(f"background_war_tick error: {e}")
        await asyncio.sleep(WAR_TICK_INTERVAL_H * 3600)  # каждый час


async def background_deployment_tick(bot: Bot):
    """Каждую минуту — проверяем прибывшие деплои."""
    await asyncio.sleep(15)
    while True:
        try:
            all_chats = await get_all_chats()
            for _cid in all_chats:
                await resolve_deployments(_cid, bot)
                await resolve_trade_ships(_cid, bot)
        except Exception as e:
            logging.error(f"background_deployment_tick error: {e}")
        await asyncio.sleep(60)  # каждую минуту


async def background_newspaper(bot: Bot):
    """Каждые NEWSPAPER_INTERVAL_H часов — публикует газету во всех чатах."""
    await asyncio.sleep(120)  # пауза при старте
    while True:
        try:
            chat_ids = await get_all_chats()
            for chat_id in chat_ids:
                await publish_newspaper(bot, chat_id)
        except Exception as e:
            logging.error(f"background_newspaper error: {e}")
        await asyncio.sleep(NEWSPAPER_INTERVAL_H * 3600)


async def hourly_income_tick(chat_id: int, bot: Bot):
    """Пассивный почасовой доход: монеты + ⚙️ для всех стран чата.
    Оптимизировано: один проход по БД, summary строится из тех же данных.
    """
    countries = await get_all_countries(chat_id)
    if not countries:
        return

    # Массовая выборка: территории + здания (bank, factory) за один запрос
    cids = [c["id"] for c in countries]
    placeholders = ",".join("?" * len(cids))

    db = await _open_db()
    async with db:
        now = int(time.time())

        # Количество территорий по каждой стране
        ter_rows = await (await db.execute(
            f"SELECT country_id, COUNT(*) as cnt FROM territories "
            f"WHERE chat_id=? AND country_id IN ({placeholders}) GROUP BY country_id",
            [chat_id] + cids
        )).fetchall()
        ter_by_cid = {r[0]: r[1] for r in ter_rows}

        # Здания bank и factory за один запрос
        bld_rows = await (await db.execute(
            f"SELECT country_id, type, level FROM buildings "
            f"WHERE chat_id=? AND country_id IN ({placeholders}) AND type IN ('bank','factory')",
            [chat_id] + cids
        )).fetchall()
        bank_by_cid    = {}
        factory_by_cid = {}
        for r in bld_rows:
            if r[1] == 'bank':
                bank_by_cid[r[0]] = r[2]
            elif r[1] == 'factory':
                factory_by_cid[r[0]] = r[2]

        # Считаем доход и сразу пишем summary для каждой страны
        income_summary = {}  # cid → (coins, prod)
        for country in countries:
            cid       = country["id"]
            ter_count = ter_by_cid.get(cid, 0)
            # Diminishing returns на территории: было ter_count*3 (линейно,
            # без ограничений) — теперь рост дохода замедляется с масштабом
            # империи. Банки/заводы остаются линейными — это осознанное
            # здание, требующее инвестиций и слота, в отличие от простого
            # захвата территории.
            ter_bonus_gold = int(3 * (max(ter_count, 0) ** 0.65))
            ter_bonus_prod = int(2 * (max(ter_count, 0) ** 0.65))
            coins     = 10 + ter_bonus_gold + bank_by_cid.get(cid, 0) * 10
            prod      = 5  + ter_bonus_prod + factory_by_cid.get(cid, 0) * 8
            income_summary[cid] = (coins, prod)
            await db.execute(
                "UPDATE countries SET treasury=treasury+?, last_hourly_income=? WHERE id=?",
                (coins, now, cid)
            )
            await db.execute(
                "UPDATE resources SET production=MIN(9999, production+?) WHERE country_id=?",
                (prod, cid)
            )
        await db.commit()

    # Собираем сводку без лишних запросов к БД
    lines = ["⏰ <b>Почасовой доход</b>\n"]
    for country in countries:
        cid      = country["id"]
        coins, prod = income_summary.get(cid, (10, 5))
        owner_id = country["owner_id"]
        lines.append(
            f"{country['emoji']} <a href='tg://openmessage?user_id={owner_id}'><b>{country['name']}</b></a>: +{coins}💰  +{prod}⚙️"
        )
    try:
        await _safe_send(bot, chat_id, "\n".join(lines), parse_mode="HTML")
    except Exception:
        pass


async def background_hourly_income(bot: Bot):
    """Каждый час — пассивный доход монет и ⚙️ всем странам."""
    await asyncio.sleep(60)  # пауза при старте
    while True:
        try:
            chat_ids = await get_all_chats()
            for chat_id in chat_ids:
                await hourly_income_tick(chat_id, bot)
                await asyncio.sleep(1.0)  # пауза между чатами — защита от FloodWait
            logging.info(f"Hourly income tick done for {len(chat_ids)} chats")
        except Exception as e:
            logging.error(f"background_hourly_income error: {e}")
        await asyncio.sleep(3600)


async def background_resource_tick(bot: Bot):
    """Каждые RESOURCE_TICK_INTERVAL_H часов — начисляет ресурсы всем странам."""
    await asyncio.sleep(180)  # пауза при старте
    while True:
        try:
            chat_ids = await get_all_chats()
            for chat_id in chat_ids:
                try:
                    await resource_tick(chat_id, bot)
                except Exception as e:
                    logging.error(f"resource_tick failed for chat {chat_id}: {e}", exc_info=True)
                await asyncio.sleep(0.5)  # пауза между чатами — защита от FloodWait
            logging.info(f"Resource tick done for {len(chat_ids)} chats")
        except Exception as e:
            logging.error(f"background_resource_tick error: {e}", exc_info=True)
        finally:
            await asyncio.sleep(RESOURCE_TICK_INTERVAL_H * 3600)


# ═══════════════════════════════════════════════════════════════
#  СЛУЧАЙНЫЕ СОБЫТИЯ
# ═══════════════════════════════════════════════════════════════

RANDOM_EVENTS_INTERVAL_H = 3   # тик проверки каждые 3 часа
RANDOM_EVENT_CHANCE      = 0.6 # вероятность события за тик (0..1)

# Каждое событие:
#   "weight"   — относительный вес (чаще / реже)
#   "positive" — True = хорошее, False = плохое, None = нейтральное
#   "requires" — список условий: "war", "no_war", "rich" (treasury>500), "army" (army>50)
#   "apply"    — функция (country, db) -> (text_suffix, changes_dict)
#                changes_dict: treasury, army, food, production, science, oil, rare_metals, population

RANDOM_EVENTS = [
    # ── ХОРОШИЕ ────────────────────────────────────────────────
    {
        "id": "gold_rush",
        "emoji": "⛏️",
        "title": "Золотая лихорадка",
        "weight": 8,
        "positive": True,
        "requires": [],
        "treasury_delta": lambda c: random.randint(200, 500),
        "army_delta":     lambda c: 0,
        "res_delta":      lambda c: {"rare_metals": random.randint(30, 80)},
        "text": lambda c, td, rd: (
            f"На территории {c['emoji']} <b>{c['name']}</b> обнаружено крупное золотое месторождение!\n"
            f"Казна пополнилась на <b>+{td}💰</b>, добыто редких металл��в: <b>+{rd['rare_metals']}💎</b>"
        ),
    },
    {
        "id": "harvest",
        "emoji": "🌾",
        "title": "Рекордный урожай",
        "weight": 10,
        "positive": True,
        "requires": [],
        "treasury_delta": lambda c: random.randint(50, 150),
        "army_delta":     lambda c: 0,
        "res_delta":      lambda c: {"food": random.randint(100, 250), "population": random.randint(20, 60)},
        "text": lambda c, td, rd: (
            f"☀️ В {c['emoji']} <b>{c['name']}</b> выдался рекордный урожай!\n"
            f"+{rd['food']}🌾 еды, +{rd['population']} населения, +{td}💰 от торговли зерном."
        ),
    },
    {
        "id": "tech_breakthrough",
        "emoji": "🔬",
        "title": "Научный прорыв",
        "weight": 6,
        "positive": True,
        "requires": [],
        "treasury_delta": lambda c: 0,
        "army_delta":     lambda c: 0,
        "res_delta":      lambda c: {"science": random.randint(50, 120)},
        "text": lambda c, td, rd: (
            f"🔬 Учёные {c['emoji']} <b>{c['name']}</b> совершили прорыв в исследованиях!\n"
            f"+{rd['science']}🔬 науки."
        ),
    },
    {
        "id": "trade_caravan",
        "emoji": "🐪",
        "title": "Торговый караван",
        "weight": 9,
        "positive": True,
        "requires": [],
        "treasury_delta": lambda c: random.randint(150, 350),
        "army_delta":     lambda c: 0,
        "res_delta":      lambda c: {"production": random.randint(20, 50)},
        "text": lambda c, td, rd: (
            f"🐪 В {c['emoji']} <b>{c['name']}</b> прибыл богатый торговый караван!\n"
            f"+{td}💰 в казну, +{rd['production']}⚙️ производства от торговых связей."
        ),
    },
    {
        "id": "volunteers",
        "emoji": "🪖",
        "title": "Волна добровольцев",
        "weight": 7,
        "positive": True,
        "requires": [],
        "treasury_delta": lambda c: 0,
        "army_delta":     lambda c: random.randint(30, 80),
        "res_delta":      lambda c: {},
        "text": lambda c, td, rd: (
            f"🪖 Патриотический подъём в {c['emoji']} <b>{c['name']}</b>!\n"
            f"В армию записались добровольцы: <b>+{td} солдат</b>."
        ),
    },
    {
        "id": "oil_field",
        "emoji": "🛢️",
        "title": "Нефтяной фонтан",
        "weight": 5,
        "positive": True,
        "requires": [],
        "treasury_delta": lambda c: random.randint(100, 300),
        "army_delta":     lambda c: 0,
        "res_delta":      lambda c: {"oil": random.randint(50, 120)},
        "text": lambda c, td, rd: (
            f"🛢️ В {c['emoji']} <b>{c['name']}</b> забил нефтяной фонтан!\n"
            f"+{rd['oil']}🛢 нефти, +{td}💰 от первых продаж."
        ),
    },
    {
        "id": "morale_boost",
        "emoji": "🎉",
        "title": "Национальный праздник",
        "weight": 8,
        "positive": True,
        "requires": [],
        "treasury_delta": lambda c: random.randint(30, 80),
        "army_delta":     lambda c: random.randint(10, 30),
        "res_delta":      lambda c: {"population": random.randint(10, 40)},
        "text": lambda c, td, rd: (
            f"🎉 В {c['emoji']} <b>{c['name']}</b> грандиозный национальный праздник!\n"
            f"Моральный дух поднялся: +{td}💰 пожертвований, +{rd['army_delta'] if 'army_delta' in rd else '?'} рекрутов. Население растёт: +{rd['population']}👥"
        ),
    },
    # ── ПЛОХИЕ ──────────────────────────────────────────────────
    {
        "id": "plague",
        "emoji": "☠️",
        "title": "Эпидемия",
        "weight": 7,
        "positive": False,
        "requires": [],
        "treasury_delta": lambda c: -random.randint(100, 300),
        "army_delta":     lambda c: -random.randint(20, 60),
        "res_delta":      lambda c: {"food": -random.randint(50, 120), "population": -random.randint(30, 80)},
        "text": lambda c, td, rd: (
            f"☠️ В {c['emoji']} <b>{c['name']}</b> вспыхнула эпидемия!\n"
            f"Потери: {td}💰 на лечение, {rd.get('army_delta',0)} солдат, {rd['food']}🌾 продовольствия. "
            f"Население сократилось на {abs(rd['population'])}👥"
        ),
    },
    {
        "id": "drought",
        "emoji": "🏜️",
        "title": "Засуха",
        "weight": 8,
        "positive": False,
        "requires": [],
        "treasury_delta": lambda c: -random.randint(50, 150),
        "army_delta":     lambda c: 0,
        "res_delta":      lambda c: {"food": -random.randint(80, 200)},
        "text": lambda c, td, rd: (
            f"🏜️ Страшная засуха обрушилась на {c['emoji']} <b>{c['name']}</b>!\n"
            f"Урожай уничтожен: {rd['food']}🌾. Потрачено {abs(td)}💰 на закупку еды."
        ),
    },
    {
        "id": "earthquake",
        "emoji": "🌋",
        "title": "Землетрясение",
        "weight": 4,
        "positive": False,
        "requires": [],
        "treasury_delta": lambda c: -random.randint(200, 450),
        "army_delta":     lambda c: -random.randint(10, 40),
        "res_delta":      lambda c: {"production": -random.randint(30, 80), "population": -random.randint(20, 60)},
        "text": lambda c, td, rd: (
            f"🌋 Мощное землетрясение в {c['emoji']} <b>{c['name']}</b>!\n"
            f"Разрушена инфраструктура: {rd['production']}⚙️ производства, {abs(td)}💰 на восстановление. "
            f"Жертвы: {abs(rd['population'])}👥"
        ),
    },
    {
        "id": "desertion",
        "emoji": "🏃",
        "title": "Дезертирство",
        "weight": 6,
        "positive": False,
        "requires": ["army"],
        "treasury_delta": lambda c: -random.randint(50, 120),
        "army_delta":     lambda c: -random.randint(30, min(80, max(10, c["army"] // 5))),
        "res_delta":      lambda c: {},
        "text": lambda c, td, rd: (
            f"🏃 Волна дезертирства охватила армию {c['emoji']} <b>{c['name']}</b>!\n"
            f"Дезертировало <b>{abs(td)} солдат</b>. Потрачено {abs(td)}💰 на розыск и штрафы."
        ),
    },
    {
        "id": "corruption",
        "emoji": "💸",
        "title": "Коррупционный скандал",
        "weight": 7,
        "positive": False,
        "requires": ["rich"],
        "treasury_delta": lambda c: -random.randint(150, min(400, c["treasury"] // 3)),
        "army_delta":     lambda c: 0,
        "res_delta":      lambda c: {},
        "text": lambda c, td, rd: (
            f"💸 В {c['emoji']} <b>{c['name']}</b> разгорелся коррупционный скандал!\n"
            f"Из казны похищено <b>{abs(td)}💰</b>. Чиновники под следствием."
        ),
    },
    {
        "id": "fire",
        "emoji": "🔥",
        "title": "Пожар на заводах",
        "weight": 6,
        "positive": False,
        "requires": [],
        "treasury_delta": lambda c: -random.randint(80, 200),
        "army_delta":     lambda c: 0,
        "res_delta":      lambda c: {"production": -random.randint(40, 100), "oil": -random.randint(10, 40)},
        "text": lambda c, td, rd: (
            f"🔥 Пожар уничтожил производственные мощности {c['emoji']} <b>{c['name']}</b>!\n"
            f"{abs(rd['production'])}⚙️ производства сгорело, {abs(rd['oil'])}🛢 нефти потеряно, ущерб {abs(td)}💰."
        ),
    },
    {
        "id": "spy_leak",
        "emoji": "🕵️",
        "title": "Утечка государственных секретов",
        "weight": 5,
        "positive": False,
        "requires": [],
        "treasury_delta": lambda c: -random.randint(60, 180),
        "army_delta":     lambda c: 0,
        "res_delta":      lambda c: {"science": -random.randint(20, 60)},
        "text": lambda c, td, rd: (
            f"🕵️ В {c['emoji']} <b>{c['name']}</b> выявлен иностранный шпион!\n"
            f"Похищены научные разработки: {abs(rd['science'])}🔬. Контрразведка обошлась в {abs(td)}💰."
        ),
    },
    # ── НЕЙТРАЛЬНЫЕ/ОСОБЫЕ ──────────────────────────────────────
    {
        "id": "migrants",
        "emoji": "🚶",
        "title": "Волна миграции",
        "weight": 6,
        "positive": None,
        "requires": [],
        "treasury_delta": lambda c: random.randint(-50, 100),
        "army_delta":     lambda c: random.randint(5, 20),
        "res_delta":      lambda c: {"population": random.randint(20, 80), "food": -random.randint(20, 60)},
        "text": lambda c, td, rd: (
            f"🚶 Волна мигрантов прибыла в {c['emoji']} <b>{c['name']}</b>!\n"
            f"+{rd['population']}👥 населения, +{rd.get('army_extra', 10)} рекрутов. "
            f"{'Казна пополнилась на ' + str(td) + '💰.' if td > 0 else 'Расходы на размещение: ' + str(abs(td)) + '💰.'} "
            f"Запасы еды: {rd['food']}🌾"
        ),
    },
    {
        "id": "rebel_suppressed",
        "emoji": "⚔️",
        "title": "Подавление восстания",
        "weight": 5,
        "positive": None,
        "requires": ["army"],
        "treasury_delta": lambda c: -random.randint(80, 200),
        "army_delta":     lambda c: -random.randint(15, 40),
        "res_delta":      lambda c: {},
        "text": lambda c, td, rd: (
            f"⚔️ В {c['emoji']} <b>{c['name']}</b> вспыхнуло восстание недовольных!\n"
            f"Армия подавила мятеж, но потеряла <b>{abs(td)}💰</b> и <b>{abs(rd.get('army_delta',0))} солдат</b>."
        ),
    },
    {
        "id": "diplomatic_gift",
        "emoji": "🎁",
        "title": "Дипломатический подарок",
        "weight": 4,
        "positive": True,
        "requires": ["no_war"],
        "treasury_delta": lambda c: random.randint(100, 300),
        "army_delta":     lambda c: 0,
        "res_delta":      lambda c: {random.choice(["food","production","science","oil","rare_metals"]): random.randint(30, 80)},
        "text": lambda c, td, rd: (
            f"🎁 Неизвестная держава прислала дипломатический подарок {c['emoji']} <b>{c['name']}</b>!\n"
            f"+{td}💰 и ресурсы: " + ", ".join(f"+{v} {k}" for k,v in rd.items())
        ),
    },
]


def _check_event_requires(event: dict, country: dict) -> bool:
    """Проверяет выполнение условий для события."""
    reqs = event.get("requires", [])
    for req in reqs:
        if req == "war":
            pass  # проверяется снаружи если нужно
        if req == "no_war":
            pass  # тоже снаружи
        if req == "rich" and country["treasury"] < 500:
            return False
        if req == "army" and country["army"] < 50:
            return False
    return True


async def random_event_tick(chat_id: int, bot):
    """Применяет случайное событие к одной случайной стране в чате."""
    db = await _open_db()
    async with db:

        # Берём все страны чата
        c = await db.execute("SELECT * FROM countries WHERE chat_id=?", (chat_id,))
        countries = [dict(r) for r in await c.fetchall()]
        if not countries:
            return

        # Случайная страна — жертва события
        country = random.choice(countries)

        # Проверяем активные войны для условий
        war_c = await db.execute(
            "SELECT id FROM wars WHERE chat_id=? AND status='active' AND (attacker_id=? OR defender_id=?)",
            (chat_id, country["id"], country["id"])
        )
        in_war = await war_c.fetchone() is not None

        # Фильтруем подходящие события
        eligible = []
        for ev in RANDOM_EVENTS:
            reqs = ev.get("requires", [])
            if "war" in reqs and not in_war:
                continue
            if "no_war" in reqs and in_war:
                continue
            if not _check_event_requires(ev, country):
                continue
            eligible.append(ev)

        if not eligible:
            eligible = [e for e in RANDOM_EVENTS if not e.get("requires")]

        # Взвешенный случайный выбор
        weights = [ev["weight"] for ev in eligible]
        event = random.choices(eligible, weights=weights, k=1)[0]

        # Вычисляем дельты
        treasury_delta = event["treasury_delta"](country)
        army_delta     = event["army_delta"](country)
        res_delta      = event["res_delta"](country)

        # Защита от отрицательных значений
        new_treasury = max(0, country["treasury"] + treasury_delta)
        new_army     = max(0, country["army"]     + army_delta)

        await db.execute(
            "UPDATE countries SET treasury=?, army=? WHERE id=?",
            (new_treasury, new_army, country["id"])
        )

        # Применяем ресурсы
        res_fields = ["food", "production", "oil", "rare_metals", "science", "population"]
        for field in res_fields:
            if field in res_delta and res_delta[field] != 0:
                if res_delta[field] > 0:
                    await db.execute(
                        f"UPDATE resources SET {field}=MIN(99999,{field}+?) WHERE country_id=?",
                        (res_delta[field], country["id"])
                    )
                else:
                    await db.execute(
                        f"UPDATE resources SET {field}=MAX(0,{field}+?) WHERE country_id=?",
                        (res_delta[field], country["id"])
                    )

        await db.commit()

    # Формируем текст события
    # Для событий где текст использует army_delta — передаём правильное значение
    res_for_text = dict(res_delta)
    res_for_text["army_delta"] = army_delta

    try:
        text_body = event["text"](country, treasury_delta, res_for_text)
    except Exception:
        text_body = f"В стране {country['emoji']} {country['name']} произошло событие «{event['title']}»."

    tone = "🟢" if event["positive"] is True else ("🔴" if event["positive"] is False else "🟡")
    full_text = (
        f"{tone} <b>Случайное событие — {event['emoji']} {event['title']}</b>\n\n"
        f"{text_body}"
    )

    try:
        await _safe_send(bot, chat_id, full_text, parse_mode="HTML")
    except Exception as e:
        logging.warning(f"random_event_tick send error: {e}")

    # Логируем для газеты
    await log_event(chat_id, "random_event", {
        "country": country["name"],
        "emoji":   country["emoji"],
        "event":   event["title"],
        "positive": event["positive"],
    })


async def background_random_events(bot: Bot):
    """Каждые RANDOM_EVENTS_INTERVAL_H часов — случайное событие в каждом чате."""
    await asyncio.sleep(120)  # пауза при старте
    while True:
        try:
            chat_ids = await get_all_chats()
            for chat_id in chat_ids:
                if random.random() < RANDOM_EVENT_CHANCE:
                    await random_event_tick(chat_id, bot)
            logging.info(f"Random events tick done for {len(chat_ids)} chats")
        except Exception as e:
            logging.error(f"background_random_events error: {e}")
        await asyncio.sleep(RANDOM_EVENTS_INTERVAL_H * 3600)


async def background_un_tick(bot: Bot):
    """Каждый час проверяем истёкшие резолюции ООН."""
    await asyncio.sleep(300)
    while True:
        try:
            import time as _t
            db = await _open_db()
            async with db:
                c = await db.execute(
                    "SELECT * FROM un_resolutions WHERE status='voting' AND expires_at<?",
                    (int(_t.time()),)
                )
                expired = [dict(r) for r in await c.fetchall()]
                for res in expired:
                    votes_for = json.loads(res["votes_for"])
                    votes_against = json.loads(res["votes_against"])
                    passed = len(votes_for) > len(votes_against)
                    status = "passed" if passed else "rejected"
                    await db.execute("UPDATE un_resolutions SET status=? WHERE id=?", (status, res["id"]))

                    target = None
                    if res["target_id"]:
                        c2 = await db.execute("SELECT * FROM countries WHERE id=?", (res["target_id"],))
                        t = await c2.fetchone()
                        target = dict(t) if t else None

                    type_names = {"sanctions": "Санкции", "peacekeepers": "Миротворцы", "condemn": "Осуждение"}
                    result_str = "✅ ПРИНЯТА" if passed else "❌ ОТКЛОНЕНА"
                    tar_str = f"{target['emoji']} {target['name']}" if target else "все"
                    try:
                        await bot.send_message(
                            res["chat_id"],
                            f"🌐 <b>ООН — Резолюция {result_str}</b>\n\n"
                            f"Тип: {type_names.get(res['type'], res['type'])}\n"
                            f"Цель: {tar_str}\n"
                            f"За: {len(votes_for)} | Против: {len(votes_against)}\n\n"
                            + (f"⚠️ Резолюция вступает в силу!" if passed else "Резолюция провалена."),
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                await db.commit()
        except Exception as e:
            logging.error(f"background_un_tick error: {e}")
        await asyncio.sleep(3600)


# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════

async def set_commands(bot: Bot):
    await bot.set_my_commands([
        BotCommand(command="start",     description="👋 Приветствие и старт"),
        BotCommand(command="create",    description="🌍 Основать страну"),
        BotCommand(command="mycountry", description="🏛 Моя страна"),
        BotCommand(command="map",       description="🗺 Карта мира"),
        BotCommand(command="top",       description="🏆 Рейтинг"),
        BotCommand(command="isolist",   description="📋 Доступные территории"),
        BotCommand(command="tax",       description="💰 Собрать налоги"),
        BotCommand(command="income",    description="📊 Доход страны"),
        BotCommand(command="buy",       description="🛒 Магазин"),
        BotCommand(command="expand",    description="🚩 Захватить нейтральную территорию"),
        BotCommand(command="war",       description="⚔️ Объявить войну"),
        BotCommand(command="status",     description="📊 Сводка: моя страна + войны чата"),
        BotCommand(command="peace",     description="🕊 Предложить мир"),
        BotCommand(command="wars",      description="📜 Активные войны"),
        BotCommand(command="ally",      description="🤝 Предложить союз"),
        BotCommand(command="pact",      description="📜 Пакт о ненападении"),
        BotCommand(command="breakally", description="💔 Разорвать союз"),
        BotCommand(command="diplomacy", description="🗺 Дипломатия"),
        BotCommand(command="garrison",    description="🏰 Гарнизоны — размещение войск по территориям"),
        BotCommand(command="withdraw",    description="🔙 Вывести войска из гарнизона обратно в пул"),
        BotCommand(command="redistribute", description="⚖️ Распределить войска по территориям"),
        BotCommand(command="move",        description="🚚 Переместить гарнизон между территориями"),
        BotCommand(command="claim",       description="📜 Претензии (casus belli) на территории"),
        BotCommand(command="weariness",   description="😓 Военная усталость всех стран"),
        BotCommand(command="capital",    description="🏯 Столица страны (посмотреть/изменить)"),
        BotCommand(command="transfer",  description="💸 Перевести золото"),
        BotCommand(command="reinforce",   description="🛡 Экстренное подкрепление (во время войны)"),
        BotCommand(command="newspaper",   description="📰 Выпустить газету"),
        BotCommand(command="cancel",      description="❌ Отменить текущее действие"),
        BotCommand(command="delete",      description="🗑 Удалить страну"),
        BotCommand(command="rename",      description="✏️ Переименовать страну (1 раз, 500💰)"),
        BotCommand(command="resources",   description="📦 Ресурсы и состояние страны"),
        BotCommand(command="happiness",   description="😊 Счастье народа и протесты"),
        BotCommand(command="build",       description="🏗 Построить здание"),
        BotCommand(command="buildings",   description="🏛 Мои здания"),
        BotCommand(command="research",    description="🔬 Дерево технологий"),
        BotCommand(command="military",    description="⚔️ Управление армией"),
        BotCommand(command="search",      description="🔍 Поиск страны / территории"),
        BotCommand(command="buildmenu",   description="🏗 Меню строительства с кнопками"),
        BotCommand(command="spy",         description="🕵️ Шпионаж"),
        BotCommand(command="ship",        description="🚢 Торговые корабли"),
        BotCommand(command="bombard",     description="💣 Артиллерийская бомбардировка"),
        BotCommand(command="score",       description="🏆 Рейтинг победных очков"),
        BotCommand(command="coalition",   description="🤝 Коалиции"),
        BotCommand(command="bloc",        description="🤝 Военные блоки"),
        BotCommand(command="trade",         description="📦 Торговля ресурсами и войсками"),
        BotCommand(command="trade_accept",  description="✅ Принять торговое предложение"),
        BotCommand(command="trade_decline", description="❌ Отклонить торговое предложение"),
        BotCommand(command="vassal",      description="⛓ Вассализация"),
        BotCommand(command="un",          description="🌐 ООН и резолюции"),
        BotCommand(command="nuclear",     description="☢️ Ядерный удар"),
        BotCommand(command="achievements",description="🏆 Достижения"),
        BotCommand(command="history",     description="📜 История страны"),
        BotCommand(command="mymap",       description="🗺 Мини-карта страны"),
        BotCommand(command="help",        description="📖 Полное руководство"),
        BotCommand(command="giveterr",    description="🤝 Передать территорию другой стране"),
        BotCommand(command="sellterr",    description="🏷 Выставить территорию на продажу"),
        BotCommand(command="buyterr",     description="🛒 Купить территорию с рынка"),
        BotCommand(command="termarket",   description="🏪 Рынок территорий"),
    ])


# ── Торговые корабли ──────────────────────────────────────���──────────────────

SHIP_LEVELS = {
    1: {"name": "Шхуна",       "capacity": 100,  "cost_gold": 300,  "speed_min_per_deg": 2.0},
    2: {"name": "Бриг",        "capacity": 250,  "cost_gold": 700,  "speed_min_per_deg": 1.6},
    3: {"name": "Фрегат",      "capacity": 500,  "cost_gold": 1500, "speed_min_per_deg": 1.2},
    4: {"name": "Галеон",      "capacity": 1000, "cost_gold": 3000, "speed_min_per_deg": 0.9},
    5: {"name": "Броненосец",  "capacity": 2000, "cost_gold": 6000, "speed_min_per_deg": 0.6},
}

# Приблизительные координаты центров стран (широта, долгота)
# Используем для расчёта расстояния между ISO
import math as _math

def _iso_coords(iso: str) -> tuple:
    """Возвращает (lat, lon) для ISO. Если нет — (0, 0)."""
    _coords = {
        "DE": (51.0, 10.0), "FR": (46.0, 2.0), "GB": (54.0, -2.0),
        "IT": (42.0, 12.0), "ES": (40.0, -4.0), "PL": (52.0, 20.0),
        "RU": (60.0, 100.0), "US": (38.0, -97.0), "CN": (35.0, 105.0),
        "JP": (36.0, 138.0), "BR": (-10.0, -55.0), "IN": (20.0, 77.0),
        "AU": (-25.0, 133.0), "CA": (60.0, -96.0), "MX": (23.0, -102.0),
        "AR": (-34.0, -64.0), "ZA": (-30.0, 25.0), "NG": (8.0, 8.0),
        "EG": (26.0, 30.0), "SA": (24.0, 45.0), "TR": (39.0, 35.0),
        "UA": (49.0, 32.0), "SE": (62.0, 15.0), "NO": (64.0, 14.0),
        "FI": (64.0, 26.0), "DK": (56.0, 10.0), "NL": (52.0, 5.0),
        "BE": (50.0, 4.0), "CH": (47.0, 8.0), "AT": (47.0, 14.0),
        "CZ": (50.0, 15.0), "SK": (48.0, 19.0), "HU": (47.0, 19.0),
        "RO": (46.0, 25.0), "BG": (43.0, 25.0), "GR": (39.0, 22.0),
        "PT": (39.0, -8.0), "IE": (53.0, -8.0), "IS": (65.0, -18.0),
        "EE": (59.0, 25.0), "LV": (57.0, 25.0), "LT": (56.0, 24.0),
        "BY": (53.0, 28.0), "MD": (47.0, 29.0), "RS": (44.0, 21.0),
        "HR": (45.0, 16.0), "BA": (44.0, 17.0), "SI": (46.0, 15.0),
        "KR": (37.0, 128.0), "KP": (40.0, 127.0), "VN": (16.0, 108.0),
        "TH": (15.0, 101.0), "ID": (-5.0, 120.0), "PH": (13.0, 122.0),
        "PK": (30.0, 70.0), "IR": (32.0, 53.0), "IQ": (33.0, 44.0),
        "SY": (35.0, 38.0), "IL": (31.0, 35.0), "KZ": (48.0, 68.0),
        "UZ": (41.0, 64.0), "AF": (33.0, 65.0), "MM": (17.0, 96.0),
        "KE": (1.0, 38.0), "ET": (9.0, 40.0), "TZ": (-6.0, 35.0),
        "MA": (32.0, -5.0), "DZ": (28.0, 3.0), "SD": (15.0, 30.0),
        "CD": (-4.0, 25.0), "GH": (8.0, -1.0), "CI": (7.0, -6.0),
        "CL": (-30.0, -71.0), "CO": (4.0, -72.0), "PE": (-10.0, -76.0),
        "VE": (8.0, -66.0), "BO": (-16.0, -65.0), "PY": (-23.0, -58.0),
        "UY": (-33.0, -56.0), "EC": (-2.0, -78.0),
        "NZ": (-41.0, 174.0), "MY": (4.0, 109.0), "SG": (1.3, 103.8),
    }
    return _coords.get(iso.upper(), (0.0, 0.0))


def _travel_minutes(from_iso: str, to_iso: str, speed_min_per_deg: float) -> int:
    """Расчёт времени пути в минутах на основе расстояния между ISO."""
    lat1, lon1 = _iso_coords(from_iso)
    lat2, lon2 = _iso_coords(to_iso)
    # Расстояние в градусах (приближение)
    dist = _math.sqrt((lat2 - lat1) ** 2 + (lon2 - lon1) ** 2)
    minutes = max(2, int(dist * speed_min_per_deg))
    return min(minutes, 360)  # макс 6 часов


async def _resolve_naval_assault(chat_id: int, bot, ship: dict, troops: int, target_iso: str):
    """
    Обрабатывает прибытие десантного корабля.
    Запускает бой: десант vs защитник территории.
    Победа → захват территории; поражение → пехота гибнет.
    Корабль в любом случае возвращается домой.
    """
    db = await _open_db()
    async with db:

        # Загружаем атакующего (владелец корабля)
        att_c = await db.execute("SELECT * FROM countries WHERE id=? AND chat_id=?",
                                 (ship["owner_id"], chat_id))
        attacker = await att_c.fetchone()
        if not attacker:
            return
        attacker = dict(attacker)

        # Загружаем защитника (кто сейчас владеет территорией)
        def_c = await db.execute(
            "SELECT c.* FROM countries c JOIN territories t ON t.country_id=c.id "
            "WHERE t.chat_id=? AND t.iso=? LIMIT 1",
            (chat_id, target_iso)
        )
        defender_row = await def_c.fetchone()
        defender = dict(defender_row) if defender_row else None

        # Считаем гарнизон защитника на цели
        gar_strength = 0
        if defender:
            gc = await db.execute(
                "SELECT infantry+cavalry+artillery+airforce+bomber+navy+COALESCE(armored,0)+COALESCE(tank,0) as total "
                "FROM garrisons WHERE chat_id=? AND country_id=? AND iso=?",
                (chat_id, defender["id"], target_iso)
            )
            gr = await gc.fetchone()
            gar_strength = (gr["total"] or 0) if gr else 0

        # Крепость
        fortress_bonus = 1.0
        if defender:
            fc = await db.execute(
                "SELECT level FROM buildings WHERE country_id=? AND iso=? AND type='fortress'",
                (defender["id"], target_iso)
            )
            fort = await fc.fetchone()
            if fort:
                fortress_bonus = 1.0 + fort["level"] * 0.25

        ter_name = COUNTRY_NAMES.get(target_iso, target_iso)
        def_army_total = (defender["army"] if defender else 0) + gar_strength
        def_effective  = gar_strength + ((defender["army"] // 2) if defender else 0)

        # Объявление десанта
        def_label = f"{defender['emoji']} {defender['name']}" if defender else "нейтральная"
        try:
            ann = await bot.send_message(
                chat_id,
                f"⚓ <b>МОРСКОЙ ДЕСАНТ ПРИБЫЛ!</b>\n\n"
                f"🚢 «{ship['name']}» ({attacker['emoji']} {attacker['name']}) "
                f"высаживает <b>{troops} 🪖 пехотинцев</b>\n"
                f"📍 Цель: <b>{ter_name}</b> ({def_label})\n\n"
                f"⚔️ Начинается <b>морской бой!</b>",
                parse_mode="HTML"
            )
            ann_id = ann.message_id
        except Exception:
            ann_id = 0

        # Бой: атакующая сила = десантная пехота (×0.85 морской штраф)
        attack_army = max(1, int(troops * 0.85))

        won, losses_att, losses_def = await run_battle_rounds(
            bot=bot,
            chat_id=chat_id,
            message_id=ann_id,
            attacker=attacker,
            defender=defender,
            target_iso=target_iso,
            attack_army=attack_army,
            def_army=max(1, def_effective) if (defender and def_army_total > 0) else 0,
            fortress_bonus=fortress_bonus,
            attack_mode="naval",
            atk_bonus=1.0,
        )

        # Применяем потери
        # Потери атакующего — из десантной пехоты (она уже списана из army)
        # losses_att уже учтены (пехота была снята при отправке)
        # возвращаем выживших атакующих
        survivors = max(0, troops - losses_att)
        if survivors > 0:
            await db.execute(
                "UPDATE military SET infantry=infantry+? WHERE country_id=?",
                (survivors, attacker["id"])
            )
            await db.execute(
                "UPDATE countries SET army=army+? WHERE id=?",
                (survivors, attacker["id"])
            )

        if defender:
            # Потери защитника: сначала из гарнизона, потом из пула
            gar_losses = min(losses_def, gar_strength)
            pool_losses = max(0, losses_def - gar_losses)
            if gar_losses > 0:
                # Снимаем пропорционально из гарнизона
                gar_c = await db.execute(
                    "SELECT * FROM garrisons WHERE chat_id=? AND country_id=? AND iso=?",
                    (chat_id, defender["id"], target_iso)
                )
                gar = await gar_c.fetchone()
                if gar:
                    gar_total = sum((gar[u] or 0) for u in UNIT_TYPES)
                    if gar_total > 0:
                        for u in UNIT_TYPES:
                            u_cnt = gar[u] or 0
                            if u_cnt > 0:
                                u_loss = min(u_cnt, int(gar_losses * u_cnt / gar_total))
                                if u_loss > 0:
                                    await db.execute(
                                        f"UPDATE garrisons SET {u}=MAX(0,{u}-?) "
                                        "WHERE chat_id=? AND country_id=? AND iso=?",
                                        (u_loss, chat_id, defender["id"], target_iso)
                                    )
            if pool_losses > 0:
                await db.execute(
                    "UPDATE countries SET army=MAX(0,army-?) WHERE id=?",
                    (pool_losses, defender["id"])
                )

        # Результат
        if won:
            await capture_territory(chat_id, target_iso, attacker["id"], bot=bot)
            result_msg = (
                f"🏆 <b>ДЕСАНТ ПОБЕДИЛ!</b>\n\n"
                f"{attacker['emoji']} <b>{attacker['name']}</b> захватил <b>{ter_name}</b>!\n"
                f"🪖 Выжило десантников: {survivors}/{troops}\n"
                f"📊 Потери защитника: {losses_def} солдат"
            )
            # Проверка — не обнулён ли защитник
            if defender:
                def_terrs = await get_country_territories(chat_id, defender["id"])
                if not def_terrs:
                    war = await get_active_war(chat_id, attacker["id"], defender["id"])
                    if war:
                        spoils = await end_war(war["id"], attacker["id"], defender["id"])
                        await add_vp(attacker["id"], 50)
                        result_msg += (
                            f"\n\n🏆 <b>{defender['emoji']} {defender['name']} разгромлена!</b>\n"
                            f"💰 Трофеи: {spoils}💰"
                        )
        else:
            result_msg = (
                f"💀 <b>ДЕСАНТ ОТБИТ!</b>\n\n"
                f"{attacker['emoji']} <b>{attacker['name']}</b> не смог захватить <b>{ter_name}</b>!\n"
                f"🪖 Выжило десантников: {survivors}/{troops} (возвращены в армию)\n"
                f"📊 Потери защитника: {losses_def} солдат"
            )

        await add_weariness(attacker["id"], WEARINESS_ON_WIN if won else WEARINESS_ON_LOSS)
        if defender:
            await add_weariness(defender["id"], WEARINESS_ON_LOSS if won else WEARINESS_ON_WIN)

        await log_event(chat_id, "naval_assault", {
            "attacker": attacker["name"], "attacker_emoji": attacker["emoji"],
            "defender": defender["name"] if defender else None,
            "territory": ter_name, "iso": target_iso,
            "troops": troops, "won": won,
            "losses_att": losses_att, "losses_def": losses_def,
        })

        # Корабль идёт домой
        sdef = SHIP_LEVELS.get(ship["level"], SHIP_LEVELS[1])
        return_min = _travel_minutes(ship["to_iso"], ship["from_iso"], sdef["speed_min_per_deg"])
        now2 = int(__import__('time').time())
        await db.execute(
            "UPDATE trade_ships SET status='returning', cargo_res='', cargo_amt=0, troops_amt=0, "
            "from_iso=?, to_iso=?, depart_at=?, arrive_at=? WHERE id=?",
            (ship["to_iso"], ship["from_iso"], now2, now2 + return_min * 60, ship["id"])
        )
        await db.commit()

        try:
            await _safe_send(bot, chat_id, result_msg, parse_mode="HTML")
        except Exception:
            pass


async def resolve_trade_ships(chat_id: int, bot):
    """Фоновый тик: доставляем прибывшие корабли."""
    now = int(__import__('time').time())
    db = await _open_db()
    async with db:
        c = await db.execute(
            "SELECT * FROM trade_ships WHERE chat_id=? AND status IN ('sailing','returning') AND arrive_at<=?",
            (chat_id, now)
        )
        ships = [dict(r) for r in await c.fetchall()]
        for ship in ships:
            if ship["status"] == "returning":
                # Корабль вернулся домой
                await db.execute(
                    "UPDATE trade_ships SET status='idle', from_iso='', to_iso='', "
                    "depart_at=0, arrive_at=0 WHERE id=?",
                    (ship["id"],)
                )
                owner_c = await db.execute("SELECT * FROM countries WHERE id=?", (ship["owner_id"],))
                owner = await owner_c.fetchone()
                if owner:
                    try:
                        home_name = COUNTRY_NAMES.get(ship["to_iso"], ship["to_iso"])
                        await bot.send_message(
                            chat_id,
                            f"⚓ <b>Корабль вернулся!</b>\n"
                            f"🚢 «{ship['name']}» ({owner['emoji']} {owner['name']}) "
                            f"вернулся в порт {home_name} и готов к новому рейсу.",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                continue
            # Доставляем груз получателю
            target_c = await db.execute(
                "SELECT c.* FROM countries c JOIN territories t ON t.country_id=c.id "
                "WHERE t.chat_id=? AND t.iso=? LIMIT 1",
                (chat_id, ship["to_iso"])
            )
            target = await target_c.fetchone()

            # ── Морской десант (атака) ─────────────────────────────
            if ship["cargo_res"] == "assault":
                troops_amt = ship.get("troops_amt", 0) or 0
                await db.commit()  # фиксируем смену статуса корабля до боя
                if troops_amt > 0:
                    try:
                        await _resolve_naval_assault(
                            chat_id=chat_id,
                            bot=bot,
                            ship=dict(ship),
                            troops=troops_amt,
                            target_iso=ship["to_iso"],
                        )
                    except Exception as _ae:
                        logging.error(f"naval assault error: {_ae}", exc_info=True)
                        owner_c2 = await db.execute("SELECT * FROM countries WHERE id=?", (ship["owner_id"],))
                        owner2 = await owner_c2.fetchone()
                        if owner2:
                            try:
                                await _safe_send(bot, chat_id,
                                    f"⚠️ Ошибка при обработке десанта корабля «{ship['name']}».",
                                    parse_mode="HTML")
                            except Exception:
                                pass
                continue  # корабль уже переведён в returning внутри _resolve_naval_assault
            # ── Обычный ресурсный груз ─────────────────────────────
            elif target and ship["cargo_res"] and ship["cargo_amt"] > 0:
                res = ship["cargo_res"]
                if res == "treasury":
                    await db.execute(
                        "UPDATE countries SET treasury=treasury+? WHERE id=?",
                        (ship["cargo_amt"], target["id"])
                    )
                else:
                    await db.execute(
                        f"UPDATE resources SET {res}=MIN(99999,{res}+?) WHERE country_id=?",
                        (ship["cargo_amt"], target["id"])
                    )
            # Корабль возвращается обратно (обратный рейс без груза)
            sdef = SHIP_LEVELS.get(ship["level"], SHIP_LEVELS[1])
            return_min = _travel_minutes(ship["to_iso"], ship["from_iso"], sdef["speed_min_per_deg"])
            now2 = int(__import__('time').time())
            await db.execute(
                "UPDATE trade_ships SET status='returning', cargo_res='', cargo_amt=0, troops_amt=0, "
                "from_iso=?, to_iso=?, depart_at=?, arrive_at=? WHERE id=?",
                (ship["to_iso"], ship["from_iso"], now2, now2 + return_min * 60, ship["id"])
            )
            # Уведомляем владельца
            owner_c = await db.execute("SELECT * FROM countries WHERE id=?", (ship["owner_id"],))
            owner = await owner_c.fetchone()
            if owner:
                to_name = COUNTRY_NAMES.get(ship["to_iso"], ship["to_iso"])
                try:
                    if ship["cargo_res"] == "troops":
                        troops_amt = ship.get("troops_amt", 0) or 0
                        await bot.send_message(
                            chat_id,
                            f"⚓ <b>Десантный транспорт прибыл!</b>\n\n"
                            f"🚢 «{ship['name']}» ({owner['emoji']} {owner['name']}) "
                            f"доставил <b>{troops_amt} 🪖 пехотинцев</b> "
                            f"в {to_name}. Войска зачислены в армию!",
                            parse_mode="HTML"
                        )
                    elif target:
                        res_label = {"food": "🌾 Еда", "production": "⚙️ Производство",
                                     "oil": "🛢 Нефть", "rare_metals": "💎 Металлы",
                                     "science": "🔬 Наука", "treasury": "💰 Золото"}.get(ship["cargo_res"], ship["cargo_res"])
                        await bot.send_message(
                            chat_id,
                            f"⚓ <b>Корабль прибыл!</b>\n\n"
                            f"🚢 «{ship['name']}» ({owner['emoji']} {owner['name']}) "
                            f"доставил <b>{ship['cargo_amt']} {res_label}</b> "
                            f"в {to_name} "
                            f"({target['emoji']} {target['name']}).",
                            parse_mode="HTML"
                        )
                except Exception:
                    pass
        await db.commit()


async def migrate_fix_territories():
    """При старте восстанавливает стартовую территорию для стран у которых 0 территорий."""
    db = await _open_db()
    async with db:
        c = await db.execute("SELECT id, chat_id, iso FROM countries")
        countries = await c.fetchall()
        for country in countries:
            ct = await db.execute(
                "SELECT COUNT(*) as cnt FROM territories WHERE chat_id=? AND country_id=?",
                (country["chat_id"], country["id"])
            )
            row = await ct.fetchone()
            if row["cnt"] == 0:
                await db.execute(
                    """INSERT OR IGNORE INTO territories (chat_id, iso, country_id)
                       VALUES (?,?,?)""",
                    (country["chat_id"], country["iso"].upper(), country["id"])
                )
                logging.info(f"migrate: restored territory {country['iso']} for country {country['id']}")
        await db.commit()


async def _repair_world_state():
    """Безопасная проверка целостности мира при каждом запуске.

    Ничего не "бафает" игроков: только исправляет физически невозможные
    значения и сироты после старых версий бота.
    """
    db = await _open_db()
    async with db:
        # Никаких отрицательных ресурсов/денег/войск из старых эксплойтов.
        await db.execute("UPDATE countries SET treasury=MAX(0,treasury), army=MAX(0,army), tax_rate=MIN(100,MAX(0,tax_rate))")
        await db.execute("UPDATE resources SET food=MAX(0,food), production=MAX(0,production), rare_metals=MAX(0,rare_metals), oil=MAX(0,oil), science=MAX(0,science), population=MAX(0,population), happiness=MIN(100,MAX(0,happiness))")
        await db.execute("UPDATE military SET infantry=MAX(0,infantry), cavalry=MAX(0,cavalry), artillery=MAX(0,artillery), airforce=MAX(0,airforce), navy=MAX(0,navy), bomber=MAX(0,bomber), armored=MAX(0,armored), tank=MAX(0,tank)")
        await db.execute("UPDATE garrisons SET infantry=MAX(0,infantry), cavalry=MAX(0,cavalry), artillery=MAX(0,artillery), airforce=MAX(0,airforce), navy=MAX(0,navy), bomber=MAX(0,bomber), armored=MAX(0,armored), tank=MAX(0,tank)")
        # Синхронизируем общий army только с войсками в пуле, не с гарнизонами.
        await db.execute("""UPDATE countries SET army=COALESCE((SELECT infantry+cavalry+artillery+airforce+navy+bomber+armored+tank FROM military m WHERE m.country_id=countries.id),0)""")
        # Удаляем заведомо битые незавершённые деплои.
        await db.execute("DELETE FROM deployments WHERE unit_count<=0 OR resolved NOT IN (0,1)")
        # Война между удалёнными странами не должна оставаться активной.
        await db.execute("""UPDATE wars SET status='ended', ended_at=strftime('%s','now')
                           WHERE status='active' AND (attacker_id NOT IN (SELECT id FROM countries)
                           OR defender_id NOT IN (SELECT id FROM countries))""")
        await db.commit()
    logging.info("world repair: integrity checks completed")


async def _supervise_background(name: str, factory, bot: Bot):
    """Не даёт одной необработанной ошибке убить вечный игровой цикл."""
    while True:
        try:
            await factory(bot)
            logging.warning("background %s завершился штатно; перезапускаем через 2с", name)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("background %s crashed; restarting in 5s", name)
            await asyncio.sleep(5)
        else:
            await asyncio.sleep(2)


_BG_TASKS: list = []


# ═══════════════════════════════════════════════════════════════
#  ПЛЮШКИ: healthcheck для Amvera, антифлуд, авто-бэкап БД
# ═══════════════════════════════════════════════════════════════

def _start_healthcheck() -> None:
    """HTTP-эндпоинт для облака: платформа видит, что контейнер жив и слушает порт."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    port = int(os.environ.get("PORT", "8080"))
    try:
        _srv = HTTPServer(("0.0.0.0", port), _HealthHandler)
        threading.Thread(target=_srv.serve_forever, daemon=True).start()
        logging.info(f"Healthcheck слушает 0.0.0.0:{port}")
    except Exception as _e:
        logging.warning(f"Healthcheck не запустился: {_e}")


class AntiFloodMiddleware(BaseMiddleware):
    """Не более 8 событий за 4 секунды с одного аккаунта (админы не ограничены)."""

    def __init__(self, limit: int = 8, window: float = 4.0):
        self.limit = limit
        self.window = window
        self._hist: dict = {}

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None or user.id in ADMIN_IDS:
            return await handler(event, data)
        now = time.monotonic()
        hist = [t for t in self._hist.get(user.id, ()) if now - t < self.window]
        if len(hist) >= self.limit:
            self._hist[user.id] = hist
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer("⏳ Не так быстро, подожди пару секунд")
                except Exception:
                    pass
            return
        hist.append(now)
        self._hist[user.id] = hist
        if len(self._hist) > 20000:
            self._hist.clear()
        return await handler(event, data)


def _daily_db_backup() -> None:
    """Раз в сутки делает консистентный бэкап БД рядом (в /data/backups), хранит 5 штук."""
    import glob
    import sqlite3

    try:
        base_dir = os.path.dirname(os.path.abspath(DB_PATH))
        backup_dir = os.path.join(base_dir, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d")
        dst_path = os.path.join(backup_dir, f"countries_rp-{stamp}.db")
        if os.path.exists(dst_path) or not os.path.exists(DB_PATH):
            return
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(dst_path)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        old = sorted(glob.glob(os.path.join(backup_dir, "countries_rp-*.db")))
        for path in old[:-5]:
            try:
                os.remove(path)
            except OSError:
                pass
        logging.info(f"Бэкап БД: {dst_path}")
    except Exception as _e:
        logging.warning(f"Бэкап БД не удался: {_e}")


async def main():
    _start_healthcheck()
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set. Export BOT_TOKEN before starting the bot.")

    # Диагностика цветных кнопок
    try:
        _test_btn = colored_button("test", "green", callback_data="test")
        _dump = _test_btn.model_dump(exclude_none=True)
        if "color" in _dump:
            logging.info("✅ Colored buttons: color field присутствует в JSON — работает!")
        else:
            logging.warning("⚠️ Colored buttons: color field ОТСУТСТВУЕТ в JSON — Telegram не получит цвет.")
    except Exception as _e:
        logging.warning(f"⚠️ Colored buttons diagnostic error: {_e}")

    _load_geo()  # Загружаем GeoJSON карту при старте
    _ensure_emoji_font_available()
    await init_db()
    _daily_db_backup()
    await migrate_fix_territories()  # восстанавливаем потерянные территории
    await _repair_world_state()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp  = Dispatcher()
    # SafeContextMiddleware должен стоять ПЕРВЫМ: он отсекает события без
    # from_user и нажатия на недоступные (старые) сообщения до того, как они
    # дойдут до остальных middleware и handler'ов.
    dp.message.middleware(SafeContextMiddleware())
    dp.callback_query.middleware(SafeContextMiddleware())
    dp.message.middleware(AntiFloodMiddleware())
    dp.callback_query.middleware(AntiFloodMiddleware())
    dp.callback_query.middleware(OwnerOnlyCallbackMiddleware())
    dp.message.middleware(UsernameUpdateMiddleware())
    dp.include_router(router_create)
    dp.include_router(router_map)
    dp.include_router(router_war)
    dp.include_router(router_economy)
    dp.include_router(router_newspaper)
    dp.include_router(router_admin)
    dp.include_router(router_dev)
    await set_commands(bot)
    # Получаем username бота чтобы команды вида /cmd@BotName работали в группах
    bot_info = await bot.get_me()
    logging.info(f"Бот запущен: @{bot_info.username}")
    for _aid in ADMIN_IDS:
        try:
            await bot.send_message(_aid, "✅ RP Country запущен и слушает чаты.")
        except Exception:
            pass
    # Запускаем фоновые задачи
    # BUG FIX: результат create_task нужно удерживать. Event loop хранит только
    # слабую ссылку на задачу — без сильной ссылки GC может собрать фоновую
    # задачу в произвольный момент, и, например, начисление дохода просто
    # молча перестанет работать.
    global _BG_TASKS
    _BG_TASKS = [
        asyncio.create_task(_supervise_background("hourly_income", background_hourly_income, bot)),
        asyncio.create_task(_supervise_background("war_tick", background_war_tick, bot)),
        asyncio.create_task(_supervise_background("deployment_tick", background_deployment_tick, bot)),
        asyncio.create_task(_supervise_background("newspaper", background_newspaper, bot)),
        asyncio.create_task(_supervise_background("resource_tick", background_resource_tick, bot)),
        asyncio.create_task(_supervise_background("un_tick", background_un_tick, bot)),
        asyncio.create_task(_supervise_background("random_events", background_random_events, bot)),
    ]
    # Удаляем вебхук и сбрасываем очередь, чтобы не конфликтовать со старым инстансом
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as _we:
        logging.warning(f"delete_webhook: {_we}")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
