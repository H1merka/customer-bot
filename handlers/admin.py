# handlers/admin.py
from __future__ import annotations

import html
import logging
from datetime import datetime, date, time, timezone, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters, ApplicationHandlerStop

from config.settings import get_settings
from database.connection import AsyncSessionFactory
from database.models import StudioSetting, User, UserRole, DayOff, Booking, BookingStatus
from sqlalchemy import select, or_
from sqlalchemy.orm import joinedload
from services.google_calendar import GoogleCalendarService

logger = logging.getLogger(__name__)
settings = get_settings()


async def check_if_admin(user_id: int) -> bool:
    """
    Проверяет, является ли пользователь администратором.
    Сначала сверяется со статическим списком в настройках .env, 
    затем делает запрос в базу данных.
    """
    if user_id in settings.admin_telegram_ids:
        return True
    async with AsyncSessionFactory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == user_id))
        if user and user.role == UserRole.ADMIN:
            return True
    return False


def build_admin_main_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("Помощь", callback_data="admin_menu:help"),
            InlineKeyboardButton("Настройки", callback_data="admin_menu:settings")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def build_admin_help_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("Помощь", callback_data="admin_menu:help"),
            InlineKeyboardButton("Настройки", callback_data="admin_menu:settings")
        ],
        [InlineKeyboardButton("Назад", callback_data="admin_menu:back_to_start")]
    ]
    return InlineKeyboardMarkup(keyboard)


def build_admin_settings_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("Выдать доступ к заживлению", callback_data="admin_setting:grant_healing")],
        [InlineKeyboardButton("Обновить текст инструкции по заживлению", callback_data="admin_setting:update_healing")],
        [InlineKeyboardButton("Добавить админа", callback_data="admin_setting:add_admin")],
        [InlineKeyboardButton("Отозвать права админа", callback_data="admin_setting:revoke_admin")],
        [InlineKeyboardButton("Изменить адрес и геолокацию", callback_data="admin_setting:set_location")],
        [InlineKeyboardButton("Добавить выходные", callback_data="admin_setting:add_days_off")],
        [InlineKeyboardButton("Назад", callback_data="admin_menu:back_to_start")]
    ]
    return InlineKeyboardMarkup(keyboard)


def build_admin_cancel_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="admin_menu:settings")]])


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_message is None:
        return

    if not await check_if_admin(update.effective_user.id):
        await update.effective_message.reply_text("У вас нет доступа к административным настройкам.")
        return

    await update.effective_message.reply_text(
        "Административные настройки студии:",
        reply_markup=build_admin_settings_menu()
    )


