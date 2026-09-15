import os
import sqlite3
import random
import base64
import asyncio
from datetime import datetime, timedelta, timezone
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    LabeledPrice,
    PreCheckoutQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.filters import CommandStart, Command
# ============================================================
#                    TALKING VPN CONFIG
# ============================================================
# ============================================================
# ТОЛЬКО ЭТИ 3 ПЕРЕМЕННЫЕ БЕРУТСЯ ИЗ RENDER
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
# ============================================================
# ВСЁ ОСТАЛЬНОЕ ПРОПИСАНО ЗДЕСЬ
# ============================================================
BOT_NAME = "Talking VPN"
GITHUB_OWNER = "bdtvyz76b6-blip"
GITHUB_REPO = "sokolovvpn"
GITHUB_BRANCH = "main"
SERVERS_FILE = "servers.txt"
# Если Render назвал сервис иначе — поменяй только эту строку.
PUBLIC_URL = "https://sokolovvpn.onrender.com"
# Цена подписки в Telegram Stars
PRICE_STARS = 100
# Срок подписки
SUBSCRIPTION_DAYS = 30
# Сколько случайных серверов получает один пользователь
SERVERS_PER_USER = 5
# SQLite
DB_FILE = "talking_vpn.sqlite3"
# Render
PORT = 10000
# ============================================================
#                         DATABASE
# ============================================================
db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.row_factory = sqlite3.Row
db.execute(
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        created_at TEXT NOT NULL
    )
    """
)
db.execute(
    """
    CREATE TABLE IF NOT EXISTS subscriptions (
        user_id INTEGER PRIMARY KEY,
        expires_at TEXT NOT NULL,
        sub_token TEXT UNIQUE NOT NULL,
        content TEXT DEFAULT ''
    )
    """
)
db.commit()
def now_utc():
    return datetime.now(timezone.utc)
def save_user(message: Message):
    user = message.from_user
    if not user:
        return
    db.execute(
        """
        INSERT INTO users (
            user_id,
            username,
            first_name,
            created_at
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name
        """,
        (
            user.id,
            user.username or "",
            user.first_name or "",
            now_utc().isoformat(),
        ),
    )
    db.commit()
def get_subscription(user_id: int):
    return db.execute(
        """
        SELECT *
        FROM subscriptions
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()
def get_subscription_by_token(token: str):
    return db.execute(
        """
        SELECT *
        FROM subscriptions
        WHERE sub_token = ?
        """,
        (token,),
    ).fetchone()
def subscription_active(row):
    if not row:
        return False
    try:
        expires = datetime.fromisoformat(row["expires_at"])
        return expires > now_utc()
    except Exception:
        return False
def create_subscription_token():
    import secrets
    while True:
        token = secrets.token_urlsafe(32)
        exists = db.execute(
            """
            SELECT 1
            FROM subscriptions
            WHERE sub_token = ?
            """,
            (token,),
        ).fetchone()
        if not exists:
            return token
def create_or_extend_subscription(user_id: int):
    current = get_subscription(user_id)
    current_time = now_utc()
    if current and subscription_active(current):
        old_expiry = datetime.fromisoformat(current["expires_at"])
        new_expiry = old_expiry + timedelta(days=SUBSCRIPTION_DAYS)
        token = current["sub_token"]
    else:
        new_expiry = current_time + timedelta(days=SUBSCRIPTION_DAYS)
        if current:
            token = current["sub_token"]
        else:
            token = create_subscription_token()
    db.execute(
        """
        INSERT INTO subscriptions (
            user_id,
            expires_at,
            sub_token,
            content
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET
            expires_at = excluded.expires_at,
            sub_token = excluded.sub_token
        """,
        (
            user_id,
            new_expiry.isoformat(),
            token,
            current["content"] if current else "",
        ),
    )
    db.commit()
    return new_expiry, token
def save_subscription_content(user_id: int, content: str):
    db.execute(
        """
        UPDATE subscriptions
        SET content = ?
        WHERE user_id = ?
        """,
        (content, user_id),
    )
    db.commit()
# ============================================================
#                       GITHUB SERVERS
# ============================================================
def github_raw_url():
    return (
        f"https://raw.githubusercontent.com/"
        f"{GITHUB_OWNER}/"
        f"{GITHUB_REPO}/"
        f"{GITHUB_BRANCH}/"
        f"{SERVERS_FILE}"
    )
async def get_servers_from_github():
    url = github_raw_url()
    headers = {
        "User-Agent": "Talking-VPN-Bot"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    timeout = aiohttp.ClientTimeout(total=20)
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
        raise RuntimeError(
            "servers.txt пустой или серверы не найдены"
        )
    return servers
def generate_random_servers(all_servers):
    count = min(
        SERVERS_PER_USER,
        len(all_servers)
    )
    return random.sample(all_servers, count)
def build_subscription_content(servers):
    """
    Telegram VPN clients обычно принимают Base64-список
    строк подключения.
    Пример servers.txt:
    vless://...
    vless://...
    vless://...
    """
    plain_text = "\n".join(servers)
    encoded = base64.b64encode(
        plain_text.encode("utf-8")
    ).decode("utf-8")
    return encoded
async def generate_user_subscription(user_id: int, all_servers=None):
    if all_servers is None:
        all_servers = await get_servers_from_github()
    selected = generate_random_servers(all_servers)
    content = build_subscription_content(selected)
    save_subscription_content(
        user_id,
        content
    )
    return selected
# ============================================================
#                         TELEGRAM
# ============================================================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
# ============================================================
#                         KEYBOARDS
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
                    callback_data="my_subscription"
                )
            ],
            [
                InlineKeyboardButton(
                    text="ℹ️ Помощь",
                    callback_data="help"
                )
            ],
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
            ],
        ]
    )
