# services/google_calendar.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from config.settings import Settings

logger = logging.getLogger(__name__)


class GoogleCalendarService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._service: Any | None = None

    def _get_authorized_http(self) -> Any | None:
        """
        Создает изолированный HTTP-клиент для текущего вызова во избежание гонки данных
        и возникновения ошибок типа [SSL: UNEXPECTED_EOF_WHILE_READING].
        """
        if not self.settings.google_application_credentials:
            logger.warning("Google application credentials are not configured.")
            return None
        try:
            import google_auth_httplib2
            import httplib2
            from google.oauth2 import service_account

            credentials = service_account.Credentials.from_service_account_file(
                self.settings.google_application_credentials,
                scopes=["https://www.googleapis.com/auth/calendar"],
            )
            # Новый Http-клиент создается под каждый сетевой запрос
            http_client = httplib2.Http(timeout=20)
            return google_auth_httplib2.AuthorizedHttp(credentials, http=http_client)
        except Exception as exc:
            logger.exception("Failed to create authorized HTTP client: %s", exc)
            return None

    async def _get_service(self) -> Any | None:
        if self._service is not None:
            return self._service

        if not self.settings.google_application_credentials:
            logger.warning(
                "Google credentials are not configured; calendar integration is disabled"
            )
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
            self._service = build(
                "calendar", "v3", credentials=credentials, cache_discovery=False
            )
            return self._service
        except Exception as exc:
            logger.exception("Unable to initialize Google Calendar client: %s", exc)
            return None

    async def list_free_slots(
        self, calendar_id: str = "primary"
    ) -> list[dict[str, Any]]:
        service = await self._get_service()
        http_auth = self._get_authorized_http()
        if service is None or http_auth is None:
            return []

        try:
            events_result = (
                service.events()
                .list(
                    calendarId=calendar_id,
                    timeMin="2026-01-01T00:00:00Z",
                    maxResults=50,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute(http=http_auth)
            )
            return events_result.get("items", [])
        except Exception as exc:
            logger.exception("Failed to retrieve Google Calendar slots: %s", exc)
            return []

    async def update_booking_event(
        self, event_id: str, booking_summary: str, description: str
    ) -> bool:
        service = await self._get_service()
        http_auth = self._get_authorized_http()
        if service is None or http_auth is None:
            return False

        try:
            service.events().patch(
                calendarId=self.settings.google_calendar_id,
                eventId=event_id,
                body={
                    "summary": booking_summary,
                    "description": description,
                },
            ).execute(http=http_auth)
            return True
        except Exception as exc:
            logger.exception(
                "Failed to update booking event in Google Calendar: %s", exc
            )
            return False

    async def get_busy_intervals(
        self, date_dt: datetime
    ) -> list[tuple[datetime, datetime]]:
        """
        Возвращает занятые интервалы времени в часовом поясе Екатеринбурга (UTC+5).
        """
        service = await self._get_service()
        http_auth = self._get_authorized_http()
        if service is None or http_auth is None:
            return []

        # Ограничиваем диапазон поиска сутками
        local_tz = timezone(timedelta(hours=5))
        local_start = datetime(
            date_dt.year, date_dt.month, date_dt.day, 0, 0, 0, tzinfo=local_tz
        )
        local_end = datetime(
            date_dt.year, date_dt.month, date_dt.day, 23, 59, 59, tzinfo=local_tz
        )

        time_min = local_start.isoformat()
        time_max = local_end.isoformat()

        try:
            events_result = (
                service.events()
                .list(
                    calendarId=self.settings.google_calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute(http=http_auth)
            )

            events = events_result.get("items", [])
            busy_intervals = []
            for event in events:
                start_raw = event.get("start", {}).get("dateTime") or event.get(
                    "start", {}
                ).get("date")
                end_raw = event.get("end", {}).get("dateTime") or event.get(
                    "end", {}
                ).get("date")
                if not start_raw or not end_raw:
                    continue

                start_dt = datetime.fromisoformat(start_raw)
                end_dt = datetime.fromisoformat(end_raw)
                busy_intervals.append((start_dt, end_dt))
            return busy_intervals
        except Exception as exc:
            logger.exception(
                "Failed to retrieve Google Calendar busy intervals: %s", exc
            )
            return []

    async def create_booking_event(
        self,
        booking_summary: str,
        description: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> str | None:
        """
        Создает новое событие в Google Calendar и возвращает его eventId.
        """
        service = await self._get_service()
        http_auth = self._get_authorized_http()
        if service is None or http_auth is None:
            return None

        try:
            event_body = {
                "summary": booking_summary,
                "description": description,
                "start": {
                    "dateTime": start_dt.isoformat(),
                    "timeZone": "Asia/Yekaterinburg",
                },
                "end": {
                    "dateTime": end_dt.isoformat(),
                    "timeZone": "Asia/Yekaterinburg",
                },
            }
            created_event = (
                service.events()
                .insert(
                    calendarId=self.settings.google_calendar_id,
                    body=event_body,
                )
                .execute(http=http_auth)
            )
            return created_event.get("id")
        except Exception as exc:
            logger.exception(
                "Failed to create booking event in Google Calendar: %s", exc
            )
            return None

    async def delete_booking_event(self, event_id: str) -> bool:
        """
        Удаляет событие из Google Calendar по его eventId.
        """
        service = await self._get_service()
        http_auth = self._get_authorized_http()
        if service is None or http_auth is None:
            return False

        try:
            service.events().delete(
                calendarId=self.settings.google_calendar_id,
                eventId=event_id,
            ).execute(http=http_auth)
            return True
        except Exception as exc:
            logger.exception(
                "Failed to delete booking event in Google Calendar: %s", exc
            )
            return False
