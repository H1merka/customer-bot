from __future__ import annotations

import logging
import os
import sys
from typing import Any

from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler

from config.settings import get_settings
from database.connection import init_db
from handlers.admin import grant_healing_access, revoke_healing_access, set_location, settings_command
from handlers.chat_bridge import accept_support_ticket, create_support_ticket, handle_support_callback
from handlers.client import client_handlers
from handlers.common import help_command, handle_channel_start, start_command

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, log_level, logging.INFO), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def build_application() -> Application:
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")

    application = ApplicationBuilder().token(settings.bot_token).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("grant_access", grant_healing_access))
    application.add_handler(CommandHandler("revoke_access", revoke_healing_access))
    application.add_handler(CommandHandler("set_location", set_location))
    application.add_handler(CommandHandler("support", create_support_ticket))
    application.add_handler(CommandHandler("accept", accept_support_ticket))
    application.add_handler(CallbackQueryHandler(handle_support_callback, pattern=r"^(accept_support|close_support)$"))

    application.add_handler(CallbackQueryHandler(handle_channel_start, pattern=r"^channel$"))
    for handler in client_handlers:
        application.add_handler(handler)

    return application


async def main() -> None:
    try:
        await init_db()
        logger.info("Database initialized successfully")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Database initialization failed: %s", exc)
        raise

    try:
        application = build_application()
        await application.initialize()
        await application.start()
        await application.updater.start_polling(allowed_updates=["message", "callback_query"])
        logger.info("Bot started")

        try:
            await application.updater.idle()
        except KeyboardInterrupt:
            logger.info("Shutting down bot")
            await application.stop()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Bot startup failed: %s", exc)
        raise


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
