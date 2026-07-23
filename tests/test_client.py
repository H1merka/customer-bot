# tests/test_client.py
from __future__ import annotations

import pytest

from config.constants import clear_booking_session
from database.models import MediaTemplate
from handlers.client import get_stage_text


@pytest.mark.asyncio
async def test_get_stage_text_fallback_to_default(db_session):
    """Проверка получения дефолтного текста, если в БД нет записи."""
    text = await get_stage_text("service_selection")
    assert text == "Выберите услугу:"


@pytest.mark.asyncio
async def test_get_stage_text_custom_from_db(db_session):
    """Проверка подтягивания и форматирования кастомного текста этапа из БД."""
    custom_tpl = MediaTemplate(
        key="stage_txt:service_selection", value="Кастомное меню: {service_name}"
    )
    db_session.add(custom_tpl)
    await db_session.commit()

    text = await get_stage_text("service_selection", service_name="Пирсинг ушей")
    assert text == "Кастомное меню: Пирсинг ушей"


def test_clear_booking_session_utility():
    """Тестирование функции полной очистки контекста сессии бронирования."""
    test_user_data = {
        "booking_state": "await_age",
        "client_name": "Иван",
        "medical_answers": {"blood_disease": "Нет"},
        "some_other_bot_key": "not_cleared",
    }
    clear_booking_session(test_user_data)

    assert "booking_state" not in test_user_data
    assert "client_name" not in test_user_data
    assert "medical_answers" not in test_user_data
    assert test_user_data.get("some_other_bot_key") == "not_cleared"
