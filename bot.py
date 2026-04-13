import os
import logging
import asyncio
import requests

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WP_API_BASE = os.environ.get("WP_API_BASE", "").strip().rstrip("/")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "").strip()
TELEGRAM_ADMIN_SECRET = os.environ.get("TELEGRAM_ADMIN_SECRET", "").strip()

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
        response = requests.post(url, json=payload, timeout=20)
        data = response.json()

        if response.status_code != 200:
            return None, data.get("message") if isinstance(data, dict) else f"HTTP {response.status_code}"

        if not isinstance(data, dict) or not data.get("success"):
            return None, "resolve failed"

        return data.get("booking"), None

    except Exception as e:
        logger.exception("wp_resolve_booking failed: %s", e)
        return None, str(e)


def wp_fetch_reminders() -> tuple[list[dict], str | None]:
    url = f"{WP_API_BASE}/telegram/reminders"

    try:
        response = requests.get(url, timeout=20)
        data = response.json()

        if response.status_code != 200:
            return [], data.get("message") if isinstance(data, dict) else f"HTTP {response.status_code}"

        if not isinstance(data, dict) or not data.get("success"):
            return [], "reminders failed"

        items = data.get("items") or []
        return items if isinstance(items, list) else [], None

    except Exception as e:
        logger.exception("wp_fetch_reminders failed: %s", e)
        return [], str(e)


def wp_mark_reminder_sent(appointment_id: int, reminder_type: str) -> bool:
    url = f"{WP_API_BASE}/telegram/reminders/mark"
    payload = {
        "appointment_id": int(appointment_id),
        "reminder_type": reminder_type,
    }

    try:
        response = requests.post(url, json=payload, timeout=20)
        data = response.json()
        return response.status_code == 200 and isinstance(data, dict) and bool(data.get("success"))
    except Exception as e:
        logger.exception("wp_mark_reminder_sent failed: %s", e)
        return False


def wp_admin_action(appointment_id: int, action: str) -> tuple[dict | None, str | None]:
    url = f"{WP_API_BASE}/telegram/admin-action"
    payload = {
        "appointment_id": int(appointment_id),
        "action": action,
        "secret": TELEGRAM_ADMIN_SECRET,
    }

    try:
        response = requests.post(url, json=payload, timeout=20)
        data = response.json()

        if response.status_code != 200:
            return None, data.get("message") if isinstance(data, dict) else f"HTTP {response.status_code}"

        if not isinstance(data, dict) or not data.get("success"):
            return None, "admin action failed"

        return data.get("appointment"), None

    except Exception as e:
        logger.exception("wp_admin_action failed: %s", e)
        return None, str(e)


def format_booking_text(booking: dict) -> str:
    return (
        "✅ Ваша запись подтверждена\n\n"
        f"Услуга: {booking.get('service_name', '—')}\n"
        f"Специалист: {booking.get('employee_name', '—')}\n"
        f"Дата: {booking.get('appointment_date', '—')}\n"
        f"Время: {booking.get('start_time', '—')}–{booking.get('end_time', '—')}\n\n"
        "Telegram-напоминания подключены."
    )


def format_reminder_text(item: dict) -> str:
    reminder_type = (item.get("reminder_type") or "").strip()

    subtitle = "Напоминаем о вашей записи."
    if reminder_type == "24h":
        subtitle = "До вашей записи остались сутки."
    elif reminder_type == "1h":
        subtitle = "До вашей записи остался 1 час."

    return (
        "⏰ Напоминание о записи\n\n"
        f"{subtitle}\n\n"
        f"Услуга: {item.get('service_name', '—')}\n"
        f"Специалист: {item.get('employee_name', '—')}\n"
        f"Дата: {item.get('appointment_date', '—')}\n"
        f"Время: {item.get('start_time', '—')}–{item.get('end_time', '—')}"
    )


def format_client_status_text(appointment: dict) -> str:
    status = appointment.get("status", "")
    title = "✅ Ваша запись подтверждена" if status == "confirmed" else "❌ Ваша запись отменена"

    return (
        f"{title}\n\n"
        f"Услуга: {appointment.get('service_name', '—')}\n"
        f"Специалист: {appointment.get('employee_name', '—')}\n"
        f"Дата: {appointment.get('appointment_date', '—')}\n"
        f"Время: {appointment.get('start_time', '—')}–{appointment.get('end_time', '—')}"
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

            if not error:
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

                        await asyncio.to_thread(
                            wp_mark_reminder_sent,
                            appointment_id,
                            reminder_type,
                        )
                    except Exception as item_error:
                        logger.exception("Reminder item failed: %s", item_error)
            else:
                logger.warning("Reminder fetch error: %s", error)

        except Exception as loop_error:
            logger.exception("Reminder loop failed: %s", loop_error)

        await asyncio.sleep(REMINDER_POLL_SECONDS)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    telegram_chat_id = update.effective_user.id
    payload = context.args[0].strip() if context.args else ""

    if payload:
        token = normalize_token(payload)
        booking, error_message = wp_resolve_booking(token=token, telegram_chat_id=telegram_chat_id)

        if booking:
            await update.message.reply_text(format_booking_text(booking))
            await send_booking_extras(update.message, booking)
            return

        await update.message.reply_text("Не удалось найти вашу запись. Пожалуйста, вернитесь на сайт и попробуйте снова.")
        logger.warning("Booking resolve failed: %s", error_message)
        return

    await update.message.reply_text("Здравствуйте. Перейдите в бот по ссылке с сайта, чтобы подтвердить запись.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    await update.message.reply_text("Чтобы подключить Telegram-напоминания, перейдите в бот по ссылке после записи на сайте.")


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    if not context.args:
        await update.message.reply_text("Использование: /test TOKEN")
        return

    token = normalize_token(context.args[0])
    booking, error_message = wp_resolve_booking(token=token, telegram_chat_id=update.effective_user.id)

    if booking:
        await update.message.reply_text(format_booking_text(booking))
        await send_booking_extras(update.message, booking)
    else:
        await update.message.reply_text(f"Тест неуспешен.\nПричина: {error_message or 'endpoint ещё не настроен'}")


async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data = query.data or ''
    if ':' not in data:
        return

    action, appointment_id_raw = data.split(':', 1)

    if action not in ['confirm', 'cancel']:
        return

    try:
        appointment_id = int(appointment_id_raw)
    except ValueError:
        await query.edit_message_text("Некорректный ID записи.")
        return

    appointment, error = wp_admin_action(appointment_id, action)

    if not appointment:
        await query.edit_message_text(f"Ошибка: {error or 'не удалось выполнить действие'}")
        return

    status_label = 'подтверждена' if action == 'confirm' else 'отменена'

    await query.edit_message_text(
        query.message.text_html + f"\n\n<b>Статус:</b> {status_label}",
        parse_mode="HTML"
    )

    client_chat_id = (appointment.get("client_telegram_chat_id") or '').strip()
    if client_chat_id:
        try:
            await context.bot.send_message(
                chat_id=int(client_chat_id),
                text=format_client_status_text(appointment),
            )
        except Exception as e:
            logger.exception("Failed to notify client after admin action: %s", e)


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
    if not TELEGRAM_ADMIN_SECRET:
        raise RuntimeError("TELEGRAM_ADMIN_SECRET не задан")


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
    app.add_handler(CallbackQueryHandler(handle_admin_callback))

    logger.info("Booking bot started")
    app.run_polling()


if __name__ == "__main__":
    run()