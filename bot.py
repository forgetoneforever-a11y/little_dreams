import os
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ==== НАСТРОЙКИ ====
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Храним связь: message_id_у_админа -> user_id
# В проде лучше заменить на БД (sqlite/redis)
pending: dict[int, int] = {}


# ==== Сообщения от пользователей ====
@dp.message(F.chat.type == "private", ~F.from_user.id == ADMIN_ID)
async def user_message(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "—"
    full_name = message.from_user.full_name

    header = (
        f"📩 <b>Сообщение от пользователя</b>\n"
        f"👤 {full_name} (@{username})\n"
        f"🆔 <code>{user_id}</code>\n"
        f"———\n"
        f"↩️ <i>Ответьте Reply на это сообщение, чтобы ответить</i>"
    )

    # Пересылаем админу с шапкой
    sent = await bot.send_message(ADMIN_ID, header)
    copied = await message.copy_to(ADMIN_ID)

    # Запоминаем оба id (на случай reply на шапку или на копию)
    pending[sent.message_id] = user_id
    pending[copied.message_id] = user_id

    await message.answer("✅ Сообщение отправлено в поддержку. Ожидайте ответа.")


# ==== Ответ админа через Reply ====
@dp.message(F.from_user.id == ADMIN_ID, F.reply_to_message)
async def admin_reply(message: types.Message):
    replied_id = message.reply_to_message.message_id
    user_id = pending.get(replied_id)

    if not user_id:
        await message.answer("⚠️ Не могу определить получателя. Ответьте Reply на пересланное сообщение пользователя.")
        return

    try:
        await message.copy_to(user_id)
        await message.answer("✅ Ответ отправлен.")
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки: {e}")


# ==== /start ====
@dp.message(F.text == "/start")
async def start(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "👋 Режим админа.\n"
            "Вам будут приходить сообщения пользователей.\n"
            "Чтобы ответить — сделайте <b>Reply</b> на пересланное сообщение."
        )
    else:
        await message.answer("👋 Здравствуйте! Напишите ваш вопрос — поддержка ответит.")


# ==== Webhook ====
async def on_startup(bot: Bot):
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Webhook: {WEBHOOK_URL}")


def main():
    dp.startup.register(on_startup)
    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()