import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
PORT = int(os.getenv("PORT", 10000))

DB_PATH = "little_dreams.db"
AUTO_REPLY_MINUTES = 10
ANTIFLOOD_LIMIT = 5
ANTIFLOOD_WINDOW = 60
ANTIFLOOD_MUTE = 60