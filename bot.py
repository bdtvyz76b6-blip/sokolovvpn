import os
import sqlite3
import random
import base64
import asyncio
from datetime import datetime, timedelta, timezone
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    LabeledPrice,
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
# ============================================================
# TALKING VPN
# ============================================================
BOT_NAME = "Talking VPN"
# Render variables — нужны только эти 3
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except ValueError:
    ADMIN_ID = 0
# ============================================================
# НАСТРОЙКИ
# ============================================================
GITHUB_OWNER = "bdtvyz76b6-blip"
GITHUB_REPO = "sokolovvpn"
GITHUB_BRANCH = "main"
SERVERS_FILE = "servers.txt"
# URL твоего Render-сервиса
PUBLIC_URL = "https://sokolovvpn.onrender.com"
PRICE_STARS = 100
SUBSCRIPTION_DAYS = 30
# Сколько серверов получает один пользователь
SERVERS_PER_USER = 5
# Локальная SQLite база
DB_FILE = "talking_vpn.sqlite3"
# Render Web Service
PORT = 10000
VERSION = "TALKING-VPN-1.0"
# ============================================================
# DATABASE
# ============================================================
def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn
def init_db():
    conn = db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INTEGER PRIMARY KEY,
            expires_at TEXT NOT NULL,
            sub_token TEXT UNIQUE NOT NULL,
            content TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
def save_user(user_id: int, username: str = "", first_name: str = ""):
    conn = db()
    conn.execute("""
        INSERT INTO users (
            user_id,
            username,
            first_name,
            created_at
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name
    """, (
        user_id,
        username or "",
        first_name or "",
        datetime.now(timezone.utc).isoformat()
    ))
    conn.commit()
    conn.close()
def get_subscription(user_id: int):
    conn = db()
    row = conn.execute("""
        SELECT *
        FROM subscriptions
        WHERE user_id = ?
    """, (user_id,)).fetchone()
    conn.close()
    return row
def get_active_subscriptions():
    conn = db()
    rows = conn.execute("""
        SELECT *
        FROM subscriptions
    """).fetchall()
    conn.close()
    now = datetime.now(timezone.utc)
    result = []
    for row in rows:
        try:
            expires = datetime.fromisoformat(row["expires_at"])
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires > now:
                result.append(row)
        except Exception:
            pass
    return result
def create_or_update_subscription(
    user_id: int,
    expires_at: datetime,
    content: str,
):
    conn = db()
    old = conn.execute("""
        SELECT sub_token
        FROM subscriptions
        WHERE user_id = ?
    """, (user_id,)).fetchone()
    if old:
        sub_token = old["sub_token"]
    else:
        sub_token = generate_token()
    conn.execute("""
        INSERT INTO subscriptions (
            user_id,
            expires_at,
            sub_token,
            content
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            expires_at = excluded.expires_at,
            content = excluded.content
    """, (
        user_id,
        expires_at.isoformat(),
        sub_token,
        content
    ))
    conn.commit()
    conn.close()
    return sub_token
def generate_token():
    raw = os.urandom(24)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")
# ============================================================
# GITHUB
# ============================================================
def github_raw_url():
    return (
        f"https://raw.githubusercontent.com/"
        f"{GITHUB_OWNER}/"
        f"{GITHUB_REPO}/"
        f"{GITHUB_BRANCH}/"
        f"{SERVERS_FILE}"
    )
async def fetch_servers():
    url = github_raw_url()
    headers = {
        "User-Agent": "Talking-VPN"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                text = await response.text()
                raise RuntimeError(
                    f"GitHub HTTP {response.status}: {text[:300]}"
                )
            text = await response.text()
    servers = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line not in servers:
            servers.append(line)
    if not servers:
        raise RuntimeError("servers.txt пустой")
    return servers
# ============================================================
# SUBSCRIPTION CONTENT
# ============================================================
def make_subscription_content(servers):
    """
    Telegram VPN subscription:
    обычный список ссылок кодируется в Base64.
    """
    selected = list(servers)
    if len(selected) > SERVERS_PER_USER:
        selected = random.sample(selected, SERVERS_PER_USER)
    raw = "\n".join(selected)
    encoded = base64.b64encode(
        raw.encode("utf-8")
    ).decode("ascii")
    return encoded
# ============================================================
# BOT KEYBOARDS
# ============================================================
def main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(
        text="⭐ Купить VPN",
        callback_data="buy"
    )
    builder.button(
        text="📱 Моя подписка",
        callback_data="subscription"
    )
    builder.button(
        text="ℹ️ Помощь",
        callback_data="help"
    )
    builder.adjust(1)
    return builder.as_markup()
def admin_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔄 Обновить серверы",
        callback_data="admin_refresh"
    )
    builder.button(
        text="📊 Статистика",
        callback_data="admin_stats"
    )
    builder.adjust(1)
    return builder.as_markup()
