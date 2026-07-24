# database/models.py
from __future__ import annotations

from datetime import date, datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UserRole(str, PyEnum):
    USER = "user"
    ADMIN = "admin"


class BookingStatus(str, PyEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class SupportTicketStatus(str, PyEnum):
    OPEN = "open"
    CLOSED = "closed"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(default=UserRole.USER)
    has_healing_access: Mapped[bool] = mapped_column(Boolean, default=False)
    # Новое поле для фиксации предоплаты за текущий сеанс
    is_prepaid: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    bookings: Mapped[list[Booking]] = relationship(back_populates="user")


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_id"))
    client_name: Mapped[str] = mapped_column(String(255))
    client_phone: Mapped[str] = mapped_column(String(255))
    client_age: Mapped[int] = mapped_column(Integer)
    has_parent_consent: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    parent_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parent_phone: Mapped[str | None] = mapped_column(String(255), nullable=True)
    service_name: Mapped[str] = mapped_column(String(255))
    blood_disease: Mapped[str | None] = mapped_column(Text, nullable=True)
    blood_clotting: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_medication: Mapped[str | None] = mapped_column(Text, nullable=True)
    chronic_disease: Mapped[str | None] = mapped_column(Text, nullable=True)
    healing_issues: Mapped[str | None] = mapped_column(Text, nullable=True)
    skin_disease: Mapped[str | None] = mapped_column(Text, nullable=True)
    piercing_zone: Mapped[str | None] = mapped_column(String(255), nullable=True)
    piercing_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    custom_piercing_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_manual_review: Mapped[bool] = mapped_column(Boolean, default=False)
    date_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    google_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[BookingStatus] = mapped_column(default=BookingStatus.PENDING)

    # Новые поля для таргетированной отправки сообщений в топик канала (Channel DM)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    direct_messages_topic_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    user: Mapped[User] = relationship(back_populates="bookings")


class TemporaryLock(Base):
    __tablename__ = "temporary_locks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slot_time: Mapped[datetime] = mapped_column(DateTime, unique=True)
    locked_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    assigned_admin_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[SupportTicketStatus] = mapped_column(
        default=SupportTicketStatus.OPEN
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MediaTemplate(Base):
    __tablename__ = "media_templates"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    telegram_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class StudioSetting(Base):
    __tablename__ = "studio_settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class DayOff(Base):
    __tablename__ = "days_off"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    google_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Broadcast(Base):
    """Модель планирования и сохранения рассылок пользователям (Persistence)."""

    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime, index=True, nullable=False
    )  # UTC datetime
    sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    admin_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
