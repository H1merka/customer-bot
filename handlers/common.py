# handlers/common.py
from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ApplicationHandlerStop

from database.connection import AsyncSessionFactory
from database.models import User
from sqlalchemy import select


async def register_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    async with AsyncSessionFactory() as session:
        existing = await session.scalar(select(User).where(User.telegram_id == user.id))
        if existing is None:
            session.add(
                User(
                    telegram_id=user.id,
                    username=user.username,
                    full_name=user.full_name or user.first_name or "Unknown",
                )
            )
            await session.commit()


def build_main_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("Записаться", callback_data="book")],
        [InlineKeyboardButton("Связаться со штатным пирсером", callback_data="support")],
        [InlineKeyboardButton("Инструкция по заживлению", callback_data="healing")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update, context)
    if update.effective_message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id

    # 1. Принудительный сброс всех состояний сценария бронирования в context.user_data
    for key in [
        "booking_state", "client_name", "client_phone", "client_age",
        "parent_name", "parent_phone", "medical_answers", "selected_service",
        "requested_date", "last_checked_date", "history", "admin_state",
        "temp_latitude", "temp_longitude"
    ]:
        context.user_data.pop(key, None)

    # 2. Поиск и закрытие активных тикетов поддержки (завершение диалога)
    async with AsyncSessionFactory() as session:
        from database.models import SupportTicket, SupportTicketStatus
        ticket = await session.scalar(
            select(SupportTicket).where(
                SupportTicket.user_telegram_id == user_id,
                SupportTicket.status == SupportTicketStatus.OPEN
            )
        )
        if ticket is not None:
            ticket.status = SupportTicketStatus.CLOSED
            ticket.assigned_admin_id = None
            await session.commit()

    # Импортируем внутри функции во избежание круговых импортов
    from handlers.admin import check_if_admin, build_admin_main_menu
    from telegram import ReplyKeyboardMarkup, KeyboardButton

    is_admin = await check_if_admin(user_id)

    if is_admin:
        # Для администратора закрепляется стандартная клавиатура "Старт"
        admin_reply_markup = ReplyKeyboardMarkup(
            [[KeyboardButton("Старт")]],
            resize_keyboard=True
        )
        await update.effective_message.reply_text(
            "Панель администратора активирована.",
            reply_markup=admin_reply_markup
        )
        await update.effective_message.reply_text(
            "Добро пожаловать в административный интерфейс студии пирсинга.\nВыберите действие ниже.",
            reply_markup=build_admin_main_menu()
        )
    else:
        # Для клиента клавиатура заменяется: "Старт" при первом выводе, затем постоянная "В начало"
        start_reply_markup = ReplyKeyboardMarkup(
            [[KeyboardButton("Старт")]],
            resize_keyboard=True
        )
        await update.effective_message.reply_text(
            "Бот запущен. Для возврата к началу используйте кнопки управления.",
            reply_markup=start_reply_markup
        )

        client_reply_markup = ReplyKeyboardMarkup(
            [[KeyboardButton("В начало")]],
            resize_keyboard=True,
            is_persistent=True
        )
        await update.effective_message.reply_text(
            "Клавиатура обновлена.",
            reply_markup=client_reply_markup
        )
        await update.effective_message.reply_text(
            "Добро пожаловать в студию пирсинга.\nВыберите действие ниже.",
            reply_markup=build_main_menu()
        )

    # Предотвращаем вызовы остальных групп обработчиков на это обновление
    raise ApplicationHandlerStop()


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(
        "Доступные команды:\n/start — главное меню\n/help — помощь\n/settings — настройки администратора"
    )


async def handle_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update, context)
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(
        "Вы перешли из канала. Мы уже зарегистрировали вас и открыли главное меню.",
        reply_markup=build_main_menu(),
    )
