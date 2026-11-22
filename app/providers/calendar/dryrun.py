"""Red-team lab only: REAL freebusy reads, but writes are recorded instead of sent, so judges can attack the live agent safely."""
from app.providers.calendar.base import CalendarProvider


class DryRunCalendar(CalendarProvider):
    def __init__(self, inner: CalendarProvider):
        self.inner, self.name, self.writes = inner, f"{inner.name} (reads real, writes dry-run)", []

    async def freebusy(self, start, end):
        return await self.inner.freebusy(start, end)

    async def create_event(self, event_id, title, start, end, attendee_email=None, description=""):
        self.writes.append({"title": title, "start": start.isoformat(), "end": end.isoformat(), "invite_to": attendee_email})
        return event_id
