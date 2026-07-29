"""Pluggable calendar/mail providers (Google, Microsoft, Yahoo).

Phase 1: Google Calendar via official OAuth. Mail / other providers next.
"""

from __future__ import annotations

from remedy.assistant.providers.base import (
    CalendarEvent,
    CalendarProvider,
    MailMessage,
    MailProvider,
    ProviderRegistry,
    get_provider_registry,
)
from remedy.assistant.providers.google_calendar import (
    GoogleCalendarProvider,
    get_google_calendar,
)
from remedy.assistant.providers.google_gmail import (
    GoogleGmailProvider,
    get_google_gmail,
)

__all__ = [
    "CalendarEvent",
    "CalendarProvider",
    "GoogleCalendarProvider",
    "GoogleGmailProvider",
    "MailMessage",
    "MailProvider",
    "ProviderRegistry",
    "get_google_calendar",
    "get_google_gmail",
    "get_provider_registry",
]
