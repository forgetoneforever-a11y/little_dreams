import asyncio
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timedelta
from urllib.parse import parse_qsl

from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, CallbackQuery,
    BotCommandScopeDefault, BotCommandScopeChat, MenuButtonCommands, WebAppInfo
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from config import (
    BOT_TOKEN, ADMIN_IDS, WEBHOOK_URL, WEBHOOK_PATH, PORT, WEBHOOK_HOST,
    AUTO_REPLY_MINUTES, ANTIFLOOD_LIMIT, ANTIFLOOD_WINDOW, ANTIFLOOD_MUTE,
    ANTIFLOOD_STRIKES_TO_BAN, RULES_TEXT
)
import database as db
from faq import match_faq

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ===== Состояние в памяти =====
flood: dict[int, list[float]] = {}
muted_until: dict[int, float] = {}
strikes: dict[int, int] = {}
pending: dict[int, int] = {}
awaiting_reply: dict[int, float] = {}

# ===== Папка с WebApp =====
WEBAPP_DIR = os.path.join(os.path.dirname(__file__), "webapp")
WEBAPP_URL = f"{WEBHOOK_HOST}/webapp"


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_flooding(user_id: int) -> bool:
    now = datetime.utcnow().timestamp()
    if muted_until.get(user_id, 0) > now:
        return True
    times = flood.get(user_id, [])
    times = [t for t in times if now - t < ANTIFLOOD_WINDOW]
    times.append(now)
    flood[user_id] = times
    if len(times) > ANTIFLOOD_LIMIT:
        muted_until[user_id] = now + ANTIFLOOD_MUTE
        strikes[user_id] = strikes.get(user_id, 0) + 1
        return True
    return False


# ================== СТАРТ ==================

@dp.message(CommandStart(), ~F.from_user.id.in_(ADMIN_IDS))
async def user_start(message: types.Message):
    if db.is_banned(message.from_user.id):
        await message.answer("🚫 Вы заблокированы в этом боте.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🚀 Открыть приложение",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )],
        [InlineKeyboardButton(text="📩 Написать в поддержку", callback_data="write_support")],
        [InlineKeyboardButton(text="📜 Правила", callback_data="rules")],
        [InlineKeyboardButton(text="👤 Мой ID", callback_data="whoami")],
    ])
    await message.answer(
        "👋 <b>Добро пожаловать в Little Dreams!</b>\n\n"
        "Это служба поддержки 💭\n\n"
        "Откройте мини-приложение 🚀 или напишите сообщение прямо здесь.",
        reply_markup=kb
    )


@dp.message(CommandStart(), F.from_user.id.in_(ADMIN_IDS))
async def admin_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🚀 Открыть приложение",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )],
    ])
    await message.answer(
        "👋 <b>Режим администратора</b>\n\n"
        "📌 <b>Команды:</b>\n"
        "/tickets — открытые обращения\n"
        "/reply &lt;id&gt; &lt;текст&gt; — ответить\n"
        "/close &lt;id&gt; — закрыть обращение\n"
        "/stats — статистика\n"
        "/broadcast &lt;текст&gt; — рассылка\n"
        "/ban &lt;id&gt; [причина] — забанить\n"
        "/unban &lt;id&gt; — разбанить\n"
        "/mute &lt;id&gt; &lt;минуты&gt; — тайм-аут\n"
        "/unmute &lt;id&gt; — снять тайм-аут\n"
        "/banned — список заблокированных\n"
        "/rules — правила\n\n"
        "💡 Или <b>Reply</b> на пересланное сообщение.",
        reply_markup=kb
    )


@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    if is_admin(message.from_user.id):
        await message.answer(
            "📌 <b>Команды админа:</b>\n"
            "/tickets, /reply, /close\n"
            "/stats, /broadcast, /rules\n"
            "/ban &lt;id&gt;, /unban &lt;id&gt;\n"
            "/mute &lt;id&gt; &lt;мин&gt;, /unmute &lt;id&gt;\n"
            "/banned"
        )
    else:
        await message.answer(
            "❓ <b>Помощь</b>\n\n"
            "Просто напишите ваш вопрос — оператор ответит здесь же.\n\n"
            "📜 /rules — правила поддержки"
        )


