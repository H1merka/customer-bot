# jobs.py
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import select
from telegram.ext import Application, ContextTypes

from config.settings import get_settings
from database.connection import AsyncSessionFactory
from database.models import Booking, BookingStatus, Broadcast, User, UserRole

logger = logging.getLogger(__name__)
settings = get_settings()


async def send_24h_reminders(app: Application) -> None:
    async with AsyncSessionFactory() as session:
        local_tz = timezone(timedelta(hours=5))
        now_local = datetime.now(local_tz).replace(tzinfo=None)

        # Поиск записей в временном окне: 24 часа назад с дельтой ±10 минут
        window_start = now_local + timedelta(hours=23, minutes=50)
        window_end = now_local + timedelta(hours=24, minutes=10)

        bookings = await session.scalars(
            select(Booking)
            .where(Booking.status == BookingStatus.CONFIRMED)
            .where(Booking.reminder_sent.is_(False))
            .where(Booking.date_time >= window_start)
            .where(Booking.date_time <= window_end)
        )

        for booking in bookings:
            try:
                # Если у записи сохранены чат и топик из Сообщений канала, отправляем строго в них
                if booking.chat_id and booking.direct_messages_topic_id:
                    await app.bot.send_message(
                        chat_id=booking.chat_id,
                        direct_messages_topic_id=booking.direct_messages_topic_id,
                        text=(
                            f"Напоминание: ваша запись на {booking.date_time.strftime('%d.%m.%Y %H:%M')}"
                            f" ожидается. Пожалуйста, приходите заранее."
                        ),
                    )
                    logger.info(
                        "Отправлено напоминание в топик Сообщений канала для записи ID %s",
                        booking.id,
                    )
                else:
                    # Резервный вариант: пробуем отправить в личные сообщения пользователю напрямую
                    await app.bot.send_message(
                        chat_id=booking.user_id,
                        text=(
                            f"Напоминание: ваша запись на {booking.date_time.strftime('%d.%m.%Y %H:%M')}"
                            f" ожидается. Пожалуйста, приходите заранее."
                        ),
                    )
                    logger.info(
                        "Отправлено напоминание в ЛС для записи ID %s (топик в Сообщениях канала не найден)",
                        booking.id,
                    )
            except Exception as exc:
                logger.exception(
                    "Failed to send reminder for booking %s: %s", booking.id, exc
                )

            booking.reminder_sent = True

        await session.commit()


async def run_broadcast_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Асинхронная фоновая отправка рассылки зарегистрированным клиентам."""
    job = context.job
    if job is None or job.data is None:
        return

    broadcast_id = job.data.get("broadcast_id")
    if not broadcast_id:
        return

    async with AsyncSessionFactory() as session:
        broadcast = await session.get(Broadcast, broadcast_id)
        if not broadcast or broadcast.sent:
            return

        broadcast_text = broadcast.text
        admin_id = broadcast.admin_id

        # Получаем всех пользователей за исключением администраторов
        stmt_users = select(User.telegram_id).where(User.role != UserRole.ADMIN)
        user_ids = list(await session.scalars(stmt_users))

        # Помечаем рассылку как отправленную
        broadcast.sent = True
        await session.commit()

    success_count = 0
    fail_count = 0

    for user_id in user_ids:
        try:
            # Отправка сообщений в неформатированном plain-тексте (parse_mode=None)
            await context.bot.send_message(
                chat_id=user_id, text=broadcast_text, parse_mode=None
            )
            success_count += 1
        except Exception as exc:
            logger.warning("Failed to send broadcast to user %s: %s", user_id, exc)
            fail_count += 1

    logger.info(
        "Broadcast %s processing complete. Sent: %d, Failed: %d",
        broadcast_id,
        success_count,
        fail_count,
    )

    if admin_id:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    f"📢 <b>Рассылка завершена!</b>\n\n"
                    f"Успешно доставлено: {success_count}\n"
                    f"Не доставлено: {fail_count}"
                ),
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning(
                "Failed to notify admin %s about broadcast job completion: %s",
                admin_id,
                exc,
            )


async def restore_broadcast_jobs(app: Application) -> None:
    """Восстанавливает ранее запланированные рассылки из PostgreSQL при перезапуске (Persistence)."""
    if not app.job_queue:
        logger.warning("JobQueue is unavailable. Cannot restore broadcasts.")
        return

    now_utc = datetime.now(UTC)

    async with AsyncSessionFactory() as session:
        stmt = select(Broadcast).where(Broadcast.sent.is_(False))
        broadcasts = list(await session.scalars(stmt))

        for b in broadcasts:
            # Преобразуем наивное UTC время из базы данных в aware datetime
            scheduled_utc = b.scheduled_at.replace(tzinfo=UTC)

            if scheduled_utc <= now_utc:
                # Если время рассылки уже прошло, пока бот был выключен — отправляем немедленно
                delay = 0.0
            else:
                delay = (scheduled_utc - now_utc).total_seconds()

            app.job_queue.run_once(
                run_broadcast_job,
                when=delay,
                data={"broadcast_id": b.id},
                name=f"broadcast_job_{b.id}",
            )
            logger.info(
                "Restored scheduled broadcast ID %s from database. Execution in %s seconds.",
                b.id,
                delay,
            )
