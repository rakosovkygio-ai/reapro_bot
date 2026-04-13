import os
import logging
import asyncio
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

REMINDER_POLL_SECONDS = 60

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


def wp_fetch_reminders() -> tuple[list[dict], str | None]:
    url = f"{WP_API_BASE}/telegram/reminders"

    try:
        response = requests.get(url, timeout=20)

        try:
            data = response.json()
        except ValueError:
            data = None

        if response.status_code != 200:
            if data and isinstance(data, dict):
                return [], data.get("message") or f"HTTP {response.status_code}"
            return [], f"HTTP {response.status_code}"

        if not data or not isinstance(data, dict):
            return [], "Невалидный JSON в reminders"

        if not data.get("success"):
            return [], data.get("message") or "success=false в reminders"

        items = data.get("items") or []
        if not isinstance(items, list):
            return [], "items не является массивом"

        return items, None

    except requests.Timeout:
        logger.exception("WP reminders timeout")
        return [], "WordPress reminders timeout"
    except requests.RequestException as e:
        logger.exception("WP reminders request failed: %s", e)
        return [], str(e)
    except Exception as e:
        logger.exception("WP reminders failed: %s", e)
        return [], str(e)


def wp_mark_reminder_sent(appointment_id: int, reminder_type: str) -> bool:
    url = f"{WP_API_BASE}/telegram/reminders/mark"
    payload = {
        "appointment_id": int(appointment_id),
        "reminder_type": reminder_type,
    }

    try:
        response = requests.post(url, json=payload, timeout=20)

        try:
            data = response.json()
        except ValueError:
            data = None

        if response.status_code != 200:
            logger.warning("Mark reminder failed: HTTP %s %s", response.status_code, response.text)
            return False

        if not data or not isinstance(data, dict):
            logger.warning("Mark reminder failed: invalid JSON")
            return False

        return bool(data.get("success"))

    except Exception as e:
        logger.exception("Mark reminder exception: %s", e)
        return False


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


def format_reminder_text(item: dict) -> str:
    reminder_type = (item.get("reminder_type") or "").strip()
    employee_name = item.get("employee_name", "—")
    service_name = item.get("service_name", "—")
    appointment_date = item.get("appointment_date", "—")
    start_time = item.get("start_time", "—")
    end_time = item.get("end_time", "—")

    title = "⏰ Напоминание о записи"
    if reminder_type == "24h":
        subtitle = "До вашей записи остались сутки."
    elif reminder_type == "1h":
        subtitle = "До вашей записи остался 1 час."
    else:
        subtitle = "Напоминаем о вашей записи."

    return (
        f"{title}\n\n"
        f"{subtitle}\n\n"
        f"Услуга: {service_name}\n"
        f"Специалист: {employee_name}\n"
        f"Дата: {appointment_date}\n"
        f"Время: {start_time}–{end_time}"
    )


async def send_booking_extras(message, booking: dict) -> None:
    address = (booking.get("address") or "").strip()
    specialist_phone = (booking.get("specialist_phone") or "").strip()
    map_link = (booking.get("map_link") or "").strip()

    if address:
        await message.reply_text(f"📍 Адрес: {address}")

    if specialist_phone:
        clean_phone = specialist_phone.replace('-', '').replace(' ', '')
    
        await message.reply_text(
            "📞 Телефон специалиста:\n"
            f"`{clean_phone}`\n\n"
            "Нажмите, чтобы скопировать",
            parse_mode="Markdown"
        )

    if map_link:
        await message.reply_text(f"🗺 Как добраться:\n{map_link}")


async def send_reminder_extras(bot, chat_id: int, item: dict) -> None:
    address = (item.get("address") or "").strip()
    specialist_phone = (item.get("specialist_phone") or "").strip()
    map_link = (item.get("map_link") or "").strip()

    if address:
        await bot.send_message(chat_id=chat_id, text=f"📍 Адрес: {address}")

    if specialist_phone:
        clean_phone = specialist_phone.replace('-', '').replace(' ', '')
    
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "📞 Телефон специалиста:\n"
                f"`{clean_phone}`\n\n"
                "Нажмите, чтобы скопировать"
            ),
            parse_mode="Markdown"
        )

    if map_link:
        await bot.send_message(chat_id=chat_id, text=f"🗺 Как добраться:\n{map_link}")


async def reminder_loop(application) -> None:
    logger.info("Reminder loop started")

    while True:
        try:
            items, error = await asyncio.to_thread(wp_fetch_reminders)

            if error:
                logger.warning("Reminder fetch error: %s", error)
            else:
                for item in items:
                    try:
                        appointment_id = int(item.get("appointment_id", 0))
                        reminder_type = (item.get("reminder_type") or "").strip()
                        telegram_chat_id = item.get("telegram_chat_id")

                        if not appointment_id or not reminder_type or not telegram_chat_id:
                            continue

                        chat_id = int(str(telegram_chat_id).strip())

                        await application.bot.send_message(
                            chat_id=chat_id,
                            text=format_reminder_text(item),
                        )
                        await send_reminder_extras(application.bot, chat_id, item)

                        marked = await asyncio.to_thread(
                            wp_mark_reminder_sent,
                            appointment_id,
                            reminder_type,
                        )

                        if not marked:
                            logger.warning(
                                "Reminder sent but mark failed: appointment_id=%s reminder_type=%s",
                                appointment_id,
                                reminder_type,
                            )

                    except Exception as item_error:
                        logger.exception("Reminder item processing failed: %s", item_error)

        except Exception as loop_error:
            logger.exception("Reminder loop failed: %s", loop_error)

        await asyncio.sleep(REMINDER_POLL_SECONDS)


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


async def post_init(application) -> None:
    application.bot_data["reminder_task"] = asyncio.create_task(reminder_loop(application))


async def post_shutdown(application) -> None:
    task = application.bot_data.get("reminder_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def validate_env() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")
    if not WP_API_BASE:
        raise RuntimeError("WP_API_BASE не задан")


def run() -> None:
    validate_env()

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("test", test_command))

    logger.info("Booking bot started")
    app.run_polling()


if __name__ == "__main__":
    run()