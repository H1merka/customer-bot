# handlers/chat_bridge.py
from __future__ import annotations

import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ApplicationHandlerStop

from config.settings import get_settings
from database.connection import AsyncSessionFactory
from database.models import SupportTicket, SupportTicketStatus, User, UserRole
from sqlalchemy import select
from handlers.common import build_main_menu

logger = logging.getLogger(__name__)
settings = get_settings()


def get_client_id_from_update(update: Update) -> int | None:
    if update.effective_message:
        if update.effective_message.direct_messages_topic:
            return update.effective_message.direct_messages_topic.user.id
    if update.effective_user:
        return update.effective_user.id
    return None


async def restrict_to_channel_dms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_user is None:
        return

    from handlers.admin import check_if_admin

    if await check_if_admin(update.effective_user.id):
        return

    if update.effective_chat.type == "private":
        if update.callback_query:
            await update.callback_query.answer("Запись происходит только через канал.", show_alert=True)
            await update.callback_query.edit_message_text(
                "Запись и связь с пирсером доступны только через сообщения нашего канала:\nhttps://t.me/folpierce_ekb"
            )
        else:
            await update.effective_chat.send_message(
                "Извините, запись на сеанс и общение с пирсером происходят только через сообщения нашего канала.\n\n"
                "Пожалуйста, перейдите в канал и нажмите кнопку «Сообщение» для связи с нами:\n"
                "https://t.me/folpierce_ekb"
            )
        raise ApplicationHandlerStop()

    if not getattr(update.effective_chat, "is_direct_messages", False):
        raise ApplicationHandlerStop()


async def handle_silent_mode_and_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_user is None:
        return

    if not getattr(update.effective_chat, "is_direct_messages", False):
        return

    client_id = get_client_id_from_update(update)
    if client_id is None:
        return

    async with AsyncSessionFactory() as session:
        ticket = await session.scalar(
            select(SupportTicket).where(
                SupportTicket.user_telegram_id == client_id,
                SupportTicket.status == SupportTicketStatus.OPEN
            )
        )

    if ticket is not None:
        from handlers.admin import check_if_admin
        is_admin_user = await check_if_admin(update.effective_user.id)
        
        is_close_command = False
        if update.effective_message and update.effective_message.text:
            text = update.effective_message.text.strip().lower()
            if text in ["/close", "/close_support"]:
                is_close_command = True

        is_close_callback = False
        if update.callback_query and update.callback_query.data == "close_support":
            is_close_callback = True

        if is_admin_user and (is_close_command or is_close_callback):
            return

        if update.callback_query:
            await update.callback_query.answer("Диалог с поддержкой активен.")
        raise ApplicationHandlerStop()


async def create_support_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_message is None or update.effective_chat is None:
        return

    client_id = update.effective_user.id
    client_name = update.effective_user.full_name or "Unknown"

    async with AsyncSessionFactory() as session:
        existing = await session.scalar(
            select(SupportTicket).where(SupportTicket.user_telegram_id == client_id)
        )
        if existing is None:
            session.add(
                SupportTicket(
                    user_telegram_id=client_id,
                    status=SupportTicketStatus.OPEN,
                )
            )
        else:
            existing.status = SupportTicketStatus.OPEN
            existing.assigned_admin_id = None
        
        # Получаем список динамических администраторов из БД и объединяем с .env
        db_admins = await session.scalars(select(User.telegram_id).where(User.role == UserRole.ADMIN))
        all_admins = set(settings.admin_telegram_ids) | set(db_admins)
        
        await session.commit()

    chat_id_str = str(update.effective_chat.id)
    if chat_id_str.startswith("-100"):
        chat_id_clean = chat_id_str[4:]
    else:
        chat_id_clean = chat_id_str

    thread_id = update.effective_message.message_thread_id
    if thread_id:
        topic_link = f"https://t.me/c/{chat_id_clean}/{thread_id}"
    else:
        topic_link = "не удалось сгенерировать ссылку"

    for admin_id in all_admins:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    f"⚠️ **Новый запрос поддержки!**\n\n"
                    f"Клиент: {client_name} (ID: {client_id})\n"
                    f"Тема в сообщениях канала: [Перейти к обсуждению]({topic_link})"
                ),
                parse_mode="Markdown"
            )
        except Exception as exc:
            logger.warning("Не удалось отправить оповещение админу %s: %s", admin_id, exc)

    await update.effective_message.reply_text(
        "Тикет поддержки открыт. Пирсер подключится к диалогу в ближайшее время.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Закрыть диалог", callback_data="close_support")]]),
    )


async def relay_message_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pass


async def accept_support_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pass


async def close_support_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_user is None or update.effective_message is None:
        return

    from handlers.admin import check_if_admin

    if not await check_if_admin(update.effective_user.id):
        return

    client_id = get_client_id_from_update(update)
    if client_id is None:
        return

    async with AsyncSessionFactory() as session:
        ticket = await session.scalar(
            select(SupportTicket).where(
                SupportTicket.user_telegram_id == client_id,
                SupportTicket.status == SupportTicketStatus.OPEN
            )
        )
        if ticket is None:
            await update.effective_message.reply_text("Нет активного обращения для этого чата.")
            return

        ticket.status = SupportTicketStatus.CLOSED
        ticket.assigned_admin_id = None
        await session.commit()

    await update.effective_message.reply_text(
        "Диалог со специалистом поддержки завершен.",
        reply_markup=build_main_menu()
    )


async def handle_support_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    if query.data == "close_support":
        client_id = get_client_id_from_update(update)
        if client_id is None:
            return

        async with AsyncSessionFactory() as session:
            ticket = await session.scalar(
                select(SupportTicket).where(SupportTicket.user_telegram_id == client_id)
            )
            if ticket is not None:
                ticket.assigned_admin_id = None
                ticket.status = SupportTicketStatus.CLOSED
                await session.commit()

        await query.edit_message_text("Диалог закрыт.")
        if update.effective_chat:
            await update.effective_chat.send_message(
                "Главное меню",
                reply_markup=build_main_menu()
            )
