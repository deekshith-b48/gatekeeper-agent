import logging
from abc import ABC, abstractmethod
from datetime import datetime

from app.core.resilience import CircuitBreaker, call_with_retry

log = logging.getLogger("calendar")


class CalendarUnavailable(Exception):
    """Calendar could not be reached after retries (or its circuit is open)."""


class CalendarProvider(ABC):
    name: str

    @abstractmethod
    async def freebusy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        """Busy intervals (tz-aware UTC) in [start, end). Never returns event details."""

    @abstractmethod
    async def create_event(self, event_id: str, title: str, start: datetime, end: datetime,
                           attendee_email: str | None = None, description: str = "") -> str:
        """Idempotent on event_id (a retry after a timeout must not double-book)."""


class ResilientCalendar(CalendarProvider):
    """Timeout + retry(2, exponential) + circuit breaker around any provider."""

    def __init__(self, inner: CalendarProvider, timeout: float = 10.0):
        self.inner, self.timeout, self.name = inner, timeout, inner.name
        self.breaker = CircuitBreaker(threshold=3, reset_s=30)

    async def _run(self, fn):
        try:
            return await call_with_retry(fn, timeout=self.timeout, retries=2, breaker=self.breaker)
        except Exception as e:  # noqa: BLE001
            log.error("calendar %s unavailable: %r", self.name, e)
            raise CalendarUnavailable(str(e)) from e

    async def freebusy(self, start, end):
        return await self._run(lambda: self.inner.freebusy(start, end))

    async def create_event(self, event_id, title, start, end, attendee_email=None, description=""):
        return await self._run(lambda: self.inner.create_event(event_id, title, start, end, attendee_email, description))