# ============================================================
# TEXT
# ============================================================
def welcome_text():
    return (
        f"🚀 <b>{BOT_NAME}</b>\n\n"
        "Быстрый VPN с подпиской на 30 дней.\n\n"
        f"⭐ Стоимость: <b>{PRICE_STARS} Stars</b>\n"
        f"📅 Срок: <b>{SUBSCRIPTION_DAYS} дней</b>\n\n"
        "Нажми кнопку ниже, чтобы оформить подписку."
    )
def help_text():
    return (
        f"ℹ️ <b>{BOT_NAME}</b>\n\n"
        "1. Нажми «Купить VPN».\n"
        "2. Оплати подписку через Telegram Stars.\n"
        "3. После оплаты бот выдаст персональную ссылку.\n\n"
        "Твоя ссылка на подписку остаётся постоянной. "
        "При обновлении серверов её менять не нужно."
    )
# ============================================================
# PAYMENT
# ============================================================
async def send_invoice(bot: Bot, chat_id: int):
    prices = [
        LabeledPrice(
            label=f"{BOT_NAME} — {SUBSCRIPTION_DAYS} дней",
            amount=PRICE_STARS
        )
    ]
    await bot.send_invoice(
        chat_id=chat_id,
        title=f"{BOT_NAME} VPN",
        description=(
            f"VPN-подписка на {SUBSCRIPTION_DAYS} дней. "
            f"Цена: {PRICE_STARS} Telegram Stars."
        ),
        payload=f"vpn_{chat_id}",
        provider_token="",
        currency="XTR",
        prices=prices,
    )
@dp if False else None
def dummy():
    pass
# ============================================================
# BOT
# ============================================================
bot = Bot(BOT_TOKEN)
dp = Dispatcher()
# ============================================================
# START
# ============================================================
@dp.message(Command("start"))
async def start_handler(message: Message):
    user = message.from_user
    save_user(
        user.id,
        user.username or "",
        user.first_name or ""
    )
    text = welcome_text()
    if user.id == ADMIN_ID:
        text += "\n\n🔐 <b>Админ-панель доступна ниже.</b>"
        builder = InlineKeyboardBuilder()
        builder.button(
            text="⭐ Купить VPN",
            callback_data="buy"
        )
        builder.button(
            text="📱 Моя подписка",
            callback_data="subscription"
        )
        builder.button(
            text="ℹ️ Помощь",
            callback_data="help"
        )
        builder.button(
            text="🔧 Админ-панель",
            callback_data="admin"
        )
        builder.adjust(1)
        await message.answer(
            text,
            reply_markup=builder.as_markup()
        )
        return
    await message.answer(
        text,
        reply_markup=main_keyboard()
    )
# ============================================================
# BUY
# ============================================================
@dp.callback_query(F.data == "buy")
async def buy_handler(callback: CallbackQuery):
    await callback.answer()
    try:
        await send_invoice(
            bot,
            callback.from_user.id
        )
    except Exception as e:
        await callback.message.answer(
            f"❌ Не удалось создать оплату.\n\n"
            f"<code>{str(e)[:500]}</code>"
        )
# ============================================================
# PRE-CHECKOUT
# ============================================================
@dp.pre_checkout_query()
async def pre_checkout_handler(query: PreCheckoutQuery):
    await query.answer(ok=True)