async def grant_healing_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Legacy-команда быстрого доступа для обратной совместимости."""
    if update.effective_user is None or update.effective_message is None:
        return

    if not await check_if_admin(update.effective_user.id):
        return

    args = context.args
    if not args:
        await update.effective_message.reply_text("Использование: /grant_access <telegram_id>")
        return

    try:
        telegram_id = int(args[0])
    except ValueError:
        await update.effective_message.reply_text("Telegram ID должен быть числом.")
        return

    async with AsyncSessionFactory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            user = User(telegram_id=telegram_id, username="unknown", full_name="Пользователь")
            session.add(user)
            await session.flush()

        user.has_healing_access = True
        await session.commit()

    await update.effective_message.reply_text("Доступ к инструкции по заживлению выдан.")


async def handle_admin_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    if not await check_if_admin(update.effective_user.id):
        await query.answer("У вас нет прав администратора.", show_alert=True)
        return

    data = query.data or ""
    action = data.split(":", 1)[1]

    if action == "help":
        help_text = (
            "Административный интерфейс.\n\n"
            "Доступные разделы:\n"
            "• Настройки — управление доступом к инструкции по заживлению, добавление/удаление администраторов, изменение адреса и геолокации студии.\n"
            "• Помощь — описание доступного функционала."
        )
        await query.edit_message_text(help_text, reply_markup=build_admin_help_menu())
    elif action == "settings":
        context.user_data.pop("admin_state", None)
        context.user_data.pop("temp_days_off_dates", None)
        await query.edit_message_text("Административные настройки студии:", reply_markup=build_admin_settings_menu())
    elif action == "back_to_start":
        context.user_data.pop("admin_state", None)
        context.user_data.pop("temp_days_off_dates", None)
        await query.edit_message_text(
            "Добро пожаловать в административный интерфейс студии пирсинга.\nВыберите действие ниже.",
            reply_markup=build_admin_main_menu()
        )


async def handle_admin_setting_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    if not await check_if_admin(update.effective_user.id):
        await query.answer("У вас нет прав администратора.", show_alert=True)
        return

    data = query.data or ""
    setting_action = data.split(":", 1)[1]

    if setting_action == "grant_healing":
        context.user_data["admin_state"] = "await_userid_grant_healing"
        await query.edit_message_text(
            "Введите Telegram ID пользователя, которому хотите выдать доступ к инструкции по заживлению:",
            reply_markup=build_admin_cancel_button()
        )
    elif setting_action == "update_healing":
        context.user_data["admin_state"] = "await_healing_instructions_text"
        await query.edit_message_text(
            "Введите новый текст инструкции по заживлению (будет сохранен как обычный текст):",
            reply_markup=build_admin_cancel_button()
        )
    elif setting_action == "add_admin":
        context.user_data["admin_state"] = "await_userid_add_admin"
        await query.edit_message_text(
            "Введите Telegram ID пользователя, которого хотите назначить администратором:",
            reply_markup=build_admin_cancel_button()
        )
    elif setting_action == "revoke_admin":
        context.user_data["admin_state"] = "await_userid_revoke_admin"
        await query.edit_message_text(
            "Введите Telegram ID администратора, у которого хотите отозвать права:",
            reply_markup=build_admin_cancel_button()
        )
    elif setting_action == "set_location":
        context.user_data["admin_state"] = "await_location"
        await query.edit_message_text(
            "Отправьте геолокацию студии через Telegram (прикрепите геопозицию).",
            reply_markup=build_admin_cancel_button()
        )
    elif setting_action == "add_days_off":
        context.user_data["admin_state"] = "await_days_off"
        await query.edit_message_text(
            "Введите даты выходных в формате ДД.ММ.ГГГГ через пробел (например, 25.07.2026 26.07.2026):",
            reply_markup=build_admin_cancel_button()
        )


async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_message is None or update.effective_message.text is None:
        return

    if not await check_if_admin(update.effective_user.id):
        return

    admin_state = context.user_data.get("admin_state")
    if not admin_state:
        return

    text = update.effective_message.text.strip()

    if admin_state in ["await_userid_grant_healing", "await_userid_add_admin", "await_userid_revoke_admin"]:
        try:
            target_id = int(text)
        except ValueError:
            await update.effective_message.reply_text(
                "Пожалуйста, введите корректный числовой Telegram ID пользователя.",
                reply_markup=build_admin_cancel_button()
            )
            raise ApplicationHandlerStop()

        async with AsyncSessionFactory() as session:
            user = await session.scalar(select(User).where(User.telegram_id == target_id))
            
            if user is None:
                user = User(
                    telegram_id=target_id,
                    username="unknown",
                    full_name="Пользователь",
                )
                session.add(user)
                await session.flush()

            if admin_state == "await_userid_grant_healing":
                user.has_healing_access = True
                success_text = f"Доступ к инструкции по заживлению успешно выдан пользователю {target_id}."
            elif admin_state == "await_userid_add_admin":
                user.role = UserRole.ADMIN
                success_text = f"Пользователь {target_id} успешно назначен администратором."
            elif admin_state == "await_userid_revoke_admin":
                user.role = UserRole.USER
                success_text = f"Права администратора у пользователя {target_id} успешно отозваны."

            await session.commit()

        context.user_data.pop("admin_state", None)
        await update.effective_message.reply_text(
            success_text + "\n\nВозврат в меню настроек.",
            reply_markup=build_admin_settings_menu()
        )
        raise ApplicationHandlerStop()

    if admin_state == "await_healing_instructions_text":
        async with AsyncSessionFactory() as session:
            setting = await session.get(StudioSetting, "healing_instructions")
            if setting:
                setting.value = text
            else:
                session.add(StudioSetting(key="healing_instructions", value=text))
            await session.commit()

        context.user_data.pop("admin_state", None)
        await update.effective_message.reply_text(
            "Текст инструкции по заживлению успешно обновлен.\n\nВозврат в меню настроек.",
            reply_markup=build_admin_settings_menu()
        )
        raise ApplicationHandlerStop()

    if admin_state == "await_address":
        latitude = context.user_data.pop("temp_latitude", None)
        longitude = context.user_data.pop("temp_longitude", None)

        if latitude is None or longitude is None:
            await update.effective_message.reply_text(
                "Произошла ошибка: координаты не найдены. Попробуйте начать заново.",
                reply_markup=build_admin_settings_menu()
            )
            context.user_data.pop("admin_state", None)
            raise ApplicationHandlerStop()

        async with AsyncSessionFactory() as session:
            for key, val in [("latitude", str(latitude)), ("longitude", str(longitude)), ("address_text", text)]:
                setting = await session.get(StudioSetting, key)
                if setting:
                    setting.value = val
                else:
                    session.add(StudioSetting(key=key, value=val))
            await session.commit()

        context.user_data.pop("admin_state", None)
        await update.effective_message.reply_text(
            "Геолокация и текстовый адрес студии успешно обновлены.\n\nВозврат в меню настроек.",
            reply_markup=build_admin_settings_menu()
        )
        raise ApplicationHandlerStop()

    if admin_state == "await_days_off":
        dates_str = text.split()
        valid_dates = []
        invalid_dates = []

        for d_str in dates_str:
            try:
                parsed_date = datetime.strptime(d_str, "%d.%m.%Y").date()
                valid_dates.append(parsed_date)
            except ValueError:
                invalid_dates.append(d_str)

        if invalid_dates:
            await update.effective_message.reply_text(
                f"Не удалось распознать следующие даты: {', '.join(invalid_dates)}.\n"
                "Пожалуйста, введите корректные даты в формате ДД.ММ.ГГГГ через пробел:",
                reply_markup=build_admin_cancel_button()
            )
            raise ApplicationHandlerStop()

        if not valid_dates:
            await update.effective_message.reply_text(
                "Вы не ввели ни одной даты. Пожалуйста, попробуйте еще раз:",
                reply_markup=build_admin_cancel_button()
            )
            raise ApplicationHandlerStop()

        # Формируем SQL-фильтр для поиска существующих подтвержденных записей
        clauses = []
        for d in valid_dates:
            start_dt = datetime.combine(d, time.min)
            end_dt = datetime.combine(d, time.max)
            clauses.append((Booking.date_time >= start_dt) & (Booking.date_time <= end_dt))

        async with AsyncSessionFactory() as session:
            conflicting_bookings = []
            if clauses:
                # Используем joinedload для упреждающей (eager) загрузки связи Booking.user
                stmt = (
                    select(Booking)
                    .options(joinedload(Booking.user))
                    .where(Booking.status == BookingStatus.CONFIRMED)
                    .where(or_(*clauses))
                )
                res = await session.scalars(stmt)
                # unique() обеспечивает консистентность объектов в кэше SQLAlchemy
                conflicting_bookings = list(res.unique().all())

        context.user_data["temp_days_off_dates"] = [d.isoformat() for d in valid_dates]
        context.user_data["admin_state"] = "await_days_off_confirm"

        if conflicting_bookings:
            # Группируем конфликтующие записи по датам
            by_date: dict[date, list[Booking]] = {}
            for b in conflicting_bookings:
                by_date.setdefault(b.date_time.date(), []).append(b)

            text_lines = ["⚠️ <b>Внимание! Обнаружены конфликтующие записи:</b>\n"]
            for d in sorted(by_date.keys()):
                text_lines.append(f"📅 <b>{d.strftime('%d.%m.%Y')}</b>:")
                for b in sorted(by_date[d], key=lambda x: x.date_time):
                    time_str = b.date_time.strftime("%H:%M")
                    username_str = f" (@{html.escape(b.user.username)})" if b.user and b.user.username else ""
                    text_lines.append(
                        f"  • {time_str} - {html.escape(b.client_name)}{username_str} "
                        f"(Тел: {html.escape(b.client_phone)}, Услуга: {html.escape(b.service_name)})"
                    )
                text_lines.append("")

            text_lines.append(
                "При установке выходных все эти записи будут <b>ОТМЕНЕНЫ</b>, "
                "а клиенты получат уведомление в свои личные чаты.\n\n"
                "Вы уверены, что хотите сделать эти дни выходными?"
            )
            confirm_text = "\n".join(text_lines)
        else:
            dates_list_str = ", ".join(d.strftime("%d.%m.%Y") for d in sorted(valid_dates))
            confirm_text = (
                f"На выбранные даты (<b>{dates_list_str}</b>) нет активных записей.\n\n"
                "Вы уверены, что хотите установить эти выходные?"
            )

        keyboard = [
            [
                InlineKeyboardButton("Да", callback_data="admin_dayoff_confirm:yes"),
                InlineKeyboardButton("Нет", callback_data="admin_dayoff_confirm:no")
            ]
        ]
        await update.effective_message.reply_text(
            confirm_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        raise ApplicationHandlerStop()


async def handle_dayoff_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    if not await check_if_admin(update.effective_user.id):
        await query.answer("У вас нет прав администратора.", show_alert=True)
        return

    data = query.data or ""
    decision = data.split(":", 1)[1]

    temp_dates_str = context.user_data.pop("temp_days_off_dates", None)
    context.user_data.pop("admin_state", None)

    if decision == "no" or not temp_dates_str:
        await query.answer("Действие отменено.")
        await query.edit_message_text(
            "Установка выходных дней отменена.\n\nВозврат в меню настроек.",
            reply_markup=build_admin_settings_menu()
        )
        return

    await query.answer("Изменения применяются...")
    dates = [date.fromisoformat(d) for d in temp_dates_str]

    clauses = []
    for d in dates:
        start_dt = datetime.combine(d, time.min)
        end_dt = datetime.combine(d, time.max)
        clauses.append((Booking.date_time >= start_dt) & (Booking.date_time <= end_dt))

    async with AsyncSessionFactory() as session:
        conflicting_bookings = []
        if clauses:
            # Безопасная выборка с joinedload для предотвращения DetachedInstanceError при уведомлении
            stmt = (
                select(Booking)
                .options(joinedload(Booking.user))
                .where(Booking.status == BookingStatus.CONFIRMED)
                .where(or_(*clauses))
            )
            res = await session.scalars(stmt)
            conflicting_bookings = list(res.unique().all())

        calendar_service = GoogleCalendarService(get_settings())

        # 1. Отмена конфликтующих записей, их удаление из Google Calendar и уведомление клиентов
        for booking in conflicting_bookings:
            booking.status = BookingStatus.CANCELLED
            if booking.google_event_id:
                await calendar_service.delete_booking_event(booking.google_event_id)
                booking.google_event_id = None

            notify_text = (
                f"⚠️ Здравствуйте, <b>{html.escape(booking.client_name)}</b>!\n\n"
                f"К сожалению, ваша запись на <b>{booking.date_time.strftime('%d.%m.%Y в %H:%M')}</b> "
                f"была отменена, так как этот день объявлен выходным в студии пирсинга.\n\n"
                f"Приносим извинения за неудобства! Вы можете выбрать любое другое свободное время для записи через меню бота."
            )
            try:
                if booking.chat_id and booking.direct_messages_topic_id:
                    # Отправляем сообщение в чат прямого диалога Сообщений канала (Channel DM)
                    await context.bot.send_message(
                        chat_id=booking.chat_id,
                        direct_messages_topic_id=booking.direct_messages_topic_id,
                        text=notify_text,
                        parse_mode="HTML"
                    )
                    logger.info("Уведомление об отмене отправлено в топик для бронирования %s", booking.id)
                else:
                    # Резервный вариант — прямая отправка в ЛС пользователю
                    await context.bot.send_message(
                        chat_id=booking.user_id,
                        text=notify_text,
                        parse_mode="HTML"
                    )
                    logger.info("Уведомление об отмене отправлено в ЛС для бронирования %s", booking.id)
            except Exception as exc:
                logger.error("Не удалось отправить уведомление пользователю %s об отмене бронирования %s: %s", booking.user_id, booking.id, exc)

        # 2. Сохранение выходных дней в БД и создание блокирующих событий в Google Calendar
        added_dates_count = 0
        for d in dates:
            # Защита от дублей записей в БД
            exists = await session.scalar(select(DayOff).where(DayOff.date == d))
            if exists:
                continue

            # Блокировка всего дня (UTC+5)
            local_tz = timezone(timedelta(hours=5))
            start_dt = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=local_tz)
            end_dt = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=local_tz)

            g_event_id = await calendar_service.create_booking_event(
                booking_summary="ВЫХОДНОЙ СТУДИИ",
                description="Этот день был отмечен как выходной администратором в настройках.",
                start_dt=start_dt,
                end_dt=end_dt
            )

            day_off = DayOff(date=d, google_event_id=g_event_id)
            session.add(day_off)
            added_dates_count += 1

        await session.commit()

    dates_str_list = ", ".join(d.strftime("%d.%m.%Y") for d in dates)
    await query.edit_message_text(
        f"Выходные успешно установлены на даты: <b>{dates_str_list}</b>.\n"
        f"Отменено конфликтующих записей: <b>{len(conflicting_bookings)}</b>.\n\n"
        f"Возврат в меню настроек.",
        reply_markup=build_admin_settings_menu(),
        parse_mode="HTML"
    )


async def handle_admin_location_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_message is None or update.effective_message.location is None:
        return

    if not await check_if_admin(update.effective_user.id):
        return

    admin_state = context.user_data.get("admin_state")
    if admin_state != "await_location":
        return

    loc = update.effective_message.location
    context.user_data["temp_latitude"] = loc.latitude
    context.user_data["temp_longitude"] = loc.longitude
    context.user_data["admin_state"] = "await_address"

    await update.effective_message.reply_text(
        "Координаты зафиксированы. Теперь введите текстовый адрес студии:",
        reply_markup=build_admin_cancel_button()
    )
    raise ApplicationHandlerStop()


admin_handlers = [
    CallbackQueryHandler(handle_admin_menu_callback, pattern=r"^admin_menu:"),
    CallbackQueryHandler(handle_admin_setting_callback, pattern=r"^admin_setting:"),
    CallbackQueryHandler(handle_dayoff_confirm_callback, pattern=r"^admin_dayoff_confirm:"),
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_input),
    MessageHandler(filters.LOCATION, handle_admin_location_input),
]
