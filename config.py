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
ANTIFLOOD_STRIKES_TO_BAN = 3     # после N срабатываний — автобан

# Текст правил (для команды /rules)
RULES_TEXT = """📜 <b>Правила поддержки Little Dreams</b>

1. 🤝 <b>Уважение</b> — без мата и оскорблений.
2. 💬 <b>Один вопрос — одно обращение.</b> Не дублируйте.
3. 🚫 <b>Без спама</b>, рекламы и флуда.
4. ⏳ <b>Терпение</b> — оператор отвечает в течение дня.
5. 🔒 <b>Не отправляйте</b> пароли, коды из SMS и данные карт.
6. 🕐 <b>График:</b> Пн–Пт, 10:00–19:00 (МСК).

❗ Нарушение → предупреждение → <b>бан</b>."""