# ============================================================
# SUCCESSFUL PAYMENT
# ============================================================
@dp.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    user_id = message.from_user.id
    save_user(
        user_id,
        message.from_user.username or "",
        message.from_user.first_name or ""
    )
    try:
        servers = await fetch_servers()
        content = make_subscription_content(servers)
        old = get_subscription(user_id)
        now = datetime.now(timezone.utc)
        if old:
            try:
                old_expiration = datetime.fromisoformat(
                    old["expires_at"]
                )
                if old_expiration.tzinfo is None:
                    old_expiration = old_expiration.replace(
                        tzinfo=timezone.utc
                    )
            except Exception:
                old_expiration = now
            if old_expiration > now:
                expires_at = (
                    old_expiration +
                    timedelta(days=SUBSCRIPTION_DAYS)
                )
            else:
                expires_at = (
                    now +
                    timedelta(days=SUBSCRIPTION_DAYS)
                )
        else:
            expires_at = (
                now +
                timedelta(days=SUBSCRIPTION_DAYS)
            )
        token = create_or_update_subscription(
            user_id,
            expires_at,
            content
        )
        sub_url = f"{PUBLIC_URL}/sub/{token}"
        await message.answer(
            "✅ <b>Оплата прошла успешно!</b>\n\n"
            f"🚀 <b>{BOT_NAME}</b>\n"
            f"📅 Подписка до: "
            f"<b>{expires_at.strftime('%d.%m.%Y %H:%M')} UTC</b>\n\n"
            "🔗 <b>Твоя ссылка подписки:</b>\n"
            f"<code>{sub_url}</code>\n\n"
            "⚠️ Не передавай эту ссылку другим людям."
        )
    except Exception as e:
        await message.answer(
            "⚠️ Оплата получена, но не удалось сформировать "
            "подписку автоматически.\n\n"
            f"<code>{str(e)[:500]}</code>"
        )
