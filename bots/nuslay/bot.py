import asyncio
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramConflictError
from aiogram.types import BotCommand

from config import ADMIN_IDS, BOT_TOKEN, CHAT_ID, CHAT_TITLE, EVENT_NAME, REMINDER_INTERVAL
from db import db
from handlers import admin, user
from keyboards import reminder_kb
from middleware import AntiFloodMiddleware, ChatMemberMiddleware

logger = logging.getLogger(__name__)


def start_healthcheck() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    port = int(os.getenv("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


async def notify_admins(bot: Bot, text: str) -> None:
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass


async def init_db_safe(bot: Bot | None = None) -> None:
    import glob
    import os as _os

    from db import DB_PATH
    try:
        await db.init()
        return
    except Exception as e:
        logger.exception("db.init failed")
        if bot:
            await notify_admins(bot, f"⚠️ База данных повреждена ({esc_log(e)}), пересоздаю пустую...")
        for suffix in ["", "-wal", "-shm"]:
            p = DB_PATH + suffix
            if _os.path.exists(p):
                try:
                    _os.rename(p, p + ".bak")
                except Exception:
                    pass
        await db.init()


def esc_log(e: Exception) -> str:
    return str(e)[:300].replace("<", "&lt;").replace(">", "&gt;")


async def reminder_loop(bot: Bot, interval: int) -> None:
    logger.info("Reminder loop started: interval=%ss, chat=%s", interval, CHAT_ID)
    send_failed_notified = False
    while True:
        try:
            last = float(await db.get_setting("last_reminder_ts", "0") or 0)
            wait = max(0.0, interval - (time.time() - last))
            if wait > 0:
                await asyncio.sleep(wait)
            stage = await db.get_stage()
            if stage == "closed":
                await asyncio.sleep(interval)
                continue
            me = await bot.me()
            bot_url = f"https://t.me/{me.username}"
            if stage == "voting":
                text = (
                    f"🗳 <b>Голосование {EVENT_NAME} идёт!</b>\n\n"
                    "Успей проголосовать за лучших — один голос в каждой номинации."
                )
            else:
                text = (
                    f"👑 <b>Премия {EVENT_NAME} для чата {CHAT_TITLE}!</b>\n\n"
                    "Голосование скоро откроется — успей заявить себя кандидатом 🎖"
                )
            await bot.send_message(CHAT_ID, text, reply_markup=reminder_kb(bot_url))
            await db.set_setting("last_reminder_ts", time.time())
            logger.info("Reminder sent to %s", CHAT_ID)
        except Exception as e:
            logger.warning("Reminder send failed: %s", e)
            if not send_failed_notified:
                await notify_admins(
                    bot,
                    f"⚠️ Не удалось отправить напоминание в {CHAT_ID}:\n{esc_log(e)}\n\n"
                    "Проверь, что бот добавлен админом в чат.",
                )
                send_failed_notified = True
            await asyncio.sleep(300)
            continue
        await asyncio.sleep(interval)


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан. Скопируй .env.example в .env и укажи токен.")

    await init_db_safe()
    start_healthcheck()
    time.sleep(3)

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    anti_flood = AntiFloodMiddleware()
    dp.message.outer_middleware(anti_flood)
    dp.callback_query.outer_middleware(anti_flood)
    dp.message.outer_middleware(ChatMemberMiddleware())
    dp.callback_query.outer_middleware(ChatMemberMiddleware())
    dp.include_routers(admin.router, user.router)

    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception as e:
        logger.warning("delete_webhook failed: %s", e)

    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="vote", description="Номинанты и голосование"),
                BotCommand(command="admin", description="Админ-панель"),
            ]
        )
    except Exception as e:
        logger.warning("set_my_commands failed: %s", e)

    await notify_admins(bot, "✅ Бот запущен и работает.")

    reminder_task = asyncio.create_task(reminder_loop(bot, REMINDER_INTERVAL))

    conflict_notified = False
    while True:
        try:
            await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
            break
        except TelegramConflictError:
            logger.error("409: getUpdates перехватывается другим клиентом (токен скомпрометирован?)")
            if not conflict_notified:
                await notify_admins(
                    bot,
                    "🚨 <b>Внимание: перехват апдейтов бота!</b>\n\n"
                    "Кто-то использует токен этого бота параллельно (ошибка 409) — "
                    "из-за этого бот может не отвечать.\n\n"
                    "Что делать:\n"
                    "1. Открой @BotFather → /mybots → выбери бота → API Token → Revoke\n"
                    "2. Пришли новый токен — обновлю деплой",
                )
                conflict_notified = True
            await asyncio.sleep(15)
        except Exception as e:
            logger.exception("Polling error, retry in 10s")
            await notify_admins(bot, "⚠️ Ошибка polling, переподключаюсь:\n" + esc_log(e))
            await asyncio.sleep(10)
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
