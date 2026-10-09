import os, asyncio, logging
from datetime import datetime, timezone
from decimal import Decimal

import asyncpg
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, ChatJoinRequest,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8916803464:AAGKBpOm2GYrEtbuHym4H4h0Tq_IGyo58FU")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://neondb_owner:npg_7UJskMrjKNi6@ep-fancy-mud-b5w296rq-pooler.c-7.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require")
ADMIN_ID = 8786951363                      # <-- ТВОЙ TELEGRAM ID
CHANNEL = "@hissbyte"                 # канал для обязательной подписки (@username или -100...)
CHANNEL_URL = "https://t.me/+tNrWwB8DXfY3OTQ5l" # ссылка на этот канал
PRICE = Decimal("0.2")                    # цена за 1 заявку, $
# ===============================================

logging.basicConfig(level=logging.INFO)
bot = Bot(BOT_TOKEN)
dp = Dispatcher()
pool: asyncpg.Pool = None


class AdminState(StatesGroup):
    reason = State()
    link = State()


# ---------- клавиатуры ----------
menu_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="👤 Профиль")]], resize_keyboard=True
)

def sub_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")],
    ])

apply_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📝 Подать заявку", callback_data="apply")]
])

def admin_kb(uid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"ok:{uid}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"no:{uid}"),
    ]])


# ---------- помощники ----------
async def is_sub(uid: int) -> bool:
    try:
        m = await bot.get_chat_member(CHANNEL, uid)
        return m.status in ("member", "administrator", "creator")
    except Exception as e:
        logging.warning("sub check failed: %s", e)
        return False


async def send_profile(m: Message):
    u = await pool.fetchrow("SELECT * FROM users WHERE id=$1", m.from_user.id)
    if not u or u["status"] != "approved":
        return await m.answer("Профиль доступен после одобрения заявки.")
    cnt = await pool.fetchval("SELECT COUNT(*) FROM referrals WHERE owner_id=$1", u["id"])
    days = (datetime.now(timezone.utc) - u["created_at"]).days
    nick = f"@{u['username']}" if u["username"] else u["name"]
    await m.answer(
        f"👤 <b>Ваш профиль</b>\n\n"
        f"Ник: {nick}\n"
        f"ID: <code>{u['id']}</code>\n"
        f"💰 Баланс: <b>{u['balance']:.2f}$</b>\n"
        f"📅 Дней с нами: {days}\n"
        f"👥 Приведено людей (заявок): {cnt}\n"
        f"🔗 Ссылка на канал для проверки:\n{u['link']}",
        parse_mode="HTML", disable_web_page_preview=True,
    )


async def show_start(m: Message):
    u = await pool.fetchrow("SELECT status FROM users WHERE id=$1", m.from_user.id)
    st = u["status"]
    if st == "approved":
        await m.answer("С возвращением! 👇", reply_markup=menu_kb)
    elif st == "pending":
        await m.answer("⏳ Ваша заявка на рассмотрении. Ожидайте ответа.")
    else:
        await m.answer("Подайте заявку 👇", reply_markup=apply_kb)


# ---------- пользователь ----------
@dp.message(CommandStart())
async def start(m: Message):
    await pool.execute(
        """INSERT INTO users(id, username, name) VALUES($1,$2,$3)
           ON CONFLICT (id) DO UPDATE SET username=$2, name=$3""",
        m.from_user.id, m.from_user.username, m.from_user.full_name,
    )
    if not await is_sub(m.from_user.id):
        return await m.answer(
            "Перед использованием бота подпишитесь на наш канал 👇", reply_markup=sub_kb()
        )
    await show_start(m)


@dp.callback_query(F.data == "check_sub")
async def check_sub(c: CallbackQuery):
    if not await is_sub(c.from_user.id):
        return await c.answer("Вы ещё не подписались!", show_alert=True)
    await c.message.delete()
    await show_start(c.message.model_copy(update={"from_user": c.from_user}))
    await c.answer()