# ============================================================
#                           TEXTS
# ============================================================
WELCOME_TEXT = """
👋 Добро пожаловать в Talking VPN!
🔐 Быстрый VPN-доступ
⭐ Оплата через Telegram Stars
🌐 Персональный набор серверов
После оплаты ты получишь персональную
subscription-ссылку.
Стоимость: 100 ⭐
Срок: 30 дней
"""
HELP_TEXT = """
ℹ️ Помощь Talking VPN
1. Нажми «⭐ Купить подписку».
2. Оплати 100 Telegram Stars.
3. Получишь персональную subscription-ссылку.
4. Добавь её в свой VPN-клиент.
Каждая подписка получает свой случайный
набор серверов.
При обновлении серверов администратором
твой URL подписки не изменяется.
"""
# ============================================================
#                         /START
# ============================================================
@dp.message(CommandStart())
async def start_handler(message: Message):
    save_user(message)
    await message.answer(
        WELCOME_TEXT,
        reply_markup=main_keyboard()
    )
# ============================================================
#                           /ADMIN
# ============================================================
@dp.message(Command("admin"))
async def admin_handler(message: Message):
    save_user(message)
    if message.from_user.id != ADMIN_ID:
        await message.answer(
            "⛔ Доступ запрещён."
        )
        return
    await message.answer(
        "🛠 Панель администратора Talking VPN",
        reply_markup=admin_keyboard()
    )
# ============================================================
#                         BUY BUTTON
# ============================================================
@dp.callback_query(F.data == "buy")
async def buy_handler(callback: CallbackQuery):
    await callback.answer()
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Talking VPN — 30 дней",
        description=(
            "Персональная VPN-подписка "
            "Talking VPN на 30 дней."
        ),
        payload=f"talking_vpn_{callback.from_user.id}",
        currency="XTR",
        prices=[
            LabeledPrice(
                label="Talking VPN — 30 дней",
                amount=PRICE_STARS
            )
        ],
        provider_token="",
    )
# ============================================================
#                       PRE-CHECKOUT
# ============================================================
@dp.pre_checkout_query()
async def pre_checkout_handler(
    query: PreCheckoutQuery
):
    if not query.invoice_payload.startswith(
        "talking_vpn_"
    ):
        await query.answer(
            ok=False,
            error_message="Некорректный платёж."
        )
        return
    await query.answer(ok=True)
