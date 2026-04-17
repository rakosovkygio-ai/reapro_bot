import os
import logging
import asyncio
import requests

from dotenv import load_dotenv
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WP_API_BASE = os.environ.get("WP_API_BASE", "").strip().rstrip("/")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "").strip()
TELEGRAM_ADMIN_SECRET = os.environ.get("TELEGRAM_ADMIN_SECRET", "").strip()

ADMIN_TELEGRAM_IDS = {
    int(x.strip())
    for x in os.environ.get("ADMIN_TELEGRAM_IDS", "").split(",")
    if x.strip().isdigit()
}

REMINDER_POLL_SECONDS = 60

CONTACT_ADDRESS = "Ростовская обл. г. Таганрог, ул. Октябрьская 17, 2 этаж, каб. 204"
CONTACT_PHONE = "+7 995 445-85-20"
CONTACT_MAP_LINK = "https://yandex.com/maps/-/CPrtN4iF"

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

PENDING_COMPLETE_KEY = "pending_complete_amount"


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_TELEGRAM_IDS


def get_main_keyboard(user_id: int | None = None) -> ReplyKeyboardMarkup:
    if user_id is not None and is_admin(user_id):
        return ReplyKeyboardMarkup(
            [
                ["📅 Записи на сегодня"],
            ],
            resize_keyboard=True,
        )

    return ReplyKeyboardMarkup(
        [
            ["Мои записи", "Ближайшая запись"],
            ["Связаться"],
        ],
        resize_keyboard=True,
    )


def normalize_token(token: str) -> str:
    return (token or "").strip()


def normalize_amount(value: str) -> float | None:
    raw = (value or "").strip().replace(" ", "").replace(",", ".")
    if raw == "":
        return None

    try:
        amount = float(raw)
    except ValueError:
        return None

    if amount < 0:
        return None

    return amount


def format_money(amount: float | int | None) -> str:
    if amount is None:
        return "0 ₽"

    try:
        value = float(amount)
    except (TypeError, ValueError):
        return "0 ₽"

    if value.is_integer():
        return f"{int(value)} ₽"

    return f"{value:.2f} ₽"


