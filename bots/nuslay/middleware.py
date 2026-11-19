import logging
import time
from collections import defaultdict, deque

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import CHAT_ID, CHAT_TITLE, chat_link, is_admin

logger = logging.getLogger(__name__)

CACHE_TTL = 60
_cache: dict = {}
_warned = False

FLOOD_WINDOW = 3
FLOOD_LIMIT = 5


class AntiFloodMiddleware(BaseMiddleware):
    def __init__(self):
        self.history: dict = defaultdict(deque)

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None or user.is_bot or is_admin(user.id):
            return await handler(event, data)

        now = time.monotonic()
        events = self.history[user.id]
        while events and now - events[0] > FLOOD_WINDOW:
            events.popleft()

        if len(events) >= FLOOD_LIMIT:
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer("⚡ Слишком много запросов, подожди пару секунд")
                except Exception:
                    pass
            return

        events.append(now)
        if len(self.history) > 10000:
            self.history.clear()
        return await handler(event, data)


def join_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"➕ Вступить в {CHAT_TITLE}", url=chat_link())]
        ]
    )


def deny_text() -> str:
    return (
        f"⛔ Вы не являетесь участником чата <b>{CHAT_TITLE}</b>!\n\n"
        f"Присоединяйся 👉 {chat_link()}\n"
        "и возвращайся к голосованию 👑"
    )


class ChatMemberMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        bot: Bot = data.get("bot")
        user = data.get("event_from_user")
        if bot is None or user is None or user.is_bot or is_admin(user.id):
            return await handler(event, data)

        uid = user.id
        now = time.monotonic()
        cached = _cache.get(uid)
        if cached and now - cached[0] < CACHE_TTL:
            ok = cached[1]
        else:
            ok = await self._check(bot, uid)
            _cache[uid] = (now, ok)

        if ok:
            return await handler(event, data)
        await self._deny(event)
        return

    async def _check(self, bot: Bot, user_id: int) -> bool:
        global _warned
        try:
            member = await bot.get_chat_member(CHAT_ID, user_id)
        except Exception as e:
            if not _warned:
                logger.warning(
                    "Не удалось проверить участие в %s (%s). Проверка отключена — "
                    "добавь бота в чат с правами админа.", CHAT_ID, e
                )
                _warned = True
            return True
        if member.status in ("member", "administrator", "creator"):
            return True
        if member.status == "restricted":
            return bool(member.is_member)
        return False

    async def _deny(self, event) -> None:
        if isinstance(event, CallbackQuery):
            await event.answer("⛔ Ты не участник чата", show_alert=True)
            if event.message:
                try:
                    await event.message.edit_text(deny_text(), reply_markup=join_kb())
                except Exception:
                    pass
        elif isinstance(event, Message):
            await event.answer(deny_text(), reply_markup=join_kb())
