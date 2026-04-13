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
WP_API_BASE = os.environ.get("WP_API_BASE", "").strip().rstrip("/")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "").strip()

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def normalize_token(token: str) -> str:
    token = (token or "").strip()

    if token.startswith("mb_"):
        token = token[3:]

    return token


def wp_resolve_booking(token: str, telegram_chat_id: int) -> tuple[dict | None, str | None]:
    token = normalize_token(token)

    if not token:
        return None, "Пустой token"

    url = f"{WP_API_BASE}/telegram/resolve"
    payload = {
        "token": token,
        "telegram_chat_id": str(telegram_chat_id),
    }

    try:
        print("REQUEST URL:", url)
        print("REQUEST JSON:", payload)

        response = requests.post(
            url,
            json=payload,
            timeout=20,
        )

        print("STATUS CODE:", response.status_code)
        print("RESPONSE TEXT:", response.text)

        try:
            data = response.json()
        except ValueError:
            data = None

        if response.status_code != 200:
            if data and isinstance(data, dict):
                message = data.get("message") or f"HTTP {response.status_code}"
                return None, f"WordPress вернул {response.status_code}: {message}"

            return None, f"WordPress вернул HTTP {response.status_code}"

        if not data or not isinstance(data, dict):
            return None, "WordPress вернул невалидный JSON"

        print("JSON DATA:", data)

        if not data.get("success"):
            return None, data.get("message") or "WordPress вернул success=false"

        booking = data.get("booking")

        if not booking:
            return None, "В ответе нет booking"

        return booking, None

    except requests.Timeout:
        logger.exception("WP resolve timeout")
        return None, "WordPress не ответил вовремя"
    except requests.RequestException as e:
        logger.exception("WP resolve request failed: %s", e)
        return None, f"Ошибка запроса к WordPress: {e}"
    except Exception as e:
        logger.exception("WP resolve failed: %s", e)
        return None, f"Неожиданная ошибка: {e}"


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
    print("RAW PAYLOAD:", payload)

    if payload:
        token = normalize_token(payload)

        print("NORMALIZED TOKEN:", token)

        await update.message.reply_text(f"Токен получен: {token}")
        await update.message.reply_text("Проверяю запись в WordPress...")

        booking, error_message = wp_resolve_booking(
            token=token,
            telegram_chat_id=telegram_chat_id,
        )

        print("BOOKING RESULT:", booking)
        print("BOOKING ERROR:", error_message)

        if booking:
            await update.message.reply_text(format_booking_text(booking))
            return

        await update.message.reply_text(
            "⚠️ Не удалось получить запись из WordPress.\n"
            f"Причина: {error_message or 'неизвестная ошибка'}"
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
    if not update.message:
        return

    user = update.effective_user
    if not user:
        return

    if not context.args:
        await update.message.reply_text("Использование: /test TOKEN")
        return

    token = normalize_token(context.args[0])

    await update.message.reply_text(f"Проверяю токен: {token}")

    booking, error_message = wp_resolve_booking(token=token, telegram_chat_id=user.id)

    if booking:
        await update.message.reply_text("Тест успешен.\n\n" + format_booking_text(booking))
    else:
        await update.message.reply_text(
            "Тест неуспешен.\n"
            f"Причина: {error_message or 'endpoint ещё не настроен'}"
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