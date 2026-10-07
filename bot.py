import os
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ==== НАСТРОЙКИ ====
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

pending: dict[int, int] = {}


# ==== /start для пользователя ====
@dp.message(CommandStart(), ~F.from_user.id.in_(ADMIN_IDS))
async def user_start(message: types.Message):
    await message.answer(
        "👋 <b>Здравствуйте!</b>\n\n"
        "Это служба поддержки <b>Little Dreams</b> 💭\n\n"
        "Опишите ваш вопрос — и мы ответим вам в ближайшее время.\n\n"
        "✍️ <i>Просто напишите сообщение ниже.</i>"
    )


# ==== /start для админа ====
@dp.message(CommandStart(), F.from_user.id.in_(ADMIN_IDS))
async def admin_start(message: types.Message):
    await message.answer(
        "👋 <b>Режим администратора</b>\n\n"
        "Вам будут приходить сообщения от пользователей.\n\n"
        "↩️ Чтобы ответить — сделайте <b>Reply</b> на пересланное сообщение."
    )


# ==== /help ====
@dp.message(Command("help"), ~F.from_user.id.in_(ADMIN_IDS))
async def user_help(message: types.Message):
    await message.answer(
        "❓ <b>Помощь</b>\n\n"
        "Просто напишите ваш вопрос — оператор ответит вам здесь же.\n\n"
        "Вы можете отправлять: текст, фото, видео, документы, голосовые."
    )


# ==== Сообщения от пользователей (кроме команд) ====
@dp.message(
    F.chat.type == "private",
    ~F.from_user.id.in_(ADMIN_IDS),
    ~F.text.startswith("/"),
)
async def user_message(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "—"
    full_name = message.from_user.full_name

    header = (
        f"📩 <b>Новое обращение</b>\n"
        f"👤 {full_name} (@{username})\n"
        f"🆔 <code>{user_id}</code>\n"
        f"———\n"
        f"↩️ <i>Ответьте Reply на это сообщение</i>"
    )

    delivered = False
    for admin_id in ADMIN_IDS:
        try:
            sent = await bot.send_message(admin_id, header)
            copied = await message.copy_to(admin_id)
            pending[sent.message_id] = user_id
            pending[copied.message_id] = user_id
            delivered = True
        except Exception as e:
            logging.warning(f"Не смог отправить админу {admin_id}: {e}")

    if delivered:
        await message.answer("✅ Сообщение отправлено. Ожидайте ответа 🙌")
    else:
        await message.answer("⚠️ Не удалось отправить сообщение. Попробуйте позже.")


# ==== Ответ админа через Reply ====
@dp.message(F.from_user.id.in_(ADMIN_IDS), F.reply_to_message)
async def admin_reply(message: types.Message):
    replied_id = message.reply_to_message.message_id
    user_id = pending.get(replied_id)

    if not user_id:
        await message.answer("⚠️ Не могу определить получателя. Ответьте Reply на сообщение пользователя.")
        return

    try:
        await message.copy_to(user_id)
        await message.answer("✅ Ответ отправлен.")
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки: {e}")


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
