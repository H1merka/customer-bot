# main.py
from __future__ import annotations

import logging
import os

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram.request import HTTPXRequest

from config.settings import get_settings
from database.connection import init_db
from handlers.admin import admin_handlers, grant_healing_access, settings_command
from handlers.chat_bridge import (
    close_support_ticket,
    create_support_ticket,
    handle_silent_mode_and_commands,
    handle_support_callback,
    restrict_to_channel_dms,
)
from handlers.client import client_handlers
from handlers.common import handle_channel_start, help_command, start_command
from scheduler.jobs import send_24h_reminders  # Импорт фоновой задачи

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Глобальный обработчик исключений для предотвращения аварийного завершения работы."""
    logger.error(
        "Исключение при обработке обновления %s:", update, exc_info=context.error
    )


async def post_init(application: Application) -> None:
    try:
        await init_db()
        logger.info("Database initialized successfully inside post_init")
    except Exception as exc:
        logger.exception("Database initialization failed: %s", exc)
        raise


def build_application() -> Application:
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")

    request_config = HTTPXRequest(
        connection_pool_size=20,
        connect_timeout=20.0,
        read_timeout=20.0,
        write_timeout=20.0,
        pool_timeout=10.0,
    )

    application = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .request(request_config)
        .post_init(post_init)
        .build()
    )

    # Регистрация глобального обработчика ошибок
    application.add_error_handler(error_handler)

    # Фильтры разграничения доступов и тихого режима
    application.add_handler(TypeHandler(Update, restrict_to_channel_dms), group=-2)
    application.add_handler(
        TypeHandler(Update, handle_silent_mode_and_commands), group=-1
    )

    # Обработка нажатий на статические Reply-кнопки "Старт" и "В начало" (Group 0)
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex("^(Старт|В начало)$"), start_command
        ),
        group=0,
    )

    # Административные и базовые команды (Group 0)
    application.add_handler(CommandHandler("start", start_command), group=0)
    application.add_handler(CommandHandler("help", help_command), group=0)
    application.add_handler(CommandHandler("settings", settings_command), group=0)
    application.add_handler(
        CommandHandler("grant_access", grant_healing_access), group=0
    )
    application.add_handler(CommandHandler("support", create_support_ticket), group=0)

    # Команды закрытия тикета поддержки администратором (Group 0)
    application.add_handler(CommandHandler("close", close_support_ticket), group=0)
    application.add_handler(
        CommandHandler("close_support", close_support_ticket), group=0
    )

    # Callback-обработчики поддержки (Group 0)
    application.add_handler(
        CallbackQueryHandler(
            handle_support_callback, pattern=r"^(accept_support|close_support)$"
        ),
        group=0,
    )
    application.add_handler(
        CallbackQueryHandler(handle_channel_start, pattern=r"^channel$"), group=0
    )

    # Регистрация сценария администратора в группе приоритета 1 (Group 1)
    for handler in admin_handlers:
        application.add_handler(handler, group=1)

    # Регистрация сценария клиента в группе приоритета 2 (Group 2)
    for handler in client_handlers:
        application.add_handler(handler, group=2)

    # Регистрация циклического фонового задания отправки напоминаний за 24 часа в планировщике
    if application.job_queue:
        # Проверяем записи каждые 5 минут (300 сек). Первый запуск через 10 секунд после старта.
        application.job_queue.run_repeating(send_24h_reminders, interval=300, first=10)
        logger.info(
            "Планировщик напоминаний (send_24h_reminders) успешно зарегистрирован в JobQueue"
        )
    else:
        logger.warning(
            "JobQueue недоступен. Напоминания работать не будут. Убедитесь в наличии библиотеки python-telegram-bot[job-queue]"
        )

    return application


def main() -> None:
    try:
        application = build_application()
        application.run_polling(
            allowed_updates=["message", "callback_query"], bootstrap_retries=5
        )
        logger.info("Bot started")
    except Exception as exc:
        logger.exception("Bot startup failed: %s", exc)
        raise


if __name__ == "__main__":
    main()
