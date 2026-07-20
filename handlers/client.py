# handlers/client.py
from __future__ import annotations

import html  # Добавлен импорт для безопасного форматирования HTML-разметки
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from config.settings import get_settings
from database.connection import AsyncSessionFactory
from database.models import Booking, BookingStatus, StudioSetting, User, UserRole  # Добавлен импорт UserRole
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

MEDICAL_QUESTIONS = {
    1: {
        "text": "Есть ли у тебя какие-нибудь заболевания крови?",
        "options": [("Да", "medical:yes"), ("Нет", "medical:no")],
        "db_field": "blood_disease",
        "state": "medical_question_1",
        "detail_state": "await_medical_text_1",
        "detail_prompt": "Пожалуйста, напишите подробнее о заболеваниях крови:"
    },
    2: {
        "text": "Свертываемость крови хорошая или плохая?",
        "options": [("Плохая", "medical:bad"), ("Хорошая", "medical:good")],
        "db_field": "blood_clotting",
        "state": "medical_question_2",
        "detail_state": "await_medical_text_2",
        "detail_prompt": "Пожалуйста, напишите подробнее о проблемах со свертываемостью крови:"
    },
    3: {
        "text": "Принимаешь ли ты в настоящее время какие-либо лекарства?",
        "options": [("Да", "medical:yes"), ("Нет", "medical:no")],
        "db_field": "current_medication",
        "state": "medical_question_3",
        "detail_state": "await_medical_text_3",
        "detail_prompt": "Пожалуйста, укажите принимаемые лекарства:"
    },
    4: {
        "text": "Есть ли хронические заболевания?",
        "options": [("Да", "medical:yes"), ("Нет", "medical:no")],
        "db_field": "chronic_disease",
        "state": "medical_question_4",
        "detail_state": "await_medical_text_4",
        "detail_prompt": "Пожалуйста, напишите подробнее о хронических заболеваниях:"
    },
    5: {
        "text": "Были ли в прошлом проблемы с заживлением пирсинга или ран?",
        "options": [("Да", "medical:yes"), ("Нет", "medical:no")],
        "db_field": "healing_issues",
        "state": "medical_question_5",
        "detail_state": "await_medical_text_5",
        "detail_prompt": "Пожалуйста, напишите подробнее о проблемах с заживлением:"
    },
    6: {
        "text": "Есть ли кожные заболевания?",
        "options": [("Да", "medical:yes"), ("Нет", "medical:no")],
        "db_field": "skin_disease",
        "state": "medical_question_6",
        "detail_state": "await_medical_text_6",
        "detail_prompt": "Пожалуйста, напишите подробнее о кожных заболеваниях:"
    }
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


def build_back_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="booking_back")]])


def build_medical_keyboard(question_id: int) -> InlineKeyboardMarkup:
    q_info = MEDICAL_QUESTIONS[question_id]
    keyboard = []
    row = []
    for label, cb_data in q_info["options"]:
        row.append(InlineKeyboardButton(label, callback_data=cb_data))
    keyboard.append(row)
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="booking_back")])
    return InlineKeyboardMarkup(keyboard)