@dp.message(Command("rules"))
async def rules_cmd(message: types.Message):
    await message.answer(RULES_TEXT)


@dp.message(Command("whoami"))
async def whoami(message: types.Message):
    u = message.from_user
    await message.answer(
        f"🆔 Ваш ID: <code>{u.id}</code>\n"
        f"👤 Имя: {u.full_name}\n"
        f"🔗 Username: @{u.username or '—'}"
    )


# ================== CALLBACK-КНОПКИ ==================

@dp.callback_query(F.data == "write_support")
async def cb_write(call: CallbackQuery):
    await call.message.answer("✍️ Напишите ваше сообщение — отправим в поддержку.")
    await call.answer()


@dp.callback_query(F.data == "rules")
async def cb_rules(call: CallbackQuery):
    await call.message.answer(RULES_TEXT)
    await call.answer()


@dp.callback_query(F.data == "whoami")
async def cb_whoami(call: CallbackQuery):
    u = call.from_user
    await call.message.answer(
        f"🆔 Ваш ID: <code>{u.id}</code>\n👤 {u.full_name}\n🔗 @{u.username or '—'}"
    )
    await call.answer()


# ================== WEBAPP DATA (fallback) ==================

@dp.message(F.web_app_data)
async def webapp_data(message: types.Message):
    """Fallback: если WebApp прислал данные через tg.sendData()."""
    try:
        data = json.loads(message.web_app_data.data)
    except Exception as e:
        logging.warning(f"WebApp: ошибка парсинга: {e}")
        return

    action = data.get("action")
    user = message.from_user

    if action == "message":
        text = (data.get("text") or "").strip()
        if not text:
            return

        if db.is_banned(user.id):
            await message.answer("🚫 Вы заблокированы.")
            return

        db.upsert_user(user.id, user.username or "", user.full_name)
        ticket = db.get_open_ticket(user.id)
        if ticket:
            ticket_id = ticket["id"]
            db.update_ticket_activity(ticket_id, text)
        else:
            ticket_id = db.create_ticket(user.id, user.username or "", user.full_name, text)

        db.add_message(ticket_id, user.id, text, is_admin=False)
        awaiting_reply[ticket_id] = datetime.utcnow().timestamp()

        header = (
            f"📩 <b>Обращение #{ticket_id:04d}</b> <i>(WebApp)</i>\n"
            f"👤 {user.full_name} (@{user.username or '—'})\n"
            f"🆔 <code>{user.id}</code>\n"
            f"———\n{text}\n\n"
            f"↩️ Reply или /reply {ticket_id} &lt;текст&gt;"
        )
        for admin_id in ADMIN_IDS:
            try:
                sent = await bot.send_message(admin_id, header)
                pending[sent.message_id] = ticket_id
            except Exception as e:
                logging.warning(f"WebApp → админ {admin_id}: {e}")

        await message.answer(
            f"✅ Отправлено из приложения.\nОбращение: <b>#{ticket_id:04d}</b>"
        )


# ================== WEBAPP API ==================

def validate_init_data(init_data: str) -> dict | None:
    """Проверяет подпись initData от Telegram. Возвращает user dict или None."""
    if not init_data:
        return None
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", "")
        if not received_hash:
            return None

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(
            b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256
        ).digest()
        computed_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            logging.warning("WebApp: неверная подпись initData")
            return None

        user_data = json.loads(parsed.get("user", "{}"))
        if not user_data.get("id"):
            return None
        return user_data
    except Exception as e:
        logging.warning(f"WebApp validate error: {e}")
        return None