# ============================================================
# MY SUBSCRIPTION
# ============================================================
@dp.callback_query(F.data == "subscription")
async def subscription_handler(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    sub = get_subscription(user_id)
    if not sub:
        await callback.message.answer(
            "📱 У тебя пока нет активной подписки.",
            reply_markup=main_keyboard()
        )
        return
    try:
        expires_at = datetime.fromisoformat(
            sub["expires_at"]
        )
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(
                tzinfo=timezone.utc
            )
    except Exception:
        await callback.message.answer(
            "❌ Не удалось прочитать данные подписки."
        )
        return
    now = datetime.now(timezone.utc)
    if expires_at <= now:
        await callback.message.answer(
            "❌ Твоя подписка закончилась.\n\n"
            "Нажми «⭐ Купить VPN», чтобы продлить её.",
            reply_markup=main_keyboard()
        )
        return
    sub_url = f"{PUBLIC_URL}/sub/{sub['sub_token']}"
    await callback.message.answer(
        "📱 <b>Твоя подписка</b>\n\n"
        f"📅 Действует до: "
        f"<b>{expires_at.strftime('%d.%m.%Y %H:%M')} UTC</b>\n\n"
        "🔗 <b>Ссылка:</b>\n"
        f"<code>{sub_url}</code>\n\n"
        "Ссылка постоянная — при обновлении серверов "
        "её менять не нужно."
    )
# ============================================================
# HELP
# ============================================================
@dp.callback_query(F.data == "help")
async def help_handler(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        help_text(),
        reply_markup=main_keyboard()
    )
# ============================================================
# ADMIN PANEL
# ============================================================
@dp.callback_query(F.data == "admin")
async def admin_handler(callback: CallbackQuery):
    await callback.answer()
    if callback.from_user.id != ADMIN_ID:
        await callback.message.answer("⛔ Доступ запрещён.")
        return
    await callback.message.answer(
        "🔐 <b>Админ-панель</b>",
        reply_markup=admin_keyboard()
    )
# ============================================================
# ADMIN — REFRESH SERVERS
# ============================================================
@dp.callback_query(F.data == "admin_refresh")
async def admin_refresh_handler(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True
        )
        return
    await callback.answer(
        "🔄 Обновляю серверы..."
    )
    try:
        # GitHub читается ОДИН раз
        servers = await fetch_servers()
        active_subs = get_active_subscriptions()
        updated = 0
        conn = db()
        for sub in active_subs:
            new_content = make_subscription_content(
                servers
            )
            conn.execute("""
                UPDATE subscriptions
                SET content = ?
                WHERE user_id = ?
            """, (
                new_content,
                sub["user_id"]
            ))
            updated += 1
        conn.commit()
        conn.close()
        await callback.message.answer(
            "✅ <b>Серверы обновлены!</b>\n\n"
            f"🌐 Серверов в GitHub: <b>{len(servers)}</b>\n"
            f"👥 Активных подписок: <b>{updated}</b>\n\n"
            "Для каждого пользователя создан новый "
            "случайный набор серверов.\n"
            "🔗 Ссылки подписок остались прежними."
        )
    except Exception as e:
        await callback.message.answer(
            "❌ <b>Ошибка обновления серверов</b>\n\n"
            f"<code>{str(e)[:1000]}</code>"
        )
# ============================================================
# ADMIN — STATS
# ============================================================
@dp.callback_query(F.data == "admin_stats")
async def admin_stats_handler(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True
        )
        return
    await callback.answer()
    conn = db()
    users_count = conn.execute("""
        SELECT COUNT(*)
        FROM users
    """).fetchone()[0]
    subscriptions_count = conn.execute("""
        SELECT COUNT(*)
        FROM subscriptions
    """).fetchone()[0]
    conn.close()
    active_count = len(get_active_subscriptions())
    await callback.message.answer(
        "📊 <b>Статистика Talking VPN</b>\n\n"
        f"👤 Пользователей: <b>{users_count}</b>\n"
        f"📱 Всего подписок: <b>{subscriptions_count}</b>\n"
        f"🟢 Активных подписок: <b>{active_count}</b>\n"
        f"💰 Цена: <b>{PRICE_STARS} Stars</b>\n"
        f"📅 Срок: <b>{SUBSCRIPTION_DAYS} дней</b>"
    )
# ============================================================
# SUBSCRIPTION HTTP ENDPOINT
# ============================================================
async def subscription_endpoint(request):
    token = request.match_info.get("token", "")
    if not token:
        return web.Response(
            status=404,
            text="Not found"
        )
    conn = db()
    row = conn.execute("""
        SELECT *
        FROM subscriptions
        WHERE sub_token = ?
    """, (token,)).fetchone()
    conn.close()
    if not row:
        return web.Response(
            status=404,
            text="Subscription not found"
        )
    try:
        expires_at = datetime.fromisoformat(
            row["expires_at"]
        )
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(
                tzinfo=timezone.utc
            )
    except Exception:
        return web.Response(
            status=500,
            text="Invalid subscription"
        )
    if expires_at <= datetime.now(timezone.utc):
        return web.Response(
            status=403,
            text="Subscription expired"
        )
    return web.Response(
        status=200,
        text=row["content"],
        content_type="text/plain"
    )
# ============================================================
# HEALTH
# ============================================================
async def health_endpoint(request):
    return web.Response(
        text="Talking VPN OK"
    )
# ============================================================
# HTTP SERVER
# ============================================================
async def start_web_server():
    app = web.Application()
    app.router.add_get(
        "/health",
        health_endpoint
    )
    app.router.add_get(
        "/sub/{token}",
        subscription_endpoint
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )
    await site.start()
    print(
        f"HTTP server started on 0.0.0.0:{PORT}"
    )
# ============================================================
# CONFIG CHECK
# ============================================================
def check_config():
    errors = []
    if not BOT_TOKEN:
        errors.append("BOT_TOKEN")
    if not ADMIN_ID:
        errors.append("ADMIN_ID")
    if not PUBLIC_URL.startswith("https://"):
        errors.append("PUBLIC_URL")
    if errors:
        raise RuntimeError(
            "Не настроены параметры: "
            + ", ".join(errors)
        )
# ============================================================
# MAIN
# ============================================================
async def main():
    check_config()
    init_db()
    print("=" * 50)
    print(f"{BOT_NAME} STARTING")
    print(f"VERSION: {VERSION}")
    print(f"GitHub: {GITHUB_OWNER}/{GITHUB_REPO}")
    print(f"File: {SERVERS_FILE}")
    print(f"Public URL: {PUBLIC_URL}")
    print(f"Price: {PRICE_STARS} Stars")
    print(f"Days: {SUBSCRIPTION_DAYS}")
    print(f"Servers per user: {SERVERS_PER_USER}")
    print("=" * 50)
    await start_web_server()
    print("Bot polling started")
    await dp.start_polling(bot)
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped")
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        raise