@dp.callback_query(F.data == "apply")
async def apply(c: CallbackQuery):
    if not await is_sub(c.from_user.id):
        return await c.answer("Сначала подпишитесь на канал!", show_alert=True)
    st = await pool.fetchval("SELECT status FROM users WHERE id=$1", c.from_user.id)
    if st in ("pending", "approved"):
        return await c.answer("Заявка уже подана.", show_alert=True)
    await pool.execute("UPDATE users SET status='pending' WHERE id=$1", c.from_user.id)
    u = c.from_user
    await bot.send_message(
        ADMIN_ID,
        f"📩 <b>Новая заявка</b>\n\nИмя: {u.full_name}\n"
        f"Username: @{u.username}\nID: <code>{u.id}</code>",
        parse_mode="HTML", reply_markup=admin_kb(u.id),
    )
    await c.message.edit_text("✅ Заявка отправлена. Ожидайте решения администратора.")
    await c.answer()


@dp.message(F.text == "👤 Профиль")
@dp.message(Command("profile"))
async def profile(m: Message):
    await send_profile(m)


# ---------- админ ----------
@dp.callback_query(F.data.startswith("ok:"))
async def ok(c: CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID:
        return await c.answer()
    uid = int(c.data.split(":")[1])
    await state.set_state(AdminState.link)
    await state.update_data(uid=uid)
    await c.message.answer(f"Отправьте ссылку-приглашение (с заявками на вступление) для {uid}:")
    await c.answer()


@dp.callback_query(F.data.startswith("no:"))
async def no(c: CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID:
        return await c.answer()
    uid = int(c.data.split(":")[1])
    await state.set_state(AdminState.reason)
    await state.update_data(uid=uid)
    await c.message.answer(f"Напишите причину отказа для {uid}:")
    await c.answer()


@dp.message(AdminState.link, F.from_user.id == ADMIN_ID)
async def got_link(m: Message, state: FSMContext):
    uid = (await state.get_data())["uid"]
    link = m.text.strip()
    await pool.execute("UPDATE users SET status='approved', link=$2 WHERE id=$1", uid, link)
    await state.clear()
    await bot.send_message(
        uid, f"✅ <b>Доступ открыт!</b>\n\nВаша ссылка:\n{link}",
        parse_mode="HTML", reply_markup=menu_kb, disable_web_page_preview=True,
    )
    await m.answer("Готово, доступ выдан.")


@dp.message(AdminState.reason, F.from_user.id == ADMIN_ID)
async def got_reason(m: Message, state: FSMContext):
    uid = (await state.get_data())["uid"]
    await pool.execute("UPDATE users SET status='rejected' WHERE id=$1", uid)
    await state.clear()
    await bot.send_message(
        uid, f"❌ Ваша заявка отклонена.\n\nПричина: {m.text}", reply_markup=apply_kb
    )
    await m.answer("Отказ отправлен.")


# ---------- подсчёт заявок на вступление ----------
@dp.chat_join_request()
async def join_request(e: ChatJoinRequest):
    if not e.invite_link:
        return
    link = e.invite_link.invite_link.strip()
    rows = await pool.fetch("SELECT id, link FROM users WHERE status='approved' AND link IS NOT NULL")
    owner = None
    for r in rows:
        saved = r["link"].strip()
        if saved == link or ("..." in link and saved.startswith(link.split("...")[0])):
            owner = r["id"]
            break
    if not owner:
        return
    ins = await pool.fetchval(
        """INSERT INTO referrals(owner_id, applicant_id) VALUES($1,$2)
           ON CONFLICT DO NOTHING RETURNING 1""",
        owner, e.from_user.id,
    )
    if ins:
        await pool.execute("UPDATE users SET balance = balance + $2 WHERE id=$1", owner, PRICE)
        try:
            await bot.send_message(owner, f"💸 Новая заявка по вашей ссылке! +{PRICE}$")
        except Exception:
            pass


# ---------- веб-сервер для UptimeRobot + запуск ----------
async def health(_):
    return web.Response(text="OK")


async def main():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id BIGINT PRIMARY KEY, username TEXT, name TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            link TEXT, balance NUMERIC(12,2) NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now());
        CREATE TABLE IF NOT EXISTS referrals(
            owner_id BIGINT, applicant_id BIGINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY(owner_id, applicant_id));
    """)
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 10000))).start()

    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "chat_join_request"])


if __name__ == "__main__":
    asyncio.run(main())