async def transition_to_state(update: Update, context: ContextTypes.DEFAULT_TYPE, state_name: str, edit_message: bool = False) -> None:
    context.user_data["booking_state"] = state_name
    text = ""
    reply_markup = None

    if state_name == "service_selection":
        text = "Выберите услугу:"
        reply_markup = build_service_menu()
    elif state_name == "await_name":
        text = f"Вы выбрали: {context.user_data.get('selected_service')}.\n\nВведите ваше ФИО."
        reply_markup = build_back_button()
    elif state_name == "await_phone":
        text = "Введите контактный номер или ссылку на Telegram."
        reply_markup = build_back_button()
    elif state_name == "await_age":
        text = "Введите возраст целым числом."
        reply_markup = build_back_button()
    elif state_name == "await_parent_name":
        text = "Вы не достигли совершеннолетия. Пожалуйста, введите ФИО вашего родителя или законного представителя."
        reply_markup = build_back_button()
    elif state_name == "await_parent_phone":
        text = "Введите контактный телефон родителя или законного представителя."
        reply_markup = build_back_button()
    elif state_name.startswith("medical_question_"):
        q_num = int(state_name.split("_")[-1])
        q_info = MEDICAL_QUESTIONS[q_num]
        text = q_info["text"]
        reply_markup = build_medical_keyboard(q_num)
    elif state_name.startswith("await_medical_text_"):
        q_num = int(state_name.split("_")[-1])
        q_info = MEDICAL_QUESTIONS[q_num]
        text = q_info["detail_prompt"]
        reply_markup = build_back_button()
    elif state_name == "await_date":
        text = "Введите желаемую дату для записи в формате ДД.ММ.ГГГГ (например, 25.07.2026):"
        reply_markup = build_back_button()
    elif state_name == "select_slot":
        req_date = context.user_data.get("requested_date")
        if req_date:
            try:
                target_date = datetime.strptime(req_date, "%d.%m.%Y").replace(tzinfo=timezone(timedelta(hours=5)))
                await process_date_availability(update, context, target_date, edit_message=edit_message)
            except Exception:
                context.user_data["booking_state"] = "await_date"
                await transition_to_state(update, context, "await_date", edit_message=edit_message)
        else:
            context.user_data["booking_state"] = "await_date"
            await transition_to_state(update, context, "await_date", edit_message=edit_message)
        return

    if edit_message and update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
        except Exception:
            await update.effective_message.reply_text(text, reply_markup=reply_markup)
    else:
        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
            except Exception:
                await update.effective_message.reply_text(text, reply_markup=reply_markup)
        else:
            await update.effective_message.reply_text(text, reply_markup=reply_markup)


async def handle_booking_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    history = context.user_data.get("history", [])
    if not history:
        if update.callback_query:
            await update.callback_query.edit_message_text("Главное меню", reply_markup=build_main_menu())
        else:
            await update.effective_message.reply_text("Главное меню", reply_markup=build_main_menu())
        context.user_data["booking_state"] = None
        return

    prev_state = history.pop()
    current_state = context.user_data.get("booking_state")

    # Сбрасываем некорректные/устаревшие данные при возврате назад
    if current_state == "await_name":
        context.user_data.pop("client_name", None)
    elif current_state == "await_phone":
        context.user_data.pop("client_phone", None)
    elif current_state == "await_age":
        context.user_data.pop("client_age", None)
    elif current_state == "await_parent_name":
        context.user_data.pop("parent_name", None)
    elif current_state == "await_parent_phone":
        context.user_data.pop("parent_phone", None)
    elif current_state == "await_date":
        context.user_data.pop("requested_date", None)
    elif current_state and current_state.startswith("await_medical_text_"):
        q_num = int(current_state.split("_")[-1])
        q_info = MEDICAL_QUESTIONS[q_num]
        if "medical_answers" in context.user_data:
            context.user_data["medical_answers"].pop(q_info["db_field"], None)
    elif current_state and current_state.startswith("medical_question_"):
        q_num = int(current_state.split("_")[-1])
        q_info = MEDICAL_QUESTIONS[q_num]
        if "medical_answers" in context.user_data:
            context.user_data["medical_answers"].pop(q_info["db_field"], None)

    await transition_to_state(update, context, prev_state, edit_message=True)


async def get_free_slots_for_date(calendar_service: GoogleCalendarService, target_date: datetime) -> list[datetime]:
    local_tz = timezone(timedelta(hours=5))
    busy_intervals = await calendar_service.get_busy_intervals(target_date)

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
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="booking_back")])
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
        [InlineKeyboardButton("⬅️ Назад", callback_data="booking_back")]
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


# Вспомогательные функции для отправки уведомлений администраторам