async def api_profile(request):
    """GET /api/profile?initData=..."""
    init_data = request.query.get("initData", "")
    user = validate_init_data(init_data)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    db.upsert_user(
        user["id"],
        user.get("username", "") or "",
        (user.get("first_name", "") + " " + user.get("last_name", "")).strip()
    )
    return web.json_response({
        "ok": True,
        "user": {
            "id": user["id"],
            "first_name": user.get("first_name", ""),
            "username": user.get("username", ""),
        }
    })


async def api_tickets(request):
    """GET /api/tickets?initData=..."""
    init_data = request.query.get("initData", "")
    user = validate_init_data(init_data)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    tickets = db.get_user_tickets(user["id"], limit=10)
    return web.json_response({
        "ok": True,
        "tickets": [
            {
                "id": t["id"],
                "status": t["status"],
                "last_message": t["last_message"] or "",
                "last_activity": (t["last_activity"] or "")[:16],
            }
            for t in tickets
        ]
    })


async def api_message(request):
    """POST /api/message. Body: {initData, text}."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)

    init_data = body.get("initData", "")
    text = (body.get("text") or "").strip()
    if not text:
        return web.json_response({"ok": False, "error": "empty text"}, status=400)

    user = validate_init_data(init_data)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    user_id = user["id"]
    username = user.get("username", "") or ""
    full_name = (user.get("first_name", "") + " " + user.get("last_name", "")).strip()

    if db.is_banned(user_id):
        return web.json_response({"ok": False, "error": "banned"}, status=403)

    if is_flooding(user_id):
        return web.json_response({"ok": False, "error": "flood"}, status=429)

    db.upsert_user(user_id, username, full_name)

    ticket = db.get_open_ticket(user_id)
    if ticket:
        ticket_id = ticket["id"]
        db.update_ticket_activity(ticket_id, text)
    else:
        ticket_id = db.create_ticket(user_id, username, full_name, text)

    db.add_message(ticket_id, user_id, text, is_admin=False)
    awaiting_reply[ticket_id] = datetime.utcnow().timestamp()

    faq_answer = match_faq(text)

    header = (
        f"📩 <b>Обращение #{ticket_id:04d}</b> <i>(из WebApp)</i>\n"
        f"👤 {full_name} (@{username or '—'})\n"
        f"🆔 <code>{user_id}</code>\n"
        f"———\n{text}\n\n"
        f"↩️ Reply или /reply {ticket_id} &lt;текст&gt;\n"
        f"/close {ticket_id} — закрыть"
    )

    delivered = False
    for admin_id in ADMIN_IDS:
        try:
            sent = await bot.send_message(admin_id, header)
            pending[sent.message_id] = ticket_id
            delivered = True
        except Exception as e:
            logging.warning(f"WebApp API → админ {admin_id}: {e}")

    # Уведомляем пользователя в чате
    try:
        if faq_answer:
            await bot.send_message(
                user_id, f"🤖 <b>Быстрый ответ:</b>\n\n{faq_answer}"
            )
        await bot.send_message(
            user_id,
            f"✅ Сообщение отправлено из приложения.\nОбращение: <b>#{ticket_id:04d}</b>"
        )
    except Exception as e:
        logging.warning(f"WebApp API → пользователь {user_id}: {e}")

    return web.json_response({
        "ok": True,
        "ticket_id": ticket_id,
        "faq": faq_answer,
        "delivered": delivered,
    })


# ================== СООБЩЕНИЯ ОТ ПОЛЬЗОВАТЕЛЕЙ ==================

@dp.message(
    F.chat.type == "private",
    ~F.from_user.id.in_(ADMIN_IDS),
    ~F.text.startswith("/"),
)
async def user_message(message: types.Message):
    user = message.from_user

    if db.is_banned(user.id):
        b = db.get_ban(user.id)
        if b and b["type"] == "mute":
            try:
                until = datetime.fromisoformat(b["reason"])
                mins = int((until - datetime.utcnow()).total_seconds() / 60) + 1
                await message.answer(f"⏳ Вы в тайм-ауте. Осталось ~{mins} мин.")
            except Exception:
                await message.answer("⏳ Вы в тайм-ауте.")
        else:
            await message.answer("🚫 Вы заблокированы в этом боте.")
        return

    if is_flooding(user.id):
        n = strikes.get(user.id, 0)
        if n >= ANTIFLOOD_STRIKES_TO_BAN:
            until = (datetime.utcnow() + timedelta(minutes=ANTIFLOOD_MUTE * 5)).isoformat()
            db.ban_user(user.id, until, bot.id, kind="mute")
            await message.answer("🚫 Превышен лимит. Временная блокировка.")
        else:
            await message.answer("⏳ Слишком много сообщений. Подождите минуту.")
        return

    db.upsert_user(user.id, user.username or "", user.full_name)

    text = message.text or message.caption or "📎 Вложение"
    ticket = db.get_open_ticket(user.id)

    if ticket:
        ticket_id = ticket["id"]
        db.update_ticket_activity(ticket_id, text)
    else:
        ticket_id = db.create_ticket(user.id, user.username or "", user.full_name, text)

    db.add_message(ticket_id, user.id, text, is_admin=False)
    awaiting_reply[ticket_id] = datetime.utcnow().timestamp()

    faq_answer = match_faq(text)
    if faq_answer:
        await message.answer(f"🤖 <b>Быстрый ответ:</b>\n\n{faq_answer}")
        await asyncio.sleep(0.5)

    header = (
        f"📩 <b>Обращение #{ticket_id:04d}</b>\n"
        f"👤 {user.full_name} (@{user.username or '—'})\n"
        f"🆔 <code>{user.id}</code>\n"
        f"———\n"
        f"↩️ Reply или /reply {ticket_id} &lt;текст&gt;\n"
        f"/close {ticket_id} — закрыть"
    )

    delivered = False
    for admin_id in ADMIN_IDS:
        try:
            sent = await bot.send_message(admin_id, header)
            copied = await message.copy_to(admin_id)
            pending[sent.message_id] = ticket_id
            pending[copied.message_id] = ticket_id
            delivered = True
        except Exception as e:
            logging.warning(f"Не смог отправить админу {admin_id}: {e}")

    if delivered:
        await message.answer(f"✅ Отправлено. Номер обращения: <b>#{ticket_id:04d}</b>")
    else:
        await message.answer("⚠️ Не удалось отправить. Попробуйте позже.")


# ================== КОМАНДЫ АДМИНА ==================

@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("tickets"))
async def cmd_tickets(message: types.Message):
    tickets = db.get_open_tickets()
    if not tickets:
        await message.answer("📭 Открытых обращений нет.")
        return
    lines = [f"📋 <b>Открытые обращения ({len(tickets)}):</b>\n"]
    for t in tickets[:20]:
        lines.append(
            f"<b>#{t['id']:04d}</b> | @{t['username'] or '—'} | <code>{t['user_id']}</code>\n"
            f"  └ {(t['last_message'] or '')[:70]}\n"
            f"  🕐 {t['last_activity'][:16]}"
        )
    await message.answer("\n\n".join(lines))


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("reply"))
async def cmd_reply(message: types.Message):
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await message.answer("Использование: <code>/reply &lt;id&gt; &lt;текст&gt;</code>")
        return

    ticket_id = int(parts[1])
    text = parts[2]

    ticket = db.get_ticket(ticket_id)
    if not ticket:
        await message.answer(f"❌ Обращение #{ticket_id:04d} не найдено.")
        return

    try:
        await bot.send_chat_action(ticket["user_id"], ChatAction.TYPING)
        await asyncio.sleep(0.7)
        await bot.send_message(
            ticket["user_id"],
            f"💬 <b>Ответ поддержки</b> (обращение #{ticket_id:04d}):\n\n{text}"
        )
        db.add_message(ticket_id, message.from_user.id, text, is_admin=True)
        awaiting_reply.pop(ticket_id, None)
        await message.answer(f"✅ Ответ по #{ticket_id:04d} отправлен.")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("close"))
async def cmd_close(message: types.Message):
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/close &lt;id&gt;</code>")
        return

    ticket_id = int(parts[1])
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        await message.answer(f"❌ Обращение #{ticket_id:04d} не найдено.")
        return

    db.close_ticket(ticket_id)
    awaiting_reply.pop(ticket_id, None)

    try:
        await bot.send_message(
            ticket["user_id"],
            f"✅ Ваше обращение <b>#{ticket_id:04d}</b> закрыто.\n\n"
            "Спасибо за обращение! Если появятся ещё вопросы — просто напишите снова."
        )
    except Exception as e:
        logging.warning(f"Не смог уведомить пользователя: {e}")

    await message.answer(f"✅ Обращение #{ticket_id:04d} закрыто.")


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("stats"))
async def cmd_stats(message: types.Message):
    s = db.get_stats()
    avg = f"{s['avg_minutes']} мин" if s["avg_minutes"] is not None else "—"
    bans_count = len(db.list_bans())

    lines = [
        "📊 <b>Статистика</b>\n",
        f"📨 Всего обращений: <b>{s['total']}</b>",
        f"📅 За сегодня: <b>{s['today']}</b>",
        f"🟢 Открытых: <b>{s['open']}</b>",
        f"⏱ Среднее время ответа: <b>{avg}</b>",
        f"🚫 Забанено/в муте: <b>{bans_count}</b>\n",
        "🏆 <b>Топ-5 активных:</b>"
    ]
    if s["top_users"]:
        for i, u in enumerate(s["top_users"], 1):
            lines.append(f"{i}. @{u['username'] or '—'} — {u['c']} обращений")
    else:
        lines.append("— пока пусто —")

    await message.answer("\n".join(lines))


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("broadcast"))
async def cmd_broadcast(message: types.Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: <code>/broadcast &lt;текст&gt;</code>")
        return

    text = parts[1]
    user_ids = db.all_user_ids()
    ok, fail = 0, 0
    await message.answer(f"📤 Рассылаю {len(user_ids)} пользователям…")

    for uid in user_ids:
        if db.is_banned(uid):
            continue
        try:
            await bot.send_message(uid, f"📢 <b>Сообщение от Little Dreams:</b>\n\n{text}")
            ok += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail += 1

    await message.answer(f"✅ Готово. Доставлено: {ok}, ошибок: {fail}.")


# ================== БАНЫ И МУТЫ ==================

@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("ban"))
async def cmd_ban(message: types.Message):
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/ban &lt;id&gt; [причина]</code>")
        return

    uid = int(parts[1])
    reason = parts[2] if len(parts) > 2 else "без причины"

    if uid in ADMIN_IDS:
        await message.answer("❌ Нельзя забанить админа.")
        return

    db.ban_user(uid, reason, message.from_user.id, kind="ban")

    try:
        await bot.send_message(uid, f"🚫 Вы заблокированы.\nПричина: {reason}")
    except Exception:
        pass

    await message.answer(f"✅ Пользователь <code>{uid}</code> заблокирован.")


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("unban"))
async def cmd_unban(message: types.Message):
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/unban &lt;id&gt;</code>")
        return

    uid = int(parts[1])
    db.unban_user(uid)

    try:
        await bot.send_message(uid, "✅ Блокировка снята. Можете писать снова.")
    except Exception:
        pass

    await message.answer(f"✅ Пользователь <code>{uid}</code> разбанен.")


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("mute"))
async def cmd_mute(message: types.Message):
    parts = (message.text or "").split()
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer("Использование: <code>/mute &lt;id&gt; &lt;минуты&gt;</code>")
        return

    uid = int(parts[1])
    minutes = int(parts[2])

    if uid in ADMIN_IDS:
        await message.answer("❌ Нельзя замутить админа.")
        return

    until = (datetime.utcnow() + timedelta(minutes=minutes)).isoformat()
    db.ban_user(uid, until, message.from_user.id, kind="mute")

    try:
        await bot.send_message(uid, f"⏳ Тайм-аут на {minutes} мин.")
    except Exception:
        pass

    await message.answer(f"✅ Пользователь <code>{uid}</code> в тайм-ауте {minutes} мин.")


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("unmute"))
async def cmd_unmute(message: types.Message):
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/unmute &lt;id&gt;</code>")
        return

    uid = int(parts[1])
    db.unban_user(uid)

    try:
        await bot.send_message(uid, "✅ Тайм-аут снят.")
    except Exception:
        pass

    await message.answer(f"✅ Тайм-аут с <code>{uid}</code> снят.")


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("banned"))
async def cmd_banned(message: types.Message):
    bans = db.list_bans()
    if not bans:
        await message.answer("📭 Список пуст.")
        return

    lines = [f"🚫 <b>Заблокированные ({len(bans)}):</b>\n"]
    for b in bans[:30]:
        icon = "🚫" if b["type"] == "ban" else "⏳"
        reason = b["reason"]
        if b["type"] == "mute":
            try:
                until = datetime.fromisoformat(reason)
                mins = int((until - datetime.utcnow()).total_seconds() / 60)
                reason = f"ещё ~{mins} мин" if mins > 0 else "истёк"
            except Exception:
                pass
        lines.append(f"{icon} <code>{b['user_id']}</code> — {reason}")

    await message.answer("\n".join(lines))


# ================== REPLY-ОТВЕТ АДМИНА ==================

@dp.message(F.from_user.id.in_(ADMIN_IDS), F.reply_to_message)
async def admin_reply(message: types.Message):
    replied_id = message.reply_to_message.message_id
    ticket_id = pending.get(replied_id)

    if not ticket_id:
        await message.answer("⚠️ Не могу определить обращение. Ответьте Reply на сообщение пользователя.")
        return

    ticket = db.get_ticket(ticket_id)
    if not ticket:
        await message.answer("⚠️ Обращение не найдено.")
        return

    try:
        await bot.send_chat_action(ticket["user_id"], ChatAction.TYPING)
        await asyncio.sleep(0.7)
        await message.copy_to(ticket["user_id"])

        db.add_message(ticket_id, message.from_user.id, message.text or "📎", is_admin=True)
        awaiting_reply.pop(ticket_id, None)

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"✅ Решено (закрыть #{ticket_id:04d})",
                callback_data=f"close_ticket:{ticket_id}"
            )]
        ])
        await message.answer(f"✅ Ответ по #{ticket_id:04d} отправлен.", reply_markup=kb)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.callback_query(F.data.startswith("close_ticket:"))
async def cb_close_ticket(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return

    ticket_id = int(call.data.split(":")[1])
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        await call.answer("Обращение не найдено", show_alert=True)
        return

    db.close_ticket(ticket_id)
    awaiting_reply.pop(ticket_id, None)

    try:
        await bot.send_message(
            ticket["user_id"],
            f"✅ Ваше обращение <b>#{ticket_id:04d}</b> закрыто.\n\n"
            "Спасибо за обращение! Если появятся ещё вопросы — просто напишите снова."
        )
    except Exception as e:
        logging.warning(f"Не смог уведомить пользователя: {e}")

    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(f"✅ Обращение #{ticket_id:04d} закрыто.")
    await call.answer("Закрыто")


# ================== ФОНОВАЯ ЗАДАЧА ==================

async def auto_reply_worker():
    while True:
        await asyncio.sleep(60)
        now = datetime.utcnow().timestamp()
        to_notify = []
        for ticket_id, ts in list(awaiting_reply.items()):
            if now - ts >= AUTO_REPLY_MINUTES * 60:
                to_notify.append(ticket_id)
                awaiting_reply.pop(ticket_id, None)

        for ticket_id in to_notify:
            ticket = db.get_ticket(ticket_id)
            if not ticket or ticket["status"] != "open":
                continue
            try:
                await bot.send_message(
                    ticket["user_id"],
                    "⏳ Ваше обращение получено, оператор скоро ответит.\n"
                    "Спасибо за терпение!"
                )
            except Exception as e:
                logging.warning(f"Автоответ не доставлен: {e}")


# ================== МЕНЮ КОМАНД ==================

USER_COMMANDS = [
    BotCommand(command="start", description="🏠 Начать"),
    BotCommand(command="help", description="❓ Помощь"),
    BotCommand(command="rules", description="📜 Правила"),
    BotCommand(command="whoami", description="👤 Мой ID"),
]

ADMIN_COMMANDS = [
    BotCommand(command="start", description="🏠 Начать"),
    BotCommand(command="tickets", description="📋 Открытые обращения"),
    BotCommand(command="reply", description="💬 Ответ: /reply <id> <текст>"),
    BotCommand(command="close", description="✅ Закрыть: /close <id>"),
    BotCommand(command="stats", description="📊 Статистика"),
    BotCommand(command="broadcast", description="📢 Рассылка"),
    BotCommand(command="ban", description="🚫 Забанить: /ban <id> [причина]"),
    BotCommand(command="unban", description="♻️ Разбанить: /unban <id>"),
    BotCommand(command="mute", description="⏳ Тайм-аут: /mute <id> <мин>"),
    BotCommand(command="unmute", description="🔊 Снять тайм-аут"),
    BotCommand(command="banned", description="📕 Список заблокированных"),
    BotCommand(command="rules", description="📜 Правила"),
]


async def setup_commands():
    try:
        await bot.set_my_commands(USER_COMMANDS, scope=BotCommandScopeDefault())
    except Exception as e:
        logging.warning(f"set_my_commands default: {e}")

    for admin_id in ADMIN_IDS:
        try:
            await bot.set_my_commands(
                ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except Exception as e:
            logging.warning(f"set_my_commands admin {admin_id}: {e}")

    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonCommands(type="commands")
        )
    except Exception as e:
        logging.warning(f"Menu button global: {e}")

    for admin_id in ADMIN_IDS:
        try:
            await bot.set_chat_menu_button(
                chat_id=admin_id,
                menu_button=MenuButtonCommands(type="commands")
            )
        except Exception as e:
            logging.warning(f"Menu button admin {admin_id}: {e}")


# ================== WEBAPP — ОТДАЧА ФАЙЛОВ ==================

async def serve_webapp(request):
    filepath = os.path.join(WEBAPP_DIR, "index.html")
    if not os.path.exists(filepath):
        return web.Response(status=404, text="index.html not found")
    return web.FileResponse(filepath)


async def serve_webapp_file(request):
    filename = request.match_info.get("filename", "")
    filepath = os.path.join(WEBAPP_DIR, filename)
    if not os.path.abspath(filepath).startswith(os.path.abspath(WEBAPP_DIR)):
        return web.Response(status=403, text="Forbidden")
    if not os.path.exists(filepath):
        return web.Response(status=404, text="Not found")
    return web.FileResponse(filepath)


# ================== ЗАПУСК ==================

async def on_startup(bot: Bot):
    db.init_db()
    logging.info(f"🚀 ADMIN_IDS = {ADMIN_IDS}")
    logging.info(f"🚀 WEBHOOK_URL = {WEBHOOK_URL}")
    logging.info(f"🚀 WEBAPP_URL = {WEBAPP_URL}")

    await bot.set_webhook(WEBHOOK_URL)
    await setup_commands()

    asyncio.create_task(auto_reply_worker())
    logging.info("✅ Webhook + меню + WebApp + API готовы")


def main():
    dp.startup.register(on_startup)
    app = web.Application()

    # Webhook для Telegram
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)

    # Мини-приложение (WebApp)
    app.router.add_get("/webapp", serve_webapp)
    app.router.add_get("/webapp/", serve_webapp)
    app.router.add_get("/webapp/{filename}", serve_webapp_file)

    # API для WebApp
    app.router.add_get("/api/profile", api_profile)
    app.router.add_get("/api/tickets", api_tickets)
    app.router.add_post("/api/message", api_message)

    setup_application(app, dp, bot=bot)
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
