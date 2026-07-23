# handlers/common.py
from __future__ import annotations

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import ContextTypes, ApplicationHandlerStop

from database.connection import AsyncSessionFactory
from database.models import User
from sqlalchemy import select
from config.constants import clear_booking_session


async def register_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    # Быстрый возврат без лишних запросов к БД, если пользователь уже зарегистрирован в сессии
    if context.user_data and context.user_data.get("is_registered") is True:
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

        if context.user_data:
            context.user_data["is_registered"] = True


def build_main_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("Записаться", callback_data="book")],
        [
            InlineKeyboardButton(
                "Связаться со штатным пирсером", callback_data="support"
            )
        ],
        [InlineKeyboardButton("Инструкция по заживлению", callback_data="healing")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update, context)
    if update.effective_message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id

    clear_booking_session(context.user_data)

    async with AsyncSessionFactory() as session:
        from database.models import SupportTicket, SupportTicketStatus

        ticket = await session.scalar(
            select(SupportTicket).where(
                SupportTicket.user_telegram_id == user_id,
                SupportTicket.status == SupportTicketStatus.OPEN,
            )
        )
        if ticket is not None:
            ticket.status = SupportTicketStatus.CLOSED
            ticket.assigned_admin_id = None
            await session.commit()

    from handlers.admin import check_if_admin, build_admin_main_menu

    is_admin = await check_if_admin(user_id, context)

    # ИСПРАВЛЕНО: Загрузка кастомного приветственного текста из MediaTemplate
    from handlers.client import get_custom_text, DEFAULT_CUSTOM_TEXTS
    welcome_text = await get_custom_text("custom_txt:welcome", DEFAULT_CUSTOM_TEXTS["welcome"])

    if is_admin:
        admin_reply_markup = ReplyKeyboardMarkup(
            [[KeyboardButton("Старт")]], resize_keyboard=True
        )
        await update.effective_message.reply_text(
            "Панель администратора активирована.", reply_markup=admin_reply_markup
        )
        await update.effective_message.reply_text(
            "Добро пожаловать в административный интерфейс студии пирсинга.\nВыберите действие ниже.",
            reply_markup=build_admin_main_menu(),
        )
    else:
        start_reply_markup = ReplyKeyboardMarkup(
            [[KeyboardButton("Старт")]], resize_keyboard=True
        )
        await update.effective_message.reply_text(
            "Бот запущен. Для возврата к началу используйте кнопки управления.",
            reply_markup=start_reply_markup,
        )

        client_reply_markup = ReplyKeyboardMarkup(
            [[KeyboardButton("Старт"), KeyboardButton("В начало")]], resize_keyboard=True, is_persistent=True
        )
        await update.effective_message.reply_text(
            "Клавиатура обновлена.", reply_markup=client_reply_markup
        )
        await update.effective_message.reply_text(
            welcome_text,
            reply_markup=build_main_menu(),
        )

    raise ApplicationHandlerStop()


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(
        "Доступные команды:\n/start — главное меню\n/help — помощь\n/settings — настройки администратора"
    )


async def handle_channel_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await register_user(update, context)
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(
        "Вы перешли из канала. Мы уже зарегистрировали вас и открыли главное меню.",
        reply_markup=build_main_menu(),
    )
