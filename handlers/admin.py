from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import get_settings
from database.connection import AsyncSessionFactory
from database.models import StudioSetting, User
from sqlalchemy import select

settings = get_settings()


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_message is None:
        return

    if update.effective_user.id not in settings.admin_telegram_ids:
        await update.effective_message.reply_text("У вас нет доступа к административным настройкам.")
        return

    await update.effective_message.reply_text(
        "Административный интерфейс доступен.\n"
        "Доступные команды:\n"
        "/grant_access <telegram_id> — выдать доступ к инструкции\n"
        "/revoke_access <telegram_id> — отозвать доступ\n"
        "/set_location — сохранить адрес и координаты студии"
    )


async def grant_healing_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_message is None:
        return

    if update.effective_user.id not in settings.admin_telegram_ids:
        return

    args = context.args
    if not args:
        await update.effective_message.reply_text("Использование: /grant_access <telegram_id>")
        return

    telegram_id = int(args[0])
    async with AsyncSessionFactory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            await update.effective_message.reply_text("Пользователь не найден в базе данных.")
            return

        user.has_healing_access = True
        await session.commit()

    await update.effective_message.reply_text("Доступ к инструкции по заживлению выдан.")


async def revoke_healing_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_message is None:
        return

    if update.effective_user.id not in settings.admin_telegram_ids:
        return

    args = context.args
    if not args:
        await update.effective_message.reply_text("Использование: /revoke_access <telegram_id>")
        return

    telegram_id = int(args[0])
    async with AsyncSessionFactory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            await update.effective_message.reply_text("Пользователь не найден в базе данных.")
            return

        user.has_healing_access = False
        await session.commit()

    await update.effective_message.reply_text("Доступ к инструкции по заживлению отозван.")


async def set_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_message is None:
        return

    if update.effective_user.id not in settings.admin_telegram_ids:
        await update.effective_message.reply_text("У вас нет доступа к административным настройкам.")
        return

    if update.effective_message.location is None:
        await update.effective_message.reply_text(
            "Отправьте геолокацию студии через Telegram, затем укажите адрес текстом."
        )
        return

    address = " ".join(context.args) if context.args else "Адрес не указан"

    async with AsyncSessionFactory() as session:
        session.add(StudioSetting(key="latitude", value=str(update.effective_message.location.latitude)))
        session.add(StudioSetting(key="longitude", value=str(update.effective_message.location.longitude)))
        session.add(StudioSetting(key="address_text", value=address))
        await session.commit()

    await update.effective_message.reply_text("Геолокация студии сохранена.")
