import asyncio, logging, os, random, time, json, math, re
import aiosqlite
from io import BytesIO
from datetime import datetime, timedelta

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    for p in [os.path.expanduser("~/.env"), os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")]:
        if os.path.exists(p):
            for line in open(p):
                line = line.strip()
                if line.startswith("BOT_TOKEN="):
                    BOT_TOKEN = line.split("=",1)[1].strip().strip('"').strip("'")
if not BOT_TOKEN: raise ValueError("BOT_TOKEN не задан!")

PROXY       = os.getenv("PROXY", None)   # Не нужен на Amvera, оставь пустым
DB_PATH     = os.getenv("DB_PATH", "/data/petbot.db")  # /data — постоянное хранилище Amvera (не стирается при перезапуске)
ADMIN_IDS   = [8839678195] + [int(x) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip().isdigit()]
GAME_THREAD    = 1667499
GAME_GROUP     = int(os.getenv("GAME_GROUP_ID", "0"))  # ID группы (не топика). Задай через .env: GAME_GROUP_ID=-100xxxxxxxxx
START_TIME     = time.time()
NEWS_CHANNEL   = "@PetWorldNews"   # канал для проверки подписки
REF_REWARD     = 1000              # монет за реферала
REF_NEW_BONUS  = 500                # бонус новому игроку по рефералке

BOT_VERSION = "17.0"
UPDATE_NOTES = [
    "🎫 Стартовал Сезон 2: Эра Титанов — новый пропуск и награды.",
    "🐾 Добавлено 26 новых питомцев, включая титанов и боссов.",
    "⚔️ Добавлено 24 новых оружия и брони, включая звёздные.",
    "🎁 Новые яйца: Яйцо Эры Титанов и Яйцо Титанов.",
    "🌍 Миры: валюта, мастерство, события и боссы.",
    "💵 Быстрая продажа питомцев с защитой от абуза.",
    "💬 Rich-сообщения нового Bot API и обновлённый экран сезона.",
]

# ═══════════════════════════════════════════════════
# v7.0 — СЕЗОННЫЙ ПРОПУСК, АЧИВКИ, СКИЛЛЫ
# ═══════════════════════════════════════════════════

SEASON_ID = 2
SEASON_NAME = "Сезон 2: Эра Титанов"
SEASON_END  = "2027-01-15"

SEASON_TOKEN_REWARDS = {
    "battle_win":    5,
    "daily":         3,
    "work":          1,
    "adventure":     2,
    "daily_quest":  10,
    "login":         1,
}

SEASON_PASS = {
    1:  {"free": ("coins",  1500),       "premium": ("crystals", 20)},
    2:  {"free": ("tokens", 10),         "premium": ("coins", 5000)},
    3:  {"free": ("item", "food"),       "premium": ("pet", "crystal_beetle")},
    4:  {"free": ("coins", 2500),        "premium": ("item", "storm_mail")},
    5:  {"free": ("crystals", 5),        "premium": ("crystals", 25)},
    6:  {"free": ("tokens", 20),         "premium": ("pet", "storm_wolf")},
    7:  {"free": ("item", "elixir"),     "premium": ("item", "storm_blade")},
    8:  {"free": ("coins", 3500),        "premium": ("crystals", 30)},
    9:  {"free": ("crystals", 10),       "premium": ("pet", "thunderbird")},
    10: {"free": ("coins", 5000),        "premium": ("pet", "magma_golem")},
    11: {"free": ("item", "berserker"),  "premium": ("item", "inferno_plate")},
    12: {"free": ("tokens", 30),         "premium": ("item", "inferno_scythe")},
    13: {"free": ("coins", 7000),        "premium": ("pet", "sand_wyrm")},
    14: {"free": ("crystals", 15),       "premium": ("item", "frost_robe")},
    15: {"free": ("item", "revive"),     "premium": ("pet", "frost_queen")},
    16: {"free": ("coins", 10000),       "premium": ("item", "frost_spear")},
    17: {"free": ("tokens", 40),         "premium": ("pet", "sea_dragon")},
    18: {"free": ("crystals", 20),       "premium": ("item", "solar_aegis")},
    19: {"free": ("coins", 12000),       "premium": ("pet", "solar_lion")},
    20: {"free": ("item", "poison"),     "premium": ("pet", "lunar_fox")},
    21: {"free": ("crystals", 25),       "premium": ("item", "lunar_cloak")},
    22: {"free": ("coins", 15000),       "premium": ("item", "lunar_daggers")},
    23: {"free": ("item", "magic_potion"),"premium": ("pet", "ice_phoenix")},
    24: {"free": ("tokens", 50),         "premium": ("item", "void_shield")},
    25: {"free": ("coins", 20000),       "premium": ("pet", "void_reaper")},
    26: {"free": ("crystals", 30),       "premium": ("item", "void_katana")},
    27: {"free": ("item", "revive"),     "premium": ("pet", "nebula_whale")},
    28: {"free": ("coins", 25000),       "premium": ("item", "nebula_scythe")},
    29: {"free": ("tokens", 100),        "premium": ("pet", "abyss_hydra")},
    30: {"free": ("item", "titan_egg"),  "premium": ("pet", "celestial_dragon")},
}
SEASON_TOKENS_PER_LEVEL = 100   # сколько токенов нужно на 1 уровень пропуска
SEASON_PREMIUM_COST     = 500   # кристаллов за премиум пропуск

# ── ТИТУЛЫ ──────────────────────────────────────────────────────────────
TITLES = {
    # за ачивки/прогресс
    "novice":       {"name": "🌱 Новичок",        "desc": "Начало пути"},
    "fighter":      {"name": "⚔️ Боец",           "desc": "10 побед в битвах"},
    "veteran":      {"name": "🛡️ Ветеран",        "desc": "50 побед в битвах"},
    "champion":     {"name": "🏆 Чемпион",        "desc": "100 побед в битвах"},
    "legend":       {"name": "👑 Легенда",         "desc": "500 побед в битвах"},
    "rich":         {"name": "💰 Богач",           "desc": "Накопить 1,000,000 монет"},
    "collector":    {"name": "🐾 Коллекционер",   "desc": "Иметь 10 питомцев"},
    "breeder":      {"name": "🧬 Заводчик",       "desc": "Провести 5 слияний"},
    "explorer":     {"name": "🧭 Исследователь",  "desc": "50 приключений"},
    "season_hero":  {"name": "🎫 Герой сезона",   "desc": "Дойти до 30 уровня пропуска"},
    "auction_king": {"name": "🏪 Аукционист",     "desc": "Выиграть 5 аукционов"},
    "boss_slayer":  {"name": "👹 Боссоборец",     "desc": "Убить 3 глобальных босса"},
    "shiny":        {"name": "✨ Сияющий",        "desc": "Получить shiny питомца"},
    "lucky":        {"name": "🎡 Счастливчик",    "desc": "Сорвать джекпот в колесе удачи"},
    "evolved":      {"name": "🧬 Совершенный",    "desc": "Достичь максимальной эволюции"},
}

# ── АЧИВКИ ──────────────────────────────────────────────────────────────
ACHIEVEMENTS = {
    "first_battle":  {"name": "⚔️ Первый бой",       "desc": "Провести первую битву",        "tokens": 20},
    "win_10":        {"name": "🏅 10 побед",          "desc": "Одержать 10 побед",            "tokens": 50,  "title": "fighter"},
    "win_50":        {"name": "🥈 50 побед",          "desc": "Одержать 50 побед",            "tokens": 150, "title": "veteran"},
    "win_100":       {"name": "🥇 100 побед",         "desc": "Одержать 100 побед",           "tokens": 300, "title": "champion"},
    "win_500":       {"name": "👑 500 побед",         "desc": "Одержать 500 побед",           "tokens": 1000,"title": "legend"},
    "first_pet":     {"name": "🐾 Первый питомец",    "desc": "Получить питомца",             "tokens": 10},
    "pet_10":        {"name": "🗃️ 10 питомцев",      "desc": "Собрать 10 питомцев",          "tokens": 100, "title": "collector"},
    "millionaire":   {"name": "💰 Миллионер",         "desc": "Накопить 1,000,000 монет",     "tokens": 200, "title": "rich"},
    "first_fuse":    {"name": "✨ Алхимик",           "desc": "Провести первое слияние",      "tokens": 30},
    "fuse_5":        {"name": "🧬 Заводчик",          "desc": "5 слияний",                    "tokens": 100, "title": "breeder"},
    "adventure_50":  {"name": "🧭 Исследователь",     "desc": "50 приключений",               "tokens": 100, "title": "explorer"},
    "boss_kill":     {"name": "👹 Убийца боссов",     "desc": "Убить глобального босса",      "tokens": 200},
    "season_30":     {"name": "🎫 Мастер сезона",     "desc": "30 уровень пропуска",          "tokens": 500, "title": "season_hero"},
    "auction_win":   {"name": "🏪 Первый аукцион",    "desc": "Выиграть аукцион",             "tokens": 50},
    "first_rebirth": {"name": "🔥 Возрождение",       "desc": "Первый ребёрт",                "tokens": 200},
    "shiny_pet":     {"name": "✨ Shiny!",            "desc": "Получить shiny питомца",       "tokens": 150, "title": "shiny"},
    "first_spin":    {"name": "🎡 Первое вращение",   "desc": "Крутить колесо удачи",         "tokens": 20},
    "wheel_jackpot": {"name": "💎 Джекпот",           "desc": "Сорвать джекпот в колесе",     "tokens": 200, "title": "lucky"},
    "first_evolution": {"name": "🧬 Эволюционер",     "desc": "Провести первую эволюцию",     "tokens": 40},
    "evo_max":       {"name": "👑 Высшая форма",      "desc": "Достичь 4 стадии эволюции",    "tokens": 300, "title": "evolved"},
}

# ── СКИЛЛЫ ПИТОМЦЕВ ─────────────────────────────────────────────────────
PET_SKILLS = {
    "atk":   {"name": "⚔️ Атака",       "desc": "+5 к урону за уровень",  "max": 10, "cost": 1},
    "def":   {"name": "🛡️ Защита",      "desc": "+5 к броне за уровень",  "max": 10, "cost": 1},
    "hp":    {"name": "❤️ Живучесть",   "desc": "+20 к HP за уровень",    "max": 10, "cost": 1},
    "crit":  {"name": "💥 Крит",        "desc": "+3% шанс крита",         "max": 5,  "cost": 2},
    "regen": {"name": "💚 Регенерация", "desc": "+3 HP/раунд",            "max": 5,  "cost": 2},
}
SKILL_RESET_COST = 50  # кристаллов за сброс всех скиллов питомца

# ── ЕЖЕДНЕВНЫЕ КВЕСТЫ (пул) ─────────────────────────────────────────────
DAILY_QUEST_POOL = [
    {"id": "dq_battle",    "desc": "🗡️ Победить в 3 битвах",     "type": "battle_win", "required": 3,  "tokens": 15, "coins": 500},
    {"id": "dq_work",      "desc": "💼 Поработать 5 раз",         "type": "work",        "required": 5,  "tokens": 10, "coins": 300},
    {"id": "dq_adventure", "desc": "🧭 Пройти 3 приключения",    "type": "adventure",   "required": 3,  "tokens": 12, "coins": 400},
    {"id": "dq_duel",      "desc": "🃏 Сыграть 5 дуэлей",        "type": "duel",        "required": 5,  "tokens": 10, "coins": 300},
    {"id": "dq_daily",     "desc": "🎁 Забрать ежедневку",       "type": "daily",       "required": 1,  "tokens": 5,  "coins": 100},
    {"id": "dq_explore",   "desc": "🗺️ Исследовать 2 локации",  "type": "explore",     "required": 2,  "tokens": 8,  "coins": 250},
    {"id": "dq_fuse",      "desc": "✨ Провести слияние",        "type": "fuse",        "required": 1,  "tokens": 20, "coins": 0},
    {"id": "dq_boss",      "desc": "👹 Атаковать глобального босса","type":"boss_attack","required": 1, "tokens": 25, "coins": 0},
    {"id": "dq_wheel",     "desc": "🎡 Крутить колесо удачи",     "type": "wheel",       "required": 1,  "tokens": 15, "coins": 300},
]

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════
# ПУЛЛ СОЕДИНЕНИЙ — решает "database is locked"
# ═══════════════════════════════════════════════════

_db_conn: aiosqlite.Connection = None
_db_lock: asyncio.Lock = None

def _get_lock():
    global _db_lock
    if _db_lock is None:
        _db_lock = asyncio.Lock()
    return _db_lock

async def get_db() -> aiosqlite.Connection:
    global _db_conn
    if _db_conn is None:
        _db_conn = await aiosqlite.connect(DB_PATH, timeout=30)
        _db_conn.row_factory = aiosqlite.Row
        await _db_conn.execute("PRAGMA journal_mode=WAL")
        await _db_conn.execute("PRAGMA synchronous=NORMAL")
        await _db_conn.execute("PRAGMA cache_size=10000")
        await _db_conn.execute("PRAGMA temp_store=MEMORY")
        await _db_conn.execute("PRAGMA busy_timeout=15000")
        await _db_conn.commit()
    return _db_conn

async def _new_db_conn() -> aiosqlite.Connection:
    conn = await _orig_aiosqlite_connect(DB_PATH, timeout=30)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.execute("PRAGMA busy_timeout=15000")
    await conn.commit()
    return conn


class _DBCtx:
    """async with db() as db: — отдельное соединение на операцию."""
    def __init__(self): self._conn = None
    async def __aenter__(self):
        self._conn = await _new_db_conn()
        return self._conn
    async def __aexit__(self, *_):
        try:
            await self._conn.commit()
        except Exception as e:
            logger.warning(f"db commit: {e}")
            try: await self._conn.rollback()
            except: pass
        finally:
            try: await self._conn.close()
            except: pass

def db():
    return _DBCtx()

# Обёртка для совместимости со старым кодом aiosqlite.connect(...)
class _LegacyCtx:
    """Замена `async with aiosqlite.connect(DB_PATH) as db:` — отдельное соединение."""
    async def __aenter__(self):
        self._conn = await _new_db_conn()
        return self._conn
    async def __aexit__(self, *_):
        try:
            await self._conn.commit()
        except Exception as e:
            logger.warning(f"db commit legacy: {e}")
            try: await self._conn.rollback()
            except: pass
        finally:
            try: await self._conn.close()
            except: pass

# Патчим aiosqlite.connect чтобы весь старый код автоматически использовал пул
_orig_aiosqlite_connect = aiosqlite.connect
def _patched_connect(path, **kw):
    if path == DB_PATH:
        return _LegacyCtx()
    return _orig_aiosqlite_connect(path, **kw)
aiosqlite.connect = _patched_connect

from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.types import (Message, CallbackQuery, TelegramObject,
                            KeyboardButton, LabeledPrice, PreCheckoutQuery,
                            BufferedInputFile)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

PREMIUM_EMOJI_ENABLED = os.getenv("PREMIUM_EMOJI", "1").lower() not in ("0", "false", "no", "off")
PREMIUM_EMOJI_PACK = os.getenv("PREMIUM_EMOJI_PACK", "AnimatedEmoji").strip()
try:
    PREMIUM_EMOJI_MAX = max(0, int(os.getenv("PREMIUM_EMOJI_MAX", "30")))
except ValueError:
    PREMIUM_EMOJI_MAX = 30
_PE_CACHE_PATH = os.path.join(os.path.dirname(DB_PATH) or ".", "premium_emoji.json")
_PE_MAP = {}
_PE_RE = None
_PE_ACTIVE = False
_PE_PACK = ""
_PE_CACHE_MTIME = 0.0

def _pe_cache_dir():
    d = os.path.dirname(_PE_CACHE_PATH)
    if not d:
        return "."
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:
        return os.path.dirname(os.path.abspath(__file__))

def _pe_set_map(mapping, pack=""):
    global _PE_MAP, _PE_RE, _PE_ACTIVE, _PE_PACK
    _PE_MAP = {k: v for k, v in (mapping or {}).items() if k and v}
    _PE_PACK = pack or ""
    keys = sorted(_PE_MAP, key=len, reverse=True)
    _PE_RE = re.compile("|".join(re.escape(k) for k in keys)) if keys else None
    _PE_ACTIVE = bool(_PE_RE) and PREMIUM_EMOJI_ENABLED and PREMIUM_EMOJI_MAX > 0

def _pe_cache_load(force=False):
    global _PE_CACHE_MTIME
    if not PREMIUM_EMOJI_ENABLED:
        return False
    path = os.path.join(_pe_cache_dir(), os.path.basename(_PE_CACHE_PATH))
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return False
    if not force and mtime == _PE_CACHE_MTIME:
        return bool(_PE_MAP)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        mapping = data.get("map") or {}
        if not mapping:
            return False
        _PE_CACHE_MTIME = mtime
        _pe_set_map(mapping, data.get("pack") or PREMIUM_EMOJI_PACK)
        logger.info("Premium emoji: loaded %s from cache", len(_PE_MAP))
        return True
    except Exception as e:
        logger.warning("Premium emoji cache read failed: %s", e)
        return False

def _pe_cache_save():
    try:
        path = os.path.join(_pe_cache_dir(), os.path.basename(_PE_CACHE_PATH))
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pack": _PE_PACK, "map": _PE_MAP}, f, ensure_ascii=False)
    except Exception as e:
        logger.warning("Premium emoji cache save failed: %s", e)

async def load_premium_emoji(bot, pack=None):
    global _PE_CACHE_MTIME
    if not PREMIUM_EMOJI_ENABLED:
        _pe_set_map({}, "")
        return False
    if pack:
        _PE_CACHE_MTIME = 0.0
    elif _pe_cache_load(force=True):
        return True
    candidates = []
    for name in [pack, PREMIUM_EMOJI_PACK, "AnimatedEmoji", "AnimatedEmojies", "Emojis"]:
        name = (name or "").strip()
        if name and name not in candidates:
            candidates.append(name)
    for name in candidates:
        try:
            sticker_set = await bot.get_sticker_set(name=name)
        except Exception as e:
            logger.info("Premium emoji pack %s unavailable: %s", name, e)
            continue
        mapping = {}
        for sticker in getattr(sticker_set, "stickers", []) or []:
            cid = getattr(sticker, "custom_emoji_id", None)
            emoji = getattr(sticker, "emoji", None)
            if cid and emoji:
                mapping.setdefault(emoji, cid)
        if mapping:
            _pe_set_map(mapping, name)
            _PE_CACHE_MTIME = 0.0
            _pe_cache_save()
            logger.info("Premium emoji: loaded pack %s (%s emojis)", name, len(mapping))
            return True
    logger.warning("Premium emoji: no pack loaded, fallback to regular emoji")
    if not _PE_MAP:
        _pe_set_map({}, "")
    return False

def premium_emoji_status():
    return {
        "enabled": PREMIUM_EMOJI_ENABLED,
        "active": _PE_ACTIVE,
        "pack": _PE_PACK,
        "count": len(_PE_MAP),
        "max": PREMIUM_EMOJI_MAX,
    }

def _pe_transform(text):
    if not _PE_ACTIVE or not text or not _PE_RE or "<tg-emoji" in text:
        return text
    out = []
    pos = 0
    used = 0
    for match in _PE_RE.finditer(text):
        if used >= PREMIUM_EMOJI_MAX:
            break
        emoji = match.group(0)
        cid = _PE_MAP.get(emoji)
        if not cid:
            continue
        out.append(text[pos:match.start()])
        out.append(f'<tg-emoji emoji-id="{cid}">{emoji}</tg-emoji>')
        pos = match.end()
        used += 1
    if not used:
        return text
    out.append(text[pos:])
    return "".join(out)

def _pe_apply(method):
    if not _PE_ACTIVE:
        return None
    if getattr(method, "parse_mode", None) != "HTML":
        return None
    attr = "text" if hasattr(method, "text") else ("caption" if hasattr(method, "caption") else None)
    if not attr:
        return None
    original = getattr(method, attr)
    upgraded = _pe_transform(original)
    if upgraded == original:
        return None
    setattr(method, attr, upgraded)
    return attr, original

class PremiumEmojiBot(Bot):
    async def __call__(self, method, request_timeout=None):
        backup = _pe_apply(method)
        try:
            return await super().__call__(method, request_timeout=request_timeout)
        except TelegramBadRequest as e:
            if backup and ("emoji" in str(e).lower() or "entity" in str(e).lower()):
                setattr(method, backup[0], backup[1])
                return await super().__call__(method, request_timeout=request_timeout)
            raise

# ═══════════════════════════════════════════════════
# ДАННЫЕ
# ═══════════════════════════════════════════════════

PETS = {
    "dog":         {"emoji":"🐶","name":"Собака",         "atk":12,"def":6,  "hp":100,"rarity":"common",    "ability":"Верный: +10% HP при победе"},
    "cat":         {"emoji":"🐱","name":"Кошка",          "atk":10,"def":10, "hp":95, "rarity":"common",    "ability":"Грация: 15% уклонение"},
    "rabbit":      {"emoji":"🐰","name":"Кролик",         "atk":7, "def":14, "hp":90, "rarity":"common",    "ability":"Прыжок: игнор 20% брони"},
    "hamster":     {"emoji":"🐹","name":"Хомяк",          "atk":9, "def":9,  "hp":85, "rarity":"common",    "ability":"Запасы: +5 монет за бой"},
    "parrot":      {"emoji":"🦜","name":"Попугай",        "atk":8, "def":7,  "hp":80, "rarity":"common",    "ability":"Крик: -5 атаки врагу"},
    "turtle":      {"emoji":"🐢","name":"Черепаха",       "atk":5, "def":20, "hp":120,"rarity":"common",    "ability":"Панцирь: -30% урона"},
    "penguin":     {"emoji":"🐧","name":"Пингвин",        "atk":8, "def":12, "hp":95, "rarity":"common",    "ability":"Лёд: 10% заморозка"},
    "capybara":    {"emoji":"🦔","name":"Капибара",       "atk":6, "def":16, "hp":110,"rarity":"common",    "ability":"Спокойствие: иммун к яду"},
    "fox":         {"emoji":"🦊","name":"Лиса",           "atk":18,"def":13, "hp":105,"rarity":"rare",      "ability":"Хитрость: крит 25%"},
    "wolf":        {"emoji":"🐺","name":"Волк",           "atk":22,"def":10, "hp":110,"rarity":"rare",      "ability":"Стая: +15% урона"},
    "bear":        {"emoji":"🐻","name":"Медведь",        "atk":16,"def":18, "hp":130,"rarity":"rare",      "ability":"Берсерк: x2 атака <30% HP"},
    "tiger":       {"emoji":"🐯","name":"Тигр",           "atk":25,"def":12, "hp":115,"rarity":"rare",      "ability":"Пронзание: игнор 30% брони"},
    "shark":       {"emoji":"🦈","name":"Акула",          "atk":20,"def":15, "hp":125,"rarity":"rare",      "ability":"Кровожадность: +5 атаки/раунд"},
    "eagle":       {"emoji":"🦅","name":"Орёл",           "atk":19,"def":11, "hp":100,"rarity":"rare",      "ability":"Пикирование: первый удар x2"},
    "snow_leopard":{"emoji":"🐆","name":"Снежный леопард","atk":23,"def":14, "hp":108,"rarity":"rare",      "ability":"Невидимость: 20% уклон"},
    "panda":       {"emoji":"🐼","name":"Панда",          "atk":15,"def":20, "hp":120,"rarity":"rare",      "ability":"Бамбук: +5 HP/раунд"},
    "gorilla":     {"emoji":"🦍","name":"Горилла",        "atk":28,"def":15, "hp":135,"rarity":"rare",      "ability":"Ярость: +5 атаки при уроне"},
    "dragon":      {"emoji":"🐲","name":"Дракон",         "atk":35,"def":22, "hp":160,"rarity":"legendary", "ability":"Огонь: ожог 10 урона/раунд"},
    "phoenix":     {"emoji":"🔥","name":"Феникс",         "atk":30,"def":18, "hp":140,"rarity":"legendary", "ability":"Возрождение: воскресает 1 раз"},
    "unicorn":     {"emoji":"🦄","name":"Единорог",       "atk":25,"def":28, "hp":150,"rarity":"legendary", "ability":"Магия: +15 HP/раунд"},
    "kraken":      {"emoji":"🐙","name":"Кракен",         "atk":40,"def":20, "hp":170,"rarity":"legendary", "ability":"Щупальца: 3 удара за раунд"},
    "cerberus":    {"emoji":"🐕","name":"Цербер",         "atk":38,"def":25, "hp":165,"rarity":"legendary", "ability":"3 головы: тройная атака"},
    "void_dragon": {"emoji":"🌑","name":"Тёмный дракон",  "atk":50,"def":30, "hp":200,"rarity":"mythic",   "ability":"Тьма: -50% HP врага"},
    "god_phoenix": {"emoji":"✨","name":"Золотой феникс", "atk":45,"def":35, "hp":190,"rarity":"mythic",   "ability":"Бессмертие: неуязвим 1 раунд"},
    "titan":       {"emoji":"⚡","name":"Титан",          "atk":55,"def":40, "hp":220,"rarity":"mythic",   "ability":"Буря: урон всем + оглушение"},
    "cosmos":      {"emoji":"🌌","name":"Космос",         "atk":60,"def":45, "hp":250,"rarity":"mythic",   "ability":"Чёрная дыра: поглощает атаку"},
    "omega":       {"emoji":"♾️","name":"Омега",          "atk":70,"def":50, "hp":300,"rarity":"mythic",   "ability":"Абсолют: всегда 50% HP врага"},
    "zeus":        {"emoji":"⚡","name":"Зевс",            "atk":100,"def":80,"hp":400,"rarity":"godlike",  "ability":"Удар молнии: мгновенный урон 100 HP"},
    # ── Донатные эксклюзивные питомцы ──────────────────────────────────────
    "celestial_cat":{"emoji":"🌙","name":"Небесная кошка",  "atk":75, "def":60,"hp":320,"rarity":"godlike",  "ability":"Лунный удар: крит 50%, восстановление 20HP/раунд"},
    "void_wolf":    {"emoji":"🌑","name":"Волк бездны",     "atk":90, "def":55,"hp":350,"rarity":"godlike",  "ability":"Поглощение: крадёт 25% HP врага за раунд"},
    "golden_panda": {"emoji":"🐼","name":"Золотая панда",   "atk":60, "def":80,"hp":380,"rarity":"godlike",  "ability":"Защита: блокирует 40% входящего урона"},
    "crystal_fox":  {"emoji":"🦊","name":"Кристальная лиса","atk":85, "def":65,"hp":330,"rarity":"godlike",  "ability":"Осколки: 3 удара по 30 урона + яд 10/раунд"},
    "inferno_tiger":{"emoji":"🐯","name":"Тигр Инферно",    "atk":110,"def":50,"hp":340,"rarity":"godlike",  "ability":"Ярость инферно: первые 3 удара x2 урона"},
    # ── Эксклюзивные питомцы миров ──
# Мир 1 — Зелёный (базовый)
    "spirit_rabbit":  {"emoji":"🌿","name":"Дух леса",        "atk":14,"def":14,"hp":110,"rarity":"rare",      "ability":"Регенерация: +8 HP/раунд в Зелёном мире"},
    # Мир 2 — Пустыня
    "sand_scorpion":  {"emoji":"🦂","name":"Скорпион песка",  "atk":20,"def":8, "hp":95, "rarity":"rare",      "ability":"Яд пустыни: 20 урон/раунд врагу"},
    "dune_sphinx":    {"emoji":"🗿","name":"Дюнный сфинкс",   "atk":18,"def":20,"hp":130,"rarity":"legendary", "ability":"Загадка: 30% шанс пропустить удар"},
    # Мир 3 — Океан
    "sea_turtle":     {"emoji":"🌊","name":"Морская черепаха","atk":8, "def":30,"hp":150,"rarity":"rare",      "ability":"Панцирь волны: -40% урона в Океане"},
    "leviathan":      {"emoji":"🌀","name":"Левиафан",        "atk":45,"def":25,"hp":180,"rarity":"legendary", "ability":"Водоворот: 2 атаки/раунд"},
    # Мир 4 — Вулкан
    "lava_golem":     {"emoji":"🔥","name":"Лавовый голем",   "atk":30,"def":35,"hp":200,"rarity":"legendary", "ability":"Расплавление: игнор 50% брони"},
    "ember_fox":      {"emoji":"🦊","name":"Огненная лиса",   "atk":28,"def":12,"hp":115,"rarity":"rare",      "ability":"Пепел: крит 35% в Вулкане"},
    # Мир 5 — Тундра
    "frost_wolf":     {"emoji":"🐺","name":"Волк мороза",     "atk":32,"def":18,"hp":140,"rarity":"legendary", "ability":"Вьюга: -20 атаки врагу на 3 раунда"},
    "glacial_bear":   {"emoji":"🐻","name":"Ледяной медведь", "atk":26,"def":28,"hp":155,"rarity":"legendary", "ability":"Лёд: замораживает на 1 раунд"},
    # Мир 6 — Тьма
    "shadow_wraith":  {"emoji":"👻","name":"Теневой призрак", "atk":42,"def":15,"hp":130,"rarity":"mythic",    "ability":"Фазовость: 30% уклон от физики"},
    "void_serpent":   {"emoji":"🐍","name":"Бездонная змея",  "atk":50,"def":22,"hp":160,"rarity":"mythic",    "ability":"Пожиратель: крадёт 15 HP/раунд"},
    # Мир 7 — Небо
    "storm_eagle":    {"emoji":"⚡","name":"Штормовой орёл",  "atk":40,"def":20,"hp":145,"rarity":"mythic",    "ability":"Молния: первый удар x3"},
    "cloud_dragon":   {"emoji":"☁️","name":"Облачный дракон", "atk":48,"def":30,"hp":175,"rarity":"mythic",    "ability":"Гроза: AoE урон всей команде"},
    # Мир 8 — Феникс
    "eternal_phoenix":{"emoji":"🌅","name":"Вечный феникс",   "atk":55,"def":35,"hp":200,"rarity":"mythic",   "ability":"Бессмертие x2: воскресает 2 раза"},
    # Мир 9 — Галактика
    "star_construct": {"emoji":"✨","name":"Звёздный конструкт","atk":65,"def":45,"hp":230,"rarity":"mythic",  "ability":"Сверхновая: 50% HP врага мгновенно"},
    # Мир 10 — Омега
    "null_entity":    {"emoji":"❌","name":"Нуль-сущность",   "atk":80,"def":60,"hp":280,"rarity":"godlike",  "ability":"Аннигиляция: уничтожает 1 щит"},
    # Секретный мир Ω (Shadow Realm)
    "chaos_beast":    {"emoji":"🌪️","name":"Зверь хаоса",    "atk":90,"def":70,"hp":320,"rarity":"godlike",  "ability":"Хаос: случайный x2-x5 урон"},
    # Секретный мир Кузница
    "iron_titan":     {"emoji":"⚙️","name":"Железный титан", "atk":85,"def":90,"hp":350,"rarity":"godlike",  "ability":"Неуязвимость: -60% урон на 2 раунда"},
    # Секретный мир Начало
    "primordial":     {"emoji":"🌌","name":"Перворождённый",  "atk":120,"def":100,"hp":500,"rarity":"godlike","ability":"Абсолютное начало: сбрасывает HP врага до 1"},
    # ── Сезон 2: Эра Титанов ──────────────────────────────────────────────
    "crystal_beetle": {"emoji":"🪲","name":"Кристальный жук",  "atk":14,"def":24,"hp":110,"rarity":"rare",      "ability":"Кристальный панцирь: -25% входящего урона"},
    "storm_wolf":     {"emoji":"🐺","name":"Штормовой волк",   "atk":34,"def":18,"hp":135,"rarity":"legendary", "ability":"Гроза: 20% шанс двойного удара"},
    "thunderbird":    {"emoji":"🦅","name":"Громовая птица",   "atk":38,"def":16,"hp":125,"rarity":"legendary", "ability":"Молния: первый удар x2"},
    "magma_golem":    {"emoji":"🪨","name":"Магмовый голем",   "atk":28,"def":38,"hp":210,"rarity":"legendary", "ability":"Расплав: игнор 40% брони"},
    "sand_wyrm":      {"emoji":"🐍","name":"Песчаный змей",    "atk":36,"def":22,"hp":160,"rarity":"legendary", "ability":"Песчаная буря: -15 атаки врагу"},
    "sky_serpent":    {"emoji":"🐍","name":"Небесный змей",    "atk":40,"def":24,"hp":150,"rarity":"legendary", "ability":"Вихрь: 2 удара за раунд"},
    "star_golem":     {"emoji":"🌟","name":"Звёздный голем",   "atk":30,"def":45,"hp":240,"rarity":"legendary", "ability":"Звёздный щит: блок 35% урона"},
    "frost_queen":    {"emoji":"❄️","name":"Королева льда",    "atk":42,"def":40,"hp":190,"rarity":"mythic",    "ability":"Ледяной трон: 30% заморозка"},
    "sea_dragon":     {"emoji":"🐉","name":"Морской дракон",   "atk":52,"def":32,"hp":210,"rarity":"mythic",    "ability":"Прилив: +15 HP/раунд"},
    "solar_lion":     {"emoji":"🦁","name":"Солнечный лев",    "atk":55,"def":35,"hp":220,"rarity":"mythic",    "ability":"Солнечная грива: +20% ATK"},
    "lunar_fox":      {"emoji":"🦊","name":"Лунная лиса",      "atk":48,"def":42,"hp":195,"rarity":"mythic",    "ability":"Лунная иллюзия: 25% уклон"},
    "ice_phoenix":    {"emoji":"🧊","name":"Ледяной феникс",   "atk":50,"def":38,"hp":205,"rarity":"mythic",    "ability":"Ледяное возрождение: воскресает 1 раз"},
    "void_reaper":    {"emoji":"💀","name":"Жнец Бездны",      "atk":58,"def":28,"hp":180,"rarity":"mythic",    "ability":"Жатва: крадёт 20 HP/раунд"},
    "abyss_hydra":    {"emoji":"🐙","name":"Гидра Бездны",     "atk":60,"def":35,"hp":230,"rarity":"mythic",    "ability":"Три головы: 3 удара за раунд"},
    "nebula_whale":   {"emoji":"🐋","name":"Небулярный кит",   "atk":45,"def":50,"hp":260,"rarity":"mythic",    "ability":"Гравитация: -30% входящего урона"},
    "dread_knight":   {"emoji":"🛡️","name":"Рыцарь Ужаса",    "atk":62,"def":40,"hp":210,"rarity":"mythic",    "ability":"Страх: -20 ATK врагу"},
    "titan_king":     {"emoji":"👑","name":"Король Титанов",   "atk":95,"def":70,"hp":380,"rarity":"godlike",   "ability":"Корона титанов: +30% ко всем статам"},
    "chronos":        {"emoji":"⏳","name":"Хронос",            "atk":85,"def":75,"hp":360,"rarity":"godlike",   "ability":"Остановка времени: пропуск хода врага"},
    "celestial_dragon":{"emoji":"🐉","name":"Небесный дракон",  "atk":100,"def":65,"hp":400,"rarity":"godlike",  "ability":"Небесный огонь: 50 урона/раунд"},
    "golden_griffin": {"emoji":"🦅","name":"Золотой грифон",   "atk":90,"def":80,"hp":370,"rarity":"godlike",   "ability":"Золотое перо: +25% монет"},
    "astral_titan":   {"emoji":"✨","name":"Астральный титан",  "atk":105,"def":60,"hp":390,"rarity":"godlike",  "ability":"Астральный удар: игнор защиты"},
    "void_titan":     {"emoji":"🌑","name":"Титан Бездны",     "atk":110,"def":55,"hp":410,"rarity":"godlike",   "ability":"Бездна: крадёт 10% HP"},
    "inferno_titan":  {"emoji":"🔥","name":"Инфернальный титан","atk":115,"def":50,"hp":420,"rarity":"godlike",  "ability":"Инферно: ожог 30/раунд"},
    "frost_titan":    {"emoji":"❄️","name":"Ледяной титан",    "atk":100,"def":75,"hp":430,"rarity":"godlike",   "ability":"Абсолютный ноль: заморозка"},
    "storm_titan":    {"emoji":"⚡","name":"Штормовой титан",  "atk":108,"def":68,"hp":405,"rarity":"godlike",   "ability":"Шторм: 3 удара за раунд"},
    "primordial_wyrm":{"emoji":"🌌","name":"Первородный змей", "atk":120,"def":80,"hp":500,"rarity":"godlike",   "ability":"Начало: сбрасывает HP врага до 10%"},
}

EVOLUTION_STAGES = {
    1: {"name": "Обычный",       "icon": "",   "min_level": 1,  "mult": 1.00, "cost": 0},
    2: {"name": "Пробуждённый",  "icon": "⭐", "min_level": 10, "mult": 1.15, "cost": 50},
    3: {"name": "Легендарный",   "icon": "🌟", "min_level": 25, "mult": 1.35, "cost": 150},
    4: {"name": "Мифический",    "icon": "👑", "min_level": 50, "mult": 1.60, "cost": 400},
}

def pet_evo_stage(pet):
    try:
        return max(1, min(max(EVOLUTION_STAGES), int((pet or {}).get("evolution") or 1)))
    except Exception:
        return 1

def pet_evo_mult(pet):
    return EVOLUTION_STAGES[pet_evo_stage(pet)]["mult"]

PET_SELL_BASE = {
    "common": 250,
    "rare": 850,
    "legendary": 2500,
    "mythic": 2500,
    "godlike": 20000,
}

PET_EGG_FLOOR = {
    "common": 250,
    "rare": 850,
    "legendary": 2500,
    "mythic": 2500,
    "godlike": 10000,
}

def pet_sell_value(pet):
    p = PETS.get(pet.get("pet_type"), PETS["dog"])
    base = PET_SELL_BASE.get(p.get("rarity"), 400)
    level = max(1, int(pet.get("level") or 1))
    value = base * (1 + (level - 1) * 0.12) * pet_evo_mult(pet)
    if pet.get("is_shiny"):
        value *= 1.6
    skills = sum(int(pet.get(k) or 0) for k in ("skill_atk", "skill_def", "skill_hp", "skill_crit", "skill_regen"))
    value += skills * 120 + int(pet.get("wins") or 0) * 20
    return max(50, int(round(value)))

def pet_quick_sell_price(pet):
    p = PETS.get(pet.get("pet_type"), PETS["dog"])
    raw = int(pet_sell_value(pet) * 0.75)
    floor = PET_EGG_FLOOR.get(p.get("rarity"))
    if floor:
        level = max(1, int(pet.get("level") or 1))
        cap = int(floor * (0.75 + 0.004 * min(level - 1, 50)))
        raw = min(raw, cap)
    return max(10, raw)

WHEEL_COOLDOWN = 8 * 3600
WHEEL_PRIZES = [
    {"kind": "coins",    "amount": 300,   "weight": 24, "label": "300 монет"},
    {"kind": "coins",    "amount": 700,   "weight": 20, "label": "700 монет"},
    {"kind": "coins",    "amount": 1500,  "weight": 14, "label": "1 500 монет"},
    {"kind": "coins",    "amount": 4000,  "weight": 7,  "label": "4 000 монет"},
    {"kind": "crystals", "amount": 5,     "weight": 12, "label": "5 кристаллов"},
    {"kind": "crystals", "amount": 15,    "weight": 6,  "label": "15 кристаллов"},
    {"kind": "tokens",   "amount": 15,    "weight": 8,  "label": "15 токенов сезона"},
    {"kind": "item",     "item": "food",  "amount": 2,  "weight": 5,  "label": "2 порции еды"},
    {"kind": "xp",       "amount": 150,   "weight": 6,  "label": "150 XP питомцу"},
    {"kind": "coins",    "amount": 25000, "weight": 1,  "label": "ДЖЕКПОТ 25 000 монет", "jackpot": True},
    {"kind": "crystals", "amount": 100,   "weight": 1,  "label": "ДЖЕКПОТ 100 кристаллов", "jackpot": True},
    {"kind": "pet",      "pet": "dragon","weight": 1,  "label": "Дракон"},
    {"kind": "pet",      "pet": "phoenix","weight": 1, "label": "Феникс"},
]

# ═══════════════════════════════════════════════════
# ЛОКАЦИИ
# ═══════════════════════════════════════════════════

LOCATIONS = {
    "forest": {
        "emoji": "🌲", "name": "Лесная чаща",
        "desc": "Густой волшебный лес с древними существами.",
        "pets": ["fox","wolf","bear","rabbit","turtle","capybara","snow_leopard"],
        "egg": "common_egg", "explore_reward": (40, 100),
    },
    "ocean": {
        "emoji": "🌊", "name": "Глубокий океан",
        "desc": "Таинственные глубины полные морских чудовищ.",
        "pets": ["shark","kraken","penguin","capybara"],
        "egg": "rare_egg", "explore_reward": (80, 180),
    },
    "volcano": {
        "emoji": "🌋", "name": "Огненный вулкан",
        "desc": "Жерло вулкана, где живут существа из пламени.",
        "pets": ["dragon","phoenix","tiger","eagle"],
        "egg": "legendary_egg", "explore_reward": (150, 300),
    },
    "tundra": {
        "emoji": "❄️", "name": "Вечная тундра",
        "desc": "Ледяные просторы с существами холода.",
        "pets": ["penguin","snow_leopard","wolf","capybara"],
        "egg": "rare_egg", "explore_reward": (70, 160),
    },
    "sky": {
        "emoji": "☁️", "name": "Небесные острова",
        "desc": "Парящие острова — обитель небесных созданий.",
        "pets": ["eagle","parrot","phoenix","unicorn","god_phoenix"],
        "egg": "legendary_egg", "explore_reward": (200, 400),
    },
    "abyss": {
        "emoji": "🌑", "name": "Тёмная бездна",
        "desc": "Место где рождаются мифические существа.",
        "pets": ["void_dragon","cerberus","titan","cosmos","omega"],
        "egg": "dark_egg", "explore_reward": (300, 600),
    },
}


# ═══════════════════════════════════════════════════
# МИРЫ — НОВАЯ СИСТЕМА v2.0
# ═══════════════════════════════════════════════════

WORLDS = {

    # ══════════════════════════════════════════════════════════════════════════
    # МИР 1 — ЗЕЛЁНЫЙ МИР
    # Механика: СТАБИЛЬНОСТЬ — безопасный старт, небольшой бонус питомцам-травоядным
    # ══════════════════════════════════════════════════════════════════════════
    1: {
        "name": "🌿 Зелёный мир",
        "desc": "Уютные луга — начало твоего пути. Здесь всегда можно отдохнуть.",
        "unlock_cost": 0,
        "bg": "🌿",

        # ── Яйца ──────────────────────────────────────────────────────────────
        "eggs": {
            "w1_common": {
                "emoji": "🥚", "name": "Яйцо луга", "price": 100, "stars": 0,
                "pool": {"dog": 30, "cat": 25, "rabbit": 20, "hamster": 15, "parrot": 10},
            },
            "w1_rare": {
                "emoji": "🪺", "name": "Яйцо рощи", "price": 400, "stars": 0,
                "pool": {"fox": 35, "wolf": 35, "bear": 20, "turtle": 10},
            },
            "w1_exclusive": {
                "emoji": "🌿", "name": "Зелёное яйцо", "price": 900, "stars": 0,
                # Эксклюзив: только в Мире 1
                "pool": {"spirit_rabbit": 70, "fox": 20, "wolf": 10},
                "exclusive": True,  # пометка — не продаётся в других мирах
            },
        },

        # ── Магазин ───────────────────────────────────────────────────────────
        "shop_weapons": ["stick", "dagger", "sword"],
        "shop_armor":   ["cloth", "leather", "chainmail"],

        # ── Доход от работы ───────────────────────────────────────────────────
        "work_reward": (30, 80),

        # ── Мировая валюта ────────────────────────────────────────────────────
        # Трава — мягкая стартовая валюта
        "currency": {
            "id": "grass",
            "emoji": "🌱",
            "name": "Трава",
            "desc": "Собирается в Зелёном мире при работе и событиях.",
            # Магазин за мировую валюту
            "shop": {
                "spirit_rabbit_egg": {"name": "🌿 Зелёное яйцо (скидка)", "cost": 50,   "action": "egg", "egg_id": "w1_exclusive"},
                "grass_elixir":      {"name": "🌱 Эликсир роста (+30 XP)", "cost": 20,   "action": "xp",  "value": 30},
                "grass_heal":        {"name": "💚 Зелёное лечение (50 HP)", "cost": 15,   "action": "heal","value": 50},
            },
        },

        # ── Механика мира ─────────────────────────────────────────────────────
        "mechanic": {
            "type": "stability",
            # Травоядные питомцы (rabbit, hamster, capybara, turtle) получают бонус в этом мире
            "synergy_pets": ["rabbit", "hamster", "capybara", "turtle", "spirit_rabbit"],
            "synergy_bonus": {
                "atk": 0,
                "def": 5,
                "hp": 20,
                "coins_mult": 1.15,  # +15% монет при работе
            },
            # Нет риска — идеально для новичков
            "risk_enabled": False,
            "desc_mechanic": "Травоядные питомцы получают +5 защиты, +20 HP и +15% монет.",
        },

        # ── Случайные события ─────────────────────────────────────────────────
        "events": [
            {
                "id": "w1_mushroom", "name": "🍄 Грибная поляна", "emoji": "🍄",
                "desc": "Ты нашёл поляну с магическими грибами!",
                "prob": 0.20,
                "coins": (50, 150), "world_currency": (5, 15), "xp": (20, 50),
                "egg_id": None, "pet_id": None, "risk": False,
            },
            {
                "id": "w1_spring", "name": "💧 Волшебный родник", "emoji": "💧",
                "desc": "Твой питомец попил из волшебного источника — HP восстановлено!",
                "prob": 0.20,
                "coins": (20, 60), "world_currency": (3, 8), "xp": (10, 30),
                "heal": 50, "egg_id": None, "pet_id": None, "risk": False,
            },
            {
                "id": "w1_lost_egg", "name": "🥚 Брошенное яйцо", "emoji": "🥚",
                "desc": "В кустах ты нашёл чьё-то забытое яйцо!",
                "prob": 0.10,
                "coins": (0, 0), "world_currency": (0, 0), "xp": (30, 60),
                "egg_id": "w1_common", "pet_id": None, "risk": False,
            },
            {
                "id": "w1_nothing", "name": "🌾 Тихий день", "emoji": "🌾",
                "desc": "Ничего особенного, зато спокойно...",
                "prob": 0.35,
                "coins": (10, 40), "world_currency": (1, 5), "xp": (5, 15),
                "egg_id": None, "pet_id": None, "risk": False,
            },
            {
                "id": "w1_rare_drop", "name": "🌿 Дух леса появился!", "emoji": "✨",
                "desc": "Редчайший момент — дух леса одобрил тебя!",
                "prob": 0.01,  # 1% — казино-момент
                "coins": (200, 500), "world_currency": (30, 60), "xp": (100, 200),
                "egg_id": None, "pet_id": "spirit_rabbit", "risk": False,
            },
            {
                "id": "w1_thief", "name": "🐀 Вор-бурундук", "emoji": "🐀",
                "desc": "Бурундук-воришка утащил немного монет!",
                "prob": 0.14,
                "coins": (-80, -20), "world_currency": (0, 0), "xp": (5, 10),
                "egg_id": None, "pet_id": None, "risk": True,
            },
        ],

        # ── Босс мира ─────────────────────────────────────────────────────────
        "boss": {
            "name": "Лесной Страж", "emoji": "🌲",
            "desc": "Древний дух леса, охраняющий его тайны.",
            "hp": 800, "atk": 25, "def": 15,
            "abilities": [
                "🍃 Шквал листьев: -10 атаки игрока на 2 раунда",
                "🌿 Корни: пропустить 1 атаку игрока (30% шанс)",
                "💚 Регенерация: +40 HP каждые 3 раунда",
            ],
            "phase2_hp_pct": 0.35,
            "phase2_atk_mult": 1.5,
            "phase2_desc": "Страж разъярён! Корни опутывают поле боя!",
            "reward_coins": (500, 1200),
            "reward_world_currency": (30, 80),
            "reward_pet": "spirit_rabbit",
            "reward_pet_chance": 0.05,   # 5%
            "cooldown_hours": 12,
        },

        # ── Награда при открытии ──────────────────────────────────────────────
        "unlock_reward": {
            "coins": 0,           # мир 1 — старт, нет бонуса
            "egg_id": "w1_common",
            "world_currency": 0,
            "desc": "Добро пожаловать в мир приключений!",
        },
    },


    # ══════════════════════════════════════════════════════════════════════════
    # МИР 2 — ПЕСЧАНАЯ ПУСТЫНЯ
    # Механика: РИСК/НАГРАДА — мираж может удвоить или обнулить доход
    # ══════════════════════════════════════════════════════════════════════════
    2: {
        "name": "🏜️ Песчаная пустыня",
        "desc": "Раскалённые пески скрывают редких существ... и смертельные опасности.",
        "unlock_cost": 2000,
        "bg": "🏜️",

        "eggs": {
            "w2_common": {
                "emoji": "🥚", "name": "Яйцо песка", "price": 250, "stars": 0,
                "pool": {"hamster": 30, "parrot": 25, "capybara": 25, "penguin": 20},
            },
            "w2_rare": {
                "emoji": "🪺", "name": "Яйцо оазиса", "price": 800, "stars": 0,
                "pool": {"tiger": 30, "eagle": 30, "snow_leopard": 25, "gorilla": 15},
            },
            "w2_exclusive": {
                "emoji": "🦂", "name": "Яйцо скорпиона", "price": 1800, "stars": 0,
                "pool": {"sand_scorpion": 60, "dune_sphinx": 30, "eagle": 10},
                "exclusive": True,
            },
        },

        "shop_weapons": ["axe", "bow", "spear"],
        "shop_armor":   ["chainmail", "plate"],
        "work_reward": (70, 160),

        "currency": {
            "id": "sandstone",
            "emoji": "🪨",
            "name": "Песчаник",
            "desc": "Добывается в пустыне. Используется у местных торговцев.",
            "shop": {
                "sand_egg_deal": {"name": "🦂 Яйцо скорпиона (скидка)", "cost": 80,  "action": "egg",  "egg_id": "w2_exclusive"},
                "mirage_boost":  {"name": "🌵 Мираж-ускоритель (x2 работа)", "cost": 50, "action": "work_mult", "value": 2.0, "duration_min": 30},
                "sand_heal":     {"name": "💛 Зелье пустыни (70 HP)",   "cost": 25,  "action": "heal", "value": 70},
                "oasis_key":     {"name": "🗝️ Ключ оазиса (редкое яйцо)","cost": 200, "action": "egg",  "egg_id": "w2_rare"},
            },
        },

        # МЕХАНИКА: МИРАЖ
        # При работе с вероятностью 25% активируется мираж:
        #   — 50% шанс: доход x2 (удача!)
        #   — 50% шанс: доход потерян (обман!)
        # Питомцы-хищники получают бонус к атаке.
        "mechanic": {
            "type": "mirage_risk",
            "mirage_chance": 0.25,       # 25% шанс миража при работе
            "mirage_good_mult": 2.0,     # удача → x2 дохода
            "mirage_bad_mult": 0.0,      # провал → x0 (потеря)
            "synergy_pets": ["tiger", "eagle", "sand_scorpion", "dune_sphinx"],
            "synergy_bonus": {"atk": 8, "def": 0, "hp": 0, "coins_mult": 1.2},
            "risk_enabled": True,
            "desc_mechanic": (
                "⚠️ Мираж: 25% шанс при работе. Удача → x2 монет. Провал → 0 монет.\n"
                "🦂 Хищники получают +8 атаки и +20% монет."
            ),
        },

        "events": [
            {
                "id": "w2_oasis", "name": "🌴 Оазис!", "emoji": "🌴",
                "desc": "Ты нашёл настоящий оазис с сокровищами!",
                "prob": 0.15,
                "coins": (150, 400), "world_currency": (15, 40), "xp": (40, 100),
                "egg_id": "w2_rare", "pet_id": None, "risk": False,
            },
            {
                "id": "w2_sandstorm", "name": "🌪️ Песчаная буря!", "emoji": "🌪️",
                "desc": "Буря разметала все запасы! Ты потерял монеты и получил урон.",
                "prob": 0.18,
                "coins": (-300, -80), "world_currency": (-10, 0), "xp": (0, 0),
                "damage": 30,
                "egg_id": None, "pet_id": None, "risk": True,
            },
            {
                "id": "w2_merchant", "name": "🐪 Торговец-кочевник", "emoji": "🐪",
                "desc": "Странствующий торговец предлагает сделку!",
                "prob": 0.20,
                "coins": (80, 200), "world_currency": (10, 25), "xp": (20, 50),
                "egg_id": None, "pet_id": None, "risk": False,
            },
            {
                "id": "w2_sphinx_riddle", "name": "🗿 Загадка сфинкса", "emoji": "🗿",
                "desc": "Сфинкс задал загадку... Ты ответил правильно!",
                "prob": 0.12,
                "coins": (200, 500), "world_currency": (20, 50), "xp": (80, 150),
                "egg_id": "w2_exclusive", "pet_id": None, "risk": False,
            },
            {
                "id": "w2_scorpion_rare", "name": "🦂 Золотой скорпион!", "emoji": "✨",
                "desc": "Легендарная встреча — золотой скорпион!",
                "prob": 0.01,
                "coins": (500, 1000), "world_currency": (50, 100), "xp": (200, 400),
                "egg_id": None, "pet_id": "sand_scorpion", "risk": False,
            },
            {
                "id": "w2_nothing", "name": "☀️ Жаркий день", "emoji": "☀️",
                "desc": "Раскалённое солнце, ни одного события...",
                "prob": 0.34,
                "coins": (30, 80), "world_currency": (3, 10), "xp": (10, 25),
                "egg_id": None, "pet_id": None, "risk": False,
            },
        ],

        "boss": {
            "name": "Дюнный Сфинкс-Владыка", "emoji": "🗿",
            "desc": "Вечный страж пустыни, хранитель её тайн и проклятий.",
            "hp": 1800, "atk": 45, "def": 25,
            "abilities": [
                "🌪️ Песчаный вихрь: урон + 50% шанс пропустить атаку",
                "🗿 Каменный взгляд: -15 атаки и -10 защиты игрока",
                "☀️ Солнечный удар: 60 прямого урона (игнор брони)",
            ],
            "phase2_hp_pct": 0.40,
            "phase2_atk_mult": 1.8,
            "phase2_desc": "Сфинкс превращается в песчаного монстра!",
            "reward_coins": (1500, 3500),
            "reward_world_currency": (80, 180),
            "reward_pet": "dune_sphinx",
            "reward_pet_chance": 0.08,
            "cooldown_hours": 18,
        },

        "unlock_reward": {
            "coins": 1000,
            "egg_id": "w2_common",
            "world_currency": 25,
            "desc": "Пустыня открылась тебе! Получи стартовый запас.",
        },
    },


    # ══════════════════════════════════════════════════════════════════════════
    # МИР 3 — ГЛУБОКИЙ ОКЕАН
    # Механика: ИССЛЕДОВАНИЕ ГЛУБИН — чем глубже ныряешь, тем больше награда,
    #           но растёт шанс встретить опасность (риск/награда с выбором)
    # ══════════════════════════════════════════════════════════════════════════
    3: {
        "name": "🌊 Глубокий океан",
        "desc": "Бескрайние воды скрывают невиданные сокровища... и чудовищ.",
        "unlock_cost": 8000,
        "bg": "🌊",

        "eggs": {
            "w3_common": {
                "emoji": "🥚", "name": "Яйцо прилива", "price": 500, "stars": 0,
                "pool": {"penguin": 30, "capybara": 25, "shark": 25, "turtle": 20},
            },
            "w3_rare": {
                "emoji": "🪺", "name": "Яйцо глубин", "price": 1500, "stars": 0,
                "pool": {"shark": 35, "kraken": 30, "panda": 20, "gorilla": 15},
            },
            "w3_exclusive": {
                "emoji": "🌀", "name": "Абиссальное яйцо", "price": 3500, "stars": 0,
                "pool": {"leviathan": 50, "sea_turtle": 35, "kraken": 15},
                "exclusive": True,
            },
        },

        "shop_weapons": ["magic_staff", "fire_sword"],
        "shop_armor":   ["magic_robe", "fire_armor"],
        "work_reward": (140, 280),

        "currency": {
            "id": "pearl",
            "emoji": "🦪",
            "name": "Жемчуг",
            "desc": "Редкие жемчужины, добытые из морских глубин.",
            "shop": {
                "abyss_egg":    {"name": "🌀 Абиссальное яйцо",           "cost": 150, "action": "egg",  "egg_id": "w3_exclusive"},
                "depth_charge": {"name": "💣 Глубинная бомба (x3 работа)", "cost": 100, "action": "work_mult", "value": 3.0, "duration_min": 20},
                "ocean_heal":   {"name": "💙 Морское зелье (100 HP)",      "cost": 40,  "action": "heal", "value": 100},
                "pearl_armor":  {"name": "🦪 Жемчужная броня (+20 def)",   "cost": 200, "action": "temp_def", "value": 20, "duration_min": 60},
            },
        },

        # МЕХАНИКА: НЫРЯНИЕ
        # Игрок выбирает глубину перед работой (мелко / средне / глубоко).
        # Глубже → больше жемчуга и монет, выше шанс плохого события.
        # Морские питомцы снижают риск на своей глубине.
        "mechanic": {
            "type": "deep_dive",
            "dive_levels": {
                "shallow": {"coins_mult": 1.0, "pearl_mult": 1.0, "risk_chance": 0.05, "label": "Мелководье 🏖️"},
                "medium":  {"coins_mult": 1.8, "pearl_mult": 2.0, "risk_chance": 0.20, "label": "Средняя глубина 🌊"},
                "deep":    {"coins_mult": 3.5, "pearl_mult": 4.0, "risk_chance": 0.45, "label": "Бездна 🌀"},
            },
            "synergy_pets": ["shark", "kraken", "penguin", "turtle", "sea_turtle", "leviathan"],
            "synergy_bonus": {"atk": 0, "def": 10, "hp": 30, "risk_reduction": 0.15},
            "risk_enabled": True,
            "desc_mechanic": (
                "🤿 Ныряние: выбирай глубину перед каждой работой.\n"
                "Бездна даёт x3.5 монет и x4 жемчуга, но 45% риск события.\n"
                "🌊 Морские питомцы снижают риск на 15%."
            ),
        },

        "events": [
            {
                "id": "w3_treasure", "name": "💎 Затонувший корабль!", "emoji": "🚢",
                "desc": "Ты нашёл затонувший корабль с сокровищами!",
                "prob": 0.12,
                "coins": (300, 700), "world_currency": (20, 60), "xp": (80, 160),
                "egg_id": "w3_rare", "pet_id": None, "risk": False,
            },
            {
                "id": "w3_whirlpool", "name": "🌀 Водоворот!", "emoji": "🌀",
                "desc": "Водоворот затянул тебя — ты потерял ресурсы и получил урон!",
                "prob": 0.15,
                "coins": (-400, -100), "world_currency": (-15, -5), "xp": (0, 0),
                "damage": 40,
                "egg_id": None, "pet_id": None, "risk": True,
            },
            {
                "id": "w3_mermaid", "name": "🧜 Русалка-помощница", "emoji": "🧜",
                "desc": "Добрая русалка помогла тебе и вылечила питомца!",
                "prob": 0.18,
                "coins": (100, 250), "world_currency": (10, 30), "xp": (30, 80),
                "heal": 80,
                "egg_id": None, "pet_id": None, "risk": False,
            },
            {
                "id": "w3_leviathan_rare", "name": "🌊 Левиафан замечен!", "emoji": "✨",
                "desc": "Великий Левиафан прошёл мимо, оставив дар!",
                "prob": 0.01,
                "coins": (1000, 2000), "world_currency": (80, 150), "xp": (300, 600),
                "egg_id": None, "pet_id": "leviathan", "risk": False,
            },
            {
                "id": "w3_coral", "name": "🪸 Коралловый риф", "emoji": "🪸",
                "desc": "Красивый риф скрывал жемчуг!",
                "prob": 0.20,
                "coins": (80, 200), "world_currency": (15, 40), "xp": (25, 60),
                "egg_id": None, "pet_id": None, "risk": False,
            },
            {
                "id": "w3_nothing", "name": "🌊 Спокойные воды", "emoji": "🌊",
                "desc": "Ничего необычного. Но океан красив.",
                "prob": 0.34,
                "coins": (50, 130), "world_currency": (5, 15), "xp": (15, 40),
                "egg_id": None, "pet_id": None, "risk": False,
            },
        ],

        "boss": {
            "name": "Кракен Бездонный", "emoji": "🦑",
            "desc": "Владыка морских глубин. Его щупальца охватывают целые острова.",
            "hp": 3500, "atk": 70, "def": 35,
            "abilities": [
                "🦑 Щупальца: атакует 2 раза за раунд при HP > 50%",
                "💧 Чернильный туман: 40% шанс промаха игрока",
                "🌊 Волна бездны: 80 прямого урона (каждые 4 раунда)",
                "🌀 Фаза 2 — Погружение: уходит под воду, +50 защиты на 2 раунда",
            ],
            "phase2_hp_pct": 0.45,
            "phase2_atk_mult": 2.0,
            "phase2_desc": "Кракен уходит в бездну и возвращается удвоенным!",
            "reward_coins": (4000, 9000),
            "reward_world_currency": (150, 350),
            "reward_pet": "leviathan",
            "reward_pet_chance": 0.06,
            "cooldown_hours": 24,
        },

        "unlock_reward": {
            "coins": 3000,
            "egg_id": "w3_common",
            "world_currency": 50,
            "desc": "Океан открыл свои глубины! Первый жемчуг твой.",
        },
    },


    # ══════════════════════════════════════════════════════════════════════════
    # МИР 4 — ОГНЕННЫЙ ВУЛКАН
    # Механика: ПЕРЕГРЕВ — чем дольше работаешь подряд, тем выше бонус,
    #           но питомец теряет HP (менеджмент ресурсов)
    # ══════════════════════════════════════════════════════════════════════════
    4: {
        "name": "🌋 Огненный вулкан",
        "desc": "Жерло вулкана — здесь куётся сталь и воля. Слабых оно поглощает.",
        "unlock_cost": 25000,
        "bg": "🌋",

        "eggs": {
            "w4_common": {
                "emoji": "🥚", "name": "Яйцо лавы", "price": 900, "stars": 0,
                "pool": {"tiger": 30, "eagle": 25, "gorilla": 25, "bear": 20},
            },
            "w4_rare": {
                "emoji": "🪺", "name": "Яйцо пламени", "price": 2500, "stars": 0,
                "pool": {"dragon": 35, "phoenix": 30, "cerberus": 20, "void_dragon": 15},
            },
            "w4_exclusive": {
                "emoji": "🔥", "name": "Магматическое яйцо", "price": 6000, "stars": 0,
                "pool": {"lava_golem": 50, "ember_fox": 35, "dragon": 15},
                "exclusive": True,
            },
        },

        "shop_weapons": ["ice_blade", "thunder_axe", "shadow_dagger"],
        "shop_armor":   ["dragon_scale"],
        "work_reward": (250, 500),

        "currency": {
            "id": "magma",
            "emoji": "🌋",
            "name": "Магма",
            "desc": "Огненная субстанция вулкана. Только жаростойкие питомцы могут её добывать.",
            "shop": {
                "magma_egg":    {"name": "🔥 Магматическое яйцо",          "cost": 300, "action": "egg",       "egg_id": "w4_exclusive"},
                "fire_shield":  {"name": "🛡️ Огненный щит (иммун к ожогу)","cost": 120, "action": "immunity",  "value": "burn", "duration_min": 60},
                "forge_boost":  {"name": "⚒️ Кузнечный усилитель (+40 ATK)","cost": 180, "action": "temp_atk",  "value": 40,    "duration_min": 45},
                "lava_heal":    {"name": "🔥 Лавовое зелье (150 HP)",       "cost": 80,  "action": "heal",       "value": 150},
            },
        },

        # МЕХАНИКА: ПЕРЕГРЕВ (Heat Stack)
        # Каждая работа добавляет 1 стак «жара» (макс 5).
        # 1 стак → +10% монет, 3 стака → +40%, 5 стаков → x2 монет + x2 магмы.
        # При 5 стаках питомец получает 20 урона (перегрев) — нужно лечить.
        # Огненные питомцы не получают урона от перегрева.
        "mechanic": {
            "type": "heat_stack",
            "max_stacks": 5,
            "stack_bonuses": {
                1: {"coins_mult": 1.10, "magma_mult": 1.10},
                2: {"coins_mult": 1.20, "magma_mult": 1.30},
                3: {"coins_mult": 1.40, "magma_mult": 1.60},
                4: {"coins_mult": 1.70, "magma_mult": 2.00},
                5: {"coins_mult": 2.00, "magma_mult": 2.50, "damage_to_pet": 20},
            },
            "synergy_pets": ["dragon", "phoenix", "lava_golem", "ember_fox"],
            "synergy_bonus": {"atk": 12, "def": 5, "hp": 20, "no_heat_damage": True},
            "risk_enabled": True,
            "desc_mechanic": (
                "🔥 Перегрев: каждая работа даёт стак жара (макс 5).\n"
                "x5 → x2 монет + x2.5 магмы, но -20 HP питомцу.\n"
                "🐉 Огненные питомцы не получают урон от перегрева."
            ),
        },

        "events": [
            {
                "id": "w4_eruption", "name": "🌋 Извержение!", "emoji": "🌋",
                "desc": "Вулкан извергается — тебе удалось собрать магму!",
                "prob": 0.12,
                "coins": (400, 900), "world_currency": (40, 100), "xp": (100, 200),
                "egg_id": "w4_rare", "pet_id": None, "risk": False,
            },
            {
                "id": "w4_lava_flood", "name": "🌊 Лавовый поток!", "emoji": "🌊",
                "desc": "Поток лавы настиг тебя! Питомец получил серьёзный урон.",
                "prob": 0.15,
                "coins": (-200, 0), "world_currency": (-20, 0), "xp": (0, 0),
                "damage": 60,
                "egg_id": None, "pet_id": None, "risk": True,
            },
            {
                "id": "w4_forge", "name": "⚒️ Древняя кузница", "emoji": "⚒️",
                "desc": "Ты нашёл кузницу огненных великанов и улучшил оружие!",
                "prob": 0.15,
                "coins": (200, 500), "world_currency": (20, 50), "xp": (60, 130),
                "temp_atk_bonus": 20, "duration_min": 120,
                "egg_id": None, "pet_id": None, "risk": False,
            },
            {
                "id": "w4_phoenix_feather", "name": "🪶 Перо феникса", "emoji": "🪶",
                "desc": "Феникс пролетел мимо и уронил перо — питомец воспрял!",
                "prob": 0.15,
                "coins": (150, 350), "world_currency": (15, 40), "xp": (40, 90),
                "heal": 100,
                "egg_id": None, "pet_id": None, "risk": False,
            },
            {
                "id": "w4_lava_golem_rare", "name": "🔥 Голем пробудился!", "emoji": "✨",
                "desc": "Огромный лавовый голем склонился перед тобой!",
                "prob": 0.01,
                "coins": (1500, 3000), "world_currency": (100, 200), "xp": (400, 800),
                "egg_id": None, "pet_id": "lava_golem", "risk": False,
            },
            {
                "id": "w4_nothing", "name": "🔥 Жар вулкана", "emoji": "🔥",
                "desc": "Жара невыносима, но ты держишься.",
                "prob": 0.42,
                "coins": (100, 250), "world_currency": (10, 25), "xp": (30, 70),
                "egg_id": None, "pet_id": None, "risk": False,
            },
        ],

        "boss": {
            "name": "Игнис — Владыка Огня", "emoji": "🔥",
            "desc": "Первородный огненный дух. Его гнев плавит горы.",
            "hp": 6000, "atk": 110, "def": 55,
            "abilities": [
                "🔥 Огненный шторм: 90 урона всем + ожог 15/раунд",
                "🌋 Лавовая броня: +30 защиты на 3 раунда (каждые 5 раундов)",
                "💥 Метеоритный дождь: 3 удара по 40 урона",
                "🐉 Фаза 2 — Дракон Игниса: превращается в дракона, атака x2",
            ],
            "phase2_hp_pct": 0.40,
            "phase2_atk_mult": 2.2,
            "phase2_desc": "Игнис трансформируется! Земля горит!",
            "reward_coins": (12000, 28000),
            "reward_world_currency": (300, 600),
            "reward_pet": "lava_golem",
            "reward_pet_chance": 0.07,
            "cooldown_hours": 36,
        },

        "unlock_reward": {
            "coins": 8000,
            "egg_id": "w4_common",
            "world_currency": 80,
            "desc": "Вулкан принял тебя! Получи дары огня.",
        },
    },


    # ══════════════════════════════════════════════════════════════════════════
    # МИР 5 — ВЕЧНАЯ ТУНДРА
    # Механика: ЗАМОРОЗКА — случайные «ледяные недели» дают x3 дохода,
    #           но без защиты питомец замерзает и теряет эффективность
    # ══════════════════════════════════════════════════════════════════════════
    5: {
        "name": "❄️ Вечная тундра",
        "desc": "Бескрайние льды хранят древние тайны. Холод убивает слабых.",
        "unlock_cost": 70000,
        "bg": "❄️",

        "eggs": {
            "w5_common": {
                "emoji": "🥚", "name": "Яйцо льда", "price": 1500, "stars": 0,
                "pool": {"snow_leopard": 35, "wolf": 30, "penguin": 25, "capybara": 10},
            },
            "w5_rare": {
                "emoji": "🪺", "name": "Яйцо метели", "price": 4000, "stars": 0,
                "pool": {"unicorn": 35, "kraken": 30, "phoenix": 25, "void_dragon": 10},
            },
            "w5_exclusive": {
                "emoji": "❄️", "name": "Ледяное яйцо", "price": 9000, "stars": 0,
                "pool": {"frost_wolf": 45, "glacial_bear": 40, "snow_leopard": 15},
                "exclusive": True,
            },
        },

        "shop_weapons": ["dragon_lance", "excalibur"],
        "shop_armor":   ["void_armor"],
        "work_reward": (450, 900),

        "currency": {
            "id": "frost_shard",
            "emoji": "🧊",
            "name": "Осколок льда",
            "desc": "Магические кристаллы тундры. Сохраняют холод тысячелетий.",
            "shop": {
                "ice_egg":      {"name": "❄️ Ледяное яйцо",                   "cost": 400, "action": "egg",     "egg_id": "w5_exclusive"},
                "cryo_shield":  {"name": "🧊 Крио-щит (иммун к заморозке)",    "cost": 200, "action": "immunity","value": "freeze", "duration_min": 90},
                "blizzard_atk": {"name": "🌨️ Вьюга-усилитель (+60 ATK)",        "cost": 300, "action": "temp_atk","value": 60, "duration_min": 60},
                "tundra_heal":  {"name": "🩵 Ледяное зелье (200 HP)",           "cost": 120, "action": "heal",   "value": 200},
            },
        },

        # МЕХАНИКА: ЛЕДЯНАЯ НЕДЕЛЯ (Ice Rush)
        # Каждые ~6 работ случайно активируется «ледяная волна»:
        # — Доход x3 на следующие 3 работы
        # — Но без «тёплого» питомца питомец получает -30% урон и -20% дохода (заморозка)
        # Холодные питомцы иммунны к заморозке и дают +25% дохода.
        "mechanic": {
            "type": "ice_rush",
            "rush_trigger_chance": 0.18,  # каждая работа: 18% шанс начать Ice Rush
            "rush_mult": 3.0,
            "rush_duration_works": 3,
            "freeze_penalty": {"coins_mult": 0.70, "atk_mult": 0.80},
            "warm_pets_immune": ["penguin", "snow_leopard", "wolf", "frost_wolf", "glacial_bear", "capybara"],
            "synergy_pets": ["snow_leopard", "wolf", "frost_wolf", "glacial_bear", "penguin"],
            "synergy_bonus": {"atk": 15, "def": 10, "hp": 40, "coins_mult": 1.25},
            "risk_enabled": True,
            "desc_mechanic": (
                "🌨️ Ледяная волна: 18% шанс при работе. Даёт x3 дохода на 3 работы.\n"
                "Без холодного питомца: -30% монет и -20% атаки (заморозка).\n"
                "❄️ Зимние питомцы: +25% монет + иммунитет к заморозке."
            ),
        },

        "events": [
            {
                "id": "w5_ice_cave", "name": "🏔️ Ледяная пещера!", "emoji": "🏔️",
                "desc": "Ты нашёл пещеру с древними ледяными кристаллами!",
                "prob": 0.13,
                "coins": (600, 1400), "world_currency": (50, 120), "xp": (150, 300),
                "egg_id": "w5_rare", "pet_id": None, "risk": False,
            },
            {
                "id": "w5_blizzard", "name": "❄️ Смертельная метель!", "emoji": "🌨️",
                "desc": "Метель застала врасплох — питомец обморожен!",
                "prob": 0.15,
                "coins": (-500, -150), "world_currency": (-25, 0), "xp": (0, 0),
                "damage": 50, "freeze_duration": 1,
                "egg_id": None, "pet_id": None, "risk": True,
            },
            {
                "id": "w5_polar_bear", "name": "🐻‍❄️ Белый медведь-союзник", "emoji": "🐻‍❄️",
                "desc": "Полярный медведь проводил тебя к тайнику!",
                "prob": 0.18,
                "coins": (300, 700), "world_currency": (30, 70), "xp": (80, 160),
                "egg_id": None, "pet_id": None, "risk": False,
            },
            {
                "id": "w5_aurora", "name": "🌌 Северное сияние", "emoji": "🌌",
                "desc": "Северное сияние зарядило питомца магией!",
                "prob": 0.15,
                "coins": (200, 500), "world_currency": (20, 50), "xp": (100, 200),
                "xp_bonus": True, "heal": 60,
                "egg_id": None, "pet_id": None, "risk": False,
            },
            {
                "id": "w5_frost_wolf_rare", "name": "🐺 Альфа мороза!", "emoji": "✨",
                "desc": "Вожак стаи снежных волков признал тебя!",
                "prob": 0.01,
                "coins": (2000, 4000), "world_currency": (150, 300), "xp": (500, 1000),
                "egg_id": None, "pet_id": "frost_wolf", "risk": False,
            },
            {
                "id": "w5_nothing", "name": "🌬️ Пронизывающий ветер", "emoji": "🌬️",
                "desc": "Холодный ветер, пустота. Но ты не сдаёшься.",
                "prob": 0.38,
                "coins": (150, 400), "world_currency": (15, 40), "xp": (40, 90),
                "egg_id": None, "pet_id": None, "risk": False,
            },
        ],

        "boss": {
            "name": "Кристальный Колосс", "emoji": "🧊",
            "desc": "Ледяной великан, рождённый в сердце вечной зимы.",
            "hp": 11000, "atk": 160, "def": 80,
            "abilities": [
                "🧊 Ледяной плен: заморозка на 2 раунда (40% шанс)",
                "❄️ Ледяное дыхание: 120 урона + -20 атаки игрока",
                "🏔️ Ледяной щит: поглощает 200 урона (раз в бой)",
                "💀 Фаза 2 — Абсолютный ноль: урон сквозь броню, иммун к огню",
            ],
            "phase2_hp_pct": 0.35,
            "phase2_atk_mult": 2.5,
            "phase2_desc": "Колосс достигает абсолютного нуля! Всё замерзает!",
            "reward_coins": (30000, 70000),
            "reward_world_currency": (500, 1000),
            "reward_pet": "glacial_bear",
            "reward_pet_chance": 0.07,
            "cooldown_hours": 48,
        },

        "unlock_reward": {
            "coins": 20000,
            "egg_id": "w5_common",
            "world_currency": 120,
            "desc": "Тундра открылась тебе! Прими дары вечного льда.",
        },
    },


    # ══════════════════════════════════════════════════════════════════════════
    # МИР 6 — ТЁМНАЯ БЕЗДНА
    # Механика: ЖЕРТВОПРИНОШЕНИЕ — игрок жертвует монетами ради шанса
    #           получить x5 или редкого питомца (высокий риск/высокая награда)
    # ══════════════════════════════════════════════════════════════════════════
    6: {
        "name": "🌑 Тёмная бездна",
        "desc": "Место, где рождаются боги и умирают герои. Ты всё ещё здесь?",
        "unlock_cost": 180000,
        "bg": "🌑",

        "eggs": {
            "w6_common": {
                "emoji": "🥚", "name": "Яйцо тьмы", "price": 3000, "stars": 0,
                "pool": {"void_dragon": 35, "cerberus": 30, "kraken": 20, "gorilla": 15},
            },
            "w6_rare": {
                "emoji": "🪺", "name": "Тёмное яйцо", "price": 8000, "stars": 0,
                "pool": {"void_dragon": 40, "god_phoenix": 30, "titan": 20, "cosmos": 10},
            },
            "w6_exclusive": {
                "emoji": "🌑", "name": "Яйцо бездны", "price": 18000, "stars": 0,
                "pool": {"shadow_wraith": 45, "void_serpent": 40, "void_dragon": 15},
                "exclusive": True,
            },
        },

        "shop_weapons": ["void_blade", "titan_hammer"],
        "shop_armor":   ["titan_shell"],
        "work_reward": (800, 1600),

        "currency": {
            "id": "dark_essence",
            "emoji": "🌑",
            "name": "Тёмная эссенция",
            "desc": "Сгусток тьмы, собранный в глубинах бездны. Крайне опасен.",
            "shop": {
                "void_egg":     {"name": "🌑 Яйцо бездны",                    "cost": 800,  "action": "egg",     "egg_id": "w6_exclusive"},
                "dark_ritual":  {"name": "🩸 Ритуал тьмы (x5 шанс питомца)", "cost": 500,  "action": "ritual",  "pet_chance_mult": 5.0},
                "void_restore": {"name": "💜 Восстановление тьмой (300 HP)", "cost": 200,  "action": "heal",    "value": 300},
                "darkness_atk": {"name": "🌑 Тёмная ярость (+80 ATK)",        "cost": 400,  "action": "temp_atk","value": 80, "duration_min": 45},
            },
        },

        # МЕХАНИКА: ЖЕРТВОПРИНОШЕНИЕ ТЬМЕ
        # Перед работой игрок может пожертвовать монеты:
        # — без жертвы: обычный доход
        # — жертва 10%: x2 монеты + x2 эссенции
        # — жертва 25%: x4 монеты + шанс питомца 5%
        # — жертва 50%: x7 монеты OR полная потеря (50/50) + шанс питомца 10%
        # Тёмные питомцы снижают потери и увеличивают шанс успеха.
        "mechanic": {
            "type": "dark_sacrifice",
            "sacrifice_tiers": {
                "none":   {"cost_pct": 0,    "coins_mult": 1.0, "ess_mult": 1.0, "pet_chance": 0,    "fail_chance": 0},
                "minor":  {"cost_pct": 0.10, "coins_mult": 2.0, "ess_mult": 2.0, "pet_chance": 0,    "fail_chance": 0},
                "major":  {"cost_pct": 0.25, "coins_mult": 4.0, "ess_mult": 3.0, "pet_chance": 0.05, "fail_chance": 0},
                "grand":  {"cost_pct": 0.50, "coins_mult": 7.0, "ess_mult": 5.0, "pet_chance": 0.10, "fail_chance": 0.50},
            },
            "synergy_pets": ["void_dragon", "cerberus", "shadow_wraith", "void_serpent"],
            "synergy_bonus": {"atk": 20, "def": 10, "hp": 50, "fail_reduction": 0.20},
            "risk_enabled": True,
            "desc_mechanic": (
                "🩸 Жертва тьме: выбери уровень перед работой.\n"
                "Большая жертва (50%) → x7 монет OR полная потеря (50/50).\n"
                "🌑 Тёмные питомцы снижают шанс провала на 20%."
            ),
        },

        "events": [
            {
                "id": "w6_rift", "name": "🌀 Разлом тьмы!", "emoji": "🌀",
                "desc": "Разлом выбросил тебя в другое измерение — с трофеями!",
                "prob": 0.10,
                "coins": (1500, 4000), "world_currency": (100, 250), "xp": (300, 600),
                "egg_id": "w6_rare", "pet_id": None, "risk": False,
            },
            {
                "id": "w6_void_consumption", "name": "💀 Бездна поглотила!", "emoji": "💀",
                "desc": "Тьма забрала часть твоей силы и монет!",
                "prob": 0.18,
                "coins": (-1000, -300), "world_currency": (-50, -10), "xp": (0, 0),
                "damage": 80,
                "egg_id": None, "pet_id": None, "risk": True,
            },
            {
                "id": "w6_dark_altar", "name": "🗿 Тёмный алтарь", "emoji": "🗿",
                "desc": "Древний алтарь тьмы принял твою кровь и дал силу!",
                "prob": 0.15,
                "coins": (500, 1200), "world_currency": (60, 140), "xp": (200, 400),
                "temp_atk_bonus": 30, "duration_min": 180,
                "egg_id": None, "pet_id": None, "risk": False,
            },
            {
                "id": "w6_shadow_wraith_rare", "name": "👻 Теневой призрак!", "emoji": "✨",
                "desc": "Призрак из бездны покорился твоей воле!",
                "prob": 0.01,
                "coins": (3000, 6000), "world_currency": (200, 400), "xp": (800, 1500),
                "egg_id": None, "pet_id": "shadow_wraith", "risk": False,
            },
            {
                "id": "w6_dark_blessing", "name": "💜 Благословение тьмы", "emoji": "💜",
                "desc": "Тёмные силы дали тебе свою благодать.",
                "prob": 0.18,
                "coins": (400, 900), "world_currency": (50, 110), "xp": (120, 250),
                "egg_id": None, "pet_id": None, "risk": False,
            },
            {
                "id": "w6_nothing", "name": "🌑 Абсолютная тьма", "emoji": "🌑",
                "desc": "Тишина и тьма. Твой питомец нервничает.",
                "prob": 0.38,
                "coins": (200, 600), "world_currency": (20, 60), "xp": (60, 130),
                "egg_id": None, "pet_id": None, "risk": False,
            },
        ],

        "boss": {
            "name": "Некрон — Владыка Бездны", "emoji": "💀",
            "desc": "Он существовал до создания мира. Его взгляд убивает.",
            "hp": 22000, "atk": 250, "def": 120,
            "abilities": [
                "💀 Взгляд смерти: мгновенно снимает 40% текущего HP питомца",
                "🌑 Аура бездны: -30 атаки и -20 защиты игрока",
                "👻 Призыв теней: вызывает 2 миньона (по 500 HP каждый)",
                "🩸 Жертвенный ритуал: лечится на 500 HP каждые 5 раундов",
                "💜 Фаза 2 — Форма небытия: все атаки игрока 30% шанс промаха",
            ],
            "phase2_hp_pct": 0.30,
            "phase2_atk_mult": 3.0,
            "phase2_desc": "Некрон принял форму небытия! Тьма непроглядная!",
            "reward_coins": (80000, 180000),
            "reward_world_currency": (1000, 2500),
            "reward_pet": "void_serpent",
            "reward_pet_chance": 0.08,
            "cooldown_hours": 72,
        },

        "unlock_reward": {
            "coins": 60000,
            "egg_id": "w6_common",
            "world_currency": 300,
            "desc": "Ты вошёл в бездну и выжил. Тьма приветствует тебя.",
        },
    },


    # ══════════════════════════════════════════════════════════════════════════
    # МИР 7 — НЕБЕСНЫЕ ОСТРОВА
    # Механика: ПОЛЁТ — питомцы «летают» между островами, каждый остров
    #           даёт разный тип ресурса. Нужно балансировать между ними.
    # ══════════════════════════════════════════════════════════════════════════
    7: {
        "name": "☁️ Небесные острова",
        "desc": "Парящие острова среди облаков. Каждый хранит уникальный дар.",
        "unlock_cost": 450000,
        "bg": "☁️",

        "eggs": {
            "w7_common": {
                "emoji": "🥚", "name": "Яйцо неба", "price": 6000, "stars": 0,
                "pool": {"god_phoenix": 35, "titan": 30, "cosmos": 25, "eagle": 10},
            },
            "w7_rare": {
                "emoji": "🪺", "name": "Небесное яйцо", "price": 18000, "stars": 0,
                "pool": {"titan": 40, "cosmos": 30, "omega": 20, "god_phoenix": 10},
            },
            "w7_exclusive": {
                "emoji": "⚡", "name": "Штормовое яйцо", "price": 40000, "stars": 0,
                "pool": {"storm_eagle": 45, "cloud_dragon": 40, "god_phoenix": 15},
                "exclusive": True,
            },
        },

        "shop_weapons": ["star_sword"],
        "shop_armor":   ["star_armor"],
        "work_reward": (1500, 3000),

        "currency": {
            "id": "stardust",
            "emoji": "✨",
            "name": "Звёздная пыль",
            "desc": "Рассыпана по небесным островам. Используется небесными торговцами.",
            "shop": {
                "storm_egg":    {"name": "⚡ Штормовое яйцо",               "cost": 1500, "action": "egg",     "egg_id": "w7_exclusive"},
                "sky_blessing": {"name": "☁️ Небесное благословение (x2 XP)","cost": 800,  "action": "xp_mult","value": 2.0, "duration_min": 60},
                "thunder_heal": {"name": "⚡ Грозовое зелье (400 HP)",        "cost": 400,  "action": "heal",   "value": 400},
                "star_essence": {"name": "🌟 Эссенция звезды (→ 5000 монет)","cost": 2000, "action": "coins",  "value": 5000},
            },
        },

        # МЕХАНИКА: ОСТРОВА (Island Hopping)
        # Перед каждой работой игрок выбирает остров:
        # — Облачный: монеты x1.5, звёздная пыль x2
        # — Штормовой: монеты x3, XP x2, риск 30% потерять всё
        # — Золотой: монеты x5, нет звёздной пыли, КД 2 работы
        # Летающие питомцы получают +1 попытку в день.
        "mechanic": {
            "type": "island_hopping",
            "islands": {
                "cloud":   {"coins_mult": 1.5, "dust_mult": 2.0, "xp_mult": 1.0, "risk": 0.0,  "cooldown_works": 0, "label": "☁️ Облачный остров"},
                "storm":   {"coins_mult": 3.0, "dust_mult": 1.0, "xp_mult": 2.0, "risk": 0.30, "cooldown_works": 0, "label": "⚡ Штормовой остров"},
                "golden":  {"coins_mult": 5.0, "dust_mult": 0.0, "xp_mult": 1.0, "risk": 0.0,  "cooldown_works": 2, "label": "🌟 Золотой остров"},
            },
            "synergy_pets": ["eagle", "phoenix", "god_phoenix", "storm_eagle", "cloud_dragon"],
            "synergy_bonus": {"atk": 15, "def": 15, "hp": 60, "extra_island_try": True},
            "risk_enabled": True,
            "desc_mechanic": (
                "🏝️ Острова: выбирай перед работой.\n"
                "Золотой остров: x5 монет, но КД 2 работы.\n"
                "⚡ Летающие питомцы получают +1 попытку острова в день."
            ),
        },

        "events": [
            {
                "id": "w7_golden_cloud", "name": "🌟 Золотое облако!", "emoji": "🌟",
                "desc": "Золотое облако осыпало тебя монетами!",
                "prob": 0.10,
                "coins": (3000, 7000), "world_currency": (200, 500), "xp": (400, 800),
                "egg_id": "w7_rare", "pet_id": None, "risk": False,
            },
            {
                "id": "w7_storm", "name": "⚡ Гроза сбила с курса!", "emoji": "⚡",
                "desc": "Молния ударила в питомца! Урон и потеря ресурсов.",
                "prob": 0.15,
                "coins": (-1500, -500), "world_currency": (-80, -20), "xp": (0, 0),
                "damage": 100,
                "egg_id": None, "pet_id": None, "risk": True,
            },
            {
                "id": "w7_celestial_altar", "name": "🗿 Небесный алтарь", "emoji": "🗿",
                "desc": "Небесные духи наградили тебя звёздной пылью!",
                "prob": 0.18,
                "coins": (500, 1200), "world_currency": (150, 350), "xp": (200, 400),
                "egg_id": None, "pet_id": None, "risk": False,
            },
            {
                "id": "w7_storm_eagle_rare", "name": "⚡ Штормовой орёл!", "emoji": "✨",
                "desc": "Штормовой орёл признал тебя повелителем бурь!",
                "prob": 0.01,
                "coins": (5000, 10000), "world_currency": (500, 1000), "xp": (1500, 3000),
                "egg_id": None, "pet_id": "storm_eagle", "risk": False,
            },
            {
                "id": "w7_rainbow", "name": "🌈 Радужный мост", "emoji": "🌈",
                "desc": "Радужный мост открыл путь к тайнику!",
                "prob": 0.18,
                "coins": (1000, 2500), "world_currency": (100, 250), "xp": (300, 600),
                "egg_id": None, "pet_id": None, "risk": False,
            },
            {
                "id": "w7_nothing", "name": "☁️ Между облаками", "emoji": "☁️",
                "desc": "Тихий полёт между облаками. Спокойствие и высота.",
                "prob": 0.38,
                "coins": (500, 1200), "world_currency": (50, 130), "xp": (100, 220),
                "egg_id": None, "pet_id": None, "risk": False,
            },
        ],

        "boss": {
            "name": "Аэрос — Бог Небес", "emoji": "⚡",
            "desc": "Повелитель ветров и молний. Он правит небесами с начала времён.",
            "hp": 55000, "atk": 380, "def": 180,
            "abilities": [
                "⚡ Небесная молния: 200 урона + оглушение на 1 раунд",
                "🌪️ Торнадо: -50% атаки игрока на 3 раунда",
                "☁️ Облачная броня: +100 защиты на 2 раунда (раз в 6 раундов)",
                "🌩️ Грозовой призыв: вызывает 3 молнии (по 100 урона)",
                "✨ Фаза 2 — Форма бури: атакует трижды за раунд",
            ],
            "phase2_hp_pct": 0.35,
            "phase2_atk_mult": 2.8,
            "phase2_desc": "Аэрос превращается в живую бурю!",
            "reward_coins": (200000, 450000),
            "reward_world_currency": (3000, 7000),
            "reward_pet": "cloud_dragon",
            "reward_pet_chance": 0.06,
            "cooldown_hours": 96,
        },

        "unlock_reward": {
            "coins": 150000,
            "egg_id": "w7_common",
            "world_currency": 800,
            "desc": "Небеса открылись! Звёздная пыль падает к твоим ногам.",
        },
    },


    # ══════════════════════════════════════════════════════════════════════════
    # МИР 8 — МИР ФЕНИКСА
    # Механика: ВОЗРОЖДЕНИЕ — при гибели питомца в бою он «возрождается»
    #           с бонусом +20% к стату, но только 3 раза. Стимул к битвам.
    # ══════════════════════════════════════════════════════════════════════════
    8: {
        "name": "🔥 Мир феникса",
        "desc": "Царство вечного огня и возрождения. Смерть здесь — не конец.",
        "unlock_cost": 1200000,
        "bg": "🔥",

        "eggs": {
            "w8_common": {
                "emoji": "🥚", "name": "Яйцо феникса", "price": 12000, "stars": 0,
                "pool": {"cosmos": 40, "omega": 30, "titan": 20, "god_phoenix": 10},
            },
            "w8_rare": {
                "emoji": "🪺", "name": "Яйцо возрождения", "price": 40000, "stars": 0,
                "pool": {"omega": 50, "cosmos": 30, "titan": 20},
            },
            "w8_exclusive": {
                "emoji": "🌅", "name": "Яйцо вечного феникса", "price": 90000, "stars": 0,
                "pool": {"eternal_phoenix": 70, "god_phoenix": 20, "phoenix": 10},
                "exclusive": True,
            },
        },

        "shop_weapons": ["omega_blade"],
        "shop_armor":   ["omega_shield"],
        "work_reward": (3000, 6000),

        "currency": {
            "id": "phoenix_ash",
            "emoji": "🪶",
            "name": "Пепел феникса",
            "desc": "Магический пепел, остающийся после возрождения феникса.",
            "shop": {
                "phoenix_egg":    {"name": "🌅 Яйцо вечного феникса",         "cost": 4000,  "action": "egg",    "egg_id": "w8_exclusive"},
                "rebirth_token":  {"name": "🪶 Жетон возрождения (+1 rebirth)","cost": 2000,  "action": "rebirth","value": 1},
                "ash_heal":       {"name": "🔥 Пепел-лечение (полное HP)",     "cost": 800,   "action": "full_heal"},
                "phoenix_power":  {"name": "💫 Сила феникса (+100 ATK, 30мин)","cost": 1500,  "action": "temp_atk","value": 100, "duration_min": 30},
            },
        },

        # МЕХАНИКА: ВОЗРОЖДЕНИЕ (Phoenix Rebirth)
        # В этом мире питомцы могут «умереть» в бою 3 раза, каждый раз:
        # — Возрождаясь с +20% к ATK, DEF или HP (случайно)
        # — После 3-го возрождения — финальная форма: все статы +60%
        # — Чем больше возрождений у питомца, тем выше доход в этом мире
        "mechanic": {
            "type": "phoenix_rebirth",
            "max_rebirths": 3,
            "rebirth_stat_bonus_pct": 0.20,   # +20% к случайному стату
            "rebirth_income_bonus": {
                0: 1.0,
                1: 1.3,
                2: 1.7,
                3: 2.5,   # финальная форма
            },
            "synergy_pets": ["phoenix", "god_phoenix", "eternal_phoenix"],
            "synergy_bonus": {"atk": 25, "def": 20, "hp": 80, "extra_rebirth": 1},
            "risk_enabled": False,
            "desc_mechanic": (
                "🔥 Возрождение: питомец возрождается до 3 раз после гибели.\n"
                "Каждое возрождение: +20% к ATK/DEF/HP.\n"
                "3 возрождения → x2.5 дохода в мире феникса."
            ),
        },

        "events": [
            {
                "id": "w8_rebirth_flame", "name": "🌅 Пламя возрождения!", "emoji": "🌅",
                "desc": "Священный огонь вернул питомца к жизни с усиленными статами!",
                "prob": 0.12,
                "coins": (5000, 12000), "world_currency": (300, 700), "xp": (600, 1200),
                "rebirth_bonus": True,
                "egg_id": "w8_rare", "pet_id": None, "risk": False,
            },
            {
                "id": "w8_eternal_phoenix_rare", "name": "🌅 Вечный феникс!", "emoji": "✨",
                "desc": "Вечный феникс избрал тебя своим хозяином!",
                "prob": 0.01,
                "coins": (10000, 20000), "world_currency": (1000, 2000), "xp": (3000, 6000),
                "egg_id": None, "pet_id": "eternal_phoenix", "risk": False,
            },
            {
                "id": "w8_inferno", "name": "💥 Адское пламя!", "emoji": "💥",
                "desc": "Всепожирающий огонь — питомец потерял одно возрождение!",
                "prob": 0.10,
                "coins": (-2000, 0), "world_currency": (-100, 0), "xp": (0, 0),
                "damage": 120, "lose_rebirth": True,
                "egg_id": None, "pet_id": None, "risk": True,
            },
            {
                "id": "w8_sacred_flame", "name": "🕯️ Священное пламя", "emoji": "🕯️",
                "desc": "Священный огонь очистил питомца и вернул жетон возрождения!",
                "prob": 0.18,
                "coins": (2000, 5000), "world_currency": (150, 350), "xp": (400, 800),
                "gain_rebirth": True,
                "egg_id": None, "pet_id": None, "risk": False,
            },
            {
                "id": "w8_nothing", "name": "🔥 Вечный огонь", "emoji": "🔥",
                "desc": "Огонь горит вечно. Ты греешься у его тепла.",
                "prob": 0.59,
                "coins": (1000, 3000), "world_currency": (80, 200), "xp": (200, 500),
                "egg_id": None, "pet_id": None, "risk": False,
            },
        ],

        "boss": {
            "name": "Архи-феникс Аваэль", "emoji": "🌅",
            "desc": "Первородный феникс. Возродился миллиард раз. Непобедим?",
            "hp": 130000, "atk": 600, "def": 280,
            "abilities": [
                "🌅 Возрождение: воскрешается с 50% HP (единожды)",
                "🔥 Солнечный взрыв: 400 урона + ожог 50/раунд",
                "💫 Пепел феникса: лечится 1000 HP каждые 4 раунда",
                "🪶 Перья-бритвы: 5 ударов по 100 урона",
                "✨ Фаза 2 — Истинная форма: невозможно промахнуться по игроку",
            ],
            "phase2_hp_pct": 0.50,  # воскрешается при 50%
            "phase2_atk_mult": 3.5,
            "phase2_desc": "Аваэль воскресает в истинной форме!",
            "reward_coins": (500000, 1200000),
            "reward_world_currency": (8000, 18000),
            "reward_pet": "eternal_phoenix",
            "reward_pet_chance": 0.05,
            "cooldown_hours": 120,
        },

        "unlock_reward": {
            "coins": 400000,
            "egg_id": "w8_common",
            "world_currency": 2000,
            "desc": "Огонь феникса принял тебя! Смерть больше не страшна.",
        },
    },


    # ══════════════════════════════════════════════════════════════════════════
    # МИР 9 — ГАЛАКТИКА
    # Механика: КВАЗАР — каждые 10 работ генерируется «квазарный взрыв»,
    #           мгновенно дающий х10 дохода. Игрок ждёт и копит стаки.
    # ══════════════════════════════════════════════════════════════════════════
    9: {
        "name": "🌌 Галактика",
        "desc": "За пределами звёзд — бесконечная мощь и тайны вселенной.",
        "unlock_cost": 3500000,
        "bg": "🌌",

        "eggs": {
            "w9_common": {
                "emoji": "🥚", "name": "Звёздное яйцо", "price": 25000, "stars": 0,
                "pool": {"omega": 55, "cosmos": 30, "titan": 15},
            },
            "w9_rare": {
                "emoji": "🪺", "name": "Яйцо галактики", "price": 80000, "stars": 0,
                "pool": {"omega": 70, "cosmos": 30},
            },
            "w9_exclusive": {
                "emoji": "✨", "name": "Яйцо конструкта", "price": 200000, "stars": 0,
                "pool": {"star_construct": 75, "cosmos": 20, "omega": 5},
                "exclusive": True,
            },
        },

        "shop_weapons": ["omega_blade"],
        "shop_armor":   ["omega_shield"],
        "work_reward": (7000, 14000),

        "currency": {
            "id": "dark_matter",
            "emoji": "🌌",
            "name": "Тёмная материя",
            "desc": "Субстанция, из которой соткана вселенная. Невероятно ценна.",
            "shop": {
                "construct_egg": {"name": "✨ Яйцо конструкта",                   "cost": 10000, "action": "egg",    "egg_id": "w9_exclusive"},
                "quasar_charge": {"name": "💥 Заряд квазара (следующий x15)",       "cost": 8000,  "action": "quasar_boost", "value": 15.0},
                "matter_heal":   {"name": "🌌 Тёмное исцеление (полное HP + щит)", "cost": 3000,  "action": "full_heal_shield"},
                "void_gate":     {"name": "🌀 Врата пустоты (→ 50000 монет)",       "cost": 15000, "action": "coins",  "value": 50000},
            },
        },

        # МЕХАНИКА: КВАЗАРНЫЙ ВЗРЫВ
        # Каждая работа заряжает счётчик. На 10-й работе — взрыв квазара:
        # доход x10 + тёмная материя x5 + случайный редкий дроп.
        # Космические питомцы ускоряют зарядку (каждая работа считается за 1.5).
        "mechanic": {
            "type": "quasar_burst",
            "burst_every_n_works": 10,
            "burst_mult": 10.0,
            "burst_matter_mult": 5.0,
            "burst_rare_drop_chance": 0.15,  # шанс редкого питомца при взрыве
            "synergy_pets": ["cosmos", "omega", "star_construct", "titan"],
            "synergy_bonus": {"atk": 30, "def": 25, "hp": 100, "work_count_mult": 1.5},
            "risk_enabled": False,
            "desc_mechanic": (
                "💥 Квазар: каждые 10 работ — взрыв квазара! x10 монет + x5 материи.\n"
                "15% шанс редкого дропа при взрыве.\n"
                "🌌 Космические питомцы: каждая работа считается за 1.5."
            ),
        },

        "events": [
            {
                "id": "w9_supernova", "name": "💥 Сверхновая!", "emoji": "💥",
                "desc": "Ближайшая звезда взорвалась и осыпала тебя сокровищами!",
                "prob": 0.08,
                "coins": (20000, 50000), "world_currency": (1000, 2500), "xp": (2000, 4000),
                "egg_id": "w9_rare", "pet_id": None, "risk": False,
            },
            {
                "id": "w9_black_hole", "name": "🌀 Чёрная дыра!", "emoji": "🌀",
                "desc": "Чёрная дыра поглотила часть твоих ресурсов!",
                "prob": 0.12,
                "coins": (-5000, -2000), "world_currency": (-300, -100), "xp": (0, 0),
                "damage": 150,
                "egg_id": None, "pet_id": None, "risk": True,
            },
            {
                "id": "w9_construct_rare", "name": "✨ Звёздный конструкт!", "emoji": "✨",
                "desc": "Конструкт из звёзд принял тебя как создателя!",
                "prob": 0.01,
                "coins": (30000, 60000), "world_currency": (3000, 6000), "xp": (10000, 20000),
                "egg_id": None, "pet_id": "star_construct", "risk": False,
            },
            {
                "id": "w9_nebula", "name": "🌟 Туманность", "emoji": "🌟",
                "desc": "Ты пролетел сквозь туманность — питомец усилился!",
                "prob": 0.18,
                "coins": (5000, 12000), "world_currency": (500, 1200), "xp": (1000, 2000),
                "temp_all_bonus": 15, "duration_min": 90,
                "egg_id": None, "pet_id": None, "risk": False,
            },
            {
                "id": "w9_nothing", "name": "🌌 Глубокий космос", "emoji": "🌌",
                "desc": "Тишина вселенной. Бесконечность рядом.",
                "prob": 0.61,
                "coins": (3000, 8000), "world_currency": (300, 800), "xp": (500, 1200),
                "egg_id": None, "pet_id": None, "risk": False,
            },
        ],

        "boss": {
            "name": "Синтезатор Галактик", "emoji": "🌌",
            "desc": "Живой компьютер, управляющий законами вселенной.",
            "hp": 400000, "atk": 1200, "def": 600,
            "abilities": [
                "🌌 Гравитационный коллапс: -70% атаки игрока на 3 раунда",
                "💥 Квазарная бомба: 800 урона (игнор брони)",
                "✨ Синтез: создаёт щит на 3000 HP",
                "🌀 Разрушение: снимает все баффы игрока",
                "🔮 Фаза 2 — Сингулярность: все атаки игрока делятся на 2",
            ],
            "phase2_hp_pct": 0.30,
            "phase2_atk_mult": 4.0,
            "phase2_desc": "Синтезатор достигает сингулярности!",
            "reward_coins": (2000000, 5000000),
            "reward_world_currency": (30000, 70000),
            "reward_pet": "star_construct",
            "reward_pet_chance": 0.04,
            "cooldown_hours": 168,  # 1 неделя
        },

        "unlock_reward": {
            "coins": 1000000,
            "egg_id": "w9_common",
            "world_currency": 5000,
            "desc": "Ты достиг галактики! Вселенная раскрыла свои тайны.",
        },
    },


    # ══════════════════════════════════════════════════════════════════════════
    # МИР 10 — МИР ОМЕГИ
    # Механика: АБСОЛЮТ — нет случайностей. Каждая работа даёт фиксированный
    #           максимум + бонус от уровня питомца (x питомец). Prestige-мир.
    # ══════════════════════════════════════════════════════════════════════════
    10: {
        "name": "♾️ Мир Омеги",
        "desc": "Абсолютный предел. Мир богов. Лишь достойные ступают сюда.",
        "unlock_cost": 10000000,
        "bg": "♾️",

        "eggs": {
            "w10_common": {
                "emoji": "🥚", "name": "Яйцо Омеги", "price": 50000, "stars": 0,
                "pool": {"omega": 80, "cosmos": 20},
            },
            "w10_rare": {
                "emoji": "🪺", "name": "Яйцо Абсолюта", "price": 200000, "stars": 0,
                "pool": {"omega": 100},
            },
            "w10_exclusive": {
                "emoji": "❌", "name": "Яйцо Нуля", "price": 500000, "stars": 0,
                "pool": {"null_entity": 80, "omega": 20},
                "exclusive": True,
            },
        },

        "shop_weapons": ["omega_blade"],
        "shop_armor":   ["omega_shield"],
        "work_reward": (15000, 30000),

        "currency": {
            "id": "omega_crystal",
            "emoji": "♾️",
            "name": "Кристалл Омеги",
            "desc": "Абсолютная субстанция. Существует вне законов физики.",
            "shop": {
                "null_egg":      {"name": "❌ Яйцо Нуля",                    "cost": 25000, "action": "egg",    "egg_id": "w10_exclusive"},
                "prestige_boost":{"name": "♾️ Престиж-ускоритель (x5 всего)","cost": 50000, "action": "prestige_mult", "value": 5.0, "duration_min": 60},
                "omega_heal":    {"name": "💠 Абсолютное исцеление",          "cost": 10000, "action": "full_heal_all"},  # лечит всех питомцев
                "transcend":     {"name": "✨ Трансцендентность (+200 ATK)", "cost": 20000, "action": "temp_atk","value": 200, "duration_min": 30},
            },
        },

        # МЕХАНИКА: АБСОЛЮТ (Omega Absolute)
        # Нет RNG при работе. Доход = base * (уровень питомца / 10) * мировой множитель.
        # Питомцы «богоподобного» редкости дают x2 этого бонуса.
        # Раз в 24ч — «Момент Омеги»: бесплатная работа с x20 доходом.
        "mechanic": {
            "type": "omega_absolute",
            "income_formula": "base * (pet_level / 10) * world_mult",
            "world_mult": 50,
            "godlike_mult": 2.0,
            "omega_moment_cooldown_hours": 24,
            "omega_moment_mult": 20.0,
            "synergy_pets": ["omega", "cosmos", "null_entity", "void_dragon", "god_phoenix"],
            "synergy_bonus": {"atk": 50, "def": 40, "hp": 150, "income_mult": 2.0},
            "risk_enabled": False,
            "desc_mechanic": (
                "♾️ Абсолют: доход = база × (уровень питомца ÷ 10) × 50.\n"
                "Богоподобные питомцы: x2 к этому бонусу.\n"
                "⏰ Момент Омеги: раз в 24ч — бесплатная работа с x20 доходом!"
            ),
        },

        "events": [
            {
                "id": "w10_omega_surge", "name": "♾️ Всплеск Омеги!", "emoji": "♾️",
                "desc": "Сам Абсолют наградил тебя своей силой!",
                "prob": 0.08,
                "coins": (100000, 300000), "world_currency": (5000, 12000), "xp": (10000, 25000),
                "egg_id": "w10_rare", "pet_id": None, "risk": False,
            },
            {
                "id": "w10_null_entity_rare", "name": "❌ Нуль-сущность!", "emoji": "✨",
                "desc": "Нечто из ничего обрело форму перед тобой!",
                "prob": 0.01,
                "coins": (200000, 500000), "world_currency": (15000, 30000), "xp": (50000, 100000),
                "egg_id": None, "pet_id": "null_entity", "risk": False,
            },
            {
                "id": "w10_reality_crack", "name": "💢 Трещина реальности!", "emoji": "💢",
                "desc": "Реальность треснула! Питомец нестабилен — получает огромный урон.",
                "prob": 0.08,
                "coins": (-10000, -3000), "world_currency": (-500, -100), "xp": (0, 0),
                "damage": 250,
                "egg_id": None, "pet_id": None, "risk": True,
            },
            {
                "id": "w10_nothing", "name": "♾️ Бесконечность", "emoji": "♾️",
                "desc": "Бесконечность не торопится. Стабильный, огромный доход.",
                "prob": 0.83,
                "coins": (8000, 20000), "world_currency": (500, 1200), "xp": (2000, 5000),
                "egg_id": None, "pet_id": None, "risk": False,
            },
        ],

        "boss": {
            "name": "АБСОЛЮТ — ОМЕГА ПЕРВОРОДНЫЙ", "emoji": "♾️",
            "desc": "Он и есть конец. Победить его — значит превзойти всё.",
            "hp": 10000000,   # 10 миллионов — глобальный рейдовый босс
            "atk": 5000, "def": 2500,
            "abilities": [
                "♾️ Абсолютный ноль: снимает ВСЕ баффы игрока и питомца",
                "❌ Аннигиляция: 2000 урона (невозможно уклониться)",
                "💠 Поглощение: крадёт 50% текущего HP питомца",
                "🌌 Сингулярность: раз в 10 раундов — 5000 урона всем",
                "♾️ Фаза 2 — ИСТИННЫЙ АБСОЛЮТ: все цифры умножаются на 2",
            ],
            "phase2_hp_pct": 0.50,
            "phase2_atk_mult": 2.0,
            "phase2_desc": "АБСОЛЮТ раскрыл истинную форму! Мир дрожит!",
            "is_raid": True,          # рейдовый босс — нужно 5+ игроков
            "min_players": 5,
            "reward_coins": (5000000, 15000000),
            "reward_world_currency": (100000, 300000),
            "reward_pet": "null_entity",
            "reward_pet_chance": 0.03,
            "cooldown_hours": 336,   # 2 недели
        },

        "unlock_reward": {
            "coins": 3000000,
            "egg_id": "w10_common",
            "world_currency": 15000,
            "desc": "ТЫ ДОСТИГ АБСОЛЮТА. Боги склоняются перед тобой.",
        },
    },


    # ══════════════════════════════════════════════════════════════════════════
    # 🔒 СЕКРЕТНЫЙ МИР Ω — ТЕНЕВОЕ ЦАРСТВО
    # Условие открытия: победить боссов миров 5, 6, 7 (три победы над боссами)
    # Механика: ХАОС — каждая работа генерирует случайный эффект (x0.1 до x50)
    # ══════════════════════════════════════════════════════════════════════════
    "shadow": {
        "name": "🌪️ Теневое царство",
        "desc": "???  Это место не должно существовать. Но оно есть.",
        "unlock_cost": 0,   # открывается выполнением условия, не монетами
        "bg": "🌪️",
        "is_secret": True,

        # Условие разблокировки
        "unlock_condition": {
            "type": "boss_kills",
            "worlds": [5, 6, 7],
            "desc": "Победи боссов миров 5, 6 и 7",
        },

        "eggs": {
            "shadow_common": {
                "emoji": "🌪️", "name": "Яйцо хаоса", "price": 0, "stars": 0,
                "pool": {"chaos_beast": 60, "void_dragon": 25, "shadow_wraith": 15},
                "exclusive": True,
                "currency_id": "chaos_token",  # покупается за валюту мира
                "currency_cost": 100,
            },
        },

        "shop_weapons": ["void_blade", "titan_hammer"],
        "shop_armor":   ["titan_shell"],
        "work_reward": (1000, 100000),  # хаос — может дать что угодно

        "currency": {
            "id": "chaos_token",
            "emoji": "🌪️",
            "name": "Жетон хаоса",
            "desc": "Нестабильная валюта теневого мира. Непредсказуема.",
            "shop": {
                "chaos_egg":   {"name": "🌪️ Яйцо хаоса",                       "cost": 100,  "action": "egg",    "egg_id": "shadow_common"},
                "chaos_power": {"name": "💥 Сила хаоса (случайный x2-x10 ATK)", "cost": 80,   "action": "chaos_atk"},
                "chaos_heal":  {"name": "🌀 Хаотичное лечение (случайно)",       "cost": 50,   "action": "chaos_heal"},
            },
        },

        # МЕХАНИКА: АБСОЛЮТНЫЙ ХАОС
        # Каждая работа крутит «колесо хаоса»:
        # — 30%: x0.1 (катастрофа)
        # — 25%: x0.5 (плохо)
        # — 20%: x1.5 (норм)
        # — 15%: x5 (удача!)
        # — 8%:  x15 (большая удача!)
        # — 2%:  x50 (ДЖЕКПОТ!)
        "mechanic": {
            "type": "chaos_wheel",
            "wheel": [
                {"prob": 0.30, "mult": 0.1,  "label": "💀 Катастрофа"},
                {"prob": 0.25, "mult": 0.5,  "label": "😬 Неудача"},
                {"prob": 0.20, "mult": 1.5,  "label": "😊 Норм"},
                {"prob": 0.15, "mult": 5.0,  "label": "🎉 Удача!"},
                {"prob": 0.08, "mult": 15.0, "label": "🌟 Большая удача!"},
                {"prob": 0.02, "mult": 50.0, "label": "💥 ДЖЕКПОТ!!!"},
            ],
            "synergy_pets": ["chaos_beast", "void_dragon", "shadow_wraith"],
            "synergy_bonus": {"atk": 40, "def": 30, "hp": 120, "wheel_shift": 1},  # сдвигает колесо в лучшую сторону
            "risk_enabled": True,
            "desc_mechanic": (
                "🌪️ Колесо хаоса: x0.1 до x50 за работу — чистый рандом.\n"
                "2% шанс ДЖЕКПОТА: x50 дохода!\n"
                "🌀 Питомцы хаоса сдвигают колесо к лучшим результатам."
            ),
        },

        "events": [
            {
                "id": "shadow_rift", "name": "🌀 Хаос-разлом!", "emoji": "🌀",
                "desc": "Разлом хаоса выбросил невероятные ресурсы!",
                "prob": 0.15,
                "coins": (10000, 50000), "world_currency": (500, 2000), "xp": (2000, 8000),
                "egg_id": None, "pet_id": None, "risk": False,
            },
            {
                "id": "shadow_chaos_pet", "name": "🌪️ Зверь хаоса!", "emoji": "✨",
                "desc": "Хаос принял форму! Зверь хаоса присоединился к тебе!",
                "prob": 0.02,
                "coins": (20000, 80000), "world_currency": (1000, 4000), "xp": (5000, 15000),
                "egg_id": None, "pet_id": "chaos_beast", "risk": False,
            },
            {
                "id": "shadow_nothing", "name": "🌪️ Хаос", "emoji": "🌪️",
                "desc": "Хаос непредсказуем. Всё могло быть иначе.",
                "prob": 0.83,
                "coins": (500, 30000), "world_currency": (50, 1500), "xp": (500, 5000),
                "egg_id": None, "pet_id": None, "risk": False,  # хаос сам по себе
            },
        ],

        "boss": {
            "name": "ХАОС-ПЕРВОРОДНЫЙ", "emoji": "🌪️",
            "desc": "Он существовал до порядка. Он будет после всего.",
            "hp": 500000, "atk": 2000, "def": 500,
            "abilities": [
                "🌪️ Хаос-волна: случайный урон от 1 до 5000",
                "💥 Хаос-взрыв: 50% шанс убить питомца мгновенно",
                "🌀 Искажение: все статы питомца случайно перемешиваются",
                "❌ Аннигиляция хаоса: сбрасывает HP питомца до 1 (40% шанс)",
                "🌪️ Фаза 2 — ИСТИННЫЙ ХАОС: все правила отменяются",
            ],
            "phase2_hp_pct": 0.40,
            "phase2_atk_mult": 99.0,  # буквально хаос — случайный множитель
            "phase2_desc": "ХАОС ОСВОБОДИЛСЯ! Всё возможно и ничего не гарантировано!",
            "reward_coins": (0, 10000000),   # от 0 до 10М — хаос
            "reward_world_currency": (0, 50000),
            "reward_pet": "chaos_beast",
            "reward_pet_chance": 0.15,      # зато 15%!
            "cooldown_hours": 48,
        },

        "unlock_reward": {
            "coins": 0,   # сам факт открытия — награда
            "egg_id": "shadow_common",
            "world_currency": 200,
            "desc": "🌪️ ТЫ НАШЁЛ СКРЫТЫЙ МИР. Хаос приветствует тебя... или нет.",
        },
    },


    # ══════════════════════════════════════════════════════════════════════════
    # 🔒 СЕКРЕТНЫЙ МИР ⚙️ — КУЗНИЦА ТИТАНОВ
    # Условие: иметь 5+ питомцев уровня 30+ одновременно
    # Механика: КРАФТ — работа добывает «детали», из них крафтятся лучшие
    #           предметы/питомцы. Никакого RNG — чистое крафтовое удовольствие.
    # ══════════════════════════════════════════════════════════════════════════
    "forge": {
        "name": "⚙️ Кузница Титанов",
        "desc": "Древнее место силы, где творятся легенды. Сюда попадают только мастера.",
        "unlock_cost": 0,
        "bg": "⚙️",
        "is_secret": True,

        "unlock_condition": {
            "type": "pets_level",
            "count": 5,
            "min_level": 30,
            "desc": "Иметь 5 или более питомцев уровня 30+",
        },

        "eggs": {
            "forge_exclusive": {
                "emoji": "⚙️", "name": "Стальное яйцо", "price": 0, "stars": 0,
                "pool": {"iron_titan": 70, "gorilla": 20, "lava_golem": 10},
                "exclusive": True,
                "currency_id": "forge_part",
                "currency_cost": 500,
            },
        },

        "shop_weapons": ["excalibur", "dragon_lance", "void_blade"],
        "shop_armor":   ["dragon_scale", "void_armor"],
        "work_reward": (5000, 10000),

        "currency": {
            "id": "forge_part",
            "emoji": "⚙️",
            "name": "Деталь кузницы",
            "desc": "Металлические детали, добытые в кузнице. Используются для крафта.",
            "shop": {
                "forge_egg":     {"name": "⚙️ Стальное яйцо",                      "cost": 500,  "action": "egg",    "egg_id": "forge_exclusive"},
                "titan_weapon":  {"name": "🔨 Оружие Титана (крафт void_blade)",    "cost": 1000, "action": "craft",  "item": "void_blade"},
                "titan_armor":   {"name": "🛡️ Броня Титана (крафт void_armor)",    "cost": 800,  "action": "craft",  "item": "void_armor"},
                "titan_upgrade": {"name": "⬆️ Улучшение питомца (+5 уровней)",      "cost": 2000, "action": "pet_level", "value": 5},
            },

            # КРАФТ-РЕЦЕПТЫ — уникальная система
            "craft_recipes": {
                "forge_pet_recipe": {
                    "name": "🔨 Рецепт: Железный Титан",
                    "ingredients": {"forge_part": 2000, "magma": 500},
                    "result_pet": "iron_titan",
                    "guaranteed": True,  # без RNG — гарантия
                },
                "omega_weapon_recipe": {
                    "name": "♾️ Рецепт: Клинок Омеги",
                    "ingredients": {"forge_part": 5000},
                    "result_item": "omega_blade",
                    "guaranteed": True,
                },
            },
        },

        "mechanic": {
            "type": "crafting",
            "work_gives_parts": (20, 60),   # работа даёт детали
            "no_rng_crafting": True,        # крафт — гарантия без рандома
            "synergy_pets": ["iron_titan", "gorilla", "lava_golem", "bear"],
            "synergy_bonus": {"atk": 30, "def": 40, "hp": 100, "parts_mult": 1.5},
            "risk_enabled": False,
            "desc_mechanic": (
                "⚙️ Крафт: работа даёт детали кузницы.\n"
                "Детали → рецепты → гарантированные питомцы и предметы.\n"
                "🔨 Кузнечные питомцы: +50% деталей."
            ),
        },

        "events": [
            {
                "id": "forge_masterwork", "name": "⚙️ Шедевр кузнеца!", "emoji": "⚙️",
                "desc": "Ты выковал шедевр! Огромный запас деталей!",
                "prob": 0.15,
                "coins": (8000, 20000), "world_currency": (200, 500), "xp": (1000, 2500),
                "egg_id": None, "pet_id": None, "risk": False,
            },
            {
                "id": "forge_iron_titan_rare", "name": "⚙️ Железный Титан пробудился!", "emoji": "✨",
                "desc": "Древний Железный Титан встал из руин кузницы!",
                "prob": 0.01,
                "coins": (30000, 60000), "world_currency": (2000, 5000), "xp": (8000, 15000),
                "egg_id": None, "pet_id": "iron_titan", "risk": False,
            },
            {
                "id": "forge_broken", "name": "💥 Кузница сломалась!", "emoji": "💥",
                "desc": "Что-то пошло не так — потеря деталей!",
                "prob": 0.10,
                "coins": (-3000, -500), "world_currency": (-80, -20), "xp": (0, 0),
                "egg_id": None, "pet_id": None, "risk": True,
            },
            {
                "id": "forge_nothing", "name": "⚒️ День в кузнице", "emoji": "⚒️",
                "desc": "Ты работаешь. Детали копятся. Результат придёт.",
                "prob": 0.74,
                "coins": (2000, 6000), "world_currency": (50, 150), "xp": (500, 1200),
                "egg_id": None, "pet_id": None, "risk": False,
            },
        ],

        "boss": {
            "name": "Прото-Титан — Первый Кузнец", "emoji": "⚙️",
            "desc": "Создатель всех механизмов мира. Его тело — живая машина.",
            "hp": 800000, "atk": 3000, "def": 2000,
            "abilities": [
                "⚙️ Механический пресс: 1500 урона + -60 защиты игрока",
                "🔨 Молот правды: 2500 урона (не промахивается)",
                "🛡️ Стальной щит: поглощает 5000 урона (раз в бой)",
                "⚡ Перегрузка: +100 атаки навсегда (каждые 8 раундов)",
                "⚙️ Фаза 2 — Протокол уничтожения: 4 атаки за раунд",
            ],
            "phase2_hp_pct": 0.35,
            "phase2_atk_mult": 3.0,
            "phase2_desc": "Прото-Титан активировал протокол уничтожения!",
            "reward_coins": (500000, 2000000),
            "reward_world_currency": (10000, 30000),
            "reward_pet": "iron_titan",
            "reward_pet_chance": 0.12,   # щедро — заслужили
            "cooldown_hours": 72,
        },

        "unlock_reward": {
            "coins": 50000,
            "egg_id": "forge_exclusive",
            "world_currency": 500,
            "desc": "⚙️ КУЗНИЦА ОТКРЫТА! Мастер нашёл своё место силы.",
        },
    },


    # ══════════════════════════════════════════════════════════════════════════
    # 🔒 СЕКРЕТНЫЙ МИР 🌌 — МИР НАЧАЛА
    # Условие: пройти все 10 основных миров И иметь питомца godlike редкости
    # Механика: ПЕРЕРОЖДЕНИЕ (New Game+) — сброс прогресса, но x100 к постоянным
    #           бонусам. Самый хардкорный и самый выгодный мир.
    # ══════════════════════════════════════════════════════════════════════════
    "origin": {
        "name": "🌌 Мир Начала",
        "desc": "До того как было всё. После того как ничего не останется. Ты здесь.",
        "unlock_cost": 0,
        "bg": "🌌✨",
        "is_secret": True,

        "unlock_condition": {
            "type": "compound",
            "conditions": [
                {"type": "worlds_unlocked", "count": 10, "desc": "Разблокировать все 10 миров"},
                {"type": "pet_rarity", "rarity": "godlike", "desc": "Иметь питомца редкости godlike"},
            ],
            "desc": "Разблокировать все 10 миров + иметь питомца godlike редкости",
        },

        "eggs": {
            "origin_egg": {
                "emoji": "🌌", "name": "Яйцо начала", "price": 0, "stars": 0,
                "pool": {"primordial": 50, "null_entity": 30, "omega": 20},
                "exclusive": True,
                "currency_id": "primordial_essence",
                "currency_cost": 1000,
            },
        },

        "shop_weapons": ["zeus_bolt"],
        "shop_armor":   ["zeus_aegis"],
        "work_reward": (50000, 100000),

        "currency": {
            "id": "primordial_essence",
            "emoji": "🌌",
            "name": "Первородная эссенция",
            "desc": "Субстанция до рождения вселенной. Недоступна смертным.",
            "shop": {
                "origin_egg":   {"name": "🌌 Яйцо начала",                    "cost": 1000,  "action": "egg",    "egg_id": "origin_egg"},
                "prestige":     {"name": "♾️ ПЕРЕРОЖДЕНИЕ (Prestige x100)",    "cost": 10000, "action": "prestige"},
                "origin_power": {"name": "✨ Первородная сила (+300 ATK/DEF)", "cost": 5000,  "action": "temp_all","value": 300, "duration_min": 30},
                "zeus_set":     {"name": "⚡ Набор Зевса (оружие + броня)",    "cost": 20000, "action": "craft_set","items": ["zeus_bolt","zeus_aegis"]},
            },
        },

        # МЕХАНИКА: ПЕРЕРОЖДЕНИЕ (Prestige System)
        # Игрок может потратить 10000 первородной эссенции для Prestige:
        # — Сброс монет, яиц, мировых валют (не питомцев!)
        # — Получение постоянного множителя x1.5 ко ВСЕМ доходам навсегда
        # — Макс 10 престижей (x1.5^10 ≈ x57 к базовому доходу)
        # — При 10 престижах — особый титул «Перворождённый» и питомец primordial
        "mechanic": {
            "type": "prestige",
            "prestige_cost_essence": 10000,
            "prestige_permanent_mult": 1.5,
            "max_prestiges": 10,
            "prestige_10_reward": {
                "pet": "primordial",
                "title": "Перворождённый ✨",
                "permanent_mult": 57.0,
            },
            "synergy_pets": ["primordial", "null_entity", "omega", "cosmos"],
            "synergy_bonus": {"atk": 100, "def": 80, "hp": 300, "prestige_mult_bonus": 0.5},
            "risk_enabled": False,
            "desc_mechanic": (
                "🌌 Перерождение: трать эссенцию на Prestige.\n"
                "Каждый престиж: постоянный x1.5 ко всему (макс 10 раз = x57!).\n"
                "10 престижей → питомец Перворождённый + титул.\n"
                "⚠️ Сброс: монеты, валюты (питомцы остаются!)."
            ),
        },

        "events": [
            {
                "id": "origin_genesis", "name": "🌌 Момент творения!", "emoji": "🌌",
                "desc": "Ты был свидетелем рождения новой вселенной! Невероятная награда!",
                "prob": 0.05,
                "coins": (500000, 1000000), "world_currency": (10000, 25000), "xp": (50000, 100000),
                "egg_id": "origin_egg", "pet_id": None, "risk": False,
            },
            {
                "id": "origin_primordial_rare", "name": "🌌 Перворождённый!", "emoji": "✨",
                "desc": "Само Начало взирает на тебя и признаёт достойным!",
                "prob": 0.005,  # 0.5% — исключительно редко
                "coins": (1000000, 2000000), "world_currency": (50000, 100000), "xp": (200000, 500000),
                "egg_id": None, "pet_id": "primordial", "risk": False,
            },
            {
                "id": "origin_unmaking", "name": "💢 Развоплощение!", "emoji": "💢",
                "desc": "Первородные силы чуть не уничтожили тебя!",
                "prob": 0.05,
                "coins": (-20000, -5000), "world_currency": (-500, -100), "xp": (0, 0),
                "damage": 300,
                "egg_id": None, "pet_id": None, "risk": True,
            },
            {
                "id": "origin_nothing", "name": "🌌 Вечность", "emoji": "🌌",
                "desc": "Здесь нет времени. Только ты и бесконечность.",
                "prob": 0.895,
                "coins": (15000, 40000), "world_currency": (500, 1500), "xp": (5000, 12000),
                "egg_id": None, "pet_id": None, "risk": False,
            },
        ],

        "boss": {
            "name": "ПЕРВОРОЖДЁННЫЙ — НАЧАЛО ВСЕГО", "emoji": "🌌",
            "desc": "Ты сражаешься с самой концепцией существования.",
            "hp": 50000000,  # 50 миллионов — легендарный рейд
            "atk": 20000, "def": 10000,
            "abilities": [
                "🌌 Первозданный вихрь: 10000 урона (невозможно заблокировать)",
                "✨ Стирание: питомец теряет все баффы и уровень уменьшается на 5",
                "💠 Воссоздание: Перворождённый лечится на 10% макс HP (раз/раунд)",
                "🌀 Пространство-время: меняет очерёдность атак случайным образом",
                "🌌 Фаза 2 — ДО НАЧАЛА: ВСЕ правила игры нарушаются",
            ],
            "phase2_hp_pct": 0.50,
            "phase2_atk_mult": 5.0,
            "phase2_desc": "ПЕРВОРОЖДЁННЫЙ открыл время до начала! Победить его — невозможно? Докажи обратное.",
            "is_raid": True,
            "min_players": 10,
            "reward_coins": (50000000, 100000000),
            "reward_world_currency": (1000000, 2000000),
            "reward_pet": "primordial",
            "reward_pet_chance": 0.20,   # Если смог убить — заслужил!
            "cooldown_hours": 720,       # 1 месяц
        },

        "unlock_reward": {
            "coins": 10000000,
            "egg_id": "origin_egg",
            "world_currency": 5000,
            "desc": "🌌 МИР НАЧАЛА ОТКРЫТ. Ты достиг конца. И начала. Они одно.",
        },
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ — ИНТЕГРАЦИЯ С ИГРОЙ
# ═══════════════════════════════════════════════════════════════════════════════

def build_world_eggs() -> dict:
    """Собирает все яйца из всех миров в единый пул (для совместимости)."""
    eggs = {}
    for wid, w in WORLDS.items():
        for eid, edata in w["eggs"].items():
            eggs[eid] = edata
    return eggs


def get_world(world_id) -> dict | None:
    """Получить данные мира по ID (int или str для секретных)."""
    return WORLDS.get(world_id)


def get_secret_worlds() -> dict:
    """Возвращает только секретные миры."""
    return {k: v for k, v in WORLDS.items() if v.get("is_secret")}


def get_world_currency(world_id) -> dict | None:
    """Получить данные мировой валюты."""
    w = get_world(world_id)
    return w["currency"] if w else None


def check_secret_world_unlock(world_key: str, user_data: dict) -> bool:
    """
    Проверяет, выполнено ли условие открытия секретного мира.

    user_data ожидает:
    {
        "boss_kills": {5: True, 6: True, 7: True, ...},  # победы над боссами миров
        "pets": [{"level": 35, "rarity": "godlike"}, ...],
        "worlds_unlocked": [1, 2, 3, ..., 10],
        "prestiged": False,
    }
    """
    w = WORLDS.get(world_key)
    if not w or not w.get("is_secret"):
        return False

    cond = w.get("unlock_condition", {})
    ctype = cond.get("type")

    if ctype == "boss_kills":
        required_worlds = cond.get("worlds", [])
        boss_kills = user_data.get("boss_kills", {})
        return all(boss_kills.get(wid) for wid in required_worlds)

    elif ctype == "pets_level":
        required_count = cond.get("count", 1)
        min_level = cond.get("min_level", 1)
        pets = user_data.get("pets", [])
        qualified = [p for p in pets if p.get("level", 1) >= min_level]
        return len(qualified) >= required_count

    elif ctype == "compound":
        conditions = cond.get("conditions", [])
        for sub in conditions:
            if sub["type"] == "worlds_unlocked":
                unlocked = user_data.get("worlds_unlocked", [])
                if len(unlocked) < sub["count"]:
                    return False
            elif sub["type"] == "pet_rarity":
                pets = user_data.get("pets", [])
                req_rarity = sub["rarity"]
                if not any(p.get("rarity") == req_rarity for p in pets):
                    return False
        return True

    return False


def get_world_synergy_bonus(world_id, pet_type: str) -> dict:
    """
    Возвращает бонусы синергии питомца в конкретном мире.
    Если питомец не в списке синергии — пустой бонус.
    """
    empty = {"atk": 0, "def": 0, "hp": 0, "coins_mult": 1.0}
    w = get_world(world_id)
    if not w:
        return empty

    mechanic = w.get("mechanic", {})
    synergy_pets = mechanic.get("synergy_pets", [])
    if pet_type not in synergy_pets:
        return empty

    bonus = mechanic.get("synergy_bonus", {}).copy()
    return {
        "atk": bonus.get("atk", 0),
        "def": bonus.get("def", 0),
        "hp": bonus.get("hp", 0),
        "coins_mult": bonus.get("coins_mult", 1.0),
    }


def roll_world_event(world_id) -> dict | None:
    """
    Бросает кубик и возвращает случайное событие мира.
    Возвращает None если мира нет или событий нет.
    """
    import random
    w = get_world(world_id)
    if not w:
        return None

    events = w.get("events", [])
    if not events:
        return None

    roll = random.random()
    cumulative = 0.0
    for event in events:
        cumulative += event.get("prob", 0)
        if roll <= cumulative:
            return event

    return events[-1]  # fallback


def roll_rare_drop(world_id) -> str | None:
    """
    Проверяет шанс редкого дропа питомца из события (для казино-момента).
    Возвращает pet_id или None.
    """
    import random
    w = get_world(world_id)
    if not w:
        return None

    # Проверяем каждое событие типа rare_drop
    for event in w.get("events", []):
        if event.get("pet_id") and random.random() < event.get("prob", 0):
            return event["pet_id"]

    return None


def format_world_card(world_id) -> str:
    """Возвращает красивую карточку мира для Telegram (HTML)."""
    w = get_world(world_id)
    if not w:
        return "❌ Мир не найден"

    lines = [
        f"<b>{w['name']}</b>",
        f"<i>{w['desc']}</i>",
        "",
    ]

    if w.get("is_secret"):
        cond = w.get("unlock_condition", {})
        lines.append(f"🔒 <b>Секретный мир</b>")
        lines.append(f"Условие: {cond.get('desc', '???')}")
    elif w.get("unlock_cost", 0) > 0:
        lines.append(f"💰 Цена открытия: <b>{w['unlock_cost']:,}</b> монет")
    else:
        lines.append("🆓 Стартовый мир")

    lines.append("")

    # Механика
    mech = w.get("mechanic", {})
    if mech.get("desc_mechanic"):
        lines.append(f"⚙️ <b>Механика:</b>")
        lines.append(mech["desc_mechanic"])
        lines.append("")

    # Валюта
    curr = w.get("currency", {})
    if curr:
        lines.append(f"{curr['emoji']} <b>Валюта:</b> {curr['name']}")
        lines.append(f"<i>{curr['desc']}</i>")
        lines.append("")

    # Босс
    boss = w.get("boss", {})
    if boss:
        lines.append(f"👹 <b>Босс:</b> {boss['emoji']} {boss['name']}")
        lines.append(f"❤️ HP: {boss['hp']:,} | ⚔️ ATK: {boss['atk']} | 🛡 DEF: {boss['def']}")
        lines.append(f"⏰ КД: {boss.get('cooldown_hours', 24)}ч")
        lines.append(f"🎁 Шанс питомца: {int(boss.get('reward_pet_chance', 0) * 100)}%")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# МНОЖИТЕЛИ МИРОВ — обновлены под новую систему
# ═══════════════════════════════════════════════════════════════════════════════

WORLD_MULTIPLIERS = {
    1: 1,
    2: 2,
    3: 4,
    4: 8,
    5: 15,
    6: 30,
    7: 60,
    8: 120,
    9: 250,
    10: 500,
    # Секретные миры — особые множители
    "shadow": 25,   # хаос: нестабильный, средний x25
    "forge":  80,   # крафт: стабильный x80
    "origin": 1000, # prestige: x1000 базы (но жертвуешь прогрессом)
}

# Пул яиц (совместимость со старым кодом)
WORLD_EGGS = build_world_eggs()
RARITY_EMOJI = {"common":"⚪","rare":"🟣","legendary":"🌟","mythic":"💎","godlike":"⚡"}
RARITY_NAMES = {"common":"Обычный","rare":"Редкий","legendary":"Легендарный","mythic":"Мифический","godlike":"Богоподобный"}

# ═══════════════════════════════════════════════════
# ГЕНЕРАТОР КАРТОЧКИ ПИТОМЦА (Pillow)
# ═══════════════════════════════════════════════════
#
# v2 — переработан дизайн карточки:
#  • Высота карточки теперь ДИНАМИЧЕСКАЯ (считается из контента), поэтому
#    длинное описание способности больше не обрезается и не наслаивается
#    на блок XP — раньше при 1+ строке способности всё съезжало.
#  • Все иконки статов/скиллов/боя теперь рисуются ВЕКТОРНО (мечи, щиты,
#    сердца, кубок и т.д. через ImageDraw), а не через emoji-шрифт.
#    Раньше при отсутствии NotoColorEmoji.ttf на сервере выводились
#    квадраты-заглушки вместо иконок — теперь иконки всегда на месте.
#  • Эмодзи питомца (аватар) всё ещё пробует системный emoji-шрифт, но
#    теперь рендерится на отдельном слое, обрезается по контенту и
#    масштабируется (resize), а не печатается «как есть» — у растровых
#    color-emoji шрифтов фиксированный нативный размер (обычно 109px),
#    из-за чего раньше эмодзи могли вылезать огромными и разваливать макет.
#    Если emoji-шрифта нет вовсе — рисуется декоративная звезда цвета
#    редкости вместо пустого квадрата.

# Цвета по редкости
RARITY_COLORS = {
    "common":    (138, 155, 176),
    "rare":      (74,  158, 255),
    "legendary": (255, 159,  28),
    "mythic":    (199, 125, 255),
    "godlike":   (255,  77, 109),
}

# Путь к шрифту — кладёшь рядом с ботом
_FONT_DIR = os.path.dirname(os.path.abspath(__file__))
_EMOJI_FONT_PATH = os.path.join(_FONT_DIR, "NotoColorEmoji.ttf")
_EMOJI_IMG_DIR = os.path.join(_FONT_DIR, "emoji")

def _emoji_image_path(emoji):
    if not emoji:
        return None
    cps = [f"{ord(ch):X}" for ch in emoji if f"{ord(ch):X}" not in ("FE0F", "FE0E", "200D")]
    if not cps:
        return None
    path = os.path.join(_EMOJI_IMG_DIR, "-".join(cps) + ".png")
    return path if os.path.exists(path) else None

# Пути к шрифтам с поддержкой кириллицы — перебираем по приоритету.
# Кладём рядом с ботом NotoSans-Regular.ttf и NotoSans-Bold.ttf (или NotoSans-Medium.ttf).
# Вариативный шрифт NotoSans[wdth,wght].ttf Pillow часто не может загрузить корректно,
# поэтому он стоит последним как запасной вариант.
_REGULAR_CANDIDATES = [
    os.path.join(_FONT_DIR, "NotoSans-Regular.ttf"),
    os.path.join(_FONT_DIR, "NotoSans-Medium.ttf"),
    os.path.join(_FONT_DIR, "NotoSans[wdth,wght].ttf"),
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]
_BOLD_CANDIDATES = [
    os.path.join(_FONT_DIR, "NotoSans-Bold.ttf"),
    os.path.join(_FONT_DIR, "NotoSans-SemiBold.ttf"),
    os.path.join(_FONT_DIR, "NotoSans[wdth,wght].ttf"),
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]

def _find_font(candidates: list) -> str | None:
    """Возвращает первый существующий путь из списка кандидатов."""
    for path in candidates:
        if os.path.exists(path):
            return path
    return None

def _load_fonts():
    """Загружает шрифты с поддержкой кириллицы. Если не найдены — дефолтные."""
    from PIL import ImageFont

    regular_path = _find_font(_REGULAR_CANDIDATES)
    bold_path    = _find_font(_BOLD_CANDIDATES)

    if regular_path and bold_path:
        try:
            fonts = {
                "title":    ImageFont.truetype(bold_path,    24),
                "bold":     ImageFont.truetype(bold_path,    19),
                "medium":   ImageFont.truetype(regular_path, 15),
                "small":    ImageFont.truetype(regular_path, 13),
                "small_b":  ImageFont.truetype(bold_path,    13),
                "tiny":     ImageFont.truetype(regular_path, 12),
                "tiny_b":   ImageFont.truetype(bold_path,    12),
            }
            try:
                fonts["emoji"] = ImageFont.truetype(_EMOJI_FONT_PATH, 109) if os.path.exists(_EMOJI_FONT_PATH) else None
            except Exception:
                fonts["emoji"] = None
            return fonts
        except Exception as e:
            logger.warning(f"_load_fonts: не удалось загрузить шрифт: {e}")

    # Последний fallback — дефолтный шрифт (без кириллицы, но хотя бы работает)
    default = ImageFont.load_default()
    logger.warning("_load_fonts: шрифты с кириллицей не найдены, используется дефолтный шрифт. "
                   "Положите NotoSans-Regular.ttf и NotoSans-Bold.ttf рядом с ботом.")
    return {k: default for k in ("title", "bold", "medium", "small", "small_b", "tiny", "tiny_b")} | {"emoji": None}

def _draw_rounded_rect(draw, xy, radius, fill=None, outline=None, width=1):
    """Рисует прямоугольник со скруглёнными углами."""
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)

def _hex_alpha(color_rgb, alpha):
    """Возвращает цвет с альфой для RGBA."""
    return color_rgb + (alpha,)

def _wrap_text(draw, text, font, max_width):
    """Перенос текста по словам в пределах max_width (px)."""
    words = text.split(" ")
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

# ── Векторные иконки (не зависят от наличия emoji-шрифта на сервере) ──
def _icon_sword(draw, cx, cy, size, color):
    """Меч по диагонали: клинок + гарда + рукоять."""
    s = size * 0.55
    draw.line([(cx - s, cy + s), (cx + s * 0.7, cy - s * 0.7)], fill=color, width=max(3, int(size * 0.13)))
    tip = (cx + s * 0.7, cy - s * 0.7)
    draw.polygon([tip, (tip[0] - 6, tip[1] + 2), (tip[0] + 2, tip[1] + 6)], fill=color)
    gx, gy = cx - s * 0.25, cy + s * 0.25
    draw.line([(gx - 9 * 0.6, gy + 9 * 0.18), (gx + 9 * 0.6, gy - 9 * 0.18)], fill=color, width=max(2, int(size * 0.11)))
    draw.line([(cx - s, cy + s), (cx - s * 1.32, cy + s * 1.32)], fill=color, width=max(3, int(size * 0.16)))

def _icon_shield(draw, cx, cy, size, color):
    """Классический геральдический щит."""
    w, h = size * 0.74, size * 0.86
    top = cy - h / 2
    pts = [
        (cx - w / 2, top), (cx + w / 2, top),
        (cx + w / 2, top + h * 0.5),
        (cx, top + h),
        (cx - w / 2, top + h * 0.5),
    ]
    draw.polygon(pts, fill=color)
    accent = (15, 24, 36, 200)
    draw.line([(cx, top + h * 0.12), (cx, top + h * 0.78)], fill=accent, width=max(2, int(size * 0.07)))

def _icon_heart(draw, cx, cy, size, color):
    r = size * 0.28
    draw.ellipse([cx - r * 1.85, cy - r * 1.1, cx - r * 0.1, cy + r * 0.85], fill=color)
    draw.ellipse([cx + r * 0.1, cy - r * 1.1, cx + r * 1.85, cy + r * 0.85], fill=color)
    draw.polygon([(cx - r * 1.7, cy + r * 0.25), (cx + r * 1.7, cy + r * 0.25), (cx, cy + r * 2.05)], fill=color)

def _icon_crit(draw, cx, cy, size, color):
    """Молния."""
    s = size * 0.55
    pts = [
        (cx + s * 0.15, cy - s), (cx - s * 0.55, cy + s * 0.05), (cx - s * 0.02, cy + s * 0.05),
        (cx - s * 0.35, cy + s), (cx + s * 0.55, cy - s * 0.12), (cx + s * 0.05, cy - s * 0.12),
    ]
    draw.polygon(pts, fill=color)

def _icon_regen(draw, cx, cy, size, color, width=None):
    r = size * 0.42
    width = width or max(2, int(size * 0.16))
    bbox = [cx - r, cy - r, cx + r, cy + r]
    draw.arc(bbox, start=-30, end=260, fill=color, width=width)
    ang = math.radians(260)
    ax, ay = cx + r * math.cos(ang), cy + r * math.sin(ang)
    draw.polygon([(ax, ay), (ax - 7, ay - 2), (ax - 2, ay + 7)], fill=color)

def _icon_trophy(draw, cx, cy, size, color):
    w, h = size * 0.62, size * 0.6
    _draw_rounded_rect(draw, [cx - w / 2, cy - h / 2, cx + w / 2, cy + h * 0.12], radius=4, fill=color)
    draw.arc([cx - w / 2 - 9, cy - h / 2 + 2, cx - w / 2 + 9, cy + h * 0.05], start=90, end=270, fill=color, width=3)
    draw.arc([cx + w / 2 - 9, cy - h / 2 + 2, cx + w / 2 + 9, cy + h * 0.05], start=270, end=90, fill=color, width=3)
    _draw_rounded_rect(draw, [cx - w * 0.18, cy + h * 0.1, cx + w * 0.18, cy + h * 0.34], radius=2, fill=color)
    draw.line([(cx - w * 0.32, cy + h * 0.36), (cx + w * 0.32, cy + h * 0.36)], fill=color, width=4)

def _icon_skull(draw, cx, cy, size, color):
    """Простой череп: округлый купол + челюсть + глаза-провалы."""
    r = size * 0.36
    _draw_rounded_rect(draw, [cx - r, cy - r * 0.95, cx + r, cy + r * 0.35], radius=int(r), fill=color)
    _draw_rounded_rect(draw, [cx - r * 0.55, cy + r * 0.05, cx + r * 0.55, cy + r * 0.62], radius=3, fill=color)
    bg = (255, 255, 255, 255)
    eye_r = r * 0.24
    draw.ellipse([cx - r * 0.46 - eye_r, cy - r * 0.12 - eye_r, cx - r * 0.46 + eye_r, cy - r * 0.12 + eye_r], fill=bg)
    draw.ellipse([cx + r * 0.46 - eye_r, cy - r * 0.12 - eye_r, cx + r * 0.46 + eye_r, cy - r * 0.12 + eye_r], fill=bg)

def _icon_chart(draw, cx, cy, size, color):
    w, h = size * 0.7, size * 0.6
    bars = [0.45, 0.75, 1.0]
    bw = w / 4
    for i, bh in enumerate(bars):
        bx = cx - w / 2 + i * (bw + bw * 0.3)
        by = cy + h / 2 - h * bh
        _draw_rounded_rect(draw, [bx, by, bx + bw, cy + h / 2], radius=2, fill=color)

def _icon_star(draw, cx, cy, size, color, outline=False, width=2):
    r_out, r_in = size / 2, size / 2 * 0.42
    pts = []
    for i in range(10):
        ang = math.pi / 2 + i * math.pi / 5
        r = r_out if i % 2 == 0 else r_in
        pts.append((cx + r * math.cos(ang), cy - r * math.sin(ang)))
    if outline:
        draw.polygon(pts, outline=color, width=width)
    else:
        draw.polygon(pts, fill=color)

def generate_pet_card(pet: dict) -> BytesIO:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    S = 2
    def u(v):
        return int(round(v * S))
    def lerp(a, b, t):
        return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

    pd = PETS.get(pet.get('pet_type'), PETS['dog'])
    rarity = pd.get('rarity', 'common')
    rc = RARITY_COLORS.get(rarity, (138, 155, 176))
    is_shiny = bool(pet.get('is_shiny'))
    stage = pet_evo_stage(pet)
    evo = EVOLUTION_STAGES[stage]
    level = max(1, int(pet.get('level') or 1))
    name = pet.get('name') or 'Питомец'

    reg = _find_font(_REGULAR_CANDIDATES)
    bold = _find_font(_BOLD_CANDIDATES)
    def F(path, size):
        try:
            return ImageFont.truetype(path, u(size)) if path else ImageFont.load_default()
        except Exception:
            return ImageFont.load_default()
    f_title = F(bold or reg, 30)
    f_bold = F(bold or reg, 22)
    f_med = F(reg or bold, 17)
    f_small = F(reg or bold, 14)
    f_small_b = F(bold or reg, 14)
    f_tiny = F(reg or bold, 12)
    f_tiny_b = F(bold or reg, 12)

    shiny_mult = 1.15 if is_shiny else 1.0
    evo_mult = evo['mult']
    sk_atk = (pet.get('skill_atk') or 0) * 5
    sk_def = (pet.get('skill_def') or 0) * 5
    sk_hp = (pet.get('skill_hp') or 0) * 20
    eff_atk = int((pd['atk'] + level * 3 + sk_atk) * shiny_mult * evo_mult)
    eff_def = int((pd['def'] + level * 2 + sk_def) * shiny_mult * evo_mult)
    max_hp = int(pet.get('max_health') or pd['hp'])
    cur_hp = max(0, int(pet.get('health') or 0))
    eff_hp = int(max_hp * shiny_mult * evo_mult) + sk_hp
    hunger = max(0, min(100, int(pet.get('hunger') or 0)))
    mood = max(0, min(100, int(pet.get('mood') or 0)))
    xp = max(0, int(pet.get('xp') or 0))
    xp_mx = max(1, int(pet.get('xp_to_next') or 100))
    xp_ratio = min(1.0, xp / xp_mx)
    wins = int(pet.get('wins') or 0)
    losses = int(pet.get('losses') or 0)
    total = wins + losses
    wr = int(wins / total * 100) if total else 0

    W = u(560)
    M = u(22)
    avatar_size = u(112)
    avatar_x = M
    avatar_y = M
    name_x = avatar_x + avatar_size + u(18)
    name_w = W - name_x - M - u(8)
    header_bottom = avatar_y + avatar_size
    y = header_bottom + u(24)
    xp_h = u(48)
    xp_y = y
    y += xp_h + u(12)
    stat_h = u(104)
    stat_y = y
    y += stat_h + u(12)
    cond_h = u(78)
    cond_y = y
    y += cond_h + u(12)
    skills_rows = 5
    skills_h = u(34) + skills_rows * u(26) + u(10)
    skills_y = y
    y += skills_h + u(12)
    battle_h = u(68)
    battle_y = y
    y += battle_h + u(18)
    footer_y = y
    H = footer_y + u(38)

    tmp = Image.new('RGBA', (u(10), u(10)))
    td = ImageDraw.Draw(tmp)
    ability = pd.get('ability', '')
    ability_lines = _wrap_text(td, ability, f_small, name_w)[:2] if ability else []

    def fit_text(text, font, max_w):
        if td.textlength(text, font=font) <= max_w:
            return text
        while text and td.textlength(text + '…', font=font) > max_w:
            text = text[:-1]
        return text + '…'

    base = Image.new('RGBA', (W, H), (8, 12, 20, 255))
    bd = ImageDraw.Draw(base)
    top_col = (min(255, 10 + int(rc[0] * 0.20)), min(255, 14 + int(rc[1] * 0.20)), min(255, 22 + int(rc[2] * 0.20)))
    bottom_col = (7, 10, 17)
    for yy in range(H):
        bd.line([(0, yy), (W, yy)], fill=lerp(top_col, bottom_col, yy / max(1, H - 1)))

    glow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-u(70), -u(90), u(320), u(240)], fill=rc + (75,))
    gd.ellipse([W - u(280), u(10), W + u(50), u(290)], fill=rc + (38,))
    if is_shiny:
        gd.ellipse([u(120), -u(50), u(450), u(150)], fill=(255, 215, 0, 28))
    glow = glow.filter(ImageFilter.GaussianBlur(u(42)))
    base = Image.alpha_composite(base, glow)

    layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for yy in range(u(175)):
        a = int(46 * (1 - yy / u(175)))
        d.line([(0, yy), (W, yy)], fill=rc + (a,))
    d.rounded_rectangle([u(1), u(1), W - u(1), H - u(1)], radius=u(22), outline=rc + (85,), width=u(1))
    for i in range(26):
        px = u(18 + (i * 73) % 524)
        py = u(10 + (i * 47) % 150)
        rr = u(1 + i % 3)
        d.ellipse([px - rr, py - rr, px + rr, py + rr], fill=(255, 255, 255, 28 + (i * 17) % 46))

    cx = avatar_x + avatar_size // 2
    cy = avatar_y + avatar_size // 2
    r = avatar_size // 2
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=rc + (38,), outline=rc + (200,), width=u(2))
    d.arc([cx - r - u(4), cy - r - u(4), cx + r + u(4), cy + r + u(4)],
          start=-90, end=-90 + 360 * xp_ratio, fill=(255, 255, 255, 235), width=u(4))
    inner_r = int(r * 0.82)
    d.ellipse([cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r], fill=(10, 16, 26, 225))

    drew_emoji = False
    emoji_path = _emoji_image_path(pd.get('emoji'))
    if emoji_path:
        try:
            em = Image.open(emoji_path).convert('RGBA')
            bbox = em.getbbox()
            if bbox:
                em = em.crop(bbox)
            target = int(avatar_size * 0.78)
            em = em.resize((target, target), Image.Resampling.LANCZOS)
            mask = Image.new('L', (target, target), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, target, target], fill=255)
            layer.paste(em, (cx - target // 2, cy - target // 2), mask)
            drew_emoji = True
        except Exception:
            drew_emoji = False
    if not drew_emoji:
        _icon_star(d, cx, cy, avatar_size * 0.44, rc + (235,))

    if is_shiny:
        for ang in (-35, 20, 145, 205):
            sx = cx + int(math.cos(math.radians(ang)) * r * 0.95)
            sy = cy + int(math.sin(math.radians(ang)) * r * 0.95)
            _icon_star(d, sx, sy, u(13), (255, 215, 0, 245))

    if stage > 1:
        bx = avatar_x + avatar_size - u(8)
        by = avatar_y + u(8)
        d.ellipse([bx - u(15), by - u(15), bx + u(15), by + u(15)], fill=(12, 18, 28, 245), outline=rc + (230,), width=u(2))
        d.text((bx, by), evo['icon'], font=f_small_b, fill=(255, 255, 255, 255), anchor='mm')

    name_y = avatar_y + u(22)
    d.text((name_x, name_y), fit_text(name, f_title, name_w), font=f_title, fill=(255, 255, 255, 250), anchor='lm')
    if is_shiny:
        nw = min(name_w, d.textlength(name, font=f_title))
        _icon_star(d, name_x + nw + u(13), name_y, u(16), (255, 215, 0, 245))

    badge_y = avatar_y + u(60)
    rarity_label = RARITY_NAMES.get(rarity, rarity).upper()
    bw = d.textlength(rarity_label, font=f_small_b) + u(18)
    d.rounded_rectangle([name_x, badge_y - u(11), name_x + bw, badge_y + u(11)], radius=u(9),
                        fill=rc + (50,), outline=rc + (185,), width=u(1))
    d.text((name_x + bw / 2, badge_y), rarity_label, font=f_small_b, fill=rc + (255,), anchor='mm')
    lv_label = f'УР. {level}'
    lv_x = name_x + bw + u(8)
    lv_w = d.textlength(lv_label, font=f_small_b) + u(16)
    d.rounded_rectangle([lv_x, badge_y - u(11), lv_x + lv_w, badge_y + u(11)], radius=u(9),
                        fill=(255, 255, 255, 18), outline=(255, 255, 255, 30), width=u(1))
    d.text((lv_x + lv_w / 2, badge_y), lv_label, font=f_small_b, fill=(215, 225, 240, 240), anchor='mm')

    ab_y = avatar_y + u(84)
    for i, line in enumerate(ability_lines):
        d.text((name_x, ab_y + i * u(17)), line, font=f_small, fill=(155, 175, 200, 230), anchor='lm')

    d.rounded_rectangle([M, xp_y, W - M, xp_y + xp_h], radius=u(14),
                        fill=(255, 255, 255, 12), outline=(255, 255, 255, 18), width=u(1))
    d.text((M + u(14), xp_y + u(15)), 'ОПЫТ', font=f_tiny_b, fill=(150, 170, 195, 230), anchor='lm')
    d.text((W - M - u(14), xp_y + u(15)), f'{xp} / {xp_mx}', font=f_tiny, fill=(150, 170, 195, 230), anchor='rm')
    bx1 = M + u(14)
    bx2 = W - M - u(14)
    bar_y = xp_y + xp_h - u(15)
    d.rounded_rectangle([bx1, bar_y, bx2, bar_y + u(7)], radius=u(3), fill=(255, 255, 255, 22))
    if xp_ratio > 0:
        fw = max(u(6), int((bx2 - bx1) * xp_ratio))
        d.rounded_rectangle([bx1, bar_y, bx1 + fw, bar_y + u(7)], radius=u(3), fill=rc + (230,))

    gap = u(10)
    cw = int((W - 2 * M - 2 * gap) / 3)
    stats = [
        (_icon_sword, 'АТАКА', eff_atk, (255, 122, 122), sk_atk),
        (_icon_shield, 'ЗАЩИТА', eff_def, (96, 214, 206), sk_def),
        (_icon_heart, 'HP', eff_hp, (255, 130, 170), sk_hp),
    ]
    for i, (icon_fn, label, val, col, bonus) in enumerate(stats):
        x = M + i * (cw + gap)
        d.rounded_rectangle([x, stat_y, x + cw, stat_y + stat_h], radius=u(16),
                            fill=(255, 255, 255, 13), outline=(255, 255, 255, 22), width=u(1))
        d.rounded_rectangle([x + u(12), stat_y + u(10), x + cw - u(12), stat_y + u(13)], radius=u(2), fill=col + (125,))
        icon_fn(d, x + cw // 2, stat_y + u(31), u(28), col + (240,))
        d.text((x + cw // 2, stat_y + u(59)), f'{val:,}', font=f_bold, fill=col + (255,), anchor='mm')
        d.text((x + cw // 2, stat_y + u(86)), label, font=f_tiny, fill=(140, 158, 182, 220), anchor='mm')
        if bonus:
            d.text((x + cw - u(10), stat_y + u(12)), f'+{bonus}', font=f_tiny_b, fill=(96, 220, 150, 240), anchor='ra')

    d.rounded_rectangle([M, cond_y, W - M, cond_y + cond_h], radius=u(16),
                        fill=(255, 255, 255, 10), outline=(255, 255, 255, 16), width=u(1))
    d.text((M + u(14), cond_y + u(14)), 'СОСТОЯНИЕ', font=f_tiny_b, fill=(150, 170, 195, 230), anchor='lm')
    inner_w = W - 2 * M - u(28)
    cond_gap = u(10)
    ccw = int((inner_w - 2 * cond_gap) / 3)
    cond_items = [
        ('Здоровье', cur_hp, max_hp, (255, 130, 170)),
        ('Сытость', hunger, 100, (255, 190, 90)),
        ('Настроение', mood, 100, (120, 200, 255)),
    ]
    for i, (label, val, mx, col) in enumerate(cond_items):
        x = M + u(14) + i * (ccw + cond_gap)
        ly = cond_y + u(36)
        d.text((x, ly), label, font=f_tiny, fill=(155, 175, 200, 225), anchor='lm')
        d.text((x + ccw, ly), f'{val}/{mx}', font=f_tiny, fill=(155, 175, 200, 225), anchor='rm')
        by = cond_y + u(52)
        d.rounded_rectangle([x, by, x + ccw, by + u(6)], radius=u(3), fill=(255, 255, 255, 22))
        ratio = min(1, max(0, val / mx if mx else 0))
        if ratio > 0:
            d.rounded_rectangle([x, by, x + max(u(5), int(ccw * ratio)), by + u(6)], radius=u(3), fill=col + (230,))

    d.rounded_rectangle([M, skills_y, W - M, skills_y + skills_h], radius=u(16),
                        fill=(255, 255, 255, 10), outline=(255, 255, 255, 16), width=u(1))
    d.text((M + u(14), skills_y + u(15)), 'НАВЫКИ', font=f_tiny_b, fill=(150, 170, 195, 230), anchor='lm')
    skill_data = [
        (_icon_sword, 'Атака', pet.get('skill_atk', 0) or 0, 10, (255, 122, 122)),
        (_icon_shield, 'Защита', pet.get('skill_def', 0) or 0, 10, (96, 214, 206)),
        (_icon_heart, 'Живучесть', pet.get('skill_hp', 0) or 0, 10, (255, 130, 170)),
        (_icon_crit, 'Крит', pet.get('skill_crit', 0) or 0, 5, (255, 210, 0)),
        (_icon_regen, 'Регенерация', pet.get('skill_regen', 0) or 0, 5, (96, 220, 150)),
    ]
    rows_top = skills_y + u(34)
    for i, (icon_fn, label, val, mx, col) in enumerate(skill_data):
        ry = rows_top + i * u(26)
        icon_fn(d, M + u(24), ry, u(15), col + (230,))
        d.text((M + u(40), ry), label, font=f_small, fill=(190, 205, 225, 230), anchor='lm')
        dot_r = u(3)
        dot_gap = u(5)
        dots_w = mx * (dot_r * 2 + dot_gap) - dot_gap
        sx = W - M - u(18) - dots_w
        for j in range(mx):
            dx = sx + j * (dot_r * 2 + dot_gap)
            if j < val:
                d.ellipse([dx, ry - dot_r, dx + dot_r * 2, ry + dot_r], fill=col + (240,))
            else:
                d.ellipse([dx, ry - dot_r, dx + dot_r * 2, ry + dot_r], fill=(255, 255, 255, 26))

    battle = [
        (_icon_trophy, 'Победы', str(wins), (96, 214, 160)),
        (_icon_skull, 'Поражения', str(losses), (255, 122, 122)),
        (_icon_chart, 'Винрейт', f'{wr}%', rc),
    ]
    for i, (icon_fn, label, val, col) in enumerate(battle):
        x = M + i * (cw + gap)
        d.rounded_rectangle([x, battle_y, x + cw, battle_y + battle_h], radius=u(14),
                            fill=(255, 255, 255, 10), outline=(255, 255, 255, 16), width=u(1))
        icon_fn(d, x + u(26), battle_y + battle_h // 2, u(26), col + (230,))
        d.text((x + u(50), battle_y + u(22)), val, font=f_bold, fill=col + (255,), anchor='lm')
        d.text((x + u(50), battle_y + u(46)), label, font=f_tiny, fill=(140, 158, 182, 220), anchor='lm')

    d.line([(M, footer_y), (W - M, footer_y)], fill=(255, 255, 255, 18), width=u(1))
    foot = f"PETWORLD  •  {RARITY_NAMES.get(rarity, rarity)}  •  ID {pet.get('pet_id', '—')}"
    d.text((W // 2, footer_y + u(20)), foot, font=f_tiny, fill=(90, 110, 140, 220), anchor='mm')

    img = Image.alpha_composite(base, layer).convert('RGB')
    buf = BytesIO()
    img.save(buf, format='PNG', optimize=True)
    buf.seek(0)
    return buf

EGG_TYPES = {
    "common_egg":   {"emoji":"🥚","name":"Обычное яйцо",    "price":250, "stars":0,
                     "pool":{"dog":20,"cat":20,"rabbit":15,"hamster":15,"parrot":10,"turtle":10,"penguin":7,"capybara":3}},
    "rare_egg":     {"emoji":"🪺","name":"Редкое яйцо",     "price":850, "stars":0,
                     "pool":{"fox":18,"wolf":18,"bear":14,"tiger":14,"shark":12,"eagle":10,"snow_leopard":8,"panda":4,"gorilla":2}},
    "legendary_egg":{"emoji":"🌟","name":"Легендарное яйцо","price":2500,"stars":0,
                     "pool":{"dragon":22,"phoenix":22,"unicorn":18,"kraken":15,"cerberus":13,"void_dragon":6,"god_phoenix":4}},
    "dark_egg":     {"emoji":"🖤","name":"Тёмное яйцо",     "price":5000,"stars":0,
                     "pool":{"void_dragon":35,"god_phoenix":30,"titan":25,"cosmos":7,"omega":3}},
    "crystal_egg":  {"emoji":"💎","name":"Кристальное яйцо","price":3500,"stars":0,
                     "pool":{"dragon":20,"phoenix":20,"unicorn":20,"kraken":20,"cerberus":20}},
    "star_egg":     {"emoji":"⭐","name":"Звёздное яйцо",   "price":0,   "stars":5,
                     "pool":{"void_dragon":25,"god_phoenix":25,"titan":20,"cosmos":20,"omega":10}},
    "omega_egg":    {"emoji":"♾️","name":"Яйцо Омеги",      "price":0,   "stars":15,
                     "pool":{"omega":100}},
    "zeus_egg":     {"emoji":"⚡","name":"Яйцо Зевса",      "price":0,   "stars":100,
                     "pool":{"zeus":100}},
    "season_egg":   {"emoji":"🎫","name":"Яйцо Эры Титанов", "price":20000, "stars":0,
                     "pool":{"frost_queen":12,"sea_dragon":12,"solar_lion":12,"lunar_fox":12,"ice_phoenix":10,"void_reaper":10,"abyss_hydra":8,"nebula_whale":8,"dread_knight":6,"star_golem":10}},
    "titan_egg":    {"emoji":"🏛️","name":"Яйцо Титанов",     "price":0,   "stars":50,
                     "pool":{"titan_king":20,"chronos":20,"celestial_dragon":20,"golden_griffin":15,"astral_titan":10,"void_titan":7,"inferno_titan":4,"frost_titan":2,"storm_titan":1,"primordial_wyrm":1}},
}

WEAPONS = {
    "stick":        {"emoji":"🪵","name":"Дубина",          "price":50,   "stars":0,"atk_bonus":5},
    "dagger":       {"emoji":"🗡️","name":"Кинжал",          "price":120,  "stars":0,"atk_bonus":10},
    "sword":        {"emoji":"⚔️","name":"Меч",              "price":200,  "stars":0,"atk_bonus":15},
    "axe":          {"emoji":"🪓","name":"Топор",            "price":180,  "stars":0,"atk_bonus":18},
    "bow":          {"emoji":"🏹","name":"Лук",              "price":220,  "stars":0,"atk_bonus":12},
    "spear":        {"emoji":"🗡️","name":"Копьё",            "price":250,  "stars":0,"atk_bonus":20},
    "magic_staff":  {"emoji":"🪄","name":"Магический посох", "price":400,  "stars":0,"atk_bonus":25},
    "fire_sword":   {"emoji":"🔥","name":"Огненный меч",     "price":600,  "stars":0,"atk_bonus":30},
    "ice_blade":    {"emoji":"❄️","name":"Ледяной клинок",   "price":650,  "stars":0,"atk_bonus":28},
    "thunder_axe":  {"emoji":"⚡","name":"Громовой топор",   "price":700,  "stars":0,"atk_bonus":35},
    "shadow_dagger":{"emoji":"🌑","name":"Теневой кинжал",   "price":800,  "stars":0,"atk_bonus":32},
    "dragon_lance": {"emoji":"🐉","name":"Копьё дракона",    "price":1200, "stars":0,"atk_bonus":45},
    "excalibur":    {"emoji":"✨","name":"Экскалибур",       "price":1000, "stars":0,"atk_bonus":40},
    "void_blade":   {"emoji":"💜","name":"Клинок Бездны",    "price":2000, "stars":0,"atk_bonus":60},
    "titan_hammer": {"emoji":"🔨","name":"Молот Титана",     "price":2500, "stars":0,"atk_bonus":70},
    "star_sword":   {"emoji":"🌟","name":"Звёздный меч",     "price":0,    "stars":10,"atk_bonus":85},
    "omega_blade":  {"emoji":"♾️","name":"Клинок Омеги",     "price":0,    "stars":25,"atk_bonus":120},
    "zeus_bolt":    {"emoji":"⚡","name":"Молния Зевса",     "price":0,    "stars":25,"atk_bonus":150},
    "storm_blade":  {"emoji":"⚡","name":"Штормовой клинок",  "price":1800, "stars":0, "atk_bonus":55},
    "inferno_scythe":{"emoji":"🔥","name":"Инфернальная коса", "price":3000, "stars":0, "atk_bonus":70},
    "frost_spear":  {"emoji":"❄️","name":"Ледяное копьё",     "price":2600, "stars":0, "atk_bonus":65},
    "solar_bow":    {"emoji":"☀️","name":"Солнечный лук",     "price":4000, "stars":0, "atk_bonus":75},
    "lunar_daggers":{"emoji":"🌙","name":"Лунные клинки",     "price":3500, "stars":0, "atk_bonus":60},
    "void_katana":  {"emoji":"🌑","name":"Катана Бездны",     "price":5000, "stars":0, "atk_bonus":85},
    "titan_axe":    {"emoji":"🪓","name":"Топор Титана",      "price":7000, "stars":0, "atk_bonus":95},
    "abyss_staff":  {"emoji":"🐙","name":"Посох Бездны",      "price":9000, "stars":0, "atk_bonus":105},
    "nebula_scythe":{"emoji":"🌌","name":"Коса Небулы",       "price":12000,"stars":0, "atk_bonus":115},
    "chrono_blade": {"emoji":"⏳","name":"Клинок Хроноса",    "price":0,    "stars":20,"atk_bonus":125},
    "celestial_sword":{"emoji":"✨","name":"Небесный меч",    "price":0,    "stars":40,"atk_bonus":140},
    "primordial_blade":{"emoji":"🌌","name":"Клинок Начала",  "price":0,    "stars":60,"atk_bonus":160},
}

ARMOR = {
    "cloth":        {"emoji":"👕","name":"Тряпичная броня", "price":30,   "stars":0,"def_bonus":3},
    "leather":      {"emoji":"🧥","name":"Кожаная броня",  "price":80,   "stars":0,"def_bonus":8},
    "chainmail":    {"emoji":"⛓️","name":"Кольчуга",        "price":250,  "stars":0,"def_bonus":18},
    "plate":        {"emoji":"🛡️","name":"Латная броня",   "price":500,  "stars":0,"def_bonus":30},
    "magic_robe":   {"emoji":"🔮","name":"Магический плащ","price":600,  "stars":0,"def_bonus":25},
    "fire_armor":   {"emoji":"🔥","name":"Огненная броня", "price":700,  "stars":0,"def_bonus":35},
    "dragon_scale": {"emoji":"🐉","name":"Чешуя дракона",  "price":1200, "stars":0,"def_bonus":50},
    "void_armor":   {"emoji":"💜","name":"Броня Бездны",   "price":2000, "stars":0,"def_bonus":70},
    "titan_shell":  {"emoji":"⚡","name":"Панцирь Титана", "price":2500, "stars":0,"def_bonus":80},
    "star_armor":   {"emoji":"🌟","name":"Звёздная броня", "price":0,    "stars":10,"def_bonus":95},
    "omega_shield": {"emoji":"♾️","name":"Щит Омеги",      "price":0,    "stars":25,"def_bonus":130},
    "zeus_aegis":   {"emoji":"⚡","name":"Эгида Зевса",    "price":0,    "stars":25,"def_bonus":180},
    "storm_mail":   {"emoji":"⚡","name":"Штормовая броня", "price":1800, "stars":0, "def_bonus":55},
    "inferno_plate":{"emoji":"🔥","name":"Инфернальная броня","price":3000, "stars":0, "def_bonus":70},
    "frost_robe":   {"emoji":"❄️","name":"Ледяная мантия",  "price":2600, "stars":0, "def_bonus":65},
    "solar_aegis":  {"emoji":"☀️","name":"Солнечная эгида", "price":4000, "stars":0, "def_bonus":75},
    "lunar_cloak":  {"emoji":"🌙","name":"Лунный плащ",     "price":3500, "stars":0, "def_bonus":60},
    "void_shield":  {"emoji":"🌑","name":"Щит Бездны",      "price":5000, "stars":0, "def_bonus":85},
    "titan_armor":  {"emoji":"🛡️","name":"Броня Титана",    "price":7000, "stars":0, "def_bonus":95},
    "abyss_guard":  {"emoji":"🐙","name":"Страж Бездны",    "price":9000, "stars":0, "def_bonus":105},
    "nebula_robe":  {"emoji":"🌌","name":"Мантия Небулы",   "price":12000,"stars":0, "def_bonus":115},
    "chrono_shield":{"emoji":"⏳","name":"Щит Хроноса",     "price":0,    "stars":20,"def_bonus":125},
    "celestial_armor":{"emoji":"✨","name":"Небесная броня","price":0,    "stars":40,"def_bonus":140},
    "primordial_armor":{"emoji":"🌌","name":"Броня Начала", "price":0,    "stars":60,"def_bonus":160},
}

ITEMS = {
    "food":         {"emoji":"🍗","name":"Корм",           "price":10,  "hunger":25},
    "premium":      {"emoji":"🥩","name":"Премиум корм",   "price":30,  "hunger":40,"mood":10},
    "cake":         {"emoji":"🎂","name":"Торт",           "price":60,  "hunger":30,"mood":40},
    "steak":        {"emoji":"🥩","name":"Стейк",          "price":80,  "hunger":50,"mood":15},
    "ball":         {"emoji":"🧸","name":"Мячик",          "price":20,  "mood":15},
    "firstaid":     {"emoji":"💊","name":"Аптечка",        "price":30,  "health":30},
    "superheal":    {"emoji":"💉","name":"Суперлечение",   "price":100, "health":100},
    "vitamins":     {"emoji":"🫐","name":"Витамины",       "price":50,  "health":20,"mood":10},
    "magic_potion": {"emoji":"🧪","name":"Зелье магии",    "price":150, "health":50,"mood":30,"hunger":30},
    "elixir":       {"emoji":"⚗️","name":"Боевое зелье",   "price":80,  "battle_atk":25},
    "berserker":    {"emoji":"🔴","name":"Зелье берсерка", "price":120, "battle_atk":40},
    "shield_charm": {"emoji":"🔮","name":"Защитный амулет","price":90,  "battle_def":30},
    "iron_wall":    {"emoji":"🧱","name":"Железная стена", "price":140, "battle_def":50},
    "revive":       {"emoji":"💫","name":"Воскрешение",    "price":200, "battle_revive":True},
    "poison":       {"emoji":"☠️","name":"Яд",             "price":110, "battle_poison":15},
    "thunder_scroll":{"emoji":"📜","name":"Свиток молнии", "price":130, "battle_atk":35},
    "vip_pass":     {"emoji":"👑","name":"VIP Пропуск",    "price":0,   "stars":20,"vip":True},
    "zeus_set":     {"emoji":"⚡","name":"СЕТ ЗЕВСА",      "price":0,   "stars":100,"is_zeus_set":True},
}
BATTLE_ITEMS = {"elixir","berserker","shield_charm","iron_wall","revive","poison","thunder_scroll"}

EVENT_POOL = [
    {"id":"double_xp",    "name":"🎓 День знаний",        "desc":"XP x2 на 6 часов!",              "duration_h":6,"mult_xp":2.0,"mult_coins":1.0},
    {"id":"double_coins", "name":"💰 Золотая лихорадка",  "desc":"Монеты x2 на 4 часа!",           "duration_h":4,"mult_xp":1.0,"mult_coins":2.0},
    {"id":"festival",     "name":"🎪 Фестиваль питомцев", "desc":"XP x3 и монеты x1.5!",           "duration_h":3,"mult_xp":3.0,"mult_coins":1.5},
    {"id":"egg_event",    "name":"🥚 Яичный ивент",       "desc":"Шанс яйца x5!",                  "duration_h":5,"mult_xp":1.0,"mult_coins":1.0,"egg_mult":5},
    {"id":"dark_night",   "name":"🌑 Тёмная ночь",        "desc":"Тёмные яйца -50% цена!",         "duration_h":4,"mult_xp":1.5,"mult_coins":1.0},
    {"id":"battle_fever", "name":"🔥 Боевая лихорадка",   "desc":"Награды за победу x3!",          "duration_h":3,"mult_xp":3.0,"mult_coins":3.0},
    {"id":"clan_wars",    "name":"⚔️ Клановые войны",     "desc":"Очки клана x2!",                 "duration_h":2,"mult_xp":2.0,"mult_coins":2.0},
]
STARS_TO_COINS = 1000

# ═══════════════════════════════════════════════════
# МНОЖИТЕЛИ МИРОВ (PSX прогрессия)
# ═══════════════════════════════════════════════════
WORLD_MULTIPLIERS = {
    1: 1, 2: 2, 3: 4, 4: 8, 5: 15,
    6: 25, 7: 35, 8: 45, 9: 50, 10: 50,
}

# ═══════════════════════════════════════════════════
# ГЛОБАЛЬНЫЙ БОСС
# ═══════════════════════════════════════════════════
GLOBAL_BOSS_POOL = [
    {"name": "🐉 Древний Дракон",  "hp": 5000, "atk": 80,  "def": 40,  "reward_crystals": (50, 150), "rare_pet": "dragon"},
    {"name": "🦑 Тёмный Кракен",   "hp": 7000, "atk": 100, "def": 50,  "reward_crystals": (80, 200), "rare_pet": "kraken"},
    {"name": "☄️ Метеор-Титан",    "hp": 10000,"atk": 130, "def": 70,  "reward_crystals": (120,300), "rare_pet": "titan"},
    {"name": "🌌 Бездонная Бездна","hp": 15000,"atk": 160, "def": 90,  "reward_crystals": (200,500), "rare_pet": "cosmos"},
    {"name": "♾️ Омега-Разрушитель","hp":20000, "atk": 200, "def": 120, "reward_crystals": (300,700), "rare_pet": "omega"},
]

# Состояние глобального босса (in-memory, сбрасывается при рестарте)
_active_boss = None   # dict с ключами: data, hp, attackers={uid: damage}
_boss_lock = None

def get_boss_lock():
    global _boss_lock
    if _boss_lock is None:
        _boss_lock = asyncio.Lock()
    return _boss_lock

# ═══════════════════════════════════════════════════
# ИСПРАВЛЕНИЕ: Race Condition / Multi-click защита
# ═══════════════════════════════════════════════════
# Проблема была в том, что баланс/кулдаун проверялись в начале
# асинхронного вызова, а списание/запись кулдауна — в конце, после
# множества await-вызовов. Параллельные запросы (быстрый кликер/скрипт)
# проходили проверку на одних и тех же "старых" данных одновременно.
# Решение: для каждого пользователя выдаём persistent asyncio.Lock,
# которым оборачиваем критическую секцию "проверка → списание → КД"
# в /work, /duel, /adventure и клановом депозите — теперь параллельные
# запросы одного игрока обрабатываются строго последовательно.
_user_locks: dict[int, asyncio.Lock] = {}

def get_user_lock(uid: int) -> asyncio.Lock:
    lock = _user_locks.get(uid)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[uid] = lock
    return lock

# ═══════════════════════════════════════════════════
# БД
# ═══════════════════════════════════════════════════

async def init_db():
    global _db_conn
    # Открываем соединение и включаем WAL
    _db_conn = await _orig_aiosqlite_connect(DB_PATH, timeout=30)
    _db_conn.row_factory = aiosqlite.Row
    await _db_conn.execute("PRAGMA journal_mode=WAL")
    await _db_conn.execute("PRAGMA synchronous=NORMAL")
    await _db_conn.execute("PRAGMA cache_size=10000")
    await _db_conn.execute("PRAGMA temp_store=MEMORY")
    await _db_conn.execute("PRAGMA busy_timeout=15000")
    await _db_conn.commit()

    await _db_conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            coins INTEGER DEFAULT 300, last_daily TEXT, last_work TEXT,
            last_adventure TEXT, last_casino TEXT, last_battle TEXT,
            is_banned INTEGER DEFAULT 0, total_earned INTEGER DEFAULT 0,
            vip_until TEXT, clan_id INTEGER, world INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS pets (
            pet_id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL, name TEXT NOT NULL, pet_type TEXT NOT NULL,
            health INTEGER DEFAULT 100, max_health INTEGER DEFAULT 100,
            hunger INTEGER DEFAULT 80, mood INTEGER DEFAULT 90,
            level INTEGER DEFAULT 1, xp INTEGER DEFAULT 0, xp_to_next INTEGER DEFAULT 100,
            is_sleeping INTEGER DEFAULT 0, weapon TEXT, armor TEXT,
            active INTEGER DEFAULT 1, wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
            for_sale INTEGER DEFAULT 0, sale_price INTEGER DEFAULT 0,
            last_updated TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS inventory (
            inv_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, item_id TEXT NOT NULL, quantity INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS tasks (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, task_type TEXT NOT NULL,
            description TEXT NOT NULL, reward_coins INTEGER DEFAULT 50,
            progress INTEGER DEFAULT 0, required INTEGER DEFAULT 1, completed INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS battle_requests (
            req_id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER NOT NULL, to_user INTEGER NOT NULL,
            status TEXT DEFAULT 'pending', created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS clans (
            clan_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL, tag TEXT UNIQUE NOT NULL,
            owner_id INTEGER NOT NULL, description TEXT DEFAULT '',
            level INTEGER DEFAULT 1, coins INTEGER DEFAULT 0, wins INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS clan_members (
            member_id INTEGER PRIMARY KEY AUTOINCREMENT,
            clan_id INTEGER NOT NULL, user_id INTEGER NOT NULL UNIQUE,
            role TEXT DEFAULT 'member'
        );
        CREATE TABLE IF NOT EXISTS trade_offers (
            offer_id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER NOT NULL, pet_id INTEGER NOT NULL,
            price INTEGER DEFAULT 0, status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS pet_trades (
            trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER NOT NULL, to_user INTEGER NOT NULL,
            from_pet_id INTEGER NOT NULL, to_pet_id INTEGER,
            extra_coins INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS transfers (
            tr_id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER, to_user INTEGER, amount INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT, name TEXT, description TEXT,
            mult_xp REAL DEFAULT 1.0, mult_coins REAL DEFAULT 1.0,
            extra TEXT DEFAULT '{}', started_at TEXT, ends_at TEXT, active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            reward_coins INTEGER DEFAULT 0,
            reward_pet TEXT DEFAULT NULL,
            uses_left INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS promo_uses (
            use_id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL, user_id INTEGER NOT NULL,
            UNIQUE(code, user_id)
        );
        CREATE TABLE IF NOT EXISTS gift_requests (
            req_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_id TEXT NOT NULL,
            item_name TEXT NOT NULL,
            stars INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS tournaments (
            tournament_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, status TEXT DEFAULT 'registration',
            prize INTEGER DEFAULT 1000, max_players INTEGER DEFAULT 8,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS tournament_players (
            tp_id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER, user_id INTEGER,
            UNIQUE(tournament_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS world_progress (
            user_id INTEGER NOT NULL,
            world_id TEXT NOT NULL,
            currency INTEGER DEFAULT 0,
            mastery INTEGER DEFAULT 0,
            boss_kills INTEGER DEFAULT 0,
            last_boss TEXT,
            PRIMARY KEY (user_id, world_id)
        );
        CREATE TABLE IF NOT EXISTS bot_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS bot_chats (
            chat_id INTEGER PRIMARY KEY,
            chat_type TEXT,
            title TEXT,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)
    await _db_conn.commit()

    # Миграции — добавляем колонки если их нет (для существующих БД)
    migrations = [
        "ALTER TABLE users ADD COLUMN last_battle TEXT",
        "ALTER TABLE users ADD COLUMN last_casino TEXT",
        "ALTER TABLE users ADD COLUMN last_adventure TEXT",
        "ALTER TABLE users ADD COLUMN vip_until TEXT",
        "ALTER TABLE users ADD COLUMN clan_id INTEGER",
        "ALTER TABLE users ADD COLUMN total_earned INTEGER DEFAULT 0",
        "ALTER TABLE pets ADD COLUMN wins INTEGER DEFAULT 0",
        "ALTER TABLE pets ADD COLUMN losses INTEGER DEFAULT 0",
        "ALTER TABLE pets ADD COLUMN for_sale INTEGER DEFAULT 0",
        "ALTER TABLE pets ADD COLUMN sale_price INTEGER DEFAULT 0",
        "ALTER TABLE pets ADD COLUMN weapon TEXT",
        "ALTER TABLE pets ADD COLUMN armor TEXT",
        "ALTER TABLE users ADD COLUMN world INTEGER DEFAULT 1",
        "ALTER TABLE tournament_players ADD COLUMN eliminated INTEGER DEFAULT 0",
        # Новые колонки v6
        "ALTER TABLE users ADD COLUMN crystals INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN rebirths INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN rebirth_bonus REAL DEFAULT 1.0",
        "ALTER TABLE users ADD COLUMN last_idle TEXT",
        "ALTER TABLE pets ADD COLUMN is_shiny INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN duel_streak INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN ref_code TEXT",
        "ALTER TABLE users ADD COLUMN referred_by INTEGER",
        "ALTER TABLE users ADD COLUMN ref_count INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN battle_rating INTEGER DEFAULT 1000",
        "ALTER TABLE users ADD COLUMN last_explore TEXT",  # отдельный КД для локаций
        # ── v7.0 ──
        "ALTER TABLE users ADD COLUMN season_tokens INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN season_pass INTEGER DEFAULT 0",  # 0=free, 1=premium
        "ALTER TABLE users ADD COLUMN season_level INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN season_id INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN title TEXT DEFAULT NULL",        # активный титул
        "ALTER TABLE users ADD COLUMN daily_quests TEXT DEFAULT NULL", # JSON ежедневных квестов
        "ALTER TABLE users ADD COLUMN last_daily_quest TEXT DEFAULT NULL",
        "ALTER TABLE pets ADD COLUMN skill_points INTEGER DEFAULT 0",
        "ALTER TABLE pets ADD COLUMN skill_atk INTEGER DEFAULT 0",
        "ALTER TABLE pets ADD COLUMN skill_def INTEGER DEFAULT 0",
        "ALTER TABLE pets ADD COLUMN skill_hp INTEGER DEFAULT 0",
        "ALTER TABLE pets ADD COLUMN skill_crit INTEGER DEFAULT 0",
        "ALTER TABLE pets ADD COLUMN skill_regen INTEGER DEFAULT 0",
        "CREATE TABLE IF NOT EXISTS achievements (ach_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, ach_type TEXT NOT NULL, earned_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,ach_type))",
        "CREATE TABLE IF NOT EXISTS auction_lots (lot_id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id INTEGER NOT NULL, pet_id INTEGER NOT NULL, start_price INTEGER NOT NULL, current_price INTEGER NOT NULL, current_buyer INTEGER, ends_at TEXT NOT NULL, status TEXT DEFAULT 'active', created_at TEXT DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS season_rewards_claimed (rc_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, season_level INTEGER NOT NULL, track TEXT NOT NULL, UNIQUE(user_id,season_level,track))",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_ref_code ON users(ref_code)",
        "ALTER TABLE users ADD COLUMN adventure_count INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN fuse_count INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN boss_kills_json TEXT DEFAULT '{}'",
        "ALTER TABLE users ADD COLUMN last_battle_pve TEXT",  # отдельный КД для боя с ИИ на арене
        "ALTER TABLE pets ADD COLUMN evolution INTEGER DEFAULT 1",
        "ALTER TABLE users ADD COLUMN last_wheel TEXT",
        "ALTER TABLE users ADD COLUMN wheel_spins INTEGER DEFAULT 0",
        # ── ИСПРАВЛЕНИЕ БАГА "кража питомцев в трейде": теперь "Принять без
        #    обмена" доступно получателю только если ИНИЦИАТОР сам разрешил
        #    дарение при создании трейда. По умолчанию (0) обмен обязателен —
        #    получатель не может забрать пета, не отдав свой пета или доплату.
        "ALTER TABLE pet_trades ADD COLUMN allow_gift INTEGER DEFAULT 0",
    ]
    for sql in migrations:
        try:
            await _db_conn.execute(sql)
        except Exception:
            pass  # колонка уже существует
    await _db_conn.commit()

async def _add_item_raw(db, uid, item_id, qty=1):
    async with db.execute("SELECT inv_id FROM inventory WHERE user_id=? AND item_id=?",(uid,item_id)) as c:
        r = await c.fetchone()
    if r: await db.execute("UPDATE inventory SET quantity=quantity+? WHERE inv_id=?",(qty,r[0]))
    else: await db.execute("INSERT INTO inventory (user_id,item_id,quantity) VALUES (?,?,?)",(uid,item_id,qty))

async def get_or_create_user(uid, username="", first_name=""):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id=?",(uid,)) as c:
            row = await c.fetchone()
        if row:
            if username or first_name:
                await db.execute("UPDATE users SET username=COALESCE(NULLIF(?,''),username),first_name=COALESCE(NULLIF(?,''),first_name) WHERE user_id=?",
                                 (username,first_name,uid))
                await db.commit()
            return dict(row)
        await db.execute("INSERT INTO users (user_id,username,first_name,season_id) VALUES (?,?,?,?)",
                         (uid, username, first_name, SEASON_ID))
        await db.commit()
        await _add_item_raw(db,uid,"common_egg",1)
        await db.commit()
        async with db.execute("SELECT * FROM users WHERE user_id=?",(uid,)) as c:
            return dict(await c.fetchone())

async def get_user(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id=?",(uid,)) as c:
            r = await c.fetchone()
        return dict(r) if r else None

async def update_coins(uid, delta):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET coins=MAX(0,coins+?),total_earned=CASE WHEN ?>0 THEN total_earned+? ELSE total_earned END WHERE user_id=?",
                         (delta,delta,max(0,delta),uid))
        await db.commit()

async def update_crystals(uid, delta):
    """Добавить/убрать кристаллы (минимум 0)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET crystals=MAX(0,crystals+?) WHERE user_id=?", (delta, uid))
        await db.commit()

async def get_top_crystals(limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users ORDER BY crystals DESC LIMIT ?", (limit,)) as c:
            return [dict(r) for r in await c.fetchall()]

async def get_top_rebirths(limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users ORDER BY rebirths DESC LIMIT ?", (limit,)) as c:
            return [dict(r) for r in await c.fetchall()]

async def do_rebirth(uid):
    """Выполнить ребёрт: сброс монет, +1 ребёрт, +50% к бонусу наград."""
    user = await get_user(uid)
    if not user: return False, "Пользователь не найден"
    if user['coins'] < 1_000_000:
        return False, f"Нужно 1,000,000💰! У тебя {user['coins']:,}💰"
    new_rebirths = (user.get('rebirths') or 0) + 1
    # Накапливающийся бонус: 1.5^rebirths (каждый ребёрт +50%)
    new_bonus = round(1.5 ** new_rebirths, 4)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET coins=300, rebirths=?, rebirth_bonus=? WHERE user_id=?",
            (new_rebirths, new_bonus, uid)
        )
        await db.commit()
    return True, new_rebirths

async def set_ts(uid, field):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {field}=? WHERE user_id=?",(datetime.now().isoformat(),uid))
        await db.commit()

async def is_vip(uid):
    user = await get_user(uid)
    if not user or not user.get('vip_until'): return False
    try: return datetime.fromisoformat(user['vip_until']) > datetime.now()
    except: return False

async def ban_user(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_banned=1 WHERE user_id=?",(uid,)); await db.commit()

async def unban_user(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_banned=0 WHERE user_id=?",(uid,)); await db.commit()

async def get_active_pet(owner_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM pets WHERE owner_id=? AND active=1 ORDER BY level DESC LIMIT 1",(owner_id,)) as c:
            r = await c.fetchone()
        return dict(r) if r else None

async def get_all_pets(owner_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM pets WHERE owner_id=? ORDER BY level DESC",(owner_id,)) as c:
            return [dict(r) for r in await c.fetchall()]

async def get_pet_by_id(pid, owner_id=None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        q = "SELECT * FROM pets WHERE pet_id=?" + (" AND owner_id=?" if owner_id else "")
        args = (pid,owner_id) if owner_id else (pid,)
        async with db.execute(q,args) as c:
            r = await c.fetchone()
        return dict(r) if r else None

async def create_pet(owner_id, name, pet_type, is_shiny=False):
    p = PETS[pet_type]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE pets SET active=0 WHERE owner_id=?",(owner_id,))
        await db.execute(
            "INSERT INTO pets (owner_id,name,pet_type,health,max_health,is_shiny) VALUES (?,?,?,?,?,?)",
            (owner_id, name, pet_type, p['hp'], p['hp'], 1 if is_shiny else 0)
        )
        await db.commit()
    # Ачивки на питомцев
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM pets WHERE owner_id=?", (owner_id,)) as c:
            pet_count = (await c.fetchone())[0]
    await check_and_grant_achievement(owner_id, "first_pet")
    if pet_count >= 10:
        await check_and_grant_achievement(owner_id, "pet_10")
    return await get_active_pet(owner_id)

async def update_pet(pid, **kw):
    if not kw: return
    sets=", ".join(f"{k}=?" for k in kw)
    vals=list(kw.values())+[datetime.now().isoformat(),pid]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE pets SET {sets},last_updated=? WHERE pet_id=?",vals)
        await db.commit()

async def set_active_pet(owner_id, pid):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE pets SET active=0 WHERE owner_id=?",(owner_id,))
        await db.execute("UPDATE pets SET active=1 WHERE pet_id=? AND owner_id=?",(pid,owner_id))
        await db.commit()

def xp_to_next_for_level(level: int) -> int:
    """
    Возвращает корректное значение xp_to_next для питомца указанного уровня,
    используя ту же прогрессию, что и add_xp() (старт 100, x1.5 за уровень).
    Нужно при ручном создании питомца не с 1-го уровня (например, после
    слияния/фьюза) — иначе xp_to_next остаётся равным дефолту 100, и питомец
    высокого уровня левелапается почти мгновенно от любого опыта, что выглядит
    как "сброс уровня" в интерфейсе.
    """
    level = max(1, int(level))
    xtn = 100
    for _ in range(level - 1):
        xtn = int(xtn * 1.5)
    return max(1, xtn)

async def add_xp(pid, amount):
    event = await get_active_event()
    if event: amount = int(amount * event.get('mult_xp',1.0))
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM pets WHERE pet_id=?",(pid,)) as c:
            pet = await c.fetchone()
        if not pet: return 1, False
        pet=dict(pet); xp=pet['xp']+amount; lvl=pet['level']; xtn=pet['xp_to_next']
        leveled=False; levels_gained=0
        while xp>=xtn: xp-=xtn; lvl+=1; xtn=int(xtn*1.5); leveled=True; levels_gained+=1
        new_sp = (pet.get('skill_points') or 0) + levels_gained  # +1 очко скилла за уровень
        await db.execute("UPDATE pets SET xp=?,level=?,xp_to_next=?,max_health=?,skill_points=? WHERE pet_id=?",
                         (xp,lvl,xtn,PETS[pet['pet_type']]['hp']+(lvl-1)*10,new_sp,pid))
        await db.commit()
    return lvl, leveled

async def decay_all_pets(bot=None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Сначала получаем питомцев, которые умрут (0 HP и 0 hunger)
        async with db.execute("""
            SELECT pet_id, owner_id, name, pet_type 
            FROM pets 
            WHERE health <= 0 AND hunger <= 0 AND is_sleeping = 0
        """) as c:
            dying_pets = [dict(r) for r in await c.fetchall()]
        
        # Удаляем мёртвых питомцев
        if dying_pets:
            await db.execute("DELETE FROM pets WHERE health <= 0 AND hunger <= 0 AND is_sleeping = 0")
            await db.commit()
        
        # Обычный decay
        await db.execute("""
            UPDATE pets 
            SET hunger = MAX(0, hunger - 5),
                mood = MAX(0, mood - 3),
                health = CASE WHEN hunger <= 10 THEN MAX(0, health - 5) ELSE health END
            WHERE is_sleeping = 0
        """)
        await db.commit()
        
    # Уведомляем владельцев об умерших питомцах
    if dying_pets and bot:
        for pet in dying_pets:
            pd = PETS.get(pet['pet_type'], PETS['dog'])
            try:
                await bot.send_message(
                    pet['owner_id'],
                    f"💀 <b>Твой питомец умер!</b>\n\n"
                    f"{pd['emoji']} <b>{pet['name']}</b> умер от голода и ран...\n"
                    f"Не забывай кормить и лечить своих питомцев!",
                    parse_mode="HTML"
                )
            except:
                pass

async def add_item(uid, item_id, qty=1):
    async with aiosqlite.connect(DB_PATH) as db:
        await _add_item_raw(db,uid,item_id,qty); await db.commit()

async def remove_item(uid, item_id, qty=1):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT inv_id,quantity FROM inventory WHERE user_id=? AND item_id=?",(uid,item_id)) as c:
            r = await c.fetchone()
        if not r or r[1]<qty: return False
        await db.execute("UPDATE inventory SET quantity=quantity-? WHERE inv_id=?",(qty,r[0]))
        await db.commit(); return True

async def has_item(uid, item_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT quantity FROM inventory WHERE user_id=? AND item_id=?",(uid,item_id)) as c:
            r = await c.fetchone()
        return r[0] if r else 0

async def get_inventory(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM inventory WHERE user_id=? AND quantity>0 ORDER BY item_id",(uid,)) as c:
            return [dict(r) for r in await c.fetchall()]

async def assign_tasks(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND completed=0",(uid,)) as c:
            if (await c.fetchone())[0]>0: return
        templates=[
            ("feed","Покорми питомца 3 раза",60,3),("play","Поиграй 5 раз",100,5),
            ("work","Заработай монеты 3 раза",120,3),("adventure","Приключение 2 раза",150,2),
            ("hatch","Открой яйцо",200,1),("win_battle","Победи в битве",250,1),
        ]
        for t in random.sample(templates,3):
            await db.execute("INSERT INTO tasks (user_id,task_type,description,reward_coins,required) VALUES (?,?,?,?,?)",
                             (uid,t[0],t[1],t[2],t[3]))
        await db.commit()

async def progress_task(uid, ttype, amount=1):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Атомарно получаем задание и обновляем прогресс одной транзакцией
        async with db.execute(
            "SELECT * FROM tasks WHERE user_id=? AND task_type=? AND completed=0 LIMIT 1",
            (uid, ttype)
        ) as c:
            task = await c.fetchone()
        if not task:
            return None
        task = dict(task)
        np = task['progress'] + amount
        done = np >= task['required']
        if done:
            # Используем WHERE completed=0 для защиты от двойного завершения
            await db.execute(
                "UPDATE tasks SET progress=?, completed=1 WHERE task_id=? AND completed=0",
                (task['required'], task['task_id'])
            )
        else:
            await db.execute(
                "UPDATE tasks SET progress=? WHERE task_id=? AND completed=0",
                (np, task['task_id'])
            )
        await db.commit()
        # Проверяем что UPDATE действительно применился (защита от гонки)
        async with db.execute(
            "SELECT completed, progress FROM tasks WHERE task_id=?", (task['task_id'],)
        ) as c:
            row = await c.fetchone()
        if row and row[0] == 1 and done:
            return task  # задание выполнено
        return None

async def get_tasks(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tasks WHERE user_id=? AND completed=0",(uid,)) as c:
            return [dict(r) for r in await c.fetchall()]

async def get_active_event():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM events WHERE active=1 AND ends_at>? ORDER BY event_id DESC LIMIT 1",
                               (datetime.now().isoformat(),)) as c:
            r = await c.fetchone()
        return dict(r) if r else None

async def start_event(ev):
    ends_at=(datetime.now()+timedelta(hours=ev['duration_h'])).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE events SET active=0")
        await db.execute("INSERT INTO events (event_type,name,description,mult_xp,mult_coins,started_at,ends_at) VALUES (?,?,?,?,?,?,?)",
                         (ev['id'],ev['name'],ev['desc'],ev.get('mult_xp',1.0),ev.get('mult_coins',1.0),
                          datetime.now().isoformat(),ends_at))
        await db.commit()

async def get_top_pets(limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT p.*,u.first_name,u.username FROM pets p JOIN users u ON p.owner_id=u.user_id ORDER BY p.level DESC,p.xp DESC LIMIT ?",(limit,)) as c:
            return [dict(r) for r in await c.fetchall()]

async def get_top_users(limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users ORDER BY coins DESC LIMIT ?",(limit,)) as c:
            return [dict(r) for r in await c.fetchall()]

async def get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users ORDER BY user_id DESC") as c:
            return [dict(r) for r in await c.fetchall()]

async def get_bot_meta(key):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM bot_meta WHERE key=?", (key,)) as c:
            row = await c.fetchone()
        return row[0] if row else None

async def set_bot_meta(key, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO bot_meta (key, value) VALUES (?,?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (key, str(value)))
        await db.commit()

async def remember_chat(chat_id, chat_type, title=""):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO bot_chats (chat_id, chat_type, title) VALUES (?,?,?) "
                             "ON CONFLICT(chat_id) DO UPDATE SET chat_type=excluded.chat_type, title=excluded.title",
                             (int(chat_id), str(chat_type), title or ""))
            await db.commit()
    except Exception:
        pass

async def get_known_chats():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM bot_chats ORDER BY added_at DESC") as c:
            return [dict(r) for r in await c.fetchall()]

async def find_user_by_username(username):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE LOWER(username)=LOWER(?)",(username.lstrip("@"),)) as c:
            r = await c.fetchone()
        return dict(r) if r else None

TRANSFER_TAX = 0.25  # 25% комиссия на переводы

async def transfer_coins(fu, tu, amount):
    """Переводит монеты с комиссией 25%. Возвращает (success, received, tax)."""
    tax = max(1, int(amount * TRANSFER_TAX))
    total_needed = amount + tax
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT coins FROM users WHERE user_id=?",(fu,)) as c:
            r = await c.fetchone()
        if not r or r[0] < total_needed: return False, 0, 0
        await db.execute("UPDATE users SET coins=coins-? WHERE user_id=?",(total_needed,fu))
        await db.execute("UPDATE users SET coins=coins+? WHERE user_id=?",(amount,tu))
        await db.execute("INSERT INTO transfers (from_user,to_user,amount) VALUES (?,?,?)",(fu,tu,amount))
        await db.commit()
    return True, amount, tax

# ── v7.0 ХЕЛПЕРЫ ────────────────────────────────────────────────────────

async def add_season_tokens(uid, amount, reason=""):
    """Начислить токены сезона и проверить апгрейд уровня пропуска."""
    if amount <= 0: return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET season_tokens=season_tokens+? WHERE user_id=?", (amount, uid))
        await db.commit()
        async with db.execute("SELECT season_tokens, season_level FROM users WHERE user_id=?", (uid,)) as c:
            row = await c.fetchone()
    if not row: return
    tokens, cur_level = row[0], row[1]
    # Апгрейд уровней
    new_level = min(30, tokens // SEASON_TOKENS_PER_LEVEL)
    if new_level > cur_level:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET season_level=? WHERE user_id=?", (new_level, uid))
            await db.commit()

async def migrate_season():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users WHERE season_id IS NULL OR season_id<>?", (SEASON_ID,)) as c:
            cnt = (await c.fetchone())[0]
        if cnt:
            await db.execute(
                "UPDATE users SET season_id=?, season_tokens=0, season_level=0, season_pass=0 "
                "WHERE season_id IS NULL OR season_id<>?",
                (SEASON_ID, SEASON_ID),
            )
            await db.execute("DELETE FROM season_rewards_claimed")
            await db.commit()
            logger.info("Season %s started: reset %s users", SEASON_ID, cnt)

async def check_and_grant_achievement(uid, ach_type, bot=None):
    """Проверить и выдать ачивку если ещё не выдана."""
    if ach_type not in ACHIEVEMENTS: return False
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT 1 FROM achievements WHERE user_id=? AND ach_type=?", (uid, ach_type)) as c:
                if await c.fetchone(): return False  # уже есть
            await db.execute("INSERT INTO achievements (user_id, ach_type) VALUES (?,?)", (uid, ach_type))
            await db.commit()
        ach = ACHIEVEMENTS[ach_type]
        await add_season_tokens(uid, ach["tokens"])
        # Выдаём титул если есть
        if "title" in ach:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE users SET title=? WHERE user_id=?", (ach["title"], uid))
                await db.commit()
        # Уведомление
        if bot:
            title_txt = f"\n🏷️ Новый титул: <b>{TITLES[ach['title']]['name']}</b>" if "title" in ach else ""
            try:
                await bot.send_message(uid,
                    f"🏆 <b>Ачивка получена!</b>\n\n"
                    f"{ach['name']}\n{ach['desc']}\n"
                    f"🪙 +{ach['tokens']} токенов сезона{title_txt}",
                    parse_mode="HTML")
            except: pass
        return True
    except: return False

async def get_user_achievements(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT ach_type, earned_at FROM achievements WHERE user_id=? ORDER BY earned_at DESC", (uid,)) as c:
            return [dict(r) for r in await c.fetchall()]

async def get_daily_quests(uid):
    """Получить или создать ежедневные квесты для пользователя."""
    user = await get_user(uid)
    today = datetime.now().strftime("%Y-%m-%d")
    last = user.get("last_daily_quest")
    raw = user.get("daily_quests")
    if last == today and raw:
        try: return json.loads(raw)
        except: pass
    # Новые квесты на день
    chosen = random.sample(DAILY_QUEST_POOL, min(3, len(DAILY_QUEST_POOL)))
    quests = [{"id": q["id"], "desc": q["desc"], "type": q["type"],
               "required": q["required"], "progress": 0,
               "tokens": q["tokens"], "coins": q["coins"], "done": False} for q in chosen]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET daily_quests=?, last_daily_quest=? WHERE user_id=?",
                         (json.dumps(quests), today, uid))
        await db.commit()
    return quests

async def progress_daily_quest(uid, quest_type, amount=1, bot=None):
    """Засчитать прогресс ежедневного квеста по типу."""
    user = await get_user(uid)
    today = datetime.now().strftime("%Y-%m-%d")
    if user.get("last_daily_quest") != today: return
    raw = user.get("daily_quests")
    if not raw: return
    try: quests = json.loads(raw)
    except: return
    changed = False
    for q in quests:
        if q["done"] or q["type"] != quest_type: continue
        q["progress"] = min(q["required"], q["progress"] + amount)
        if q["progress"] >= q["required"] and not q["done"]:
            q["done"] = True; changed = True
            await add_season_tokens(uid, q["tokens"])
            if q["coins"]: await update_coins(uid, q["coins"])
            if bot:
                try:
                    coins_txt = f" +{q['coins']}💰" if q['coins'] else ''
                    await bot.send_message(uid,
                        f"✅ <b>Квест выполнен!</b>\n{q['desc']}\n"
                        f"🪙 +{q['tokens']} токенов{coins_txt}",
                        parse_mode="HTML")
                except: pass
        else: changed = True
    if changed:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET daily_quests=? WHERE user_id=?", (json.dumps(quests), uid))
            await db.commit()

async def delete_user_data(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pets WHERE owner_id=?",(uid,))
        await db.execute("DELETE FROM inventory WHERE user_id=?",(uid,))
        await db.execute("DELETE FROM tasks WHERE user_id=?",(uid,))
        await db.execute("UPDATE users SET coins=300,clan_id=NULL WHERE user_id=?",(uid,))
        await db.commit()

# КЛАНЫ
async def create_clan(owner_id, name, tag):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("INSERT INTO clans (name,tag,owner_id) VALUES (?,?,?)",(name,tag,owner_id))
            await db.commit()
            async with db.execute("SELECT clan_id FROM clans WHERE tag=?",(tag,)) as c:
                r = await c.fetchone()
            clan_id=r[0]
            await db.execute("DELETE FROM clan_members WHERE user_id=?",(owner_id,))
            await db.execute("INSERT INTO clan_members (clan_id,user_id,role) VALUES (?,?,'owner')",(clan_id,owner_id))
            await db.execute("UPDATE users SET clan_id=? WHERE user_id=?",(clan_id,owner_id))
            await db.commit(); return clan_id,None
        except Exception as e: return None,str(e)

async def get_clan(clan_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM clans WHERE clan_id=?",(clan_id,)) as c:
            r = await c.fetchone()
        return dict(r) if r else None

async def get_clan_by_user(uid):
    user = await get_user(uid)
    if not user or not user.get('clan_id'): return None
    return await get_clan(user['clan_id'])

async def get_clan_members(clan_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT cm.*,u.first_name,u.username FROM clan_members cm JOIN users u ON cm.user_id=u.user_id WHERE cm.clan_id=?",(clan_id,)) as c:
            return [dict(r) for r in await c.fetchall()]

async def join_clan(uid, clan_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM clan_members WHERE clan_id=?",(clan_id,)) as c:
            cnt=(await c.fetchone())[0]
        if cnt>=20: return False,"Клан заполнен (макс 20)"
        try:
            await db.execute("INSERT INTO clan_members (clan_id,user_id) VALUES (?,?)",(clan_id,uid))
            await db.execute("UPDATE users SET clan_id=? WHERE user_id=?",(clan_id,uid))
            await db.commit(); return True,None
        except: return False,"Ты уже в клане"

async def leave_clan(uid):
    user = await get_user(uid)
    if user and user.get('clan_id'):
        clan = await get_clan(user['clan_id'])
        if clan and clan['owner_id'] == uid:
            return False, "owner"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM clan_members WHERE user_id=?",(uid,))
        await db.execute("UPDATE users SET clan_id=NULL WHERE user_id=?",(uid,))
        await db.commit()
    return True, None

async def disband_clan(clan_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET clan_id=NULL WHERE clan_id=?",(clan_id,))
        await db.execute("DELETE FROM clan_members WHERE clan_id=?",(clan_id,))
        await db.execute("DELETE FROM clans WHERE clan_id=?",(clan_id,))
        await db.commit()

async def get_top_clans(limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM clans ORDER BY wins DESC,level DESC LIMIT ?",(limit,)) as c:
            return [dict(r) for r in await c.fetchall()]

# РЫНОК
async def create_trade_offer(uid, pid, price):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO trade_offers (from_user,pet_id,price) VALUES (?,?,?)",(uid,pid,price))
        await db.execute("UPDATE pets SET for_sale=1,sale_price=? WHERE pet_id=?",(price,pid))
        await db.commit()

async def get_market_offers():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT trade_offers.offer_id, trade_offers.from_user, trade_offers.pet_id, trade_offers.price,
                   pets.name as pet_name, pets.pet_type, pets.level,
                   users.first_name, users.username
            FROM trade_offers
            JOIN pets ON trade_offers.pet_id=pets.pet_id
            JOIN users ON trade_offers.from_user=users.user_id
            WHERE trade_offers.status='active'
            ORDER BY trade_offers.created_at DESC LIMIT 20
        """) as c:
            return [dict(r) for r in await c.fetchall()]

async def buy_pet_from_market(buyer_id, offer_id):
    async with get_user_lock(buyer_id):
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM trade_offers WHERE offer_id=? AND status='active'",(offer_id,)) as c:
                offer = await c.fetchone()
            if not offer: return False,"Предложение не найдено"
            offer=dict(offer)
            if offer['from_user']==buyer_id: return False,"Нельзя купить у себя"
            # ── ИСПРАВЛЕНИЕ RACE CONDITION: атомарно "захватываем" лот, переводя
            #    его в status='sold' только если он всё ещё 'active'. Если два
            #    покупателя одновременно жмут "купить", второй получит rowcount=0
            #    и не сможет купить уже проданный лот (раньше оба читали
            #    status='active' до того, как кто-либо успевал его сменить).
            cur = await db.execute(
                "UPDATE trade_offers SET status='sold' WHERE offer_id=? AND status='active'",
                (offer_id,)
            )
            if cur.rowcount == 0:
                await db.commit()
                return False, "Лот уже куплен"
            async with db.execute("SELECT coins FROM users WHERE user_id=?",(buyer_id,)) as c:
                buyer = await c.fetchone()
            if not buyer or buyer[0]<offer['price']:
                # Откатываем захват лота, раз покупка не состоялась
                await db.execute("UPDATE trade_offers SET status='active' WHERE offer_id=?",(offer_id,))
                await db.commit()
                return False,"Недостаточно монет"
            await db.execute("UPDATE users SET coins=coins-? WHERE user_id=?",(offer['price'],buyer_id))
            await db.execute("UPDATE users SET coins=coins+? WHERE user_id=?",(offer['price'],offer['from_user']))
            await db.execute("UPDATE pets SET owner_id=?,active=0,for_sale=0,sale_price=0 WHERE pet_id=?",(buyer_id,offer['pet_id']))
            await db.execute("UPDATE trade_offers SET status='completed' WHERE offer_id=?",(offer_id,))
            await db.commit(); return True,offer

async def cancel_trade_offer(uid, offer_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT pet_id FROM trade_offers WHERE offer_id=? AND from_user=?",(offer_id,uid)) as c:
            r = await c.fetchone()
        if not r: return False
        await db.execute("UPDATE trade_offers SET status='cancelled' WHERE offer_id=?",(offer_id,))
        await db.execute("UPDATE pets SET for_sale=0,sale_price=0 WHERE pet_id=?",(r[0],))
        await db.commit(); return True

# ТРЕЙД ПИТОМЦАМИ
async def create_pet_trade(from_uid, to_uid, from_pet_id, extra_coins=0, allow_gift=0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO pet_trades (from_user,to_user,from_pet_id,extra_coins,allow_gift) VALUES (?,?,?,?,?)",
                         (from_uid,to_uid,from_pet_id,extra_coins,allow_gift))
        await db.commit()
        async with db.execute("SELECT last_insert_rowid()") as c:
            return (await c.fetchone())[0]

async def get_pet_trade(trade_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM pet_trades WHERE trade_id=?",(trade_id,)) as c:
            r = await c.fetchone()
        return dict(r) if r else None

async def accept_pet_trade(trade_id, to_pet_id, caller_uid):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM pet_trades WHERE trade_id=? AND status='pending'",(trade_id,)) as c:
            trade = await c.fetchone()
        if not trade: return False,"Обмен не найден"
        trade=dict(trade)
        # Безопасность: только реальный адресат трейда может его принять
        if caller_uid != trade['to_user']:
            return False,"Это не твой обмен!"
        # Безопасность: если предложен ответный питомец, он должен принадлежать принимающему
        if to_pet_id:
            async with db.execute("SELECT owner_id FROM pets WHERE pet_id=?",(to_pet_id,)) as c:
                owner_row = await c.fetchone()
            if not owner_row or owner_row[0] != caller_uid:
                return False,"Питомец тебе не принадлежит!"
        # ── ИСПРАВЛЕНИЕ БАГА "кража питомцев": если ответный питомец НЕ передаётся
        # и нет доплаты монетами, это "дарение" — разрешено только если инициатор
        # сам это явно включил при создании трейда (allow_gift). Иначе получатель
        # не может забрать пета, ничего не отдав взамен.
        if not to_pet_id and not trade.get('allow_gift') and trade.get('extra_coins', 0) <= 0:
            return False,"Этот трейд требует ответного питомца — инициатор не разрешил дарение!"
        # Проверяем монеты если есть доплата
        if trade['extra_coins']>0:
            async with db.execute("SELECT coins FROM users WHERE user_id=?",(trade['to_user'],)) as c:
                r=await c.fetchone()
            if not r or r[0]<trade['extra_coins']: return False,"Недостаточно монет для доплаты"
            await db.execute("UPDATE users SET coins=coins-? WHERE user_id=?",(trade['extra_coins'],trade['to_user']))
            await db.execute("UPDATE users SET coins=coins+? WHERE user_id=?",(trade['extra_coins'],trade['from_user']))
        # ── ИСПРАВЛЕНИЕ: снимаем снаряжение перед передачей (иначе дюп) ──────
        # Получаем текущее снаряжение питомцев
        async with db.execute("SELECT weapon,armor FROM pets WHERE pet_id=?",(trade['from_pet_id'],)) as c:
            fp_eq = await c.fetchone()
        fp_weapon, fp_armor = (fp_eq[0], fp_eq[1]) if fp_eq else (None, None)

        to_pet_weapon, to_pet_armor = None, None
        if to_pet_id:
            async with db.execute("SELECT weapon,armor FROM pets WHERE pet_id=?",(to_pet_id,)) as c:
                tp_eq = await c.fetchone()
            to_pet_weapon, to_pet_armor = (tp_eq[0], tp_eq[1]) if tp_eq else (None, None)

        # Снимаем снаряжение с обоих питомцев перед обменом — возвращаем в инвентарь хозяев
        if fp_weapon:
            await _add_item_raw(db, trade['from_user'], fp_weapon, 1)
            await db.execute("UPDATE pets SET weapon=NULL WHERE pet_id=?",(trade['from_pet_id'],))
        if fp_armor:
            await _add_item_raw(db, trade['from_user'], fp_armor, 1)
            await db.execute("UPDATE pets SET armor=NULL WHERE pet_id=?",(trade['from_pet_id'],))
        if to_pet_id and to_pet_weapon:
            await _add_item_raw(db, trade['to_user'], to_pet_weapon, 1)
            await db.execute("UPDATE pets SET weapon=NULL WHERE pet_id=?",(to_pet_id,))
        if to_pet_id and to_pet_armor:
            await _add_item_raw(db, trade['to_user'], to_pet_armor, 1)
            await db.execute("UPDATE pets SET armor=NULL WHERE pet_id=?",(to_pet_id,))

        # Меняем хозяев питомцев
        await db.execute("UPDATE pets SET owner_id=?,active=0 WHERE pet_id=?",(trade['to_user'],trade['from_pet_id']))
        if to_pet_id:
            await db.execute("UPDATE pets SET owner_id=?,active=0 WHERE pet_id=?",(trade['from_user'],to_pet_id))
            await db.execute("UPDATE pet_trades SET to_pet_id=?,status='completed' WHERE trade_id=?",(to_pet_id,trade_id))
        else:
            await db.execute("UPDATE pet_trades SET status='completed' WHERE trade_id=?",(trade_id,))
        await db.commit(); return True,trade

async def decline_pet_trade(trade_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE pet_trades SET status='declined' WHERE trade_id=?",(trade_id,))
        await db.commit()

async def create_promo(code, coins=0, pet=None, uses=1):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("INSERT INTO promo_codes (code,reward_coins,reward_pet,uses_left) VALUES (?,?,?,?)",
                             (code.upper(), coins, pet, uses))
            await db.commit(); return True
        except: return False

async def delete_promo(code):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM promo_codes WHERE code=?",(code.upper(),))
        await db.execute("DELETE FROM promo_uses WHERE code=?",(code.upper(),))
        await db.commit()

async def use_promo(uid, code):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM promo_codes WHERE code=?",(code.upper(),)) as c:
            promo = await c.fetchone()
        if not promo: return False, "❌ Промокод не найден!"
        promo = dict(promo)
        if promo['uses_left'] <= 0: return False, "❌ Промокод исчерпан!"
        async with db.execute("SELECT use_id FROM promo_uses WHERE code=? AND user_id=?",(code.upper(),uid)) as c:
            already = await c.fetchone()
        if already: return False, "❌ Ты уже использовал этот промокод!"
        await db.execute("INSERT INTO promo_uses (code,user_id) VALUES (?,?)",(code.upper(),uid))
        await db.execute("UPDATE promo_codes SET uses_left=uses_left-1 WHERE code=?",(code.upper(),))
        await db.commit()
        return True, promo

async def get_all_promos():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM promo_codes ORDER BY created_at DESC") as c:
            return [dict(r) for r in await c.fetchall()]

async def get_pending_pet_trade(to_uid):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM pet_trades WHERE to_user=? AND status='pending' ORDER BY created_at DESC LIMIT 1",(to_uid,)) as c:
            r = await c.fetchone()
        return dict(r) if r else None

# БИТВЫ
async def create_battle_request(fu, tu):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM battle_requests WHERE from_user=? OR to_user=? OR from_user=? OR to_user=?",(fu,fu,tu,tu))
        await db.execute("INSERT INTO battle_requests (from_user,to_user) VALUES (?,?)",(fu,tu))
        await db.commit()

async def get_pending_request(to_uid):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM battle_requests WHERE to_user=? AND status='pending' ORDER BY created_at DESC LIMIT 1",(to_uid,)) as c:
            r = await c.fetchone()
        return dict(r) if r else None

async def accept_req(req_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE battle_requests SET status='accepted' WHERE req_id=?",(req_id,)); await db.commit()

async def decline_req(req_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE battle_requests SET status='declined' WHERE req_id=?",(req_id,)); await db.commit()

async def create_tournament(name, prize, max_players=8):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO tournaments (name,prize,max_players) VALUES (?,?,?)",(name,prize,max_players))
        await db.commit()
        async with db.execute("SELECT last_insert_rowid()") as c:
            return (await c.fetchone())[0]

async def get_active_tournament():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tournaments WHERE status IN ('registration','active') ORDER BY tournament_id DESC LIMIT 1") as c:
            r = await c.fetchone()
        return dict(r) if r else None

async def join_tournament(uid, tid):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM tournaments WHERE tournament_id=? AND status='registration'",(tid,)) as c:
            t = await c.fetchone()
        if not t: return False,"Турнир не найден"
        async with db.execute("SELECT COUNT(*) FROM tournament_players WHERE tournament_id=?",(tid,)) as c:
            cnt=(await c.fetchone())[0]
        if cnt>=t[4]: return False,"Турнир заполнен"
        try:
            await db.execute("INSERT INTO tournament_players (tournament_id,user_id) VALUES (?,?)",(tid,uid))
            await db.commit(); return True,None
        except: return False,"Уже зарегистрирован"

# ═══════════════════════════════════════════════════
# УТИЛИТЫ
# ═══════════════════════════════════════════════════

def mbar(v,m=100,l=8):
    v=max(0,min(m,v)); f=round(v/m*l); return "█"*f+"░"*(l-f)

def hpbar(v,m):
    pct=v/m if m>0 else 0
    c="🟢" if pct>0.6 else ("🟡" if pct>0.3 else "🔴")
    return f"{c}{mbar(v,m,6)} {v}/{m}"

def pet_full(pet):
    p=PETS.get(pet['pet_type'],PETS['dog']); r=RARITY_EMOJI.get(p['rarity'],'⚪')
    w=WEAPONS.get(pet.get('weapon')); a=ARMOR.get(pet.get('armor'))
    hp=pet['health']; mhp=pet.get('max_health',100)
    shiny_tag = " ✨<b>SHINY</b>" if pet.get('is_shiny') else ""
    # Shiny даёт +15% к характеристикам (отображаем)
    shiny_mult = 1.15 if pet.get('is_shiny') else 1.0
    evo_stage = pet_evo_stage(pet)
    evo = EVOLUTION_STAGES[evo_stage]
    evo_mult = evo["mult"]
    base_atk = int(p['atk'] * shiny_mult * evo_mult)
    base_def = int(p['def'] * shiny_mult * evo_mult)
    return (f"{p['emoji']} <b>{pet['name']}</b> {r} <i>{RARITY_NAMES.get(p['rarity'],'')}</i>{shiny_tag}  Ур.<b>{pet['level']}</b>\n"
            f"🔮 <i>{p.get('ability','')}</i>\n\n"
            f"❤️ {hpbar(hp,mhp)}\n🍗 {mbar(pet['hunger'])} {pet['hunger']}/100\n"
            f"🎮 {mbar(pet['mood'])} {pet['mood']}/100\n✨ {pet['xp']}/{pet['xp_to_next']} XP\n\n"
            f"⚔️ {base_atk}  🛡️ {base_def}\n"
            f"🗡️ {w['emoji']+' '+w['name'] if w else 'без оружия'}  🛡️ {a['emoji']+' '+a['name'] if a else 'без брони'}\n"
            f"⚔️ {pet.get('wins',0)}п  💀 {pet.get('losses',0)}п"
            +("\n😴 Спит" if pet.get('is_sleeping') else "")
            +("\n🏪 Продаётся" if pet.get('for_sale') else "")
            +(f"\n🧬 Эволюция {evo['icon']} <b>{evo['name']}</b> ×{evo_mult:.2f}" if evo_stage > 1 else ""))

def pet_power(pet):
    p=PETS.get(pet['pet_type'],PETS['dog'])
    w=WEAPONS.get(pet.get('weapon'),{"atk_bonus":0}); a=ARMOR.get(pet.get('armor'),{"def_bonus":0})
    return int((p['atk']+w['atk_bonus']+p['def']+a['def_bonus'])*pet['level']*pet_evo_mult(pet))

def battle_stats(pet):
    p=PETS.get(pet['pet_type'],PETS['dog'])
    w=WEAPONS.get(pet.get('weapon'),{"atk_bonus":0}); a=ARMOR.get(pet.get('armor'),{"def_bonus":0})
    shiny_mult = 1.15 if pet.get('is_shiny') else 1.0
    # v7.0 — скилл-бонусы
    sk_atk  = (pet.get('skill_atk')  or 0) * 5
    sk_def  = (pet.get('skill_def')  or 0) * 5
    sk_hp   = (pet.get('skill_hp')   or 0) * 20
    evo_mult = pet_evo_mult(pet)
    atk = int((p['atk'] + w['atk_bonus'] + pet['level']*3 + sk_atk) * shiny_mult * evo_mult)
    def_ = int((p['def'] + a['def_bonus'] + pet['level']*2 + sk_def) * shiny_mult * evo_mult)
    hp  = int((pet['health'] + sk_hp) * evo_mult)
    return atk, def_, hp

def cd_left(ts,secs):
    if not ts: return None
    try: e=(datetime.now()-datetime.fromisoformat(ts)).total_seconds()
    except: return None
    left=secs-e; return int(left) if left>0 else None

def fmt_t(s):
    h,m=s//3600,(s%3600)//60
    return f"{h}ч {m}м" if h else f"{m}м {s%60}с"

def hatch_egg(egg_id):
    pool=EGG_TYPES[egg_id]['pool']; total=sum(pool.values()); roll=random.randint(1,total); cum=0
    for pt,w in pool.items():
        cum+=w
        if roll<=cum: return pt
    return list(pool.keys())[0]

def uptime():
    s=int(time.time()-START_TIME); h,m,sec=s//3600,(s%3600)//60,s%60
    return f"{h}ч {m}м {sec}с"

def is_admin(uid): return uid in ADMIN_IDS

TRIGGERS = {
    "pets":       {"питомцы","питомец","петы","пет","🐾 питомцы"},
    "eggs":       {"яйца","яйцо","🥚 яйца"},
    "shop":       {"магазин","магаз","🎒 магазин"},
    "work":       {"работать","работа","💰 работать"},
    "daily":      {"ежедневка","🎁 ежедневка"},
    "battle":     {"битва","бой","⚔️ битва","арена","🏟️ арена"},
    "adventure":  {"приключение","поход","🧭 приключение"},
    "tasks":      {"задания","📋 задания"},
    "top":        {"рейтинг","топ","🏆 рейтинг","🏅 рейтинг"},
    "profile":    {"профиль","я","👤 профиль"},
    "clan":       {"клан","кланы","🏰 клан"},
    "market":     {"рынок","маркет","🏪 рынок"},
    "trade":      {"трейд","обмен","обменять","🔄 трейд"},
    "duel":       {"дуэль","карты","🃏 дуэль"},
    "referral":   {"рефералы","реферал","реф","👥 рефералы"},
    "tournament": {"турнир","🎯 турнир"},
    "donate":     {"донат","звёзды","⭐ донат"},
    "event":      {"ивент","событие","🎪 ивент"},
    "help":       {"помощь","хелп","help"},
    "world":      {"мир","миры","локации","локация","🌍 миры","🗺️ миры"},
    "promo":      {"промокод","промо","promo"},
    "rebirth":    {"ребёрт","rebirth","🔥 ребёрт"},
    "crystals":   {"кристаллы","кристалл","💎 кристаллы"},
    "boss":       {"босс","boss","👹 босс"},
    "fuse":       {"слить","слияние","✨ слить"},
    # v7.0
    "season":     {"сезон","пропуск","🎫 сезон"},
    "quests_daily":{"квесты","ежедневные квесты","📋 квесты"},
    "achievements":{"ачивки","достижения","🏆 ачивки"},
    "skills":     {"скиллы","умения","навыки","💡 скиллы"},
    "auction":    {"аукцион","торги","🏪 аукцион"},
    "evolve":     {"эволюция","эволюционировать","🧬 эволюция"},
    "wheel":      {"колесо","колесо удачи","🎡 колесо удачи","рулетка"},
}

def get_trigger(text):
    t=text.lower().strip()
    for action,words in TRIGGERS.items():
        if t in words: return action
    return None

async def safe_edit(msg, text, **kw):
    try:
        await msg.edit_text(text,**kw)
    except TelegramBadRequest:
        try:
            await msg.edit_caption(caption=text,**kw)
        except TelegramBadRequest:
            pass
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after+1)
            await safe_edit(msg,text,**kw)
        except Exception as e:
            logger.warning(f"edit caption: {e}")
    except TelegramRetryAfter as e: await asyncio.sleep(e.retry_after+1)
    except Exception as e: logger.warning(f"edit: {e}")

async def safe_answer(msg, text, **kw):
    try: return await msg.answer(text,**kw)
    except TelegramRetryAfter as e: await asyncio.sleep(e.retry_after+1); return await safe_answer(msg,text,**kw)
    except Exception as e: logger.warning(f"answer: {e}"); return None

async def safe_reply(msg, text, **kw):
    try: return await msg.reply(text,**kw)
    except TelegramRetryAfter as e: await asyncio.sleep(e.retry_after+1); return await safe_reply(msg,text,**kw)
    except Exception as e: logger.warning(f"reply: {e}"); return None

async def send_rich(bot, chat_id, rich_html, fallback_html=None, reply_markup=None,
                    message_thread_id=None, disable_notification=False):
    payload = {
        "chat_id": chat_id,
        "rich_message": {"html": rich_html},
        "disable_notification": bool(disable_notification),
    }
    if message_thread_id:
        payload["message_thread_id"] = int(message_thread_id)
    if reply_markup is not None:
        try:
            payload["reply_markup"] = reply_markup.model_dump(exclude_none=True)
        except Exception:
            payload["reply_markup"] = reply_markup
    try:
        session = await bot.session.create_session()
        url = f"https://api.telegram.org/bot{bot.token}/sendRichMessage"
        async with session.post(url, json=payload, timeout=30) as resp:
            data = await resp.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "rich message failed"))
        return data.get("result")
    except Exception as e:
        logger.warning("sendRichMessage fallback: %s", e)
        kw = {"parse_mode": "HTML", "disable_notification": bool(disable_notification)}
        if reply_markup is not None:
            kw["reply_markup"] = reply_markup
        if message_thread_id:
            kw["message_thread_id"] = int(message_thread_id)
        return await bot.send_message(chat_id, fallback_html or rich_html, **kw)

# ═══════════════════════════════════════════════════
# КЛАВИАТУРЫ
# ═══════════════════════════════════════════════════

def main_kb():
    b=ReplyKeyboardBuilder()
    b.row(KeyboardButton(text="🐾 Питомцы"),  KeyboardButton(text="🥚 Яйца"))
    b.row(KeyboardButton(text="🎒 Магазин"),  KeyboardButton(text="💰 Работать"))
    b.row(KeyboardButton(text="🏟️ Арена"),    KeyboardButton(text="🧭 Приключение"))
    b.row(KeyboardButton(text="📋 Задания"),  KeyboardButton(text="🎁 Ежедневка"))
    b.row(KeyboardButton(text="🏰 Клан"),     KeyboardButton(text="🏪 Рынок"))
    b.row(KeyboardButton(text="🔄 Трейд"),    KeyboardButton(text="🃏 Дуэль"))
    b.row(KeyboardButton(text="👥 Рефералы"), KeyboardButton(text="🏆 Рейтинг"))
    b.row(KeyboardButton(text="🎪 Ивент"),    KeyboardButton(text="⭐ Донат"))
    b.row(KeyboardButton(text="🌍 Миры"),     KeyboardButton(text="💎 Кристаллы"))
    b.row(KeyboardButton(text="🔥 Ребёрт"),   KeyboardButton(text="👹 Босс"))
    b.row(KeyboardButton(text="✨ Слить"),    KeyboardButton(text="👤 Профиль"))
    b.row(KeyboardButton(text="❓ Помощь"))
    # v7.0
    b.row(KeyboardButton(text="🎫 Сезон"),    KeyboardButton(text="🏆 Ачивки"))
    b.row(KeyboardButton(text="🏪 Аукцион"),  KeyboardButton(text="💡 Скиллы"))
    b.row(KeyboardButton(text="🧬 Эволюция"), KeyboardButton(text="🎡 Колесо удачи"))
    return b.as_markup(resize_keyboard=True)

def battle_menu_kb(uid_str):
    """Меню выбора типа битвы."""
    b=InlineKeyboardBuilder()
    b.button(text="🏟️ Подбор соперника",    callback_data=f"battle:random:{uid_str}")
    b.button(text="👤 Вызвать игрока",        callback_data=f"battle:choose:{uid_str}")
    b.button(text="📋 Принять вызов",         callback_data=f"battle:check:{uid_str}")
    b.adjust(1); return b.as_markup()

PER_PAGE = 6

def pets_kb(pets, owner_id, page=0):
    b=InlineKeyboardBuilder()
    total=len(pets); pages=max(1,(total+PER_PAGE-1)//PER_PAGE)
    page=max(0,min(page,pages-1))
    chunk=pets[page*PER_PAGE:(page+1)*PER_PAGE]
    for p in chunk:
        pd=PETS.get(p['pet_type'],PETS['dog']); ac="✅ " if p['active'] else ""
        sale=" 🏪" if p.get('for_sale') else ""
        b.button(text=f"{ac}{pd['emoji']} {p['name']} ур.{p['level']}{sale}",
                 callback_data=f"vp:{p['pet_id']}:{owner_id}")
    b.adjust(1)
    if pages>1:
        prev=page-1 if page>0 else pages-1
        nxt=page+1 if page<pages-1 else 0
        b.row(
            InlineKeyboardBuilder().button(text="◀️",callback_data=f"pl:{owner_id}:{prev}").as_markup().inline_keyboard[0][0],
            InlineKeyboardBuilder().button(text=f"{page+1}/{pages}",callback_data=f"pl:{owner_id}:{page}").as_markup().inline_keyboard[0][0],
            InlineKeyboardBuilder().button(text="▶️",callback_data=f"pl:{owner_id}:{nxt}").as_markup().inline_keyboard[0][0],
        )
    return b.as_markup()

def pet_kb(pid, owner_id):
    b=InlineKeyboardBuilder()
    b.button(text="🍗 Кормить",    callback_data=f"a:feed:{pid}:{owner_id}")
    b.button(text="🎮 Играть",     callback_data=f"a:play:{pid}:{owner_id}")
    b.button(text="😴 Спать",      callback_data=f"a:sleep:{pid}:{owner_id}")
    b.button(text="💊 Лечить",     callback_data=f"a:heal:{pid}:{owner_id}")
    b.button(text="🎒 Предметы",   callback_data=f"a:items:{pid}:{owner_id}")
    b.button(text="🧬 Эволюция",   callback_data=f"evo:menu:{pid}:{owner_id}")
    b.button(text="⚔️ Экипировка", callback_data=f"eq:m:{pid}:{owner_id}")
    b.button(text="🏪 Продать",    callback_data=f"sell:{pid}:{owner_id}")
    b.button(text="💵 Быстрая продажа", callback_data=f"sellfast:{pid}:{owner_id}")
    b.button(text="✅ Активный",   callback_data=f"sa:{pid}:{owner_id}")
    b.button(text="◀️ Назад",      callback_data=f"pl:{owner_id}")
    b.adjust(2); return b.as_markup()

def equip_kb(pid, owner_id):
    b=InlineKeyboardBuilder()
    b.button(text="🗡️ Оружие",callback_data=f"eq:w:{pid}:{owner_id}")
    b.button(text="🛡️ Броня", callback_data=f"eq:a:{pid}:{owner_id}")
    b.button(text="◀️ Назад", callback_data=f"vp:{pid}:{owner_id}")
    b.adjust(2); return b.as_markup()

def weapons_kb(pid, owner_id, inv):
    b=InlineKeyboardBuilder(); owned={i['item_id'] for i in inv}
    for wid,w in WEAPONS.items():
        if wid in owned: b.button(text=f"{w['emoji']} {w['name']} +{w['atk_bonus']}⚔️",callback_data=f"eq:sw:{pid}:{owner_id}:{wid}")
    b.button(text="◀️ Назад",callback_data=f"eq:m:{pid}:{owner_id}"); b.adjust(1); return b.as_markup()

def armor_kb(pid, owner_id, inv):
    b=InlineKeyboardBuilder(); owned={i['item_id'] for i in inv}
    for aid,a in ARMOR.items():
        if aid in owned: b.button(text=f"{a['emoji']} {a['name']} +{a['def_bonus']}🛡️",callback_data=f"eq:sa:{pid}:{owner_id}:{aid}")
    b.button(text="◀️ Назад",callback_data=f"eq:m:{pid}:{owner_id}"); b.adjust(1); return b.as_markup()

def eggs_kb(inv, uid):
    b=InlineKeyboardBuilder()
    for e in inv:
        if e['item_id'] in EGG_TYPES:
            eg=EGG_TYPES[e['item_id']]
            b.button(text=f"{eg['emoji']} {eg['name']} ×{e['quantity']}",callback_data=f"hatch:{e['item_id']}:{uid}")
    b.adjust(1); return b.as_markup()

def shop_cat_kb(uid):
    b=InlineKeyboardBuilder()
    b.button(text="🥚 Яйца",     callback_data=f"sc:eggs:{uid}")
    b.button(text="🍖 Еда",      callback_data=f"sc:food:{uid}")
    b.button(text="💊 Лечение",  callback_data=f"sc:heal:{uid}")
    b.button(text="🗡️ Оружие",   callback_data=f"sc:weapons:{uid}")
    b.button(text="🛡️ Броня",    callback_data=f"sc:armor:{uid}")
    b.button(text="⚗️ Бой",      callback_data=f"sc:battle:{uid}")
    b.button(text="⭐ За звёзды", callback_data=f"sc:stars:{uid}")
    b.button(text="🔥 АКЦИИ",    callback_data=f"sc:deals:{uid}")
    b.adjust(2); return b.as_markup()

def shop_items_kb(cat, uid):
    b=InlineKeyboardBuilder()
    m={
        "eggs":   [(k,EGG_TYPES[k]['emoji'],EGG_TYPES[k]['name'],EGG_TYPES[k]['price']) for k in EGG_TYPES if EGG_TYPES[k]['price']>0],
        "food":   [(k,ITEMS[k]['emoji'],ITEMS[k]['name'],ITEMS[k]['price']) for k in ["food","premium","cake","steak","ball"]],
        "heal":   [(k,ITEMS[k]['emoji'],ITEMS[k]['name'],ITEMS[k]['price']) for k in ["firstaid","superheal","vitamins","magic_potion"]],
        "weapons":[(k,WEAPONS[k]['emoji'],WEAPONS[k]['name'],WEAPONS[k]['price']) for k in WEAPONS if WEAPONS[k]['price']>0],
        "armor":  [(k,ARMOR[k]['emoji'],ARMOR[k]['name'],ARMOR[k]['price']) for k in ARMOR if ARMOR[k]['price']>0],
        "battle": [(k,ITEMS[k]['emoji'],ITEMS[k]['name'],ITEMS[k]['price']) for k in BATTLE_ITEMS if ITEMS.get(k,{}).get('price',0)>0],
    }
    if cat=="stars":
        for k,v in EGG_TYPES.items():
            if v.get('stars',0)>0: b.button(text=f"{v['emoji']} {v['name']} — {v['stars']}⭐",callback_data=f"sb_star:{k}:{uid}")
        for k,v in WEAPONS.items():
            if v.get('stars',0)>0: b.button(text=f"{v['emoji']} {v['name']} — {v['stars']}⭐",callback_data=f"sb_star:{k}:{uid}")
        for k,v in ARMOR.items():
            if v.get('stars',0)>0: b.button(text=f"{v['emoji']} {v['name']} — {v['stars']}⭐",callback_data=f"sb_star:{k}:{uid}")
        for k,v in ITEMS.items():
            if v.get('stars',0)>0 and k != 'zeus_set': b.button(text=f"{v['emoji']} {v['name']} — {v['stars']}⭐",callback_data=f"sb_star:{k}:{uid}")
    elif cat=="deals":
        # Только акция на СЕТ ЗЕВСА
        b.button(text=f"⚡ СЕТ ЗЕВСА (Питомец + Экипировка) — 100⭐",callback_data=f"sb_star:zeus_set:{uid}")
    else:
        for iid,emoji,name,price in m.get(cat,[]):
            b.button(text=f"{emoji} {name} — {price}💰",callback_data=f"sb:{iid}:{uid}")
    b.button(text="◀️ Назад",callback_data=f"sc:main:{uid}"); b.adjust(1); return b.as_markup()

def top_kb(uid):
    b=InlineKeyboardBuilder()
    b.button(text="🐾 Питомцы",    callback_data=f"top:pets:{uid}")
    b.button(text="💰 Богатейшие", callback_data=f"top:users:{uid}")
    b.button(text="⚔️ Бойцы",      callback_data=f"top:fighters:{uid}")
    b.button(text="🏟️ Арена",      callback_data=f"top:arena:{uid}")
    b.button(text="🏰 Кланы",      callback_data=f"top:clans:{uid}")
    b.button(text="💎 Кристаллы",  callback_data=f"top:crystals:{uid}")
    b.button(text="🔥 Ребёрты",    callback_data=f"top:rebirths:{uid}")
    b.adjust(2); return b.as_markup()

def battle_req_kb(req_id, to_uid):
    b=InlineKeyboardBuilder()
    b.button(text="⚔️ Принять вызов!",callback_data=f"br:a:{req_id}:{to_uid}")
    b.button(text="❌ Отклонить",      callback_data=f"br:d:{req_id}:{to_uid}")
    b.adjust(1); return b.as_markup()

def inv_use_kb(inv, pid, owner_id):
    b=InlineKeyboardBuilder(); usable=set(ITEMS.keys())-BATTLE_ITEMS
    for e in inv:
        if e['item_id'] in usable:
            it=ITEMS.get(e['item_id'])
            if it: b.button(text=f"{it['emoji']} {it['name']} ×{e['quantity']}",callback_data=f"ui:{e['item_id']}:{pid}:{owner_id}")
    b.button(text="❌ Закрыть",callback_data=f"close:{owner_id}"); b.adjust(1); return b.as_markup()

def clan_no_kb(uid):
    b=InlineKeyboardBuilder()
    b.button(text="🏰 Создать (500💰)",callback_data=f"clan:create:{uid}")
    b.button(text="🔍 Найти клан",     callback_data=f"clan:search:{uid}")
    b.button(text="🏆 Топ кланов",     callback_data=f"top:clans:{uid}")
    b.adjust(1); return b.as_markup()

def clan_yes_kb(uid, is_owner=False):
    b=InlineKeyboardBuilder()
    b.button(text="👥 Участники",  callback_data=f"clan:members:{uid}")
    b.button(text="💰 Банк клана", callback_data=f"clan:bank:{uid}")
    b.button(text="⚔️ Клан. война", callback_data=f"clan:war:menu:{uid}")
    if is_owner: b.button(text="🗑️ Распустить",callback_data=f"clan:disband:{uid}")
    b.button(text="🚪 Покинуть",  callback_data=f"clan:leave:{uid}")
    b.adjust(2); return b.as_markup()

def duel_kb(uid):
    b=InlineKeyboardBuilder()
    b.button(text="🃏 Дуэль 200💰",   callback_data=f"duel:play:200:{uid}")
    b.button(text="🃏 Дуэль 500💰",   callback_data=f"duel:play:500:{uid}")
    b.button(text="🃏 Дуэль 1000💰",  callback_data=f"duel:play:1000:{uid}")
    b.button(text="🃏 Дуэль 3000💰",  callback_data=f"duel:play:3000:{uid}")
    b.adjust(2); return b.as_markup()

def donate_kb(uid):
    b=InlineKeyboardBuilder()
    b.button(text="━━━ 💰 МОНЕТЫ ━━━",          callback_data="don:noop")
    b.button(text="⭐ 1 = 1000💰",               callback_data=f"don:1:{uid}")
    b.button(text="⭐ 5 = 5500💰 (+10%)",        callback_data=f"don:5:{uid}")
    b.button(text="⭐ 10 = 12000💰 (+20%)",      callback_data=f"don:10:{uid}")
    b.button(text="⭐ 25 = 32000💰 (+28%)",      callback_data=f"don:25:{uid}")
    b.button(text="⭐ 50 = 70000💰 (+40%)",      callback_data=f"don:50:{uid}")
    b.button(text="━━━ 👑 VIP ━━━",              callback_data="don:noop")
    b.button(text="👑 VIP 30д = 20⭐",           callback_data=f"don:vip:{uid}")
    b.button(text="━━━ 🐾 ПИТОМЦЫ ━━━",         callback_data="don:noop")
    b.button(text="🌙 Небесная кошка = 30⭐",    callback_data=f"don:pet_celestial_cat:{uid}")
    b.button(text="🌑 Волк бездны = 35⭐",       callback_data=f"don:pet_void_wolf:{uid}")
    b.button(text="🐼 Золотая панда = 30⭐",     callback_data=f"don:pet_golden_panda:{uid}")
    b.button(text="🦊 Кристальная лиса = 35⭐",  callback_data=f"don:pet_crystal_fox:{uid}")
    b.button(text="🐯 Тигр Инферно = 40⭐",      callback_data=f"don:pet_inferno_tiger:{uid}")
    b.button(text="━━━ 📦 АКЦИИ ━━━",            callback_data="don:noop")
    b.button(text="🎁 Стартовый пак = 15⭐",     callback_data=f"don:pack_start:{uid}")
    b.button(text="💎 Кристальный пак = 25⭐",   callback_data=f"don:pack_crystal:{uid}")
    b.button(text="🌟 Мега-пак = 60⭐",          callback_data=f"don:pack_mega:{uid}")
    b.adjust(1); return b.as_markup()

def market_kb(offers, uid, page=0):
    b=InlineKeyboardBuilder()
    total=len(offers); pages=max(1,(total+PER_PAGE-1)//PER_PAGE)
    page=max(0,min(page,pages-1))
    chunk=offers[page*PER_PAGE:(page+1)*PER_PAGE]
    for o in chunk:
        p=PETS.get(o['pet_type'],PETS['dog'])
        seller=o.get('first_name') or o.get('username') or '?'
        b.button(text=f"{p['emoji']} {o['pet_name']} ур.{o['level']} — {o['price']}💰 ({seller})",
                 callback_data=f"mkt:buy:{o['offer_id']}:{uid}")
    b.adjust(1)
    if pages>1:
        prev=page-1 if page>0 else pages-1
        nxt=page+1 if page<pages-1 else 0
        b.row(
            InlineKeyboardBuilder().button(text="◀️",callback_data=f"mkt:pg:{uid}:{prev}").as_markup().inline_keyboard[0][0],
            InlineKeyboardBuilder().button(text=f"{page+1}/{pages}",callback_data=f"mkt:pg:{uid}:{page}").as_markup().inline_keyboard[0][0],
            InlineKeyboardBuilder().button(text="▶️",callback_data=f"mkt:pg:{uid}:{nxt}").as_markup().inline_keyboard[0][0],
        )
    b.row(
        InlineKeyboardBuilder().button(text="➕ Выставить питомца",callback_data=f"mkt:sell:{uid}").as_markup().inline_keyboard[0][0],
        InlineKeyboardBuilder().button(text="📋 Мои предложения",callback_data=f"mkt:mine:{uid}").as_markup().inline_keyboard[0][0],
    )
    b.row(InlineKeyboardBuilder().button(text="💵 Быстрая продажа (−25%)",callback_data=f"mkt:quick:{uid}").as_markup().inline_keyboard[0][0])
    return b.as_markup()

def trade_pets_kb(pets, uid, target_uid, trade_id, allow_gift=False, extra_coins=0):
    """Выбор питомца для ответного обмена."""
    b=InlineKeyboardBuilder()
    for p in pets:
        pd=PETS.get(p['pet_type'],PETS['dog'])
        b.button(text=f"{pd['emoji']} {p['name']} ур.{p['level']}",
                 callback_data=f"trade:accept:{trade_id}:{p['pet_id']}:{uid}")
    b.button(text="❌ Отклонить",callback_data=f"trade:decline:{trade_id}:{uid}")
    # ── ИСПРАВЛЕНИЕ БАГА "кража питомцев": кнопка "Принять без обмена" (т.е.
    # получить пета, не отдав своего) показывается только если инициатор сам
    # разрешил дарение, ЛИБО трейд уже включает доплату монетами от получателя.
    if allow_gift or extra_coins > 0:
        b.button(text="💰 Принять без обмена",callback_data=f"trade:accept:{trade_id}:0:{uid}")
    b.adjust(1); return b.as_markup()

# adm_kb и adm_player_kb определены ниже вместе с новыми admin handlers

# ═══════════════════════════════════════════════════
# FSM
# ═══════════════════════════════════════════════════

class S(StatesGroup):
    pet_name       = State()
    rename         = State()
    clan_name      = State()
    clan_tag       = State()
    clan_join      = State()
    sell_price     = State()
    trade_target   = State()
    trade_pet      = State()
    trade_coins    = State()
    trade_gift     = State()
    battle_target  = State()
    adm_find       = State()
    adm_coins_id   = State()
    adm_coins_amt  = State()
    adm_gc_amt     = State()
    adm_egg_id     = State()
    adm_broadcast  = State()
    adm_tour_name  = State()
    adm_tour_prize = State()
    adm_promo_code = State()
    adm_promo_coins= State()
    adm_promo_pet  = State()
    adm_promo_uses = State()
    adm_take_uid   = State()
    adm_take_pet   = State()
    adm_give_pet_uid= State()
    adm_give_pet_type= State()
    adm_crystals_id  = State()
    adm_crystals_amt = State()
    # v7.0
    auction_bid   = State()
    auction_price = State()

# ═══════════════════════════════════════════════════
# MIDDLEWARE
# ═══════════════════════════════════════════════════

class ThreadMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data):
        if isinstance(event, Message):
            if event.chat.type != ChatType.PRIVATE:
                await remember_chat(event.chat.id, event.chat.type, event.chat.title or "")
                t = event.message_thread_id
                if t is not None and t != GAME_THREAD: return
        return await handler(event, data)

class CrashGuardMiddleware(BaseMiddleware):
    """
    ── ИСПРАВЛЕНИЕ БАГА "арена: найден игрок, но битвы нет" ──
    Раньше необработанное исключение внутри callback-хендлера (например,
    арены) просто падало в лог aiogram, а пользователь не получал НИКАКОГО
    ответа — выглядело как "зависший" подбор соперника. Эта middleware
    перехватывает любые необработанные исключения и хотя бы отвечает
    пользователю, что что-то пошло не так, вместо тишины.
    """
    async def __call__(self, handler, event: TelegramObject, data):
        try:
            return await handler(event, data)
        except Exception as e:
            logger.exception(f"Unhandled error in handler for {type(event).__name__}: {e}")
            try:
                if isinstance(event, CallbackQuery):
                    await event.answer("⚠️ Произошла ошибка. Попробуй ещё раз.", show_alert=True)
                elif isinstance(event, Message):
                    await event.answer("⚠️ Произошла ошибка при обработке. Попробуй ещё раз.")
            except Exception:
                pass

class BanMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data):
        u = getattr(event,'from_user',None)
        if u:
            await get_or_create_user(u.id, u.username or "", u.first_name or "")
            db_u = await get_user(u.id)
            if db_u and db_u.get('is_banned') and not is_admin(u.id):
                if isinstance(event,Message):
                    text = event.text or ""
                    if text.startswith("/"):
                        await event.answer("🚫 Ты заблокирован в PetWorld.")
                    return
                elif isinstance(event,CallbackQuery):
                    await event.answer("🚫 Заблокирован!",show_alert=True)
                    return
                return
        return await handler(event, data)

class AntiSpamMiddleware(BaseMiddleware):
    """
    Защита от спама кнопками.
    Один пользователь не может нажимать кнопки чаще, чем раз в COOLDOWN секунд.
    При попытке — тихий answer() без вызова хендлера.
    """
    COOLDOWN: float = 1.5  # секунды между нажатиями

    def __init__(self):
        super().__init__()
        self._last_click: dict[int, float] = {}

    async def __call__(self, handler, event: TelegramObject, data):
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)

        user = event.from_user
        if user is None or is_admin(user.id):
            return await handler(event, data)

        now = asyncio.get_event_loop().time()
        last = self._last_click.get(user.id, 0.0)
        diff = now - last

        if diff < self.COOLDOWN:
            remaining = self.COOLDOWN - diff
            await event.answer(
                f"⏳ Не так быстро! Подожди {remaining:.1f} сек.",
                show_alert=False,
            )
            return  # хендлер не вызываем

        self._last_click[user.id] = now
        return await handler(event, data)


class OwnerCheckMiddleware(BaseMiddleware):
    """Проверяет что кнопку нажимает правильный пользователь."""
    async def __call__(self, handler, event: TelegramObject, data):
        if isinstance(event, CallbackQuery):
            cb_data = event.data or ""
            parts = cb_data.split(":")
            
            # Кнопки, которые доступны всем (для взаимодействия между игроками)
            # Это кнопки которые один игрок нажимает на сообщении другого игрока
            PUBLIC_BUTTONS = {
                "br:a",           # Принять вызов на битву
                "br:d",           # Отклонить вызов на битву
                "trade:accept",   # Принять трейд
                "trade:decline",  # Отклонить трейд
                "tour:join",      # Присоединиться к турниру
            }
            
            # Проверяем публичные кнопки
            if any(cb_data.startswith(s) for s in PUBLIC_BUTTONS):
                return await handler(event, data)
            
            # Определяем позицию uid в callback_data для разных типов кнопок
            expected_uid = None
            
            # Особые случаи с известной структурой
            if cb_data.startswith("pl:"):
                # pl:{owner_id}:{page} или pl:{owner_id}
                if len(parts) >= 2 and parts[1].isdigit():
                    expected_uid = int(parts[1])
            elif cb_data.startswith("mkt:pg:"):
                # mkt:pg:{uid}:{page}
                if len(parts) >= 3 and parts[2].isdigit():
                    expected_uid = int(parts[2])
            elif cb_data.startswith("mkt:buy:"):
                # mkt:buy:{offer_id}:{uid}
                if len(parts) >= 4 and parts[3].isdigit():
                    expected_uid = int(parts[3])
            elif cb_data.startswith("mkt:confirm:"):
                # mkt:confirm:{offer_id}:{uid}
                if len(parts) >= 4 and parts[3].isdigit():
                    expected_uid = int(parts[3])
            elif cb_data.startswith("mkt:back:"):
                # mkt:back:{uid}
                if len(parts) >= 3 and parts[2].isdigit():
                    expected_uid = int(parts[2])
            else:
                # Для всех остальных кнопок uid в последней позиции
                if len(parts) > 0:
                    last = parts[-1]
                    if last.isdigit():
                        expected_uid = int(last)
            
            # Проверяем владельца
            if expected_uid is not None:
                actual_uid = event.from_user.id
                if expected_uid != actual_uid and not is_admin(actual_uid):
                    await event.answer("❌ Это не твоя кнопка!", show_alert=True)
                    return
        
        return await handler(event, data)

# ═══════════════════════════════════════════════════
# ПРОВЕРКА ПОДПИСКИ
# ═══════════════════════════════════════════════════

async def check_subscription(bot: Bot, user_id: int) -> bool:
    """True = подписан (или не удалось проверить)."""
    try:
        member = await bot.get_chat_member(NEWS_CHANNEL, user_id)
        return member.status not in ("left", "kicked")
    except Exception:
        return True  # не блокируем если нет прав на проверку

async def check_started_bot(user_id: int) -> bool:
    """True = игрок уже запускал бота (есть в БД)."""
    return (await get_user(user_id)) is not None

async def maybe_block_group_user(msg: Message) -> bool:
    """
    Проверяет подписку и запуск бота для сообщений в группе.
    Возвращает True если нужно ЗАБЛОКИРОВАТЬ (не пускать дальше).
    """
    uid = msg.from_user.id if msg.from_user else None
    if uid is None or is_admin(uid):
        return False

    bot: Bot = msg.bot
    started = await check_started_bot(uid)
    subscribed = await check_subscription(bot, uid)

    if started and subscribed:
        return False

    name = msg.from_user.first_name or "Игрок"
    lines = [f"👋 <b>{name}</b>, для игры в PetWorld нужно:\n"]
    b = InlineKeyboardBuilder()

    if not started:
        lines.append("🤖 Запустить бота в личных сообщениях — нажми кнопку и отправь /start")
        b.button(text="🤖 Запустить бота", url=f"https://t.me/{(await bot.get_me()).username}?start=hi")
    if not subscribed:
        lines.append(f"📢 Подписаться на канал {NEWS_CHANNEL}")
        b.button(text="📢 Подписаться на канал", url=f"https://t.me/{NEWS_CHANNEL.lstrip('@')}")

    lines.append("\nПосле этого повтори команду!")
    b.adjust(1)

    try:
        await msg.reply("\n".join(lines), reply_markup=b.as_markup(), parse_mode="HTML")
    except Exception:
        pass
    return True

# ═══════════════════════════════════════════════════
# ХЕНДЛЕРЫ
# ═══════════════════════════════════════════════════

router = Router()

@router.message(CommandStart())
async def cmd_start(msg: Message):
    is_group = msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

    if is_group:
        # В группе показываем инструкцию / проверяем условия
        blocked = await maybe_block_group_user(msg)
        if blocked:
            return
        # Если уже всё ок — просто подтверждение
        name = msg.from_user.first_name or "Игрок"
        await msg.reply(
            f"✅ <b>{name}</b>, ты уже в игре! Используй команды или кнопки меню.",
            parse_mode="HTML"
        )
        return

    # ЛС — обычный флоу
    is_new_user = not await get_user(msg.from_user.id)
    user = await get_or_create_user(msg.from_user.id, msg.from_user.username or "", msg.from_user.first_name or "")
    # Реферальная программа: /start ref_XXXXXX
    ref_bonus_txt = ""
    if is_new_user:
        parts = msg.text.split(maxsplit=1) if msg.text else []
        if len(parts) > 1 and parts[1].startswith("ref_"):
            ref_code = parts[1][4:]
            applied = await apply_referral(msg.from_user.id, ref_code)
            if applied:
                ref_bonus_txt = f"\n🎁 Бонус за реферальную ссылку: +{REF_NEW_BONUS}💰!"
                user = await get_user(msg.from_user.id)
    await assign_tasks(msg.from_user.id)
    pet = await get_active_pet(msg.from_user.id)
    eggs = await has_item(msg.from_user.id, "common_egg")
    event = await get_active_event()
    ev = f"\n🎪 <b>{event['name']}</b>" if event else ""
    vip = "👑 VIP  " if await is_vip(msg.from_user.id) else ""
    if pet:
        await safe_answer(msg, f"👋 <b>{msg.from_user.first_name}</b>! {vip}💰{user['coins']}{ev}{ref_bonus_txt}\n\n{pet_full(pet)}",
                          reply_markup=main_kb(), parse_mode="HTML")
    else:
        note = "\n\n🥚 У тебя есть стартовое яйцо! Напиши <b>яйца</b>" if eggs else ""
        await safe_answer(msg, f"🐾 <b>Добро пожаловать в PetWorld v5.2!</b>\n💰{user['coins']}{note}{ev}{ref_bonus_txt}\n\nИспользуй кнопки! 👇",
                          reply_markup=main_kb(), parse_mode="HTML")

# ── УНИВЕРСАЛЬНЫЙ ОБРАБОТЧИК ─────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(msg: Message):
    if not is_admin(msg.from_user.id):
        logger.warning(f"Non-admin /admin: {msg.from_user.id} | admins: {ADMIN_IDS}")
        return
    await safe_reply(msg,"🔧 <b>Панель администратора PetWorld v5.2</b>",
                     reply_markup=adm_kb(msg.from_user.id),parse_mode="HTML")


@router.message(Command("pets"))
async def cmd_pets(msg): await h_pets(msg)
@router.message(Command("eggs"))
async def cmd_eggs(msg): await h_eggs(msg)
@router.message(Command("shop"))
async def cmd_shop(msg): await h_shop(msg)
@router.message(Command("donate"))
async def cmd_donate(msg): await h_donate(msg)
@router.message(Command("work"))
async def cmd_work(msg): await h_work(msg)
@router.message(Command("daily"))
async def cmd_daily(msg): await h_daily(msg)
@router.message(Command("tasks"))
async def cmd_tasks(msg): await h_tasks(msg)
@router.message(Command("adventure"))
async def cmd_adventure(msg): await h_adventure(msg)
@router.message(Command("battle"))
async def cmd_battle(msg: Message, state: FSMContext): await h_battle(msg,state)
@router.message(Command("top"))
async def cmd_top(msg): await h_top(msg)
@router.message(Command("profile"))
async def cmd_profile(msg): await h_profile(msg)
@router.message(Command("clan"))
async def cmd_clan(msg: Message, state: FSMContext): await h_clan(msg,state)
@router.message(Command("market"))
async def cmd_market(msg): await h_market(msg)
@router.message(Command("trade"))
async def cmd_trade(msg: Message, state: FSMContext): await h_trade(msg,state)
@router.message(Command("tournament"))
async def cmd_tournament(msg): await h_tournament(msg)
@router.message(Command("event"))
async def cmd_event(msg): await h_event(msg)
@router.message(Command("rename"))
async def cmd_rename(msg: Message, state: FSMContext):
    pet=await get_active_pet(msg.from_user.id)
    if not pet: await safe_reply(msg,"Нет питомца!"); return
    await state.set_state(S.rename); await state.update_data(pet_id=pet['pet_id'])
    await safe_reply(msg,f"✏️ Новое имя для <b>{pet['name']}</b>:",parse_mode="HTML")
@router.message(Command("help"))
async def cmd_help(msg): await h_help(msg)
@router.message(Command("promo"))
async def cmd_promo(msg: Message, state: FSMContext): await h_promo(msg, state)
@router.message(Command("rebirth"))
async def cmd_rebirth(msg): await h_rebirth(msg)
@router.message(Command("boss"))
async def cmd_boss(msg): await h_boss(msg)
@router.message(Command("attack"))
async def cmd_attack(msg): await h_attack(msg)
@router.message(Command("fuse"))
async def cmd_fuse(msg): await h_fuse(msg)
@router.message(Command("top_crystals"))
async def cmd_top_crystals(msg): await h_top_crystals(msg)
@router.message(Command("top_rebirths"))
async def cmd_top_rebirths(msg): await h_top_rebirths(msg)
@router.message(Command("world_info"))
async def cmd_world_info(msg): await h_world_info(msg)
# v7.0
@router.message(Command("season"))
async def cmd_season(msg): await h_season(msg)
@router.message(Command("achievements"))
async def cmd_achievements(msg): await h_achievements(msg)
@router.message(Command("skills"))
async def cmd_skills(msg): await h_skills(msg)
@router.message(Command("auction"))
async def cmd_auction(msg): await h_auction(msg)
@router.message(Command("evolve"))
async def cmd_evolve(msg): await h_evolve(msg)
@router.message(Command("wheel"))
async def cmd_wheel(msg): await h_wheel(msg)

async def h_transfer(msg: Message):
    """Обработчик перевода монет — вызывается из /transfer и текстовых триггеров."""
    text = msg.text or ""
    parts = text.split(); amt = parts[1] if len(parts) > 1 else None
    if msg.reply_to_message and msg.reply_to_message.from_user and amt and amt.isdigit():
        tid = msg.reply_to_message.from_user.id; amount = int(amt)
        if amount <= 0: await safe_reply(msg, "❌ Сумма должна быть больше 0!"); return
        if tid == msg.from_user.id: await safe_reply(msg, "❌ Нельзя себе!"); return
        tgt = await get_user(tid)
        if not tgt: await safe_reply(msg, "❌ Игрок не зарегистрирован!"); return
        tax = max(1, int(amount * TRANSFER_TAX))
        ok, received, tax = await transfer_coins(msg.from_user.id, tid, amount)
        if not ok:
            await safe_reply(msg, f"❌ Недостаточно монет!\n💡 Нужно {amount + tax}💰 (сумма + комиссия {tax}💰)"); return
        u2 = await get_user(msg.from_user.id); tname = tgt.get('first_name') or tgt.get('username') or str(tid)
        await safe_reply(msg,
            f"✅ Перевод выполнен!\n\n"
            f"📤 Отправлено: <b>{amount}💰</b> → <b>{tname}</b>\n"
            f"💸 Комиссия: <b>{tax}💰</b> (25%)\n"
            f"💰 Твой остаток: <b>{u2['coins']}💰</b>",
            parse_mode="HTML")
    else:
        tax_ex = max(1, int(100 * TRANSFER_TAX))
        await safe_reply(msg,
            f"💸 <b>Перевод монет</b>\n\n"
            f"Ответь на сообщение игрока:\n<code>/transfer 500</code>\n\n"
            f"⚠️ Комиссия: <b>25%</b>\nПример: переводишь 100💰 — спишется {100+tax_ex}💰",
            parse_mode="HTML")

@router.message(Command("transfer"))
async def cmd_transfer(msg: Message): await h_transfer(msg)

@router.message(Command("premiumemoji"))
async def cmd_premiumemoji(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    args = (msg.text or "").split(maxsplit=1)
    pack = args[1].strip() if len(args) > 1 else PREMIUM_EMOJI_PACK
    await load_premium_emoji(msg.bot, pack)
    st = premium_emoji_status()
    state = "включены" if st["active"] else "выключены"
    await safe_reply(
        msg,
        f"🎨 <b>Premium emoji</b>\n\n"
        f"Статус: <b>{state}</b>\n"
        f"Пак: <code>{st['pack'] or 'не загружен'}</code>\n"
        f"Эмодзи в кэше: <b>{st['count']}</b>\n"
        f"Лимит на сообщение: <b>{st['max']}</b>\n\n"
        f"Перезагрузка: <code>/premiumemoji AnimatedEmoji</code>",
        parse_mode="HTML",
    )

@router.message(F.text)
async def universal(msg: Message, state: FSMContext):
    # Игнорируем команды — они обрабатываются отдельными хендлерами
    if msg.text and msg.text.startswith("/"):
        return

    # Проверка подписки и запуска бота в группах
    if msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        text_check = msg.text or ""
        if get_trigger(text_check) is not None or text_check.lower().startswith(("передать", "перевести")):
            if await maybe_block_group_user(msg):
                return

    current = await state.get_state()
    if current:
        if current == S.pet_name:        await s_pet_name(msg,state)
        elif current == S.rename:        await s_rename(msg,state)
        elif current == S.clan_name:     await s_clan_name(msg,state)
        elif current == S.clan_tag:      await s_clan_tag(msg,state)
        elif current == S.clan_join:     await s_clan_join(msg,state)
        elif current == S.sell_price:    await s_sell_price(msg,state)
        elif current == S.trade_target:  await s_trade_target(msg,state)
        elif current == S.trade_pet:     await s_trade_pet(msg,state)
        elif current == S.trade_coins:   await s_trade_coins(msg,state)
        elif current == S.battle_target: await s_battle_target(msg,state)
        elif current == S.adm_find:      await s_adm_find(msg,state)
        elif current == S.adm_coins_id:  await s_adm_coins_id(msg,state)
        elif current == S.adm_coins_amt: await s_adm_coins_amt(msg,state)
        elif current == S.adm_gc_amt:    await s_adm_gc_amt(msg,state)
        elif current == S.adm_egg_id:    await s_adm_egg_id(msg,state)
        elif current == S.adm_broadcast: await s_adm_broadcast(msg,state)
        elif current == S.adm_tour_name: await s_adm_tour_name(msg,state)
        elif current == S.adm_tour_prize:await s_adm_tour_prize(msg,state)
        elif current == S.adm_promo_code: await s_adm_promo_code(msg,state)
        elif current == S.adm_promo_coins: await s_adm_promo_coins(msg,state)
        elif current == S.adm_promo_pet: await s_adm_promo_pet(msg,state)
        elif current == S.adm_promo_uses: await s_adm_promo_uses(msg,state)
        elif current == S.adm_take_uid: await s_adm_take_uid(msg,state)
        elif current == S.adm_take_pet: await s_adm_take_pet(msg,state)
        elif current == S.adm_give_pet_uid: await s_adm_give_pet_uid(msg,state)
        elif current == S.adm_give_pet_type: await s_adm_give_pet_type(msg,state)
        elif current == S.adm_crystals_id:  await s_adm_crystals_id(msg,state)
        elif current == S.adm_crystals_amt: await s_adm_crystals_amt(msg,state)
        elif current == PromoState.waiting: await s_promo_input(msg,state)
        elif current == S.auction_bid:   await s_auction_bid(msg,state)
        elif current == S.auction_price: await s_auction_price(msg,state)
        return

    text = msg.text or ""

    # /transfer через реплай
    if text.lower().startswith(("/transfer","передать","перевести")):
        await h_transfer(msg); return

    tr = get_trigger(text)
    if   tr=="pets":       await h_pets(msg)
    elif tr=="eggs":       await h_eggs(msg)
    elif tr=="shop":       await h_shop(msg)
    elif tr=="work":       await h_work(msg)
    elif tr=="daily":      await h_daily(msg)
    elif tr=="battle":     await h_battle(msg,state)
    elif tr=="adventure":  await h_adventure(msg)
    elif tr=="tasks":      await h_tasks(msg)
    elif tr=="top":        await h_top(msg)
    elif tr=="profile":    await h_profile(msg)
    elif tr=="clan":       await h_clan(msg,state)
    elif tr=="market":     await h_market(msg)
    elif tr=="trade":      await h_trade(msg,state)
    elif tr=="duel":       await h_duel(msg)
    elif tr=="referral":   await h_referral(msg)
    elif tr=="tournament": await h_tournament(msg)
    elif tr=="donate":     await h_donate(msg)
    elif tr=="event":      await h_event(msg)
    elif tr=="help":       await h_help(msg)
    elif tr=="world":      await h_world(msg)
    elif tr=="locations":  await h_locations(msg)
    elif tr=="promo":      await h_promo(msg,state)
    elif tr=="rebirth":    await h_rebirth(msg)
    elif tr=="crystals":   await h_crystals(msg)
    elif tr=="boss":       await h_boss(msg)
    elif tr=="fuse":       await h_fuse(msg)
    # v7.0
    elif tr=="season":       await h_season(msg)
    elif tr=="achievements": await h_achievements(msg)
    elif tr=="skills":       await h_skills(msg)
    elif tr=="auction":      await h_auction(msg)
    elif tr=="evolve":       await h_evolve(msg)
    elif tr=="wheel":        await h_wheel(msg)


# ── ЛОКАЦИИ ────────────────────────────────────────────────────────────

async def h_locations(msg):
    uid = msg.from_user.id
    user = await get_user(uid)
    b = InlineKeyboardBuilder()
    for loc_id, loc in LOCATIONS.items():
        b.button(text=f"{loc['emoji']} {loc['name']}", callback_data=f"loc:view:{loc_id}:{uid}")
    b.adjust(1)
    await safe_answer(msg,
        f"🗺️ <b>Локации PetWorld</b>\n💰{user['coins']}\n\n"
        "Каждая локация — уникальный мир с особыми питомцами!\n"
        "Исследуй и получай яйца с местными существами.",
        reply_markup=b.as_markup(), parse_mode="HTML")

@router.callback_query(F.data.startswith("loc:view:"))
async def cb_loc_view(cb):
    parts = cb.data.split(":"); loc_id = parts[2]; uid = int(parts[3])
    loc = LOCATIONS.get(loc_id)
    if not loc: await cb.answer("Не найдено!"); return
    pets_list = "\n".join(
        f"{PETS[p]['emoji']} <b>{PETS[p]['name']}</b> — {RARITY_NAMES.get(PETS[p]['rarity'],'')}"
        for p in loc['pets'] if p in PETS
    )
    egg = EGG_TYPES.get(loc['egg'], {})
    b = InlineKeyboardBuilder()
    b.button(text=f"🧭 Исследовать ({loc['explore_reward'][0]}-{loc['explore_reward'][1]}💰)",
             callback_data=f"loc:explore:{loc_id}:{uid}")
    b.button(text=f"🥚 Купить {egg.get('name','яйцо')} ({egg.get('price',0)}💰)",
             callback_data=f"sb:{loc['egg']}:{uid}")
    b.button(text="◀️ Назад", callback_data=f"loc:list:{uid}")
    b.adjust(1)
    await safe_edit(cb.message,
        f"{loc['emoji']} <b>{loc['name']}</b>\n\n"
        f"_{loc['desc']}_\n\n"
        f"🐾 <b>Местные существа:</b>\n{pets_list}\n\n"
        f"🥚 Яйцо локации: <b>{egg.get('name','')}</b>",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("loc:list:"))
async def cb_loc_list(cb):
    uid = int(cb.data.split(":")[-1])
    user = await get_user(uid)
    b = InlineKeyboardBuilder()
    for loc_id, loc in LOCATIONS.items():
        b.button(text=f"{loc['emoji']} {loc['name']}", callback_data=f"loc:view:{loc_id}:{uid}")
    b.adjust(1)
    await safe_edit(cb.message,
        f"🗺️ <b>Локации PetWorld</b>\n💰{user['coins']}\n\nВыбери локацию для исследования:",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("loc:explore:"))
async def cb_loc_explore(cb):
    parts = cb.data.split(":"); loc_id = parts[2]; uid = int(parts[3])
    loc = LOCATIONS.get(loc_id)
    if not loc: await cb.answer("Ошибка!"); return
    user = await get_user(uid)
    cd = cd_left(user.get('last_explore'), 3600)
    if cd:
        await cb.answer(f"⏳ Через {fmt_t(cd)}", show_alert=True); return
    import random
    earned = random.randint(*loc['explore_reward'])
    event = await get_active_event()
    if event: earned = int(earned * event.get('mult_coins', 1.0))
    await update_coins(uid, earned)
    await set_ts(uid, 'last_explore')
    await add_season_tokens(uid, SEASON_TOKEN_REWARDS["adventure"])
    await progress_daily_quest(uid, "explore")
    # Шанс найти яйцо 20%
    found_egg = random.random() < 0.20
    egg_txt = ""
    if found_egg:
        await add_item(uid, loc['egg'])
        egg = EGG_TYPES.get(loc['egg'], {})
        egg_txt = f"\n🥚 Нашёл <b>{egg.get('name','яйцо')}</b>!"
    # Случайная находка — местный питомец прямо
    events_txt = random.choice([
        f"Ты прошёлся по {loc['name']} и нашёл сокровища!",
        f"Местные существа помогли тебе найти монеты!",
        f"Ты исследовал {loc['name']} и вернулся с добычей!",
        f"Опасное приключение в {loc['name']} окупилось!",
    ])
    u2 = await get_user(uid)
    b = InlineKeyboardBuilder()
    b.button(text="◀️ К локации", callback_data=f"loc:view:{loc_id}:{uid}")
    b.adjust(1)
    await safe_edit(cb.message,
        f"{loc['emoji']} <b>{events_txt}</b>\n\n"
        f"💰 +{earned} монет{egg_txt}\n"
        f"💰 Баланс: {u2['coins']}",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()



# ═══════════════════════════════════════════════════
# ПРОВЕРКА ПОДПИСКИ НА КАНАЛ И ЛС
# ═══════════════════════════════════════════════════

async def check_subscription(bot, user_id: int) -> bool:
    """Проверяет подписку пользователя на NEWS_CHANNEL."""
    try:
        member = await bot.get_chat_member(NEWS_CHANNEL, user_id)
        return member.status not in ("left", "kicked", "banned")
    except Exception:
        return True  # если канал недоступен — не блокируем

def sub_required_kb():
    """Кнопки для неподписанного пользователя."""
    b = InlineKeyboardBuilder()
    b.button(text="📢 Подписаться на канал", url=f"https://t.me/{NEWS_CHANNEL.lstrip('@')}")
    b.button(text="✅ Я подписался!", callback_data="check_sub")
    b.adjust(1)
    return b.as_markup()

class SubscriptionMiddleware(BaseMiddleware):
    """Блокирует все действия пока пользователь не подписан на канал."""
    
    EXEMPT_CALLBACKS = {"check_sub"}  # эти колбэки пропускаем без проверки
    
    async def __call__(self, handler, event: TelegramObject, data: dict):
        from aiogram.types import Message, CallbackQuery
        
        # Определяем пользователя и бота
        if isinstance(event, Message):
            user = event.from_user
            bot  = event.bot
            chat_type = event.chat.type
            # Разрешаем только ЛС
            if chat_type != ChatType.PRIVATE:
                return  # в группах не отвечаем вообще
            # /start всегда пропускаем (для обработки реф-кода)
            if event.text and event.text.startswith("/start"):
                return await handler(event, data)
        elif isinstance(event, CallbackQuery):
            user = event.from_user
            bot  = event.message.bot
            # check_sub — пропускаем без проверки
            if event.data in self.EXEMPT_CALLBACKS:
                return await handler(event, data)
        else:
            return await handler(event, data)

        # Проверяем подписку
        if not await check_subscription(bot, user.id):
            sub_msg = (
                "📢 <b>Для игры нужно подписаться на канал!</b>\n\n"
                f"Подпишись на {NEWS_CHANNEL} и нажми «✅ Я подписался!»"
            )
            if isinstance(event, Message):
                await event.answer(sub_msg, reply_markup=sub_required_kb(), parse_mode="HTML")
            elif isinstance(event, CallbackQuery):
                await event.answer("❌ Сначала подпишись на канал!", show_alert=True)
                try:
                    await event.message.answer(sub_msg, reply_markup=sub_required_kb(), parse_mode="HTML")
                except: pass
            return  # не передаём дальше

        return await handler(event, data)

@router.callback_query(F.data == "check_sub")
async def cb_check_sub(cb: CallbackQuery):
    """Пользователь нажал «Я подписался»."""
    if await check_subscription(cb.message.bot, cb.from_user.id):
        await cb.answer("✅ Отлично! Теперь можешь играть.", show_alert=True)
        try: await cb.message.delete()
        except: pass
        # Показываем главное меню
        user = await get_or_create_user(cb.from_user.id,
                                        cb.from_user.username or "",
                                        cb.from_user.first_name or "")
        await cb.message.answer(
            f"🐾 <b>Добро пожаловать в PetWorld!</b>\n💰 {user['coins']}",
            reply_markup=main_kb(), parse_mode="HTML"
        )
    else:
        await cb.answer("❌ Ты ещё не подписан! Подпишись и попробуй снова.", show_alert=True)


# ═══════════════════════════════════════════════════
# РЕФЕРАЛЬНАЯ СИСТЕМА
# ═══════════════════════════════════════════════════

import hashlib

def make_ref_code(uid: int) -> str:
    """Генерирует уникальный реф-код из user_id."""
    h = hashlib.md5(f"petworld_{uid}".encode()).hexdigest()[:8].upper()
    return f"PW{h}"

async def ensure_ref_code(uid: int) -> str:
    """Гарантирует что у пользователя есть реф-код, создаёт если нет."""
    user = await get_user(uid)
    if user and user.get("ref_code"):
        return user["ref_code"]
    code = make_ref_code(uid)
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("UPDATE users SET ref_code=? WHERE user_id=?", (code, uid))
            await db.commit()
        except: pass
    return code

async def apply_referral(new_uid: int, ref_code: str) -> bool:
    """Применяет реферальный код при первом запуске. Возвращает True если успех."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Ищем владельца реф-кода
        async with db.execute("SELECT user_id, ref_count FROM users WHERE ref_code=?", (ref_code,)) as c:
            row = await c.fetchone()
        if not row: return False
        ref_uid = row["user_id"]
        if ref_uid == new_uid: return False  # нельзя себя рефернуть
        # Проверяем что новый юзер ещё не использовал реферал
        async with db.execute("SELECT referred_by FROM users WHERE user_id=?", (new_uid,)) as c:
            nrow = await c.fetchone()
        if nrow and nrow[0]: return False  # уже использовал
        # Начисляем реферёру 1000 монет и +1 к счётчику
        await db.execute(
            "UPDATE users SET coins=coins+?, total_earned=total_earned+?, ref_count=ref_count+1 WHERE user_id=?",
            (REF_REWARD, REF_REWARD, ref_uid)
        )
        # Начисляем новому игроку обещанный бонус
        await db.execute(
            "UPDATE users SET coins=coins+?, total_earned=total_earned+? WHERE user_id=?",
            (REF_NEW_BONUS, REF_NEW_BONUS, new_uid)
        )
        # Фиксируем что новый игрок пришёл по рефералу
        await db.execute("UPDATE users SET referred_by=? WHERE user_id=?", (ref_uid, new_uid))
        await db.commit()
    return True

async def h_referral(msg):
    uid = msg.from_user.id
    code = await ensure_ref_code(uid)
    user = await get_user(uid)
    ref_count = user.get("ref_count") or 0
    bot_info = await msg.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{code}"
    await safe_answer(msg,
        f"👥 <b>Реферальная программа</b>\n\n"
        f"Твой реф-код: <code>{code}</code>\n"
        f"Твоя ссылка:\n{ref_link}\n\n"
        f"👥 Приглашено друзей: <b>{ref_count}</b>\n"
        f"💰 За каждого: <b>{REF_REWARD:,}💰</b> тебе\n\n"
        f"Друг получает бонус +500💰 при старте!",
        parse_mode="HTML")

# ═══════════════════════════════════════════════════
# СИСТЕМА МИРОВ
# ═══════════════════════════════════════════════════

async def get_user_world(uid):
    user = await get_user(uid)
    return parse_wid(user.get("world", 1))

def parse_wid(raw):
    """Безопасно парсит world id: int для обычных миров, str для секретных."""
    if raw is None:
        return 1
    # Если это число или строка-число ("1", "10") — возвращаем int
    try:
        return int(raw)
    except (ValueError, TypeError):
        s = str(raw)
        # Секретные миры: "shadow", "forge", "origin"
        if s in ("shadow", "forge", "origin"):
            return s
        return 1  # fallback

async def set_user_world(uid, world_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET world=? WHERE user_id=?", (world_id, uid))
        await db.commit()

WORLD_MASTERY_THRESHOLDS = [0, 20, 50, 100, 180, 300, 500, 800, 1200, 1800]

def world_mastery_level(mastery):
    m = max(0, int(mastery or 0))
    level = 1
    for i, need in enumerate(WORLD_MASTERY_THRESHOLDS):
        if m >= need:
            level = i + 1
    return min(10, level)

def world_mastery_progress(mastery):
    m = max(0, int(mastery or 0))
    level = world_mastery_level(m)
    if level >= 10:
        return level, 1.0, 0, 0
    cur = WORLD_MASTERY_THRESHOLDS[level - 1]
    nxt = WORLD_MASTERY_THRESHOLDS[level]
    return level, min(1.0, (m - cur) / max(1, nxt - cur)), m - cur, nxt - cur

async def get_world_progress(uid, wid):
    key = str(wid)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM world_progress WHERE user_id=? AND world_id=?", (uid, key)) as c:
            row = await c.fetchone()
        if row:
            return dict(row)
        await db.execute("INSERT OR IGNORE INTO world_progress (user_id, world_id) VALUES (?,?)", (uid, key))
        await db.commit()
    return {"user_id": uid, "world_id": key, "currency": 0, "mastery": 0, "boss_kills": 0, "last_boss": None}

async def add_world_currency(uid, wid, amount):
    key = str(wid)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO world_progress (user_id, world_id) VALUES (?,?)", (uid, key))
        await db.execute("UPDATE world_progress SET currency=MAX(0,currency+?) WHERE user_id=? AND world_id=?",
                         (int(amount), uid, key))
        async with db.execute("SELECT currency FROM world_progress WHERE user_id=? AND world_id=?", (uid, key)) as c:
            row = await c.fetchone()
        await db.commit()
    return int(row[0]) if row else 0

async def spend_world_currency(uid, wid, amount):
    key = str(wid)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO world_progress (user_id, world_id) VALUES (?,?)", (uid, key))
        cur = await db.execute(
            "UPDATE world_progress SET currency=currency-? WHERE user_id=? AND world_id=? AND currency>=?",
            (int(amount), uid, key, int(amount))
        )
        ok = cur.rowcount > 0
        await db.commit()
    return ok

async def add_world_mastery(uid, wid, amount):
    key = str(wid)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO world_progress (user_id, world_id) VALUES (?,?)", (uid, key))
        await db.execute("UPDATE world_progress SET mastery=mastery+? WHERE user_id=? AND world_id=?",
                         (int(amount), uid, key))
        await db.commit()

async def record_world_boss(uid, wid, win):
    key = str(wid)
    ts = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO world_progress (user_id, world_id) VALUES (?,?)", (uid, key))
        if win:
            await db.execute("UPDATE world_progress SET last_boss=?, boss_kills=boss_kills+1 WHERE user_id=? AND world_id=?",
                             (ts, uid, key))
        else:
            await db.execute("UPDATE world_progress SET last_boss=? WHERE user_id=? AND world_id=?", (ts, uid, key))
        await db.commit()

def world_shop_catalog(wid, w):
    cur = w.get("currency", {})
    raw = cur.get("shop", {})
    supported = {"egg", "xp", "heal", "full_heal", "coins", "craft", "craft_set", "pet_level"}
    items = {k: dict(v) for k, v in raw.items() if v.get("action") in supported}
    costs = [int(v.get("cost", 0)) for v in raw.values() if int(v.get("cost", 0)) > 0]
    base = min(costs) if costs else 50
    try:
        wnum = int(wid)
    except (TypeError, ValueError):
        wnum = 10
    if not any(v.get("action") == "egg" for v in items.values()):
        exclusive = next((eid for eid, e in w.get("eggs", {}).items() if e.get("exclusive")), None)
        if exclusive:
            ee = w["eggs"][exclusive]
            items["_egg_exclusive"] = {"name": f"{ee['emoji']} {ee['name']}", "cost": max(50, base * 2),
                                        "action": "egg", "egg_id": exclusive}
    if not any(v.get("action") == "xp" for v in items.values()):
        wr = w.get("work_reward", (100, 100))
        xp_val = max(100, int((sum(wr) / 2) * 0.5))
        items["_xp"] = {"name": f"✨ Опыт питомцу (+{xp_val:,})", "cost": base,
                        "action": "xp", "value": xp_val}
    if not any(v.get("action") in ("heal", "full_heal") for v in items.values()):
        items["_heal"] = {"name": "❤️ Полное лечение питомца", "cost": base, "action": "full_heal"}
    items["_chest"] = {"name": "🎁 Сундук мира", "cost": max(50, base * 2), "action": "chest"}
    return items

async def world_main_text(uid, wid):
    user = await get_user(uid)
    w = WORLDS.get(wid) or WORLDS[1]
    prog = await get_world_progress(uid, wid)
    cur = w.get("currency", {})
    lvl, ratio, cm, nm = world_mastery_progress(prog.get("mastery", 0))
    pet = await get_active_pet(uid)
    syn_txt = ""
    if pet:
        syn = get_world_synergy_bonus(wid, pet["pet_type"])
        if syn["coins_mult"] > 1 or syn["atk"] or syn["def"] or syn["hp"]:
            syn_txt = f"\n⚡ Синергия: +{int((syn['coins_mult'] - 1) * 100)}% монет"
            if syn["atk"]: syn_txt += f", +{syn['atk']} ATK"
            if syn["def"]: syn_txt += f", +{syn['def']} DEF"
            if syn["hp"]: syn_txt += f", +{syn['hp']} HP"
    boss = w.get("boss", {})
    boss_txt = "нет"
    if boss:
        cd_h = max(4, min(48, int(boss.get("cooldown_hours", 24))))
        cd = cd_left(prog.get("last_boss"), cd_h * 3600)
        boss_txt = f"{boss['emoji']} {boss['name']}"
        boss_txt += f" — ⏳ через {fmt_t(cd)}" if cd else " — готов к бою"
    pet_txt = f"🐾 Активный: <b>{pet['name']}</b> ур.{pet['level']}{syn_txt}" if pet else "🐾 Нет активного питомца"
    is_secret = w.get("is_secret", False)
    world_num = f"Секретный мир: {w['name']}" if is_secret else f"Мир {wid}/10"
    mastery_txt = f"{cm}/{nm}" if nm else "MAX"
    return (
        f"{w['bg']} <b>{w['name']}</b>\n<i>{w['desc']}</i>\n\n"
        f"💰 {user['coins']:,}   {cur.get('emoji', '🪙')} <b>{prog.get('currency', 0):,}</b> {cur.get('name', 'валюта')}\n"
        f"🏅 Мастерство: <b>{lvl}/10</b> {mbar(int(ratio * 100), 100, 8)} ({mastery_txt})\n"
        f"👹 {boss_txt}\n"
        f"{pet_txt}\n\n{world_num}"
    )

def world_kb(uid, current_world):
    """Главное меню мира."""
    b = InlineKeyboardBuilder()
    b.button(text=f"🥚 Яйца мира", callback_data=f"world:eggs:{uid}")
    b.button(text=f"💰 Работать в мире", callback_data=f"world:work:{uid}")
    b.button(text=f"💱 Магазин валюты", callback_data=f"world:currshop:{uid}")
    b.button(text=f"👹 Босс мира", callback_data=f"world:boss:{uid}")
    b.button(text=f"🏪 Магазин мира", callback_data=f"world:shop:{uid}")
    b.button(text=f"🗺️ Сменить мир", callback_data=f"world:select:{uid}")
    b.adjust(1)
    return b.as_markup()

async def h_world(msg):
    uid = msg.from_user.id
    user = await get_user(uid)
    wid = parse_wid(user.get("world", 1))
    text = await world_main_text(uid, wid)
    await safe_answer(msg, text, reply_markup=world_kb(uid, wid), parse_mode="HTML")

@router.callback_query(F.data.startswith("world:eggs:"))
async def cb_world_eggs(cb):
    uid = int(cb.data.split(":")[-1])
    user = await get_user(uid)
    wid = parse_wid(user.get("world", 1))
    w = WORLDS[wid]
    b = InlineKeyboardBuilder()
    inv = await get_inventory(uid)
    inv_map = {i["item_id"]: i["quantity"] for i in inv}
    for eid, egg in w["eggs"].items():
        qty = inv_map.get(eid, 0)
        qty_txt = f" (есть {qty})" if qty else ""
        b.button(text=f"{egg['emoji']} {egg['name']} — {egg['price']}💰{qty_txt}",
                 callback_data=f"world:buyegg:{eid}:{uid}")
    # Яйца в инвентаре
    has_eggs = [eid for eid in w["eggs"] if inv_map.get(eid, 0) > 0]
    if has_eggs:
        b.button(text="🐣 Открыть яйцо", callback_data=f"world:hatchmenu:{uid}")
    b.button(text="◀️ Назад", callback_data=f"world:main:{uid}")
    b.adjust(1)
    prog = await get_world_progress(uid, wid)
    cur = w.get("currency", {})
    await safe_edit(cb.message,
        f"{w['bg']} <b>Яйца — {w['name']}</b>\n"
        f"💰 {user['coins']:,}   {cur.get('emoji', '🪙')} {prog.get('currency', 0):,}\n\nВыбери яйцо для покупки:",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("world:buyegg:"))
async def cb_world_buyegg(cb):
    parts = cb.data.split(":"); eid = parts[2]; uid = int(parts[3])
    user = await get_user(uid)
    wid = parse_wid(user.get("world", 1))
    w = WORLDS[wid]
    egg = w["eggs"].get(eid)
    if not egg: await cb.answer("Яйцо не найдено!", show_alert=True); return
    if user["coins"] < egg["price"]:
        await cb.answer(f"❌ Нужно {egg['price']}💰!", show_alert=True); return
    await update_coins(uid, -egg["price"])
    await add_item(uid, eid)
    u2 = await get_user(uid)
    await cb.answer(f"✅ {egg['emoji']} {egg['name']} куплено! Остаток: {u2['coins']}💰")

@router.callback_query(F.data.startswith("world:hatchmenu:"))
async def cb_world_hatchmenu(cb):
    uid = int(cb.data.split(":")[-1])
    user = await get_user(uid)
    wid = parse_wid(user.get("world", 1))
    w = WORLDS[wid]
    inv = await get_inventory(uid)
    inv_map = {i["item_id"]: i["quantity"] for i in inv}
    b = InlineKeyboardBuilder()
    for eid, egg in w["eggs"].items():
        qty = inv_map.get(eid, 0)
        if qty > 0:
            b.button(text=f"{egg['emoji']} {egg['name']} ×{qty}",
                     callback_data=f"world:hatch:{eid}:{uid}")
    # Также обычные яйца из EGG_TYPES
    for eid, egg in EGG_TYPES.items():
        qty = inv_map.get(eid, 0)
        if qty > 0:
            b.button(text=f"{egg['emoji']} {egg['name']} ×{qty}",
                     callback_data=f"world:hatch:{eid}:{uid}")
    b.button(text="◀️ Назад", callback_data=f"world:eggs:{uid}")
    b.adjust(1)
    await safe_edit(cb.message, "🐣 <b>Какое яйцо открыть?</b>",
                    reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("world:hatch:"))
async def cb_world_hatch(cb):
    parts = cb.data.split(":"); eid = parts[2]; uid = int(parts[3])
    # Найти яйцо в мировых или обычных
    all_eggs = {**WORLD_EGGS, **EGG_TYPES}
    egg = all_eggs.get(eid)
    if not egg: await cb.answer("Яйцо не найдено!", show_alert=True); return
    inv = await get_inventory(uid)
    inv_map = {i["item_id"]: i["quantity"] for i in inv}
    if inv_map.get(eid, 0) < 1:
        await cb.answer("Нет яйца!", show_alert=True); return
    ok = await remove_item(uid, eid, 1)
    if not ok: await cb.answer("Ошибка!", show_alert=True); return
    # Хетчим
    pool = egg["pool"]
    total = sum(pool.values()); roll = random.randint(1, total); cum = 0
    pet_type = list(pool.keys())[0]
    for pt, w_val in pool.items():
        cum += w_val
        if roll <= cum: pet_type = pt; break
    p = PETS.get(pet_type, PETS["dog"])
    # 1% шанс Shiny
    is_shiny = random.random() < 0.01
    await create_pet(uid, p["name"], pet_type, is_shiny=is_shiny)
    if is_shiny:
        await check_and_grant_achievement(uid, "shiny_pet")
    r = RARITY_EMOJI.get(p["rarity"], "⚪")
    shiny_txt = "\n✨ <b>SHINY!</b> +15% к характеристикам!" if is_shiny else ""
    await cb.answer(f"🎉 {p['emoji']} {p['name']}{'  ✨SHINY!' if is_shiny else ''}!")
    await safe_edit(cb.message,
        f"🐣 <b>Яйцо открыто!</b>\n\n"
        f"{p['emoji']} <b>{p['name']}</b> {r}\n"
        f"<i>{RARITY_NAMES.get(p['rarity'],'')}</i>{shiny_txt}\n\n"
        f"Питомец добавлен! Напиши <b>питомцы</b>",
        parse_mode="HTML")
    await progress_task(uid, "hatch")
    try:
        user_now = await get_user(uid)
        wid_now = parse_wid(user_now.get("world", 1))
        w_now = WORLDS.get(wid_now) or {}
        if eid in (w_now.get("eggs") or {}):
            await add_world_mastery(uid, wid_now, 2)
    except Exception:
        pass

@router.callback_query(F.data.startswith("world:work:"))
async def cb_world_work(cb):
    uid = int(cb.data.split(":")[-1])
    user = await get_user(uid)
    wid = parse_wid(user.get("world", 1))
    w = WORLDS.get(wid) or WORLDS[1]
    cd = cd_left(user.get("last_work"), 3600)
    if cd:
        await cb.answer(f"⏳ Через {fmt_t(cd)}", show_alert=True); return
    pet = await get_active_pet(uid)
    syn = get_world_synergy_bonus(wid, pet["pet_type"]) if pet else {"atk": 0, "def": 0, "hp": 0, "coins_mult": 1.0}
    prog = await get_world_progress(uid, wid)
    mlvl, _, _, _ = world_mastery_progress(prog.get("mastery", 0))
    event = await get_active_event()
    mult = event.get("mult_coins", 1.0) if event else 1.0
    vip_mult = 1.5 if await is_vip(uid) else 1.0
    rebirth_bonus = user.get("rebirth_bonus") or 1.0
    mastery_mult = 1 + 0.04 * (mlvl - 1)
    earned = int(random.randint(*w["work_reward"]) * mult * vip_mult * rebirth_bonus * syn["coins_mult"] * mastery_mult)
    await update_coins(uid, earned)
    await set_ts(uid, "last_work")
    wnum = int(wid) if str(wid).isdigit() else 10
    wc = max(1, int(earned * 0.10))
    new_cur = await add_world_currency(uid, wid, wc)
    await add_world_mastery(uid, wid, 4)
    await add_season_tokens(uid, SEASON_TOKEN_REWARDS["work"])
    crystal_txt = ""
    if random.random() < 0.10:
        crystals = random.randint(1, 3 + wnum)
        await update_crystals(uid, crystals)
        crystal_txt = f"\n💎 +{crystals} кристаллов!"
    done = await progress_task(uid, "work")
    if done:
        await update_coins(uid, done["reward_coins"])
    event_txt = ""
    if random.random() < 0.35:
        ev = roll_world_event(wid)
        if ev:
            ev_coin = 0
            ev_cur = 0
            ev_xp = 0
            ev_heal = 0
            ev_egg = ev.get("egg_id")
            ev_pet = ev.get("pet_id")
            if ev.get("coins"):
                ev_coin = random.randint(*ev["coins"])
                await update_coins(uid, ev_coin)
            if ev.get("world_currency"):
                ev_cur = random.randint(*ev["world_currency"])
                await add_world_currency(uid, wid, ev_cur)
            if ev.get("xp") and pet:
                ev_xp = random.randint(*ev["xp"])
                await add_xp(pet["pet_id"], ev_xp)
            if ev.get("heal") and pet:
                ev_heal = int(ev["heal"])
                fresh = await get_pet_by_id(pet["pet_id"], uid)
                if fresh:
                    await update_pet(pet["pet_id"], health=min(fresh["max_health"], fresh["health"] + ev_heal))
            ev_egg_name = ""
            if ev_egg:
                await add_item(uid, ev_egg)
                eg = WORLD_EGGS.get(ev_egg) or EGG_TYPES.get(ev_egg, {})
                ev_egg_name = f"{eg.get('emoji', '🥚')} {eg.get('name', 'яйцо')}"
            pet_got = ""
            if ev_pet and ev_pet in PETS:
                await create_pet(uid, PETS[ev_pet]["name"], ev_pet, random.random() < 0.01)
                pet_got = f"\n🐾 Найден питомец: <b>{PETS[ev_pet]['name']}</b>!"
            bits = []
            if ev_coin:
                bits.append(f"{ev_coin:+d}💰")
            if ev_cur:
                bits.append(f"{ev_cur:+d}{w['currency']['emoji']}")
            if ev_xp:
                bits.append(f"+{ev_xp} XP")
            if ev_heal:
                bits.append(f"+{ev_heal} HP")
            if ev_egg_name:
                bits.append(ev_egg_name)
            reward_txt = ", ".join(bits)
            risk = " ⚠️" if ev.get("risk") else ""
            event_txt = f"\n\n{ev.get('emoji', '🎲')} <b>{ev.get('name', 'Событие')}</b>{risk}\n<i>{ev.get('desc', '')}</i>"
            if reward_txt:
                event_txt += f"\n🎁 {reward_txt}{pet_got}"
            elif pet_got:
                event_txt += pet_got
    u2 = await get_user(uid)
    prog2 = await get_world_progress(uid, wid)
    mlvl2, _, cm, nm = world_mastery_progress(prog2.get("mastery", 0))
    cur = w["currency"]
    works = ["🔧 Починил механизм", "🌾 Собрал урожай", "⚔️ Охранял территорию",
             "🏗️ Построил здание", "🎨 Украсил мир", "🚀 Исследовал местность"]
    rb_note = f" 🔥×{rebirth_bonus:.1f}" if rebirth_bonus > 1 else ""
    bon = f"\n✅ Задание! +{done['reward_coins']}💰" if done else ""
    await safe_edit(cb.message,
        f"{random.choice(works)} в {w['name']}!\n"
        f"+{earned}💰{rb_note}\n"
        f"{cur['emoji']} +{wc} {cur['name']} (всего {prog2.get('currency', new_cur):,})\n"
        f"🏅 Мастерство: {mlvl2}/10 (+{(mlvl2 - 1) * 4}% к работе) {mbar(int((cm / nm if nm else 1) * 100), 100, 6)}\n"
        f"{crystal_txt}{bon}{event_txt}\n\n💰 Баланс: {u2['coins']:,}",
        reply_markup=world_kb(uid, wid), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("world:shop:"))
async def cb_world_shop(cb):
    uid = int(cb.data.split(":")[-1])
    user = await get_user(uid)
    wid = parse_wid(user.get("world", 1))
    w = WORLDS[wid]
    b = InlineKeyboardBuilder()
    for wid_item in w["shop_weapons"]:
        wp = WEAPONS.get(wid_item)
        if wp:
            b.button(text=f"{wp['emoji']} {wp['name']} +{wp['atk_bonus']}⚔️ — {wp['price']}💰",
                     callback_data=f"sb:{wid_item}:{uid}")
    for aid in w["shop_armor"]:
        ar = ARMOR.get(aid)
        if ar:
            b.button(text=f"{ar['emoji']} {ar['name']} +{ar['def_bonus']}🛡️ — {ar['price']}💰",
                     callback_data=f"sb:{aid}:{uid}")
    b.button(text="◀️ Назад", callback_data=f"world:main:{uid}")
    b.adjust(1)
    prog = await get_world_progress(uid, wid)
    cur = w.get("currency", {})
    await safe_edit(cb.message,
        f"{w['bg']} <b>Магазин — {w['name']}</b>\n"
        f"💰 {user['coins']:,}   {cur.get('emoji', '🪙')} {prog.get('currency', 0):,}",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

async def _open_world_chest(uid, wid, w, cost):
    wnum = int(wid) if str(wid).isdigit() else 10
    wr = w.get("work_reward", (100, 100))
    coins = random.randint(*wr) * 2
    cur = random.randint(5, max(10, cost // 3))
    crystals = random.randint(1, 2 + wnum)
    xp = random.randint(20, 60) * wnum
    egg_id = None
    exclusive = next((eid for eid, e in w.get("eggs", {}).items() if e.get("exclusive")), None)
    if exclusive and random.random() < 0.10:
        egg_id = exclusive
    await update_coins(uid, coins)
    await add_world_currency(uid, wid, cur)
    await update_crystals(uid, crystals)
    pet = await get_active_pet(uid)
    if pet and xp:
        await add_xp(pet["pet_id"], xp)
    egg_txt = ""
    if egg_id:
        await add_item(uid, egg_id)
        eg = w.get("eggs", {}).get(egg_id, {})
        egg_txt = f", 🥚 {eg.get('emoji', '')} {eg.get('name', 'яйцо')}"
    cur_data = w.get("currency", {})
    parts = [f"+{coins:,}💰", f"+{cur:,}{cur_data.get('emoji', '🪙')}", f"+{crystals}💎"]
    if pet:
        parts.append(f"+{xp:,} XP")
    return ", ".join(parts) + egg_txt

@router.callback_query(F.data.startswith("world:currshop:"))
async def cb_world_currshop(cb):
    uid = int(cb.data.split(":")[-1])
    user = await get_user(uid)
    wid = parse_wid(user.get("world", 1))
    w = WORLDS.get(wid) or WORLDS[1]
    prog = await get_world_progress(uid, wid)
    cur = w.get("currency", {})
    cat = world_shop_catalog(wid, w)
    b = InlineKeyboardBuilder()
    for key, item in cat.items():
        b.button(text=f"{item['name']} — {item['cost']} {cur.get('emoji', '🪙')}",
                 callback_data=f"world:buycurr:{key}:{uid}")
    b.button(text="◀️ Назад", callback_data=f"world:main:{uid}")
    b.adjust(1)
    await safe_edit(cb.message,
        f"{cur.get('emoji', '🪙')} <b>{cur.get('name', 'Валюта')}: {prog.get('currency', 0):,}</b>\n"
        f"<i>Зарабатывается работой, событиями и боссами мира.</i>\n\nВыбери товар:",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("world:buycurr:"))
async def cb_world_buycurr(cb):
    parts = cb.data.split(":")
    key = parts[2]
    uid = int(parts[3])
    user = await get_user(uid)
    wid = parse_wid(user.get("world", 1))
    w = WORLDS.get(wid) or WORLDS[1]
    cat = world_shop_catalog(wid, w)
    item = cat.get(key)
    if not item:
        await cb.answer("Товар не найден!", show_alert=True); return
    cost = int(item.get("cost", 0))
    if not await spend_world_currency(uid, wid, cost):
        await cb.answer("Недостаточно мировой валюты!", show_alert=True); return
    action = item.get("action")
    try:
        if action == "egg":
            await add_item(uid, item["egg_id"])
            eg = w.get("eggs", {}).get(item["egg_id"], {})
            msg = f"🥚 Получено: <b>{eg.get('emoji', '')} {eg.get('name', 'яйцо')}</b>"
        elif action == "xp":
            pet = await get_active_pet(uid)
            if not pet:
                await add_world_currency(uid, wid, cost)
                await cb.answer("Нужен активный питомец!", show_alert=True); return
            lvl, leveled = await add_xp(pet["pet_id"], int(item.get("value", 0)))
            msg = f"✨ +{int(item.get('value', 0)):,} XP" + (f"\n🎉 Новый уровень: <b>{lvl}</b>!" if leveled else "")
        elif action in ("heal", "full_heal"):
            pet = await get_active_pet(uid)
            if not pet:
                await add_world_currency(uid, wid, cost)
                await cb.answer("Нужен активный питомец!", show_alert=True); return
            new_hp = pet["max_health"] if action == "full_heal" else min(pet["max_health"], pet["health"] + int(item.get("value", 0)))
            await update_pet(pet["pet_id"], health=new_hp)
            msg = f"❤️ HP восстановлено: <b>{new_hp}/{pet['max_health']}</b>"
        elif action == "coins":
            value = int(item.get("value", 0))
            await update_coins(uid, value)
            msg = f"💰 Получено: <b>+{value:,} монет</b>"
        elif action == "craft":
            await add_item(uid, item["item"])
            it = WEAPONS.get(item["item"]) or ARMOR.get(item["item"]) or {}
            msg = f"⚔️ Скрафчено: <b>{it.get('emoji', '')} {it.get('name', item['item'])}</b>"
        elif action == "craft_set":
            for iid in item.get("items", []):
                await add_item(uid, iid)
            msg = "⚡ <b>Набор получен!</b>"
        elif action == "pet_level":
            pet = await get_active_pet(uid)
            if not pet:
                await add_world_currency(uid, wid, cost)
                await cb.answer("Нужен активный питомец!", show_alert=True); return
            add_lv = int(item.get("value", 5))
            nl = pet["level"] + add_lv
            base_hp = PETS.get(pet["pet_type"], PETS["dog"])["hp"]
            max_hp = base_hp + (nl - 1) * 10
            await update_pet(pet["pet_id"], level=nl, xp=0, xp_to_next=xp_to_next_for_level(nl),
                             max_health=max_hp, health=max_hp,
                             skill_points=(pet.get("skill_points") or 0) + add_lv)
            msg = f"⬆️ Питомец повышен до <b>ур. {nl}</b>!"
        elif action == "chest":
            loot = await _open_world_chest(uid, wid, w, cost)
            msg = f"🎁 <b>Сундук открыт!</b>\n{loot}"
        else:
            await add_world_currency(uid, wid, cost)
            await cb.answer("Этот товар пока недоступен.", show_alert=True); return
    except Exception as e:
        logger.warning(f"world buy {key}: {e}")
        await add_world_currency(uid, wid, cost)
        await cb.answer("Ошибка покупки, валюта возвращена.", show_alert=True); return
    prog = await get_world_progress(uid, wid)
    cur = w.get("currency", {})
    b = InlineKeyboardBuilder()
    b.button(text="🛒 Купить ещё", callback_data=f"world:currshop:{uid}")
    b.button(text="◀️ В мир", callback_data=f"world:main:{uid}")
    b.adjust(1)
    await safe_edit(cb.message,
        f"{msg}\n\n{cur.get('emoji', '🪙')} Остаток: <b>{prog.get('currency', 0):,}</b> {cur.get('name', '')}",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer("Куплено!")

@router.callback_query(F.data.startswith("world:bossfight:"))
async def cb_world_bossfight(cb):
    uid = int(cb.data.split(":")[-1])
    user = await get_user(uid)
    wid = parse_wid(user.get("world", 1))
    w = WORLDS.get(wid) or WORLDS[1]
    boss = w.get("boss")
    if not boss:
        await cb.answer("У этого мира нет босса!", show_alert=True); return
    prog = await get_world_progress(uid, wid)
    cd_h = max(4, min(48, int(boss.get("cooldown_hours", 24))))
    cd = cd_left(prog.get("last_boss"), cd_h * 3600)
    if cd:
        await cb.answer(f"⏳ Босс восстановится через {fmt_t(cd)}", show_alert=True); return
    pet = await get_active_pet(uid)
    if not pet:
        await cb.answer("Нужен активный питомец!", show_alert=True); return
    syn = get_world_synergy_bonus(wid, pet["pet_type"])
    power = pet_power(pet) * 5 * (1 + (syn["atk"] + syn["def"]) / 200)
    boss_power = boss["atk"] * 2 + boss["def"] * 2 + boss["hp"] / 50
    mlvl, _, _, _ = world_mastery_progress(prog.get("mastery", 0))
    chance = min(0.92, max(0.05, power / (power + boss_power) + (mlvl - 1) * 0.01))
    win = random.random() < chance
    coins_range = boss.get("reward_coins", (0, 0))
    cur_range = boss.get("reward_world_currency", (0, 0))
    cur = w.get("currency", {})
    if win:
        coins = random.randint(*coins_range)
        wcur = random.randint(*cur_range)
        await update_coins(uid, coins)
        await add_world_currency(uid, wid, wcur)
        await add_world_mastery(uid, wid, 40)
        pet_drop = ""
        pet_id = boss.get("reward_pet")
        drop_chance = boss.get("reward_pet_chance", 0) * (1 + (mlvl - 1) * 0.05)
        if pet_id in PETS and random.random() < drop_chance:
            await create_pet(uid, PETS[pet_id]["name"], pet_id, random.random() < 0.02)
            pet_drop = f"\n🐾 Дроп: <b>{PETS[pet_id]['name']}</b>!"
        result = "🏆 <b>Победа!</b>"
        rewards = (f"+{coins:,}💰\n{cur.get('emoji', '🪙')} +{wcur:,} {cur.get('name', '')}\n"
                   f"🏅 +40 мастерства{pet_drop}")
    else:
        coins = int(random.randint(*coins_range) * 0.2) if coins_range[1] > 0 else 0
        wcur = int(random.randint(*cur_range) * 0.25) if cur_range[1] > 0 else 0
        await update_coins(uid, coins)
        await add_world_currency(uid, wid, wcur)
        await add_world_mastery(uid, wid, 8)
        result = "💀 <b>Поражение</b>"
        rewards = (f"Утешительные: +{coins:,}💰, {cur.get('emoji', '🪙')} +{wcur:,}\n"
                   f"🏅 +8 мастерства")
    await record_world_boss(uid, wid, win)
    await add_season_tokens(uid, 20 if win else 5)
    await progress_daily_quest(uid, "boss_attack", 1, cb.bot)
    user2 = await get_user(uid)
    b = InlineKeyboardBuilder()
    b.button(text="◀️ В мир", callback_data=f"world:main:{uid}")
    b.adjust(1)
    await safe_edit(cb.message,
        f"👹 <b>{boss['emoji']} {boss['name']}</b>\n\n"
        f"Твоя сила: <b>{int(power):,}</b>\n"
        f"Сила босса: <b>{int(boss_power):,}</b>\n"
        f"Шанс победы: <b>{int(chance * 100)}%</b>\n\n"
        f"{result}\n{rewards}\n\n💰 Баланс: {user2['coins']:,}",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer("Победа!" if win else "Поражение...")

@router.callback_query(F.data.startswith("world:boss:"))
async def cb_world_boss(cb):
    uid = int(cb.data.split(":")[-1])
    user = await get_user(uid)
    wid = parse_wid(user.get("world", 1))
    w = WORLDS.get(wid) or WORLDS[1]
    boss = w.get("boss")
    if not boss:
        await cb.answer("У этого мира нет босса!", show_alert=True); return
    prog = await get_world_progress(uid, wid)
    cd_h = max(4, min(48, int(boss.get("cooldown_hours", 24))))
    cd = cd_left(prog.get("last_boss"), cd_h * 3600)
    pet = await get_active_pet(uid)
    syn = get_world_synergy_bonus(wid, pet["pet_type"]) if pet else {"atk": 0, "def": 0}
    power = pet_power(pet) * 5 * (1 + (syn["atk"] + syn["def"]) / 200) if pet else 0
    boss_power = boss["atk"] * 2 + boss["def"] * 2 + boss["hp"] / 50
    mlvl, _, _, _ = world_mastery_progress(prog.get("mastery", 0))
    chance = min(0.92, max(0.05, power / (power + boss_power) + (mlvl - 1) * 0.01)) if power else 0
    coins_range = boss.get("reward_coins", (0, 0))
    cur_range = boss.get("reward_world_currency", (0, 0))
    cur = w.get("currency", {})
    b = InlineKeyboardBuilder()
    if pet and not cd:
        b.button(text="⚔️ Сразиться", callback_data=f"world:bossfight:{uid}")
    b.button(text="◀️ Назад", callback_data=f"world:main:{uid}")
    b.adjust(1)
    status = f"⏳ Через {fmt_t(cd)}" if cd else ("✅ Готов к бою" if pet else "❌ Нет питомца")
    await safe_edit(cb.message,
        f"👹 <b>{boss['emoji']} {boss['name']}</b>\n"
        f"<i>{boss.get('desc', '')}</i>\n\n"
        f"❤️ HP: <b>{boss['hp']:,}</b> · ⚔️ {boss['atk']} · 🛡️ {boss['def']}\n"
        f"🎁 {coins_range[0]:,}–{coins_range[1]:,}💰 + {cur_range[0]:,}–{cur_range[1]:,} {cur.get('emoji', '🪙')}\n"
        f"🐾 Шанс питомца: {int(boss.get('reward_pet_chance', 0) * 100)}%\n"
        f"⏰ КД: {cd_h}ч\n\n"
        f"Твоя сила: <b>{int(power):,}</b> / сила босса: <b>{int(boss_power):,}</b>\n"
        f"Шанс победы: <b>{int(chance * 100)}%</b>\n"
        f"Статус: {status}",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("world:main:"))
async def cb_world_main(cb):
    uid = int(cb.data.split(":")[-1])
    user = await get_user(uid)
    wid = parse_wid(user.get("world", 1))
    await safe_edit(cb.message, await world_main_text(uid, wid),
                    reply_markup=world_kb(uid, wid), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("world:select:"))
async def cb_world_select(cb):
    uid = int(cb.data.split(":")[-1])
    user = await get_user(uid)
    raw = user.get("world", 1) or 1
    try:
        current_wid = int(raw)   # для числовых миров всегда int
    except (ValueError, TypeError):
        current_wid = 1          # если в БД строка секретного мира — считаем что все 10 открыты
    b = InlineKeyboardBuilder()
    for wid, w in WORLDS.items():
        is_secret = w.get("is_secret", False)
        if is_secret:
            # Секретные миры — проверяем условие разблокировки
            unlocked = check_secret_world_unlock(wid, {
                "boss_kills": {},
                "pets": [],
                "worlds_unlocked": list(range(1, current_wid + 1)),
            })
            icon = "🔓" if unlocked else "🔒"
            cost_txt = f" ({w['unlock_condition'].get('desc', '???')})" if not unlocked else ""
            b.button(
                text=f"{icon} {w['name']}{cost_txt}",
                callback_data=f"world:goto_secret:{wid}:{uid}"
            )
        else:
            # Обычные миры — wid гарантированно int
            wid_int = int(wid)
            is_unlocked = wid_int <= current_wid
            cost_txt = f" ({w['unlock_cost']:,}💰)" if not is_unlocked else ""
            b.button(
                text=f"{'✅' if is_unlocked else '🔒'} {w['name']}{cost_txt}",
                callback_data=f"world:goto:{wid_int}:{uid}"
            )
    b.button(text="◀️ Назад", callback_data=f"world:main:{uid}")
    b.adjust(1)
    await safe_edit(cb.message,
        f"🗺️ <b>Выбор мира</b>\n\n✅ = открыт  🔒 = нужны монеты\n🔒 = секретный (особое условие)",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("world:goto:"))
async def cb_world_goto(cb):
    parts = cb.data.split(":")
    target_wid = int(parts[2])
    uid = int(parts[3])
    user = await get_user(uid)
    current_wid = int(user.get("world", 1) or 1) if str(user.get("world", 1) or 1).isdigit() else 10
    w = WORLDS.get(target_wid)
    if not w: await cb.answer("Мир не найден!", show_alert=True); return

    if target_wid <= current_wid:
        await set_user_world(uid, target_wid)
        await get_world_progress(uid, target_wid)
        await cb.answer(f"✅ Ты в {w['name']}!")
        await safe_edit(cb.message, await world_main_text(uid, target_wid),
                        reply_markup=world_kb(uid, target_wid), parse_mode="HTML")
    elif target_wid == current_wid + 1:
        cost = w["unlock_cost"]
        if user["coins"] < cost:
            await cb.answer(f"❌ Нужно {cost:,}💰! У тебя {user['coins']:,}💰", show_alert=True); return
        await update_coins(uid, -cost)
        await set_user_world(uid, target_wid)
        await get_world_progress(uid, target_wid)
        unlock_reward = w.get("unlock_reward", {})
        reward_txt = ""
        if unlock_reward.get("coins", 0) > 0:
            await update_coins(uid, unlock_reward["coins"])
            reward_txt += f"\n🎁 Бонус: +{unlock_reward['coins']:,}💰"
        if unlock_reward.get("world_currency", 0) > 0:
            cur = w.get("currency", {})
            await add_world_currency(uid, target_wid, unlock_reward["world_currency"])
            reward_txt += f"\n{cur.get('emoji', '🪙')} +{unlock_reward['world_currency']:,} {cur.get('name', 'валюты')}"
        if unlock_reward.get("egg_id"):
            egg_id = unlock_reward["egg_id"]
            await add_item(uid, egg_id)
            eg = w.get("eggs", {}).get(egg_id) or WORLD_EGGS.get(egg_id) or EGG_TYPES.get(egg_id, {})
            reward_txt += f"\n🥚 {eg.get('emoji', '')} {eg.get('name', 'Яйцо')}"
        await add_world_mastery(uid, target_wid, 5)
        if unlock_reward.get("desc"):
            reward_txt += f"\n✨ {unlock_reward['desc']}"
        u2 = await get_user(uid)
        await cb.answer("🎉 Мир разблокирован!")
        await safe_edit(cb.message,
            f"🎉 <b>Добро пожаловать в {w['name']}!</b>\n\n"
            f"<i>{w['desc']}</i>\n\n"
            f"💰 Потрачено: {cost:,}\n💰 Остаток: {u2['coins']:,}{reward_txt}",
            reply_markup=world_kb(uid, target_wid), parse_mode="HTML")
    else:
        await cb.answer("🔒 Сначала открой предыдущий мир!", show_alert=True)

@router.callback_query(F.data.startswith("world:goto_secret:"))
async def cb_world_goto_secret(cb):
    """Переход в секретный мир — проверяем условие разблокировки."""
    parts = cb.data.split(":")
    secret_key = parts[2]   # "shadow", "forge", "origin"
    uid = int(parts[3])
    user = await get_user(uid)
    w = WORLDS.get(secret_key)
    if not w or not w.get("is_secret"):
        await cb.answer("Мир не найден!", show_alert=True); return

    # Проверяем условие
    all_pets = await get_all_pets(uid)
    pets_for_check = [{"level": p["level"], "rarity": PETS.get(p["pet_type"], {}).get("rarity", "common")} for p in all_pets]
    current_wid = int(user.get("world", 1) or 1) if str(user.get("world", 1) or 1).isdigit() else 10
    try:
        boss_kills_raw = json.loads(user.get("boss_kills_json") or "{}")
        boss_kills_check = {int(k): v for k, v in boss_kills_raw.items()}
    except: boss_kills_check = {}
    user_data_check = {
        "boss_kills": boss_kills_check,
        "pets": pets_for_check,
        "worlds_unlocked": list(range(1, current_wid + 1)),
    }
    unlocked = check_secret_world_unlock(secret_key, user_data_check)
    if not unlocked:
        cond_desc = w.get("unlock_condition", {}).get("desc", "Неизвестное условие")
        await cb.answer(f"🔒 Условие: {cond_desc}", show_alert=True); return

    # Открыт — переходим (сохраняем как строку в world поле)
    async with aiosqlite.connect(DB_PATH) as dbc:
        await dbc.execute("UPDATE users SET world=? WHERE user_id=?", (secret_key, uid))
        await dbc.commit()
    await get_world_progress(uid, secret_key)
    await cb.answer(f"✅ Ты в {w['name']}!")
    await safe_edit(cb.message, await world_main_text(uid, secret_key),
                    reply_markup=world_kb(uid, secret_key), parse_mode="HTML")

# ── ПИТОМЦЫ ──────────────────────────────────────────────────────────

async def h_pets(msg):
    uid=msg.from_user.id; pets=await get_all_pets(uid); user=await get_user(uid)
    if not pets: await safe_answer(msg,"Нет питомцев! Напиши <b>яйца</b> 🥚",parse_mode="HTML"); return
    await safe_answer(msg,f"🐾 <b>Питомцы</b> ({len(pets)})\n💰{user['coins']}\n\nВыбери:",
                      reply_markup=pets_kb(pets,uid,0),parse_mode="HTML")


@router.callback_query(F.data.startswith("pl:"))
async def cb_pl(cb):
    parts=cb.data.split(":"); uid=int(parts[1]); page=int(parts[2]) if len(parts)>2 else 0
    pets=await get_all_pets(uid); user=await get_user(uid)
    await safe_edit(cb.message,f"🐾 <b>Питомцы</b> ({len(pets)})\n💰{user['coins']}\n\nВыбери:",
                    reply_markup=pets_kb(pets,uid,page),parse_mode="HTML"); await cb.answer()

async def send_pet_card(target, pet: dict, kb=None, edit=False):
    """
    Отправляет карточку питомца как фото.
    target — Message или CallbackQuery.message
    edit=True — редактирует сообщение (только медиа), edit=False — отправляет новое.
    """
    try:
        buf  = generate_pet_card(pet)
        photo = BufferedInputFile(buf.read(), filename="pet_card.png")
        caption = pet_full(pet)
        if edit:
            try:
                await target.edit_media(
                    __import__('aiogram').types.InputMediaPhoto(media=photo, caption=caption, parse_mode="HTML"),
                    reply_markup=kb
                )
                return
            except Exception:
                pass  # fallback — отправим новым сообщением
        await target.answer_photo(photo=photo, caption=caption, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"send_pet_card: {e}")
        # Fallback: обычный текст если Pillow не работает
        text = pet_full(pet)
        if edit:
            await safe_edit(target, text, reply_markup=kb, parse_mode="HTML")
        else:
            await safe_answer(target, text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("vp:"))
async def cb_vp(cb):
    parts = cb.data.split(":"); pid = int(parts[1]); owner_id = int(parts[2])
    pet = await get_pet_by_id(pid, owner_id)
    if not pet: await cb.answer("Не найден!", show_alert=True); return
    kb = pet_kb(pid, owner_id)
    try:
        buf   = generate_pet_card(pet)
        photo = BufferedInputFile(buf.read(), filename="pet_card.png")
        caption = pet_full(pet)
        await cb.message.answer_photo(photo=photo, caption=caption, reply_markup=kb, parse_mode="HTML")
        # Удаляем старое текстовое сообщение
        try: await cb.message.delete()
        except: pass
    except Exception as e:
        logger.warning(f"cb_vp card: {e}")
        await safe_edit(cb.message, pet_full(pet), reply_markup=kb, parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("sa:"))
async def cb_sa(cb):
    parts=cb.data.split(":"); pid=int(parts[1]); owner_id=int(parts[2])
    await set_active_pet(owner_id,pid); await cb.answer("✅ Активный изменён!")
    pets=await get_all_pets(owner_id); user=await get_user(owner_id)
    await safe_edit(cb.message,f"🐾 <b>Питомцы</b>\n💰{user['coins']}\n\nВыбери:",
                    reply_markup=pets_kb(pets,owner_id),parse_mode="HTML")

# Действия с питомцем

@router.callback_query(F.data.startswith("a:feed:"))
async def cb_feed(cb):
    parts=cb.data.split(":"); pid=int(parts[2]); owner_id=int(parts[3])
    pet=await get_pet_by_id(pid,owner_id)
    if not pet: await cb.answer("Не найден!",show_alert=True); return
    if pet['is_sleeping']: await cb.answer("😴 Спит!",show_alert=True); return
    if pet['hunger']>=90: await cb.answer("Сытый!",show_alert=True); return
    
    # Проверка монет
    user = await get_user(owner_id)
    cost = 15
    if user['coins'] < cost:
        await cb.answer(f"❌ Нужно {cost}💰 для кормления!",show_alert=True)
        return
    
    await update_coins(owner_id, -cost)
    await update_pet(pid,hunger=min(100,pet['hunger']+25),mood=min(100,pet['mood']+5))
    lvl,up=await add_xp(pid,5)
    done=await progress_task(owner_id,'feed')
    if done: await update_coins(owner_id,done['reward_coins'])
    pet=await get_pet_by_id(pid,owner_id)
    note=(f"\n✅ Задание! +{done['reward_coins']}💰" if done else "")+(f"\n🎉 Уровень {lvl}!" if up else "")
    await safe_edit(cb.message,f"🍗 Покормлен! (-{cost}💰)\n{pet_full(pet)}{note}",reply_markup=pet_kb(pid,owner_id),parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("a:play:"))
async def cb_play(cb):
    parts=cb.data.split(":"); pid=int(parts[2]); owner_id=int(parts[3])
    pet=await get_pet_by_id(pid,owner_id)
    if not pet: await cb.answer("Не найден!",show_alert=True); return
    if pet['is_sleeping']: await cb.answer("😴 Спит!",show_alert=True); return
    if pet['hunger']<20: await cb.answer("😰 Голодный!",show_alert=True); return
    
    # Проверка монет
    user = await get_user(owner_id)
    cost = 20
    if user['coins'] < cost:
        await cb.answer(f"❌ Нужно {cost}💰 для игры!",show_alert=True)
        return
    
    await update_coins(owner_id, -cost)
    await update_pet(pid,mood=min(100,pet['mood']+20),hunger=max(0,pet['hunger']-10))
    lvl,up=await add_xp(pid,10)
    done=await progress_task(owner_id,'play')
    if done: await update_coins(owner_id,done['reward_coins'])
    pet=await get_pet_by_id(pid,owner_id)
    plays=["гонялся за мячом 🎾","прыгал 🤸","играл в прятки 🙈","бегал кругами 🌀"]
    note=(f"\n✅ Задание! +{done['reward_coins']}💰" if done else "")+(f"\n🎉 Уровень {lvl}!" if up else "")
    await safe_edit(cb.message,f"🎮 {random.choice(plays)}! (-{cost}💰)\n{pet_full(pet)}{note}",reply_markup=pet_kb(pid,owner_id),parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("a:sleep:"))
async def cb_sleep(cb):
    parts=cb.data.split(":"); pid=int(parts[2]); owner_id=int(parts[3])
    pet=await get_pet_by_id(pid,owner_id)
    if not pet: await cb.answer("Не найден!",show_alert=True); return
    if pet['is_sleeping']:
        await update_pet(pid,is_sleeping=0,health=min(pet['max_health'],pet['health']+20),mood=min(100,pet['mood']+15))
        txt="☀️ Проснулся!"
    else:
        await update_pet(pid,is_sleeping=1); txt="😴 Засыпает..."
    pet=await get_pet_by_id(pid,owner_id)
    await safe_edit(cb.message,f"{txt}\n{pet_full(pet)}",reply_markup=pet_kb(pid,owner_id),parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("a:heal:"))
async def cb_heal(cb):
    parts=cb.data.split(":"); pid=int(parts[2]); owner_id=int(parts[3])
    pet=await get_pet_by_id(pid,owner_id); mhp=pet.get('max_health',100)
    if pet['health']>=mhp: await cb.answer("Здоров!",show_alert=True); return
    if await has_item(owner_id,'superheal'): await remove_item(owner_id,'superheal'); nh=mhp; nm="💉"
    elif await has_item(owner_id,'magic_potion'): await remove_item(owner_id,'magic_potion'); nh=min(mhp,pet['health']+50); nm="🧪"
    elif await has_item(owner_id,'firstaid'): await remove_item(owner_id,'firstaid'); nh=min(mhp,pet['health']+30); nm="💊"
    else:
        u=await get_user(owner_id)
        if u['coins']<25: await cb.answer("❌ Нет монет!",show_alert=True); return
        await update_coins(owner_id,-25); nh=min(mhp,pet['health']+20); nm="(-25💰)"
    await update_pet(pid,health=nh); pet=await get_pet_by_id(pid,owner_id)
    await safe_edit(cb.message,f"💊 {nm} Вылечен!\n{pet_full(pet)}",reply_markup=pet_kb(pid,owner_id),parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("a:items:"))
async def cb_items(cb):
    parts=cb.data.split(":"); pid=int(parts[2]); owner_id=int(parts[3])
    inv=await get_inventory(owner_id)
    usable=[i for i in inv if i['item_id'] in ITEMS and i['item_id'] not in BATTLE_ITEMS]
    if not usable: await cb.answer("🎒 Пусто!",show_alert=True); return
    await safe_edit(cb.message,"🎒 <b>Выбери предмет:</b>",reply_markup=inv_use_kb(inv,pid,owner_id),parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("ui:"))
async def cb_ui(cb):
    parts=cb.data.split(":"); iid=parts[1]; pid=int(parts[2]); owner_id=int(parts[3])
    pet=await get_pet_by_id(pid,owner_id); item=ITEMS.get(iid)
    if not pet or not item: await cb.answer("Ошибка!",show_alert=True); return
    if not await remove_item(owner_id,iid): await cb.answer("❌ Нет!",show_alert=True); return
    mhp=pet.get('max_health',100); ch={}
    if 'health' in item: ch['health']=min(mhp,pet['health']+item['health'])
    if 'hunger' in item: ch['hunger']=min(100,pet['hunger']+item['hunger'])
    if 'mood'   in item: ch['mood']  =min(100,pet['mood']  +item['mood'])
    if ch: await update_pet(pid,**ch)
    pet=await get_pet_by_id(pid,owner_id)
    await safe_edit(cb.message,f"✅ {item['emoji']} использован!\n{pet_full(pet)}",reply_markup=pet_kb(pid,owner_id),parse_mode="HTML")
    await cb.answer("✅")

@router.callback_query(F.data.startswith("close:"))
async def cb_close(cb):
    try: await cb.message.delete()
    except: pass
    await cb.answer()

# Экипировка
@router.callback_query(F.data.startswith("eq:m:"))
async def cb_eqm(cb):
    parts=cb.data.split(":"); pid=int(parts[2]); owner_id=int(parts[3])
    pet=await get_pet_by_id(pid,owner_id)
    if not pet: await cb.answer("Не найден!",show_alert=True); return
    await safe_edit(cb.message,f"⚔️ <b>Экипировка — {pet['name']}</b>",reply_markup=equip_kb(pid,owner_id),parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("eq:w:"))
async def cb_eqw(cb):
    parts=cb.data.split(":"); pid=int(parts[2]); owner_id=int(parts[3])
    inv=await get_inventory(owner_id)
    if not any(i['item_id'] in WEAPONS for i in inv): await cb.answer("Нет оружия!",show_alert=True); return
    await safe_edit(cb.message,"🗡️ <b>Оружие:</b>",reply_markup=weapons_kb(pid,owner_id,inv),parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("eq:a:"))
async def cb_eqa(cb):
    parts=cb.data.split(":"); pid=int(parts[2]); owner_id=int(parts[3])
    inv=await get_inventory(owner_id)
    if not any(i['item_id'] in ARMOR for i in inv): await cb.answer("Нет брони!",show_alert=True); return
    await safe_edit(cb.message,"🛡️ <b>Броня:</b>",reply_markup=armor_kb(pid,owner_id,inv),parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("eq:sw:"))
async def cb_eqsw(cb):
    parts=cb.data.split(":"); pid=int(parts[2]); owner_id=int(parts[3]); wid=parts[4]
    if wid not in WEAPONS: await cb.answer("❌ Оружие не найдено!",show_alert=True); return
    pet=await get_pet_by_id(pid,owner_id)
    if not pet: await cb.answer("Питомец не найден!",show_alert=True); return
    # ── ИСПРАВЛЕНИЕ ДЮПА: атомарно списываем новое оружие из инвентаря,
    #    а старое (если было) возвращаем обратно — никогда не теряем и не плодим предметы.
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT inv_id,quantity FROM inventory WHERE user_id=? AND item_id=?",(owner_id,wid)) as c:
            r = await c.fetchone()
        if not r or r[1] < 1:
            await cb.answer("❌ У тебя нет этого оружия в инвентаре!",show_alert=True); return
        await db.execute("UPDATE inventory SET quantity=quantity-1 WHERE inv_id=?",(r[0],))
        old_weapon = pet.get('weapon')
        if old_weapon and old_weapon != wid:
            await _add_item_raw(db, owner_id, old_weapon, 1)
        await db.execute("UPDATE pets SET weapon=?,last_updated=? WHERE pet_id=?",(wid,datetime.now().isoformat(),pid))
        await db.commit()
    w=WEAPONS[wid]; await cb.answer(f"✅ {w['emoji']} экипировано!")
    pet=await get_pet_by_id(pid,owner_id)
    await safe_edit(cb.message,pet_full(pet),reply_markup=pet_kb(pid,owner_id),parse_mode="HTML")

@router.callback_query(F.data.startswith("eq:sa:"))
async def cb_eqsa(cb):
    parts=cb.data.split(":"); pid=int(parts[2]); owner_id=int(parts[3]); aid=parts[4]
    if aid not in ARMOR: await cb.answer("❌ Броня не найдена!",show_alert=True); return
    pet=await get_pet_by_id(pid,owner_id)
    if not pet: await cb.answer("Питомец не найден!",show_alert=True); return
    # ── ИСПРАВЛЕНИЕ ДЮПА: атомарно списываем новую броню из инвентаря,
    #    а старую (если была) возвращаем обратно — никогда не теряем и не плодим предметы.
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT inv_id,quantity FROM inventory WHERE user_id=? AND item_id=?",(owner_id,aid)) as c:
            r = await c.fetchone()
        if not r or r[1] < 1:
            await cb.answer("❌ У тебя нет этой брони в инвентаре!",show_alert=True); return
        await db.execute("UPDATE inventory SET quantity=quantity-1 WHERE inv_id=?",(r[0],))
        old_armor = pet.get('armor')
        if old_armor and old_armor != aid:
            await _add_item_raw(db, owner_id, old_armor, 1)
        await db.execute("UPDATE pets SET armor=?,last_updated=? WHERE pet_id=?",(aid,datetime.now().isoformat(),pid))
        await db.commit()
    a=ARMOR[aid]; await cb.answer(f"✅ {a['emoji']} экипировано!")
    pet=await get_pet_by_id(pid,owner_id)
    await safe_edit(cb.message,pet_full(pet),reply_markup=pet_kb(pid,owner_id),parse_mode="HTML")

@router.callback_query(F.data.startswith("sell:"))
async def cb_sell(cb: CallbackQuery, state: FSMContext):
    parts=cb.data.split(":"); pid=int(parts[1]); owner_id=int(parts[2])
    pet=await get_pet_by_id(pid,owner_id)
    if not pet: await cb.answer("Не найден!",show_alert=True); return
    if pet.get('for_sale'): await cb.answer("Уже на продаже!",show_alert=True); return
    await state.update_data(sell_pid=pid); await state.set_state(S.sell_price)
    p=PETS.get(pet['pet_type'],PETS['dog'])
    await safe_edit(cb.message,f"🏪 Продажа {p['emoji']} <b>{pet['name']}</b>\n\nВведи цену (монет):",parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("sellfast:do:"))
async def cb_sellfast_do(cb):
    parts=cb.data.split(":"); pid=int(parts[2]); uid=int(parts[3])
    if cb.from_user.id != uid:
        await cb.answer("Это не твой питомец!", show_alert=True); return
    async with get_user_lock(uid):
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM pets WHERE pet_id=? AND owner_id=?", (pid, uid)) as c:
                row = await c.fetchone()
            if not row:
                await cb.answer("Питомец не найден!", show_alert=True); return
            pet = dict(row)
            value = pet_sell_value(pet)
            price = pet_quick_sell_price(pet)
            if pet.get('weapon'):
                await _add_item_raw(db, uid, pet['weapon'], 1)
            if pet.get('armor'):
                await _add_item_raw(db, uid, pet['armor'], 1)
            await db.execute("UPDATE trade_offers SET status='cancelled' WHERE pet_id=? AND status='active'", (pid,))
            await db.execute("UPDATE auction_lots SET status='cancelled' WHERE pet_id=? AND status='active'", (pid,))
            if pet.get('active'):
                async with db.execute("SELECT pet_id FROM pets WHERE owner_id=? AND pet_id<>? ORDER BY pet_id LIMIT 1", (uid, pid)) as c:
                    nxt = await c.fetchone()
                if nxt:
                    await db.execute("UPDATE pets SET active=1 WHERE pet_id=?", (nxt[0],))
            await db.execute("UPDATE users SET coins=coins+?, total_earned=COALESCE(total_earned,0)+? WHERE user_id=?", (price, price, uid))
            await db.execute("DELETE FROM pets WHERE pet_id=? AND owner_id=?", (pid, uid))
            await db.commit()
    user = await get_user(uid)
    p = PETS.get(pet['pet_type'], PETS['dog'])
    b = InlineKeyboardBuilder()
    b.button(text="🐾 Мои питомцы", callback_data=f"pl:{uid}:0")
    b.button(text="🏪 Рынок", callback_data=f"mkt:back:{uid}")
    b.adjust(1)
    await safe_edit(cb.message,
        f"💵 <b>Питомец продан!</b>\n\n"
        f"{p['emoji']} <b>{pet['name']}</b> · Ур.{pet['level']}\n"
        f"Оценка: {value:,}💰 · получено: <b>{price:,}💰</b>\n"
        f"💰 Баланс: <b>{user['coins']:,}</b>",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer("Продано!")

@router.callback_query(F.data.startswith("sellfast:"))
async def cb_sellfast(cb):
    parts=cb.data.split(":")
    if len(parts) != 3:
        await cb.answer("Ошибка кнопки", show_alert=True); return
    pid=int(parts[1]); uid=int(parts[2])
    if cb.from_user.id != uid:
        await cb.answer("Это не твой питомец!", show_alert=True); return
    pet = await get_pet_by_id(pid, uid)
    if not pet:
        await cb.answer("Питомец не найден!", show_alert=True); return
    value = pet_sell_value(pet)
    price = pet_quick_sell_price(pet)
    p = PETS.get(pet['pet_type'], PETS['dog'])
    b = InlineKeyboardBuilder()
    b.button(text=f"✅ Продать за {price:,}💰", callback_data=f"sellfast:do:{pid}:{uid}")
    b.button(text="❌ Отмена", callback_data=f"vp:{pid}:{uid}")
    b.adjust(1)
    sale_note = "\n⚠️ Лот на рынке будет снят." if pet.get('for_sale') else ""
    await safe_edit(cb.message,
        f"💵 <b>Быстрая продажа</b>\n\n"
        f"{p['emoji']} <b>{pet['name']}</b> · Ур.<b>{pet['level']}</b>\n"
        f"Оценка питомца: <b>{value:,}💰</b>\n"
        f"Цена быстрой продажи: <b>{price:,}💰</b>{sale_note}\n\n"
        f"Экипировка вернётся в инвентарь.",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

async def s_sell_price(msg: Message, state: FSMContext):
    text = msg.text.strip()
    
    # Проверка на отмену
    if text.lower() in ["отмена", "cancel", "стоп", "stop", "/cancel"]:
        await state.clear()
        await safe_reply(msg,"❌ Продажа отменена.")
        return
    
    if not msg.text.isdigit(): 
        await safe_reply(msg,"❌ Введи число!\n\n💡 Для отмены напиши: <b>отмена</b>", parse_mode="HTML")
        return
    price=int(msg.text)
    if price<10: 
        await safe_reply(msg,"❌ Минимум 10💰\n\n💡 Для отмены напиши: <b>отмена</b>", parse_mode="HTML")
        return
    data=await state.get_data(); await state.clear()
    pid=data.get('sell_pid'); uid=msg.from_user.id
    pet=await get_pet_by_id(pid,uid)
    if not pet: await safe_reply(msg,"❌ Питомец не найден!"); return
    await create_trade_offer(uid,pid,price)
    p=PETS.get(pet['pet_type'],PETS['dog'])
    await safe_reply(msg,f"✅ {p['emoji']} <b>{pet['name']}</b> выставлен за <b>{price}💰</b>!\nНайди в рынке.",parse_mode="HTML")

# ── ЯЙЦА ─────────────────────────────────────────────────────────────

async def h_eggs(msg):
    uid=msg.from_user.id; inv=await get_inventory(uid); ei=[i for i in inv if i['item_id'] in EGG_TYPES]
    user=await get_user(uid)
    if not ei: await safe_answer(msg,f"🥚 Нет яиц!\n💰{user['coins']}\nКупи в <b>магазин</b>",parse_mode="HTML"); return
    txt=f"🥚 <b>Яйца</b>\n💰{user['coins']}\n\n"
    for e in ei:
        eg=EGG_TYPES[e['item_id']]; txt+=f"{eg['emoji']} <b>{eg['name']}</b> ×{e['quantity']}\n"
    await safe_answer(msg,txt,reply_markup=eggs_kb(ei,uid),parse_mode="HTML")


@router.callback_query(F.data.startswith("hatch:"))
async def cb_hatch(cb: CallbackQuery, state: FSMContext):
    parts=cb.data.split(":"); eid=parts[1]; uid=int(parts[2])
    if not await remove_item(uid,eid): await cb.answer("❌ Нет яйца!",show_alert=True); return
    pt=hatch_egg(eid); p=PETS[pt]; r=RARITY_EMOJI.get(p['rarity'],'⚪')
    # 1% шанс Shiny
    is_shiny = random.random() < 0.01
    await safe_edit(cb.message,f"{EGG_TYPES[eid]['emoji']} <b>Яйцо трескается...</b>",parse_mode="HTML")
    await asyncio.sleep(1)
    await safe_edit(cb.message,"💥 Кто-то вылупляется...",parse_mode="HTML")
    await asyncio.sleep(1)
    rn={"common":"Обычный","rare":"🟣 Редкий","legendary":"🌟 Легендарный","mythic":"💎 МИФИЧЕСКИЙ!!!"}
    shiny_txt = "\n✨ <b>SHINY! +15% к характеристикам!</b>" if is_shiny else ""
    await safe_edit(cb.message,
        f"{'🎆' if p['rarity'] in ('legendary','mythic') or is_shiny else '🎉'} <b>Из яйца появился...</b>\n\n"
        f"{p['emoji']} <b>{p['name']}</b> {r}\n⭐ {rn.get(p['rarity'],'')}{shiny_txt}\n"
        f"🔮 <i>{p.get('ability','')}</i>\n\nВведи имя питомца:",parse_mode="HTML")
    await state.update_data(pet_type=pt, is_shiny=is_shiny); await state.set_state(S.pet_name)
    if is_shiny:
        await check_and_grant_achievement(uid, "shiny_pet")
    done=await progress_task(uid,'hatch')
    if done: await update_coins(uid,done['reward_coins'])
    await cb.answer()

async def s_pet_name(msg: Message, state: FSMContext):
    name=msg.text.strip()
    if not 2<=len(name)<=20: await safe_reply(msg,"❌ 2–20 символов!"); return
    data=await state.get_data(); await state.clear()
    is_shiny = data.get('is_shiny', False)
    pet=await create_pet(msg.from_user.id, name, data['pet_type'], is_shiny=is_shiny)
    p=PETS[data['pet_type']]; r=RARITY_EMOJI.get(p['rarity'],'⚪')
    shiny_txt = " ✨ SHINY!" if is_shiny else ""
    await safe_answer(msg,f"🎉 <b>{p['emoji']} {name}!</b> {r}{shiny_txt}\n\n{pet_full(pet)}",reply_markup=main_kb(),parse_mode="HTML")

# ── МАГАЗИН ───────────────────────────────────────────────────────────

async def h_shop(msg):
    uid=msg.from_user.id; user=await get_user(uid)
    await safe_answer(msg,f"🏪 <b>Магазин PetWorld</b>\n💰{user['coins']}\n\nКатегория:",
                      reply_markup=shop_cat_kb(uid),parse_mode="HTML")


@router.callback_query(F.data.startswith("sc:main:"))
async def cb_scm(cb):
    uid=int(cb.data.split(":")[-1]); user=await get_user(uid)
    await safe_edit(cb.message,f"🏪 <b>Магазин</b>\n💰{user['coins']}\n\nКатегория:",reply_markup=shop_cat_kb(uid),parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("sc:"))
async def cb_sc(cb):
    parts=cb.data.split(":"); cat=parts[1]; uid=int(parts[2])
    if cat=="main": await cb_scm(cb); return
    cats={"eggs":"🥚 Яйца","food":"🍖 Еда","heal":"💊 Лечение","weapons":"🗡️ Оружие","armor":"🛡️ Броня","battle":"⚗️ Бой","stars":"⭐ За звёзды","deals":"🔥 АКЦИИ"}
    user=await get_user(uid)
    await safe_edit(cb.message,f"🏪 <b>{cats.get(cat,'Магазин')}</b>\n💰{user['coins']}",reply_markup=shop_items_kb(cat,uid),parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("sb:"))
async def cb_sb(cb):
    parts=cb.data.split(":"); iid=parts[1]; uid=int(parts[2]); user=await get_user(uid)
    ag={}
    for k,v in EGG_TYPES.items(): ag[k]={"name":v['name'],"emoji":v['emoji'],"price":v['price']}
    for k,v in ITEMS.items():     ag[k]={"name":v['name'],"emoji":v['emoji'],"price":v['price']}
    for k,v in WEAPONS.items():   ag[k]={"name":v['name'],"emoji":v['emoji'],"price":v['price']}
    for k,v in ARMOR.items():     ag[k]={"name":v['name'],"emoji":v['emoji'],"price":v['price']}
    good=ag.get(iid)
    if not good: await cb.answer("Не найдено!",show_alert=True); return
    price=good['price']
    if price<=0: await cb.answer("Только за звёзды!",show_alert=True); return
    event=await get_active_event()
    if event and event.get('event_type')=='dark_night' and iid=='dark_egg': price//=2
    if user['coins']<price: await cb.answer(f"❌ Нужно {price}💰!",show_alert=True); return
    await update_coins(uid,-price); await add_item(uid,iid)
    u2=await get_user(uid); await cb.answer(f"✅ {good['emoji']} куплено! Остаток: {u2['coins']}💰")

@router.callback_query(F.data.startswith("sb_star:"))
async def cb_sb_star(cb):
    parts=cb.data.split(":"); iid=parts[1]; uid=int(parts[2])
    stars_price=0; item_name=""; item_emoji=""
    for d in [EGG_TYPES,WEAPONS,ARMOR,ITEMS]:
        if iid in d and d[iid].get("stars",0)>0:
            stars_price=d[iid]["stars"]; item_name=d[iid]["name"]; item_emoji=d[iid].get("emoji","⭐"); break
    if not stars_price: await cb.answer("Товар не найден!",show_alert=True); return
    b=InlineKeyboardBuilder()
    b.button(text="✅ Я подарил!", callback_data=f"gift:confirm:{iid}:{uid}")
    b.button(text="❌ Отмена", callback_data=f"sc:stars:{uid}")
    b.adjust(1)
    txt = ("⭐ <b>" + item_emoji + " " + item_name + "</b>\n"
           "Цена: <b>" + str(stars_price) + " звёзд</b>\n\n"
           "Для покупки подари подарок на <b>" + str(stars_price) + "⭐</b> пользователю @xecibe в Telegram.\n\n"
           "После отправки нажми <b>«Я подарил!»</b> — заявка поступит админу.")
    await safe_edit(cb.message, txt, reply_markup=b.as_markup(), parse_mode="HTML")

@router.callback_query(F.data.startswith("gift:confirm:"))
async def cb_gift_confirm(cb):
    parts=cb.data.split(":"); iid=parts[2]; uid=int(parts[3])
    stars_price=0; item_name=""; item_emoji=""
    for d in [EGG_TYPES,WEAPONS,ARMOR,ITEMS]:
        if iid in d and d[iid].get("stars",0)>0:
            stars_price=d[iid]["stars"]; item_name=d[iid]["name"]; item_emoji=d[iid].get("emoji","⭐"); break
    if not stars_price: await cb.answer("Ошибка!",show_alert=True); return
    user=await get_user(uid); uname=user.get("first_name") or user.get("username") or str(uid)
    # Сохраняем заявку в БД
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO gift_requests (user_id,item_id,item_name,stars) VALUES (?,?,?,?)",
                         (uid,iid,item_name,stars_price))
        await db.commit()
        async with db.execute("SELECT last_insert_rowid()") as c:
            req_id=(await c.fetchone())[0]
    # Уведомляем админа
    adm_kb2=InlineKeyboardBuilder()
    adm_kb2.button(text="✅ Выдать товар", callback_data=f"gift:approve:{req_id}:{uid}")
    adm_kb2.button(text="❌ Отклонить",    callback_data=f"gift:reject:{req_id}:{uid}")
    adm_kb2.adjust(1)
    for admin_id in ADMIN_IDS:
        try:
            await cb.message.bot.send_message(admin_id,
                "🎁 <b>Новая заявка на подарок</b>\n\n"
                "👤 " + uname + " (ID: " + str(uid) + ")\n"
                + item_emoji + " <b>" + item_name + "</b>\n"
                "⭐ " + str(stars_price) + " звёзд\n"
                "🆔 Заявка #" + str(req_id),
                reply_markup=adm_kb2.as_markup(), parse_mode="HTML")
        except: pass
    await cb.answer("✅ Заявка отправлена!")
    await safe_edit(cb.message,
        "✅ <b>Заявка #" + str(req_id) + " принята!</b>\n\n"
        "Как только администратор проверит подарок — товар будет выдан.\n"
        + item_emoji + " <b>" + item_name + "</b>",
        parse_mode="HTML")

@router.callback_query(F.data.startswith("gift:approve:"))
async def cb_gift_approve(cb):
    if not is_admin(cb.from_user.id): await cb.answer("❌ Нет доступа!",show_alert=True); return
    parts=cb.data.split(":"); req_id=int(parts[2]); uid=int(parts[3])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory=aiosqlite.Row
        async with db.execute("SELECT * FROM gift_requests WHERE req_id=?",(req_id,)) as c:
            req=await c.fetchone()
        if not req: await cb.answer("Заявка не найдена!",show_alert=True); return
        req=dict(req)
        if req["status"]!="pending": await cb.answer("Уже обработана!",show_alert=True); return
        await db.execute("UPDATE gift_requests SET status='approved' WHERE req_id=?",(req_id,))
        await db.commit()
    # Проверяем, если это СЕТ ЗЕВСА
    if req["item_id"] == "zeus_set":
        # Выдаём яйцо Зевса
        await add_item(uid, "zeus_egg")
        # Выдаём оружие и броню
        await add_item(uid, "zeus_bolt")
        await add_item(uid, "zeus_aegis")
        try:
            await cb.message.bot.send_message(uid,
                "✅ <b>Заявка #" + str(req_id) + " одобрена!</b>\n\n"
                "⚡ <b>СЕТ ЗЕВСА</b> получен!\n\n"
                "⚡ Яйцо Зевса\n"
                "⚡ Молния Зевса (оружие)\n"
                "⚡ Эгида Зевса (броня)\n\n"
                "Используй яйцо чтобы получить питомца!",
                parse_mode="HTML")
        except: pass
        await safe_edit(cb.message, f"✅ Выдано: ⚡ СЕТ ЗЕВСА → {uid}", parse_mode="HTML")
        await cb.answer("✅ Выдано!")
    else:
        await add_item(uid, req["item_id"])
        all_items={**EGG_TYPES,**WEAPONS,**ARMOR,**ITEMS}; item=all_items.get(req["item_id"],{})
        emoji=item.get("emoji","⭐")
        try:
            await cb.message.bot.send_message(uid,
                "✅ <b>Заявка #" + str(req_id) + " одобрена!</b>\n"
                + emoji + " <b>" + req["item_name"] + "</b> добавлен в инвентарь!",
                parse_mode="HTML")
        except: pass
        await safe_edit(cb.message, f"✅ Выдано: {emoji} {req['item_name']} → {uid}", parse_mode="HTML")
        await cb.answer("✅ Выдано!")

@router.callback_query(F.data.startswith("gift:reject:"))
async def cb_gift_reject(cb):
    if not is_admin(cb.from_user.id): await cb.answer("❌ Нет доступа!",show_alert=True); return
    parts=cb.data.split(":"); req_id=int(parts[2]); uid=int(parts[3])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE gift_requests SET status='rejected' WHERE req_id=?",(req_id,))
        await db.commit()
    try:
        await cb.message.bot.send_message(uid,
            "❌ <b>Заявка #" + str(req_id) + " отклонена.</b> Обратись к @xecibe.",
            parse_mode="HTML")
    except: pass
    await safe_edit(cb.message, f"❌ Заявка #{req_id} отклонена.", parse_mode="HTML")
    await cb.answer("Отклонено!")

DONATION_OPTIONS = {
    "1":  (1,  1000,  "1000💰",            "coins"),
    "5":  (5,  5500,  "5500💰 +10%",       "coins"),
    "10": (10, 12000, "12000💰 +20%",       "coins"),
    "25": (25, 32000, "32000💰 +28%",       "coins"),
    "50": (50, 70000, "70000💰 +40%",       "coins"),
    "vip":(20, 0,     "VIP 30 дней",        "vip"),
    "pet_celestial_cat": (30, 0, "🌙 Небесная кошка (godlike)",   "pet:celestial_cat"),
    "pet_void_wolf":     (35, 0, "🌑 Волк бездны (godlike)",      "pet:void_wolf"),
    "pet_golden_panda":  (30, 0, "🐼 Золотая панда (godlike)",    "pet:golden_panda"),
    "pet_crystal_fox":   (35, 0, "🦊 Кристальная лиса (godlike)", "pet:crystal_fox"),
    "pet_inferno_tiger": (40, 0, "🐯 Тигр Инферно (godlike)",     "pet:inferno_tiger"),
    "pack_start":   (15, 0, "🎁 Стартовый пак (5000💰 + 50💎 + rare_egg)",       "pack:start"),
    "pack_crystal": (25, 0, "💎 Кристальный пак (10000💰 + 200💎 + crystal_egg)", "pack:crystal"),
    "pack_mega":    (60, 0, "🌟 Мега-пак (30000💰 + 500💎 + VIP7д + dark_egg)",   "pack:mega"),
}
DONATION_ACTIONS = set(DONATION_OPTIONS) | {"noop"}

# ── ДОНАТ ─────────────────────────────────────────────────────────────

async def h_donate(msg):
    uid=msg.from_user.id; user=await get_user(uid)
    vip_s="👑 VIP активен!" if await is_vip(uid) else "Нет VIP"
    await safe_answer(msg,f"⭐ <b>Донат PetWorld</b>\n\n💰{user['coins']}\n{vip_s}\n\n"
                     f"1 звезда = {STARS_TO_COINS} монет\n\nVIP 30д (20⭐):\n• +50% монет\n• +50% XP\n• Значок 👑\n\n"
                     f"Для доната подари подарок @xecibe и нажми кнопку ниже:",
                     reply_markup=donate_kb(uid),parse_mode="HTML")


@router.callback_query(lambda cb: (cb.data or "").startswith("don:") and (cb.data or "").split(":")[1] in DONATION_ACTIONS)
async def cb_don(cb):
    parts=cb.data.split(":"); action=parts[1]
    if action=="noop": await cb.answer(); return
    uid=int(parts[2])
    opts = DONATION_OPTIONS
    stars,coins,desc,itype=opts[action]
    b=InlineKeyboardBuilder()
    b.button(text="✅ Я подарил!", callback_data=f"don:confirm:{action}:{uid}")
    b.button(text="❌ Отмена", callback_data=f"don:back:{uid}")
    b.adjust(1)
    await safe_edit(cb.message,
        "💰 <b>" + desc + "</b>\nЦена: <b>" + str(stars) + "⭐</b>\n\n"
        "Подари подарок на <b>" + str(stars) + "⭐</b> пользователю @xecibe в Telegram.\n\n"
        "После отправки нажми <b>«Я подарил!»</b>",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("don:back:"))
async def cb_don_back(cb):
    uid=int(cb.data.split(":")[-1])
    user=await get_user(uid)
    vip_s="👑 VIP активен!" if await is_vip(uid) else "Нет VIP"
    await safe_edit(cb.message,
        "⭐ <b>Донат PetWorld</b>\n\n💰" + str(user['coins']) + "\n" + vip_s + "\n\n"
        "Для доната подари подарок @xecibe нужное количество звёзд и нажми кнопку ниже:",
        reply_markup=donate_kb(uid), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("don:confirm:"))
async def cb_don_confirm(cb):
    parts=cb.data.split(":"); action=parts[2]; uid=int(parts[3])
    opts = DONATION_OPTIONS
    if action not in opts: await cb.answer("Ошибка!",show_alert=True); return
    stars,coins,desc,itype=opts[action]
    user=await get_user(uid); uname=user.get("first_name") or user.get("username") or str(uid)
    item_id=f"coins:{coins}" if itype=="coins" else ("vip:30" if itype=="vip" else itype)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO gift_requests (user_id,item_id,item_name,stars) VALUES (?,?,?,?)",
                         (uid,item_id,desc,stars))
        await db.commit()
        async with db.execute("SELECT last_insert_rowid()") as c:
            req_id=(await c.fetchone())[0]
    adm_kb2=InlineKeyboardBuilder()
    adm_kb2.button(text="✅ Подтвердить", callback_data=f"don:approve:{req_id}:{uid}:{action}")
    adm_kb2.button(text="❌ Отклонить",   callback_data=f"don:reject:{req_id}:{uid}")
    adm_kb2.adjust(1)
    for admin_id in ADMIN_IDS:
        try:
            await cb.message.bot.send_message(admin_id,
                "💰 <b>Заявка на донат</b>\n\n"
                "👤 " + uname + " (ID: " + str(uid) + ")\n"
                "💎 " + desc + "\n⭐ " + str(stars) + " звёзд\n"
                "🆔 Заявка #" + str(req_id),
                reply_markup=adm_kb2.as_markup(), parse_mode="HTML")
        except: pass
    await cb.answer("✅ Заявка отправлена!")
    await safe_edit(cb.message,
        "✅ <b>Заявка #" + str(req_id) + " принята!</b>\n\n"
        "Администратор проверит подарок и начислит: <b>" + desc + "</b>",
        parse_mode="HTML")

@router.callback_query(F.data.startswith("don:approve:"))
async def cb_don_approve(cb):
    if not is_admin(cb.from_user.id): await cb.answer("❌ Нет доступа!",show_alert=True); return
    parts=cb.data.split(":"); req_id=int(parts[2]); uid=int(parts[3]); action=parts[4]
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT status FROM gift_requests WHERE req_id=?",(req_id,)) as c:
            row=await c.fetchone()
        if not row: await cb.answer("Не найдено!",show_alert=True); return
        if row[0]!="pending": await cb.answer("Уже обработана!",show_alert=True); return
        await db.execute("UPDATE gift_requests SET status='approved' WHERE req_id=?",(req_id,))
        await db.commit()
    opts={
        "1":(1,1000,"1000💰","coins"),"5":(5,5500,"5500💰","coins"),
        "10":(10,12000,"12000💰","coins"),"25":(25,32000,"32000💰","coins"),
        "50":(50,70000,"70000💰","coins"),"vip":(20,0,"VIP 30 дней","vip"),
        "pet_celestial_cat":(30,0,"Небесная кошка","pet:celestial_cat"),
        "pet_void_wolf":    (35,0,"Волк бездны","pet:void_wolf"),
        "pet_golden_panda": (30,0,"Золотая панда","pet:golden_panda"),
        "pet_crystal_fox":  (35,0,"Кристальная лиса","pet:crystal_fox"),
        "pet_inferno_tiger":(40,0,"Тигр Инферно","pet:inferno_tiger"),
        "pack_start":   (15,0,"Стартовый пак","pack:start"),
        "pack_crystal": (25,0,"Кристальный пак","pack:crystal"),
        "pack_mega":    (60,0,"Мега-пак","pack:mega"),
    }
    entry=opts.get(action)
    if not entry: await cb.answer("Неизвестный тип!",show_alert=True); return
    stars,coins,desc,itype=entry
    if itype=="vip":
        vip_until=(datetime.now()+timedelta(days=30)).isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET vip_until=? WHERE user_id=?",(vip_until,uid)); await db.commit()
        try: await cb.message.bot.send_message(uid,"👑 <b>VIP на 30 дней активирован!</b>",parse_mode="HTML")
        except: pass
    elif itype=="coins":
        await update_coins(uid,coins)
        user=await get_user(uid)
        try: await cb.message.bot.send_message(uid,f"✅ <b>+{coins}💰</b> начислено!\nБаланс: {user['coins']}💰",parse_mode="HTML")
        except: pass
    elif itype.startswith("pet:"):
        pet_type=itype.split(":")[1]
        pd=PETS.get(pet_type,{})
        new_pet=await create_pet(uid,pd.get("name",pet_type),pet_type)
        try: await cb.message.bot.send_message(uid,f"🎉 <b>Питомец выдан!</b>\n{pd.get('emoji','')} {pd.get('name',pet_type)} (godlike)\nНапиши <b>питомцы</b>",parse_mode="HTML")
        except: pass
    elif itype.startswith("pack:"):
        pack=itype.split(":")[1]
        if pack=="start":
            await update_coins(uid,5000); await update_crystals(uid,50); await add_item(uid,"rare_egg")
            try: await cb.message.bot.send_message(uid,"🎁 <b>Стартовый пак получен!</b>\n+5000💰 +50💎 +Редкое яйцо",parse_mode="HTML")
            except: pass
        elif pack=="crystal":
            await update_coins(uid,10000); await update_crystals(uid,200); await add_item(uid,"crystal_egg")
            try: await cb.message.bot.send_message(uid,"💎 <b>Кристальный пак получен!</b>\n+10000💰 +200💎 +Кристальное яйцо",parse_mode="HTML")
            except: pass
        elif pack=="mega":
            await update_coins(uid,30000); await update_crystals(uid,500); await add_item(uid,"dark_egg")
            vip_until=(datetime.now()+timedelta(days=7)).isoformat()
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE users SET vip_until=? WHERE user_id=?",(vip_until,uid)); await db.commit()
            try: await cb.message.bot.send_message(uid,"🌟 <b>Мега-пак получен!</b>\n+30000💰 +500💎 +VIP 7д +Тёмное яйцо",parse_mode="HTML")
            except: pass

    await safe_edit(cb.message, f"✅ Заявка #{req_id} одобрена: {desc} → {uid}", parse_mode="HTML")
    await cb.answer("✅ Начислено!")

@router.callback_query(F.data.startswith("don:reject:"))
async def cb_don_reject(cb):
    if not is_admin(cb.from_user.id): await cb.answer("❌ Нет доступа!",show_alert=True); return
    parts=cb.data.split(":"); req_id=int(parts[2]); uid=int(parts[3])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE gift_requests SET status='rejected' WHERE req_id=?",(req_id,))
        await db.commit()
    try: await cb.message.bot.send_message(uid,f"❌ Заявка #{req_id} отклонена. Обратись к @xecibe.",parse_mode="HTML")
    except: pass
    await safe_edit(cb.message,f"❌ Заявка #{req_id} отклонена.",parse_mode="HTML"); await cb.answer("Отклонено!")

@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery): await query.answer(ok=True)

@router.message(F.successful_payment)
async def successful_payment(msg: Message):
    payload=msg.successful_payment.invoice_payload; uid=msg.from_user.id
    if payload.startswith("coins:"):
        coins=int(payload.split(":")[1]); await update_coins(uid,coins)
        user=await get_user(uid); await safe_answer(msg,f"✅ +{coins}💰\nБаланс: {user['coins']}💰",parse_mode="HTML")
    elif payload.startswith("vip:"):
        days=int(payload.split(":")[1])
        vip_until=(datetime.now()+timedelta(days=days)).isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET vip_until=? WHERE user_id=?",(vip_until,uid)); await db.commit()
        await safe_answer(msg,f"👑 <b>VIP на {days} дней!</b>\n• +50% монет\n• +50% XP",parse_mode="HTML")
    elif payload.startswith("item:"):
        iid=payload.split(":")[1]; await add_item(uid,iid)
        all_items={**EGG_TYPES,**WEAPONS,**ARMOR,**ITEMS}; item=all_items.get(iid,{})
        await safe_answer(msg,f"✅ {item.get('emoji','⭐')} <b>{item.get('name',iid)}</b> получен!",parse_mode="HTML")

# ── РАБОТА / ЕЖЕДНЕВКА ────────────────────────────────────────────────

async def h_work(msg):
    uid=msg.from_user.id
    async with get_user_lock(uid):
        user=await get_user(uid); cd=cd_left(user.get('last_work'),3600)
        if cd: await safe_reply(msg,f"⏳ Через <b>{fmt_t(cd)}</b>",parse_mode="HTML"); return
        # ── ИСПРАВЛЕНИЕ RACE CONDITION: сразу фиксируем КД внутри лока,
        #    чтобы параллельные клики не прошли проверку одновременно
        await set_ts(uid,'last_work')
        event=await get_active_event(); mult=event.get('mult_coins',1.0) if event else 1.0
        vip_mult=1.5 if await is_vip(uid) else 1.0
        wid = user.get('world', 1) or 1
        world_mult = WORLD_MULTIPLIERS.get(wid, 1)
        rebirth_bonus = user.get('rebirth_bonus') or 1.0
        base = random.randint(30, 120)
        earned = int(base * mult * vip_mult * world_mult * rebirth_bonus)
        await update_coins(uid, earned)
        await add_season_tokens(uid, SEASON_TOKEN_REWARDS["work"])
        await progress_daily_quest(uid, "work")
        # 10% шанс получить кристаллы
        crystal_bonus = ""
        if random.random() < 0.10:
            crystals = random.randint(1, 3 + wid)
            await update_crystals(uid, crystals)
            crystal_bonus = f"\n💎 +{crystals} кристаллов!"
        done=await progress_task(uid,'work')
        if done: await update_coins(uid,done['reward_coins'])
        u2=await get_user(uid)
    works=["🔧 Починил трубы","🚗 Развёз заказы","💻 Написал код","📦 Разгрузил фуру","🍕 Доставил пиццу","🎨 Нарисовал логотип"]
    ev=f" <i>x{mult:.1f}</i>" if mult>1 else ""; vn=" 👑+50%" if vip_mult>1 else ""
    rb_note = f" 🔥x{rebirth_bonus:.1f}" if rebirth_bonus > 1 else ""
    bon=f"\n✅ Задание! +{done['reward_coins']}💰" if done else ""
    await safe_reply(msg,
        f"{random.choice(works)}!\n+{earned}💰{ev}{vn}{rb_note}{crystal_bonus}{bon}"
        f"  Баланс: {u2['coins']}💰  💎{u2.get('crystals',0)}",
        parse_mode="HTML")


async def h_daily(msg):
    uid=msg.from_user.id; user=await get_user(uid); cd=cd_left(user.get('last_daily'),86400)
    if cd: await safe_reply(msg,f"⏳ Через <b>{fmt_t(cd)}</b>",parse_mode="HTML"); return
    event=await get_active_event(); mult=event.get('mult_coins',1.0) if event else 1.0
    vip_mult=1.5 if await is_vip(uid) else 1.0
    rew=int(150*mult*vip_mult); await update_coins(uid,rew); await set_ts(uid,'last_daily')
    await add_season_tokens(uid, SEASON_TOKEN_REWARDS["daily"])
    await progress_daily_quest(uid, "daily")
    bon=""
    r=random.random()
    if r<0.04: await add_item(uid,"rare_egg"); bon="\n🪺 Бонус: редкое яйцо!"
    elif r<0.15: await add_item(uid,"common_egg"); bon="\n🥚 Бонус: обычное яйцо!"
    u2=await get_user(uid)
    await safe_reply(msg,f"🎁 +{rew}💰{bon}\nБаланс: {u2['coins']}💰",parse_mode="HTML")


# ── ЗАДАНИЯ ───────────────────────────────────────────────────────────

async def h_tasks(msg):
    uid=msg.from_user.id; await assign_tasks(uid); tasks=await get_tasks(uid)
    if not tasks: await safe_answer(msg,"✅ Все выполнены! Скоро новые."); return
    txt="📋 <b>Задания:</b>\n\n"
    for t in tasks:
        pct=int(t['progress']/t['required']*10)
        txt+=f"📌 {t['description']}\n[{'█'*pct+'░'*(10-pct)}] {t['progress']}/{t['required']} +{t['reward_coins']}💰\n\n"
    await safe_answer(msg,txt,parse_mode="HTML")


# ── ПРИКЛЮЧЕНИЕ ───────────────────────────────────────────────────────

async def h_adventure(msg):
    uid=msg.from_user.id
    async with get_user_lock(uid):
        user=await get_user(uid); pet=await get_active_pet(uid)
        if not pet: await safe_reply(msg,"Нет питомца! Напиши <b>яйца</b> 🥚",parse_mode="HTML"); return
        if pet['is_sleeping']: await safe_reply(msg,"😴 Спит!"); return
        if pet['health']<20: await safe_reply(msg,"❤️ Слишком слаб!"); return
        cd=cd_left(user.get('last_adventure'),7200)
        if cd: await safe_reply(msg,f"⏳ Через <b>{fmt_t(cd)}</b>",parse_mode="HTML"); return
        # ── ИСПРАВЛЕНИЕ RACE CONDITION: фиксируем КД сразу внутри лока
        await set_ts(uid,'last_adventure')
        locs=[("🌲 Тёмный лес",(20,80)),("⛰️ Горная пещера",(30,100)),("🏰 Заброшенный замок",(50,150)),
              ("🏝️ Таинственный остров",(40,120)),("🌋 Огненная гора",(60,180)),("🌌 Астральный план",(80,250))]
        ln,cr=random.choice(locs); event=await get_active_event()
        em=event.get('egg_mult',1) if event else 1; cm=event.get('mult_coins',1.0) if event else 1.0
        vip_mult=1.5 if await is_vip(uid) else 1.0
        coins=int(random.randint(*cr)*cm*vip_mult); await update_coins(uid,coins)
        await add_season_tokens(uid, SEASON_TOKEN_REWARDS["adventure"])
        await progress_daily_quest(uid, "adventure")
        # Счётчик приключений + ачивка
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET adventure_count = adventure_count + 1 WHERE user_id=?", (uid,))
            await db.commit()
            async with db.execute("SELECT adventure_count FROM users WHERE user_id=?", (uid,)) as c:
                adv_count = (await c.fetchone())[0]
        if adv_count >= 50:
            await check_and_grant_achievement(uid, "adventure_50")
        res=[f"🧭 <b>{ln}</b>",f"💰 +{coins}"]
        if random.random()<0.5:
            drop=random.choice(["food","firstaid","ball","vitamins","elixir","premium","magic_potion"])
            await add_item(uid,drop); res.append(f"🎁 {ITEMS[drop]['emoji']} {ITEMS[drop]['name']}")
        if random.random()<0.08*em:
            er=random.random(); eid="common_egg" if er<0.5 else ("rare_egg" if er<0.85 else "legendary_egg")
            await add_item(uid,eid); res.append(f"🥚 <b>{EGG_TYPES[eid]['emoji']} {EGG_TYPES[eid]['name']}!</b>")
        if random.random()<0.2:
            dmg=random.randint(10,30); await update_pet(pet['pet_id'],health=max(1,pet['health']-dmg))
            res.append(f"🤕 Травма! -{dmg}❤️")
        xp=random.randint(25,60); lvl,up=await add_xp(pet['pet_id'],xp)
        res.append(f"✨ +{xp} XP"+(f"\n🎉 <b>Уровень {lvl}!</b>" if up else ""))
        done=await progress_task(uid,'adventure')
        if done: await update_coins(uid,done['reward_coins']); res.append(f"✅ Задание! +{done['reward_coins']}💰")
        u2=await get_user(uid); res.append(f"👛 {u2['coins']}💰")
    await safe_reply(msg,"\n".join(res),parse_mode="HTML")


# ══════════════════════════════════════════════════════════════════════
# 🏟️ АРЕНА v2.0 — пошаговые бои с выбором действий, артефактами и рейтингом
# ══════════════════════════════════════════════════════════════════════

# ── Константы арены ──────────────────────────────────────────────────
ARENA_CD = 120  # КД между PvP-боями (2 минуты)
ARENA_PVE_CD = 60  # КД между боями с ИИ (1 минута) — отдельный от PvP

ARENA_MOVES = {
    "attack":  {"emoji": "⚔️",  "name": "Атака",       "desc": "Нанести обычный урон"},
    "heavy":   {"emoji": "💥",  "name": "Мощный удар",  "desc": "+50% урона, но -10% точности"},
    "defend":  {"emoji": "🛡️",  "name": "Защита",       "desc": "Снизить входящий урон на 40%"},
    "skill":   {"emoji": "✨",   "name": "Спец. удар",   "desc": "Использовать способность питомца"},
}

ARENA_ARTIFACTS = {
    "berserker_potion": {"emoji": "🔴", "name": "Зелье ярости",   "effect": "atk_up",   "value": 40},
    "iron_shield":      {"emoji": "🧱", "name": "Железный щит",    "effect": "def_up",   "value": 50},
    "healing_herb":     {"emoji": "🌿", "name": "Лечебная трава",  "effect": "heal",     "value": 60},
    "lightning_bolt":   {"emoji": "⚡",  "name": "Молния",          "effect": "instant",  "value": 80},
}

ARENA_SEASONS = {
    1: {"name": "Бронза",   "emoji": "🥉", "min_rating": 0,    "max_rating": 1199},
    2: {"name": "Серебро",  "emoji": "🥈", "min_rating": 1200, "max_rating": 1499},
    3: {"name": "Золото",   "emoji": "🥇", "min_rating": 1500, "max_rating": 1799},
    4: {"name": "Платина",  "emoji": "💠", "min_rating": 1800, "max_rating": 2099},
    5: {"name": "Алмаз",    "emoji": "💎", "min_rating": 2100, "max_rating": 2499},
    6: {"name": "Мастер",   "emoji": "👑", "min_rating": 2500, "max_rating": 99999},
}

# ── Награды PvP (бой с другим игроком/подбор соперника) ───────────────
# По просьбе: награды за бой с живым соперником снижены.
ARENA_RANK_REWARDS = {
    1: {"coins": 120,  "xp": 40},
    2: {"coins": 220,  "xp": 70},
    3: {"coins": 380,  "xp": 110},
    4: {"coins": 650,  "xp": 170},
    5: {"coins": 1100, "xp": 280},
    6: {"coins": 2200, "xp": 480},
}

# ── Награды PvE ("бой с ИИ") — небольшие фиксированные, без влияния на рейтинг ──
ARENA_PVE_REWARDS = {
    "win":  {"coins_min": 100, "coins_max": 180, "xp": 30},
    "lose": {"coins_min": 30,  "coins_max": 60,  "xp": 10},
}

ARENA_ACTS = ["наносит молниеносный удар","бросается в атаку","делает финт и бьёт","атакует без пощады","наносит точный выпад"]
ARENA_CRITS = ["💥 КРИТ!", "⚡ СОКРУШИТЕЛЬНЫЙ УДАР!", "🔥 СМЕРТОНОСНЫЙ УДАР!"]
ARENA_DODGE = ["🌪️ уклоняется", "👻 исчезает в тени", "🌀 уходит от удара"]

# In-memory: активные арена-сессии (uid -> dict)
_arena_sessions: dict[int, dict] = {}
_arena_queue: list[dict] = []  # очередь на поиск соперника
_arena_queue_lock: asyncio.Lock = None
ARENA_QUEUE_TTL = 300

def _get_arena_queue_lock():
    global _arena_queue_lock
    if _arena_queue_lock is None:
        _arena_queue_lock = asyncio.Lock()
    return _arena_queue_lock

def _cleanup_arena_queue(now: float = None):
    now = now or time.time()
    _arena_queue[:] = [q for q in _arena_queue if now - q.get("ts", now) <= ARENA_QUEUE_TTL]

def _remove_from_arena_queue(uid: int):
    _arena_queue[:] = [q for q in _arena_queue if q.get("uid") != uid]

def arena_wait_kb(uid: int) -> object:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отменить подбор", callback_data=f"arena:cancelmatch:{uid}")
    b.button(text="🏟️ Меню арены", callback_data=f"arena:menu:{uid}")
    b.adjust(1)
    return b.as_markup()

def arena_result_kb(uid: int) -> object:
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Новый подбор", callback_data=f"arena:pvp:{uid}")
    b.button(text="🏟️ Меню арены", callback_data=f"arena:menu:{uid}")
    b.adjust(1)
    return b.as_markup()

def get_arena_rank(rating: int) -> dict:
    for rank_id in sorted(ARENA_SEASONS.keys(), reverse=True):
        r = ARENA_SEASONS[rank_id]
        if rating >= r["min_rating"]:
            return {"id": rank_id, **r}
    return {"id": 1, **ARENA_SEASONS[1]}

def arena_main_kb(uid: int) -> object:
    b = InlineKeyboardBuilder()
    b.button(text="🏟️ Подбор соперника",  callback_data=f"arena:pvp:{uid}")
    b.button(text="🤖 Бой с ИИ",                callback_data=f"arena:pve:{uid}")
    b.button(text="👤 Вызвать игрока",          callback_data=f"arena:challenge:{uid}")
    b.button(text="📋 Принять вызов",           callback_data=f"arena:checkreq:{uid}")
    b.button(text="🏆 Моя статистика",          callback_data=f"arena:stats:{uid}")
    b.button(text="🌍 Топ арены",               callback_data=f"arena:top:{uid}")
    b.adjust(1)
    return b.as_markup()

def arena_move_kb(uid: int, session_key: str, has_artifact: bool = False) -> object:
    b = InlineKeyboardBuilder()
    for move_id, move in ARENA_MOVES.items():
        b.button(
            text=f"{move['emoji']} {move['name']}",
            callback_data=f"amove:{move_id}:{uid}"
        )
    if has_artifact:
        b.button(text="💊 Использовать артефакт", callback_data=f"amove:artifact:{uid}")
    b.adjust(2)
    return b.as_markup()

def arena_accept_kb(req_id: int, to_uid: int) -> object:
    b = InlineKeyboardBuilder()
    b.button(text="⚔️ Принять вызов на арену!", callback_data=f"br:a:{req_id}:{to_uid}")
    b.button(text="❌ Отклонить",               callback_data=f"br:d:{req_id}:{to_uid}")
    b.adjust(1)
    return b.as_markup()

# ── Расчёт боевых параметров арены (с артефактами) ──────────────────
def _arena_stats(pet: dict, atk_bonus: int = 0, def_bonus: int = 0) -> tuple:
    atk, def_, hp = battle_stats(pet)
    return atk + atk_bonus, def_ + def_bonus, hp

# ── Генерация ИИ-противника для PvE ─────────────────────────────────
def _make_ai_opponent(player_pet: dict, rating: int) -> dict:
    rank = get_arena_rank(rating)
    # Выбираем питомца по рангу
    pools = {
        1: ["dog","cat","rabbit","hamster","parrot","turtle"],
        2: ["fox","wolf","bear","tiger","panda"],
        3: ["shark","eagle","gorilla","snow_leopard","dragon"],
        4: ["phoenix","unicorn","kraken","cerberus"],
        5: ["void_dragon","god_phoenix","titan","cosmos"],
        6: ["omega","zeus"],
    }
    pool = pools.get(rank["id"], pools[1])
    pet_type = random.choice(pool)
    p = PETS.get(pet_type, PETS["dog"])
    lvl = max(1, player_pet["level"] + random.randint(-3, 3))
    scale = 1.0 + (rating - 1000) / 2000  # масштаб по рангу
    return {
        "pet_id":    -1,
        "owner_id":  -1,
        "name":      p["name"],
        "pet_type":  pet_type,
        "level":     lvl,
        "health":    int(p["hp"] * scale),
        "max_health":int(p["hp"] * scale),
        "weapon":    None, "armor": None,
        "skill_atk": int(lvl / 5), "skill_def": int(lvl / 5),
        "skill_hp": 0, "skill_crit": int(lvl / 10), "skill_regen": 0,
        "wins": 0, "losses": 0, "is_shiny": False,
        "is_ai": True,
        "_ai_name": f"⚔️ {p['name']}-{rank['emoji']} [ИИ]",
        "_atk_scale": scale,
    }

# ── Одиночный раунд: рассчитывает урон с учётом действия ────────────
def _arena_round(atk: int, def_: int, move: str, pet: dict, opp_move: str, regen: int) -> tuple:
    """Возвращает (нанесённый урон, описание, использован_артефакт)"""
    crit_chance = 0.15 + (pet.get("skill_crit") or 0) * 0.03
    dodge_chance = 0.10 + (0.05 if opp_move == "defend" else 0)

    if move == "attack":
        base = atk + random.randint(-5, 10)
        dmg_reduction = def_ // 3 + (def_ // 2 if opp_move == "defend" else 0)
        if random.random() < dodge_chance:
            return 0, random.choice(ARENA_DODGE), False
        crit = random.random() < crit_chance
        raw = int(base * (1.8 if crit else 1.0))
        dmg = max(0, raw - dmg_reduction)
        act = random.choice(ARENA_CRITS) if crit else random.choice(ARENA_ACTS)
        return dmg, act, False

    elif move == "heavy":
        base = atk + random.randint(-5, 15)
        dmg_reduction = def_ // 3 + (def_ // 2 if opp_move == "defend" else 0)
        if random.random() < (dodge_chance + 0.10):  # heavy misses more
            return 0, "💨 промахивается (мощный удар)!", False
        crit = random.random() < crit_chance
        raw = int(base * 1.5 * (1.8 if crit else 1.0))
        dmg = max(0, raw - dmg_reduction)
        return dmg, f"💥 МОЩНЫЙ УДАР{' + КРИТ' if crit else ''}!", False

    elif move == "defend":
        # В защите атакует с 50% силой
        base = atk + random.randint(-5, 5)
        dmg_reduction = def_ // 3 + (def_ // 2 if opp_move == "defend" else 0)
        raw = int(base * 0.5)
        dmg = max(0, raw - dmg_reduction)
        return dmg, "🛡️ защищается и контратакует", False

    elif move == "skill":
        # Спец. удар: использует способность питомца
        p = PETS.get(pet.get("pet_type", "dog"), PETS["dog"])
        ability = p.get("ability", "")
        base = atk + random.randint(5, 20)
        dmg_reduction = def_ // 4  # skill пробивает немного брони
        crit = random.random() < (crit_chance + 0.10)
        raw = int(base * (2.0 if crit else 1.3))
        dmg = max(0, raw - dmg_reduction)
        return dmg, f"✨ {ability[:30]}{'...' if len(ability)>30 else ''}{'  КРИТ!' if crit else ''}", False

    return 0, "??", False

# ── Полный симуляция PvE-боя (против ИИ, 1 вызов) ───────────────────
async def run_arena_pve(chat_id: int, bot, pet: dict, uid: int):
    """Запускает PvE бой на арене. Отправляет результат через bot.send_message."""
    user = await get_user(uid)
    rating = user.get("battle_rating", 1000) or 1000
    rank = get_arena_rank(rating)
    ai = _make_ai_opponent(pet, rating)

    ma, md, mh = _arena_stats(pet)
    oa = int(PETS.get(ai["pet_type"], PETS["dog"])["atk"] * ai["_atk_scale"]) + ai["level"] * 3
    od = int(PETS.get(ai["pet_type"], PETS["dog"])["def"] * ai["_atk_scale"]) + ai["level"] * 2
    oh = ai["health"]
    m_regen = (pet.get("skill_regen") or 0) * 3

    p1h, p2h = mh, oh
    mpe = PETS.get(pet["pet_type"], PETS["dog"])
    ope = PETS.get(ai["pet_type"], PETS["dog"])

    log = [
        f"🏟️ <b>АРЕНА — PvE БОЙ</b>  {rank['emoji']} {rank['name']}",
        "─" * 20,
        f"🔵 {mpe['emoji']} <b>{pet['name']}</b>  ур.{pet['level']}",
        f"   ⚔️{ma}  🛡️{md}  ❤️{mh}",
        f"🔴 {ope['emoji']} <b>{ai['_ai_name']}</b>  ур.{ai['level']}",
        f"   ⚔️{oa}  🛡️{od}  ❤️{oh}",
        "─" * 20, "",
    ]

    mn = user.get("first_name", "?")
    ai_moves_pool = list(ARENA_MOVES.keys())

    for rd in range(1, 10):
        if p1h <= 0 or p2h <= 0:
            break
        if m_regen and p1h > 0:
            p1h = min(mh, p1h + m_regen)

        log.append(f"<b>⚔️ Раунд {rd}</b>")

        # ИИ выбирает действие — немного умнее рандома
        if p2h < oh * 0.3:
            ai_move = "defend"
        elif p1h < mh * 0.4 and random.random() < 0.4:
            ai_move = "heavy"
        else:
            ai_move = random.choice(["attack", "attack", "heavy", "defend", "skill"])

        # ИИ всегда делает "умный" выбор для атаки игрока
        player_move = "attack"  # PvE — авто

        # Атака игрока
        dmg, act, _ = _arena_round(ma, od, player_move, pet, ai_move, 0)
        p2h = max(0, p2h - dmg)
        log.append(f"  🔵 {pet['name']} {act} → <b>{dmg}</b>")
        if p2h <= 0:
            log.append(f"  💀 {ai['name']} повержен!")
            break

        # Атака ИИ
        dmg2, act2, _ = _arena_round(oa, md, ai_move, ai, player_move, 0)
        p1h = max(0, p1h - dmg2)
        log.append(f"  🔴 {ai['_ai_name']} {act2} → <b>{dmg2}</b>")
        if p1h <= 0:
            log.append(f"  💀 {pet['name']} повержен!")

        log.append(f"  {hpbar(p1h, mh)} {mn}\n  {hpbar(p2h, oh)} ИИ\n")

    log.append("─" * 20)
    player_won = p1h > p2h or p2h <= 0

    event = await get_active_event()
    rm = event.get("mult_coins", 1.0) if event else 1.0
    xm = event.get("mult_xp", 1.0) if event else 1.0
    vip_mult = 1.5 if await is_vip(uid) else 1.0

    # ── По запросу: бой с ИИ даёт небольшую фиксированную награду и НЕ
    #    трогает рейтинг/Elo арены — это отдельный казуальный режим
    #    с собственным кулдауном, а не замена PvP подбору соперника.
    if player_won:
        rcfg = ARENA_PVE_REWARDS["win"]
        rew = int(random.randint(rcfg["coins_min"], rcfg["coins_max"]) * rm * vip_mult)
        xrew = int(rcfg["xp"] * xm)
        await update_coins(uid, rew)
        await add_xp(pet["pet_id"], xrew)
        await update_pet(pet["pet_id"], wins=(pet.get("wins") or 0) + 1)
        log.append(
            f"\n🏆 <b>{mpe['emoji']} {pet['name']} ПОБЕДИЛ ИИ!</b>\n"
            f"🎁 +{rew}💰  +{xrew}✨"
        )
        await progress_task(uid, "win_battle")
        await add_season_tokens(uid, SEASON_TOKEN_REWARDS["battle_win"])
        await progress_daily_quest(uid, "battle_win")
        # Ачивки
        total_wins = sum(p.get("wins", 0) for p in await get_all_pets(uid))
        total_wins += 1
        if total_wins >= 1:   await check_and_grant_achievement(uid, "first_battle")
        if total_wins >= 10:  await check_and_grant_achievement(uid, "win_10")
        if total_wins >= 50:  await check_and_grant_achievement(uid, "win_50")
        if total_wins >= 100: await check_and_grant_achievement(uid, "win_100")
        if total_wins >= 500: await check_and_grant_achievement(uid, "win_500")
        wu = await get_user(uid)
        if wu and wu.get("coins", 0) >= 1_000_000:
            await check_and_grant_achievement(uid, "millionaire")
    else:
        rcfg = ARENA_PVE_REWARDS["lose"]
        consolation = int(random.randint(rcfg["coins_min"], rcfg["coins_max"]) * rm)
        await update_coins(uid, consolation)
        await update_pet(pet["pet_id"], losses=(pet.get("losses") or 0) + 1,
                         health=max(5, p1h))
        log.append(
            f"\n💀 <b>{pet['name']} проиграл ИИ...</b>\n"
            f"🎁 Утешение: +{consolation}💰"
        )

    await set_ts(uid, "last_battle_pve")

    # Кнопки после боя
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Ещё бой с ИИ", callback_data=f"arena:pve:{uid}")
    b.button(text="🏟️ Меню арены",    callback_data=f"arena:menu:{uid}")
    b.adjust(1)

    battle_text = "\n".join(log)
    try:
        await bot.send_message(chat_id, battle_text, parse_mode="HTML", reply_markup=b.as_markup())
    except Exception as e:
        logger.warning(f"arena pve send: {e}")

# ── PvP бой (оба реальных игрока) ───────────────────────────────────
async def run_arena_pvp(target_msg, mp: dict, op: dict, mid: int, oid: int, ou: dict,
                        notify: bool = False, bot=None, result_targets=None):
    """Пошаговый авто-бой двух реальных игроков на арене."""
    ma, md, mh = _arena_stats(mp)
    oa, od, oh = _arena_stats(op)
    mu = await get_user(mid)
    mn = mu.get("first_name", "?") if mu else "?"
    on = ou.get("first_name", "?") if ou else "?"
    mpe = PETS.get(mp["pet_type"], PETS["dog"])
    ope = PETS.get(op["pet_type"], PETS["dog"])
    mclan = await get_clan_by_user(mid)
    oclan = await get_clan_by_user(oid)
    mc = f" [{mclan['tag']}]" if mclan else ""
    oc = f" [{oclan['tag']}]" if oclan else ""

    m_crit_bonus = (mp.get("skill_crit") or 0) * 0.03
    o_crit_bonus = (op.get("skill_crit") or 0) * 0.03
    m_regen = (mp.get("skill_regen") or 0) * 3
    o_regen = (op.get("skill_regen") or 0) * 3

    # ── ИСПРАВЛЕНИЕ БАГА "битвы не происходит после 'найден соперник'":
    # раньше здесь снова вызывался get_user(...) и сразу .get(...) без проверки
    # на None. Если пользователь не находился в БД (гонка данных/бан/удаление),
    # это бросало AttributeError, который ничем не перехватывался — бой "терялся"
    # молча. Теперь используем уже полученные mu/ou с защитой от None.
    m_rating = (mu.get("battle_rating", 1000) or 1000) if mu else 1000
    o_rating = (ou.get("battle_rating", 1000) or 1000) if ou else 1000
    m_rank = get_arena_rank(m_rating)
    o_rank = get_arena_rank(o_rating)

    log = [
        f"🏟️ <b>АРЕНА — PvP БОЙ</b>",
        "─" * 20,
        f"🔵 {mpe['emoji']} <b>{mp['name']}</b> ({mn}{mc}) {m_rank['emoji']}",
        f"   Ур.{mp['level']}  ⚔️{ma}  🛡️{md}  ❤️{mh}",
        f"🔴 {ope['emoji']} <b>{op['name']}</b> ({on}{oc}) {o_rank['emoji']}",
        f"   Ур.{op['level']}  ⚔️{oa}  🛡️{od}  ❤️{oh}",
        "─" * 20, "",
    ]

    p1h, p2h = mh, oh

    for rd in range(1, 12):
        if p1h <= 0 or p2h <= 0:
            break
        if m_regen and p1h > 0:
            p1h = min(mh, p1h + m_regen)
        if o_regen and p2h > 0:
            p2h = min(oh, p2h + o_regen)

        log.append(f"<b>⚔️ Раунд {rd}</b>")

        # Оба выбирают ход по ИИ-логике (PvP авто — честный рандом с тактикой)
        def pick_move(hp, max_hp):
            ratio = hp / max_hp if max_hp else 1
            if ratio < 0.25:
                return random.choice(["defend", "defend", "skill", "attack"])
            elif ratio < 0.5:
                return random.choice(["attack", "heavy", "skill", "defend"])
            else:
                return random.choice(["attack", "attack", "heavy", "skill", "defend"])

        m_move = pick_move(p1h, mh)
        o_move = pick_move(p2h, oh)

        # Атака игрока 1
        dmg, act, _ = _arena_round(ma, od, m_move, mp, o_move, 0)
        p2h = max(0, p2h - dmg)
        move_icon = ARENA_MOVES[m_move]["emoji"]
        log.append(f"  🔵 {mp['name']} {move_icon} {act} → <b>{dmg}</b>")
        if p2h <= 0:
            log.append(f"  💀 {op['name']} повержен!")
            break

        # Атака игрока 2
        dmg2, act2, _ = _arena_round(oa, md, o_move, op, m_move, 0)
        p1h = max(0, p1h - dmg2)
        move_icon2 = ARENA_MOVES[o_move]["emoji"]
        log.append(f"  🔴 {op['name']} {move_icon2} {act2} → <b>{dmg2}</b>")
        if p1h <= 0:
            log.append(f"  💀 {mp['name']} повержен!")

        log.append(f"  {hpbar(p1h, mh)} {mn}\n  {hpbar(p2h, oh)} {on}\n")

    log.append("─" * 20)
    player1_won = p1h >= p2h or p2h <= 0
    wid = mid if player1_won else oid
    lid = oid if player1_won else mid
    wn = mn if player1_won else on
    wp = mp if player1_won else op
    lp = op if player1_won else mp
    we = mpe["emoji"] if player1_won else ope["emoji"]

    event = await get_active_event()
    rm = event.get("mult_coins", 1.0) if event else 1.0
    xm = event.get("mult_xp", 1.0) if event else 1.0
    vip_mult_w = 1.5 if await is_vip(wid) else 1.0

    # Elo-рейтинг
    k = 32
    expected_w = 1 / (1 + 10 ** ((o_rating if player1_won else m_rating) -
                                   (m_rating if player1_won else o_rating)) / 400)
    delta = max(10, min(50, int(k * (1 - expected_w))))

    w_rank = get_arena_rank(m_rating if player1_won else o_rating)
    w_rank_rewards = ARENA_RANK_REWARDS.get(w_rank["id"], ARENA_RANK_REWARDS[1])
    rew = int(w_rank_rewards["coins"] * rm * vip_mult_w)
    xrew = int(w_rank_rewards["xp"] * xm)

    await update_coins(wid, rew)
    await add_xp(wp["pet_id"], xrew)
    await update_pet(wp["pet_id"], wins=(wp.get("wins") or 0) + 1)
    await update_pet(lp["pet_id"], losses=(lp.get("losses") or 0) + 1,
                     health=max(5, p2h if player1_won else p1h))

    async with aiosqlite.connect(DB_PATH) as _rdb:
        await _rdb.execute("UPDATE users SET battle_rating=MIN(9999,battle_rating+?) WHERE user_id=?", (delta, wid))
        await _rdb.execute("UPDATE users SET battle_rating=MAX(100,battle_rating-?) WHERE user_id=?", (delta, lid))
        await _rdb.commit()

    log.append(
        f"\n{we} <b>{wn} ПОБЕДИЛ!</b> 🏆\n"
        f"🎁 +{rew}💰  +{xrew}✨  +{delta}⭐рейтинг\n"
        f"🔵 {mn}: {hpbar(p1h, mh)}\n🔴 {on}: {hpbar(p2h, oh)}"
    )

    await set_ts(mid, "last_battle")
    await set_ts(oid, "last_battle")
    await progress_task(wid, "win_battle")
    await add_season_tokens(wid, SEASON_TOKEN_REWARDS["battle_win"])
    await progress_daily_quest(wid, "battle_win")

    # Ачивки
    total_wins = sum(p.get("wins", 0) for p in await get_all_pets(wid)) + 1
    if total_wins >= 1:   await check_and_grant_achievement(wid, "first_battle")
    if total_wins >= 10:  await check_and_grant_achievement(wid, "win_10")
    if total_wins >= 50:  await check_and_grant_achievement(wid, "win_50")
    if total_wins >= 100: await check_and_grant_achievement(wid, "win_100")
    if total_wins >= 500: await check_and_grant_achievement(wid, "win_500")
    wu = await get_user(wid)
    if wu and wu.get("coins", 0) >= 1_000_000:
        await check_and_grant_achievement(wid, "millionaire")

    wclan = await get_clan_by_user(wid)
    if wclan:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE clans SET wins=wins+1 WHERE clan_id=?", (wclan["clan_id"],))
            await db.commit()

    battle_text = "\n".join(log)
    sent_chat_id = None
    try:
        if target_msg and target_msg.chat:
            sent_chat_id = target_msg.chat.id
            await target_msg.answer(battle_text, parse_mode="HTML", reply_markup=arena_result_kb(mid))
        elif bot:
            sent_chat_id = mid
            await bot.send_message(mid, battle_text, parse_mode="HTML", reply_markup=arena_result_kb(mid))
    except Exception as e:
        logger.warning(f"arena pvp send: {e}")
        try:
            if bot:
                await bot.send_message(mid, battle_text, parse_mode="HTML")
        except: pass

    if bot and result_targets:
        for chat_id, target_uid in result_targets:
            if chat_id == sent_chat_id:
                continue
            try:
                await bot.send_message(chat_id, battle_text, parse_mode="HTML", reply_markup=arena_result_kb(target_uid))
            except Exception as e:
                logger.warning(f"arena pvp extra send: {e}")

    if notify and bot:
        try:
            loser_id = lid
            result_txt = "🏆 Ты победил!" if loser_id == oid else "💀 Ты проиграл."
            b2 = InlineKeyboardBuilder()
            b2.button(text="🔄 Новый подбор", callback_data=f"arena:pvp:{loser_id}")
            await bot.send_message(
                loser_id,
                f"🏟️ <b>Бой на арене завершён!</b>\n{result_txt}\n"
                f"{wn} победил! +{rew}💰\n📊 Рейтинг: -{delta}⭐",
                parse_mode="HTML", reply_markup=b2.as_markup()
            )
        except: pass

# ── Обратная совместимость (старые кнопки битвы) ─────────────────────
async def run_auto_battle(target, mp, op, mid, oid, ou, notify=False, bot=None):
    """Устаревший интерфейс — теперь делегирует в арену."""
    await run_arena_pvp(target, mp, op, mid, oid, ou, notify=notify, bot=bot)

# ── Хендлеры арены ───────────────────────────────────────────────────

async def h_battle(msg, state):
    """Теперь открывает меню Арены вместо старого боя."""
    uid = msg.from_user.id
    user = await get_user(uid)
    pet = await get_active_pet(uid)
    if not pet:
        await safe_reply(msg, "🏟️ Нет питомца для арены!\nОткрой яйцо чтобы получить питомца.")
        return

    cd = cd_left(user.get("last_battle"), ARENA_CD)
    if cd:
        await safe_reply(msg, f"⏳ Арена восстанавливается: <b>{fmt_t(cd)}</b>", parse_mode="HTML")
        return

    rating = user.get("battle_rating", 1000) or 1000
    rank = get_arena_rank(rating)
    atk, df, hp = battle_stats(pet)
    pd = PETS.get(pet["pet_type"], PETS["dog"])

    await safe_reply(
        msg,
        f"🏟️ <b>АРЕНА</b>\n\n"
        f"{pd['emoji']} <b>{pet['name']}</b>  ур.{pet['level']}\n"
        f"⚔️ {atk}  🛡️ {df}  ❤️ {hp}\n\n"
        f"📊 Рейтинг: <b>{rating}</b>  {rank['emoji']} <b>{rank['name']}</b>\n"
        f"⚔️ Побед: {pet.get('wins', 0)}  💀 Поражений: {pet.get('losses', 0)}\n\n"
        f"Нажми <b>Подбор соперника</b> и жди второго игрока:",
        reply_markup=arena_main_kb(uid),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("arena:menu:"))
async def cb_arena_menu(cb: CallbackQuery):
    uid = int(cb.data.split(":")[-1])
    user = await get_user(uid)
    pet = await get_active_pet(uid)
    if not pet:
        await cb.answer("Нет питомца!", show_alert=True)
        return
    cd = cd_left(user.get("last_battle"), ARENA_CD)
    cd_pve = cd_left(user.get("last_battle_pve"), ARENA_PVE_CD)
    rating = user.get("battle_rating", 1000) or 1000
    rank = get_arena_rank(rating)
    pd = PETS.get(pet["pet_type"], PETS["dog"])
    atk, df, hp = battle_stats(pet)
    cd_txt = f"\n⏳ PvP КД: {fmt_t(cd)}" if cd else ""
    cd_pve_txt = f"\n⏳ ИИ-бой КД: {fmt_t(cd_pve)}" if cd_pve else ""
    await safe_edit(
        cb.message,
        f"🏟️ <b>АРЕНА</b>{cd_txt}{cd_pve_txt}\n\n"
        f"{pd['emoji']} <b>{pet['name']}</b>  ур.{pet['level']}\n"
        f"⚔️ {atk}  🛡️ {df}  ❤️ {hp}\n\n"
        f"📊 Рейтинг: <b>{rating}</b>  {rank['emoji']} <b>{rank['name']}</b>",
        reply_markup=arena_main_kb(uid),
        parse_mode="HTML"
    )
    await cb.answer()

@router.callback_query(F.data.startswith("arena:pve:"))
async def cb_arena_pve(cb: CallbackQuery):
    """Бой с ИИ-противником: отдельный от PvP кулдаун, не влияет на рейтинг,
    небольшая фиксированная награда (по запросу — около 100+ монет за победу)."""
    uid = int(cb.data.split(":")[-1])
    if cb.from_user and cb.from_user.id != uid:
        await cb.answer("Это не твоя кнопка.", show_alert=True)
        return
    user = await get_user(uid)
    if not user:
        await cb.answer("Игрок не найден!", show_alert=True)
        return
    cd = cd_left(user.get("last_battle_pve"), ARENA_PVE_CD)
    if cd:
        await cb.answer(f"⏳ КД: {fmt_t(cd)}", show_alert=True)
        return
    pet = await get_active_pet(uid)
    if not pet:
        await cb.answer("Нет питомца!", show_alert=True)
        return
    if pet["health"] < 20:
        await cb.answer("❤️ Питомец слишком слаб! Вылечи его.", show_alert=True)
        return
    await cb.answer()
    await run_arena_pve(cb.message.chat.id, cb.bot, pet, uid)

@router.callback_query(F.data.startswith("arena:pvp:"))
async def cb_arena_pvp(cb: CallbackQuery):
    """Подбор PvP соперника через очередь ожидания."""
    uid = int(cb.data.split(":")[-1])
    if cb.from_user and cb.from_user.id != uid:
        await cb.answer("Это не твоя кнопка.", show_alert=True)
        return
    user = await get_user(uid)
    if not user:
        await cb.answer("Игрок не найден!", show_alert=True)
        return
    cd = cd_left(user.get("last_battle"), ARENA_CD)
    if cd:
        await cb.answer(f"⏳ КД: {fmt_t(cd)}", show_alert=True)
        return
    pet = await get_active_pet(uid)
    if not pet:
        await cb.answer("Нет питомца!", show_alert=True)
        return
    if pet["health"] < 20:
        await cb.answer("❤️ Питомец слишком слаб! Вылечи его.", show_alert=True)
        return

    match = None
    already_queued = False
    queue_size = 0
    async with _get_arena_queue_lock():
        _cleanup_arena_queue()
        for q in _arena_queue:
            if q.get("uid") == uid:
                q["chat_id"] = cb.message.chat.id
                q["ts"] = time.time()
                already_queued = True
                break

        if not already_queued:
            for q in list(_arena_queue):
                opp_uid = q.get("uid")
                if not opp_uid or opp_uid == uid:
                    continue
                opp_user = await get_user(opp_uid)
                opp_pet = await get_active_pet(opp_uid)
                opp_cd = cd_left(opp_user.get("last_battle"), ARENA_CD) if opp_user else None
                if not opp_user or not opp_pet or opp_pet["health"] < 20 or opp_cd:
                    _remove_from_arena_queue(opp_uid)
                    continue
                match = (q, opp_user, opp_pet)
                _remove_from_arena_queue(opp_uid)
                break

            if not match:
                _arena_queue.append({"uid": uid, "chat_id": cb.message.chat.id, "ts": time.time()})

        queue_size = len(_arena_queue)

    if already_queued or not match:
        pd = PETS.get(pet["pet_type"], PETS["dog"])
        pet_emoji = pd["emoji"]
        pet_name = pet["name"]
        pet_level = pet["level"]
        atk, df, hp = battle_stats(pet)
        await safe_edit(
            cb.message,
            f"🏟️ <b>Подбор соперника</b>\n\n"
            f"{pet_emoji} <b>{pet_name}</b>  ур.{pet_level}\n"
            f"⚔️ {atk}  🛡️ {df}  ❤️ {hp}\n\n"
            f"Ты в очереди. Ждём второго игрока.\n"
            f"Сейчас в подборе: <b>{queue_size}</b>",
            reply_markup=arena_wait_kb(uid),
            parse_mode="HTML"
        )
        await cb.answer("⏳ Ищем соперника...")
        return

    opp_entry, ou, op = match
    opp_uid = ou["user_id"]
    user_name = user.get("first_name") or user.get("username") or str(uid)
    await cb.answer("⚔️ Соперник найден! Бой начинается!")
    try:
        await cb.message.bot.send_message(
            opp_entry.get("chat_id", opp_uid),
            f"🏟️ Соперник найден: <b>{user_name}</b>. Бой начинается!",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"arena queue notify: {e}")

    # ── ИСПРАВЛЕНИЕ БАГА "найден игрок, но битвы нет": раньше если run_arena_pvp
    # бросал исключение (например, get_user возвращал None из-за гонки данных),
    # ошибка просто падала в лог и игроки молча зависали без результата боя.
    # Теперь ловим исключение, логируем и явно сообщаем обоим, а не оставляем тишину.
    try:
        await run_arena_pvp(
            cb.message, pet, op, uid, opp_uid, ou,
            notify=False, bot=cb.message.bot,
            result_targets=[(cb.message.chat.id, uid), (opp_entry.get("chat_id", opp_uid), opp_uid)]
        )
    except Exception as e:
        logger.warning(f"run_arena_pvp crashed: {e}")
        fail_text = "⚠️ Бой не удалось провести из-за технической ошибки. КД не потрачен, попробуй поискать соперника снова."
        for chat_id, target_uid in [(cb.message.chat.id, uid), (opp_entry.get("chat_id", opp_uid), opp_uid)]:
            try:
                await cb.message.bot.send_message(chat_id, fail_text, reply_markup=arena_main_kb(target_uid))
            except Exception as e2:
                logger.warning(f"arena failure notify: {e2}")

@router.callback_query(F.data.startswith("arena:cancelmatch:"))
async def cb_arena_cancelmatch(cb: CallbackQuery):
    uid = int(cb.data.split(":")[-1])
    if cb.from_user and cb.from_user.id != uid:
        await cb.answer("Это не твоя кнопка.", show_alert=True)
        return
    async with _get_arena_queue_lock():
        _remove_from_arena_queue(uid)
    await safe_edit(
        cb.message,
        "❌ <b>Подбор отменён.</b>\n\nМожешь запустить поиск соперника заново.",
        reply_markup=arena_main_kb(uid),
        parse_mode="HTML"
    )
    await cb.answer("Подбор отменён")

@router.callback_query(F.data.startswith("arena:challenge:"))
async def cb_arena_challenge(cb: CallbackQuery, state: FSMContext):
    uid = int(cb.data.split(":")[-1])
    await state.set_state(S.battle_target)
    await state.update_data(challenger_uid=uid)
    await safe_edit(
        cb.message,
        "⚔️ Напиши @username или ID игрока которого хочешь вызвать на арену:\n\n"
        "💡 Для отмены напиши: <b>отмена</b>",
        parse_mode="HTML"
    )
    await cb.answer()

async def s_battle_target(msg: Message, state: FSMContext):
    text = msg.text.strip()
    if text.lower() in ["отмена", "cancel", "стоп", "stop", "/cancel"]:
        await state.clear()
        await safe_reply(msg, "❌ Вызов на арену отменён.")
        return
    data = await state.get_data()
    await state.clear()
    uid = data.get("challenger_uid", msg.from_user.id)
    ou = await get_user(int(text)) if text.isdigit() else await find_user_by_username(text)
    if not ou:
        await safe_reply(msg, "❌ Игрок не найден!\n\n💡 Для отмены напиши: <b>отмена</b>", parse_mode="HTML")
        return
    if ou["user_id"] == uid:
        await safe_reply(msg, "❌ Нельзя вызвать самого себя!", parse_mode="HTML")
        return
    pet = await get_active_pet(uid)
    op = await get_active_pet(ou["user_id"])
    if not pet: await safe_reply(msg, "❌ У тебя нет питомца!"); return
    if not op:  await safe_reply(msg, "❌ У игрока нет питомца!"); return
    await create_battle_request(uid, ou["user_id"])
    pd = PETS.get(pet["pet_type"], PETS["dog"])
    atk, df, hp = battle_stats(pet)
    target_name = ou.get("first_name") or ou.get("username") or str(ou["user_id"])
    await safe_reply(
        msg,
        f"🏟️ <b>{msg.from_user.first_name}</b> вызывает <b>{target_name}</b> на арену!\n\n"
        f"{pd['emoji']} {pet['name']}  ур.{pet['level']}\n"
        f"⚔️{atk}  🛡️{df}  ❤️{hp}\n\n"
        f"Соперник пишет <b>арена</b> или <b>битва</b> → <b>Принять вызов</b>",
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("arena:checkreq:"))
async def cb_arena_checkreq(cb: CallbackQuery):
    uid = int(cb.data.split(":")[-1])
    req = await get_pending_request(uid)
    if not req:
        await cb.answer("Нет входящих вызовов!", show_alert=True)
        return
    cp = await get_active_pet(req["from_user"])
    cu = await get_user(req["from_user"])
    if cp and cu:
        cpd = PETS.get(cp["pet_type"], PETS["dog"])
        atk, df, hp = battle_stats(cp)
        challenger_name = cu.get("first_name") or cu.get("username") or str(cu["user_id"])
        await safe_edit(
            cb.message,
            f"🏟️ <b>Вызов на арену!</b>\nОт: <b>{challenger_name}</b>\n\n"
            f"{cpd['emoji']} {cp['name']}  ур.{cp['level']}\n"
            f"⚔️{atk}  🛡️{df}  ❤️{hp}\n\nПринять вызов?",
            reply_markup=arena_accept_kb(req["req_id"], uid),
            parse_mode="HTML"
        )
    await cb.answer()

@router.callback_query(F.data.startswith("arena:stats:"))
async def cb_arena_stats(cb: CallbackQuery):
    uid = int(cb.data.split(":")[-1])
    user = await get_user(uid)
    pets = await get_all_pets(uid)
    rating = user.get("battle_rating", 1000) or 1000
    rank = get_arena_rank(rating)
    total_wins = sum(p.get("wins", 0) for p in pets)
    total_losses = sum(p.get("losses", 0) for p in pets)
    total = total_wins + total_losses
    wr = int(total_wins / total * 100) if total > 0 else 0

    # Следующий ранг
    next_rank_txt = ""
    for rank_id in sorted(ARENA_SEASONS.keys()):
        r = ARENA_SEASONS[rank_id]
        if r["min_rating"] > rating:
            need = r["min_rating"] - rating
            next_rank_txt = f"\n📈 До {r['emoji']} {r['name']}: <b>{need}</b>⭐"
            break

    b = InlineKeyboardBuilder()
    b.button(text="◀️ Назад", callback_data=f"arena:menu:{uid}")
    await safe_edit(
        cb.message,
        f"📊 <b>Статистика арены</b>\n\n"
        f"📊 Рейтинг: <b>{rating}</b>  {rank['emoji']} <b>{rank['name']}</b>{next_rank_txt}\n\n"
        f"⚔️ Побед: <b>{total_wins}</b>\n"
        f"💀 Поражений: <b>{total_losses}</b>\n"
        f"📈 Винрейт: <b>{wr}%</b>\n\n"
        f"🏟️ Ранги:\n" +
        "\n".join(
            f"{'✅' if rank['id'] == rid else '🔒'} {r['emoji']} {r['name']}"
            f"  ({r['min_rating']}+ ⭐)"
            for rid, r in ARENA_SEASONS.items()
        ),
        reply_markup=b.as_markup(),
        parse_mode="HTML"
    )
    await cb.answer()

@router.callback_query(F.data.startswith("arena:top:"))
async def cb_arena_top(cb: CallbackQuery):
    uid = int(cb.data.split(":")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT u.user_id, u.first_name, u.username, u.battle_rating "
            "FROM users u ORDER BY u.battle_rating DESC LIMIT 10"
        ) as c:
            rows = [dict(r) for r in await c.fetchall()]
    medals = ["🥇", "🥈", "🥉"]
    txt = "🏟️ <b>Топ арены</b>\n─────────────\n"
    for i, r in enumerate(rows):
        m = medals[i] if i < 3 else f"{i+1}."
        name = r.get("first_name") or r.get("username") or f"ID:{r['user_id']}"
        rating = r.get("battle_rating", 1000) or 1000
        rank = get_arena_rank(rating)
        txt += f"{m} {rank['emoji']} <b>{name}</b> — {rating}⭐\n"
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Назад", callback_data=f"arena:menu:{uid}")
    await safe_edit(cb.message, txt, reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

# ── Принять/отклонить вызов (совместимость с br:a / br:d) ────────────
@router.callback_query(F.data.startswith("battle:random:"))
async def cb_battle_random(cb: CallbackQuery):
    """Перенаправляем на подбор PvP."""
    uid = int(cb.data.split(":")[-1])
    # Меняем data чтобы передать в новый хендлер
    cb.data = f"arena:pvp:{uid}"
    await cb_arena_pvp(cb)

@router.callback_query(F.data.startswith("battle:choose:"))
async def cb_battle_choose(cb: CallbackQuery, state: FSMContext):
    uid = int(cb.data.split(":")[-1])
    await state.set_state(S.battle_target)
    await state.update_data(challenger_uid=uid)
    await safe_edit(
        cb.message,
        "⚔️ Напиши @username или ID игрока:\n\n💡 Для отмены напиши: <b>отмена</b>",
        parse_mode="HTML"
    )
    await cb.answer()

@router.callback_query(F.data.startswith("battle:check:"))
async def cb_battle_check(cb: CallbackQuery):
    uid = int(cb.data.split(":")[-1])
    cb.data = f"arena:checkreq:{uid}"
    await cb_arena_checkreq(cb)

@router.callback_query(F.data.startswith("br:a:"))
async def cb_bra(cb: CallbackQuery):
    """Принять вызов на арену."""
    parts = cb.data.split(":")
    req_id = int(parts[2])
    to_uid = int(parts[3])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM battle_requests WHERE req_id=?", (req_id,)) as c:
            req = await c.fetchone()
    if not req:
        await cb.answer("Вызов устарел!", show_alert=True)
        return
    req = dict(req)
    if req.get("status") != "pending":
        await cb.answer("Вызов уже обработан!", show_alert=True)
        return
    await accept_req(req_id)
    mp = await get_active_pet(to_uid)
    op = await get_active_pet(req["from_user"])
    ou = await get_user(req["from_user"])
    if not mp or not op:
        await cb.answer("Питомец недоступен!", show_alert=True)
        return
    if mp["health"] < 10:
        await cb.answer("❤️ Твой питомец слишком слаб!", show_alert=True)
        return
    # ВАЖНО: сначала отвечаем на callback, потом запускаем бой
    await cb.answer("⚔️ Вызов принят! Бой начинается!")
    await run_arena_pvp(
        cb.message, mp, op, to_uid, req["from_user"], ou,
        notify=True, bot=cb.message.bot
    )

@router.callback_query(F.data.startswith("br:d:"))
async def cb_brd(cb: CallbackQuery):
    req_id = int(cb.data.split(":")[2])
    await decline_req(req_id)
    await safe_edit(cb.message, "❌ Вызов на арену отклонён.")
    await cb.answer()

# ── ТОП ───────────────────────────────────────────────────────────────

async def h_top(msg):
    uid=msg.from_user.id; pets=await get_top_pets(5); medals=["🥇","🥈","🥉","4.","5."]
    txt="🏆 <b>Топ питомцев:</b>\n"
    for i,p in enumerate(pets):
        pe=PETS.get(p['pet_type'],PETS['dog']); r=RARITY_EMOJI.get(pe['rarity'],'⚪')
        own=p.get('first_name') or p.get('username') or f"ID:{p['owner_id']}"
        txt+=f"{medals[i]} {pe['emoji']} {p['name']} {r} ур.{p['level']} — {own}\n"
    await safe_reply(msg,txt,reply_markup=top_kb(uid),parse_mode="HTML")


@router.callback_query(F.data.startswith("top:pets:"))
async def cb_tp(cb):
    uid=int(cb.data.split(":")[-1]); pets=await get_top_pets(10); medals=["🥇","🥈","🥉"]
    txt="🐾 <b>Лучшие питомцы:</b>\n─────────────\n"
    for i,p in enumerate(pets):
        m=medals[i] if i<3 else f"{i+1}."; pe=PETS.get(p['pet_type'],PETS['dog'])
        own=p.get('first_name') or p.get('username') or f"ID:{p['owner_id']}"
        txt+=f"{m} {pe['emoji']} {p['name']} {RARITY_EMOJI.get(pe['rarity'],'⚪')} ур.<b>{p['level']}</b> — {own}\n"
    await safe_edit(cb.message,txt,reply_markup=top_kb(uid),parse_mode="HTML"); await cb.answer()

@router.callback_query(F.data.startswith("top:users:"))
async def cb_tu(cb):
    uid=int(cb.data.split(":")[-1]); users=await get_top_users(10); medals=["🥇","🥈","🥉"]
    txt="💰 <b>Богатейшие:</b>\n─────────────\n"
    for i,u in enumerate(users):
        m=medals[i] if i<3 else f"{i+1}."; n=u.get('first_name') or u.get('username') or f"ID:{u['user_id']}"
        vip_mark="👑" if u.get('vip_until') and datetime.fromisoformat(u['vip_until'])>datetime.now() else ""
        txt+=f"{m} {vip_mark}<b>{n}</b> — {u['coins']}💰\n"
    await safe_edit(cb.message,txt,reply_markup=top_kb(uid),parse_mode="HTML"); await cb.answer()

@router.callback_query(F.data.startswith("top:fighters:"))
async def cb_tf(cb):
    uid=int(cb.data.split(":")[-1]); pets=await get_top_pets(30)
    pets=sorted(pets,key=lambda p:p.get('wins',0),reverse=True)[:10]; medals=["🥇","🥈","🥉"]
    txt="⚔️ <b>Лучшие бойцы:</b>\n─────────────\n"
    for i,p in enumerate(pets):
        m=medals[i] if i<3 else f"{i+1}."; pe=PETS.get(p['pet_type'],PETS['dog'])
        own=p.get('first_name') or p.get('username') or f"ID:{p['owner_id']}"
        txt+=f"{m} {pe['emoji']} {p['name']} ⚔️{p.get('wins',0)}п — {own}\n"
    await safe_edit(cb.message,txt,reply_markup=top_kb(uid),parse_mode="HTML"); await cb.answer()

@router.callback_query(F.data.startswith("top:arena:"))
async def cb_top_arena(cb):
    uid = int(cb.data.split(":")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, first_name, username, battle_rating "
            "FROM users ORDER BY battle_rating DESC LIMIT 10"
        ) as c:
            rows = [dict(r) for r in await c.fetchall()]
    medals = ["🥇", "🥈", "🥉"]
    txt = "🏟️ <b>Рейтинг арены:</b>\n─────────────\n"
    for i, u in enumerate(rows):
        m = medals[i] if i < 3 else f"{i+1}."
        user_id = u["user_id"]
        display_name = u.get("first_name") or u.get("username") or f"ID:{user_id}"
        elo = u.get("battle_rating", 1000) or 1000
        rank = get_arena_rank(elo)
        rank_emoji = rank["emoji"]
        rank_name = rank["name"]
        txt += f"{m} {rank_emoji} <b>{display_name}</b> — {elo} ELO · {rank_name}\n"
    await safe_edit(cb.message, txt, reply_markup=top_kb(uid), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data.startswith("top:clans:"))
async def cb_tc(cb):
    uid=int(cb.data.split(":")[-1]); clans=await get_top_clans(); medals=["🥇","🥈","🥉"]
    txt="🏰 <b>Топ кланов:</b>\n─────────────\n"
    for i,c in enumerate(clans):
        m=medals[i] if i<3 else f"{i+1}."; txt+=f"{m} <b>[{c['tag']}] {c['name']}</b> ур.{c['level']} ⚔️{c['wins']}п\n"
    await safe_edit(cb.message,txt,reply_markup=top_kb(uid),parse_mode="HTML"); await cb.answer()

# ── ПРОФИЛЬ ───────────────────────────────────────────────────────────

async def h_profile(msg):
    uid=msg.from_user.id; user=await get_user(uid); pets=await get_all_pets(uid)
    inv=await get_inventory(uid); clan=await get_clan_by_user(uid)
    wins=sum(p.get('wins',0) for p in pets); losses=sum(p.get('losses',0) for p in pets)
    power=sum(pet_power(p) for p in pets); eggs=sum(i['quantity'] for i in inv if i['item_id'] in EGG_TYPES)
    myth=sum(1 for p in pets if PETS.get(p['pet_type'],{}).get('rarity')=='mythic')
    leg=sum(1 for p in pets if PETS.get(p['pet_type'],{}).get('rarity')=='legendary')
    shiny_cnt=sum(1 for p in pets if p.get('is_shiny'))
    vip_s="👑 VIP" if await is_vip(uid) else ""
    clan_info=f"\n🏰 [{clan['tag']}] {clan['name']}" if clan else ""
    wid = user.get('world', 1) or 1
    rebirths = user.get('rebirths') or 0
    rebirth_bonus = user.get('rebirth_bonus') or 1.0
    crystals = user.get('crystals') or 0
    season_tokens = user.get('season_tokens') or 0
    season_level  = user.get('season_level') or 0
    active_title  = user.get('title')
    title_txt = f"\n🏷️ {TITLES.get(active_title,{}).get('name','')}" if active_title else ""
    rb_info = f"\n🔥 Ребёртов: {rebirths} (бонус ×{rebirth_bonus:.1f})" if rebirths > 0 else ""
    rare_info = ""
    if myth or leg:
        rare_info = f" (💎{myth}" if myth else " ("
        rare_info += f" 🌟{leg}" if leg else ""
        rare_info += ")"
    await safe_reply(msg,
        f"👤 <b>{msg.from_user.first_name}</b> {vip_s}{title_txt}\n─────────────\n"
        f"💰 {user['coins']}  💎 {crystals} кристаллов\n"
        f"🪙 {season_tokens} токенов сезона  (ур.{season_level}/30)\n"
        f"🌍 Мир: {wid}{rb_info}\n"
        f"🐾 {len(pets)} питомцев{rare_info}"
        f"{f' ✨{shiny_cnt} shiny' if shiny_cnt else ''}\n"
        f"🥚 {eggs} яиц\n⚔️ {wins}п  💀 {losses}п\n💪 Сила: {power}{clan_info}",
        parse_mode="HTML")


# ── КЛАНЫ ─────────────────────────────────────────────────────────────

async def h_clan(msg, state):
    uid=msg.from_user.id; clan=await get_clan_by_user(uid)
    if clan:
        members=await get_clan_members(clan['clan_id'])
        my_role=next((m['role'] for m in members if m['user_id']==uid),'member')
        txt=(f"🏰 <b>Клан [{clan['tag']}] {clan['name']}</b>\n"
             f"📊 Уровень: {clan['level']}  💰 Банк: {clan['coins']}\n"
             f"⚔️ Победы: {clan['wins']}  👥 Участников: {len(members)}/20\n\n"
             f"<i>{clan.get('description') or 'Нет описания'}</i>\n\n"
             f"Роль: {'👑 Владелец' if my_role=='owner' else '👤 Участник'}")
        await safe_answer(msg,txt,reply_markup=clan_yes_kb(uid,my_role=='owner'),parse_mode="HTML")
    else:
        await safe_answer(msg,"🏰 <b>Кланы PetWorld</b>\n\nУ тебя нет клана!\nСоздай за 500💰 или найди существующий.",
                          reply_markup=clan_no_kb(uid),parse_mode="HTML")


@router.callback_query(F.data.startswith("clan:create:"))
async def cb_clan_create(cb: CallbackQuery, state: FSMContext):
    uid=int(cb.data.split(":")[-1]); user=await get_user(uid)
    if user['coins']<500: await cb.answer("❌ Нужно 500💰!",show_alert=True); return
    if user.get('clan_id'): await cb.answer("Ты уже в клане!",show_alert=True); return
    await state.set_state(S.clan_name)
    await safe_edit(cb.message,"🏰 <b>Создание клана</b>\n\nВведи название (2-20 символов):",parse_mode="HTML")
    await cb.answer()

async def s_clan_name(msg: Message, state: FSMContext):
    name=msg.text.strip()
    if not 2<=len(name)<=20: await safe_reply(msg,"❌ 2–20 символов!"); return
    await state.update_data(clan_name=name); await state.set_state(S.clan_tag)
    await safe_reply(msg,f"Название: <b>{name}</b>\n\nВведи тег (2-5 букв, например PWR):",parse_mode="HTML")

async def s_clan_tag(msg: Message, state: FSMContext):
    tag=msg.text.strip().upper()
    if not 2<=len(tag)<=5: await safe_reply(msg,"❌ Тег 2–5 символов!"); return
    data=await state.get_data(); await state.clear()
    uid=msg.from_user.id; clan_id,err=await create_clan(uid,data['clan_name'],tag)
    if err: await safe_reply(msg,"❌ Клан с таким названием или тегом уже существует!"); return
    await update_coins(uid,-500)
    await safe_answer(msg,f"🏰 <b>Клан [{tag}] {data['clan_name']} создан!</b>\n-500💰",
                      reply_markup=main_kb(),parse_mode="HTML")

@router.callback_query(F.data.startswith("clan:search:"))
async def cb_clan_search(cb: CallbackQuery, state: FSMContext):
    await state.set_state(S.clan_join)
    await safe_edit(cb.message,"🔍 Введи тег или название клана:")
    await cb.answer()

async def s_clan_join(msg: Message, state: FSMContext):
    await state.clear(); query=msg.text.strip()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory=aiosqlite.Row
        async with db.execute("SELECT * FROM clans WHERE LOWER(tag)=LOWER(?) OR LOWER(name)=LOWER(?)",(query,query)) as c:
            clan=await c.fetchone()
    if not clan: await safe_reply(msg,"❌ Клан не найден!"); return
    clan=dict(clan); ok,err=await join_clan(msg.from_user.id,clan['clan_id'])
    if not ok: await safe_reply(msg,f"❌ {err}"); return
    await safe_reply(msg,f"✅ Вступил в клан <b>[{clan['tag']}] {clan['name']}</b>!",parse_mode="HTML")

@router.callback_query(F.data.startswith("clan:members:"))
async def cb_clan_members(cb):
    uid=int(cb.data.split(":")[-1]); clan=await get_clan_by_user(uid)
    if not clan: await cb.answer("Нет клана!",show_alert=True); return
    members=await get_clan_members(clan['clan_id'])
    txt=f"👥 <b>Участники [{clan['tag']}]</b>\n\n"
    roles={"owner":"👑","officer":"⭐","member":"👤"}
    for m in members:
        n=m.get('first_name') or m.get('username') or str(m['user_id'])
        txt+=f"{roles.get(m['role'],'👤')} {n}\n"
    my_role=next((m['role'] for m in members if m['user_id']==uid),'member')
    await safe_edit(cb.message,txt,reply_markup=clan_yes_kb(uid,my_role=='owner'),parse_mode="HTML"); await cb.answer()

@router.callback_query(F.data.startswith("clan:bank:"))
async def cb_clan_bank(cb):
    uid=int(cb.data.split(":")[-1]); clan=await get_clan_by_user(uid)
    if not clan: await cb.answer("Нет клана!",show_alert=True); return
    b=InlineKeyboardBuilder()
    for amt in [100,500,1000]:
        b.button(text=f"💰 Внести {amt}",callback_data=f"clan:dep:{amt}:{uid}")
    b.button(text="◀️ Назад",callback_data=f"clan:back:{uid}"); b.adjust(3)
    await safe_edit(cb.message,f"💰 <b>Банк клана</b>\nВ казне: {clan['coins']}💰\n\nВнести:",
                    reply_markup=b.as_markup(),parse_mode="HTML"); await cb.answer()

@router.callback_query(F.data.startswith("clan:dep:"))
async def cb_clan_dep(cb):
    parts=cb.data.split(":"); amt=int(parts[2]); uid=int(parts[3])
    if amt <= 0:
        await cb.answer("❌ Некорректная сумма!", show_alert=True); return
    clan=await get_clan_by_user(uid)
    if not clan: await cb.answer("Нет клана!",show_alert=True); return
    async with get_user_lock(uid):
        # ── ИСПРАВЛЕНИЕ RACE CONDITION: списываем атомарно одним запросом
        #    с условием coins>=amt — параллельный спам кнопки депозита
        #    больше не может списать у игрока больше, чем у него есть,
        #    при этом каждый раз зачисляя amt в казну клана.
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "UPDATE users SET coins=coins-? WHERE user_id=? AND coins>=?",
                (amt, uid, amt)
            )
            changed = cur.rowcount
            if changed:
                await db.execute("UPDATE clans SET coins=coins+? WHERE clan_id=?",(amt,clan['clan_id']))
            await db.commit()
        if not changed:
            await cb.answer(f"❌ Нужно {amt}💰!",show_alert=True); return
    await cb.answer(f"✅ Внесено {amt}💰!")
    await cb_clan_bank(cb)

@router.callback_query(F.data.startswith("clan:leave:"))
async def cb_clan_leave(cb):
    uid=int(cb.data.split(":")[-1])
    ok, reason = await leave_clan(uid)
    if not ok and reason == "owner":
        await cb.answer("❌ Владелец не может покинуть клан! Распусти клан или передай права.", show_alert=True)
        return
    await safe_edit(cb.message,"✅ Ты покинул клан."); await cb.answer()

@router.callback_query(F.data.startswith("clan:disband:"))
async def cb_clan_disband(cb):
    uid=int(cb.data.split(":")[-1])
    clan=await get_clan_by_user(uid)
    if not clan:
        await cb.answer("Ты не в клане!", show_alert=True); return
    if clan['owner_id'] != uid:
        await cb.answer("Только владелец может распустить клан!", show_alert=True); return
    members = await get_clan_members(clan['clan_id'])
    await disband_clan(clan['clan_id'])
    await safe_edit(cb.message, f"🗑️ Клан [{clan['tag']}] {clan['name']} распущен.")
    await cb.answer("Клан распущен")
    for m in members:
        if m['user_id'] == uid: continue
        try:
            await cb.message.bot.send_message(m['user_id'],
                f"🗑️ Клан [{clan['tag']}] {clan['name']} был распущен владельцем.")
        except: pass

@router.callback_query(F.data.startswith("clan:back:"))
async def cb_clan_back(cb):
    uid=int(cb.data.split(":")[-1]); clan=await get_clan_by_user(uid)
    if not clan:
        await safe_edit(cb.message,"🏰 У тебя нет клана.",reply_markup=clan_no_kb(uid)); await cb.answer(); return
    members=await get_clan_members(clan['clan_id'])
    my_role=next((m['role'] for m in members if m['user_id']==uid),'member')
    txt=f"🏰 <b>[{clan['tag']}] {clan['name']}</b>\nУр.{clan['level']} 💰{clan['coins']} ⚔️{clan['wins']}п 👥{len(members)}/20"
    await safe_edit(cb.message,txt,reply_markup=clan_yes_kb(uid,my_role=='owner'),parse_mode="HTML"); await cb.answer()

# ── РЫНОК ─────────────────────────────────────────────────────────────

async def h_market(msg):
    uid=msg.from_user.id
    try: offers=await get_market_offers()
    except Exception as e:
        logger.warning(f"market: {e}"); await safe_answer(msg,"🏪 Ошибка загрузки рынка."); return
    user=await get_user(uid)
    if not offers:
        b=InlineKeyboardBuilder(); b.button(text="➕ Выставить питомца",callback_data=f"mkt:sell:{uid}"); b.button(text="💵 Быстрая продажа",callback_data=f"mkt:quick:{uid}"); b.adjust(1)
        await safe_answer(msg,f"🏪 <b>Рынок питомцев</b>\n💰{user['coins']}\n\nПока пусто!",
                          reply_markup=b.as_markup(),parse_mode="HTML"); return
    await safe_answer(msg,f"🏪 <b>Рынок питомцев</b> ({len(offers)})\n💰{user['coins']}\n\nВыбери:",
                      reply_markup=market_kb(offers,uid,0),parse_mode="HTML")


@router.callback_query(F.data.startswith("mkt:pg:"))
async def cb_mkt_pg(cb):
    parts=cb.data.split(":"); uid=int(parts[2]); page=int(parts[3])
    offers=await get_market_offers(); user=await get_user(uid)
    await safe_edit(cb.message,f"🏪 <b>Рынок питомцев</b> ({len(offers)})\n💰{user['coins']}\n\nВыбери:",
                    reply_markup=market_kb(offers,uid,page),parse_mode="HTML"); await cb.answer()

@router.callback_query(F.data.startswith("mkt:buy:"))
async def cb_mkt_buy(cb):
    parts=cb.data.split(":"); offer_id=int(parts[2]); uid=int(parts[3])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT trade_offers.*, pets.name as pet_name, pets.pet_type, pets.level,
                   pets.health, pets.max_health, pets.xp, pets.xp_to_next, pets.wins,
                   pets.losses, pets.is_shiny, pets.weapon, pets.armor,
                   pets.skill_atk, pets.skill_def, pets.skill_hp, pets.skill_crit, pets.skill_regen,
                   users.first_name, users.username
            FROM trade_offers
            JOIN pets ON trade_offers.pet_id = pets.pet_id
            JOIN users ON trade_offers.from_user = users.user_id
            WHERE trade_offers.offer_id = ? AND trade_offers.status = 'active'
        """, (offer_id,)) as c:
            offer = await c.fetchone()
    if not offer:
        await cb.answer("❌ Предложение не найдено!", show_alert=True); return
    offer = dict(offer)
    seller_name = offer['first_name'] or offer['username'] or str(offer['from_user'])
    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить покупку", callback_data=f"mkt:confirm:{offer_id}:{uid}")
    b.button(text="❌ Отмена",             callback_data=f"mkt:back:{uid}")
    b.adjust(1)
    caption = (f"🏪 <b>Купить питомца?</b>\n\n"
               f"Продавец: <b>{seller_name}</b>\n"
               f"💰 Цена: <b>{offer['price']:,}</b>\n\n"
               f"Подтвердить покупку?")
    # Отправляем карточку питомца
    pet_for_card = dict(offer); pet_for_card['name'] = offer['pet_name']
    try:
        buf   = generate_pet_card(pet_for_card)
        photo = BufferedInputFile(buf.read(), filename="pet_card.png")
        await cb.message.answer_photo(photo=photo, caption=caption,
                                      reply_markup=b.as_markup(), parse_mode="HTML")
        try: await cb.message.delete()
        except: pass
    except Exception as e:
        logger.warning(f"mkt_buy card: {e}")
        pd = PETS.get(offer['pet_type'], PETS['dog'])
        await safe_edit(cb.message,
            f"💰 <b>Подтверждение покупки</b>\n\n"
            f"{pd['emoji']} <b>{offer['pet_name']}</b> ур.{offer['level']}\n"
            f"Продавец: {seller_name}\n💰 Цена: <b>{offer['price']:,}</b>",
            reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("mkt:confirm:"))
async def cb_mkt_confirm(cb):
    parts=cb.data.split(":"); offer_id=int(parts[2]); uid=int(parts[3])
    if cb.from_user.id != uid:
        await cb.answer("❌ Это не твоя покупка!", show_alert=True); return
    ok,result=await buy_pet_from_market(uid,offer_id)
    if not ok: await cb.answer(f"❌ {result}",show_alert=True); return
    pet=await get_pet_by_id(result['pet_id'], uid); p=PETS.get(pet['pet_type'],PETS['dog']) if pet else {}
    
    # Получаем информацию о покупателе и продавце
    buyer = await get_user(uid)
    seller = await get_user(result['from_user'])
    buyer_name = buyer.get('first_name') or buyer.get('username') or str(uid)
    seller_name = seller.get('first_name') or seller.get('username') or str(result['from_user'])
    
    await cb.answer(f"✅ Куплен {p.get('emoji','')} {pet['name'] if pet else '?'}!")
    
    # Уведомление продавцу
    try:
        await cb.message.bot.send_message(result['from_user'],
            f"💰 <b>Твой питомец продан!</b>\n"
            f"{p.get('emoji','')} {pet['name'] if pet else '?'} → {buyer_name}\n"
            f"+{result['price']}💰",parse_mode="HTML")
    except: pass
    
    # Публичное сообщение в чат
    try:
        if GAME_GROUP:
            await cb.message.bot.send_message(GAME_GROUP,
                f"🏪 <b>Продажа на рынке!</b>\n\n"
                f"{buyer_name} купил {p.get('emoji','')} <b>{pet['name'] if pet else '?'}</b> "
                f"у {seller_name} за {result['price']} монет!",
                parse_mode="HTML", message_thread_id=GAME_THREAD)
    except: pass
    
    # Обновляем список предложений
    try:
        offers=await get_market_offers(); user=await get_user(uid)
        if offers: await safe_edit(cb.message,f"🏪 <b>Рынок</b> ({len(offers)})\n💰{user['coins']}\n\nВыбери:",reply_markup=market_kb(offers,uid),parse_mode="HTML")
        else:
            b=InlineKeyboardBuilder(); b.button(text="➕ Выставить",callback_data=f"mkt:sell:{uid}"); b.adjust(1)
            await safe_edit(cb.message,"🏪 Рынок пуст!",reply_markup=b.as_markup(),parse_mode="HTML")
    except: pass

@router.callback_query(F.data.startswith("mkt:back:"))
async def cb_mkt_back(cb):
    uid=int(cb.data.split(":")[-1])
    offers=await get_market_offers(); user=await get_user(uid)
    if not offers:
        b=InlineKeyboardBuilder(); b.button(text="➕ Выставить питомца",callback_data=f"mkt:sell:{uid}"); b.adjust(1)
        await safe_edit(cb.message,f"🏪 <b>Рынок питомцев</b>\n💰{user['coins']}\n\nПока пусто!",
                        reply_markup=b.as_markup(),parse_mode="HTML")
    else:
        await safe_edit(cb.message,f"🏪 <b>Рынок питомцев</b> ({len(offers)})\n💰{user['coins']}\n\nВыбери:",
                        reply_markup=market_kb(offers,uid,0),parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("mkt:quick:"))
async def cb_mkt_quick(cb):
    uid=int(cb.data.split(":")[-1])
    pets=await get_all_pets(uid)
    available=[p for p in pets if not p.get('for_sale')]
    if not available:
        await cb.answer("Нет питомцев для продажи!", show_alert=True); return
    b=InlineKeyboardBuilder()
    for p in available:
        pd=PETS.get(p['pet_type'],PETS['dog'])
        price=pet_quick_sell_price(p)
        b.button(text=f"{pd['emoji']} {p['name']} ур.{p['level']} — {price:,}💰",
                 callback_data=f"sellfast:{p['pet_id']}:{uid}")
    b.button(text="◀️ Назад", callback_data=f"mkt:back:{uid}")
    b.adjust(1)
    await safe_edit(cb.message,
        "💵 <b>Быстрая продажа</b>\n\nВыбери питомца. Цена = оценка −25%:",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("mkt:sell:"))
async def cb_mkt_sell(cb: CallbackQuery, state: FSMContext):
    uid=int(cb.data.split(":")[-1]); pets=await get_all_pets(uid)
    available=[p for p in pets if not p.get('for_sale')]
    if not available: await cb.answer("Нет доступных питомцев!",show_alert=True); return
    b=InlineKeyboardBuilder()
    for p in available:
        pd=PETS.get(p['pet_type'],PETS['dog'])
        b.button(text=f"{pd['emoji']} {p['name']} ур.{p['level']}",callback_data=f"mkt:sel:{p['pet_id']}:{uid}")
    b.adjust(1); await safe_edit(cb.message,"🏪 Выбери питомца для продажи:",reply_markup=b.as_markup()); await cb.answer()

@router.callback_query(F.data.startswith("mkt:sel:"))
async def cb_mkt_sel(cb: CallbackQuery, state: FSMContext):
    parts=cb.data.split(":"); pid=int(parts[2]); uid=int(parts[3])
    await state.update_data(sell_pid=pid); await state.set_state(S.sell_price)
    await safe_edit(cb.message,"💰 Введи цену продажи (монет):\n\n💡 Для отмены напиши: <b>отмена</b>", parse_mode="HTML"); await cb.answer()

@router.callback_query(F.data.startswith("mkt:mine:"))
async def cb_mkt_mine(cb):
    uid=int(cb.data.split(":")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory=aiosqlite.Row
        async with db.execute("SELECT trade_offers.*,pets.name as pname,pets.pet_type FROM trade_offers JOIN pets ON trade_offers.pet_id=pets.pet_id WHERE trade_offers.from_user=? AND trade_offers.status='active'",(uid,)) as c:
            offers=[dict(r) for r in await c.fetchall()]
    if not offers: await cb.answer("Нет активных предложений!",show_alert=True); return
    b=InlineKeyboardBuilder()
    for o in offers:
        p=PETS.get(o['pet_type'],PETS['dog'])
        b.button(text=f"❌ Снять {p['emoji']} {o['pname']} ({o['price']}💰)",callback_data=f"mkt:cancel:{o['offer_id']}:{uid}")
    b.adjust(1); await safe_edit(cb.message,"📋 <b>Мои предложения:</b>",reply_markup=b.as_markup(),parse_mode="HTML"); await cb.answer()

@router.callback_query(F.data.startswith("mkt:cancel:"))
async def cb_mkt_cancel(cb):
    parts=cb.data.split(":"); offer_id=int(parts[2]); uid=int(parts[3])
    ok=await cancel_trade_offer(uid,offer_id)
    await cb.answer("✅ Снято!" if ok else "❌ Ошибка!")
    await cb_mkt_mine(cb)

# ── ТРЕЙД ПИТОМЦАМИ ───────────────────────────────────────────────────

async def h_trade(msg, state):
    uid=msg.from_user.id
    # Проверяем входящий трейд
    pending=await get_pending_pet_trade(uid)
    if pending:
        from_user=await get_user(pending['from_user'])
        from_pet=await get_pet_by_id(pending['from_pet_id'])
        if from_user and from_pet:
            fp=PETS.get(from_pet['pet_type'],PETS['dog'])
            my_pets=await get_all_pets(uid)
            extra=""
            if pending['extra_coins']>0: extra=f"\n💰 Доплата: {pending['extra_coins']} монет от тебя"
            gift_note = "\n🎁 Инициатор разрешил принять без ответного питомца." if pending.get('allow_gift') else "\n⚠️ Нужно отдать своего питомца в ответ (или доплату)."
            await safe_reply(msg,
                f"🔄 <b>Предложение обмена!</b>\nОт: <b>{from_user.get('first_name','?')}</b>\n\n"
                f"Предлагает: {fp['emoji']} <b>{from_pet['name']}</b> ур.{from_pet['level']}{extra}{gift_note}\n\n"
                f"Выбери питомца для обмена или отклони:",
                reply_markup=trade_pets_kb(my_pets,uid,pending['from_user'],pending['trade_id'],
                                            allow_gift=bool(pending.get('allow_gift')), extra_coins=pending['extra_coins']),
                parse_mode="HTML")
            return
    # Новый трейд
    await state.set_state(S.trade_target)
    await safe_reply(msg,
        "🔄 <b>Трейд питомцами</b>\n\n"
        "Введи @username или ID игрока для обмена:\n\n"
        "💡 Для отмены напиши: <b>отмена</b>",
        parse_mode="HTML")


async def s_trade_target(msg: Message, state: FSMContext):
    text=msg.text.strip()
    
    # Проверка на отмену
    if text.lower() in ["отмена", "cancel", "стоп", "stop", "/cancel"]:
        await state.clear()
        await safe_reply(msg,"❌ Трейд отменён.")
        return
    
    ou=await get_user(int(text)) if text.isdigit() else await find_user_by_username(text)
    if not ou: 
        await safe_reply(msg,"❌ Игрок не найден!\n\n💡 Введи @username или ID игрока\nДля отмены напиши: <b>отмена</b>", parse_mode="HTML")
        return
    if ou['user_id']==msg.from_user.id: 
        await safe_reply(msg,"❌ Нельзя с собой!\n\nДля отмены напиши: <b>отмена</b>", parse_mode="HTML")
        return
    await state.update_data(trade_to=ou['user_id'],trade_to_name=ou.get('first_name','?'))
    await state.set_state(S.trade_pet)
    pets=await get_all_pets(msg.from_user.id)
    if not pets: await safe_reply(msg,"❌ У тебя нет питомцев!"); await state.clear(); return
    txt="🔄 Выбери питомца для обмена (напиши номер):\n\n"
    for i,p in enumerate(pets,1):
        pd=PETS.get(p['pet_type'],PETS['dog']); r=RARITY_EMOJI.get(pd['rarity'],'⚪')
        txt+=f"{i}. {pd['emoji']} {p['name']} {r} ур.{p['level']}\n"
    txt+="\n💡 Для отмены напиши: <b>отмена</b>"
    await state.update_data(trade_pets=[p['pet_id'] for p in pets])
    await safe_reply(msg,txt,parse_mode="HTML")

async def s_trade_pet(msg: Message, state: FSMContext):
    text = msg.text.strip()
    
    # Проверка на отмену
    if text.lower() in ["отмена", "cancel", "стоп", "stop", "/cancel"]:
        await state.clear()
        await safe_reply(msg,"❌ Трейд отменён.")
        return
    
    data=await state.get_data()
    if not msg.text.isdigit() or int(msg.text)<1 or int(msg.text)>len(data['trade_pets']):
        await safe_reply(msg,"❌ Введи номер из списка!\n\n💡 Для отмены напиши: <b>отмена</b>", parse_mode="HTML")
        return
    pet_id=data['trade_pets'][int(msg.text)-1]
    await state.update_data(trade_pet_id=pet_id); await state.set_state(S.trade_coins)
    await safe_reply(msg,"💰 Хочешь добавить монеты к обмену? Введи 0 если нет, или сумму доплаты:\n\n💡 Для отмены напиши: <b>отмена</b>", parse_mode="HTML")

async def s_trade_coins(msg: Message, state: FSMContext):
    text = msg.text.strip()
    
    # Проверка на отмену
    if text.lower() in ["отмена", "cancel", "стоп", "stop", "/cancel"]:
        await state.clear()
        await safe_reply(msg,"❌ Трейд отменён.")
        return
    
    if not msg.text.isdigit(): 
        await safe_reply(msg,"❌ Введи число!\n\n💡 Для отмены напиши: <b>отмена</b>", parse_mode="HTML")
        return
    extra=int(msg.text); data=await state.get_data()
    uid=msg.from_user.id
    pet=await get_pet_by_id(data['trade_pet_id'],uid)
    if not pet: await safe_reply(msg,"❌ Питомец не найден!"); await state.clear(); return
    if extra>0:
        user=await get_user(uid)
        if user['coins']<extra: await safe_reply(msg,"❌ Недостаточно монет!"); return
    await state.update_data(trade_extra=extra)
    await state.set_state(S.trade_gift)
    # ── ИСПРАВЛЕНИЕ БАГА "кража питомцев": спрашиваем явное согласие, может ли
    # получатель забрать пета вообще без ответной передачи (без своего пета и
    # без доплаты). По умолчанию это запрещено — обмен обязателен.
    b=InlineKeyboardBuilder()
    b.button(text="✅ Да, можно без обмена", callback_data=f"tradegift:yes:{uid}")
    b.button(text="🔒 Нет, только обмен пет-на-пет", callback_data=f"tradegift:no:{uid}")
    b.adjust(1)
    await safe_reply(msg,
        "❓ Разрешить получателю принять твоего питомца БЕЗ отдачи своего пета "
        "(если он откажется меняться)?\n\n"
        "🔒 Если выберешь «Нет», получатель обязательно должен отдать в ответ "
        "своего питомца"+(" или доплату." if extra>0 else "."),
        reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("tradegift:"))
async def cb_trade_gift_choice(cb: CallbackQuery, state: FSMContext):
    parts=cb.data.split(":")
    choice=parts[1]
    owner_uid=int(parts[2]) if len(parts)>2 and parts[2].isdigit() else None
    if owner_uid is not None and cb.from_user.id != owner_uid:
        await cb.answer("❌ Это не твоя кнопка!", show_alert=True); return
    current = await state.get_state()
    if current != S.trade_gift:
        await cb.answer(); return
    allow_gift = 1 if choice == "yes" else 0
    data=await state.get_data(); await state.clear()
    uid=cb.from_user.id; to_uid=data.get('trade_to'); extra=data.get('trade_extra',0)
    pet=await get_pet_by_id(data.get('trade_pet_id'),uid)
    if not pet or not to_uid:
        await cb.answer(); await safe_edit(cb.message,"❌ Питомец не найден или трейд устарел!"); return
    trade_id=await create_pet_trade(uid,to_uid,data['trade_pet_id'],extra,allow_gift)
    pd=PETS.get(pet['pet_type'],PETS['dog']); r=RARITY_EMOJI.get(pd['rarity'],'⚪')
    extra_txt=f"\n💰 Доплата: {extra} монет от получателя" if extra>0 else ""
    # Уведомляем получателя
    try:
        my_name=cb.from_user.first_name or "Игрок"
        await cb.message.bot.send_message(to_uid,
            f"🔄 <b>{my_name}</b> предлагает тебе обмен питомцами!\n\n"
            f"Предлагает: {pd['emoji']} <b>{pet['name']}</b> {r} ур.{pet['level']}{extra_txt}\n\n"
            f"Напиши <b>трейд</b> чтобы ответить!",parse_mode="HTML")
    except: pass
    await cb.answer("✅ Предложение отправлено!")
    await safe_edit(cb.message,f"✅ Предложение обмена отправлено!\n{pd['emoji']} {pet['name']} {r}{extra_txt}",parse_mode="HTML")

@router.callback_query(F.data.startswith("trade:accept:"))
async def cb_trade_accept(cb: CallbackQuery):
    parts=cb.data.split(":"); trade_id=int(parts[2]); to_pet_id_str=parts[3]; uid=int(parts[4])
    if cb.from_user.id != uid:
        await cb.answer("❌ Это не твой обмен!", show_alert=True); return
    to_pet_id=int(to_pet_id_str) if to_pet_id_str!="0" else None
    ok,result=await accept_pet_trade(trade_id,to_pet_id,cb.from_user.id)
    if not ok: await cb.answer(f"❌ {result}",show_alert=True); return
    from_pet=await get_pet_by_id(result['from_pet_id'])
    fp=PETS.get(from_pet['pet_type'],PETS['dog']) if from_pet else {}
    await cb.answer("✅ Обмен совершён!")
    await safe_edit(cb.message,f"🔄 <b>Обмен совершён!</b>\n\nТы получил: {fp.get('emoji','')} {from_pet['name'] if from_pet else '?'}",parse_mode="HTML")
    try:
        from_user=await get_user(result['from_user'])
        msg_text=f"🔄 <b>Обмен принят!</b>\n"
        if to_pet_id:
            to_pet=await get_pet_by_id(to_pet_id)
            tp=PETS.get(to_pet['pet_type'],PETS['dog']) if to_pet else {}
            msg_text+=f"Ты получил: {tp.get('emoji','')} {to_pet['name'] if to_pet else '?'}"
        else:
            msg_text+="Питомец передан без ответного обмена."
        await cb.message.bot.send_message(result['from_user'],msg_text,parse_mode="HTML")
    except: pass

@router.callback_query(F.data.startswith("trade:decline:"))
async def cb_trade_decline(cb: CallbackQuery):
    parts=cb.data.split(":"); trade_id=int(parts[2]); uid=int(parts[3])
    if cb.from_user.id != uid:
        await cb.answer("❌ Это не твой обмен!", show_alert=True); return
    await decline_pet_trade(trade_id)
    await cb.answer("❌ Обмен отклонён.")
    await safe_edit(cb.message,"❌ Ты отклонил предложение обмена.")

# ── КАРТОЧНАЯ ДУЭЛЬ (новый способ заработка) ─────────────────────────
# Игрок тянет 3 карты, бот тянет 3 карты. Побеждает тот у кого сумма выше.
# Туз = 11, Картинки (J/Q/K) = 10, остальные = номинал.
# Победа x1.8 | Ничья — возврат | Поражение — потеря ставки.
# Каждые 4 победы подряд — бонус +20% к следующему выигрышу (streak).

CARD_FACES = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]
CARD_VALUES = {"2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,"10":10,"J":10,"Q":10,"K":10,"A":11}
CARD_SUITS  = ["♠","♥","♦","♣"]

def draw_cards(n=3):
    return [(random.choice(CARD_FACES), random.choice(CARD_SUITS)) for _ in range(n)]

def cards_sum(cards):
    return sum(CARD_VALUES[c[0]] for c in cards)

def fmt_cards(cards):
    return "  ".join(f"{v}{s}" for v,s in cards)

async def h_duel(msg):
    uid = msg.from_user.id
    user = await get_user(uid)
    streak = user.get("duel_streak", 0) or 0
    streak_txt = f"\n🔥 Серия побед: {streak} (бонус +{min(streak//4,5)*20}%)" if streak >= 4 else ""
    await safe_answer(msg,
        f"🃏 <b>Карточная дуэль</b>\n💰 {user['coins']:,}{streak_txt}\n\n"
        "Тяни 3 карты — у кого сумма выше, тот победил!\n"
        "Победа → x1.8 ставки | Ничья → возврат\n\n"
        "Выбери ставку:",
        reply_markup=duel_kb(uid), parse_mode="HTML")

@router.callback_query(F.data.startswith("duel:play:"))
async def cb_duel(cb):
    parts = cb.data.split(":"); bet = int(parts[2]); uid = int(parts[3])
    if bet <= 0:
        await cb.answer("❌ Некорректная ставка!", show_alert=True); return
    async with get_user_lock(uid):
        # ── ИСПРАВЛЕНИЕ RACE CONDITION: ставка списывается атомарно одним
        #    запросом с условием coins>=bet. Если параллельный клик уже
        #    списал монеты — этот запрос затронет 0 строк и мы откажем.
        async with aiosqlite.connect(DB_PATH) as dbc:
            cur = await dbc.execute(
                "UPDATE users SET coins=coins-? WHERE user_id=? AND coins>=?",
                (bet, uid, bet)
            )
            changed = cur.rowcount
            await dbc.commit()
        if not changed:
            await cb.answer(f"❌ Нужно {bet}💰!", show_alert=True); return

        user = await get_user(uid)

        # Тянем карты
        p_cards = draw_cards(3)
        b_cards = draw_cards(3)
        p_sum = cards_sum(p_cards)
        b_sum = cards_sum(b_cards)

        streak = user.get("duel_streak", 0) or 0

        if p_sum > b_sum:
            # Победа — считаем бонус за серию (ставка уже списана выше, начисляем полный выигрыш)
            bonus_pct = min(streak // 4, 5) * 0.20  # каждые 4 победы +20%, макс +100%
            mult = 1.8 + bonus_pct
            win = int(bet * mult)
            await update_coins(uid, win)
            new_streak = streak + 1
            streak_txt = f"\n🔥 Серия: {new_streak}!" if new_streak >= 2 else ""
            bonus_txt = f" (+{int(bonus_pct*100)}% бонус серии)" if bonus_pct > 0 else ""
            result = f"✅ <b>Победа!{bonus_txt}</b>\n+{win - bet:,}💰{streak_txt}"
        elif p_sum == b_sum:
            # Ничья — возврат ставки (была списана выше)
            await update_coins(uid, bet)
            new_streak = streak
            result = f"🤝 <b>Ничья!</b> Ставка возвращена."
        else:
            # Поражение — ставка уже списана, ничего возвращать не нужно
            new_streak = 0
            result = f"❌ <b>Поражение!</b> -{bet:,}💰"

        # Обновляем streak
        async with aiosqlite.connect(DB_PATH) as dbc:
            await dbc.execute("UPDATE users SET duel_streak=? WHERE user_id=?", (new_streak, uid))
            await dbc.commit()

        u2 = await get_user(uid)
        await progress_task(uid, "duel")
        await progress_daily_quest(uid, "duel")
    await safe_edit(cb.message,
        f"🃏 <b>Карточная дуэль</b>\n{'─'*15}\n"
        f"Ты:  {fmt_cards(p_cards)} = <b>{p_sum}</b>\n"
        f"Бот: {fmt_cards(b_cards)} = <b>{b_sum}</b>\n\n"
        f"{result}\n\n💰 {u2['coins']:,}",
        reply_markup=duel_kb(uid), parse_mode="HTML")
    await cb.answer()

# ── ТУРНИР ────────────────────────────────────────────────────────────

async def h_tournament(msg):
    t=await get_active_tournament()
    if not t: await safe_answer(msg,"🎯 <b>Турниры</b>\n\nСейчас нет активных турниров.",parse_mode="HTML"); return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM tournament_players WHERE tournament_id=?",(t['tournament_id'],)) as c: cnt=(await c.fetchone())[0]
        async with db.execute("SELECT tp_id FROM tournament_players WHERE tournament_id=? AND user_id=?",(t['tournament_id'],msg.from_user.id)) as c: already=await c.fetchone()
    uid=msg.from_user.id; b=InlineKeyboardBuilder()
    if not already and t['status']=='registration':
        b.button(text="⚔️ Участвовать!",callback_data=f"tour:join:{t['tournament_id']}:{uid}")
    b.adjust(1)
    await safe_answer(msg,f"🎯 <b>{t['name']}</b>\n📊 {'Регистрация' if t['status']=='registration' else 'Идёт'}\n"
                     f"👥 {cnt}/{t['max_players']}\n🏆 Приз: {t['prize']}💰\n"
                     f"{'✅ Ты зарегистрирован!' if already else ''}",
                     reply_markup=b.as_markup(),parse_mode="HTML")


@router.callback_query(F.data.startswith("tour:join:"))
async def cb_tour_join(cb: CallbackQuery):
    parts=cb.data.split(":"); tid=int(parts[2]); uid=int(parts[3])
    pet=await get_active_pet(uid)
    if not pet: await cb.answer("Нужен питомец!",show_alert=True); return
    ok,err=await join_tournament(uid,tid)
    if not ok: await cb.answer(f"❌ {err}",show_alert=True); return
    await cb.answer("✅ Ты зарегистрирован!")
    try: await safe_edit(cb.message,(cb.message.text or "")+" ✅",parse_mode="HTML")
    except: pass

# ── ИВЕНТ ─────────────────────────────────────────────────────────────

async def h_event(msg):
    event=await get_active_event()
    if not event: await safe_reply(msg,"🎪 Нет активных ивентов."); return
    ends=datetime.fromisoformat(event['ends_at']); rem=ends-datetime.now()
    h=int(rem.total_seconds()//3600); m=int((rem.total_seconds()%3600)//60)
    await safe_reply(msg,f"🎪 <b>{event['name']}</b>\n\n{event['description']}\n\n"
                     f"⏰ Осталось: {h}ч {m}м\n✨ XP ×{event['mult_xp']:.1f}  💰 ×{event['mult_coins']:.1f}",parse_mode="HTML")


# ── ПЕРЕИМЕНОВАНИЕ ────────────────────────────────────────────────────


async def s_rename(msg: Message, state: FSMContext):
    name=msg.text.strip()
    if not 2<=len(name)<=20: await safe_reply(msg,"❌ 2–20 символов!"); return
    data=await state.get_data(); await update_pet(data['pet_id'],name=name); await state.clear()
    await safe_reply(msg,f"✅ Переименован в <b>{name}</b>!",parse_mode="HTML")

# ── ПОМОЩЬ ────────────────────────────────────────────────────────────

async def h_help(msg):
    await safe_reply(msg,
        "🐾 <b>PetWorld v17.0</b>\n\n<b>Основные команды:</b>\n"
        "• <b>питомцы</b> — питомцы\n• <b>яйца</b> — открыть яйцо\n"
        "• <b>магазин</b> — купить предметы\n• <b>работа</b> — заработать монеты\n"
        "• <b>ежедневка</b> — ежедневная награда\n"
        "• <b>битва</b> — выбор соперника\n• <b>поход</b> — приключение\n"
        "• <b>задания</b> — задания\n• <b>клан</b> — кланы\n"
        "• <b>рынок</b> — продажа питомцев\n• <b>трейд</b> — обмен питомцами\n"
        "• <b>дуэль</b> — карточная дуэль\n• <b>донат</b> — звёзды\n"
        "• <b>топ</b> — рейтинг\n• <b>профиль</b> — статистика\n\n"
        "🆕 <b>Новое в v17.0:</b>\n"
        "• <b>сезон</b> — Сезон 2: Эра Титанов 🎫\n"
        "• <b>26 новых питомцев</b> и <b>24 снаряжения</b> ⚔️\n"
        "• <b>миры</b> — валюта, мастерство, события, боссы 🌍\n"
        "• <b>эволюция</b> — /evolve — прокачка стадий питомца 🧬\n"
        "• <b>колесо</b> — /wheel — колесо удачи с джекпотом 🎡\n"
        "• <b>премиум-эмодзи</b> — /premiumemoji — статус и перезагрузка 🎨\n\n"
        "🆕 <b>Новое в v7.0:</b>\n"
        "• <b>сезон</b> — /season — сезонный пропуск и токены 🪙\n"
        "• <b>ачивки</b> — /achievements — достижения и титулы 🏆\n"
        "• <b>скиллы</b> — /skills — прокачка питомца 💡\n"
        "• <b>аукцион</b> — /auction — торги питомцами 🏪\n\n"
        "♻️ <b>Старые фичи:</b>\n"
        "• <b>ребёрт</b> — сброс за 1M💰 + бонус\n"
        "• <b>кристаллы</b> — баланс 💎\n"
        "• <b>босс</b> — глобальный босс\n"
        "• <b>слить</b> — слияние питомцев за 100💎\n\n"
        "💸 Перевод: ответь на сообщение игрока:\n<code>/transfer 500</code>\n"
        "⚠️ Комиссия на перевод: 25%",
        parse_mode="HTML")


# ── РЕБЁРТ 🔥 ─────────────────────────────────────────────────────────

async def h_rebirth(msg):
    uid = msg.from_user.id
    user = await get_user(uid)
    rebirths = user.get('rebirths') or 0
    rebirth_bonus = user.get('rebirth_bonus') or 1.0
    crystals = user.get('crystals') or 0
    b = InlineKeyboardBuilder()
    b.button(text="🔥 Выполнить ребёрт!", callback_data=f"rebirth:confirm:{uid}")
    b.adjust(1)
    need = 1_000_000 - user['coins']
    can_rebirth = user['coins'] >= 1_000_000
    rebirth_status = "✅ Ты можешь сделать ребёрт!" if can_rebirth else f"❌ Нужно 1,000,000💰 (не хватает {need:,})"
    await safe_reply(msg,
        f"🔥 <b>Система Ребёрта</b>\n\n"
        f"Ребёртов: <b>{rebirths}</b>\n"
        f"Текущий бонус наград: <b>×{rebirth_bonus:.2f}</b>\n"
        f"💰 Монет: {user['coins']:,} / 1,000,000\n"
        f"💎 Кристаллов: {crystals}\n\n"
        f"После ребёрта:\n"
        f"• Монеты сбрасываются до 300\n"
        f"• Бонус наград станет ×{rebirth_bonus*1.5:.2f} (накапливается!)\n"
        f"• Питомцы, яйца, экипировка сохраняются\n\n"
        f"{rebirth_status}",
        reply_markup=b.as_markup() if can_rebirth else None,
        parse_mode="HTML")

@router.callback_query(F.data.startswith("rebirth:confirm:"))
async def cb_rebirth_confirm(cb):
    uid = int(cb.data.split(":")[-1])
    ok, result = await do_rebirth(uid)
    if not ok:
        await cb.answer(f"❌ {result}", show_alert=True); return
    new_rebirths = result
    new_bonus = round(1.5 ** new_rebirths, 4)
    await check_and_grant_achievement(uid, "first_rebirth")
    await cb.answer("🔥 Ребёрт выполнен!")
    await safe_edit(cb.message,
        f"🔥 <b>РЕБЁРТ #{new_rebirths} ВЫПОЛНЕН!</b>\n\n"
        f"💰 Монеты сброшены до 300\n"
        f"✨ Новый бонус наград: ×{new_bonus:.2f}\n"
        f"Продолжай играть — бонус действует на работу и приключения!",
        parse_mode="HTML")


# ── КРИСТАЛЛЫ 💎 ──────────────────────────────────────────────────────

async def h_crystals(msg):
    uid = msg.from_user.id
    user = await get_user(uid)
    crystals = user.get('crystals') or 0
    await safe_reply(msg,
        f"💎 <b>Кристаллы</b>\n\n"
        f"У тебя: <b>{crystals} 💎</b>\n\n"
        f"Как получить:\n"
        f"• Работа — 10% шанс\n"
        f"• Атака глобального босса\n\n"
        f"Как потратить:\n"
        f"• ✨ Слияние питомцев — 100💎\n\n"
        f"<i>Используй /fuse или напиши <b>слить</b></i>",
        parse_mode="HTML")


# ── ГЛОБАЛЬНЫЙ БОСС 👹 ────────────────────────────────────────────────

async def h_boss(msg):
    global _active_boss
    uid = msg.from_user.id
    async with get_boss_lock():
        boss = _active_boss
    if not boss:
        await safe_reply(msg,
            "👹 <b>Глобальный босс</b>\n\n"
            "Сейчас нет активного босса.\n"
            "Боссы появляются каждые 30 мин (5% шанс).\n\n"
            "<i>Следи за уведомлениями!</i>", parse_mode="HTML")
        return
    bdata = boss['data']
    hp = boss['hp']
    total_hp = bdata['hp']
    attackers = len(boss['attackers'])
    b = InlineKeyboardBuilder()
    b.button(text="⚔️ Атаковать!", callback_data=f"boss:attack:{uid}")
    b.adjust(1)
    hp_bar = mbar(hp, total_hp, 10)
    await safe_reply(msg,
        f"👹 <b>{bdata['name']}</b>\n\n"
        f"❤️ {hp_bar} {hp:,}/{total_hp:,}\n"
        f"⚔️ Атака: {bdata['atk']}  🛡️ Защита: {bdata['def']}\n"
        f"👥 Атакующих: {attackers}\n\n"
        f"💎 Награда: {bdata['reward_crystals'][0]}–{bdata['reward_crystals'][1]} кристаллов\n"
        f"🐾 Топ-3 получат редкого питомца!",
        reply_markup=b.as_markup(), parse_mode="HTML")

@router.callback_query(F.data.startswith("boss:attack:"))
async def cb_boss_attack(cb):
    uid = int(cb.data.split(":")[-1])
    await h_attack_boss(cb.message, uid, cb)

async def h_attack(msg):
    """Команда /attack"""
    uid = msg.from_user.id
    await h_attack_boss(msg, uid, None)

async def h_attack_boss(target, uid, cb=None):
    global _active_boss
    async with get_boss_lock():
        boss = _active_boss
        if not boss:
            txt = "👹 Нет активного босса! Напиши <b>босс</b> для проверки."
            if cb: await cb.answer(txt, show_alert=True)
            else: await safe_reply(target, txt, parse_mode="HTML")
            return
        pet = await get_active_pet(uid)
        if not pet:
            txt = "❌ Нет активного питомца!"
            if cb: await cb.answer(txt, show_alert=True)
            else: await safe_reply(target, txt, parse_mode="HTML")
            return
        atk, def_, hp = battle_stats(pet)
        # Урон по боссу
        dmg = max(1, atk - boss['data']['def'] // 2 + random.randint(-5, 15))
        boss['hp'] = max(0, boss['hp'] - dmg)
        boss['attackers'][uid] = boss['attackers'].get(uid, 0) + dmg
        dead = boss['hp'] <= 0
        bdata = boss['data']
        remaining_hp = boss['hp']
        total_hp = bdata['hp']

        if dead:
            # Финализируем босса — раздаём награды
            attackers_sorted = sorted(boss['attackers'].items(), key=lambda x: x[1], reverse=True)
            _active_boss = None
            # Раздача кристаллов всем участникам
            for auid, dealt in attackers_sorted:
                share = min(1.0, dealt / total_hp)
                crystals_min, crystals_max = bdata['reward_crystals']
                earned = max(1, int(share * random.randint(crystals_min, crystals_max)))
                await update_crystals(auid, earned)
                try:
                    await target.bot.send_message(
                        auid,
                        f"👹 <b>{bdata['name']} повержен!</b>\n"
                        f"💎 Твоя награда: +{earned} кристаллов\n"
                        f"Урон нанесено: {dealt:,}",
                        parse_mode="HTML"
                    )
                except: pass
            # Топ-3 получают редкого питомца
            for i, (auid, dealt) in enumerate(attackers_sorted[:3]):
                rare_pt = bdata['rare_pet']
                p = PETS.get(rare_pt, PETS['dragon'])
                await create_pet(auid, p['name'], rare_pt)
                try:
                    await target.bot.send_message(
                        auid,
                        f"🏆 Топ-{i+1} урона! Получаешь питомца:\n"
                        f"{p['emoji']} <b>{p['name']}</b>!",
                        parse_mode="HTML"
                    )
                except: pass
            result_txt = f"💀 <b>БОСС ПОВЕРЖЕН!</b> Урон: {dmg}"
            # Ачивка boss_kill всем участникам + запись мира в boss_kills_json
            for auid, _ in attackers_sorted:
                await check_and_grant_achievement(auid, "boss_kill")
                # Записываем kill в boss_kills_json для секретного мира
                try:
                    u_tmp = await get_user(auid)
                    w_tmp = int(u_tmp.get("world", 1) or 1) if str(u_tmp.get("world", 1) or 1).isdigit() else 1
                    import json as _json
                    bkj = _json.loads(u_tmp.get("boss_kills_json") or "{}")
                    bkj[str(w_tmp)] = True
                    async with aiosqlite.connect(DB_PATH) as _bkdb:
                        await _bkdb.execute("UPDATE users SET boss_kills_json=? WHERE user_id=?",
                                            (_json.dumps(bkj), auid))
                        await _bkdb.commit()
                except: pass
        else:
            # Прогресс ежедневного квеста boss_attack
            await progress_daily_quest(uid, "boss_attack")
            result_txt = (
                f"⚔️ Удар! -{dmg} урона боссу\n"
                f"❤️ {mbar(remaining_hp, total_hp, 8)} {remaining_hp:,}/{total_hp:,}\n"
                f"Суммарный урон: {boss['attackers'][uid]:,}"
            )

    if cb:
        await cb.answer(f"⚔️ -{dmg} урона!")
        await safe_edit(cb.message, result_txt + "\n\nНапиши <b>босс</b> для обновления.", parse_mode="HTML")
    else:
        await safe_reply(target, result_txt, parse_mode="HTML")




# ── СЛИЯНИЕ ПИТОМЦЕВ ✨ ────────────────────────────────────────────────

async def h_fuse(msg):
    uid = msg.from_user.id
    user = await get_user(uid)
    crystals = user.get('crystals') or 0
    pets = await get_all_pets(uid)
    if len(pets) < 2:
        await safe_reply(msg, "✨ Нужно минимум 2 питомца для слияния!", parse_mode="HTML")
        return
    b = InlineKeyboardBuilder()
    for p in pets[:10]:
        pd = PETS.get(p['pet_type'], PETS['dog'])
        shiny = " ✨" if p.get('is_shiny') else ""
        b.button(text=f"{pd['emoji']} {p['name']} ур.{p['level']}{shiny}",
                 callback_data=f"fuse:p1:{p['pet_id']}:{uid}")
    b.adjust(1)
    not_enough = "\n⚠️ <b>Недостаточно кристаллов!</b> Нужно 100💎." if crystals < 100 else ""
    await safe_reply(msg,
        f"✨ <b>Слияние питомцев</b>\n\n"
        f"💎 У тебя: <b>{crystals}</b> кристаллов\n"
        f"💎 Стоимость: <b>100</b> кристаллов{not_enough}\n\n"
        f"Слияние объединяет двух питомцев в одного более сильного!\n\n"
        f"Выбери <b>первого</b> питомца (кристаллы спишутся только при подтверждении):",
        reply_markup=b.as_markup(), parse_mode="HTML")

@router.callback_query(F.data.startswith("fuse:p1:"))
async def cb_fuse_p1(cb):
    parts = cb.data.split(":"); p1_id = int(parts[2]); uid = int(parts[3])
    # Проверяем кристаллы ДО показа второго шага — но НЕ списываем
    async with aiosqlite.connect(DB_PATH) as _db:
        async with _db.execute("SELECT crystals FROM users WHERE user_id=?", (uid,)) as _c:
            _row = await _c.fetchone()
    actual_crystals = _row[0] if _row else 0
    if actual_crystals < 100:
        await cb.answer(f"❌ Недостаточно кристаллов! У тебя {actual_crystals}💎, нужно 100💎", show_alert=True)
        return
    p1 = await get_pet_by_id(p1_id, uid)
    if not p1: await cb.answer("Не найден!", show_alert=True); return
    pets = await get_all_pets(uid)
    b = InlineKeyboardBuilder()
    for p in pets:
        if p['pet_id'] == p1_id: continue
        pd = PETS.get(p['pet_type'], PETS['dog'])
        shiny = " ✨" if p.get('is_shiny') else ""
        b.button(text=f"{pd['emoji']} {p['name']} ур.{p['level']}{shiny}",
                 callback_data=f"fuse:do:{p1_id}:{p['pet_id']}:{uid}")
    b.adjust(1)
    pd1 = PETS.get(p1['pet_type'], PETS['dog'])
    await safe_edit(cb.message,
        f"✨ Первый: {pd1['emoji']} <b>{p1['name']}</b> ур.{p1['level']}\n"
        f"💎 Кристаллы: <b>{actual_crystals}</b> (спишутся 100 при подтверждении)\n\n"
        f"Выбери <b>второго</b> питомца для слияния:",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("fuse:do:"))
async def cb_fuse_do(cb):
    parts = cb.data.split(":"); p1_id = int(parts[2]); p2_id = int(parts[3]); uid = int(parts[4])

    # Всё делается в одной транзакции: проверка кристаллов, удаление питомцев, создание нового
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # 1. Проверяем и списываем кристаллы атомарно
        async with db.execute(
            "UPDATE users SET crystals=crystals-100 WHERE user_id=? AND crystals>=100", (uid,)
        ) as cur:
            changed = cur.rowcount
        if not changed:
            await db.rollback()
            async with db.execute("SELECT crystals FROM users WHERE user_id=?", (uid,)) as c:
                row = await c.fetchone()
            have = row[0] if row else 0
            await cb.answer(f"❌ Недостаточно кристаллов! У тебя {have}💎, нужно 100💎", show_alert=True)
            return

        # 2. Проверяем что оба питомца принадлежат игроку и существуют
        async with db.execute("SELECT * FROM pets WHERE pet_id=? AND owner_id=?", (p1_id, uid)) as c:
            p1_row = await c.fetchone()
        async with db.execute("SELECT * FROM pets WHERE pet_id=? AND owner_id=?", (p2_id, uid)) as c:
            p2_row = await c.fetchone()
        if not p1_row or not p2_row:
            await db.rollback()
            await cb.answer("❌ Питомец не найден! Возможно, уже удалён.", show_alert=True)
            return
        p1 = dict(p1_row); p2 = dict(p2_row)

        # 3. Запоминаем кто был активным ДО удаления
        was_active_id = None
        async with db.execute("SELECT pet_id FROM pets WHERE owner_id=? AND active=1", (uid,)) as c:
            active_row = await c.fetchone()
        if active_row:
            was_active_id = active_row[0]

        # 4. Определяем тип нового питомца
        pd1 = PETS.get(p1['pet_type'], PETS['dog'])
        pd2 = PETS.get(p2['pet_type'], PETS['dog'])
        rarity_order = ["common", "rare", "legendary", "mythic", "godlike"]
        r1 = rarity_order.index(pd1.get('rarity', 'common') if pd1.get('rarity', 'common') in rarity_order else 'common')
        r2 = rarity_order.index(pd2.get('rarity', 'common') if pd2.get('rarity', 'common') in rarity_order else 'common')
        base_type = p1['pet_type'] if r1 >= r2 else p2['pet_type']
        is_shiny = random.random() < 0.15
        new_level = (p1['level'] + p2['level']) // 2 + 1
        pdata = PETS[base_type]
        new_hp = pdata['hp'] + (new_level - 1) * 10
        # ── ИСПРАВЛЕНИЕ БАГА: xp_to_next должен соответствовать new_level,
        #    иначе он остаётся равным дефолту схемы (100, как у 1-го уровня),
        #    и питомец высокого уровня левелапается почти мгновенно от любого
        #    опыта — внешне это выглядит как "уровень сбрасывается".
        new_xtn = xp_to_next_for_level(new_level)

        # 5. Удаляем оба старых питомца
        await db.execute("DELETE FROM pets WHERE pet_id IN (?,?)", (p1_id, p2_id))

        # 6. Создаём нового питомца — НЕ трогаем active других питомцев
        #    active=1 только если один из слитых был активным, иначе active=0
        new_active = 1 if was_active_id in (p1_id, p2_id) else 0
        await db.execute(
            "INSERT INTO pets (owner_id, name, pet_type, health, max_health, level, xp, xp_to_next, is_shiny, active) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (uid, pdata['name'], base_type, new_hp, new_hp, new_level, 0, new_xtn, 1 if is_shiny else 0, new_active)
        )
        async with db.execute("SELECT last_insert_rowid()") as c:
            new_pet_id = (await c.fetchone())[0]

        # 7. Если активный питомец не удалялся — восстанавливаем его active=1
        if was_active_id and was_active_id not in (p1_id, p2_id):
            await db.execute("UPDATE pets SET active=0 WHERE owner_id=? AND pet_id!=?", (uid, was_active_id))
            await db.execute("UPDATE pets SET active=1 WHERE pet_id=?", (was_active_id,))

        await db.commit()

    if is_shiny:
        await check_and_grant_achievement(uid, "shiny_pet")
    # Ачивки за слияние
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET fuse_count = fuse_count + 1 WHERE user_id=?", (uid,))
        await db.commit()
        async with db.execute("SELECT fuse_count FROM users WHERE user_id=?", (uid,)) as c:
            fuse_count = (await c.fetchone())[0]
    await check_and_grant_achievement(uid, "first_fuse")
    if fuse_count >= 5:
        await check_and_grant_achievement(uid, "fuse_5")
    await progress_daily_quest(uid, "fuse")
    pd = PETS[base_type]
    shiny_txt = " ✨ <b>SHINY!</b>" if is_shiny else ""
    active_txt = "\n⚔️ Стал активным питомцем!" if new_active else ""
    await safe_edit(cb.message,
        f"✨ <b>Слияние завершено!</b>\n\n"
        f"{pd['emoji']} <b>{pd['name']}</b>{shiny_txt}\n"
        f"📊 Уровень: {new_level}\n"
        f"❤️ HP: {new_hp}\n"
        f"💎 Потрачено: 100 кристаллов{active_txt}\n\n"
        f"Питомец добавлен! Напиши <b>питомцы</b>",
        parse_mode="HTML")
    await cb.answer("✨ Слияние выполнено!")


# ══════════════════════════════════════════════════════════════════════
# v7.0 — СКИЛЛЫ ПИТОМЦЕВ
# ══════════════════════════════════════════════════════════════════════

async def h_skills(msg):
    uid = msg.from_user.id
    pet = await get_active_pet(uid)
    if not pet:
        await safe_reply(msg, "❌ Нет активного питомца!"); return
    sp = pet.get('skill_points', 0) or 0
    b = InlineKeyboardBuilder()
    for sk_id, sk in PET_SKILLS.items():
        cur = pet.get(f'skill_{sk_id}', 0) or 0
        if cur < sk['max']:
            b.button(text=f"{sk['name']} ({cur}/{sk['max']}) — {sk['cost']}🔮",
                     callback_data=f"skill:up:{sk_id}:{uid}")
        else:
            b.button(text=f"{sk['name']} МАКС ✅", callback_data=f"skill:maxed:{uid}")
    spent_points = sum((pet.get(f"skill_{sk_id}") or 0) * sk["cost"] for sk_id, sk in PET_SKILLS.items())
    if spent_points > 0:
        b.button(text=f"♻️ Сбросить скиллы ({SKILL_RESET_COST}💎)", callback_data=f"skill:reset:{uid}")
    b.adjust(1)
    pd = PETS.get(pet['pet_type'], PETS['dog'])
    skills_txt = "\n".join(
        f"  {sk['name']}: {pet.get(f'skill_{sk_id}',0) or 0}/{sk['max']}"
        for sk_id, sk in PET_SKILLS.items()
    )
    await safe_reply(msg,
        f"💡 <b>Скиллы питомца</b>\n\n"
        f"{pd['emoji']} <b>{pet['name']}</b> ур.{pet['level']}\n"
        f"🔮 Очков скиллов: <b>{sp}</b>\n\n"
        f"<b>Текущие скиллы:</b>\n{skills_txt}\n\n"
        f"💡 Очки скиллов даются при повышении уровня питомца.\n"
        f"Выбери скилл для прокачки:",
        reply_markup=b.as_markup(), parse_mode="HTML")

@router.callback_query(F.data.startswith("skill:up:"))
async def cb_skill_up(cb: CallbackQuery):
    parts = cb.data.split(":"); sk_id = parts[2]; uid = int(parts[3])
    pet = await get_active_pet(uid)
    if not pet: await cb.answer("Нет питомца!", show_alert=True); return
    sp = pet.get('skill_points', 0) or 0
    sk = PET_SKILLS.get(sk_id)
    if not sk: await cb.answer("Неверный скилл!", show_alert=True); return
    cur = pet.get(f'skill_{sk_id}', 0) or 0
    if cur >= sk['max']: await cb.answer("Уже максимум!", show_alert=True); return
    if sp < sk['cost']: await cb.answer(f"Нужно {sk['cost']}🔮 очков скиллов!", show_alert=True); return
    new_val = cur + 1
    new_sp = sp - sk['cost']
    await update_pet(pet['pet_id'], **{f'skill_{sk_id}': new_val, 'skill_points': new_sp})
    await cb.answer(f"✅ {sk['name']} прокачан до {new_val}!")
    # Обновляем меню
    pet2 = await get_active_pet(uid)
    sp2 = pet2.get('skill_points', 0) or 0
    b = InlineKeyboardBuilder()
    for s_id, s in PET_SKILLS.items():
        c2 = pet2.get(f'skill_{s_id}', 0) or 0
        if c2 < s['max']:
            b.button(text=f"{s['name']} ({c2}/{s['max']}) — {s['cost']}🔮",
                     callback_data=f"skill:up:{s_id}:{uid}")
        else:
            b.button(text=f"{s['name']} МАКС ✅", callback_data=f"skill:maxed:{uid}")
    spent_points = sum((pet2.get(f"skill_{s_id}") or 0) * s["cost"] for s_id, s in PET_SKILLS.items())
    if spent_points > 0:
        b.button(text=f"♻️ Сбросить скиллы ({SKILL_RESET_COST}💎)", callback_data=f"skill:reset:{uid}")
    b.adjust(1)
    skills_txt = "\n".join(f"  {s['name']}: {pet2.get(f'skill_{s_id}',0) or 0}/{s['max']}" for s_id, s in PET_SKILLS.items())
    pd = PETS.get(pet2['pet_type'], PETS['dog'])
    await safe_edit(cb.message,
        f"💡 <b>Скиллы питомца</b>\n\n"
        f"{pd['emoji']} <b>{pet2['name']}</b> ур.{pet2['level']}\n"
        f"🔮 Очков скиллов: <b>{sp2}</b>\n\n"
        f"<b>Текущие скиллы:</b>\n{skills_txt}",
        reply_markup=b.as_markup(), parse_mode="HTML")

@router.callback_query(F.data.startswith("skill:reset:do:"))
async def cb_skill_reset_do(cb: CallbackQuery):
    uid = int(cb.data.split(":")[-1])
    if cb.from_user.id != uid:
        await cb.answer("Это не твой питомец!", show_alert=True); return
    async with get_user_lock(uid):
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM pets WHERE owner_id=? AND active=1 ORDER BY level DESC LIMIT 1", (uid,)
            ) as c:
                row = await c.fetchone()
            if not row:
                await cb.answer("Нет активного питомца!", show_alert=True); return
            pet = dict(row)
            refund = sum((pet.get(f"skill_{sk_id}") or 0) * sk["cost"] for sk_id, sk in PET_SKILLS.items())
            if refund <= 0:
                await cb.answer("Нечего сбрасывать!", show_alert=True); return
            cur = await db.execute(
                "UPDATE users SET crystals=crystals-? WHERE user_id=? AND crystals>=?",
                (SKILL_RESET_COST, uid, SKILL_RESET_COST),
            )
            if cur.rowcount == 0:
                await db.rollback()
                user = await get_user(uid)
                await cb.answer(
                    f"❌ Нужно {SKILL_RESET_COST}💎, у тебя {user.get('crystals', 0) if user else 0}💎",
                    show_alert=True,
                )
                return
            updates = {"skill_points": (pet.get("skill_points") or 0) + refund}
            for sk_id in PET_SKILLS:
                updates[f"skill_{sk_id}"] = 0
            sets = ", ".join(f"{k}=?" for k in updates)
            await db.execute(
                f"UPDATE pets SET {sets},last_updated=? WHERE pet_id=? AND owner_id=?",
                (*updates.values(), datetime.now().isoformat(), pet["pet_id"], uid),
            )
            await db.commit()
    pet2 = await get_active_pet(uid)
    user2 = await get_user(uid)
    pd = PETS.get(pet2['pet_type'], PETS['dog'])
    skills_txt = "\n".join(
        f"  {sk['name']}: {pet2.get(f'skill_{sk_id}',0) or 0}/{sk['max']}"
        for sk_id, sk in PET_SKILLS.items()
    )
    b = InlineKeyboardBuilder()
    for sk_id, sk in PET_SKILLS.items():
        val = pet2.get(f'skill_{sk_id}', 0) or 0
        if val < sk['max']:
            b.button(text=f"{sk['name']} ({val}/{sk['max']}) — {sk['cost']}🔮", callback_data=f"skill:up:{sk_id}:{uid}")
        else:
            b.button(text=f"{sk['name']} МАКС ✅", callback_data=f"skill:maxed:{uid}")
    b.adjust(1)
    await safe_edit(cb.message,
        f"♻️ <b>Скиллы сброшены!</b>\n\n"
        f"{pd['emoji']} <b>{pet2['name']}</b>\n"
        f"🔮 Возвращено очков: <b>{refund}</b>\n"
        f"💎 Потрачено: <b>{SKILL_RESET_COST}</b> (осталось {user2.get('crystals', 0)}💎)\n\n"
        f"<b>Текущие скиллы:</b>\n{skills_txt}\n\n"
        f"Выбери скилл для прокачки:",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer("Скиллы сброшены!")

@router.callback_query(F.data.startswith("skill:reset:cancel:"))
async def cb_skill_reset_cancel(cb: CallbackQuery):
    uid = int(cb.data.split(":")[-1])
    if cb.from_user.id != uid:
        await cb.answer("Это не твой питомец!", show_alert=True); return
    pet = await get_active_pet(uid)
    if not pet:
        await cb.answer("Нет активного питомца!", show_alert=True); return
    sp = pet.get('skill_points', 0) or 0
    b = InlineKeyboardBuilder()
    for sk_id, sk in PET_SKILLS.items():
        cur = pet.get(f'skill_{sk_id}', 0) or 0
        if cur < sk['max']:
            b.button(text=f"{sk['name']} ({cur}/{sk['max']}) — {sk['cost']}🔮", callback_data=f"skill:up:{sk_id}:{uid}")
        else:
            b.button(text=f"{sk['name']} МАКС ✅", callback_data=f"skill:maxed:{uid}")
    spent_points = sum((pet.get(f"skill_{sk_id}") or 0) * sk["cost"] for sk_id, sk in PET_SKILLS.items())
    if spent_points > 0:
        b.button(text=f"♻️ Сбросить скиллы ({SKILL_RESET_COST}💎)", callback_data=f"skill:reset:{uid}")
    b.adjust(1)
    pd = PETS.get(pet['pet_type'], PETS['dog'])
    skills_txt = "\n".join(
        f"  {sk['name']}: {pet.get(f'skill_{sk_id}',0) or 0}/{sk['max']}"
        for sk_id, sk in PET_SKILLS.items()
    )
    await safe_edit(cb.message,
        f"💡 <b>Скиллы питомца</b>\n\n"
        f"{pd['emoji']} <b>{pet['name']}</b> ур.{pet['level']}\n"
        f"🔮 Очков скиллов: <b>{sp}</b>\n\n"
        f"<b>Текущие скиллы:</b>\n{skills_txt}\n\n"
        f"Выбери скилл для прокачки:",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("skill:reset:"))
async def cb_skill_reset(cb: CallbackQuery):
    uid = int(cb.data.split(":")[-1])
    if cb.from_user.id != uid:
        await cb.answer("Это не твой питомец!", show_alert=True); return
    pet = await get_active_pet(uid)
    if not pet:
        await cb.answer("Нет активного питомца!", show_alert=True); return
    refund = sum((pet.get(f"skill_{sk_id}") or 0) * sk["cost"] for sk_id, sk in PET_SKILLS.items())
    if refund <= 0:
        await cb.answer("Нечего сбрасывать!", show_alert=True); return
    user = await get_user(uid)
    pd = PETS.get(pet['pet_type'], PETS['dog'])
    b = InlineKeyboardBuilder()
    b.button(text=f"✅ Сбросить за {SKILL_RESET_COST}💎", callback_data=f"skill:reset:do:{uid}")
    b.button(text="❌ Отмена", callback_data=f"skill:reset:cancel:{uid}")
    b.adjust(1)
    await safe_edit(cb.message,
        f"♻️ <b>Сброс скиллов</b>\n\n"
        f"{pd['emoji']} <b>{pet['name']}</b>\n"
        f"Будет возвращено: <b>{refund}🔮</b>\n"
        f"Стоимость сброса: <b>{SKILL_RESET_COST}💎</b>\n"
        f"У тебя кристаллов: <b>{user.get('crystals', 0)}💎</b>\n\n"
        f"Все скиллы питомца станут 0, очки вернутся.",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("skill:maxed:"))
async def cb_skill_maxed(cb): await cb.answer("Этот скилл уже на максимуме!", show_alert=True)


# ══════════════════════════════════════════════════════════════════════
# v7.0 — СЕЗОННЫЙ ПРОПУСК
# ══════════════════════════════════════════════════════════════════════

async def h_season(msg):
    uid = msg.from_user.id
    user = await get_user(uid)
    tokens = user.get('season_tokens', 0) or 0
    level  = user.get('season_level',  0) or 0
    prem   = user.get('season_pass',   0) or 0
    progress_to_next = SEASON_TOKENS_PER_LEVEL - (tokens % SEASON_TOKENS_PER_LEVEL) if level < 30 else 0
    bar_filled = min(10, (tokens % SEASON_TOKENS_PER_LEVEL) * 10 // SEASON_TOKENS_PER_LEVEL)
    bar = "█" * bar_filled + "░" * (10 - bar_filled)
    end_date = datetime.strptime(SEASON_END, "%Y-%m-%d")
    days_left = max(0, (end_date - datetime.now()).days)
    end_txt = f"📅 До конца сезона: <b>{days_left} дн.</b> (до {end_date.strftime('%d.%m.%Y')})"

    def fmt_reward(rw):
        t, v = rw
        if t == "coins": return f"{v:,}💰"
        if t == "crystals": return f"{v}💎"
        if t == "tokens": return f"{v}🪙"
        if t == "pet": return f"{PETS.get(v,{}).get('emoji','🐾')} {PETS.get(v,{}).get('name',v)}"
        if t == "item":
            it = ITEMS.get(v) or WEAPONS.get(v) or ARMOR.get(v) or EGG_TYPES.get(v) or {}
            return f"{it.get('emoji','🎁')} {it.get('name',v)}"
        return str(v)

    reward_rows = []
    for lv in range(max(1, level), min(31, level + 4)):
        r = SEASON_PASS.get(lv, {})
        free_txt = fmt_reward(r.get("free", ("?", "?")))
        prem_txt = fmt_reward(r.get("premium", ("?", "?")))
        check = "✅" if lv <= level else f"Ур.{lv}"
        reward_rows.append((check, free_txt, prem_txt))

    prem_txt = "👑 <b>ПРЕМИУМ АКТИВЕН</b>" if prem else f"💎 Премиум: {SEASON_PREMIUM_COST} кристаллов"
    b = InlineKeyboardBuilder()
    if not prem:
        b.button(text=f"👑 Купить Премиум ({SEASON_PREMIUM_COST}💎)", callback_data=f"season:buyprem:{uid}")
    b.button(text="🎁 Забрать награды", callback_data=f"season:claim:{uid}")
    b.adjust(1)

    plain = (
        f"🎫 <b>{SEASON_NAME}</b>\n{end_txt}\n{'─'*20}\n"
        f"📊 Уровень: <b>{level}/30</b>\n"
        f"🪙 Токены: <b>{tokens}</b> [{bar}]\n"
        f"{'⬆️ До след. уровня: ' + str(progress_to_next) + ' токенов' if level < 30 else '🏆 Максимальный уровень!'}\n"
        f"{prem_txt}\n{'─'*20}\n<b>Ближайшие награды:</b>\n"
        + "\n".join(f"<b>{check}</b> Бесплатно: {free}  |  💎 {prem_r}" for check, free, prem_r in reward_rows)
        + "\n\n🪙 Токены начисляются за битвы, работу, приключения и ежедневные задания!"
    )
    rich = (
        f"<h1>🎫 {SEASON_NAME}</h1>"
        f"<p>{end_txt}</p>"
        f"<p>📊 Уровень: <b>{level}/30</b><br/>"
        f"🪙 Токены: <b>{tokens}</b> [{bar}]<br/>"
        f"{'⬆️ До след. уровня: ' + str(progress_to_next) + ' токенов' if level < 30 else '🏆 Максимальный уровень!'}<br/>"
        f"{prem_txt}</p>"
        f"<p><b>Ближайшие награды:</b></p><ul>"
        + "".join(f"<li><b>{check}</b> Бесплатно: {free} | 💎 {prem_r}</li>" for check, free, prem_r in reward_rows)
        + "</ul>"
        f"<p>🪙 Токены начисляются за битвы, работу, приключения и ежедневные задания!</p>"
    )
    await send_rich(msg.bot, msg.chat.id, rich, fallback_html=plain, reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("season:buyprem:"))
async def cb_season_buyprem(cb: CallbackQuery):
    uid = int(cb.data.split(":")[-1])
    user = await get_user(uid)
    if user.get('season_pass'): await cb.answer("Уже куплено!", show_alert=True); return
    crystals = user.get('crystals', 0) or 0
    if crystals < SEASON_PREMIUM_COST:
        await cb.answer(f"❌ Нужно {SEASON_PREMIUM_COST}💎, у тебя {crystals}💎", show_alert=True); return
    await update_crystals(uid, -SEASON_PREMIUM_COST)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET season_pass=1 WHERE user_id=?", (uid,))
        await db.commit()
    await cb.answer("👑 Премиум пропуск активирован!", show_alert=True)
    await safe_edit(cb.message,
        f"👑 <b>Премиум пропуск активирован!</b>\n\n"
        f"Теперь ты получаешь Premium-награды на каждом уровне!\n"
        f"Напиши <b>сезон</b> чтобы забрать награды.",
        parse_mode="HTML")

@router.callback_query(F.data.startswith("season:claim:"))
async def cb_season_claim(cb: CallbackQuery):
    uid = int(cb.data.split(":")[-1])
    user = await get_user(uid)
    level  = user.get('season_level', 0) or 0
    prem   = user.get('season_pass', 0) or 0
    if level == 0: await cb.answer("Пока нет уровней для получения!", show_alert=True); return

    claimed_any = False
    results = []
    for lv in range(1, level + 1):
        for track in (["free"] + (["premium"] if prem else [])):
            async with aiosqlite.connect(DB_PATH) as db:
                try:
                    await db.execute("INSERT INTO season_rewards_claimed (user_id,season_level,track) VALUES (?,?,?)", (uid,lv,track))
                    await db.commit()
                except: continue  # уже забрано
            # Выдаём награду
            rtype, rval = SEASON_PASS.get(lv, {}).get(track, ("coins", 0))
            if rtype == "coins":   await update_coins(uid, rval); results.append(f"Ур.{lv} {track}: +{rval}💰")
            elif rtype == "crystals": await update_crystals(uid, rval); results.append(f"Ур.{lv} {track}: +{rval}💎")
            elif rtype == "tokens":   await add_season_tokens(uid, rval); results.append(f"Ур.{lv} {track}: +{rval}🪙")
            elif rtype == "pet":
                p = PETS.get(rval, PETS['dog'])
                await create_pet(uid, p['name'], rval)
                results.append(f"Ур.{lv} {track}: {p['emoji']} {p['name']}")
            elif rtype == "item":
                await add_item(uid, rval)
                it = ITEMS.get(rval) or WEAPONS.get(rval) or ARMOR.get(rval) or EGG_TYPES.get(rval) or {}
                results.append(f"Ур.{lv} {track}: {it.get('emoji','🎁')} {it.get('name',rval)}")
            claimed_any = True
        if lv == 30: await check_and_grant_achievement(uid, "season_30")

    if not claimed_any: await cb.answer("Нечего забирать — всё уже получено!", show_alert=True); return
    txt = "\n".join(results[:15]) + ("\n...и ещё" if len(results) > 15 else "")
    await cb.answer("✅ Награды получены!")
    await safe_edit(cb.message, f"🎁 <b>Получено наград:</b>\n\n{txt}", parse_mode="HTML")


# ══════════════════════════════════════════════════════════════════════
# v7.0 — АЧИВКИ И ТИТУЛЫ
# ══════════════════════════════════════════════════════════════════════

async def h_achievements(msg):
    uid = msg.from_user.id
    user = await get_user(uid)
    earned = {a['ach_type'] for a in await get_user_achievements(uid)}
    active_title = user.get('title')

    lines = []
    for ach_id, ach in ACHIEVEMENTS.items():
        icon = "✅" if ach_id in earned else "🔒"
        lines.append(f"{icon} <b>{ach['name']}</b>\n   {ach['desc']} (+{ach['tokens']}🪙)")

    # Кнопки для смены титула
    b = InlineKeyboardBuilder()
    # Собираем титулы которые есть у игрока
    available_titles = []
    for ach_id in earned:
        ach = ACHIEVEMENTS.get(ach_id, {})
        if "title" in ach:
            available_titles.append(ach["title"])
    available_titles.append("novice")  # базовый всегда доступен
    for t_id in set(available_titles):
        t = TITLES.get(t_id, {})
        mark = " 🟠" if t_id == active_title else ""
        b.button(text=f"{t.get('name','?')}{mark}", callback_data=f"title:set:{t_id}:{uid}")
    b.adjust(2)

    title_line = f"\n🏷️ Активный титул: <b>{TITLES.get(active_title,{}).get('name','нет')}</b>" if active_title else "\n🏷️ Титул не выбран"
    count = len(earned)
    await safe_reply(msg,
        f"🏆 <b>Достижения</b> ({count}/{len(ACHIEVEMENTS)})\n"
        f"{title_line}\n{'─'*20}\n\n" + "\n".join(lines),
        reply_markup=b.as_markup() if available_titles else None,
        parse_mode="HTML")

@router.callback_query(F.data.startswith("title:set:"))
async def cb_title_set(cb: CallbackQuery):
    parts = cb.data.split(":"); t_id = parts[2]; uid = int(parts[3])
    if t_id not in TITLES: await cb.answer("Неверный титул!", show_alert=True); return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET title=? WHERE user_id=?", (t_id, uid))
        await db.commit()
    await cb.answer(f"✅ Титул «{TITLES[t_id]['name']}» установлен!")


# ══════════════════════════════════════════════════════════════════════
# v7.0 — АУКЦИОН ПИТОМЦЕВ
# ══════════════════════════════════════════════════════════════════════

async def get_active_lots():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        now = datetime.now().isoformat()
        # Закрываем просроченные
        async with db.execute("SELECT lot_id, current_buyer, pet_id, current_price FROM auction_lots WHERE status='active' AND ends_at<?", (now,)) as c:
            expired = [dict(r) for r in await c.fetchall()]
        for lot in expired:
            await db.execute("UPDATE auction_lots SET status='ended' WHERE lot_id=?", (lot['lot_id'],))
            if lot['current_buyer']:
                # Передаём питомца победителю
                await db.execute("UPDATE pets SET owner_id=?,active=0,for_sale=0 WHERE pet_id=?",
                                 (lot['current_buyer'], lot['pet_id']))
        if expired: await db.commit()
        async with db.execute(
            "SELECT a.*, p.name as pet_name, p.pet_type, p.level, u.first_name as seller_name "
            "FROM auction_lots a JOIN pets p ON a.pet_id=p.pet_id JOIN users u ON a.seller_id=u.user_id "
            "WHERE a.status='active' ORDER BY a.ends_at ASC LIMIT 20"
        ) as c:
            return [dict(r) for r in await c.fetchall()]

async def h_auction(msg):
    uid = msg.from_user.id
    lots = await get_active_lots()
    b = InlineKeyboardBuilder()
    b.button(text="➕ Выставить питомца", callback_data=f"auc:sell:{uid}")
    b.button(text="📋 Мои лоты",         callback_data=f"auc:mine:{uid}")
    b.adjust(2)
    if not lots:
        await safe_reply(msg,
            "🏪 <b>Аукцион питомцев</b>\n\nПока нет активных лотов.\nБудь первым — выстави питомца!",
            reply_markup=b.as_markup(), parse_mode="HTML"); return
    txt = "🏪 <b>Аукцион питомцев</b>\n\n"
    for lot in lots[:8]:
        pd = PETS.get(lot['pet_type'], PETS['dog'])
        ends = datetime.fromisoformat(lot['ends_at'])
        rem = ends - datetime.now()
        h, m = int(rem.total_seconds()//3600), int((rem.total_seconds()%3600)//60)
        txt += (f"{pd['emoji']} <b>{lot['pet_name']}</b> ур.{lot['level']}\n"
                f"💰 Ставка: <b>{lot['current_price']:,}</b> | ⏰ {h}ч {m}м\n"
                f"🆔 /bid_{lot['lot_id']}\n\n")
        b.button(text=f"{pd['emoji']} {lot['pet_name']} — {lot['current_price']:,}💰",
                 callback_data=f"auc:view:{lot['lot_id']}:{uid}")
    b.button(text="➕ Выставить питомца", callback_data=f"auc:sell:{uid}")
    b.adjust(1)
    await safe_reply(msg, txt, reply_markup=b.as_markup(), parse_mode="HTML")

@router.callback_query(F.data.startswith("auc:view:"))
async def cb_auc_view(cb: CallbackQuery):
    parts = cb.data.split(":"); lot_id = int(parts[2]); uid = int(parts[3])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT a.*, p.name as pet_name, p.pet_type, p.level, u.first_name as seller_name "
            "FROM auction_lots a JOIN pets p ON a.pet_id=p.pet_id JOIN users u ON a.seller_id=u.user_id "
            "WHERE a.lot_id=? AND a.status='active'", (lot_id,)
        ) as c:
            lot = await c.fetchone()
    if not lot: await cb.answer("Лот не найден или завершён!", show_alert=True); return
    lot = dict(lot)
    pd = PETS.get(lot['pet_type'], PETS['dog'])
    ends = datetime.fromisoformat(lot['ends_at'])
    rem = ends - datetime.now()
    h, m = max(0,int(rem.total_seconds()//3600)), max(0,int((rem.total_seconds()%3600)//60))
    min_bid = lot['current_price'] + max(50, lot['current_price']//10)
    b = InlineKeyboardBuilder()
    if lot['seller_id'] != uid:
        b.button(text=f"💰 Ставка {min_bid:,}💰", callback_data=f"auc:bid:{lot_id}:{min_bid}:{uid}")
        b.button(text=f"💰 Своя ставка",          callback_data=f"auc:custombid:{lot_id}:{uid}")
    else:
        b.button(text="❌ Отменить лот", callback_data=f"auc:cancel:{lot_id}:{uid}")
    b.button(text="◀️ Назад", callback_data=f"auc:list:{uid}")
    b.adjust(1)
    buyer_txt = "Нет ставок" if not lot['current_buyer'] else "Есть ставки"
    await safe_edit(cb.message,
        f"🏪 <b>Аукционный лот #{lot_id}</b>\n\n"
        f"{pd['emoji']} <b>{lot['pet_name']}</b> ур.{lot['level']}\n"
        f"📊 Редкость: {RARITY_NAMES.get(pd.get('rarity','common'),'')}\n"
        f"👤 Продавец: {lot['seller_name']}\n"
        f"💰 Текущая ставка: <b>{lot['current_price']:,}💰</b>\n"
        f"📈 Мин. след. ставка: <b>{min_bid:,}💰</b>\n"
        f"🔥 {buyer_txt}\n"
        f"⏰ Осталось: {h}ч {m}м",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("auc:bid:"))
async def cb_auc_bid(cb: CallbackQuery):
    parts = cb.data.split(":"); lot_id = int(parts[2]); bid = int(parts[3]); uid = int(parts[4])
    user = await get_user(uid)
    if user['coins'] < bid:
        await cb.answer(f"❌ Нужно {bid:,}💰, у тебя {user['coins']:,}💰", show_alert=True); return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM auction_lots WHERE lot_id=? AND status='active'", (lot_id,)) as c:
            lot = await c.fetchone()
        if not lot: await cb.answer("Лот завершён!", show_alert=True); return
        lot = dict(lot)
        if lot['seller_id'] == uid: await cb.answer("Нельзя ставить на свой лот!", show_alert=True); return
        if bid <= lot['current_price']: await cb.answer("Ставка слишком мала!", show_alert=True); return
        # Возвращаем монеты предыдущему участнику
        if lot['current_buyer'] and lot['current_buyer'] != uid:
            await db.execute("UPDATE users SET coins=coins+? WHERE user_id=?",
                             (lot['current_price'], lot['current_buyer']))
        # Списываем монеты у нового
        await db.execute("UPDATE users SET coins=coins-? WHERE user_id=?", (bid, uid))
        await db.execute("UPDATE auction_lots SET current_price=?, current_buyer=? WHERE lot_id=?",
                         (bid, uid, lot_id))
        await db.commit()
    await cb.answer(f"✅ Ставка {bid:,}💰 принята!")
    await safe_edit(cb.message,
        f"✅ <b>Ставка принята!</b>\n\n"
        f"💰 Твоя ставка: <b>{bid:,}💰</b>\nЕсли тебя перебьют — монеты вернутся.\n"
        f"Лот #{lot_id}",
        parse_mode="HTML")

@router.callback_query(F.data.startswith("auc:custombid:"))
async def cb_auc_custombid(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":"); lot_id = int(parts[2]); uid = int(parts[3])
    await state.set_state(S.auction_bid)
    await state.update_data(lot_id=lot_id, uid=uid)
    await safe_edit(cb.message, f"💰 Введи сумму своей ставки для лота #{lot_id}:\n\n(Только число)", parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("auc:sell:"))
async def cb_auc_sell(cb: CallbackQuery, state: FSMContext):
    uid = int(cb.data.split(":")[-1])
    pets = await get_all_pets(uid)
    if not pets: await cb.answer("Нет питомцев!", show_alert=True); return
    b = InlineKeyboardBuilder()
    for p in pets[:10]:
        pd = PETS.get(p['pet_type'], PETS['dog'])
        b.button(text=f"{pd['emoji']} {p['name']} ур.{p['level']}",
                 callback_data=f"auc:setpet:{p['pet_id']}:{uid}")
    b.adjust(1)
    await safe_edit(cb.message, "🏪 Выбери питомца для аукциона:", reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("auc:setpet:"))
async def cb_auc_setpet(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":"); pet_id = int(parts[2]); uid = int(parts[3])
    await state.set_state(S.auction_price)
    await state.update_data(auction_pet_id=pet_id, uid=uid)
    await safe_edit(cb.message,
        "💰 Введи <b>стартовую цену</b> аукциона:\n(Минимум 100💰, только число)",
        parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("auc:cancel:"))
async def cb_auc_cancel(cb: CallbackQuery):
    parts = cb.data.split(":"); lot_id = int(parts[2]); uid = int(parts[3])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM auction_lots WHERE lot_id=? AND seller_id=? AND status='active'",
                              (lot_id, uid)) as c:
            lot = await c.fetchone()
        if not lot: await cb.answer("Лот не найден!", show_alert=True); return
        lot = dict(lot)
        if lot['current_buyer']:
            await db.execute("UPDATE users SET coins=coins+? WHERE user_id=?",
                             (lot['current_price'], lot['current_buyer']))
        await db.execute("UPDATE auction_lots SET status='cancelled' WHERE lot_id=?", (lot_id,))
        await db.execute("UPDATE pets SET for_sale=0 WHERE pet_id=?", (lot['pet_id'],))
        await db.commit()
    await cb.answer("✅ Лот отменён, питомец возвращён!")
    await safe_edit(cb.message, "❌ Аукцион отменён. Питомец возвращён тебе.", parse_mode="HTML")

@router.callback_query(F.data.startswith("auc:list:"))
async def cb_auc_list(cb: CallbackQuery):
    uid = int(cb.data.split(":")[-1])
    lots = await get_active_lots()
    b = InlineKeyboardBuilder()
    for lot in lots[:8]:
        pd = PETS.get(lot['pet_type'], PETS['dog'])
        b.button(text=f"{pd['emoji']} {lot['pet_name']} — {lot['current_price']:,}💰",
                 callback_data=f"auc:view:{lot['lot_id']}:{uid}")
    b.button(text="➕ Выставить питомца", callback_data=f"auc:sell:{uid}")
    b.adjust(1)
    await safe_edit(cb.message,
        f"🏪 <b>Аукцион питомцев</b> ({len(lots)} лотов)\n\nВыбери лот:",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("auc:mine:"))
async def cb_auc_mine(cb: CallbackQuery):
    uid = int(cb.data.split(":")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT a.*, p.name as pet_name, p.pet_type FROM auction_lots a "
            "JOIN pets p ON a.pet_id=p.pet_id WHERE a.seller_id=? AND a.status='active'", (uid,)
        ) as c:
            mine = [dict(r) for r in await c.fetchall()]
    if not mine:
        await cb.answer("У тебя нет активных лотов!", show_alert=True); return
    b = InlineKeyboardBuilder()
    for lot in mine:
        pd = PETS.get(lot['pet_type'], PETS['dog'])
        b.button(text=f"❌ Снять {pd['emoji']} {lot['pet_name']}",
                 callback_data=f"auc:cancel:{lot['lot_id']}:{uid}")
    b.adjust(1)
    txt = "\n".join(
        f"{PETS.get(l['pet_type'],PETS['dog'])['emoji']} {l['pet_name']} — {l['current_price']:,}💰"
        for l in mine
    )
    await safe_edit(cb.message, f"📋 <b>Мои лоты:</b>\n\n{txt}", reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()


async def s_auction_bid(msg: Message, state: FSMContext):
    """Ввод произвольной ставки на аукционе."""
    text = msg.text.strip()
    if not text.isdigit():
        await safe_reply(msg, "❌ Введи число!"); return
    bid = int(text)
    data = await state.get_data()
    lot_id = data.get('lot_id'); uid = data.get('uid') or msg.from_user.id
    await state.clear()
    # ── ИСПРАВЛЕНИЕ RACE CONDITION: вся проверка-и-списание теперь внутри
    #    персонального лока пользователя И одной транзакции. Раньше баланс
    #    проверялся отдельным вызовом get_user() ДО открытия транзакции,
    #    что позволяло параллельным ставкам пройти проверку на одних и тех
    #    же "старых" данных. Также добавлена защита от гонки между двумя
    #    РАЗНЫМИ пользователями, ставящими на один лот одновременно —
    #    обновление лота теперь условно на current_price, который мы
    #    прочитали (optimistic locking): если кто-то успел перебить ставку
    #    первым, наш UPDATE не применится и мы вернём списанные монеты.
    async with get_user_lock(uid):
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM auction_lots WHERE lot_id=? AND status='active'", (lot_id,)) as c:
                lot = await c.fetchone()
            if not lot: await safe_reply(msg, "❌ Лот завершён!"); return
            lot = dict(lot)
            if lot['seller_id'] == uid: await safe_reply(msg, "❌ Нельзя ставить на свой лот!"); return
            min_bid = lot['current_price'] + max(50, lot['current_price']//10)
            if bid < min_bid:
                await safe_reply(msg, f"❌ Минимальная ставка: {min_bid:,}💰"); return

            async with db.execute("SELECT coins FROM users WHERE user_id=?", (uid,)) as c:
                bal_row = await c.fetchone()
            if not bal_row or bal_row[0] < bid:
                await safe_reply(msg, f"❌ Нужно {bid:,}💰, у тебя {bal_row[0] if bal_row else 0:,}💰"); return

            # Атомарно перебиваем ставку только если текущая цена лота не изменилась
            cur = await db.execute(
                "UPDATE auction_lots SET current_price=?, current_buyer=? "
                "WHERE lot_id=? AND status='active' AND current_price=? AND IFNULL(current_buyer,0)=IFNULL(?,0)",
                (bid, uid, lot_id, lot['current_price'], lot['current_buyer'])
            )
            if cur.rowcount == 0:
                await db.commit()
                await safe_reply(msg, "❌ Кто-то уже перебил эту ставку, попробуй снова!"); return

            # Списываем монеты у нового лидера и возвращаем предыдущему лидеру
            await db.execute("UPDATE users SET coins=coins-? WHERE user_id=?", (bid, uid))
            if lot['current_buyer'] and lot['current_buyer'] != uid:
                await db.execute("UPDATE users SET coins=coins+? WHERE user_id=?",
                                 (lot['current_price'], lot['current_buyer']))
            await db.commit()
    u2 = await get_user(uid)
    await safe_reply(msg,
        f"✅ <b>Ставка {bid:,}💰 принята!</b>\n"
        f"Лот #{lot_id}\n💰 Остаток: {u2['coins']:,}💰",
        parse_mode="HTML")

async def s_auction_price(msg: Message, state: FSMContext):
    """Ввод стартовой цены при выставлении на аукцион."""
    text = msg.text.strip()
    if not text.isdigit() or int(text) < 100:
        await safe_reply(msg, "❌ Минимальная цена 100💰. Введи число!"); return
    price = int(text)
    data = await state.get_data()
    pet_id = data.get('auction_pet_id'); uid = data.get('uid') or msg.from_user.id
    await state.clear()
    pet = await get_pet_by_id(pet_id, uid)
    if not pet: await safe_reply(msg, "❌ Питомец не найден!"); return
    ends_at = (datetime.now() + timedelta(hours=24)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE pets SET for_sale=1 WHERE pet_id=?", (pet_id,))
        await db.execute(
            "INSERT INTO auction_lots (seller_id, pet_id, start_price, current_price, ends_at) VALUES (?,?,?,?,?)",
            (uid, pet_id, price, price, ends_at)
        )
        await db.commit()
    pd = PETS.get(pet['pet_type'], PETS['dog'])
    await safe_reply(msg,
        f"✅ <b>{pd['emoji']} {pet['name']}</b> выставлен на аукцион!\n\n"
        f"💰 Стартовая цена: {price:,}💰\n⏰ Аукцион длится 24 часа\n\n"
        f"Напиши <b>аукцион</b> чтобы посмотреть",
        parse_mode="HTML")


# ── ТОП КРИСТАЛЛЫ / РЕБЁРТЫ ──────────────────────────────────────────

async def h_top_crystals(msg):
    users = await get_top_crystals(10)
    medals = ["🥇","🥈","🥉"]
    txt = "💎 <b>Топ по кристаллам:</b>\n─────────────\n"
    for i, u in enumerate(users):
        m = medals[i] if i < 3 else f"{i+1}."
        n = u.get('first_name') or u.get('username') or f"ID:{u['user_id']}"
        txt += f"{m} <b>{n}</b> — {u.get('crystals',0)}💎\n"
    await safe_reply(msg, txt, reply_markup=top_kb(msg.from_user.id), parse_mode="HTML")

async def h_top_rebirths(msg):
    users = await get_top_rebirths(10)
    medals = ["🥇","🥈","🥉"]
    txt = "🔥 <b>Топ по ребёртам:</b>\n─────────────\n"
    for i, u in enumerate(users):
        m = medals[i] if i < 3 else f"{i+1}."
        n = u.get('first_name') or u.get('username') or f"ID:{u['user_id']}"
        bonus = u.get('rebirth_bonus') or 1.0
        txt += f"{m} <b>{n}</b> — {u.get('rebirths',0)}🔥 (×{bonus:.1f})\n"
    await safe_reply(msg, txt, reply_markup=top_kb(msg.from_user.id), parse_mode="HTML")

async def h_world_info(msg):
    txt = "🌍 <b>Множители всех миров:</b>\n─────────────\n"
    for wid, w in WORLDS.items():
        mult = WORLD_MULTIPLIERS.get(wid, 1)
        txt += f"Мир {wid} {w['name']} — ×{mult}\n"
    await safe_reply(msg, txt, parse_mode="HTML")


# ── НОВЫЕ ТОП КОЛБЭКИ ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("top:crystals:"))
async def cb_top_crystals(cb):
    uid = int(cb.data.split(":")[-1])
    users = await get_top_crystals(10); medals = ["🥇","🥈","🥉"]
    txt = "💎 <b>Топ по кристаллам:</b>\n─────────────\n"
    for i, u in enumerate(users):
        m = medals[i] if i < 3 else f"{i+1}."
        n = u.get('first_name') or u.get('username') or f"ID:{u['user_id']}"
        txt += f"{m} <b>{n}</b> — {u.get('crystals',0)}💎\n"
    await safe_edit(cb.message, txt, reply_markup=top_kb(uid), parse_mode="HTML"); await cb.answer()

@router.callback_query(F.data.startswith("top:rebirths:"))
async def cb_top_rebirths(cb):
    uid = int(cb.data.split(":")[-1])
    users = await get_top_rebirths(10); medals = ["🥇","🥈","🥉"]
    txt = "🔥 <b>Топ по ребёртам:</b>\n─────────────\n"
    for i, u in enumerate(users):
        m = medals[i] if i < 3 else f"{i+1}."
        n = u.get('first_name') or u.get('username') or f"ID:{u['user_id']}"
        bonus = u.get('rebirth_bonus') or 1.0
        txt += f"{m} <b>{n}</b> — {u.get('rebirths',0)}🔥 (×{bonus:.1f})\n"
    await safe_edit(cb.message, txt, reply_markup=top_kb(uid), parse_mode="HTML"); await cb.answer()


# ── ПОМОЩЬ ────────────────────────────────────────────────────────────


# ── АДМИН ПАНЕЛЬ ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm:stats:"))
async def cb_adm_stats(cb):
    uid=int(cb.data.split(":")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c: uc=(await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE is_banned=1") as c: bc=(await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM pets") as c: pc=(await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM clans") as c: cc=(await c.fetchone())[0]
        async with db.execute("SELECT SUM(coins) FROM users") as c: tc=(await c.fetchone())[0] or 0
        async with db.execute("SELECT COUNT(*) FROM trade_offers WHERE status='active'") as c: to_cnt=(await c.fetchone())[0]
    ev=await get_active_event(); evt=f"🎪 {ev['name']}" if ev else "нет"
    await safe_edit(cb.message,
        f"📊 <b>Статистика</b>\n─────────────\n"
        f"👥 {uc} игроков (🚫{bc})\n🐾 {pc} питомцев\n🏰 {cc} кланов\n"
        f"🏪 {to_cnt} на рынке\n💰 {tc} монет\n⏱️ {uptime()}\n🎪 {evt}",
        reply_markup=adm_kb(uid),parse_mode="HTML"); await cb.answer()

@router.callback_query(F.data.startswith("adm:server:"))
async def cb_adm_server(cb):
    uid=int(cb.data.split(":")[-1])
    try:
        import psutil; proc=psutil.Process()
        ram=proc.memory_info().rss/1024/1024; total=psutil.virtual_memory().total/1024/1024
        cpu=psutil.cpu_percent(interval=0.1); ramt=f"🧠 RAM: {ram:.1f}/{total:.0f} MB\n⚡ CPU: {cpu:.1f}%\n"
    except: ramt="🧠 RAM: установи psutil\n"
    await safe_edit(cb.message,f"🖥️ <b>Сервер</b>\n─────────────\n{ramt}⏱️ {uptime()}\n🐍 PythonAnywhere",
                    reply_markup=adm_kb(uid),parse_mode="HTML"); await cb.answer()

@router.callback_query(F.data.startswith("adm:emoji:"))
async def cb_adm_emoji(cb):
    uid = int(cb.data.split(":")[-1])
    if not is_admin(cb.from_user.id):
        await cb.answer("Нет доступа!", show_alert=True)
        return
    await load_premium_emoji(cb.bot, PREMIUM_EMOJI_PACK)
    st = premium_emoji_status()
    await safe_edit(
        cb.message,
        f"🎨 <b>Premium emoji</b>\n\n"
        f"Статус: <b>{'включены' if st['active'] else 'выключены'}</b>\n"
        f"Пак: <code>{st['pack'] or 'не загружен'}</code>\n"
        f"Эмодзи: <b>{st['count']}</b>\n"
        f"Лимит: <b>{st['max']}</b>",
        reply_markup=adm_kb(uid),
        parse_mode="HTML",
    )
    await cb.answer("Обновлено")

@router.callback_query(F.data.startswith("adm:players:"))
async def cb_adm_players(cb):
    uid=int(cb.data.split(":")[-1]); users=await get_all_users(); b=InlineKeyboardBuilder()
    for u in users[:20]:
        n=u.get('first_name') or u.get('username') or str(u['user_id'])
        vip_mark="👑" if u.get('vip_until') and datetime.fromisoformat(u['vip_until'])>datetime.now() else ""
        b.button(text=f"{'🚫' if u.get('is_banned') else '✅'}{vip_mark}{n} {u['coins']}💰",
                 callback_data=f"adm:p:{u['user_id']}:{uid}")
    b.button(text="◀️ Назад",callback_data=f"adm:back:{uid}"); b.adjust(1)
    await safe_edit(cb.message,f"👥 <b>Игроки</b> ({len(users)}):",reply_markup=b.as_markup(),parse_mode="HTML"); await cb.answer()

@router.callback_query(F.data.startswith("adm:p:"))
async def cb_adm_p(cb):
    parts=cb.data.split(":"); target_uid=int(parts[2]); caller_uid=int(parts[3])
    user=await get_user(target_uid); pets=await get_all_pets(target_uid)
    if not user: await cb.answer("Не найден!",show_alert=True); return
    n=user.get('first_name') or user.get('username') or str(target_uid)
    wins=sum(p.get('wins',0) for p in pets); clan=await get_clan_by_user(target_uid)
    clan_info=f"\n🏰 {clan['name']}" if clan else ""
    await safe_edit(cb.message,
        f"👤 <b>{n}</b>\n─────────────\n🆔 {target_uid}\n💰 {user['coins']}\n"
        f"🐾 {len(pets)} питомцев\n⚔️ {wins} побед{clan_info}\n"
        f"{'🚫 Заблокирован' if user.get('is_banned') else '✅ Активен'}",
        reply_markup=adm_player_kb(target_uid,caller_uid,user.get('is_banned',0)),parse_mode="HTML"); await cb.answer()

@router.callback_query(F.data.startswith("adm:ban:"))
async def cb_adm_ban(cb):
    parts=cb.data.split(":"); target_uid=int(parts[2]); caller_uid=int(parts[3])
    await ban_user(target_uid); await cb.answer("🚫 Заблокирован!")
    await cb_adm_p(cb)

@router.callback_query(F.data.startswith("adm:unban:"))
async def cb_adm_unban(cb):
    parts=cb.data.split(":"); target_uid=int(parts[2]); caller_uid=int(parts[3])
    await unban_user(target_uid); await cb.answer("✅ Разблокирован!")
    await cb_adm_p(cb)

@router.callback_query(F.data.startswith("adm:reset:"))
async def cb_adm_reset(cb):
    parts=cb.data.split(":"); target_uid=int(parts[2]); caller_uid=int(parts[3])
    await delete_user_data(target_uid); await cb.answer("🗑️ Сброшено!")
    await cb_adm_p(cb)

@router.callback_query(F.data.startswith("adm:gc:"))
async def cb_adm_gc(cb: CallbackQuery, state: FSMContext):
    parts=cb.data.split(":"); target_uid=int(parts[2]); caller_uid=int(parts[3])
    await state.update_data(target_uid=target_uid); await state.set_state(S.adm_gc_amt)
    await safe_edit(cb.message,f"💰 Сколько монет игроку {target_uid}? (минус — забрать)"); await cb.answer()

async def s_adm_gc_amt(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if not msg.text.lstrip("-").isdigit(): await safe_reply(msg,"❌ Число!"); return
    data=await state.get_data(); await state.clear()
    await update_coins(data['target_uid'],int(msg.text))
    u=await get_user(data['target_uid']); await safe_reply(msg,f"✅ {msg.text}💰 → {data['target_uid']}. Теперь: {u['coins']}💰")

@router.callback_query(F.data.startswith("adm:find:"))
async def cb_adm_find(cb: CallbackQuery, state: FSMContext):
    uid=int(cb.data.split(":")[-1]); await state.set_state(S.adm_find); await state.update_data(caller_uid=uid)
    await safe_edit(cb.message,"🔍 @username или ID:"); await cb.answer()

async def s_adm_find(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    data=await state.get_data(); caller_uid=data.get('caller_uid',msg.from_user.id)
    text=msg.text.strip(); await state.clear()
    u=await get_user(int(text)) if text.isdigit() else await find_user_by_username(text)
    if not u: await safe_reply(msg,"❌ Не найден!"); return
    target_uid=u['user_id']; pets=await get_all_pets(target_uid)
    n=u.get('first_name') or u.get('username') or str(target_uid); wins=sum(p.get('wins',0) for p in pets)
    await safe_reply(msg,
        f"👤 <b>{n}</b>\n─────────────\n🆔 {target_uid}\n💰 {u['coins']}\n"
        f"🐾 {len(pets)} питомцев\n⚔️ {wins} побед\n{'🚫 Заблокирован' if u.get('is_banned') else '✅ Активен'}",
        reply_markup=adm_player_kb(target_uid,caller_uid,u.get('is_banned',0)),parse_mode="HTML")

@router.callback_query(F.data.startswith("adm:coins:"))
async def cb_adm_coins(cb: CallbackQuery, state: FSMContext):
    uid=int(cb.data.split(":")[-1]); await state.set_state(S.adm_coins_id); await state.update_data(caller_uid=uid)
    await safe_edit(cb.message,"💰 Кому? @username или ID:"); await cb.answer()

async def s_adm_coins_id(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    text=msg.text.strip(); u=await get_user(int(text)) if text.isdigit() else await find_user_by_username(text)
    if not u: await safe_reply(msg,"❌ Не найден!"); return
    await state.update_data(target_uid=u['user_id']); await state.set_state(S.adm_coins_amt)
    await safe_reply(msg,"Сколько? (минус — забрать)")

async def s_adm_coins_amt(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if not msg.text.lstrip("-").isdigit(): await safe_reply(msg,"❌ Число!"); return
    data=await state.get_data(); await state.clear()
    await update_coins(data['target_uid'],int(msg.text))
    u=await get_user(data['target_uid']); await safe_reply(msg,f"✅ {msg.text}💰. Теперь: {u['coins']}💰")

@router.callback_query(F.data.startswith("adm:egg:"))
async def cb_adm_egg(cb: CallbackQuery):
    uid=int(cb.data.split(":")[-1]); b=InlineKeyboardBuilder()
    for eid,egg in EGG_TYPES.items(): b.button(text=f"{egg['emoji']} {egg['name']}",callback_data=f"adm:et:{eid}:{uid}")
    b.adjust(1); await safe_edit(cb.message,"🥚 Какое яйцо?",reply_markup=b.as_markup()); await cb.answer()

@router.callback_query(F.data.startswith("adm:et:"))
async def cb_adm_et(cb: CallbackQuery, state: FSMContext):
    parts=cb.data.split(":"); eid=parts[2]; uid=int(parts[3])
    await state.update_data(egg_id=eid); await state.set_state(S.adm_egg_id)
    await safe_edit(cb.message,"Кому? @username или ID:"); await cb.answer()

async def s_adm_egg_id(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    data=await state.get_data()
    if 'egg_id' not in data: await state.clear(); return
    text=msg.text.strip(); u=await get_user(int(text)) if text.isdigit() else await find_user_by_username(text)
    if not u: await safe_reply(msg,"❌ Не найден!"); return
    await add_item(u['user_id'],data['egg_id']); await state.clear()
    egg=EGG_TYPES[data['egg_id']]; await safe_reply(msg,f"✅ {egg['emoji']} {egg['name']} → {u.get('first_name','?')}!")

@router.callback_query(F.data.startswith("adm:event:"))
async def cb_adm_event(cb: CallbackQuery):
    uid=int(cb.data.split(":")[-1]); b=InlineKeyboardBuilder()
    for ev in EVENT_POOL: b.button(text=f"{ev['name']} ({ev['duration_h']}ч)",callback_data=f"adm:ev:{ev['id']}:{uid}")
    b.button(text="◀️ Назад",callback_data=f"adm:back:{uid}"); b.adjust(1)
    await safe_edit(cb.message,"🎪 Выбери ивент:",reply_markup=b.as_markup()); await cb.answer()

@router.callback_query(F.data.startswith("adm:ev:"))
async def cb_adm_ev(cb: CallbackQuery):
    parts=cb.data.split(":"); eid=parts[2]; uid=int(parts[3])
    ev=next((e for e in EVENT_POOL if e['id']==eid),None)
    if not ev: await cb.answer("Не найден!",show_alert=True); return
    await start_event(ev); users=await get_all_users(); sent=0
    for u in users:
        try:
            await cb.message.bot.send_message(u['user_id'],
                f"🎪 <b>НОВЫЙ ИВЕНТ!</b>\n\n{ev['name']}\n{ev['desc']}\n\n"
                f"⏰ {ev['duration_h']}ч  ✨ ×{ev.get('mult_xp',1.0):.1f}  💰 ×{ev.get('mult_coins',1.0):.1f}",
                parse_mode="HTML"); sent+=1; await asyncio.sleep(0.05)
        except: pass
    await safe_edit(cb.message,f"✅ Ивент запущен!\n📢 {sent}",reply_markup=adm_kb(uid)); await cb.answer()

@router.callback_query(F.data.startswith("adm:tournament:"))
async def cb_adm_tournament(cb: CallbackQuery, state: FSMContext):
    await state.set_state(S.adm_tour_name)
    await safe_edit(cb.message,"🏆 Введи название турнира:"); await cb.answer()

async def s_adm_tour_name(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.update_data(tour_name=msg.text.strip()); await state.set_state(S.adm_tour_prize)
    await safe_reply(msg,"Введи приз (монеты):")

async def s_adm_tour_prize(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if not msg.text.isdigit(): await safe_reply(msg,"❌ Число!"); return
    data=await state.get_data(); await state.clear()
    tid=await create_tournament(data['tour_name'],int(msg.text))
    users=await get_all_users(); sent=0
    for u in users:
        try:
            await msg.bot.send_message(u['user_id'],
                f"🏆 <b>НОВЫЙ ТУРНИР!</b>\n\n<b>{data['tour_name']}</b>\n💰 Приз: {msg.text} монет!\n\n"
                f"Напиши <b>турнир</b> чтобы участвовать!",parse_mode="HTML"); sent+=1; await asyncio.sleep(0.05)
        except: pass
    await safe_reply(msg,f"✅ Турнир создан! ID:{tid}\n📢 {sent} уведомлений")

@router.callback_query(F.data.startswith("adm:broadcast:"))
async def cb_adm_broadcast(cb: CallbackQuery, state: FSMContext):
    await state.set_state(S.adm_broadcast)
    await safe_edit(cb.message,"📢 Текст рассылки:"); await cb.answer()

async def s_adm_broadcast(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.clear(); users=await get_all_users(); sent=0; failed=0
    sm=await safe_reply(msg,f"📢 Рассылка {len(users)} игрокам...")
    for u in users:
        try:
            await msg.bot.send_message(u['user_id'],f"📢 <b>От администратора:</b>\n\n{msg.text}",parse_mode="HTML")
            sent+=1; await asyncio.sleep(0.05)
        except: failed+=1
    if sm:
        try: await sm.edit_text(f"📢 Готово!\n✅ {sent}  ❌ {failed}")
        except: pass

@router.callback_query(F.data.startswith("adm:back:"))
async def cb_adm_back(cb):
    uid=int(cb.data.split(":")[-1])
    await safe_edit(cb.message,"🔧 <b>Панель администратора</b>",reply_markup=adm_kb(uid),parse_mode="HTML"); await cb.answer()

# ── ПРОМОКОД (ПОЛЬЗОВАТЕЛЬ) ───────────────────────────────────────────

class PromoState(StatesGroup):
    waiting = State()

async def h_promo(msg, state):
    await state.set_state(PromoState.waiting)
    await safe_reply(msg,"🎟️ Введи промокод:")


# Обрабатываем ввод промокода в PromoState
@router.message(PromoState.waiting)
async def s_promo_input(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    code = msg.text.strip()
    ok, result = await use_promo(uid, code)
    if not ok:
        await safe_reply(msg, result); return
    promo = result
    parts = []
    if promo['reward_coins']:
        await update_coins(uid, promo['reward_coins'])
        parts.append(f"💰 +{promo['reward_coins']} монет")
    if promo['reward_pet']:
        pt = promo['reward_pet']
        p = PETS.get(pt)
        if p:
            await create_pet(uid, p['name'], pt)
            parts.append(f"{p['emoji']} Питомец {p['name']} добавлен! Напиши <b>питомцы</b>")
    u2 = await get_user(uid)
    await safe_reply(msg, f"✅ Промокод активирован!\n" + "\n".join(parts) + f"\n💰 Баланс: {u2['coins']}", parse_mode="HTML")

# ── НОВЫЕ ADMIN HANDLERS ───────────────────────────────────────────────

# Обновлённая клавиатура теперь с новыми кнопками
def adm_kb(uid):
    b=InlineKeyboardBuilder()
    b.button(text="📊 Статистика",       callback_data=f"adm:stats:{uid}")
    b.button(text="🖥️ Сервер",           callback_data=f"adm:server:{uid}")
    b.button(text="👥 Игроки",           callback_data=f"adm:players:{uid}")
    b.button(text="🔍 Найти",            callback_data=f"adm:find:{uid}")
    b.button(text="💰 Выдать монеты",    callback_data=f"adm:coins:{uid}")
    b.button(text="💎 Выдать кристаллы", callback_data=f"adm:crystals:{uid}")
    b.button(text="🥚 Выдать яйцо",     callback_data=f"adm:egg:{uid}")
    b.button(text="🐾 Выдать питомца",   callback_data=f"adm:givepet:{uid}")
    b.button(text="🗑️ Забрать питомца",  callback_data=f"adm:takepet:{uid}")
    b.button(text="🎟️ Промокоды",        callback_data=f"adm:promos:{uid}")
    b.button(text="🎪 Запустить ивент",  callback_data=f"adm:event:{uid}")
    b.button(text="👹 Спавн босса",      callback_data=f"adm:boss_spawn:{uid}")
    b.button(text="🏆 Создать турнир",   callback_data=f"adm:tournament:{uid}")
    b.button(text="📢 Рассылка",         callback_data=f"adm:broadcast:{uid}")
    b.button(text="🎨 Premium emoji",    callback_data=f"adm:emoji:{uid}")
    b.adjust(2); return b.as_markup()

# ── ВЫДАЧА КРИСТАЛЛОВ (АДМИН) ─────────────────────────────────────────

@router.callback_query(F.data.startswith("adm:crystals:"))
async def cb_adm_crystals(cb: CallbackQuery, state: FSMContext):
    uid = int(cb.data.split(":")[-1])
    await state.update_data(caller_uid=uid)
    await state.set_state(S.adm_crystals_id)
    await safe_edit(cb.message, "💎 Кому выдать кристаллы? @username или ID:")
    await cb.answer()

async def s_adm_crystals_id(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    text = msg.text.strip()
    u = await get_user(int(text)) if text.isdigit() else await find_user_by_username(text)
    if not u: await safe_reply(msg, "❌ Не найден!"); return
    await state.update_data(target_uid=u['user_id'])
    await state.set_state(S.adm_crystals_amt)
    await safe_reply(msg, f"💎 Сколько кристаллов выдать <b>{u.get('first_name','?')}</b>? (минус — забрать)", parse_mode="HTML")

async def s_adm_crystals_amt(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if not msg.text.lstrip("-").isdigit(): await safe_reply(msg, "❌ Введи число!"); return
    data = await state.get_data(); await state.clear()
    amount = int(msg.text)
    await update_crystals(data['target_uid'], amount)
    u = await get_user(data['target_uid'])
    await safe_reply(msg,
        f"✅ {'Выдано' if amount >= 0 else 'Снято'} {abs(amount)}💎 → {u.get('first_name','?')}\n"
        f"Теперь у игрока: {u.get('crystals', 0)}💎")

# ── СПАВН БОССА (АДМИН) ───────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm:boss_spawn:"))
async def cb_adm_boss_spawn(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): await cb.answer("❌ Нет доступа!", show_alert=True); return
    uid = int(cb.data.split(":")[-1])
    global _active_boss
    # Показываем меню выбора босса
    b = InlineKeyboardBuilder()
    for i, boss in enumerate(GLOBAL_BOSS_POOL):
        b.button(text=f"{boss['name']} ({boss['hp']:,} HP)",
                 callback_data=f"adm:boss_do:{i}:{uid}")
    if _active_boss:
        b.button(text="💀 Убить текущего босса", callback_data=f"adm:boss_kill:{uid}")
    b.button(text="◀️ Назад", callback_data=f"adm:back:{uid}")
    b.adjust(1)
    boss_status = f"👹 Активен: {_active_boss['data']['name']} ({_active_boss['hp']:,} HP)" if _active_boss else "Босса нет"
    await safe_edit(cb.message,
        f"👹 <b>Спавн глобального босса</b>\n\n"
        f"Статус: {boss_status}\n\n"
        f"Выбери босса для спавна:",
        reply_markup=b.as_markup(), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("adm:boss_do:"))
async def cb_adm_boss_do(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): await cb.answer("❌ Нет доступа!", show_alert=True); return
    parts = cb.data.split(":"); boss_idx = int(parts[2]); uid = int(parts[3])
    global _active_boss
    if boss_idx >= len(GLOBAL_BOSS_POOL):
        await cb.answer("Босс не найден!", show_alert=True); return
    bdata = GLOBAL_BOSS_POOL[boss_idx]
    async with get_boss_lock():
        _active_boss = {"data": bdata, "hp": bdata['hp'], "attackers": {}}
    # Уведомляем всех игроков
    users = await get_all_users(); sent = 0
    for u in users:
        try:
            await cb.message.bot.send_message(
                u['user_id'],
                f"👹 <b>ПОЯВИЛСЯ ГЛОБАЛЬНЫЙ БОСС!</b>\n\n"
                f"{bdata['name']}\n"
                f"❤️ {bdata['hp']:,} HP\n"
                f"💎 Награда: {bdata['reward_crystals'][0]}–{bdata['reward_crystals'][1]} кристаллов\n\n"
                f"Напиши <b>босс</b> чтобы атаковать!",
                parse_mode="HTML"
            )
            sent += 1; await asyncio.sleep(0.05)
        except: pass
    await safe_edit(cb.message,
        f"✅ Босс <b>{bdata['name']}</b> заспавнен!\n📢 Уведомлено: {sent} игроков",
        reply_markup=adm_kb(uid), parse_mode="HTML")
    await cb.answer("👹 Босс заспавнен!")

@router.callback_query(F.data.startswith("adm:boss_kill:"))
async def cb_adm_boss_kill(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): await cb.answer("❌ Нет доступа!", show_alert=True); return
    uid = int(cb.data.split(":")[-1])
    global _active_boss
    async with get_boss_lock():
        _active_boss = None
    await cb.answer("💀 Босс убит!")
    await safe_edit(cb.message, "💀 Текущий босс удалён.", reply_markup=adm_kb(uid), parse_mode="HTML")

# ── ПРОМОКОДЫ (АДМИН) ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm:promos:"))
async def cb_adm_promos(cb: CallbackQuery):
    uid=int(cb.data.split(":")[-1]); promos=await get_all_promos()
    b=InlineKeyboardBuilder()
    b.button(text="➕ Создать промокод", callback_data=f"adm:promo_new:{uid}")
    for p in promos[:15]:
        used = p.get('uses_left',0)
        label = f"🎟️ {p['code']} | 💰{p['reward_coins']}"
        if p.get('reward_pet'): label += f" | 🐾{p['reward_pet']}"
        label += f" | x{used}"
        b.button(text=label, callback_data=f"adm:promo_del:{p['code']}:{uid}")
    b.button(text="◀️ Назад", callback_data=f"adm:back:{uid}"); b.adjust(1)
    txt = f"🎟️ <b>Промокоды</b> ({len(promos)})\n\nНажми на промокод чтобы удалить его."
    await safe_edit(cb.message, txt, reply_markup=b.as_markup(), parse_mode="HTML"); await cb.answer()

@router.callback_query(F.data.startswith("adm:promo_del:"))
async def cb_adm_promo_del(cb: CallbackQuery):
    parts=cb.data.split(":"); code=parts[2]; uid=int(parts[3])
    await delete_promo(code); await cb.answer(f"\U0001f5d1\ufe0f {code} удалён!")
    promos=await get_all_promos()
    b=InlineKeyboardBuilder()
    b.button(text="➕ Создать промокод", callback_data=f"adm:promo_new:{uid}")
    for p in promos[:15]:
        used=p.get("uses_left",0)
        label="🎟️ " + p["code"] + " | 💰" + str(p["reward_coins"])
        if p.get("reward_pet"): label += " | 🐾" + p["reward_pet"]
        label += " | x" + str(used)
        b.button(text=label, callback_data="adm:promo_del:" + p["code"] + ":" + str(uid))
    b.button(text="◀️ Назад", callback_data=f"adm:back:{uid}"); b.adjust(1)
    txt = "🎟️ <b>Промокоды</b> (" + str(len(promos)) + ")\n\nНажми на промокод чтобы удалить его."
    await safe_edit(cb.message, txt, reply_markup=b.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("adm:promo_new:"))
async def cb_adm_promo_new(cb: CallbackQuery, state: FSMContext):
    uid=int(cb.data.split(":")[-1]); await state.update_data(caller_uid=uid)
    await state.set_state(S.adm_promo_code)
    await safe_edit(cb.message,"🎟️ Введи код промокода (например PETWORLD2025):"); await cb.answer()

async def s_adm_promo_code(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    code=msg.text.strip().upper()
    if not code: await safe_reply(msg,"❌ Пустой!"); return
    await state.update_data(promo_code=code); await state.set_state(S.adm_promo_coins)
    await safe_reply(msg,f"Код: <b>{code}</b>\n\nСколько монет выдать? (0 = не выдавать):",parse_mode="HTML")

async def s_adm_promo_coins(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if not msg.text.isdigit(): await safe_reply(msg,"❌ Число!"); return
    await state.update_data(promo_coins=int(msg.text)); await state.set_state(S.adm_promo_pet)
    pets_list=", ".join(PETS.keys())
    await safe_reply(msg,f"🐾 Какого питомца выдать? (0 = не выдавать)\n\nДоступные: <code>{pets_list[:300]}</code>",parse_mode="HTML")

async def s_adm_promo_pet(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    val=msg.text.strip()
    pet_type=None
    if val!="0":
        if val not in PETS: await safe_reply(msg,f"❌ Питомец не найден! Напиши 0 или точное ID питомца."); return
        pet_type=val
    await state.update_data(promo_pet=pet_type); await state.set_state(S.adm_promo_uses)
    await safe_reply(msg,"Сколько раз можно использовать? (число):")

async def s_adm_promo_uses(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if not msg.text.isdigit(): await safe_reply(msg,"❌ Число!"); return
    data=await state.get_data(); await state.clear()
    code=data['promo_code']; coins=data['promo_coins']; pet=data.get('promo_pet'); uses=int(msg.text)
    ok=await create_promo(code,coins,pet,uses)
    if not ok: await safe_reply(msg,"❌ Промокод с таким названием уже существует!"); return
    pet_str=f" + 🐾{pet}" if pet else ""
    await safe_reply(msg,f"✅ Промокод <b>{code}</b> создан!\n💰 {coins} монет{pet_str}\n♻️ {uses} использований",parse_mode="HTML")

# ── ВЫДАТЬ ПИТОМЦА (АДМИН) ────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm:givepet:"))
async def cb_adm_givepet(cb: CallbackQuery, state: FSMContext):
    uid=int(cb.data.split(":")[-1]); await state.update_data(caller_uid=uid)
    await state.set_state(S.adm_give_pet_uid)
    await safe_edit(cb.message,"🐾 Кому выдать питомца? @username или ID:"); await cb.answer()

async def s_adm_give_pet_uid(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    text=msg.text.strip(); u=await get_user(int(text)) if text.isdigit() else await find_user_by_username(text)
    if not u: await safe_reply(msg,"❌ Не найден!"); return
    await state.update_data(give_uid=u['user_id']); await state.set_state(S.adm_give_pet_type)
    pets_list="\n".join(f"<code>{k}</code> — {v['emoji']} {v['name']}" for k,v in PETS.items())
    await safe_reply(msg,f"🐾 Какого питомца выдать игроку <b>{u.get('first_name','?')}</b>?\n\n{pets_list}",parse_mode="HTML")

async def s_adm_give_pet_type(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    pt=msg.text.strip()
    if pt not in PETS: await safe_reply(msg,"❌ Питомец не найден!"); return
    data=await state.get_data(); await state.clear()
    uid=data['give_uid']; p=PETS[pt]
    await create_pet(uid, p['name'], pt)
    u=await get_user(uid)
    await safe_reply(msg,f"✅ {p['emoji']} <b>{p['name']}</b> выдан игроку <b>{u.get('first_name','?')}</b>!",parse_mode="HTML")

# ── ЗАБРАТЬ ПИТОМЦА (АДМИН) ───────────────────────────────────────────

@router.callback_query(F.data.startswith("adm:takepet:"))
async def cb_adm_takepet(cb: CallbackQuery, state: FSMContext):
    uid=int(cb.data.split(":")[-1]); await state.update_data(caller_uid=uid)
    await state.set_state(S.adm_take_uid)
    await safe_edit(cb.message,"🗑️ У кого забрать питомца? @username или ID:"); await cb.answer()

async def s_adm_take_uid(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    text=msg.text.strip(); u=await get_user(int(text)) if text.isdigit() else await find_user_by_username(text)
    if not u: await safe_reply(msg,"❌ Не найден!"); return
    pets=await get_all_pets(u['user_id'])
    if not pets: await safe_reply(msg,"У игрока нет питомцев!"); await state.clear(); return
    await state.update_data(take_from_uid=u['user_id']); await state.set_state(S.adm_take_pet)
    txt=f"🐾 Питомцы <b>{u.get('first_name','?')}</b>:\n\n"
    for p in pets:
        pd=PETS.get(p['pet_type'],PETS['dog'])
        txt+=f"🆔 <code>{p['pet_id']}</code> — {pd['emoji']} <b>{p['name']}</b> ур.{p['level']}\n"
    await safe_reply(msg,txt+"\nВведи ID питомца:",parse_mode="HTML")

async def s_adm_take_pet(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if not msg.text.isdigit(): await safe_reply(msg,"❌ ID питомца!"); return
    data=await state.get_data(); await state.clear()
    pid=int(msg.text); pet=await get_pet_by_id(pid, data['take_from_uid'])
    if not pet: await safe_reply(msg,"❌ Питомец не найден у этого игрока!"); return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pets WHERE pet_id=?",(pid,)); await db.commit()
    pd=PETS.get(pet['pet_type'],PETS['dog'])
    u=await get_user(data['take_from_uid'])
    await safe_reply(msg,f"✅ {pd['emoji']} <b>{pet['name']}</b> забран у <b>{u.get('first_name','?')}</b>!",parse_mode="HTML")

# ── ЗАБРАТЬ/ВЫДАТЬ МОНЕТЫ ЧЕРЕЗ PANEL ИГРОКА ─────────────────────────

def adm_player_kb(target_uid, caller_uid, banned):
    b=InlineKeyboardBuilder()
    if banned: b.button(text="✅ Разбанить",callback_data=f"adm:unban:{target_uid}:{caller_uid}")
    else:      b.button(text="🚫 Забанить", callback_data=f"adm:ban:{target_uid}:{caller_uid}")
    b.button(text="💰 Дать монеты",  callback_data=f"adm:gc:{target_uid}:{caller_uid}")
    b.button(text="🗑️ Забрать монеты",callback_data=f"adm:take_coins:{target_uid}:{caller_uid}")
    b.button(text="🐾 Забрать питомца",callback_data=f"adm:takepet_direct:{target_uid}:{caller_uid}")
    b.button(text="🗑️ Сбросить",    callback_data=f"adm:reset:{target_uid}:{caller_uid}")
    b.button(text="◀️ Назад",       callback_data=f"adm:players:{caller_uid}")
    b.adjust(1); return b.as_markup()

@router.callback_query(F.data.startswith("adm:take_coins:"))
async def cb_adm_take_coins(cb: CallbackQuery, state: FSMContext):
    parts=cb.data.split(":"); target_uid=int(parts[2]); caller_uid=int(parts[3])
    await state.update_data(target_uid=target_uid); await state.set_state(S.adm_gc_amt)
    await safe_edit(cb.message,f"💰 Сколько монет забрать у {target_uid}? (введи положительное число):"); await cb.answer()

@router.callback_query(F.data.startswith("adm:takepet_direct:"))
async def cb_adm_takepet_direct(cb: CallbackQuery, state: FSMContext):
    parts=cb.data.split(":"); target_uid=int(parts[2]); caller_uid=int(parts[3])
    pets=await get_all_pets(target_uid)
    if not pets: await cb.answer("Нет питомцев!",show_alert=True); return
    await state.update_data(take_from_uid=target_uid, caller_uid=caller_uid)
    await state.set_state(S.adm_take_pet)
    txt=f"🐾 Питомцы:\n\n"
    for p in pets:
        pd=PETS.get(p['pet_type'],PETS['dog'])
        txt+=f"🆔 <code>{p['pet_id']}</code> — {pd['emoji']} <b>{p['name']}</b> ур.{p['level']}\n"
    await safe_edit(cb.message, txt+"\nВведи ID питомца:", parse_mode="HTML"); await cb.answer()

# ═══════════════════════════════════════════════════
# ЗАПУСК
# ═══════════════════════════════════════════════════


# ═══════════════════════════════════════════════════
# КЛАНОВЫЕ ВОЙНЫ
# ═══════════════════════════════════════════════════
# Система: каждый клан может объявить войну другому клану.
# Участники вносят своих питомцев. Каждые 30 минут
# scheduler проводит автоматические бои между кланами.
# Клан набравший 5 побед — выигрывает войну и получает приз.
# ════════════════════════════════════════════════════

async def init_clan_wars_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS clan_wars (
                war_id INTEGER PRIMARY KEY AUTOINCREMENT,
                clan1_id INTEGER NOT NULL,
                clan2_id INTEGER NOT NULL,
                clan1_wins INTEGER DEFAULT 0,
                clan2_wins INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                prize INTEGER DEFAULT 1000,
                started_at TEXT DEFAULT CURRENT_TIMESTAMP,
                ended_at TEXT
            );
            CREATE TABLE IF NOT EXISTS clan_war_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                war_id INTEGER NOT NULL,
                clan_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                pet_id INTEGER NOT NULL,
                UNIQUE(war_id, user_id)
            );
        """)
        await db.commit()

async def get_active_clan_war(clan_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM clan_wars WHERE (clan1_id=? OR clan2_id=?) AND status='active' ORDER BY war_id DESC LIMIT 1",
            (clan_id, clan_id)
        ) as c:
            r = await c.fetchone()
        return dict(r) if r else None

async def declare_clan_war(clan1_id, clan2_id, prize=1000):
    async with aiosqlite.connect(DB_PATH) as db:
        # Проверить что оба клана не в войне
        async with db.execute(
            "SELECT war_id FROM clan_wars WHERE (clan1_id=? OR clan2_id=? OR clan1_id=? OR clan2_id=?) AND status='active'",
            (clan1_id, clan1_id, clan2_id, clan2_id)
        ) as c:
            if await c.fetchone(): return False, "Один из кланов уже в войне!"
        await db.execute(
            "INSERT INTO clan_wars (clan1_id, clan2_id, prize) VALUES (?,?,?)",
            (clan1_id, clan2_id, prize)
        )
        await db.commit()
        async with db.execute("SELECT last_insert_rowid()") as c:
            war_id = (await c.fetchone())[0]
        return True, war_id

async def join_clan_war(war_id, clan_id, user_id, pet_id):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO clan_war_members (war_id,clan_id,user_id,pet_id) VALUES (?,?,?,?)",
                (war_id, clan_id, user_id, pet_id)
            )
            await db.commit(); return True
        except: return False

async def do_clan_war_round(bot):
    """Проводит раунд между всеми активными войнами."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM clan_wars WHERE status='active'") as c:
            wars = [dict(r) for r in await c.fetchall()]

    for war in wars:
        try:
            await _do_one_clan_war_round(bot, war)
        except Exception as e:
            # ── ИСПРАВЛЕНИЕ: одна сломанная война больше не блокирует
            #    проведение раундов для всех остальных активных войн.
            logger.warning(f"do_clan_war_round war_id={war.get('war_id')}: {e}")

async def _do_one_clan_war_round(bot, war):
        war_id = war['war_id']
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM clan_war_members WHERE war_id=? AND clan_id=?",
                (war_id, war['clan1_id'])
            ) as c:
                team1 = [dict(r) for r in await c.fetchall()]
            async with db.execute(
                "SELECT * FROM clan_war_members WHERE war_id=? AND clan_id=?",
                (war_id, war['clan2_id'])
            ) as c:
                team2 = [dict(r) for r in await c.fetchall()]

        if not team1 or not team2:
            # ── ИСПРАВЛЕНИЕ: раньше тут было тихое continue — война висела
            #    "активной" бесконечно без единого раунда, если одна из
            #    сторон не выставила питомцев, и игроки не понимали, почему.
            #    Теперь сторона с выставленными питомцами получает уведомление.
            empty_side_clan = war['clan2_id'] if not team2 else war['clan1_id']
            waiting_team = team1 if team1 else team2
            if waiting_team:
                empty_clan_data = await get_clan(empty_side_clan)
                wait_msg = (
                    f"⏳ <b>Клановая война</b>\n\n"
                    f"Ждём, пока клан [{empty_clan_data['tag'] if empty_clan_data else '?'}] "
                    f"выставит питомцев. Раунд пропущен."
                )
                seen = set()
                for m in waiting_team:
                    if m['user_id'] in seen: continue
                    seen.add(m['user_id'])
                    try: await bot.send_message(m['user_id'], wait_msg, parse_mode="HTML")
                    except: pass
            return

        import random
        f1 = random.choice(team1); f2 = random.choice(team2)
        p1 = await get_pet_by_id(f1['pet_id'], f1['user_id'])
        p2 = await get_pet_by_id(f2['pet_id'], f2['user_id'])
        if not p1 or not p2: return

        atk1,def1,hp1 = battle_stats(p1)
        atk2,def2,hp2 = battle_stats(p2)
        pow1 = atk1 + def1 + hp1
        pow2 = atk2 + def2 + hp2
        # Добавляем рандом ±20%
        pow1 *= random.uniform(0.8, 1.2)
        pow2 *= random.uniform(0.8, 1.2)
        winner_clan = war['clan1_id'] if pow1 >= pow2 else war['clan2_id']
        loser_clan  = war['clan2_id'] if pow1 >= pow2 else war['clan1_id']
        win_pet = p1 if pow1 >= pow2 else p2
        los_pet = p2 if pow1 >= pow2 else p1

        async with aiosqlite.connect(DB_PATH) as db:
            if winner_clan == war['clan1_id']:
                await db.execute("UPDATE clan_wars SET clan1_wins=clan1_wins+1 WHERE war_id=?", (war_id,))
            else:
                await db.execute("UPDATE clan_wars SET clan2_wins=clan2_wins+1 WHERE war_id=?", (war_id,))
            await db.commit()
            async with db.execute("SELECT clan1_wins,clan2_wins FROM clan_wars WHERE war_id=?", (war_id,)) as c:
                row = await c.fetchone(); w1,w2 = row[0],row[1]

        clan1 = await get_clan(war['clan1_id']); clan2 = await get_clan(war['clan2_id'])
        score_txt = f"⚔️ <b>Раунд клановой войны!</b>\n\n[{clan1['tag']}] vs [{clan2['tag']}]\n\n"
        score_txt += f"{PETS.get(win_pet['pet_type'],PETS['dog'])['emoji']} <b>{win_pet['name']}</b> победил!\n"
        score_txt += f"📊 Счёт: {w1}:{w2}\n"

        # Проверяем победителя (первый до 5 побед)
        winner_id = None
        if w1 >= 5: winner_id = war['clan1_id']; loser_id = war['clan2_id']
        elif w2 >= 5: winner_id = war['clan2_id']; loser_id = war['clan1_id']

        if winner_id:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE clan_wars SET status='finished',ended_at=? WHERE war_id=?",
                                 (datetime.now().isoformat(), war_id))
                # Даём приз клану победителю
                await db.execute("UPDATE clans SET wins=wins+1,coins=coins+? WHERE clan_id=?",
                                 (war['prize'], winner_id))
                await db.commit()
            winner_clan_data = await get_clan(winner_id)
            score_txt += f"\n🏆 <b>[{winner_clan_data['tag']}] выиграл войну!</b>\n💰 Приз: {war['prize']} монет в казну клана!"

        # Уведомляем всех участников
        all_members = []
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT DISTINCT user_id FROM clan_war_members WHERE war_id=?", (war_id,)) as c:
                all_members = [r[0] for r in await c.fetchall()]
        for uid in all_members:
            try: await bot.send_message(uid, score_txt, parse_mode="HTML"); await asyncio.sleep(0.05)
            except: pass

# ── ХЕНДЛЕРЫ КЛАНОВЫХ ВОЙН ────────────────────────────────────────────

@router.callback_query(F.data.startswith("clan:war:"))
async def cb_clan_war(cb: CallbackQuery):
    # Формат данных:
    # clan:war:menu:{uid}
    # clan:war:declare:{uid}
    # clan:war:attack:{target_clan_id}:{uid}   ← uid всегда последний
    # clan:war:enroll:{uid}
    parts = cb.data.split(":")
    action = parts[2]
    uid = int(parts[-1])  # uid ВСЕГДА последний элемент

    clan = await get_clan_by_user(uid)
    if not clan: await cb.answer("Ты не в клане!", show_alert=True); return

    if action == "menu":
        war = await get_active_clan_war(clan['clan_id'])
        b = InlineKeyboardBuilder()
        if war:
            other_clan_id = war['clan2_id'] if war['clan1_id']==clan['clan_id'] else war['clan1_id']
            other = await get_clan(other_clan_id)
            w1 = war['clan1_wins'] if war['clan1_id']==clan['clan_id'] else war['clan2_wins']
            w2 = war['clan2_wins'] if war['clan1_id']==clan['clan_id'] else war['clan1_wins']
            b.button(text="⚔️ Выставить питомца", callback_data=f"clan:war:enroll:{uid}")
            b.button(text="◀️ Назад", callback_data=f"clan:back:{uid}")
            b.adjust(1)
            await safe_edit(cb.message,
                f"⚔️ <b>Клановая война!</b>\n\n"
                f"[{clan['tag']}] vs [{other['tag']}]\n"
                f"📊 Счёт: <b>{w1}:{w2}</b>\n"
                f"🏆 Первый до 5 побед выигрывает\n💰 Приз: {war['prize']} монет в казну",
                reply_markup=b.as_markup(), parse_mode="HTML")
        else:
            b.button(text="🗡️ Объявить войну клану", callback_data=f"clan:war:declare:{uid}")
            b.button(text="◀️ Назад", callback_data=f"clan:back:{uid}")
            b.adjust(1)
            await safe_edit(cb.message,
                f"⚔️ <b>Клановые войны</b>\n\n"
                "У твоего клана нет активной войны.\n"
                "Объяви войну другому клану и сразитесь за 💰!",
                reply_markup=b.as_markup(), parse_mode="HTML")
        await cb.answer()

    elif action == "declare":
        # Проверяем что владелец
        if clan['owner_id'] != uid:
            await cb.answer("Только владелец клана может объявить войну!", show_alert=True); return
        clans = await get_top_clans(10)
        b = InlineKeyboardBuilder()
        for c2 in clans:
            if c2['clan_id'] == clan['clan_id']: continue
            b.button(text=f"[{c2['tag']}] {c2['name']} ур.{c2['level']}",
                     callback_data=f"clan:war:attack:{c2['clan_id']}:{uid}")
        b.button(text="◀️ Назад", callback_data=f"clan:war:menu:{uid}")
        b.adjust(1)
        await safe_edit(cb.message, "🗡️ <b>Выбери противника:</b>",
                        reply_markup=b.as_markup(), parse_mode="HTML")
        await cb.answer()

    elif action == "attack":
        # parts = ["clan","war","attack","{target_clan_id}","{uid}"]
        target_clan_id = int(parts[3])
        # clan уже загружен для uid выше
        if clan['owner_id'] != uid:
            await cb.answer("Только владелец клана может объявить войну!", show_alert=True); return
        ok, result = await declare_clan_war(clan['clan_id'], target_clan_id, prize=2000)
        if not ok: await cb.answer(f"❌ {result}", show_alert=True); return
        target = await get_clan(target_clan_id)
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT user_id FROM clan_members WHERE clan_id=?", (target_clan_id,)) as c:
                members = [r[0] for r in await c.fetchall()]
        for mid in members:
            try:
                await cb.message.bot.send_message(mid,
                    f"⚔️ <b>Клан [{clan['tag']}] {clan['name']} объявил вам войну!</b>\n\n"
                    f"🏆 Приз: 2000 монет в казну клана\n"
                    f"Выставьте питомцев через меню клана → Войны!",
                    parse_mode="HTML")
            except: pass
        await cb.answer("⚔️ Война объявлена!")
        await safe_edit(cb.message,
            f"⚔️ <b>Война объявлена!</b>\n\n[{clan['tag']}] vs [{target['tag']}]\n"
            "Выставьте питомцев для боёв — раунды идут каждые 30 минут!",
            parse_mode="HTML")

    elif action == "enroll":
        pet = await get_active_pet(uid)
        if not pet: await cb.answer("Нет активного питомца!", show_alert=True); return
        war = await get_active_clan_war(clan['clan_id'])
        if not war: await cb.answer("Нет активной войны!", show_alert=True); return
        ok = await join_clan_war(war['war_id'], clan['clan_id'], uid, pet['pet_id'])
        if ok:
            await cb.answer(f"✅ {pet['name']} выставлен в войну!")
        else:
            await cb.answer("Ты уже выставил питомца!", show_alert=True)


# ═══════════════════════════════════════════════════
# ТУРНИРЫ — автостарт при 8 игроках, пошаговые бои
# ═══════════════════════════════════════════════════
# Формат: плей-офф на выбывание (8→4→2→1)
# Раунды проводятся автоматически каждые 10 минут
# ════════════════════════════════════════════════════

async def run_tournament_round(bot):
    """Проводит раунд для активных турниров."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tournaments WHERE status='active'") as c:
            active = [dict(r) for r in await c.fetchall()]
        # Автостарт — регистрация заполнена
        async with db.execute(
            "SELECT t.* FROM tournaments t JOIN (SELECT tournament_id,COUNT(*) as cnt FROM tournament_players GROUP BY tournament_id) tp ON t.tournament_id=tp.tournament_id WHERE t.status='registration' AND tp.cnt>=8"
        ) as c:
            to_start = [dict(r) for r in await c.fetchall()]

    import random

    # Стартуем заполненные турниры
    for t in to_start:
        tid = t['tournament_id']
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE tournaments SET status='active' WHERE tournament_id=?", (tid,))
            await db.commit()
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT user_id FROM tournament_players WHERE tournament_id=?", (tid,)) as c:
                players = [r[0] for r in await c.fetchall()]
        random.shuffle(players)
        for uid in players:
            try:
                await bot.send_message(uid,
                    f"🏆 <b>Турнир '{t['name']}' начался!</b>\n\n"
                    f"Участников: {len(players)}\n"
                    f"💰 Приз: {t['prize']} монет\n\n"
                    "Раунды проходят автоматически каждые 10 минут!",
                    parse_mode="HTML")
            except: pass
        active.append(t)

    # Проводим раунды активных турниров
    for t in active:
        tid = t['tournament_id']
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT user_id FROM tournament_players WHERE tournament_id=? AND eliminated=0",
                (tid,)
            ) as c:
                alive = [r[0] for r in await c.fetchall()]

        if len(alive) <= 1:
            # Турнир завершён
            winner_id = alive[0] if alive else None
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE tournaments SET status='finished' WHERE tournament_id=?", (tid,))
                await db.commit()
            if winner_id:
                await update_coins(winner_id, t['prize'])
                winner = await get_user(winner_id)
                wname = winner.get('first_name') or winner.get('username') or str(winner_id)
                users = await get_all_users()
                for u in users:
                    try:
                        await bot.send_message(u['user_id'],
                            f"🏆 <b>Турнир '{t['name']}' завершён!</b>\n\n"
                            f"👑 Победитель: <b>{wname}</b>\n"
                            f"💰 Приз: {t['prize']} монет!",
                            parse_mode="HTML")
                    except: pass
            continue

        if len(alive) < 2: continue
        random.shuffle(alive)
        # Бои парами
        round_results = []
        eliminated = []
        for i in range(0, len(alive)-1, 2):
            u1, u2 = alive[i], alive[i+1]
            p1 = await get_active_pet(u1)
            p2 = await get_active_pet(u2)
            if not p1: eliminated.append(u1); round_results.append((u2,u1,None,None)); continue
            if not p2: eliminated.append(u2); round_results.append((u1,u2,None,None)); continue
            atk1,def1,hp1 = battle_stats(p1)
            atk2,def2,hp2 = battle_stats(p2)
            pow1 = (atk1+def1+hp1)*random.uniform(0.8,1.2)
            pow2 = (atk2+def2+hp2)*random.uniform(0.8,1.2)
            if pow1 >= pow2:
                round_results.append((u1,u2,p1,p2)); eliminated.append(u2)
            else:
                round_results.append((u2,u1,p2,p1)); eliminated.append(u1)
        # Нечётный — проходит автоматически
        if len(alive) % 2 == 1:
            round_results.append((alive[-1], None, None, None))

        async with aiosqlite.connect(DB_PATH) as db:
            for uid_elim in eliminated:
                await db.execute(
                    "UPDATE tournament_players SET eliminated=1 WHERE tournament_id=? AND user_id=?",
                    (tid, uid_elim)
                )
            await db.commit()

        # Уведомляем участников
        remaining = len(alive) - len(eliminated)
        for winner_id, loser_id, wp, lp in round_results:
            w_user = await get_user(winner_id)
            wname = w_user.get('first_name') or str(winner_id)
            if loser_id:
                l_user = await get_user(loser_id)
                lname = l_user.get('first_name') or str(loser_id)
                wpname = wp['name'] if wp else '?'
                lpname = lp['name'] if lp else '?'
                # Победителю
                try:
                    await bot.send_message(winner_id,
                        f"🏆 <b>Раунд турнира '{t['name']}'</b>\n\n"
                        f"✅ <b>{wpname}</b> победил <b>{lpname}</b> ({lname})!\n"
                        f"👥 Осталось участников: {remaining}",
                        parse_mode="HTML")
                except: pass
                # Проигравшему
                try:
                    await bot.send_message(loser_id,
                        f"🏆 <b>Раунд турнира '{t['name']}'</b>\n\n"
                        f"💀 <b>{lpname}</b> проиграл <b>{wpname}</b> ({wname})\n"
                        f"Ты выбываешь из турнира. Удачи в следующий раз!",
                        parse_mode="HTML")
                except: pass
            else:
                try:
                    await bot.send_message(winner_id,
                        f"🏆 <b>Турнир '{t['name']}'</b>\n✅ Ты проходишь в следующий раунд автоматически!",
                        parse_mode="HTML")
                except: pass

async def tick_events(bot: Bot):
    try:
        event=await get_active_event()
        if event: return
        if random.random()>0.25: return
        ev=random.choice(EVENT_POOL); await start_event(ev)
        users=await get_all_users()
        for u in users:
            try: await bot.send_message(u['user_id'],f"🎪 <b>{ev['name']}</b>\n{ev['desc']}\n⏰ {ev['duration_h']}ч",parse_mode="HTML"); await asyncio.sleep(0.05)
            except: pass
    except Exception as e: logger.warning(f"tick_events: {e}")

async def tick_boss_spawn(bot: Bot):
    """Спавн глобального босса с 25% шансом каждые 30 мин."""
    global _active_boss
    try:
        async with get_boss_lock():
            if _active_boss is not None:
                return  # Босс уже активен
            if random.random() > 0.25:
                return  # Не повезло
            bdata = random.choice(GLOBAL_BOSS_POOL)
            _active_boss = {"data": bdata, "hp": bdata['hp'], "attackers": {}}
        # Уведомляем всех игроков
        users = await get_all_users()
        for u in users:
            try:
                await bot.send_message(
                    u['user_id'],
                    f"👹 <b>ПОЯВИЛСЯ ГЛОБАЛЬНЫЙ БОСС!</b>\n\n"
                    f"{bdata['name']}\n"
                    f"❤️ {bdata['hp']:,} HP\n"
                    f"💎 Награда: {bdata['reward_crystals'][0]}–{bdata['reward_crystals'][1]} кристаллов\n\n"
                    f"Напиши <b>босс</b> чтобы атаковать!",
                    parse_mode="HTML"
                )
                await asyncio.sleep(0.05)
            except: pass
    except Exception as e:
        logger.warning(f"tick_boss_spawn: {e}")

async def tick_auction(bot: Bot):
    """Завершает просроченные аукционы и уведомляет участников."""
    try:
        now = datetime.now().isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT a.*, p.name as pet_name, p.pet_type FROM auction_lots a "
                "JOIN pets p ON a.pet_id=p.pet_id WHERE a.status='active' AND a.ends_at<?", (now,)
            ) as c:
                expired = [dict(r) for r in await c.fetchall()]
            for lot in expired:
                await db.execute("UPDATE auction_lots SET status='ended' WHERE lot_id=?", (lot['lot_id'],))
                if lot['current_buyer']:
                    # Передаём питомца победителю и платим продавцу
                    await db.execute("UPDATE pets SET owner_id=?,active=0,for_sale=0 WHERE pet_id=?",
                                     (lot['current_buyer'], lot['pet_id']))
                    await db.execute("UPDATE users SET coins=coins+? WHERE user_id=?",
                                     (lot['current_price'], lot['seller_id']))
                    await db.commit()
                    pd = PETS.get(lot['pet_type'], PETS['dog'])
                    # Ачивка победителю
                    await check_and_grant_achievement(lot['current_buyer'], "auction_win")
                    # Уведомляем победителя
                    try:
                        await bot.send_message(lot['current_buyer'],
                            f"🏪 <b>Аукцион завершён!</b>\n\n"
                            f"🏆 Ты выиграл {pd['emoji']} <b>{lot['pet_name']}</b>!\n"
                            f"💰 Итоговая цена: {lot['current_price']:,}💰",
                            parse_mode="HTML")
                    except: pass
                    # Уведомляем продавца
                    try:
                        await bot.send_message(lot['seller_id'],
                            f"🏪 <b>Питомец продан на аукционе!</b>\n\n"
                            f"{pd['emoji']} <b>{lot['pet_name']}</b>\n"
                            f"💰 Выручка: +{lot['current_price']:,}💰",
                            parse_mode="HTML")
                    except: pass
                else:
                    # Никто не ставил — возвращаем питомца
                    await db.execute("UPDATE pets SET for_sale=0 WHERE pet_id=?", (lot['pet_id'],))
                    try:
                        await bot.send_message(lot['seller_id'],
                            f"🏪 Аукцион завершён без ставок.\n"
                            f"Питомец <b>{lot['pet_name']}</b> возвращён тебе.",
                            parse_mode="HTML")
                    except: pass
            if expired: await db.commit()
    except Exception as e:
        logger.warning(f"tick_auction: {e}")

def _evo_screen(uid, pet, user):
    stage = pet_evo_stage(pet)
    cur = EVOLUTION_STAGES[stage]
    max_stage = max(EVOLUTION_STAGES)
    crystals = user.get("crystals") or 0
    b = InlineKeyboardBuilder()
    if stage >= max_stage:
        next_txt = "🏆 Достигнута максимальная стадия эволюции!"
        can = False
    else:
        nxt = EVOLUTION_STAGES[stage + 1]
        next_txt = (
            f"Следующая: {nxt['icon']} <b>{nxt['name']}</b> ×{nxt['mult']:.2f}\n"
            f"Требования: Ур. <b>{nxt['min_level']}</b>, 💎 <b>{nxt['cost']}</b>\n"
            f"Уровень питомца: <b>{pet['level']}</b> / {nxt['min_level']}\n"
            f"Кристаллы: <b>{crystals}</b> / {nxt['cost']}"
        )
        can = pet['level'] >= nxt['min_level'] and crystals >= nxt['cost']
        b.button(
            text=f"🧬 Эволюционировать ({nxt['cost']}💎)",
            callback_data=f"evo:do:{pet['pet_id']}:{uid}",
        )
    b.button(text="◀️ К питомцу", callback_data=f"vp:{pet['pet_id']}:{uid}")
    b.adjust(1)
    text = (
        f"🧬 <b>Эволюция питомца</b>\n\n"
        f"{PETS.get(pet['pet_type'], PETS['dog'])['emoji']} <b>{pet['name']}</b> · Ур. {pet['level']}\n"
        f"Стадия: {cur['icon']} <b>{cur['name']}</b> ×{cur['mult']:.2f}\n\n"
        f"{next_txt}"
    )
    if stage < max_stage and not can:
        text += "\n\n⚠️ Недостаточно уровня или кристаллов."
    return text, b.as_markup()

async def h_evolve(msg):
    uid = msg.from_user.id
    pet = await get_active_pet(uid)
    if not pet:
        await safe_reply(msg, "❌ У тебя нет активного питомца!", parse_mode="HTML")
        return
    user = await get_user(uid)
    text, kb = _evo_screen(uid, pet, user)
    await safe_reply(msg, text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("evo:menu:"))
async def cb_evo_menu(cb):
    parts = cb.data.split(":")
    pid = int(parts[2]); uid = int(parts[3])
    if cb.from_user.id != uid:
        await cb.answer("Это меню не для тебя!", show_alert=True)
        return
    pet = await get_pet_by_id(pid, uid)
    if not pet:
        await cb.answer("Питомец не найден!", show_alert=True)
        return
    user = await get_user(uid)
    text, kb = _evo_screen(uid, pet, user)
    await safe_edit(cb.message, text, reply_markup=kb, parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("evo:do:"))
async def cb_evo_do(cb):
    parts = cb.data.split(":")
    pid = int(parts[2]); uid = int(parts[3])
    if cb.from_user.id != uid:
        await cb.answer("Это не твой питомец!", show_alert=True)
        return
    pet = await get_pet_by_id(pid, uid)
    if not pet:
        await cb.answer("Питомец не найден!", show_alert=True)
        return
    stage = pet_evo_stage(pet)
    max_stage = max(EVOLUTION_STAGES)
    if stage >= max_stage:
        await cb.answer("Максимальная стадия уже достигнута!", show_alert=True)
        return
    nxt_stage = stage + 1
    nxt = EVOLUTION_STAGES[nxt_stage]
    if pet["level"] < nxt["min_level"]:
        await cb.answer(f"Нужен уровень {nxt['min_level']}!", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE users SET crystals=crystals-? WHERE user_id=? AND crystals>=?",
            (nxt["cost"], uid, nxt["cost"]),
        )
        if cur.rowcount == 0:
            await db.rollback()
            await cb.answer("Недостаточно кристаллов!", show_alert=True)
            return
        base_hp = PETS.get(pet["pet_type"], PETS["dog"])["hp"]
        max_hp = base_hp + (pet["level"] - 1) * 10
        await db.execute(
            "UPDATE pets SET evolution=?, max_health=?, health=? WHERE pet_id=? AND owner_id=?",
            (nxt_stage, max_hp, max_hp, pid, uid),
        )
        await db.commit()
    await check_and_grant_achievement(uid, "first_evolution", cb.bot)
    if nxt_stage >= max_stage:
        await check_and_grant_achievement(uid, "evo_max", cb.bot)
    pet = await get_pet_by_id(pid, uid)
    user = await get_user(uid)
    text, kb = _evo_screen(uid, pet, user)
    await safe_edit(
        cb.message,
        f"🎉 <b>Эволюция завершена!</b>\n\n"
        f"{PETS.get(pet['pet_type'], PETS['dog'])['emoji']} <b>{pet['name']}</b> теперь "
        f"{nxt['icon']} <b>{nxt['name']}</b>!\n"
        f"Бонус к характеристикам: <b>×{nxt['mult']:.2f}</b>\n\n{text}",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await cb.answer("Эволюция успешна!")

async def h_wheel(msg):
    uid = msg.from_user.id
    user = await get_user(uid)
    cd = cd_left(user.get("last_wheel"), WHEEL_COOLDOWN)
    spins = user.get("wheel_spins") or 0
    b = InlineKeyboardBuilder()
    if cd:
        b.button(text=f"⏳ Через {fmt_t(cd)}", callback_data="wheel:cooldown")
    else:
        b.button(text="🎡 Крутить колесо!", callback_data=f"wheel:spin:{uid}")
    b.adjust(1)
    text = (
        f"🎡 <b>Колесо удачи</b>\n\n"
        f"💰 Монеты: <b>{user['coins']:,}</b>\n"
        f"💎 Кристаллы: <b>{user.get('crystals') or 0}</b>\n"
        f"🔄 Вращений всего: <b>{spins}</b>\n\n"
        f"Призы: монеты, кристаллы, токены сезона, еда, XP и даже питомцы!\n"
        f"Джекпот: <b>25 000 монет</b> или <b>100 кристаллов</b>.\n\n"
    )
    if cd:
        text += f"⏳ Следующее вращение через <b>{fmt_t(cd)}</b>"
    else:
        text += "Нажми кнопку, чтобы испытать удачу!"
    await safe_reply(msg, text, reply_markup=b.as_markup(), parse_mode="HTML")

@router.callback_query(F.data == "wheel:cooldown")
async def cb_wheel_cooldown(cb):
    uid = cb.from_user.id
    user = await get_user(uid)
    cd = cd_left(user.get("last_wheel"), WHEEL_COOLDOWN)
    if cd:
        await cb.answer(f"⏳ Через {fmt_t(cd)}", show_alert=True)
    else:
        await cb.answer("Колесо готово! Напиши /wheel")

@router.callback_query(F.data.startswith("wheel:spin:"))
async def cb_wheel_spin(cb):
    uid = int(cb.data.split(":")[-1])
    if cb.from_user.id != uid:
        await cb.answer("Это не твоё колесо!", show_alert=True)
        return
    now = datetime.now().isoformat()
    cutoff = (datetime.now() - timedelta(seconds=WHEEL_COOLDOWN)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE users SET last_wheel=?, wheel_spins=COALESCE(wheel_spins,0)+1 "
            "WHERE user_id=? AND (last_wheel IS NULL OR last_wheel<?)",
            (now, uid, cutoff),
        )
        ok = cur.rowcount
        await db.commit()
    if not ok:
        user = await get_user(uid)
        cd = cd_left(user.get("last_wheel"), WHEEL_COOLDOWN)
        await cb.answer(f"⏳ Через {fmt_t(cd) if cd else 'несколько секунд'}", show_alert=True)
        return
    await cb.answer("🎡 Крутим колесо...")
    prize = random.choices(WHEEL_PRIZES, weights=[p["weight"] for p in WHEEL_PRIZES], k=1)[0]
    result = prize["label"]
    extra = ""
    if prize["kind"] == "coins":
        await update_coins(uid, prize["amount"])
    elif prize["kind"] == "crystals":
        await update_crystals(uid, prize["amount"])
    elif prize["kind"] == "tokens":
        await add_season_tokens(uid, prize["amount"], "wheel")
    elif prize["kind"] == "item":
        await add_item(uid, prize["item"], prize.get("amount", 1))
    elif prize["kind"] == "xp":
        pet = await get_active_pet(uid)
        if pet:
            lvl, leveled = await add_xp(pet["pet_id"], prize["amount"])
            if leveled:
                extra = f"\n🎉 Питомец достиг уровня <b>{lvl}</b>!"
        else:
            await update_coins(uid, 1000)
            result = "1 000 монет (нет питомца для XP)"
    elif prize["kind"] == "pet":
        pet_type = prize["pet"]
        if pet_type not in PETS:
            pet_type = "fox"
        await create_pet(uid, PETS[pet_type]["name"], pet_type, random.random() < 0.05)
        extra = "\n🐾 Питомец добавлен в коллекцию и стал активным!"
    if prize.get("jackpot"):
        await check_and_grant_achievement(uid, "wheel_jackpot", cb.bot)
    await check_and_grant_achievement(uid, "first_spin", cb.bot)
    await add_season_tokens(uid, 2, "wheel")
    await progress_daily_quest(uid, "wheel", 1, cb.bot)
    await asyncio.sleep(0.8)
    user = await get_user(uid)
    b = InlineKeyboardBuilder()
    b.button(text="🎡 Ещё раз", callback_data=f"wheel:spin:{uid}")
    b.button(text="❌ Закрыть", callback_data=f"close:{uid}")
    b.adjust(1)
    await safe_edit(
        cb.message,
        f"🎡 <b>Колесо удачи</b>\n\n"
        f"🎉 Выпало: <b>{result}</b>{extra}\n\n"
        f"💰 Монеты: <b>{user['coins']:,}</b>\n"
        f"💎 Кристаллы: <b>{user.get('crystals') or 0}</b>\n"
        f"🔄 Вращений всего: <b>{user.get('wheel_spins') or 0}</b>\n\n"
        f"⏳ Следующее вращение через <b>{fmt_t(WHEEL_COOLDOWN)}</b>",
        reply_markup=b.as_markup(),
        parse_mode="HTML",
    )

async def announce_update(bot):
    last = await get_bot_meta("last_update_version")
    if last == BOT_VERSION:
        return
    await set_bot_meta("last_update_version", BOT_VERSION)
    plain_text = (
        f"🚀 <b>Апдейт PetWorld v{BOT_VERSION}</b>\n\n"
        f"<b>Добавлено:</b>\n" + "\n".join(f"• {n}" for n in UPDATE_NOTES)
    )
    rich_text = (
        f"<h1>🚀 Апдейт PetWorld v{BOT_VERSION}</h1>"
        f"<p><b>Добавлено:</b></p><ul>"
        + "".join(f"<li>{n}</li>" for n in UPDATE_NOTES)
        + "</ul>"
    )
    sent = 0
    failed = 0
    seen = set()
    try:
        users = await get_all_users()
    except Exception:
        users = []
    for u in users:
        uid = u.get("user_id")
        if not uid or uid in seen or u.get("is_banned"):
            continue
        seen.add(uid)
        try:
            await send_rich(bot, uid, rich_text, fallback_html=plain_text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    try:
        chats = await get_known_chats()
    except Exception:
        chats = []
    for ch in chats:
        cid = ch.get("chat_id")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        try:
            await send_rich(bot, cid, rich_text, fallback_html=plain_text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    if GAME_GROUP and GAME_GROUP not in seen:
        try:
            await send_rich(bot, GAME_GROUP, rich_text, fallback_html=plain_text, message_thread_id=GAME_THREAD)
            sent += 1
        except Exception:
            try:
                await send_rich(bot, GAME_GROUP, rich_text, fallback_html=plain_text)
                sent += 1
            except Exception:
                failed += 1
    logger.info("Update announce v%s: sent=%s failed=%s", BOT_VERSION, sent, failed)

async def _job_decay_all_pets(bot):
    await asyncio.wait_for(decay_all_pets(bot), timeout=120)


async def _job_tick_events(bot):
    await asyncio.wait_for(tick_events(bot), timeout=120)


async def _job_do_clan_war_round(bot):
    await asyncio.wait_for(do_clan_war_round(bot), timeout=180)


async def _job_run_tournament_round(bot):
    await asyncio.wait_for(run_tournament_round(bot), timeout=180)


async def _job_tick_boss_spawn(bot):
    await asyncio.wait_for(tick_boss_spawn(bot), timeout=120)


async def _job_tick_auction(bot):
    await asyncio.wait_for(tick_auction(bot), timeout=120)


async def main():
    await init_db()
    await migrate_season()
    await init_clan_wars_db()
    session=AiohttpSession(proxy=PROXY) if PROXY else AiohttpSession()
    bot=PremiumEmojiBot(token=BOT_TOKEN,session=session)
    await load_premium_emoji(bot)
    dp=Dispatcher(storage=MemoryStorage())
    dp.message.middleware(CrashGuardMiddleware())
    dp.callback_query.middleware(CrashGuardMiddleware())
    dp.message.middleware(ThreadMiddleware())
    dp.callback_query.middleware(ThreadMiddleware())
    dp.message.middleware(BanMiddleware())
    dp.callback_query.middleware(BanMiddleware())
    dp.callback_query.middleware(AntiSpamMiddleware())
    dp.callback_query.middleware(OwnerCheckMiddleware())
    dp.include_router(router)
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    scheduler=AsyncIOScheduler()
    scheduler.add_job(_job_decay_all_pets,'interval',hours=1,args=[bot])
    scheduler.add_job(_job_tick_events,'interval',minutes=30,args=[bot])
    # ── ИСПРАВЛЕНИЕ БАГА "клановая война не идёт": раньше у job не было
    #    next_run_time, поэтому APScheduler ждал полные 30 минут ПОСЛЕ
    #    каждого запуска/перезапуска бота, прежде чем провести первый раунд.
    #    При частых перезапусках раунды могли не проходить часами. Теперь
    #    первая проверка идёт сразу при старте, а дальше — каждые 30 минут.
    scheduler.add_job(_job_do_clan_war_round,'interval',minutes=30,args=[bot],next_run_time=datetime.now())
    scheduler.add_job(_job_run_tournament_round,'interval',minutes=10,args=[bot],next_run_time=datetime.now())
    scheduler.add_job(_job_tick_boss_spawn,'interval',minutes=30,args=[bot],next_run_time=datetime.now())
    scheduler.add_job(_job_tick_auction,'interval',minutes=5,args=[bot],next_run_time=datetime.now())  # v7.0
    scheduler.start()
    asyncio.create_task(announce_update(bot))
    print(f"PetWorld v{BOT_VERSION} | Thread:{GAME_THREAD} | Admins:{ADMIN_IDS}")
    try:
        await dp.start_polling(bot,allowed_updates=["message","callback_query","pre_checkout_query"])
    finally:
        await bot.session.close()

if __name__=='__main__':
    asyncio.run(main())