async def notify_admins_about_blood_disease(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Уведомляет всех администраторов о попытке записи пользователя с заболеваниями крови."""
    settings = get_settings()
    user = update.effective_user
    if not user:
        return

    user_id = user.id
    username_part = f", @{user.username}" if user.username else ""
    msg_text = (
        f"⚠️ Пользователь {user.full_name or 'Unknown'} (ID: {user_id}{username_part}) "
        f"попытался записаться на сеанс, но указал наличие заболевания крови. "
        f"Запись была автоматически отклонена."
    )

    async with AsyncSessionFactory() as session:
        db_admins = await session.scalars(select(User.telegram_id).where(User.role == UserRole.ADMIN))
        all_admins = set(settings.admin_telegram_ids) | set(db_admins)

    for admin_id in all_admins:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=msg_text,
            )
        except Exception as exc:
            logger.warning("Не удалось отправить оповещение о заболевании крови админу %s: %s", admin_id, exc)


async def notify_admins_about_minor(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    client_age: int,
    parent_name: str,
    parent_phone: str
) -> None:
    """Уведомляет администраторов о попытке записи несовершеннолетнего."""
    settings = get_settings()
    user = update.effective_user
    if not user:
        return

    client_name = html.escape(user.full_name or "Unknown")
    username_str = f" (@{user.username})" if user.username else ""

    msg_text = (
        f"⚠️ <b>Попытка записи несовершеннолетнего!</b>\n\n"
        f"Пользователь: {client_name}{username_str} (ID: {user.id})\n"
        f"Возраст: {client_age}\n"
        f"Родитель: {html.escape(parent_name)}\n"
        f"Телефон родителя: {html.escape(parent_phone)}"
    )

    async with AsyncSessionFactory() as session:
        db_admins = await session.scalars(select(User.telegram_id).where(User.role == UserRole.ADMIN))
        all_admins = set(settings.admin_telegram_ids) | set(db_admins)

    for admin_id in all_admins:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=msg_text,
                parse_mode="HTML"
            )
        except Exception as exc:
            logger.warning("Не удалось отправить оповещение о несовершеннолетнем админу %s: %s", admin_id, exc)


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
        # Требование (б) — мгновенно открываем тикет и уведомляем администраторов
        from handlers.chat_bridge import create_support_ticket
        await query.answer()
        try:
            await query.delete_message()
        except Exception:
            pass
        await create_support_ticket(update, context)
    elif data == "healing":
        # Проверяем доступ к инструкции по заживлению через базу данных
        async with AsyncSessionFactory() as session:
            user = await session.scalar(select(User).where(User.telegram_id == query.from_user.id))
        
        if user and user.has_healing_access:
            healing_text = (
                "✨ **Инструкция по заживлению пирсинга** ✨\n\n"
                "1. Не трогайте прокол руками.\n"
                "2. Обрабатывайте физраствором 2-3 раза в день.\n"
                "3. Избегайте саун, бассейнов и открытых водоемов первые 2-4 недели.\n"
                "4. Не проворачивайте украшение."
            )
            await query.edit_message_text(
                healing_text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="back:main")]]),
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(
                "Инструкция по заживлению заблокирована. Доступ предоставляется администратором студии после процедуры.",
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
    context.user_data["history"] = ["service_selection"]

    await transition_to_state(update, context, "await_name", edit_message=True)


async def handle_booking_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_message.text is None:
        return

    # Защита: если администратор сейчас находится в состоянии ввода настроек, пропускаем обработку ввода записи
    if context.user_data.get("admin_state"):
        return

    text = update.effective_message.text.strip()

    # Защита: игнорируем ввод кнопок смены меню на этапе заполнения
    if text in ["Старт", "В начало"]:
        return

    state = context.user_data.get("booking_state")
    if not state:
        return

    if state == "await_name":
        context.user_data["client_name"] = text
        context.user_data.setdefault("history", []).append("await_name")
        await transition_to_state(update, context, "await_phone")
        return

    elif state == "await_phone":
        context.user_data["client_phone"] = text
        context.user_data.setdefault("history", []).append("await_phone")
        await transition_to_state(update, context, "await_age")
        return

    elif state == "await_age":
        try:
            age = int(text)
        except ValueError:
            await update.effective_message.reply_text(
                "Пожалуйста, введите возраст цифрами.",
                reply_markup=build_back_button()
            )
            return

        context.user_data["client_age"] = age
        context.user_data.setdefault("history", []).append("await_age")

        if age < 18:
            next_state = "await_parent_name"
        else:
            if context.user_data.get("selected_service") == SERVICE_OPTIONS["piercing"]:
                next_state = "medical_question_1"
            else:
                next_state = "await_date"

        await transition_to_state(update, context, next_state)
        return

    elif state == "await_parent_name":
        context.user_data["parent_name"] = text
        context.user_data.setdefault("history", []).append("await_parent_name")
        await transition_to_state(update, context, "await_parent_phone")
        return

    elif state == "await_parent_phone":
        context.user_data["parent_phone"] = text
        context.user_data.setdefault("history", []).append("await_parent_phone")

        # Требование (в) — Ввод информации о несовершеннолетнем завершен. Оповещаем администраторов
        client_age = context.user_data.get("client_age", 0)
        parent_name = context.user_data.get("parent_name", "Не указано")
        parent_phone = text

        await notify_admins_about_minor(update, context, client_age, parent_name, parent_phone)

        if context.user_data.get("selected_service") == SERVICE_OPTIONS["piercing"]:
            next_state = "medical_question_1"
        else:
            next_state = "await_date"

        await transition_to_state(update, context, next_state)
        return

    elif state == "await_date":
        try:
            input_date = datetime.strptime(text, "%d.%m.%Y")
            today = datetime.now(timezone(timedelta(hours=5))).replace(hour=0, minute=0, second=0, microsecond=0)
            input_date_local = input_date.replace(tzinfo=timezone(timedelta(hours=5)))
            if input_date_local < today:
                await update.effective_message.reply_text(
                    "Дата не может быть в прошлом. Пожалуйста, введите корректную будущую дату в формате ДД.ММ.ГГГГ:",
                    reply_markup=build_back_button()
                )
                return
        except ValueError:
            await update.effective_message.reply_text(
                "Неверный формат даты. Пожалуйста, введите дату в формате ДД.ММ.ГГГГ (например, 25.07.2026):",
                reply_markup=build_back_button()
            )
            return

        context.user_data["requested_date"] = text
        context.user_data.setdefault("history", []).append("await_date")
        await transition_to_state(update, context, "select_slot")
        return

    elif state.startswith("await_medical_text_"):
        q_num = int(state.split("_")[-1])
        q_info = MEDICAL_QUESTIONS[q_num]
        
        context.user_data.setdefault("medical_answers", {})
        context.user_data["medical_answers"][q_info["db_field"]] = text
        context.user_data.setdefault("history", []).append(state)
        
        if q_num < 6:
            next_state = f"medical_question_{q_num+1}"
        else:
            next_state = "await_date"
            
        await transition_to_state(update, context, next_state)
        return


async def handle_booking_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    data = query.data or ""
    if data == "booking_back":
        await handle_booking_back(update, context)
        return

    if data == "back:service":
        await query.edit_message_text("Главное меню", reply_markup=build_main_menu())
        return

    if data == "change_date":
        context.user_data["booking_state"] = "await_date"
        await query.edit_message_text(
            "Введите желаемую дату для записи в формате ДД.ММ.ГГГГ (например, 25.07.2026):",
            reply_markup=build_back_button()
        )
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
        q_state = context.user_data.get("booking_state", "")
        if q_state.startswith("medical_question_"):
            q_num = int(q_state.split("_")[-1])
            q_info = MEDICAL_QUESTIONS[q_num]
            answer = data.split(":", 1)[1]
            
            context.user_data.setdefault("medical_answers", {})
            
            # Требование (а) — Прерывание при заболевании крови
            if q_num == 1 and answer == "yes":
                await notify_admins_about_blood_disease(update, context)

                # Полная очистка временного состояния сессии
                for key in [
                    "booking_state", "client_name", "client_phone", "client_age",
                    "parent_name", "parent_phone", "medical_answers", "selected_service",
                    "requested_date", "last_checked_date", "history", "admin_state",
                    "temp_latitude", "temp_longitude"
                ]:
                    context.user_data.pop(key, None)

                # Вывод сообщения пользователю и возврат в главное меню
                text = (
                    "К сожалению, мы не сможем записать вас на прием, "
                    "так как мастер не работает с клиентами, имеющими заболевания крови.\n"
                    "Приносим извинения за неудобства."
                )
                await query.edit_message_text(text, reply_markup=build_main_menu())
                return
            
            if answer in ["yes", "bad"]:
                context.user_data.setdefault("history", []).append(q_state)
                next_state = q_info["detail_state"]
                await transition_to_state(update, context, next_state, edit_message=True)
            else:
                ans_text = "Нет" if answer == "no" else "Хорошая"
                context.user_data["medical_answers"][q_info["db_field"]] = ans_text
                context.user_data.setdefault("history", []).append(q_state)
                
                if q_num < 6:
                    next_state = f"medical_question_{q_num+1}"
                else:
                    next_state = "await_date"
                await transition_to_state(update, context, next_state, edit_message=True)
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
                parent_name=context.user_data.get("parent_name"),
                parent_phone=context.user_data.get("parent_phone"),
                has_parent_consent=True if context.user_data.get("client_age", 0) < 18 else None,
                blood_disease=context.user_data.get("medical_answers", {}).get("blood_disease"),
                blood_clotting=context.user_data.get("medical_answers", {}).get("blood_clotting"),
                current_medication=context.user_data.get("medical_answers", {}).get("current_medication"),
                chronic_disease=context.user_data.get("medical_answers", {}).get("chronic_disease"),
                healing_issues=context.user_data.get("medical_answers", {}).get("healing_issues"),
                skin_disease=context.user_data.get("medical_answers", {}).get("skin_disease"),
                date_time=slot_dt.replace(tzinfo=None),
                status=BookingStatus.CONFIRMED,
            )
            session.add(booking)
            await session.commit()
            await session.refresh(booking)

            end_dt = slot_dt + timedelta(hours=1, minutes=30)
            
            # Подготовка подробного описания события для календаря
            desc_lines = [
                f"Клиент: {booking.client_name}",
                f"Контакт: {booking.client_phone}",
                f"Возраст: {booking.client_age}",
                f"Услуга: {booking.service_name}"
            ]
            if booking.client_age < 18:
                desc_lines.append(f"Родитель: {booking.parent_name} ({booking.parent_phone})")
                desc_lines.append("Согласие родителя: Да (автоматически)")
            
            if booking.blood_disease:
                desc_lines.append(f"Заболевания крови: {booking.blood_disease}")
            if booking.blood_clotting:
                desc_lines.append(f"Свертываемость: {booking.blood_clotting}")
            if booking.current_medication:
                desc_lines.append(f"Принимаемые лекарства: {booking.current_medication}")
            if booking.chronic_disease:
                desc_lines.append(f"Хронические заболевания: {booking.chronic_disease}")
            if booking.healing_issues:
                desc_lines.append(f"Проблемы с заживлением: {booking.healing_issues}")
            if booking.skin_disease:
                desc_lines.append(f"Кожные заболевания: {booking.skin_disease}")

            calendar_event_id = await calendar_service.create_booking_event(
                booking_summary=f"Запись: {booking.client_name} ({booking.service_name})",
                description="\n".join(desc_lines),
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
            send_kwargs = {"text": booking_message}
            if getattr(update.effective_chat, "is_direct_messages", False):
                topic_id = None
                if update.effective_message:
                    if update.effective_message.direct_messages_topic:
                        topic_id = update.effective_message.direct_messages_topic.topic_id
                    elif update.effective_message.message_thread_id:
                        topic_id = update.effective_message.message_thread_id
                if topic_id:
                    send_kwargs["direct_messages_topic_id"] = topic_id
            await update.effective_chat.send_message(**send_kwargs)

        # Полная очистка временного состояния
        context.user_data.pop("booking_state", None)
        context.user_data.pop("client_name", None)
        context.user_data.pop("client_phone", None)
        context.user_data.pop("client_age", None)
        context.user_data.pop("parent_name", None)
        context.user_data.pop("parent_phone", None)
        context.user_data.pop("medical_answers", None)
        context.user_data.pop("selected_service", None)
        context.user_data.pop("requested_date", None)
        context.user_data.pop("last_checked_date", None)
        context.user_data.pop("history", None)
        return


client_handlers = [
    CallbackQueryHandler(handle_main_menu_callback, pattern=r"^(book|support|healing|back:main)$"),
    CallbackQueryHandler(handle_service_selection, pattern=r"^service:"),
    CallbackQueryHandler(handle_booking_callback, pattern=r"^(back:service|slot:|medical:|change_date|check_date:|book_slot:|booking_back)"),
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_booking_input),
]
