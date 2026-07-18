from __future__ import annotations

import logging
from typing import Any

from config.settings import Settings

logger = logging.getLogger(__name__)


class GoogleCalendarService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._service: Any | None = None

    async def _get_service(self) -> Any | None:
        if self._service is not None:
            return self._service

        if not self.settings.google_application_credentials:
            logger.warning("Google credentials are not configured; calendar integration is disabled")
            return None

        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as exc:
            logger.exception("Google client libraries are unavailable: %s", exc)
            return None

        try:
            credentials = service_account.Credentials.from_service_account_file(
                self.settings.google_application_credentials,
                scopes=["https://www.googleapis.com/auth/calendar"],
            )
            self._service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
            return self._service
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unable to initialize Google Calendar client: %s", exc)
            return None

    async def list_free_slots(self, calendar_id: str = "primary") -> list[dict[str, Any]]:
        service = await self._get_service()
        if service is None:
            return []

        try:
            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin="2026-01-01T00:00:00Z",
                maxResults=50,
                singleEvents=True,
                orderBy="startTime",
            ).execute()
            return events_result.get("items", [])
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to retrieve Google Calendar slots: %s", exc)
            return []

    async def update_booking_event(self, event_id: str, booking_summary: str, description: str) -> bool:
        service = await self._get_service()
        if service is None:
            return False

        try:
            service.events().patch(
                calendarId=self.settings.google_calendar_id,
                eventId=event_id,
                body={
                    "summary": booking_summary,
                    "description": description,
                },
            ).execute()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to update booking event in Google Calendar: %s", exc)
            return False
