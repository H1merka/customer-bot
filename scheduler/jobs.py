from __future__ import annotations

import logging
from datetime import datetime, timedelta

from telegram.ext import Application

from config.settings import get_settings
from database.connection import AsyncSessionFactory
from database.models import Booking, BookingStatus
from sqlalchemy import select

logger = logging.getLogger(__name__)
settings = get_settings()


async def send_24h_reminders(app: Application) -> None:
    async with AsyncSessionFactory() as session:
        now = datetime.utcnow()
        window_start = now + timedelta(hours=23, minutes=50)
        window_end = now + timedelta(hours=24, minutes=10)

        bookings = await session.scalars(
            select(Booking)
            .where(Booking.status == BookingStatus.CONFIRMED)
            .where(Booking.reminder_sent.is_(False))
            .where(Booking.date_time >= window_start)
            .where(Booking.date_time <= window_end)
        )

        for booking in bookings:
            try:
                await app.bot.send_message(
                    chat_id=booking.user_id,
                    text=(
                        f"Напоминание: ваша запись на {booking.date_time.strftime('%d.%m.%Y %H:%M')}"
                        f" ожидается. Пожалуйста, arrive заранее."
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to send reminder for booking %s: %s", booking.id, exc)

            booking.reminder_sent = True

        await session.commit()
