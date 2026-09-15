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
# =========================================================
# KIL VPN — настройки
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except ValueError:
    ADMIN_ID = 0
BOT_NAME = "KIL VPN"
# GitHub
GITHUB_OWNER = "bdtvyz76b6-blip"
GITHUB_REPO = "sokolovvpn"
GITHUB_BRANCH = "main"
SERVERS_FILE = "servers.txt"
# Render
PUBLIC_URL = "https://sokolovvpn.onrender.com"
PORT = 10000
# Оплата
PRICE_STARS = 100
SUBSCRIPTION_DAYS = 30
# Сколько серверов получает каждый пользователь
SERVERS_PER_USER = 5
# Локальная SQLite БД
DB_FILE = "kil_vpn.sqlite3"
VERSION = "KIL-VPN-1.0"
# =========================================================
# Проверка настроек
# =========================================================
def check_config():
    errors = []
    if not BOT_TOKEN:
        errors.append("BOT_TOKEN")
    if not ADMIN_ID:
        errors.append("ADMIN_ID")
    if not PUBLIC_URL:
        errors.append("PUBLIC_URL")
    if errors:
        raise RuntimeError(
            "Не заданы переменные/настройки: " + ", ".join(errors)
        )
# =========================================================
# SQLite
# =========================================================
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
def save_user(user: Message):
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
        user.from_user.id,
        user.from_user.username or "",
        user.from_user.first_name or "",
        datetime.now(timezone.utc).isoformat(),
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
def save_subscription(
    user_id: int,
    expires_at: str,
    sub_token: str,
    content: str
):
    conn = db()
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
            sub_token = excluded.sub_token,
            content = excluded.content
    """, (
        user_id,
        expires_at,
        sub_token,
        content,
    ))
    conn.commit()
    conn.close()
def update_subscription_content(
    user_id: int,
    content: str
):
    conn = db()
    conn.execute("""
        UPDATE subscriptions
        SET content = ?
        WHERE user_id = ?
    """, (
        content,
        user_id,
    ))
    conn.commit()
    conn.close()
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
            continue
    return result
def get_stats():
    conn = db()
    users = conn.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]
    subscriptions = conn.execute(
        "SELECT COUNT(*) FROM subscriptions"
    ).fetchone()[0]
    conn.close()
    return users, subscriptions
# =========================================================
# Token
# =========================================================
def generate_token(length=32):
    chars = (
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789"
    )
    return "".join(random.choice(chars) for _ in range(length))
def get_or_create_token(user_id: int):
    existing = get_subscription(user_id)
    if existing:
        return existing["sub_token"]
    return generate_token()
# =========================================================
# GitHub
# =========================================================
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
        "User-Agent": "KIL-VPN-Bot"
    }
    github_token = os.getenv("GITHUB_TOKEN", "").strip()
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                text = await response.text()
                raise RuntimeError(
                    f"GitHub вернул HTTP {response.status}: {text[:300]}"
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
        raise RuntimeError(
            "servers.txt пустой или серверы не найдены."
        )
    return servers
# =========================================================
# Генерация серверов для пользователя
# =========================================================
def choose_servers(all_servers):
    count = min(
        SERVERS_PER_USER,
        len(all_servers)
    )
    return random.sample(all_servers, count)
def make_subscription_content(servers):
    """
    Делает Base64-список VPN-ссылок.
    Каждый сервер — отдельная строка.
    """
    raw = "\n".join(servers)
    encoded = base64.b64encode(
        raw.encode("utf-8")
    ).decode("utf-8")
    return encoded
# =========================================================
# URL подписки
# =========================================================
def subscription_url(token):
    return f"{PUBLIC_URL.rstrip('/')}/sub/{token}"
# =========================================================
# Telegram Bot
# =========================================================
bot = Bot(BOT_TOKEN)
dp = Dispatcher()
# =========================================================
# Клавиатуры
# =========================================================
def main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(
        text="💳 Купить VPN",
        callback_data="buy"
    )
    builder.button(
        text="📱 Моя подписка",
        callback_data="my_sub"
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
# =========================================================
# /start
# =========================================================
@dp.message(Command("start"))
async def start_handler(message: Message):
    save_user(message)
    text = (
        f"🔐 <b>{BOT_NAME}</b>\n\n"
        "VPN с удобной подпиской через Telegram Stars.\n\n"
        f"💰 Цена: <b>{PRICE_STARS} Stars</b>\n"
        f"⏳ Срок: <b>{SUBSCRIPTION_DAYS} дней</b>\n\n"
        "Нажми кнопку ниже, чтобы купить подписку."
    )
    await message.answer(
        text,
        reply_markup=main_keyboard()
    )
# =========================================================
# /admin
# =========================================================
@dp.message(Command("admin"))
async def admin_handler(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещён.")
        return
    await message.answer(
        "🛠 <b>KIL VPN — админ-панель</b>\n\n"
        "Управление серверами и статистика.",
        reply_markup=admin_keyboard()
    )
# =========================================================
# Покупка
# =========================================================
@dp.callback_query(F.data == "buy")
async def buy_handler(callback: CallbackQuery):
    await callback.answer()
    prices = [
        LabeledPrice(
            label=f"{BOT_NAME} — {SUBSCRIPTION_DAYS} дней",
            amount=PRICE_STARS
        )
    ]
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"{BOT_NAME} — VPN",
        description=(
            f"VPN подписка на {SUBSCRIPTION_DAYS} дней. "
            f"Цена: {PRICE_STARS} Telegram Stars."
        ),
        payload=f"kilvpn:{callback.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=prices
    )
# =========================================================
# PreCheckout
# =========================================================
@dp.pre_checkout_query()
async def pre_checkout_handler(query: PreCheckoutQuery):
    await query.answer(ok=True)
# =========================================================
# Успешная оплата
# =========================================================
@dp.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    payment = message.successful_payment
    user_id = message.from_user.id
    try:
        servers = await fetch_servers()
    except Exception as e:
        await message.answer(
            "⚠️ Оплата получена, но серверы сейчас "
            "не удалось загрузить.\n\n"
            "Обратитесь к администратору."
        )
        print("Ошибка загрузки серверов:", e)
        return
    selected_servers = choose_servers(servers)
    content = make_subscription_content(
        selected_servers
    )
    existing = get_subscription(user_id)
    now = datetime.now(timezone.utc)
    if existing:
        try:
            old_expires = datetime.fromisoformat(
                existing["expires_at"]
            )
            if old_expires.tzinfo is None:
                old_expires = old_expires.replace(
                    tzinfo=timezone.utc
                )
        except Exception:
            old_expires = now
        start_date = max(now, old_expires)
        token = existing["sub_token"]
    else:
        start_date = now
        token = generate_token()
    expires_at = (
        start_date +
        timedelta(days=SUBSCRIPTION_DAYS)
    )
    save_subscription(
        user_id=user_id,
        expires_at=expires_at.isoformat(),
        sub_token=token,
        content=content
    )
    url = subscription_url(token)
    await message.answer(
        f"✅ <b>Оплата получена!</b>\n\n"
        f"🔐 {BOT_NAME}\n"
        f"⏳ Действует до: "
        f"<b>{expires_at.strftime('%d.%m.%Y %H:%M')}</b> UTC\n\n"
        "🔗 <b>Ссылка на подписку:</b>\n"
        f"<code>{url}</code>\n\n"
        "Добавьте эту ссылку в ваше VPN-приложение."
    )
# =========================================================
# Моя подписка
# =========================================================
@dp.callback_query(F.data == "my_sub")
async def my_subscription_handler(
    callback: CallbackQuery
):
    await callback.answer()
    sub = get_subscription(
        callback.from_user.id
    )
    if not sub:
        await callback.message.answer(
            "❌ У вас пока нет активной подписки.",
            reply_markup=main_keyboard()
        )
        return
    try:
        expires = datetime.fromisoformat(
            sub["expires_at"]
        )
        if expires.tzinfo is None:
            expires = expires.replace(
                tzinfo=timezone.utc
            )
    except Exception:
        expires = None
    if not expires or expires <= datetime.now(timezone.utc):
        await callback.message.answer(
            "❌ Ваша подписка закончилась.\n\n"
            "Нажмите «💳 Купить VPN», чтобы продлить.",
            reply_markup=main_keyboard()
        )
        return
    url = subscription_url(
        sub["sub_token"]
    )
    await callback.message.answer(
        f"🔐 <b>{BOT_NAME}</b>\n\n"
        f"⏳ Действует до:\n"
        f"<b>{expires.strftime('%d.%m.%Y %H:%M')} UTC</b>\n\n"
        "🔗 <b>Ваша ссылка:</b>\n"
        f"<code>{url}</code>",
        reply_markup=main_keyboard()
    )
# =========================================================
# Помощь
# =========================================================
@dp.callback_query(F.data == "help")
async def help_handler(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        f"ℹ️ <b>{BOT_NAME}</b>\n\n"
        "1. Нажмите «💳 Купить VPN».\n"
        "2. Оплатите подписку через Telegram Stars.\n"
        "3. Получите персональную ссылку.\n"
        "4. Добавьте ссылку в своё VPN-приложение.\n\n"
        "Каждому пользователю выдаётся "
        "свой случайный набор серверов."
    )
# =========================================================
# Админ: обновление серверов
# =========================================================
@dp.callback_query(F.data == "admin_refresh")
async def admin_refresh_handler(
    callback: CallbackQuery
):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True
        )
        return
    await callback.answer(
        "Обновляю серверы..."
    )
    try:
        # GitHub загружается ОДИН раз
        all_servers = await fetch_servers()
        active_subs = get_active_subscriptions()
        updated = 0
        for sub in active_subs:
            selected_servers = choose_servers(
                all_servers
            )
            content = make_subscription_content(
                selected_servers
            )
            update_subscription_content(
                sub["user_id"],
                content
            )
            updated += 1
        await callback.message.answer(
            "✅ <b>Серверы обновлены</b>\n\n"
            f"🌐 Всего серверов в GitHub: "
            f"<b>{len(all_servers)}</b>\n"
            f"👤 Активных подписок обновлено: "
            f"<b>{updated}</b>\n\n"
            "Для каждого пользователя создан "
            "новый случайный набор серверов.\n\n"
            "🔗 Ссылки подписок пользователей "
            "<b>не изменились</b>."
        )
    except Exception as e:
        print("Ошибка обновления серверов:", e)
        await callback.message.answer(
            "❌ Не удалось обновить серверы.\n\n"
            f"<code>{str(e)[:1000]}</code>"
        )
# =========================================================
# Админ: статистика
# =========================================================
@dp.callback_query(F.data == "admin_stats")
async def admin_stats_handler(
    callback: CallbackQuery
):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True
        )
        return
    users, subscriptions = get_stats()
    active = len(
        get_active_subscriptions()
    )
    await callback.answer()
    await callback.message.answer(
        "📊 <b>KIL VPN — статистика</b>\n\n"
        f"👤 Пользователей: <b>{users}</b>\n"
        f"📦 Подписок: <b>{subscriptions}</b>\n"
        f"🟢 Активных: <b>{active}</b>\n\n"
        f"💰 Цена: <b>{PRICE_STARS} Stars</b>\n"
        f"⏳ Срок: <b>{SUBSCRIPTION_DAYS} дней</b>"
    )
# =========================================================
# Web server Render
# =========================================================
async def health_handler(request):
    return web.Response(
        text="KIL VPN OK"
    )
async def subscription_handler(request):
    token = request.match_info.get("token", "")
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
        expires = datetime.fromisoformat(
            row["expires_at"]
        )
        if expires.tzinfo is None:
            expires = expires.replace(
                tzinfo=timezone.utc
            )
    except Exception:
        return web.Response(
            status=500,
            text="Invalid subscription"
        )
    if expires <= datetime.now(timezone.utc):
        return web.Response(
            status=403,
            text="Subscription expired"
        )
    return web.Response(
        text=row["content"],
        content_type="text/plain"
    )
async def start_web_server():
    app = web.Application()
    app.router.add_get(
        "/health",
        health_handler
    )
    app.router.add_get(
        "/sub/{token}",
        subscription_handler
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
        f"Web server started on port {PORT}"
    )
# =========================================================
# Main
# =========================================================
async def main():
    check_config()
    init_db()
    print("=" * 50)
    print(f"{BOT_NAME} starting")
    print(f"Version: {VERSION}")
    print(f"GitHub: {github_raw_url()}")
    print(f"Price: {PRICE_STARS} Stars")
    print(
        f"Subscription: {SUBSCRIPTION_DAYS} days"
    )
    print(
        f"Servers per user: {SERVERS_PER_USER}"
    )
    print(f"Public URL: {PUBLIC_URL}")
    print("=" * 50)
    await start_web_server()
    print("Telegram polling started")
    await dp.start_polling(bot)
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("KIL VPN stopped")