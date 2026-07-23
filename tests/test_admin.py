# tests/test_admin.py
from __future__ import annotations

import pytest
from sqlalchemy import select
from database.models import User, UserRole
from handlers.admin import check_if_admin

@pytest.mark.asyncio
async def test_check_if_admin_static_list(db_session, mock_context):
    """Проверка определения прав админа по статическому списку из настроек."""
    # 123456789 находится в admin_telegram_ids в conftest.py
    is_admin = await check_if_admin(123456789, mock_context)
    assert is_admin is True

@pytest.mark.asyncio
async def test_check_if_admin_context_cache(db_session, mock_context):
    """Проверка извлечения прав админа из in-memory кэша context.user_data."""
    mock_context.user_data["is_admin"] = True
    is_admin = await check_if_admin(99999, mock_context)
    assert is_admin is True

@pytest.mark.asyncio
async def test_check_if_admin_database(db_session, mock_context):
    """Проверка определения прав админа на основе роли пользователя в базе данных."""
    # Создаем пользователя с ролью ADMIN в тестовой БД
    admin_user = User(
        telegram_id=88888,
        username="db_admin",
        full_name="Database Admin",
        role=UserRole.ADMIN
    )
    db_session.add(admin_user)
    await db_session.commit()

    is_admin = await check_if_admin(88888, mock_context)
    assert is_admin is True
    # Убеждаемся, что значение закэшировалось в сессии
    assert mock_context.user_data.get("is_admin") is True

@pytest.mark.asyncio
async def test_check_if_regular_user(db_session, mock_context):
    """Проверка, что обычный пользователь не проходит валидацию на права админа."""
    is_admin = await check_if_admin(11111, mock_context)
    assert is_admin is False
