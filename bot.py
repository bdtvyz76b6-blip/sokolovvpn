import os
import sqlite3
import random
import base64
import asyncio
from datetime import datetime, timedelta, timezone
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    PreCheckoutQuery,
)
# ============================================================
# TALKING VPN — НАСТРОЙКИ
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
# GitHub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
SERVERS_FILE = os.getenv("SERVERS_FILE", "servers.txt")
# Публичный адрес Render-сервиса
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
# Настройки подписки
PRICE_STARS = int(os.getenv("PRICE_STARS", "100"))
SUBSCRIPTION_DAYS = 30
SERVERS_PER_USER = int(os.getenv("SERVERS_PER_USER", "5"))
# Render
PORT = int(os.getenv("PORT", "10000"))
# Локальная база
DB_FILE = os.getenv("DB_FILE", "talking_vpn.sqlite3")
# ============================================================
# DATABASE
# ============================================================
db = sqlite3.connect(
    DB_FILE,
    check_same_thread=False
)
db.row_factory = sqlite3.Row
db.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT DEFAULT '',
    first_name TEXT DEFAULT '',
    subscription_until TEXT,
    subscription_content TEXT DEFAULT '',
    created_at TEXT NOT NULL
)
""")
db.commit()
def utc_now():
    return datetime.now(timezone.utc)
def dt_to_string(dt):
    return dt.astimezone(timezone.utc).isoformat()
def string_to_dt(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            result = result.replace(
                tzinfo=timezone.utc
            )
        return result.astimezone(timezone.utc)
    except Exception:
        return None
def get_user(user_id):
    return db.execute(
        """
        SELECT *
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchone()
def create_or_update_user(tg_user):
    existing = get_user(tg_user.id)
    if existing:
        db.execute(
            """
            UPDATE users
            SET username = ?,
                first_name = ?
            WHERE user_id = ?
            """,
            (
                tg_user.username or "",
                tg_user.first_name or "",
                tg_user.id
            )
        )
    else:
        db.execute(
            """
            INSERT INTO users (
                user_id,
                username,
                first_name,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                tg_user.id,
                tg_user.username or "",
                tg_user.first_name or "",
                dt_to_string(utc_now())
            )
        )
    db.commit()
def subscription_active(user):
    if not user:
        return False
    until = string_to_dt(
        user["subscription_until"]
    )
    if not until:
        return False
    return until > utc_now()
# ============================================================
# GITHUB
# ============================================================
async def get_servers_from_github():
    if not GITHUB_OWNER:
        raise RuntimeError(
            "Не задан GITHUB_OWNER"
        )
    if not GITHUB_REPO:
        raise RuntimeError(
            "Не задан GITHUB_REPO"
        )
    url = (
        "https://raw.githubusercontent.com/"
        f"{GITHUB_OWNER}/"
        f"{GITHUB_REPO}/"
        f"{GITHUB_BRANCH}/"
        f"{SERVERS_FILE}"
    )
    headers = {}
    if GITHUB_TOKEN:
        headers["Authorization"] = (
            f"Bearer {GITHUB_TOKEN}"
        )
    timeout = aiohttp.ClientTimeout(
        total=20
    )
    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:
        async with session.get(
            url,
            headers=headers
        ) as response:
            if response.status != 200:
                text = await response.text()
                raise RuntimeError(
                    f"GitHub HTTP {response.status}: "
                    f"{text[:300]}"
                )
            text = await response.text()
    servers = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        servers.append(line)
    # Убираем дубликаты
    servers = list(
        dict.fromkeys(servers)
    )
    if not servers:
        raise RuntimeError(
            "Файл servers.txt пуст."
        )
    return servers
# ============================================================
# RANDOM SERVERS
# ============================================================
def generate_random_servers(all_servers):
    count = min(
        SERVERS_PER_USER,
        len(all_servers)
    )
    return random.sample(
        all_servers,
        count
    )
def build_subscription_content(all_servers):
    selected = generate_random_servers(
        all_servers
    )
    return "\n".join(selected) + "\n"
def save_subscription_content(
    user_id,
    content
):
    db.execute(
        """
        UPDATE users
        SET subscription_content = ?
        WHERE user_id = ?
        """,
        (
            content,
            user_id
        )
    )
    db.commit()
# ============================================================
# PERMANENT SUBSCRIPTION URL
# ============================================================
def get_subscription_url(user_id):
    if not PUBLIC_URL:
        return "PUBLIC_URL не настроен"
    return (
        f"{PUBLIC_URL}"
        f"/sub/{user_id}"
    )
# ============================================================
# KEYBOARDS
# ============================================================
def main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ Купить подписку",
                    callback_data="buy"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📱 Моя подписка",
                    callback_data="subscription"
                )
            ],
            [
                InlineKeyboardButton(
                    text="ℹ️ Помощь",
                    callback_data="help"
                )
            ]
        ]
    )
def admin_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Обновить серверы",
                    callback_data="admin_refresh"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Статистика",
                    callback_data="admin_stats"
                )
            ]
        ]
    )
# ============================================================
# BOT
# ============================================================
bot = Bot(
    token=BOT_TOKEN
)
dp = Dispatcher()
# ============================================================
# START
# ============================================================
@dp.message(CommandStart())
async def start_handler(
    message: Message
):
    create_or_update_user(
        message.from_user
    )
    await message.answer(
        "👋 <b>Talking VPN</b>\n\n"
        "🔐 Быстрый и надёжный VPN.\n\n"
        "После покупки вы получите "
        "персональную ссылку подписки.\n\n"
        "Для каждого пользователя "
        "выбирается свой случайный "
        "набор серверов.",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )
# ============================================================
# ADMIN
# ============================================================
@dp.message(Command("admin"))
async def admin_handler(
    message: Message
):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer(
        "🛠 <b>Talking VPN — админ-панель</b>",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )
# ============================================================
# BUY
# ============================================================
@dp.callback_query(
    F.data == "buy"
)
async def buy_handler(
    call: CallbackQuery
):
    create_or_update_user(
        call.from_user
    )
    payload = (
        f"talking_vpn:"
        f"{call.from_user.id}:"
        f"{int(utc_now().timestamp())}"
    )
    await call.message.answer_invoice(
        title="Talking VPN",
        description=(
            f"Подписка Talking VPN "
            f"на {SUBSCRIPTION_DAYS} дней."
        ),
        payload=payload,
        currency="XTR",
        prices=[
            LabeledPrice(
                label="Talking VPN",
                amount=PRICE_STARS
            )
        ]
    )
    await call.answer()
# ============================================================
# PRE-CHECKOUT
# ============================================================
@dp.pre_checkout_query()
async def pre_checkout_handler(
    query: PreCheckoutQuery
):
    await query.answer(
        ok=True
    )
# ============================================================
# SUCCESSFUL PAYMENT
# ============================================================
@dp.message(
    F.successful_payment
)
async def successful_payment_handler(
    message: Message
):
    create_or_update_user(
        message.from_user
    )
    user = get_user(
        message.from_user.id
    )
    current = utc_now()
    old_until = string_to_dt(
        user["subscription_until"]
    )
    # Если подписка ещё действует,
    # добавляем дни к её окончанию.
    if old_until and old_until > current:
        start_date = old_until
    else:
        start_date = current
    new_until = (
        start_date
        + timedelta(
            days=SUBSCRIPTION_DAYS
        )
    )
    # Получаем серверы один раз
    # и создаём персональный список.
    try:
        servers = await get_servers_from_github()
        content = build_subscription_content(
            servers
        )
    except Exception as error:
        print(
            "GitHub error:",
            error
        )
        content = ""
    db.execute(
        """
        UPDATE users
        SET subscription_until = ?,
            subscription_content = ?
        WHERE user_id = ?
        """,
        (
            dt_to_string(new_until),
            content,
            message.from_user.id
        )
    )
    db.commit()
    url = get_subscription_url(
        message.from_user.id
    )
    await message.answer(
        "✅ <b>Оплата получена!</b>\n\n"
        f"📅 Подписка до:\n"
        f"<code>"
        f"{new_until.strftime('%d.%m.%Y %H:%M')}"
        f"</code>\n\n"
        "🔗 <b>Ваша ссылка:</b>\n"
        f"<code>{url}</code>\n\n"
        "♾ Эта ссылка постоянная.\n"
        "При обновлении серверов "
        "она не изменится.",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )
# ============================================================
# MY SUBSCRIPTION
# ============================================================
@dp.callback_query(
    F.data == "subscription"
)
async def subscription_handler(
    call: CallbackQuery
):
    create_or_update_user(
        call.from_user
    )
    user = get_user(
        call.from_user.id
    )
    if not subscription_active(user):
        await call.answer(
            "❌ Подписка не активна.",
            show_alert=True
        )
        return
    until = string_to_dt(
        user["subscription_until"]
    )
    url = get_subscription_url(
        call.from_user.id
    )
    await call.message.answer(
        "📱 <b>Моя подписка</b>\n\n"
        "🟢 Статус: <b>активна</b>\n\n"
        f"📅 До:\n"
        f"<code>"
        f"{until.strftime('%d.%m.%Y %H:%M')}"
        f"</code>\n\n"
        "🔗 <b>Ссылка:</b>\n"
        f"<code>{url}</code>\n\n"
        "♾ Ссылка постоянная.",
        parse_mode="HTML"
    )
    await call.answer()
# ============================================================
# HELP
# ============================================================
@dp.callback_query(
    F.data == "help"
)
async def help_handler(
    call: CallbackQuery
):
    await call.message.answer(
        "ℹ️ <b>Talking VPN</b>\n\n"
        "1️⃣ Покупаете подписку за Telegram Stars.\n\n"
        "2️⃣ Получаете персональную ссылку.\n\n"
        "3️⃣ Добавляете её в VPN-клиент.\n\n"
        "🔄 Администратор может обновить "
        "список серверов.\n\n"
        "При обновлении каждому активному "
        "пользователю назначается новый "
        "случайный набор серверов.\n\n"
        "♾ Сама ссылка пользователя "
        "при этом остаётся прежней.",
        parse_mode="HTML"
    )
    await call.answer()
# ============================================================
# ADMIN — REFRESH SERVERS
# ============================================================
@dp.callback_query(
    F.data == "admin_refresh"
)
async def admin_refresh_handler(
    call: CallbackQuery
):
    if call.from_user.id != ADMIN_ID:
        await call.answer(
            "⛔ Нет доступа.",
            show_alert=True
        )
        return
    await call.answer(
        "Обновляю серверы..."
    )
    status = await call.message.answer(
        "🔄 <b>Обновление серверов...</b>\n\n"
        "Получаю актуальный список GitHub.",
        parse_mode="HTML"
    )
    try:
        # ВАЖНО:
        # GitHub скачивается только один раз.
        all_servers = (
            await get_servers_from_github()
        )
        users = db.execute(
            """
            SELECT *
            FROM users
            WHERE subscription_until IS NOT NULL
            """
        ).fetchall()
        updated = 0
        skipped = 0
        errors = 0
        for user in users:
            if not subscription_active(user):
                skipped += 1
                continue
            try:
                # Для КАЖДОГО пользователя
                # создаём новый random sample.
                content = (
                    build_subscription_content(
                        all_servers
                    )
                )
                save_subscription_content(
                    user["user_id"],
                    content
                )
                updated += 1
            except Exception as error:
                errors += 1
                print(
                    f"Refresh error "
                    f"user={user['user_id']}:",
                    error
                )
        await status.edit_text(
            "✅ <b>Серверы обновлены!</b>\n\n"
            f"🌐 Серверов в GitHub: "
            f"<b>{len(all_servers)}</b>\n\n"
            f"🟢 Обновлено активных: "
            f"<b>{updated}</b>\n"
            f"⏭ Пропущено неактивных: "
            f"<b>{skipped}</b>\n"
            f"❌ Ошибок: "
            f"<b>{errors}</b>\n\n"
            "♾ Ссылки пользователей "
            "не изменились.",
            parse_mode="HTML",
            reply_markup=admin_keyboard()
        )
    except Exception as error:
        print(
            "Admin refresh error:",
            error
        )
        await status.edit_text(
            "❌ <b>Ошибка обновления</b>\n\n"
            f"<code>{str(error)[:1500]}</code>",
            parse_mode="HTML",
            reply_markup=admin_keyboard()
        )
# ============================================================
# ADMIN — STATS
# ============================================================
@dp.callback_query(
    F.data == "admin_stats"
)
async def admin_stats_handler(
    call: CallbackQuery
):
    if call.from_user.id != ADMIN_ID:
        await call.answer(
            "⛔ Нет доступа.",
            show_alert=True
        )
        return
    users = db.execute(
        "SELECT * FROM users"
    ).fetchall()
    total = len(users)
    active = sum(
        1
        for user in users
        if subscription_active(user)
    )
    await call.message.answer(
        "📊 <b>Статистика Talking VPN</b>\n\n"
        f"👤 Пользователей: <b>{total}</b>\n"
        f"🟢 Активных подписок: <b>{active}</b>",
        parse_mode="HTML"
    )
    await call.answer()
# ============================================================
# HTTP — SUBSCRIPTION
# ============================================================
async def subscription_http_handler(
    request: web.Request
):
    try:
        user_id = int(
            request.match_info["user_id"]
        )
    except Exception:
        return web.Response(
            status=400,
            text="Invalid user"
        )
    user = get_user(user_id)
    if not subscription_active(user):
        return web.Response(
            status=403,
            text="Subscription inactive"
        )
    content = (
        user["subscription_content"]
        or ""
    )
    # Если по какой-то причине контент
    # отсутствует — создаём его.
    if not content:
        try:
            servers = (
                await get_servers_from_github()
            )
            content = (
                build_subscription_content(
                    servers
                )
            )
            save_subscription_content(
                user_id,
                content
            )
        except Exception as error:
            print(
                "Subscription error:",
                error
            )
            return web.Response(
                status=503,
                text="Subscription unavailable"
            )
    # Отдаём Base64.
    encoded = base64.b64encode(
        content.encode("utf-8")
    ).decode("ascii")
    return web.Response(
        text=encoded,
        content_type="text/plain",
        charset="utf-8",
        headers={
            "Cache-Control":
                "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )
# ============================================================
# HEALTH CHECK FOR RENDER
# ============================================================
async def health_handler(
    request: web.Request
):
    return web.Response(
        text="Talking VPN OK"
    )
# ============================================================
# HTTP SERVER
# ============================================================
async def start_http():
    app = web.Application()
    app.router.add_get(
        "/sub/{user_id}",
        subscription_http_handler
    )
    app.router.add_get(
        "/health",
        health_handler
    )
    runner = web.AppRunner(
        app
    )
    await runner.setup()
    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )
    await site.start()
    print(
        f"HTTP server started on port {PORT}"
    )
    return runner
# ============================================================
# STARTUP CHECKS
# ============================================================
def check_config():
    errors = []
    if not BOT_TOKEN:
        errors.append(
            "BOT_TOKEN"
        )
    if not ADMIN_ID:
        errors.append(
            "ADMIN_ID"
        )
    if not GITHUB_OWNER:
        errors.append(
            "GITHUB_OWNER"
        )
    if not GITHUB_REPO:
        errors.append(
            "GITHUB_REPO"
        )
    if not PUBLIC_URL:
        errors.append(
            "PUBLIC_URL"
        )
    if errors:
        raise RuntimeError(
            "Не заданы переменные Render: "
            + ", ".join(errors)
        )
# ============================================================
# MAIN
# ============================================================
async def main():
    check_config()
    print(
        "================================="
    )
    print(
        "      TALKING VPN STARTING"
    )
    print(
        "================================="
    )
    print(
        f"Admin ID: {ADMIN_ID}"
    )
    print(
        f"GitHub: "
        f"{GITHUB_OWNER}/{GITHUB_REPO}"
    )
    print(
        f"Stars price: {PRICE_STARS}"
    )
    print(
        f"Servers per user: "
        f"{SERVERS_PER_USER}"
    )
    runner = await start_http()
    try:
        await dp.start_polling(
            bot
        )
    finally:
        await runner.cleanup()
        await bot.session.close()
        db.close()
if __name__ == "__main__":
    asyncio.run(
        main()
    )