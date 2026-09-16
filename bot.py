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
# RAF VPN — НАСТРОЙКИ
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS = set()
for value in ADMIN_IDS_RAW.split(","):
    value = value.strip()
    if not value:
        continue
    try:
        ADMIN_IDS.add(int(value))
    except ValueError:
        pass
# Совместимость со старым ADMIN_ID
if not ADMIN_IDS:
    old_admin_id = os.getenv("ADMIN_ID", "").strip()
    try:
        if old_admin_id:
            ADMIN_IDS.add(int(old_admin_id))
    except ValueError:
        pass
def is_admin(user_id: int):
    return user_id in ADMIN_IDS
BOT_NAME = "RAF VPN"
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
# Пробный период
TRIAL_DAYS = 3
# Серверов на пользователя
SERVERS_PER_USER = 5
# SQLite
DB_FILE = "raf_vpn.sqlite3"
VERSION = "RAF-VPN-2.0"
# =========================================================
# ПРОВЕРКА
# =========================================================
def check_config():
    errors = []
    if not BOT_TOKEN:
        errors.append("BOT_TOKEN")
    if not ADMIN_IDS:
        errors.append("ADMIN_IDS")
    if not PUBLIC_URL:
        errors.append("PUBLIC_URL")
    if errors:
        raise RuntimeError(
            "Не заданы: " + ", ".join(errors)
        )
# =========================================================
# DATABASE
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
            created_at TEXT NOT NULL,
            trial_used INTEGER DEFAULT 0,
            blocked INTEGER DEFAULT 0
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
    columns = [
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(users)"
        ).fetchall()
    ]
    if "trial_used" not in columns:
        conn.execute(
            "ALTER TABLE users ADD COLUMN trial_used INTEGER DEFAULT 0"
        )
    if "blocked" not in columns:
        conn.execute(
            "ALTER TABLE users ADD COLUMN blocked INTEGER DEFAULT 0"
        )
    conn.commit()
    conn.close()
# =========================================================
# USERS
# =========================================================
def save_user(message: Message):
    user = message.from_user
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
        user.id,
        user.username or "",
        user.first_name or "",
        datetime.now(timezone.utc).isoformat(),
    ))
    conn.commit()
    conn.close()
def get_user(user_id: int):
    conn = db()
    row = conn.execute("""
        SELECT *
        FROM users
        WHERE user_id = ?
    """, (user_id,)).fetchone()
    conn.close()
    return row
