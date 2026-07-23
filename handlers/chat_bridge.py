# handlers/chat_bridge.py
from __future__ import annotations

import logging
import html  # Стандартная библиотека для экранирования HTML
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ApplicationHandlerStop

from config.settings import get_settings
from database.connection import AsyncSessionFactory
from database.models import SupportTicket, SupportTicketStatus, User, UserRole
from sqlalchemy import select
from handlers.common import build_main_menu

logger = logging.getLogger(__name__)
settings = get_settings()


async def get_client_id_from_update(update: Update) -> int | None:
    """
    Извлекает ID клиента из контекста обновления.
    Если запрос идет из топика прямого диалога (Channel DM), возвращает ID клиента.
    В случае приватного чата возвращает ID отправителя, если он не является администратором.
    """
    if update.effective_message:
        if update.effective_message.direct_messages_topic:
            return update.effective_message.direct_messages_topic.user.id
    if update.effective_user:
        from handlers.admin import check_if_admin

        # Предотвращаем ложное определение админа как клиента при отправке команд в ЛС бота
        if await check_if_admin(update.effective_user.id):
            return None
        return update.effective_user.id
    return None


async def restrict_to_channel_dms(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_chat is None or update.effective_user is None:
        return

    from handlers.admin import check_if_admin

    if await check_if_admin(update.effective_user.id, context):
        return

    if update.effective_chat.type == "private":
        if update.callback_query:
            await update.callback_query.answer(
                "Запись происходит только через канал.", show_alert=True
            )
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


async def handle_silent_mode_and_commands(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_chat is None or update.effective_user is None:
        return

    if not getattr(update.effective_chat, "is_direct_messages", False):
        return

    client_id = await get_client_id_from_update(update)
    if client_id is None:
        return

    async with AsyncSessionFactory() as session:
        ticket = await session.scalar(
            select(SupportTicket).where(
                SupportTicket.user_telegram_id == client_id,
                SupportTicket.status == SupportTicketStatus.OPEN,
            )
        )

    if ticket is not None:
        # Проверяем, является ли сообщение командой перезапуска или закрытия диалога
        bypass_silent_mode = False

        if update.effective_message and update.effective_message.text:
            text = update.effective_message.text.strip().lower()
            # Разрешаем пользователям использовать команды перезапуска и закрытия диалога
            if text in ["/start", "старт", "в начало", "/close", "/close_support"]:
                bypass_silent_mode = True

        # Разрешаем обработку нажатия кнопки "Закрыть диалог"
        if update.callback_query and update.callback_query.data == "close_support":
            bypass_silent_mode = True

        # Если зафиксировано управляющее действие, пропускаем его к обработчикам группы 0
        if bypass_silent_mode:
            return

        # Для всех остальных сообщений (пока диалог активен) блокируем прохождение
        if update.callback_query:
            await update.callback_query.answer("Диалог с поддержкой активен.")
        raise ApplicationHandlerStop()


async def create_support_ticket(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if (
        update.effective_user is None
        or update.effective_message is None
        or update.effective_chat is None
    ):
        return

    client_id = update.effective_user.id
    # Безопасное экранирование имени во избежание сбоев парсинга разметки
    client_name = html.escape(update.effective_user.full_name or "Unknown")

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
        db_admins = await session.scalars(
            select(User.telegram_id).where(User.role == UserRole.ADMIN)
        )
        all_admins = set(settings.admin_telegram_ids) | set(db_admins)

        await session.commit()

    # Отправка уведомления администраторам (строка со ссылкой на топик полностью удалена)
    for admin_id in all_admins:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    f"⚠️ <b>Новый запрос поддержки!</b>\n\n"
                    f"Клиент: {client_name} (ID: {client_id})"
                ),
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning(
                "Не удалось отправить оповещение админу %s: %s", admin_id, exc
            )

    # Параметры отправки для корректной поддержки Channel Direct Messages
    send_kwargs = {
        "text": "Тикет поддержки открыт. Пирсер подключится к диалогу в ближайшее время.",
        "reply_markup": InlineKeyboardMarkup(
            [[InlineKeyboardButton("Закрыть диалог", callback_data="close_support")]]
        ),
    }

    thread_id = None
    if update.effective_message:
        if update.effective_message.direct_messages_topic:
            thread_id = update.effective_message.direct_messages_topic.topic_id
        elif update.effective_message.message_thread_id:
            thread_id = update.effective_message.message_thread_id

    if getattr(update.effective_chat, "is_direct_messages", False):
        if thread_id:
            send_kwargs["direct_messages_topic_id"] = thread_id
    else:
        if update.effective_message and update.effective_message.message_thread_id:
            send_kwargs["message_thread_id"] = (
                update.effective_message.message_thread_id
            )

    await update.effective_chat.send_message(**send_kwargs)


async def close_support_ticket(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if (
        update.effective_chat is None
        or update.effective_user is None
        or update.effective_message is None
    ):
        return

    from handlers.admin import check_if_admin

    if not await check_if_admin(update.effective_user.id, context):
        return

    client_id = await get_client_id_from_update(update)
    if client_id is None:
        await update.effective_message.reply_text(
            "Не удалось определить клиента. Команда закрытия диалога должна выполняться "
            "строго внутри топика прямого сообщения (Channel DM) с клиентом."
        )
        return

    async with AsyncSessionFactory() as session:
        ticket = await session.scalar(
            select(SupportTicket).where(
                SupportTicket.user_telegram_id == client_id,
                SupportTicket.status == SupportTicketStatus.OPEN,
            )
        )
        if ticket is None:
            await update.effective_message.reply_text(
                "Нет активного обращения для этого чата."
            )
            return

        ticket.status = SupportTicketStatus.CLOSED
        ticket.assigned_admin_id = None
        await session.commit()

    # Проверяем, приостановлен ли сценарий записи для ручного разбора
    client_user_data = (
        context.application.user_data.get(client_id) if context.application else None
    )
    if (
        client_user_data
        and client_user_data.get("booking_state") == "paused_for_medical_review"
    ):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = [
            [InlineKeyboardButton("Продолжить запись", callback_data="resume_booking")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Подтверждение закрытия администратору в служебную тему
        await update.effective_message.reply_text(
            "Диалог со специалистом завершен. Пользователю отправлена кнопка для продолжения бронирования."
        )

        # Вывод кнопки в чат с пользователем (Channel DM)
        thread_id = None
        if update.effective_message:
            if update.effective_message.direct_messages_topic:
                thread_id = update.effective_message.direct_messages_topic.topic_id
            elif update.effective_message.message_thread_id:
                thread_id = update.effective_message.message_thread_id

        send_kwargs = {
            "text": "Диалог со специалистом завершен. Вы можете продолжить бронирование услуги.",
            "reply_markup": reply_markup,
        }
        if thread_id:
            if getattr(update.effective_chat, "is_direct_messages", False):
                send_kwargs["direct_messages_topic_id"] = thread_id
            else:
                send_kwargs["message_thread_id"] = thread_id

        await update.effective_chat.send_message(**send_kwargs)
    else:
        await update.effective_message.reply_text(
            "Диалог со специалистом поддержки завершен.", reply_markup=build_main_menu()
        )


async def handle_support_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None:
        return

    if query.data == "close_support":
        client_id = await get_client_id_from_update(update)
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

        # Проверяем, приостановлен ли сценарий записи для ручного разбора
        client_user_data = (
            context.application.user_data.get(client_id)
            if context.application
            else None
        )
        if (
            client_user_data
            and client_user_data.get("booking_state") == "paused_for_medical_review"
        ):
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            keyboard = [
                [
                    InlineKeyboardButton(
                        "Продолжить запись", callback_data="resume_booking"
                    )
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            send_kwargs = {
                "text": "Диалог со специалистом завершен. Вы можете продолжить бронирование услуги.",
                "reply_markup": reply_markup,
            }
            if update.effective_chat:
                topic_id = None
                if query.message:
                    if query.message.direct_messages_topic:
                        topic_id = query.message.direct_messages_topic.topic_id
                    elif query.message.message_thread_id:
                        topic_id = query.message.message_thread_id
                if topic_id:
                    if getattr(update.effective_chat, "is_direct_messages", False):
                        send_kwargs["direct_messages_topic_id"] = topic_id
                    else:
                        send_kwargs["message_thread_id"] = topic_id
                await update.effective_chat.send_message(**send_kwargs)
        else:
            if update.effective_chat:
                send_kwargs = {
                    "text": "Главное меню",
                    "reply_markup": build_main_menu(),
                }
                topic_id = None
                if query.message:
                    if query.message.direct_messages_topic:
                        topic_id = query.message.direct_messages_topic.topic_id
                    elif query.message.message_thread_id:
                        topic_id = query.message.message_thread_id
                if topic_id:
                    if getattr(update.effective_chat, "is_direct_messages", False):
                        send_kwargs["direct_messages_topic_id"] = topic_id
                    else:
                        send_kwargs["message_thread_id"] = topic_id
                await update.effective_chat.send_message(**send_kwargs)
