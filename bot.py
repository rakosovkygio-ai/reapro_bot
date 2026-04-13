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
        logger.info("Resolving booking via %s", url)

        response = requests.post(
            url,
            json=payload,
            timeout=20,
        )

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
    employee_name = booking.get("employee_name", "—")
    service_name = booking.get("service_name", "—")
    appointment_date = booking.get("appointment_date", "—")
    start_time = booking.get("start_time", "—")
    end_time = booking.get("end_time", "—")

    return (
        "✅ Ваша запись подтверждена\n\n"
        f"Услуга: {service_name}\n"
        f"Специалист: {employee_name}\n"
        f"Дата: {appointment_date}\n"
        f"Время: {start_time}–{end_time}\n\n"
        "Telegram-напоминания подключены."
    )


async def send_booking_extras(message, booking: dict) -> None:
    latitude = booking.get("location_lat")
    longitude = booking.get("location_lng")
    address = (booking.get("address") or "").strip()
    specialist_phone = (booking.get("specialist_phone") or "").strip()
    map_link = (booking.get("map_link") or "").strip()

    if latitude and longitude:
        try:
            await message.reply_location(
                latitude=float(latitude),
                longitude=float(longitude),
            )
        except Exception as e:
            logger.warning("Failed to send location: %s", e)

    if address:
        await message.reply_text(f"📍 Адрес: {address}")

    if specialist_phone:
        await message.reply_text(f"📞 Телефон специалиста: {specialist_phone}")

    if map_link:
        await message.reply_text(f"🗺 Как добраться:\n{map_link}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    user = update.effective_user
    if not user:
        return

    telegram_chat_id = user.id
    payload = context.args[0].strip() if context.args else ""

    if payload:
        token = normalize_token(payload)

        booking, error_message = wp_resolve_booking(
            token=token,
            telegram_chat_id=telegram_chat_id,
        )

        if booking:
            await update.message.reply_text(format_booking_text(booking))
            await send_booking_extras(update.message, booking)
            return

        await update.message.reply_text(
            "Не удалось найти вашу запись. Пожалуйста, вернитесь на сайт и попробуйте снова."
        )
        logger.warning("Booking resolve failed: %s", error_message)
        return

    await update.message.reply_text(
        "Здравствуйте. Перейдите в бот по ссылке с сайта, чтобы подтвердить запись."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    await update.message.reply_text(
        "Чтобы подключить Telegram-напоминания, перейдите в бот по ссылке после записи на сайте."
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

    booking, error_message = wp_resolve_booking(token=token, telegram_chat_id=user.id)

    if booking:
        await update.message.reply_text(format_booking_text(booking))
        await send_booking_extras(update.message, booking)
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

    logger.info("Booking bot started")
    app.run_polling()


if __name__ == "__main__":
    run()