# ============================================================
#                     SUCCESSFUL PAYMENT
# ============================================================
@dp.message(F.successful_payment)
async def successful_payment_handler(
    message: Message
):
    save_user(message)
    payment = message.successful_payment
    if not payment:
        return
    expected_payload = (
        f"talking_vpn_{message.from_user.id}"
    )
    if payment.invoice_payload != expected_payload:
        await message.answer(
            "⚠️ Платёж получен, но данные платежа "
            "не прошли проверку."
        )
        return
    try:
        expiry, token = create_or_extend_subscription(
            message.from_user.id
        )
        all_servers = await get_servers_from_github()
        selected = generate_random_servers(
            all_servers
        )
        content = build_subscription_content(
            selected
        )
        save_subscription_content(
            message.from_user.id,
            content
        )
        subscription_url = (
            f"{PUBLIC_URL}/sub/{token}"
        )
        expiry_text = expiry.strftime(
            "%d.%m.%Y %H:%M UTC"
        )
        await message.answer(
            f"""
✅ Оплата успешно получена!
🔐 Talking VPN активирован.
📅 Действует до:
{expiry_text}
🌐 Твоя персональная подписка:
{subscription_url}
📡 Серверов в подписке: {len(selected)}
⚠️ URL не изменится при обновлении серверов.
""",
            reply_markup=main_keyboard()
        )
    except Exception as e:
        print(
            "PAYMENT ERROR:",
            repr(e)
        )
        await message.answer(
            """
✅ Платёж получен.
Но серверы временно не удалось загрузить.
Попробуй открыть «📱 Моя подписка» чуть позже.
"""
        )
# ============================================================
#                    MY SUBSCRIPTION
# ============================================================
@dp.callback_query(F.data == "my_subscription")
async def my_subscription_handler(
    callback: CallbackQuery
):
    await callback.answer()
    row = get_subscription(
        callback.from_user.id
    )
    if not row:
        await callback.message.answer(
            "❌ Активной подписки нет.",
            reply_markup=main_keyboard()
        )
        return
    if not subscription_active(row):
        await callback.message.answer(
            "❌ Твоя подписка закончилась.",
            reply_markup=main_keyboard()
        )
        return
    content = row["content"]
    if not content:
        try:
            await generate_user_subscription(
                callback.from_user.id
            )
            row = get_subscription(
                callback.from_user.id
            )
            content = row["content"]
        except Exception as e:
            print(
                "SUBSCRIPTION GENERATION ERROR:",
                repr(e)
            )
            await callback.message.answer(
                "⚠️ Не удалось загрузить серверы. "
                "Попробуй позже."
            )
            return
    subscription_url = (
        f"{PUBLIC_URL}/sub/{row['sub_token']}"
    )
    expiry = datetime.fromisoformat(
        row["expires_at"]
    )
    expiry_text = expiry.strftime(
        "%d.%m.%Y %H:%M UTC"
    )
    await callback.message.answer(
        f"""
📱 Твоя подписка Talking VPN
📅 До: {expiry_text}
🔗 Subscription URL:
{subscription_url}
🔄 Ссылка постоянная — она не меняется
при обновлении серверов.
""",
        reply_markup=main_keyboard()
    )
# ============================================================
#                           HELP
# ============================================================
@dp.callback_query(F.data == "help")
async def help_handler(
    callback: CallbackQuery
):
    await callback.answer()
    await callback.message.answer(
        HELP_TEXT,
        reply_markup=main_keyboard()
    )
# ============================================================
#                     ADMIN: REFRESH SERVERS
# ============================================================
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
        "🔄 Обновляю серверы..."
    )
    status_message = await callback.message.answer(
        "🔄 Загружаю servers.txt из GitHub..."
    )
    try:
        # ВАЖНО:
        # GitHub читается ОДИН раз.
        all_servers = await get_servers_from_github()
        active_subscriptions = db.execute(
            """
            SELECT user_id
            FROM subscriptions
            WHERE expires_at > ?
            """,
            (now_utc().isoformat(),),
        ).fetchall()
        updated = 0
        for row in active_subscriptions:
            user_id = row["user_id"]
            # Для КАЖДОГО пользователя
            # создаётся свой случайный набор.
            selected = generate_random_servers(
                all_servers
            )
            content = build_subscription_content(
                selected
            )
            save_subscription_content(
                user_id,
                content
            )
            updated += 1
        await status_message.edit_text(
            f"""
✅ Серверы обновлены.
🌐 Всего серверов в GitHub: {len(all_servers)}
👥 Активных подписок обновлено: {updated}
🔗 URL подписок пользователей
остались прежними.
""",
            reply_markup=admin_keyboard()
        )
    except Exception as e:
        print(
            "ADMIN REFRESH ERROR:",
            repr(e)
        )
        await status_message.edit_text(
            f"""
❌ Ошибка обновления серверов.
{str(e)[:500]}
""",
            reply_markup=admin_keyboard()
        )
