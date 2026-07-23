# tests/conftest.py
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Гарантируем, что корневая директория находится в пути поиска модулей
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from database.models import Base


@pytest.fixture(scope="session")
def event_loop():
    """Создает сессионный event loop для выполнения асинхронных тестов."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
def mock_settings():
    """Подменяет глобальные настройки приложения тестовыми значениями."""
    with patch("config.settings.get_settings") as mock_get:
        mock_s = Settings(
            bot_token="123456:ABC-def123456",
            database_url="sqlite+aiosqlite:///:memory:",
            google_calendar_id="test_calendar_id",
            google_application_credentials=None,
            admin_telegram_ids=(123456789,),
            log_level="DEBUG",
        )
        mock_get.return_value = mock_s
        yield mock_s


@pytest.fixture(scope="function")
async def db_session():
    """
    Создает изолированную асинхронную базу данных SQLite в памяти для каждого теста.
    Переопределяет AsyncSessionFactory во всем приложении для изоляции тестов от PostgreSQL.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    # Создаем таблицы на основе Declarative Base моделей
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionFactory = async_sessionmaker(bind=engine, expire_on_commit=False)

    # Патчим фабрику сессий в модуле подключения
    with patch("database.connection.AsyncSessionFactory", SessionFactory):
        async with SessionFactory() as session:
            yield session

    await engine.dispose()


@pytest.fixture
def mock_update():
    """Формирует мок-объект входящего обновления Telegram Update."""
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = 123456789
    update.effective_user.username = "test_user"
    update.effective_user.full_name = "Test FullName"

    update.effective_message = MagicMock()
    update.effective_message.text = "Hello Bot"
    update.effective_message.direct_messages_topic = None
    update.effective_message.message_thread_id = None

    update.effective_chat = MagicMock()
    update.effective_chat.id = 987654321
    update.effective_chat.type = "private"
    update.effective_chat.is_direct_messages = False

    update.callback_query = None
    return update


@pytest.fixture
def mock_context():
    """Формирует тестовый контекст CallbackContext."""
    context = MagicMock()
    context.user_data = {}
    context.args = []
    context.bot = AsyncMock()
    return context


@pytest.fixture
def mock_calendar_service():
    """Изолирует тесты от вызовов внешнего Google Calendar API."""
    with patch("handlers.client.GoogleCalendarService") as mock_cls:
        mock_inst = MagicMock()
        mock_inst.get_busy_intervals = AsyncMock(return_value=[])
        mock_inst.create_booking_event = AsyncMock(return_value="test_g_event_123")
        mock_inst.delete_booking_event = AsyncMock(return_value=True)
        mock_cls.return_value = mock_inst
        yield mock_inst
