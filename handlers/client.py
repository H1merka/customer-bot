from __future__ import annotations

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


def build_slot_menu() -> InlineKeyboardMarkup:
    slots = [
        (datetime.now(timezone.utc) + timedelta(days=1, hours=10)).strftime("%d.%m %H:%M"),
        (datetime.now(timezone.utc) + timedelta(days=1, hours=12)).strftime("%d.%m %H:%M"),
        (datetime.now(timezone.utc) + timedelta(days=1, hours=15)).strftime("%d.%m %H:%M"),
    ]
    keyboard = [[InlineKeyboardButton(slot, callback_data=f"slot:{slot}")] for slot in slots]
    keyboard.append([InlineKeyboardButton("Назад", callback_data="back:service")])
    return InlineKeyboardMarkup(keyboard)


def get_booking_next_state(service_name: str, age: int) -> str:
    if service_name == SERVICE_OPTIONS["piercing"]:
        return "medical_question_1"
    return "select_slot"


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

        context.user_data["booking_state"] = "select_slot"
        await update.effective_message.reply_text(
            "Выберите свободный слот:",
            reply_markup=build_slot_menu(),
        )
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
            context.user_data["booking_state"] = "select_slot"
            await update.effective_message.reply_text(
                "Выберите свободный слот:",
                reply_markup=build_slot_menu(),
            )


async def handle_booking_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    data = query.data or ""
    if data == "back:service":
        await query.edit_message_text("Главное меню", reply_markup=build_main_menu())
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
            context.user_data["booking_state"] = "select_slot"
            await query.edit_message_text(
                "Выберите свободный слот:",
                reply_markup=build_slot_menu(),
            )
        return

    if data.startswith("slot:"):
        slot_value = data.split(":", 1)[1]
        context.user_data["selected_slot"] = slot_value

        settings = get_settings()
        calendar_service = GoogleCalendarService(settings)

        try:
            parsed_slot = datetime.strptime(slot_value, "%d.%m %H:%M")
            slot_dt = datetime(parsed_slot.year, parsed_slot.month, parsed_slot.day, parsed_slot.hour, parsed_slot.minute, tzinfo=timezone.utc)
        except ValueError:
            await query.edit_message_text("Не удалось распознать выбранный слот. Попробуйте ещё раз.")
            return

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

            calendar_event_created = await calendar_service.update_booking_event(
                event_id="placeholder-event",
                booking_summary=f"Запись: {booking.client_name} ({booking.service_name})",
                description=(
                    f"Клиент: {booking.client_name}\n"
                    f"Контакт: {booking.client_phone}\n"
                    f"Возраст: {booking.client_age}\n"
                    f"Услуга: {booking.service_name}"
                ),
            )

        async with AsyncSessionFactory() as session:
            location_settings = await session.scalars(select(StudioSetting))
            settings_map = {item.key: item.value for item in location_settings}

        address_text = settings_map.get("address_text", "Адрес студии будет уточнен")
        latitude = settings_map.get("latitude")
        longitude = settings_map.get("longitude")

        booking_message = (
            f"Бронирование подтверждено на {slot_value}.\n"
            f"Адрес студии: {address_text}"
        )
        if latitude and longitude:
            booking_message += f"\nКоординаты: {latitude}, {longitude}"

        if calendar_event_created:
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
        return


client_handlers = [
    CallbackQueryHandler(handle_main_menu_callback, pattern=r"^(book|support|healing|back:main)$"),
    CallbackQueryHandler(handle_service_selection, pattern=r"^service:"),
    CallbackQueryHandler(handle_booking_callback, pattern=r"^(back:service|slot:|medical:)"),
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_booking_input),
]
