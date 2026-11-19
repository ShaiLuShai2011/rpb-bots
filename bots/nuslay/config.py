import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {
    int(x)
    for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",")
    if x.strip()
}
CHAT_ID = os.getenv("CHAT_ID", "@HGDYFL")
CHAT_TITLE = os.getenv("CHAT_TITLE", "Hooligan's Chat")
EVENT_NAME = os.getenv("EVENT_NAME", "NuSlay")
REMINDER_INTERVAL = int(os.getenv("REMINDER_INTERVAL", "7200"))


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def chat_link() -> str:
    if CHAT_ID.startswith("@"):
        return f"https://t.me/{CHAT_ID[1:]}"
    return CHAT_ID