def wp_resolve_booking(token: str, telegram_chat_id: int, telegram_user_id: int) -> tuple[dict | None, str | None]:
    token = normalize_token(token)

    if not token:
        return None, "Пустой token"

    url = f"{WP_API_BASE}/telegram/client-link"
    payload = {
        "token": token,
        "telegram_chat_id": str(telegram_chat_id),
        "telegram_user_id": str(telegram_user_id),
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


def wp_get_client_current_booking(telegram_user_id: int) -> tuple[dict | None, str | None]:
    url = f"{WP_API_BASE}/telegram/client-current"
    params = {
        "telegram_user_id": str(telegram_user_id),
    }

    try:
        response = requests.get(url, params=params, timeout=20)
        data = response.json()

        if response.status_code != 200:
            return None, data.get("message") if isinstance(data, dict) else f"HTTP {response.status_code}"

        if not isinstance(data, dict) or not data.get("success"):
            return None, "client current failed"

        return data.get("booking"), None

    except Exception as e:
        logger.exception("wp_get_client_current_booking failed: %s", e)
        return None, str(e)


def wp_get_client_bookings(telegram_user_id: int) -> tuple[list[dict], str | None]:
    url = f"{WP_API_BASE}/telegram/client-bookings"
    params = {
        "telegram_user_id": str(telegram_user_id),
    }

    try:
        response = requests.get(url, params=params, timeout=20)
        data = response.json()

        if response.status_code != 200:
            return [], data.get("message") if isinstance(data, dict) else f"HTTP {response.status_code}"

        if not isinstance(data, dict) or not data.get("success"):
            return [], "client bookings failed"

        items = data.get("items") or []
        return items if isinstance(items, list) else [], None

    except Exception as e:
        logger.exception("wp_get_client_bookings failed: %s", e)
        return [], str(e)


def wp_get_today_appointments() -> tuple[list[dict], str | None]:
    url = f"{WP_API_BASE}/telegram/today-appointments"
    params = {
        "secret": TELEGRAM_ADMIN_SECRET,
    }

    try:
        response = requests.get(url, params=params, timeout=20)
        data = response.json()

        if response.status_code != 200:
            return [], data.get("message") if isinstance(data, dict) else f"HTTP {response.status_code}"

        if not isinstance(data, dict) or not data.get("success"):
            return [], "today appointments failed"

        items = data.get("items") or []
        return items if isinstance(items, list) else [], None

    except Exception as e:
        logger.exception("wp_get_today_appointments failed: %s", e)
        return [], str(e)


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


def wp_admin_action(appointment_id: int, action: str, actual_amount: float | None = None) -> tuple[dict | None, str | None]:
    url = f"{WP_API_BASE}/telegram/admin-action"
    payload = {
        "appointment_id": int(appointment_id),
        "action": action,
        "secret": TELEGRAM_ADMIN_SECRET,
    }

    if actual_amount is not None:
        payload["actual_amount"] = actual_amount

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


def format_current_booking_text(booking: dict | None) -> str:
    if not booking:
        return "У вас нет ближайшей активной записи."

    return (
        "📅 Ваша ближайшая запись\n\n"
        f"Услуга: {booking.get('service_name', '—')}\n"
        f"Специалист: {booking.get('employee_name', '—')}\n"
        f"Дата: {booking.get('appointment_date', '—')}\n"
        f"Время: {booking.get('start_time', '—')}–{booking.get('end_time', '—')}"
    )


def format_bookings_list_text(items: list[dict]) -> str:
    if not items:
        return "У вас пока нет истории посещений."

    lines = ["🕘 История посещений\n"]
    for index, item in enumerate(items, start=1):
        lines.append(
            f"{index}. {item.get('appointment_date', '—')}, "
            f"{item.get('start_time', '—')}–{item.get('end_time', '—')}\n"
            f"Услуга: {item.get('service_name', '—')}\n"
            f"Специалист: {item.get('employee_name', '—')}"
        )

    return "\n\n".join(lines)


def format_contact_text() -> str:
    return (
        "📍 Контакты\n\n"
        f"Адрес: {CONTACT_ADDRESS}\n\n"
        f"Телефон: {CONTACT_PHONE}\n\n"
        f"Как добраться:\n{CONTACT_MAP_LINK}"
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

    if status == "confirmed":
        title = "✅ Ваша запись подтверждена"
    elif status == "canceled":
        title = "❌ Ваша запись отменена"
    elif status == "completed":
        title = "✅ Ваша запись завершена"
    else:
        title = "ℹ️ Статус вашей записи изменён"

    text = (
        f"{title}\n\n"
        f"Услуга: {appointment.get('service_name', '—')}\n"
        f"Специалист: {appointment.get('employee_name', '—')}\n"
        f"Дата: {appointment.get('appointment_date', '—')}\n"
        f"Время: {appointment.get('start_time', '—')}–{appointment.get('end_time', '—')}"
    )

    if status == "completed":
        text += f"\nОплата: {format_money(appointment.get('actual_amount'))}"

    return text


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


async def send_main_menu(target, user_id: int | None = None) -> None:
    await target.reply_text(
        "Выберите действие:",
        reply_markup=get_main_keyboard(user_id),
    )


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
                        await application.bot.send_message(
                            chat_id=chat_id,
                            text="Меню доступно ниже:",
                            reply_markup=get_main_keyboard(),
                        )

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

    telegram_chat_id = update.effective_chat.id
    telegram_user_id = update.effective_user.id
    payload = context.args[0].strip() if context.args else ""

    if payload:
        token = normalize_token(payload)
        booking, error_message = wp_resolve_booking(
            token=token,
            telegram_chat_id=telegram_chat_id,
            telegram_user_id=telegram_user_id,
        )

        if booking:
            await update.message.reply_text(format_booking_text(booking))
            await send_booking_extras(update.message, booking)
            await send_main_menu(update.message, telegram_user_id)
            return

        await update.message.reply_text(
            "Не удалось привязать ваш Telegram. Пожалуйста, вернитесь на сайт и попробуйте снова.",
            reply_markup=get_main_keyboard(telegram_user_id),
        )
        logger.warning("Booking resolve failed: %s", error_message)
        return

    booking, _ = wp_get_client_current_booking(telegram_user_id)

    if booking:
        await update.message.reply_text(format_current_booking_text(booking))
        await send_main_menu(update.message, telegram_user_id)
        return

    await update.message.reply_text(
        "Здравствуйте. Перейдите в бот по персональной ссылке с сайта, чтобы подключить уведомления.",
        reply_markup=get_main_keyboard(telegram_user_id),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    await update.message.reply_text(
        "Используйте кнопки ниже, чтобы посмотреть записи или контакты.",
        reply_markup=get_main_keyboard(update.effective_user.id),
    )


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    if not context.args:
        await update.message.reply_text(
            "Использование: /test TOKEN",
            reply_markup=get_main_keyboard(update.effective_user.id),
        )
        return

    token = normalize_token(context.args[0])
    booking, error_message = wp_resolve_booking(
        token=token,
        telegram_chat_id=update.effective_chat.id,
        telegram_user_id=update.effective_user.id,
    )

    if booking:
        await update.message.reply_text(format_booking_text(booking))
        await send_booking_extras(update.message, booking)
        await send_main_menu(update.message, update.effective_user.id)
    else:
        await update.message.reply_text(
            f"Тест неуспешен.\nПричина: {error_message or 'endpoint ещё не настроен'}",
            reply_markup=get_main_keyboard(update.effective_user.id),
        )


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Нет доступа")
        return

    items, error = wp_get_today_appointments()

    if error:
        await update.message.reply_text(f"❌ Ошибка получения записей: {error}")
        return

    if not items:
        await update.message.reply_text("📭 На сегодня записей нет.")
        return

    status_labels = {
        "new": "🆕 Новая",
        "confirmed": "✅ Подтверждена",
        "completed": "✔️ Завершена",
        "canceled": "❌ Отменена",
        "no_show": "🚫 Не пришёл",
    }

    for index, item in enumerate(items, start=1):
        status = (item.get("status") or "").strip()
        status_label = status_labels.get(status, status or "—")

        start_time = str(item.get("start_time", "—"))[:5]
        end_time = str(item.get("end_time", "—"))[:5]

        text = (
            f"🗓 <b>Запись #{index}</b>\n\n"
            f"👤 <b>Клиент:</b> {item.get('client_name', '—')}\n"
            f"📞 <b>Телефон:</b> {item.get('client_phone', '—')}\n\n"
            f"💼 <b>Услуга:</b> {item.get('service_name', '—')}\n"
            f"👨‍⚕️ <b>Специалист:</b> {item.get('employee_name', '—')}\n\n"
            f"⏰ <b>Время:</b> {start_time}–{end_time}\n"
            f"📌 <b>Статус:</b> {status_label}"
        )

        if status == "completed":
            text += f"\n💰 <b>Сумма:</b> {format_money(item.get('actual_amount'))}"

        buttons = []

        if status != "completed":
            buttons.append([
                InlineKeyboardButton(
                    "✅ Завершить",
                    callback_data=f"complete:{int(item['id'])}"
                )
            ])

        if status != "confirmed" and status != "completed":
            buttons.append([
                InlineKeyboardButton(
                    "Подтвердить",
                    callback_data=f"confirm:{int(item['id'])}"
                ),
                InlineKeyboardButton(
                    "Отменить",
                    callback_data=f"cancel:{int(item['id'])}"
                )
            ])

        reply_markup = InlineKeyboardMarkup(buttons) if buttons else None

        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )

    total = len(items)
    confirmed = sum(1 for i in items if i.get("status") == "confirmed")
    completed = sum(1 for i in items if i.get("status") == "completed")
    new = sum(1 for i in items if i.get("status") == "new")
    canceled = sum(1 for i in items if i.get("status") == "canceled")
    no_show = sum(1 for i in items if i.get("status") == "no_show")

    summary_text = (
        "📊 <b>Сводка за сегодня</b>\n\n"
        f"📅 Всего записей: <b>{total}</b>\n\n"
        f"🆕 Новые: <b>{new}</b>\n"
        f"✅ Подтверждены: <b>{confirmed}</b>\n"
        f"✔️ Завершены: <b>{completed}</b>\n"
        f"❌ Отменены: <b>{canceled}</b>\n"
        f"🚫 Не пришёл: <b>{no_show}</b>"
    )

    await update.message.reply_text(
        summary_text,
        parse_mode="HTML",
    )


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    text = (update.message.text or "").strip()
    telegram_user_id = update.effective_user.id

    pending_complete = context.user_data.get(PENDING_COMPLETE_KEY)

    if pending_complete and is_admin(telegram_user_id):
        amount = normalize_amount(text)

        if amount is None:
            await update.message.reply_text(
                "Введите корректную сумму числом.\nНапример: 2500 или 2500.50"
            )
            return

        appointment_id = int(pending_complete["appointment_id"])
        client_name = pending_complete.get("client_name", "—")
        service_name = pending_complete.get("service_name", "—")
        appointment_date = pending_complete.get("appointment_date", "—")
        time_range = pending_complete.get("time_range", "—")

        appointment, error = wp_admin_action(
            appointment_id=appointment_id,
            action="complete",
            actual_amount=amount,
        )

        if not appointment:
            await update.message.reply_text(
                f"Не удалось завершить запись.\nПричина: {error or 'неизвестная ошибка'}",
                reply_markup=get_main_keyboard(telegram_user_id),
            )
            context.user_data.pop(PENDING_COMPLETE_KEY, None)
            return

        context.user_data.pop(PENDING_COMPLETE_KEY, None)

        await update.message.reply_text(
            "✅ Приём завершён\n\n"
            f"Клиент: {client_name}\n"
            f"Услуга: {service_name}\n"
            f"Дата: {appointment_date}\n"
            f"Время: {time_range}\n"
            f"Сумма: {format_money(amount)}",
            reply_markup=get_main_keyboard(telegram_user_id),
        )

        client_chat_id = (appointment.get("client_telegram_chat_id") or "").strip()
        if client_chat_id:
            try:
                await context.bot.send_message(
                    chat_id=int(client_chat_id),
                    text=format_client_status_text(appointment),
                )
                await context.bot.send_message(
                    chat_id=int(client_chat_id),
                    text="Выберите действие:",
                    reply_markup=get_main_keyboard(int(client_chat_id)),
                )
            except Exception as e:
                logger.exception("Failed to notify client after complete action: %s", e)

        return

    if text == "Мои записи":
        items, error = wp_get_client_bookings(telegram_user_id)

        if error and not items:
            await update.message.reply_text(
                "Не удалось получить историю посещений. Убедитесь, что Telegram уже привязан через персональную ссылку.",
                reply_markup=get_main_keyboard(telegram_user_id),
            )
            return

        await update.message.reply_text(
            format_bookings_list_text(items),
            reply_markup=get_main_keyboard(telegram_user_id),
        )
        return

    if text == "Ближайшая запись":
        booking, error = wp_get_client_current_booking(telegram_user_id)

        if error and not booking:
            await update.message.reply_text(
                "Не удалось получить ближайшую запись. Убедитесь, что Telegram уже привязан через персональную ссылку.",
                reply_markup=get_main_keyboard(telegram_user_id),
            )
            return

        await update.message.reply_text(
            format_current_booking_text(booking),
            reply_markup=get_main_keyboard(telegram_user_id),
        )

        if booking:
            await send_booking_extras(update.message, booking)
            await send_main_menu(update.message, telegram_user_id)
        return

    if text == "Связаться":
        await update.message.reply_text(
            format_contact_text(),
            reply_markup=get_main_keyboard(telegram_user_id),
        )
        return

    if text == "📅 Записи на сегодня":
        if not is_admin(telegram_user_id):
            await update.message.reply_text("Нет доступа")
            return

        await today_command(update, context)
        return

    await update.message.reply_text(
        "Используйте кнопки ниже.",
        reply_markup=get_main_keyboard(telegram_user_id),
    )


async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return

    await query.answer()

    if not is_admin(update.effective_user.id):
        await query.edit_message_text("Нет доступа")
        return

    data = query.data or ''
    if ':' not in data:
        return

    action, appointment_id_raw = data.split(':', 1)

    if action not in ['confirm', 'cancel', 'complete']:
        return

    try:
        appointment_id = int(appointment_id_raw)
    except ValueError:
        await query.edit_message_text("Некорректный ID записи.")
        return

    current_text = query.message.text_html if query.message and query.message.text_html else "Статус обновлен"

    if action == "complete":
        client_name = "—"
        service_name = "—"
        appointment_date = "—"
        time_range = "—"

        if query.message and query.message.text:
            plain_text = query.message.text
            lines = plain_text.splitlines()
            for line in lines:
                if line.startswith("Клиент:"):
                    client_name = line.replace("Клиент:", "", 1).strip()
                elif line.startswith("Услуга:"):
                    service_name = line.replace("Услуга:", "", 1).strip()
                elif line.startswith("Дата:"):
                    appointment_date = line.replace("Дата:", "", 1).strip()
                elif line.startswith("Время:"):
                    time_range = line.replace("Время:", "", 1).strip()

        context.user_data[PENDING_COMPLETE_KEY] = {
            "appointment_id": appointment_id,
            "client_name": client_name,
            "service_name": service_name,
            "appointment_date": appointment_date,
            "time_range": time_range,
        }

        await query.message.reply_text(
            "Введите сумму приёма.\n\n"
            f"Клиент: {client_name}\n"
            f"Услуга: {service_name}\n"
            f"Дата: {appointment_date}\n"
            f"Время: {time_range}\n\n"
            "Пример: 2500"
        )
        return

    appointment, error = wp_admin_action(appointment_id, action)

    if not appointment:
        await query.edit_message_text(f"Ошибка: {error or 'не удалось выполнить действие'}")
        return

    if action == 'confirm':
        status_label = 'подтверждена'
    else:
        status_label = 'отменена'

    await query.edit_message_text(
        current_text + f"\n\n<b>Статус:</b> {status_label}",
        parse_mode="HTML"
    )

    client_chat_id = (appointment.get("client_telegram_chat_id") or '').strip()
    if client_chat_id:
        try:
            await context.bot.send_message(
                chat_id=int(client_chat_id),
                text=format_client_status_text(appointment),
            )
            await context.bot.send_message(
                chat_id=int(client_chat_id),
                text="Выберите действие:",
                reply_markup=get_main_keyboard(int(client_chat_id)),
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
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CallbackQueryHandler(handle_admin_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    logger.info("Booking bot started")
    app.run_polling()


if __name__ == "__main__":
    run()