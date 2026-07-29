"""Abstract calendar/mail provider interfaces — keep tools provider-agnostic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class CalendarEvent:
    id: str
    title: str
    start: str  # ISO
    end: str = ""
    description: str = ""
    location: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class MailMessage:
    id: str
    subject: str
    from_addr: str = ""
    snippet: str = ""
    date: str = ""
    thread_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class CalendarProvider(Protocol):
    provider_id: str

    def list_events(self, *, time_min: str, time_max: str) -> list[CalendarEvent]: ...
    def create_event(
        self,
        *,
        title: str,
        start: str,
        end: str,
        description: str = "",
    ) -> CalendarEvent: ...


@runtime_checkable
class MailProvider(Protocol):
    provider_id: str

    def list_messages(self, *, query: str = "", limit: int = 20) -> list[MailMessage]: ...
    def get_message(self, message_id: str) -> MailMessage: ...
    def create_draft(
        self, *, to: str, subject: str, body: str
    ) -> dict[str, Any]: ...


class ProviderRegistry:
    """Registry of connected provider adapters (empty until OAuth lands)."""

    def __init__(self) -> None:
        self._calendars: dict[str, CalendarProvider] = {}
        self._mail: dict[str, MailProvider] = {}

    def register_calendar(self, account_id: str, provider: CalendarProvider) -> None:
        self._calendars[account_id] = provider

    def register_mail(self, account_id: str, provider: MailProvider) -> None:
        self._mail[account_id] = provider

    def get_calendar(self, account_id: str = "") -> CalendarProvider | None:
        if account_id and account_id in self._calendars:
            return self._calendars[account_id]
        if len(self._calendars) == 1:
            return next(iter(self._calendars.values()))
        return None

    def get_mail(self, account_id: str = "") -> MailProvider | None:
        if account_id and account_id in self._mail:
            return self._mail[account_id]
        if len(self._mail) == 1:
            return next(iter(self._mail.values()))
        return None

    def status(self) -> dict[str, Any]:
        return {
            "calendar_accounts": list(self._calendars.keys()),
            "mail_accounts": list(self._mail.keys()),
        }


_registry: ProviderRegistry | None = None


def get_provider_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry
