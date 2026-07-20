from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from config.settings import get_settings
from database.connection import AsyncSessionFactory
from database.models import Booking, BookingStatus, StudioSetting, User
from handlers.common import build_main_menu, register_user
from services.google_calendar import GoogleCalendarService
from sqlalchemy import select

logger = logging.getLogger(__name__)

SERVICE_OPTIONS = {
    "piercing": "Прокол",
    "apsize": "Апсайз",
    "downsize": "Даунсайз",
    "cleaning": "Чистка украшения",
    "consultation": "Консультация",
    "jewelry": "Покупка украшения",
    "anodizing": "Анодирование титана",
}


def build_service_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(SERVICE_OPTIONS["piercing"], callback_data="service:piercing")],
        [InlineKeyboardButton(SERVICE_OPTIONS["apsize"], callback_data="service:apsize")],
        [InlineKeyboardButton(SERVICE_OPTIONS["downsize"], callback_data="service:downsize")],
        [InlineKeyboardButton(SERVICE_OPTIONS["cleaning"], callback_data="service:cleaning")],
        [InlineKeyboardButton(SERVICE_OPTIONS["consultation"], callback_data="service:consultation")],
        [InlineKeyboardButton(SERVICE_OPTIONS["jewelry"], callback_data="service:jewelry")],
        [InlineKeyboardButton(SERVICE_OPTIONS["anodizing"], callback_data="service:anodizing")],
        [InlineKeyboardButton("Назад", callback_data="back:main")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_booking_next_state(service_name: str, age: int) -> str:
    if service_name == SERVICE_OPTIONS["piercing"]:
        return "medical_question_1"
    return "await_date"


async def get_free_slots_for_date(calendar_service: GoogleCalendarService, target_date: datetime) -> list[datetime]:
    """
    Вычисляет свободные сеансы, сопоставляя фиксированную сетку с занятыми слотами из Google Calendar.
    """
    local_tz = timezone(timedelta(hours=5))
    busy_intervals = await calendar_service.get_busy_intervals(target_date)

    # Фиксированная сетка рабочих сеансов
    working_hours = ["10:00", "12:00", "14:00", "16:00", "18:00", "20:00"]
    slot_duration = timedelta(hours=1, minutes=30)

    free_slots = []
    now = datetime.now(timezone.utc)

    for hw in working_hours:
        hour, minute = map(int, hw.split(":"))
        slot_start = datetime(target_date.year, target_date.month, target_date.day, hour, minute, tzinfo=local_tz)

        if slot_start < now:
            continue

        slot_end = slot_start + slot_duration
        overlaps = False
        for busy_start, busy_end in busy_intervals:
            if slot_start < busy_end and slot_end > busy_start:
                overlaps = True
                break

        if not overlaps:
            free_slots.append(slot_start)

    return free_slots


def build_slots_keyboard(date_str: str, slots: list[datetime]) -> InlineKeyboardMarkup:
    keyboard = []
    for slot in slots:
        btn_text = slot.strftime("%H:%M")
        callback_data = f"book_slot:{slot.isoformat()}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=callback_data)])

    keyboard.append([InlineKeyboardButton("✍️ Ввести другую дату", callback_data="change_date")])
    keyboard.append([InlineKeyboardButton("🏠 Назад в меню", callback_data="back:main")])
    return InlineKeyboardMarkup(keyboard)


def build_neighboring_dates_keyboard(current_date: datetime) -> InlineKeyboardMarkup:
    prev_date = current_date - timedelta(days=1)
    next_date = current_date + timedelta(days=1)

    prev_str = prev_date.strftime("%d.%m.%Y")
    next_str = next_date.strftime("%d.%m.%Y")

    keyboard = [
        [
            InlineKeyboardButton(f"⬅️ {prev_date.strftime('%d.%m')}", callback_data=f"check_date:{prev_str}"),
            InlineKeyboardButton(f"➡️ {next_date.strftime('%d.%m')}", callback_data=f"check_date:{next_str}")
        ],
        [InlineKeyboardButton("✍️ Ввести другую дату", callback_data="change_date")],
        [InlineKeyboardButton("🏠 Назад в меню", callback_data="back:main")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def process_date_availability(update: Update, context: ContextTypes.DEFAULT_TYPE, target_date: datetime, edit_message: bool = False) -> None:
    settings = get_settings()
    calendar_service = GoogleCalendarService(settings)

    checking_msg = None
    if edit_message and update.callback_query:
        await update.callback_query.answer("Проверяем расписание...")
    else:
        checking_msg = await update.effective_message.reply_text("Минутку, сверяемся с календарем студии...")

    free_slots = await get_free_slots_for_date(calendar_service, target_date)

    if checking_msg:
        try:
            await checking_msg.delete()
        except Exception:
            pass

    date_str = target_date.strftime("%d.%m.%Y")
    context.user_data["last_checked_date"] = date_str

    if free_slots:
        text = f"Свободные слоты на {date_str}:"
        reply_markup = build_slots_keyboard(date_str, free_slots)
    else:
        text = f"К сожалению, на {date_str} свободных мест нет. Пожалуйста, выберите соседнюю дату или введите другую:"
        reply_markup = build_neighboring_dates_keyboard(target_date)

    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.effective_message.reply_text(text, reply_markup=reply_markup)


async def handle_main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update, context)
    query = update.callback_query
    if query is None:
        return

    data = query.data or ""
    if data == "book":
        await query.edit_message_text(
            "Выберите услугу:",
            reply_markup=build_service_menu(),
        )
    elif data == "support":
        await query.edit_message_text(
            "Мы откроем тикет поддержки. Пожалуйста, напишите ваш вопрос.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="back:main")]]),
        )
    elif data == "healing":
        await query.edit_message_text(
            "Инструкция по заживлению доступна после подтверждения доступа администратора.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="back:main")]]),
        )
    elif data.startswith("back:"):
        await query.edit_message_text(
            "Главное меню",
            reply_markup=build_main_menu(),
        )


async def handle_service_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    data = query.data or ""
    if not data.startswith("service:"):
        return

    service_key = data.split(":", 1)[1]
    service_name = SERVICE_OPTIONS.get(service_key, "Неизвестная услуга")
    context.user_data["selected_service"] = service_name
    context.user_data["history"] = context.user_data.get("history", [])
    context.user_data["history"].append("service_selection")

    await query.edit_message_text(
        f"Вы выбрали: {service_name}.\nВведите ваше ФИО.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="back:service")]]),
    )
    context.user_data["booking_state"] = "await_name"


async def handle_booking_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_message.text is None:
        return

    state = context.user_data.get("booking_state")
    text = update.effective_message.text.strip()

    if state == "await_name":
        context.user_data["client_name"] = text
        context.user_data["booking_state"] = "await_phone"
        await update.effective_message.reply_text("Введите контактный номер или ссылку на Telegram.")
        return

    if state == "await_phone":
        context.user_data["client_phone"] = text
        context.user_data["booking_state"] = "await_age"
        await update.effective_message.reply_text("Введите возраст целым числом.")
        return

    if state == "await_age":
        try:
            age = int(text)
        except ValueError:
            await update.effective_message.reply_text("Пожалуйста, введите возраст цифрами.")
            return

        context.user_data["client_age"] = age
        next_state = get_booking_next_state(context.user_data.get("selected_service", ""), age)
        context.user_data["booking_state"] = next_state

        if next_state == "medical_question_1":
            await update.effective_message.reply_text(
                "Есть ли у тебя какие-нибудь заболевания крови?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Да", callback_data="medical:yes")],
                    [InlineKeyboardButton("Нет", callback_data="medical:no")],
                    [InlineKeyboardButton("Пропустить", callback_data="medical:skip")],
                ]),
            )
            return

        context.user_data["booking_state"] = "await_date"
        await update.effective_message.reply_text(
            "Введите желаемую дату для записи в формате ДД.ММ.ГГГГ (например, 25.07.2026):"
        )
        return

    if state == "await_date":
        try:
            input_date = datetime.strptime(text, "%d.%m.%Y")
            today = datetime.now(timezone(timedelta(hours=5))).replace(hour=0, minute=0, second=0, microsecond=0)
            input_date_local = input_date.replace(tzinfo=timezone(timedelta(hours=5)))
            if input_date_local < today:
                await update.effective_message.reply_text("Дата не может быть в прошлом. Пожалуйста, введите корректную будущую дату в формате ДД.ММ.ГГГГ:")
                return
        except ValueError:
            await update.effective_message.reply_text("Неверный формат даты. Пожалуйста, введите дату в формате ДД.ММ.ГГГГ (например, 25.07.2026):")
            return

        context.user_data["requested_date"] = text
        context.user_data["booking_state"] = "select_slot"
        await process_date_availability(update, context, input_date_local)
        return

    if state.startswith("medical_question"):
        context.user_data["medical_answers"] = context.user_data.get("medical_answers", {})
        context.user_data["medical_answers"][state] = text
        if state == "medical_question_1":
            context.user_data["booking_state"] = "medical_question_2"
            await update.effective_message.reply_text(
                "Свертываемость крови хорошая или плохая?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Хорошая", callback_data="medical:good")],
                    [InlineKeyboardButton("Плохая", callback_data="medical:bad")],
                    [InlineKeyboardButton("Пропустить", callback_data="medical:skip")],
                ]),
            )
        elif state == "medical_question_2":
            context.user_data["booking_state"] = "medical_question_3"
            await update.effective_message.reply_text(
                "Принимаешь ли ты в настоящее время какие-либо лекарства?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Да", callback_data="medical:yes")],
                    [InlineKeyboardButton("Нет", callback_data="medical:no")],
                    [InlineKeyboardButton("Пропустить", callback_data="medical:skip")],
                ]),
            )
        elif state == "medical_question_3":
            context.user_data["booking_state"] = "medical_question_4"
            await update.effective_message.reply_text(
                "Есть ли хронические заболевания?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Да", callback_data="medical:yes")],
                    [InlineKeyboardButton("Нет", callback_data="medical:no")],
                    [InlineKeyboardButton("Пропустить", callback_data="medical:skip")],
                ]),
            )
        elif state == "medical_question_4":
            context.user_data["booking_state"] = "medical_question_5"
            await update.effective_message.reply_text(
                "Были ли в прошлом проблемы с заживлением пирсинга или ран?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Да", callback_data="medical:yes")],
                    [InlineKeyboardButton("Нет", callback_data="medical:no")],
                    [InlineKeyboardButton("Пропустить", callback_data="medical:skip")],
                ]),
            )
        elif state == "medical_question_5":
            context.user_data["booking_state"] = "medical_question_6"
            await update.effective_message.reply_text(
                "Есть ли кожные заболевания?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Да", callback_data="medical:yes")],
                    [InlineKeyboardButton("Нет", callback_data="medical:no")],
                    [InlineKeyboardButton("Пропустить", callback_data="medical:skip")],
                ]),
            )
        elif state == "medical_question_6":
            context.user_data["booking_state"] = "await_date"
            await update.effective_message.reply_text(
                "Введите желаемую дату для записи в формате ДД.ММ.ГГГГ (например, 25.07.2026):"
            )


