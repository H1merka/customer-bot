# jobs.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from telegram.ext import Application

from config.settings import get_settings
from database.connection import AsyncSessionFactory
from database.models import Booking, BookingStatus

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
