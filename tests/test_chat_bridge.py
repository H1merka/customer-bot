# tests/test_chat_bridge.py
from __future__ import annotations

import pytest
from handlers.chat_bridge import get_client_id_from_update

@pytest.mark.asyncio
async def test_get_client_id_from_update_admin_returns_none(db_session, mock_update, mock_context):
    """
    Проверяет, что администратор не определяется как клиент при отправке команд в ЛС.
    """
    # mock_update.effective_user.id по умолчанию равен 123456789 (статический админ)
    client_id = await get_client_id_from_update(mock_update)
    assert client_id is None

@pytest.mark.asyncio
async def test_get_client_id_from_update_regular_user(db_session, mock_update, mock_context):
    """
    Проверяет, что для обычного пользователя возвращается его персональный Telegram ID.
    """
    mock_update.effective_user.id = 555555  # Обычный пользователь
    client_id = await get_client_id_from_update(mock_update)
    assert client_id == 555555
