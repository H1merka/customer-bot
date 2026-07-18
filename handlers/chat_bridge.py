from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.settings import get_settings
from database.connection import AsyncSessionFactory
from database.models import SupportTicket, SupportTicketStatus
from sqlalchemy import select

settings = get_settings()


async def create_support_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_message is None:
        return

    async with AsyncSessionFactory() as session:
        existing = await session.scalar(
            select(SupportTicket).where(SupportTicket.user_telegram_id == update.effective_user.id)
        )
        if existing is None:
            session.add(
                SupportTicket(
                    user_telegram_id=update.effective_user.id,
                    status=SupportTicketStatus.OPEN,
                )
            )
            await session.commit()
        else:
            existing.status = SupportTicketStatus.OPEN
            existing.assigned_admin_id = None
            await session.commit()

    await update.effective_message.reply_text(
        "Тикет поддержки открыт. Пирсер подключится к диалогу в ближайшее время.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Принять вызов", callback_data="accept_support")]]),
    )


async def relay_message_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_message is None:
        return

    async with AsyncSessionFactory() as session:
        ticket = await session.scalar(
            select(SupportTicket).where(SupportTicket.user_telegram_id == update.effective_user.id)
        )
        assigned_admin_id = None
        if ticket is not None:
            assigned_admin_id = ticket.assigned_admin_id

    target_chat_id = assigned_admin_id if assigned_admin_id is not None else None
    reply_text = (
        f"[Сообщение от клиента ID: {update.effective_user.id}]\n"
        f"{update.effective_message.text or 'Attachment received'}"
    )
    await update.effective_message.reply_text(reply_text)
    if target_chat_id is not None:
        await context.bot.send_message(chat_id=target_chat_id, text=reply_text)


async def accept_support_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_message is None:
        return

    if update.effective_user.id not in settings.admin_telegram_ids:
        return

    async with AsyncSessionFactory() as session:
        open_ticket = await session.scalar(
            select(SupportTicket).where(SupportTicket.status == SupportTicketStatus.OPEN)
        )
        if open_ticket is None:
            await update.effective_message.reply_text("Нет активных тикетов поддержки.")
            return

        open_ticket.assigned_admin_id = update.effective_user.id
        await session.commit()

    await update.effective_message.reply_text(
        "Вы приняли запрос поддержки. Клиенту будет направлено уведомление о подключении.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Закрыть диалог", callback_data="close_support")]]),
    )


async def handle_support_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    if query.data == "accept_support":
        if query.from_user.id not in settings.admin_telegram_ids:
            await query.answer("У вас нет доступа к поддержке.")
            return

        async with AsyncSessionFactory() as session:
            ticket = await session.scalar(
                select(SupportTicket).where(SupportTicket.user_telegram_id == query.from_user.id)
            )
            if ticket is None:
                await query.answer("Тикет не найден.")
                return

            ticket.assigned_admin_id = query.from_user.id
            ticket.status = SupportTicketStatus.OPEN
            await session.commit()

        await query.edit_message_text("Вы приняли вызов. Теперь клиент может писать вам сообщения.")
        return

    if query.data == "close_support":
        async with AsyncSessionFactory() as session:
            ticket = await session.scalar(
                select(SupportTicket).where(SupportTicket.user_telegram_id == query.from_user.id)
            )
            if ticket is not None:
                ticket.assigned_admin_id = None
                ticket.status = SupportTicketStatus.CLOSED
                await session.commit()

        await query.edit_message_text("Диалог закрыт.")