async def handle_booking_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    data = query.data or ""
    if data == "back:service":
        await query.edit_message_text("Главное меню", reply_markup=build_main_menu())
        return

    if data == "change_date":
        context.user_data["booking_state"] = "await_date"
        await query.edit_message_text("Введите желаемую дату для записи в формате ДД.ММ.ГГГГ (например, 25.07.2026):")
        return

    if data.startswith("check_date:"):
        date_str = data.split(":", 1)[1]
        try:
            target_date = datetime.strptime(date_str, "%d.%m.%Y").replace(tzinfo=timezone(timedelta(hours=5)))
        except ValueError:
            await query.answer("Неверный формат даты в системе.", show_alert=True)
            return

        await process_date_availability(update, context, target_date, edit_message=True)
        return

    if data.startswith("medical:"):
        answer = data.split(":", 1)[1]
        context.user_data["medical_answers"] = context.user_data.get("medical_answers", {})
        context.user_data["medical_answers"][context.user_data.get("booking_state", "medical_question_1")] = answer
        if context.user_data.get("booking_state") == "medical_question_1":
            context.user_data["booking_state"] = "medical_question_2"
            await query.edit_message_text(
                "Свертываемость крови хорошая или плохая?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Хорошая", callback_data="medical:good")],
                    [InlineKeyboardButton("Плохая", callback_data="medical:bad")],
                    [InlineKeyboardButton("Пропустить", callback_data="medical:skip")],
                ]),
            )
        elif context.user_data.get("booking_state") == "medical_question_2":
            context.user_data["booking_state"] = "medical_question_3"
            await query.edit_message_text(
                "Принимаешь ли ты в настоящее время какие-либо лекарства?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Да", callback_data="medical:yes")],
                    [InlineKeyboardButton("Нет", callback_data="medical:no")],
                    [InlineKeyboardButton("Пропустить", callback_data="medical:skip")],
                ]),
            )
        elif context.user_data.get("booking_state") == "medical_question_3":
            context.user_data["booking_state"] = "medical_question_4"
            await query.edit_message_text(
                "Есть ли хронические заболевания?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Да", callback_data="medical:yes")],
                    [InlineKeyboardButton("Нет", callback_data="medical:no")],
                    [InlineKeyboardButton("Пропустить", callback_data="medical:skip")],
                ]),
            )
        elif context.user_data.get("booking_state") == "medical_question_4":
            context.user_data["booking_state"] = "medical_question_5"
            await query.edit_message_text(
                "Были ли в прошлом проблемы с заживлением пирсинга или ран?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Да", callback_data="medical:yes")],
                    [InlineKeyboardButton("Нет", callback_data="medical:no")],
                    [InlineKeyboardButton("Пропустить", callback_data="medical:skip")],
                ]),
            )
        elif context.user_data.get("booking_state") == "medical_question_5":
            context.user_data["booking_state"] = "medical_question_6"
            await query.edit_message_text(
                "Есть ли кожные заболевания?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Да", callback_data="medical:yes")],
                    [InlineKeyboardButton("Нет", callback_data="medical:no")],
                    [InlineKeyboardButton("Пропустить", callback_data="medical:skip")],
                ]),
            )
        elif context.user_data.get("booking_state") == "medical_question_6":
            context.user_data["booking_state"] = "await_date"
            await query.edit_message_text(
                "Введите желаемую дату для записи в формате ДД.ММ.ГГГГ (например, 25.07.2026):"
            )
        return

    if data.startswith("book_slot:"):
        slot_value = data.split(":", 1)[1]
        try:
            slot_dt = datetime.fromisoformat(slot_value)
        except ValueError:
            await query.edit_message_text("Не удалось распознать выбранный слот. Попробуйте ещё раз.")
            return

        slot_display = slot_dt.strftime("%d.%m.%Y в %H:%M")

        settings = get_settings()
        calendar_service = GoogleCalendarService(settings)

        if update.effective_user is None:
            return

        async with AsyncSessionFactory() as session:
            user = await session.scalar(select(User).where(User.telegram_id == update.effective_user.id))
            if user is None:
                await query.edit_message_text("Сначала требуется регистрация. Попробуйте /start.")
                return

            booking = Booking(
                user_id=user.telegram_id,
                client_name=context.user_data.get("client_name", "Unknown"),
                client_phone=context.user_data.get("client_phone", ""),
                client_age=context.user_data.get("client_age", 0),
                service_name=context.user_data.get("selected_service", "Unknown"),
                blood_disease=context.user_data.get("medical_answers", {}).get("medical_question_1"),
                blood_clotting=context.user_data.get("medical_answers", {}).get("medical_question_2"),
                current_medication=context.user_data.get("medical_answers", {}).get("medical_question_3"),
                chronic_disease=context.user_data.get("medical_answers", {}).get("medical_question_4"),
                healing_issues=context.user_data.get("medical_answers", {}).get("medical_question_5"),
                skin_disease=context.user_data.get("medical_answers", {}).get("medical_question_6"),
                date_time=slot_dt,
                status=BookingStatus.CONFIRMED,
            )
            session.add(booking)
            await session.commit()
            await session.refresh(booking)

            end_dt = slot_dt + timedelta(hours=1, minutes=30)
            calendar_event_id = await calendar_service.create_booking_event(
                booking_summary=f"Запись: {booking.client_name} ({booking.service_name})",
                description=(
                    f"Клиент: {booking.client_name}\n"
                    f"Контакт: {booking.client_phone}\n"
                    f"Возраст: {booking.client_age}\n"
                    f"Услуга: {booking.service_name}"
                ),
                start_dt=slot_dt,
                end_dt=end_dt
            )

            if calendar_event_id:
                booking.google_event_id = calendar_event_id
                await session.commit()

        async with AsyncSessionFactory() as session:
            location_settings = await session.scalars(select(StudioSetting))
            settings_map = {item.key: item.value for item in location_settings}

        address_text = settings_map.get("address_text", "Адрес студии будет уточнен")
        latitude = settings_map.get("latitude")
        longitude = settings_map.get("longitude")

        booking_message = (
            f"Бронирование подтверждено на {slot_display}.\n"
            f"Адрес студии: {address_text}"
        )
        if latitude and longitude:
            booking_message += f"\nКоординаты: {latitude}, {longitude}"

        if calendar_event_id:
            await query.edit_message_text(
                booking_message + "\nСобытие добавлено в Google Calendar.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Главное меню", callback_data="back:main")]]),
            )
        else:
            await query.edit_message_text(
                booking_message + "\nGoogle Calendar временно недоступен, но запись уже зарегистрирована.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Главное меню", callback_data="back:main")]]),
            )

        if update.effective_chat is not None:
            await update.effective_chat.send_message(booking_message)

        # Очищаем временное состояние мастера записи
        context.user_data.pop("booking_state", None)
        context.user_data.pop("client_name", None)
        context.user_data.pop("client_phone", None)
        context.user_data.pop("client_age", None)
        context.user_data.pop("medical_answers", None)
        context.user_data.pop("selected_service", None)
        context.user_data.pop("requested_date", None)
        context.user_data.pop("last_checked_date", None)
        return


client_handlers = [
    CallbackQueryHandler(handle_main_menu_callback, pattern=r"^(book|support|healing|back:main)$"),
    CallbackQueryHandler(handle_service_selection, pattern=r"^service:"),
    CallbackQueryHandler(handle_booking_callback, pattern=r"^(back:service|slot:|medical:|change_date|check_date:|book_slot:)"),
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_booking_input),
]
