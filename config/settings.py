from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE)


@dataclass(slots=True)
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@db:5432/customer_bot",
    )
    google_calendar_id: str = os.getenv("GOOGLE_CALENDAR_ID", "primary")
    google_application_credentials: str | None = os.getenv(
        "GOOGLE_APPLICATION_CREDENTIALS"
    )
    admin_telegram_ids: Tuple[int, ...] = field(
        default_factory=lambda: tuple(
            int(item.strip())
            for item in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
            if item.strip()
        )
    )
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            bot_token=os.getenv("BOT_TOKEN", ""),
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql+asyncpg://postgres:postgres@db:5432/customer_bot",
            ),
            google_calendar_id=os.getenv("GOOGLE_CALENDAR_ID", "primary"),
            google_application_credentials=os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
            admin_telegram_ids=tuple(
                int(item.strip())
                for item in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
                if item.strip()
            ),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )


def get_settings() -> Settings:
    return Settings.from_env()