def get_all_users():
    conn = db()
    rows = conn.execute("""
        SELECT *
        FROM users
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()
    return rows
def is_blocked(user_id: int):
    user = get_user(user_id)
    if not user:
        return False
    return bool(user["blocked"])
def set_blocked(user_id: int, value: bool):
    conn = db()
    conn.execute("""
        UPDATE users
        SET blocked = ?
        WHERE user_id = ?
    """, (
        1 if value else 0,
        user_id,
    ))
    conn.commit()
    conn.close()
def mark_trial_used(user_id: int):
    conn = db()
    conn.execute("""
        UPDATE users
        SET trial_used = 1
        WHERE user_id = ?
    """, (user_id,))
    conn.commit()
    conn.close()
# =========================================================
# SUBSCRIPTIONS
# =========================================================
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
def delete_subscription(user_id: int):
    conn = db()
    conn.execute("""
        DELETE FROM subscriptions
        WHERE user_id = ?
    """, (user_id,))
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
            expires = datetime.fromisoformat(
                row["expires_at"]
            )
            if expires.tzinfo is None:
                expires = expires.replace(
                    tzinfo=timezone.utc
                )
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
    blocked = conn.execute(
        "SELECT COUNT(*) FROM users WHERE blocked = 1"
    ).fetchone()[0]
    conn.close()
    active = len(
        get_active_subscriptions()
    )
    return users, subscriptions, active, blocked
# =========================================================
# TOKEN
# =========================================================
def generate_token(length=32):
    chars = (
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789"
    )
    return "".join(
        random.choice(chars)
        for _ in range(length)
    )
# =========================================================
# GITHUB
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
        "User-Agent": "RAF-VPN-Bot"
    }
    github_token = os.getenv(
        "GITHUB_TOKEN",
        ""
    ).strip()
    if github_token:
        headers["Authorization"] = (
            f"Bearer {github_token}"
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
        if line not in servers:
            servers.append(line)
    if not servers:
        raise RuntimeError(
            "servers.txt пустой."
        )
    return servers
# =========================================================
# СЕРВЕРА
# =========================================================
def choose_servers(all_servers):
    count = min(
        SERVERS_PER_USER,
        len(all_servers)
    )
    return random.sample(
        all_servers,
        count
    )
# =========================================================
# HAPP PROFILE
# =========================================================
def make_subscription_content(
    servers,
    user_id,
    expires_at
):
    """
    Формирует подписку в формате Happ:
    #profile-title: RAF SUBSCRIPTION 💙
    #profile-update-interval: 1
    #subscription-userinfo: upload=0; download=0; total=0; expire=...
    #hide-settings: 1
    #happ-hide-settings: true
    #hide_server_settings: true
    #hidesettings: true
    #announce: 🟢 Подписка активна • до ... • 🆔 ID: ...
    """
    expire_timestamp = int(
        expires_at.timestamp()
    )
    expire_text = expires_at.strftime(
        "%d.%m.%Y"
    )
    profile = (
        "#profile-title: RAF SUBSCRIPTION 💙\n"
        "#profile-update-interval: 1\n"
        f"#subscription-userinfo: "
        f"upload=0; download=0; total=0; "
        f"expire={expire_timestamp}\n"
        "#hide-settings: 1\n"
        "#happ-hide-settings: true\n"
        "#hide_server_settings: true\n"
        "#hidesettings: true\n"
        f"#announce: 🟢 Подписка активна • "
        f"до {expire_text} • 🆔 ID: {user_id}\n"
        "\n"
    )
    raw = profile + "\n".join(servers)
    return base64.b64encode(
        raw.encode("utf-8")
    ).decode("utf-8")
# =========================================================
# SUB URL
# =========================================================
def subscription_url(token):
    return (
        f"{PUBLIC_URL.rstrip('/')}"
        f"/sub/{token}"
    )
# =========================================================
# ВЫДАЧА ПОДПИСКИ
# =========================================================
async def create_or_extend_subscription(
    user_id: int,
    days: int,
    use_new_servers=True
):
    servers = await fetch_servers()
    selected_servers = choose_servers(
        servers
    )
    existing = get_subscription(
        user_id
    )
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
        start_date = max(
            now,
            old_expires
        )
        token = existing["sub_token"]
    else:
        start_date = now
        token = generate_token()
    expires_at = (
        start_date +
        timedelta(days=days)
    )
    content = make_subscription_content(
        selected_servers,
        user_id,
        expires_at
    )
    save_subscription(
        user_id=user_id,
        expires_at=expires_at.isoformat(),
        sub_token=token,
        content=content
    )
    return expires_at, token
# =========================================================
# BOT
# =========================================================
bot = Bot(BOT_TOKEN)
dp = Dispatcher()
# =========================================================
# КЛАВИАТУРЫ
# =========================================================
def main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🎁 Пробный период",
        callback_data="trial"
    )
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
        text="👥 Пользователи",
        callback_data="admin_users"
    )
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
def admin_user_keyboard(user_id):
    builder = InlineKeyboardBuilder()
    builder.button(
        text="➕ 7 дней",
        callback_data=f"user_add7:{user_id}"
    )
    builder.button(
        text="➕ 30 дней",
        callback_data=f"user_add30:{user_id}"
    )
    builder.button(
        text="❌ Забрать подписку",
        callback_data=f"user_revoke:{user_id}"
    )
    user = get_user(user_id)
    if user and user["blocked"]:
        builder.button(
            text="✅ Разблокировать",
            callback_data=f"user_unblock:{user_id}"
        )
    else:
        builder.button(
            text="🚫 Заблокировать",
            callback_data=f"user_block:{user_id}"
        )
    builder.button(
        text="◀️ К пользователям",
        callback_data="admin_users"
    )
    builder.adjust(2, 1, 1, 1)
    return builder.as_markup()
# =========================================================
# /START
# =========================================================
@dp.message(Command("start"))
async def start_handler(
    message: Message
):
    save_user(message)
    if is_blocked(
        message.from_user.id
    ):
        await message.answer(
            "🚫 Вы заблокированы."
        )
        return
    await message.answer(
        f"🔐 <b>{BOT_NAME}</b>\n\n"
        "Быстрый VPN с подпиской "
        "через Telegram Stars.\n\n"
        f"🎁 Пробный период: "
        f"<b>{TRIAL_DAYS} дня</b>\n"
        f"💰 Цена: "
        f"<b>{PRICE_STARS} Stars</b>\n"
        f"⏳ Подписка: "
        f"<b>{SUBSCRIPTION_DAYS} дней</b>\n\n"
        "Выберите действие:",
        reply_markup=main_keyboard()
    )
# =========================================================
# ПРОБНЫЙ ПЕРИОД
# =========================================================
@dp.callback_query(F.data == "trial")
async def trial_handler(
    callback: CallbackQuery
):
    user_id = callback.from_user.id
    if is_blocked(user_id):
        await callback.answer(
            "🚫 Вы заблокированы.",
            show_alert=True
        )
        return
    user = get_user(user_id)
    if not user:
        await callback.answer(
            "Сначала нажмите /start",
            show_alert=True
        )
        return
    if user["trial_used"]:
        await callback.answer(
            "Вы уже использовали пробный период.",
            show_alert=True
        )
        return
    existing = get_subscription(user_id)
    if existing:
        try:
            expires = datetime.fromisoformat(
                existing["expires_at"]
            )
            if expires.tzinfo is None:
                expires = expires.replace(
                    tzinfo=timezone.utc
                )
            if expires > datetime.now(timezone.utc):
                await callback.answer(
                    "У вас уже есть активная подписка.",
                    show_alert=True
                )
                return
        except Exception:
            pass
    await callback.answer(
        "Выдаю пробную подписку..."
    )
    try:
        expires_at, token = (
            await create_or_extend_subscription(
                user_id,
                TRIAL_DAYS
            )
        )
        mark_trial_used(user_id)
        url = subscription_url(token)
        await callback.message.answer(
            f"🎁 <b>Пробный период активирован!</b>\n\n"
            f"🔐 {BOT_NAME}\n"
            f"⏳ До: "
            f"<b>{expires_at.strftime('%d.%m.%Y %H:%M')} UTC</b>\n\n"
            "🔗 <b>Ссылка:</b>\n"
            f"<code>{url}</code>\n\n"
            "После окончания можно купить полную подписку."
        )
    except Exception as e:
        print(
            "Ошибка trial:",
            e
        )
        await callback.message.answer(
            "❌ Не удалось выдать пробную подписку."
        )
# =========================================================
# ПОКУПКА
# =========================================================
@dp.callback_query(F.data == "buy")
async def buy_handler(
    callback: CallbackQuery
):
    user_id = callback.from_user.id
    if is_blocked(user_id):
        await callback.answer(
            "🚫 Вы заблокированы.",
            show_alert=True
        )
        return
    await callback.answer()
    prices = [
        LabeledPrice(
            label=(
                f"{BOT_NAME} — "
                f"{SUBSCRIPTION_DAYS} дней"
            ),
            amount=PRICE_STARS
        )
    ]
    await bot.send_invoice(
        chat_id=user_id,
        title=f"{BOT_NAME} — VPN",
        description=(
            f"VPN подписка на "
            f"{SUBSCRIPTION_DAYS} дней."
        ),
        payload=f"rafvpn:{user_id}",
        provider_token="",
        currency="XTR",
        prices=prices
    )
# =========================================================
# PRE CHECKOUT
# =========================================================
@dp.pre_checkout_query()
async def pre_checkout_handler(
    query: PreCheckoutQuery
):
    user_id = query.from_user.id
    if is_blocked(user_id):
        await query.answer(
            ok=False,
            error_message="Вы заблокированы."
        )
        return
    await query.answer(
        ok=True
    )
# =========================================================
# УСПЕШНАЯ ОПЛАТА
# =========================================================
@dp.message(F.successful_payment)
async def successful_payment_handler(
    message: Message
):
    user_id = message.from_user.id
    if is_blocked(user_id):
        return
    try:
        expires_at, token = (
            await create_or_extend_subscription(
                user_id,
                SUBSCRIPTION_DAYS
            )
        )
        url = subscription_url(token)
        await message.answer(
            f"✅ <b>Оплата получена!</b>\n\n"
            f"🔐 {BOT_NAME}\n"
            f"⏳ Действует до:\n"
            f"<b>{expires_at.strftime('%d.%m.%Y %H:%M')} UTC</b>\n\n"
            "🔗 <b>Ссылка на подписку:</b>\n"
            f"<code>{url}</code>\n\n"
            "Добавьте эту ссылку "
            "в ваше VPN-приложение."
        )
    except Exception as e:
        print(
            "Ошибка выдачи подписки:",
            e
        )
        await message.answer(
            "⚠️ Оплата получена, "
            "но подписку автоматически выдать "
            "не удалось.\n\n"
            "Администратор сможет выдать её "
            "через админ-панель."
        )
# =========================================================
# МОЯ ПОДПИСКА
# =========================================================
@dp.callback_query(F.data == "my_sub")
async def my_subscription_handler(
    callback: CallbackQuery
):
    user_id = callback.from_user.id
    if is_blocked(user_id):
        await callback.answer(
            "🚫 Вы заблокированы.",
            show_alert=True
        )
        return
    await callback.answer()
    sub = get_subscription(
        user_id
    )
    if not sub:
        await callback.message.answer(
            "❌ Активной подписки нет.",
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
    if (
        not expires
        or expires <= datetime.now(timezone.utc)
    ):
        await callback.message.answer(
            "❌ Ваша подписка закончилась.\n\n"
            "Можно оформить новую подписку.",
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
# ПОМОЩЬ
# =========================================================
@dp.callback_query(F.data == "help")
async def help_handler(
    callback: CallbackQuery
):
    await callback.answer()
    await callback.message.answer(
        f"ℹ️ <b>{BOT_NAME}</b>\n\n"
        "1. Получите пробный период "
        "или купите подписку.\n"
        "2. Скопируйте ссылку.\n"
        "3. Добавьте её в VPN-приложение.\n\n"
        f"🎁 Пробный период: "
        f"{TRIAL_DAYS} дня.\n"
        f"💰 Полная подписка: "
        f"{PRICE_STARS} Stars / "
        f"{SUBSCRIPTION_DAYS} дней."
    )
# =========================================================
# ADMIN
# =========================================================
@dp.message(Command("admin"))
async def admin_handler(
    message: Message
):
    if not is_admin(message.from_user.id):
        await message.answer(
            "⛔ Доступ запрещён."
        )
        return
    await message.answer(
        "🛠 <b>RAF VPN — админ-панель</b>\n\n"
        "Здесь можно управлять "
        "пользователями и серверами.",
        reply_markup=admin_keyboard()
    )
# =========================================================
# ADMIN USERS
# =========================================================
@dp.callback_query(F.data == "admin_users")
async def admin_users_handler(
    callback: CallbackQuery
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True
        )
        return
    await callback.answer()
    users = get_all_users()
    if not users:
        await callback.message.edit_text(
            "👥 Пользователей пока нет.",
            reply_markup=admin_keyboard()
        )
        return
    builder = InlineKeyboardBuilder()
    for user in users[:50]:
        name = (
            user["first_name"]
            or user["username"]
            or str(user["user_id"])
        )
        if user["blocked"]:
            prefix = "🚫"
        else:
            prefix = "👤"
        builder.button(
            text=f"{prefix} {name[:25]}",
            callback_data=(
                f"admin_user:{user['user_id']}"
            )
        )
    builder.button(
        text="◀️ Назад",
        callback_data="admin_back"
    )
    builder.adjust(1)
    await callback.message.edit_text(
        f"👥 <b>Пользователи</b>\n\n"
        f"Всего: <b>{len(users)}</b>\n\n"
        "Выберите пользователя:",
        reply_markup=builder.as_markup()
    )
# =========================================================
# ADMIN USER DETAILS
# =========================================================
@dp.callback_query(
    F.data.startswith("admin_user:")
)
async def admin_user_handler(
    callback: CallbackQuery
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True
        )
        return
    await callback.answer()
    try:
        user_id = int(
            callback.data.split(":")[1]
        )
    except Exception:
        return
    user = get_user(user_id)
    if not user:
        await callback.message.edit_text(
            "❌ Пользователь не найден.",
            reply_markup=admin_keyboard()
        )
        return
    sub = get_subscription(
        user_id
    )
    name = user["first_name"] or "Без имени"
    username = (
        f"@{user['username']}"
        if user["username"]
        else "нет"
    )
    if user["blocked"]:
        status = "🚫 Заблокирован"
    else:
        status = "🟢 Активен"
    if sub:
        try:
            expires = datetime.fromisoformat(
                sub["expires_at"]
            )
            if expires.tzinfo is None:
                expires = expires.replace(
                    tzinfo=timezone.utc
                )
            if expires > datetime.now(timezone.utc):
                sub_text = (
                    "🟢 Активна до "
                    f"<b>{expires.strftime('%d.%m.%Y %H:%M')} UTC</b>"
                )
            else:
                sub_text = "🔴 Истекла"
        except Exception:
            sub_text = "⚠️ Ошибка даты"
    else:
        sub_text = "❌ Нет подписки"
    trial_text = (
        "использован"
        if user["trial_used"]
        else "не использован"
    )
    await callback.message.edit_text(
        "👤 <b>Пользователь</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"👤 Имя: <b>{name}</b>\n"
        f"🔗 Username: {username}\n"
        f"📅 Регистрация: "
        f"{user['created_at'][:10]}\n\n"
        f"📦 Подписка: {sub_text}\n"
        f"🎁 Пробный период: {trial_text}\n"
        f"📌 Статус: {status}",
        reply_markup=admin_user_keyboard(
            user_id
        )
    )
# =========================================================
# ADMIN ADD 7 DAYS
# =========================================================
@dp.callback_query(
    F.data.startswith("user_add7:")
)
async def admin_add7_handler(
    callback: CallbackQuery
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True
        )
        return
    user_id = int(
        callback.data.split(":")[1]
    )
    try:
        expires_at, token = (
            await create_or_extend_subscription(
                user_id,
                7
            )
        )
        await callback.answer(
            "✅ Выдано 7 дней"
        )
        await callback.message.edit_text(
            "✅ <b>Подписка выдана</b>\n\n"
            f"👤 ID: <code>{user_id}</code>\n"
            f"⏳ До: "
            f"<b>{expires_at.strftime('%d.%m.%Y %H:%M')} UTC</b>",
            reply_markup=admin_user_keyboard(
                user_id
            )
        )
    except Exception as e:
        print(e)
        await callback.answer(
            "Ошибка выдачи",
            show_alert=True
        )
# =========================================================
# ADMIN ADD 30 DAYS
# =========================================================
@dp.callback_query(
    F.data.startswith("user_add30:")
)
async def admin_add30_handler(
    callback: CallbackQuery
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True
        )
        return
    user_id = int(
        callback.data.split(":")[1]
    )
    try:
        expires_at, token = (
            await create_or_extend_subscription(
                user_id,
                30
            )
        )
        await callback.answer(
            "✅ Выдано 30 дней"
        )
        await callback.message.edit_text(
            "✅ <b>Подписка выдана</b>\n\n"
            f"👤 ID: <code>{user_id}</code>\n"
            f"⏳ До: "
            f"<b>{expires_at.strftime('%d.%m.%Y %H:%M')} UTC</b>",
            reply_markup=admin_user_keyboard(
                user_id
            )
        )
    except Exception as e:
        print(e)
        await callback.answer(
            "Ошибка выдачи",
            show_alert=True
        )
# =========================================================
# ADMIN REVOKE
# =========================================================
@dp.callback_query(
    F.data.startswith("user_revoke:")
)
async def admin_revoke_handler(
    callback: CallbackQuery
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True
        )
        return
    user_id = int(
        callback.data.split(":")[1]
    )
    delete_subscription(
        user_id
    )
    await callback.answer(
        "❌ Подписка забрана"
    )
    await callback.message.edit_text(
        f"❌ <b>Подписка пользователя забрана</b>\n\n"
        f"ID: <code>{user_id}</code>",
        reply_markup=admin_user_keyboard(
            user_id
        )
    )
# =========================================================
# ADMIN BLOCK
# =========================================================
@dp.callback_query(
    F.data.startswith("user_block:")
)
async def admin_block_handler(
    callback: CallbackQuery
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True
        )
        return
    user_id = int(
        callback.data.split(":")[1]
    )
    if is_admin(user_id):
        await callback.answer(
            "Нельзя заблокировать администратора.",
            show_alert=True
        )
        return
    set_blocked(
        user_id,
        True
    )
    await callback.answer(
        "🚫 Пользователь заблокирован"
    )
    await callback.message.edit_text(
        "🚫 <b>Пользователь заблокирован</b>\n\n"
        f"ID: <code>{user_id}</code>",
        reply_markup=admin_user_keyboard(
            user_id
        )
    )
# =========================================================
# ADMIN UNBLOCK
# =========================================================
@dp.callback_query(
    F.data.startswith("user_unblock:")
)
async def admin_unblock_handler(
    callback: CallbackQuery
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True
        )
        return
    user_id = int(
        callback.data.split(":")[1]
    )
    set_blocked(
        user_id,
        False
    )
    await callback.answer(
        "✅ Пользователь разблокирован"
    )
    await callback.message.edit_text(
        "✅ <b>Пользователь разблокирован</b>\n\n"
        f"ID: <code>{user_id}</code>",
        reply_markup=admin_user_keyboard(
            user_id
        )
    )
# =========================================================
# ADMIN BACK
# =========================================================
@dp.callback_query(F.data == "admin_back")
async def admin_back_handler(
    callback: CallbackQuery
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True
        )
        return
    await callback.answer()
    await callback.message.edit_text(
        "🛠 <b>RAF VPN — админ-панель</b>",
        reply_markup=admin_keyboard()
    )
# =========================================================
# ADMIN REFRESH SERVERS
# =========================================================
@dp.callback_query(
    F.data == "admin_refresh"
)
async def admin_refresh_handler(
    callback: CallbackQuery
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True
        )
        return
    await callback.answer(
        "Обновляю серверы..."
    )
    try:
        all_servers = await fetch_servers()
        active_subs = (
            get_active_subscriptions()
        )
        updated = 0
        for sub in active_subs:
            selected_servers = choose_servers(
                all_servers
            )
            try:
                expires_at = datetime.fromisoformat(
                    sub["expires_at"]
                )
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(
                        tzinfo=timezone.utc
                    )
            except Exception:
                continue
            content = make_subscription_content(
                selected_servers,
                sub["user_id"],
                expires_at
            )
            update_subscription_content(
                sub["user_id"],
                content
            )
            updated += 1
        await callback.message.answer(
            "✅ <b>Серверы обновлены</b>\n\n"
            f"🌐 Серверов в GitHub: "
            f"<b>{len(all_servers)}</b>\n"
            f"👥 Активных подписок: "
            f"<b>{updated}</b>\n\n"
            "Каждый пользователь получил "
            "новый случайный набор серверов.\n\n"
            "🔗 Ссылки подписок "
            "<b>не изменились</b>."
        )
    except Exception as e:
        print(
            "Ошибка обновления:",
            e
        )
        await callback.message.answer(
            "❌ Ошибка обновления серверов.\n\n"
            f"<code>{str(e)[:1000]}</code>"
        )
# =========================================================
# ADMIN STATS
# =========================================================
@dp.callback_query(
    F.data == "admin_stats"
)
async def admin_stats_handler(
    callback: CallbackQuery
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True
        )
        return
    users, subscriptions, active, blocked = (
        get_stats()
    )
    await callback.answer()
    await callback.message.answer(
        "📊 <b>RAF VPN — статистика</b>\n\n"
        f"👤 Пользователей: <b>{users}</b>\n"
        f"📦 Подписок: <b>{subscriptions}</b>\n"
        f"🟢 Активных: <b>{active}</b>\n"
        f"🚫 Заблокировано: <b>{blocked}</b>\n\n"
        f"🎁 Trial: <b>{TRIAL_DAYS} дня</b>\n"
        f"💰 Цена: <b>{PRICE_STARS} Stars</b>\n"
        f"⏳ Срок: <b>{SUBSCRIPTION_DAYS} дней</b>"
    )
# =========================================================
# WEB / RENDER
# =========================================================
async def health_handler(request):
    return web.Response(
        text="RAF VPN OK"
    )
async def subscription_handler(request):
    token = request.match_info.get(
        "token",
        ""
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
    if expires <= datetime.now(
        timezone.utc
    ):
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
# MAIN
# =========================================================
async def main():
    check_config()
    init_db()
    print("=" * 50)
    print(f"{BOT_NAME} starting")
    print(f"Version: {VERSION}")
    print(
        f"GitHub: {github_raw_url()}"
    )
    print(
        f"Price: {PRICE_STARS} Stars"
    )
    print(
        f"Subscription: "
        f"{SUBSCRIPTION_DAYS} days"
    )
    print(
        f"Trial: {TRIAL_DAYS} days"
    )
    print(
        f"Servers per user: "
        f"{SERVERS_PER_USER}"
    )
    print(
        f"Public URL: {PUBLIC_URL}"
    )
    print("=" * 50)
    await start_web_server()
    print(
        "Telegram polling started"
    )
    await dp.start_polling(bot)
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(
            "RAF VPN stopped"
        )