# ============================================================
#                      ADMIN: STATISTICS
# ============================================================
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
    await callback.answer()
    total_users = db.execute(
        "SELECT COUNT(*) AS count FROM users"
    ).fetchone()["count"]
    active_subscriptions = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM subscriptions
        WHERE expires_at > ?
        """,
        (now_utc().isoformat(),),
    ).fetchone()["count"]
    await callback.message.answer(
        f"""
📊 Talking VPN
👥 Пользователей: {total_users}
🟢 Активных подписок: {active_subscriptions}
⭐ Цена: {PRICE_STARS} Stars
📅 Срок: {SUBSCRIPTION_DAYS} дней
🌐 Серверов на пользователя:
до {SERVERS_PER_USER}
""",
        reply_markup=admin_keyboard()
    )
# ============================================================
#                    SUBSCRIPTION HTTP SERVER
# ============================================================
async def subscription_endpoint(
    request: web.Request
):
    token = request.match_info.get("token")
    if not token:
        return web.Response(
            status=404,
            text="Not found"
        )
    row = get_subscription_by_token(token)
    if not row:
        return web.Response(
            status=404,
            text="Subscription not found"
        )
    if not subscription_active(row):
        return web.Response(
            status=403,
            text="Subscription expired"
        )
    content = row["content"]
    if not content:
        try:
            all_servers = await get_servers_from_github()
            selected = generate_random_servers(
                all_servers
            )
            content = build_subscription_content(
                selected
            )
            save_subscription_content(
                row["user_id"],
                content
            )
        except Exception as e:
            print(
                "SUBSCRIPTION HTTP ERROR:",
                repr(e)
            )
            return web.Response(
                status=503,
                text="Servers temporarily unavailable"
            )
    return web.Response(
        status=200,
        text=content,
        content_type="text/plain",
        charset="utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )
async def health_endpoint(
    request: web.Request
):
    return web.Response(
        text="Talking VPN OK"
    )
async def start_http_server():
    app = web.Application()
    app.router.add_get(
        "/sub/{token}",
        subscription_endpoint
    )
    app.router.add_get(
        "/health",
        health_endpoint
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
        f"HTTP server started on port {PORT}"
    )
    return runner
# ============================================================
#                         CONFIG CHECK
# ============================================================
def check_config():
    errors = []
    if not BOT_TOKEN:
        errors.append(
            "BOT_TOKEN не задан"
        )
    if not ADMIN_ID:
        errors.append(
            "ADMIN_ID не задан"
        )
    if not PUBLIC_URL.startswith("http"):
        errors.append(
            "PUBLIC_URL указан неправильно"
        )
    if errors:
        print("\nCONFIG ERRORS:")
        for error in errors:
            print(
                " -",
                error
            )
        return False
    return True
# ============================================================
#                           MAIN
# ============================================================
async def main():
    if not check_config():
        return
    print("=" * 50)
    print("Talking VPN")
    print("=" * 50)
    print(
        f"GitHub: "
        f"{GITHUB_OWNER}/{GITHUB_REPO}"
    )
    print(
        f"Servers file: {SERVERS_FILE}"
    )
    print(
        f"Price: {PRICE_STARS} Stars"
    )
    print(
        f"Subscription: "
        f"{SUBSCRIPTION_DAYS} days"
    )
    print(
        f"Servers per user: "
        f"{SERVERS_PER_USER}"
    )
    print("=" * 50)
    http_runner = await start_http_server()
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types()
        )
    finally:
        await http_runner.cleanup()
        await bot.session.close()
        db.close()
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(
            "Talking VPN stopped."
        )