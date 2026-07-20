# handlers/common.py
from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

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

    # Импортируем внутри функции во избежание круговых импортов
    from handlers.admin import check_if_admin, build_admin_main_menu

    if await check_if_admin(update.effective_user.id):
        text = (
            "Добро пожаловать в административный интерфейс студии пирсинга.\n"
            "Выберите действие ниже."
        )
        await update.effective_message.reply_text(text, reply_markup=build_admin_main_menu())
        return

    text = (
        "Добро пожаловать в студию пирсинга.\n"
        "Выберите действие ниже."
    )
    await update.effective_message.reply_text(text, reply_markup=build_main_menu())


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
