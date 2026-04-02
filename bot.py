import os
import logging
import requests

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WP_API_BASE = os.environ.get("WP_API_BASE", "").strip()
BOT_USERNAME = os.environ.get("BOT_USERNAME", "").strip()

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def wp_resolve_booking(token: str, telegram_chat_id: int) -> dict | None:
    try:
        url = f"{WP_API_BASE}/telegram/resolve"

        print("REQUEST URL:", url)
        print("REQUEST TOKEN:", token)
        print("REQUEST TELEGRAM ID:", telegram_chat_id)

        response = requests.post(
            url,
            json={
                "token": token,
                "telegram_chat_id": str(telegram_chat_id),
            },
            timeout=20,
        )

        print("STATUS CODE:", response.status_code)
        print("RESPONSE TEXT:", response.text)

        if response.status_code != 200:
            return None

        data = response.json()
        print("JSON DATA:", data)

        if not data.get("success"):
            return None

        return data.get("booking")

    except Exception as e:
        print("WP RESOLVE EXCEPTION:", repr(e))
        logger.exception("WP resolve failed: %s", e)
        return None


def format_booking_text(booking: dict) -> str:
    client_name = booking.get("client_name", "—")
    employee_name = booking.get("employee_name", "—")
    service_name = booking.get("service_name", "—")
    appointment_date = booking.get("appointment_date", "—")
    start_time = booking.get("start_time", "—")
    end_time = booking.get("end_time", "—")

    return (
        "✅ Ваша запись найдена\n\n"
        f"Клиент: {client_name}\n"
        f"Услуга: {service_name}\n"
        f"Специалист: {employee_name}\n"
        f"Дата: {appointment_date}\n"
        f"Время: {start_time}–{end_time}\n\n"
        "Telegram-напоминания подключены."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    user = update.effective_user
    if not user:
        return

    telegram_chat_id = user.id
    payload = context.args[0].strip() if context.args else ""

    print("START CALLED")
    print("USER ID:", telegram_chat_id)
    print("PAYLOAD:", payload)

    if payload.startswith("mb_"):
        token = payload[3:]

        await update.message.reply_text(f"Токен получен: {token}")
        await update.message.reply_text("Проверяю запись в WordPress...")

        booking = wp_resolve_booking(token=token, telegram_chat_id=telegram_chat_id)

        print("BOOKING RESULT:", booking)

        if booking:
            await update.message.reply_text(format_booking_text(booking))
            return

        await update.message.reply_text(
            "⚠️ WordPress не вернул запись.\n"
            "Смотри консоль бота и проверь endpoint."
        )
        return

    await update.message.reply_text(
        "Бот работает.\n"
        "Токен не передан."
    )

    deep_link_hint = (
        f"https://t.me/{BOT_USERNAME}?start=mb_TESTTOKEN"
        if BOT_USERNAME
        else "https://t.me/<bot_username>?start=mb_TESTTOKEN"
    )

    await update.message.reply_text(
        "Бот работает.\n\n"
        "Токен не передан.\n"
        "Для теста открой deep link:\n"
        f"{deep_link_hint}"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    await update.message.reply_text(
        "Как это работает:\n\n"
        "1. Сайт создаёт запись и telegram_token\n"
        "2. Кнопка на сайте ведёт в Telegram по ссылке:\n"
        "   https://t.me/<bot_username>?start=mb_TOKEN\n"
        "3. После /start бот получает TOKEN\n"
        "4. Бот отправляет TOKEN в WordPress\n"
        "5. WordPress находит запись и сохраняет ваш telegram_chat_id\n"
        "6. Бот показывает детали записи"
    )


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Ручной тест:
    /test TOKEN
    """
    if not update.message:
        return

    user = update.effective_user
    if not user:
        return

    if not context.args:
        await update.message.reply_text("Использование: /test TOKEN")
        return

    token = context.args[0].strip()

    await update.message.reply_text(f"Проверяю токен: {token}")

    booking = wp_resolve_booking(token=token, telegram_chat_id=user.id)

    if booking:
        await update.message.reply_text("Тест успешен.\n\n" + format_booking_text(booking))
    else:
        await update.message.reply_text(
            "Тест неуспешен.\n"
            "WordPress не вернул запись или endpoint ещё не настроен."
        )


def validate_env() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")
    if not WP_API_BASE:
        raise RuntimeError("WP_API_BASE не задан")


def run() -> None:
    validate_env()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("test", test_command))

    print("БОТ ЗАПУЩЕН 🚀")
    logger.info("Booking bot started")

    app.run_polling()


if __name__ == "__main__":
    run()