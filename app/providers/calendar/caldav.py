import asyncio
from datetime import date, datetime, timedelta, timezone

from app.providers.calendar.base import CalendarProvider


def _as_utc(v) -> datetime:
    if isinstance(v, datetime):
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)
    if isinstance(v, date):  # all-day event
        return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
    raise TypeError(type(v))


class CalDAVProvider(CalendarProvider):
    """Any CalDAV server (Radicale, Nextcloud, iCloud...)."""
    name = "caldav"

    def __init__(self, url: str, user: str, password: str, calendar_name: str = ""):
        import caldav
        self._client = caldav.DAVClient(url=url, username=user, password=password)
        principal = self._client.principal()
        cals = principal.calendars()
        if not cals:
            cals = [principal.make_calendar(name=calendar_name or "gatekeeper")]
        self._cal = next((c for c in cals if calendar_name and c.name == calendar_name), cals[0])

    def _freebusy(self, start, end):
        busy = []
        for ev in self._cal.date_search(start=start, end=end, expand=True):
            for comp in ev.icalendar_instance.walk("VEVENT"):
                if str(comp.get("TRANSP", "OPAQUE")).upper() == "TRANSPARENT":
                    continue
                s = _as_utc(comp.decoded("dtstart"))
                e = _as_utc(comp.decoded("dtend")) if comp.get("dtend") else s + comp.decoded("duration", timedelta(hours=1))
                busy.append((s, e))
        return busy

    def _create(self, event_id, title, start, end, email, description):
        from icalendar import Calendar, Event, vCalAddress
        cal, ev = Calendar(), Event()
        cal.add("prodid", "-//Gatekeeper//EN")
        cal.add("version", "2.0")
        ev.add("uid", event_id)  # deterministic UID: PUT is idempotent
        ev.add("summary", title)
        ev.add("description", description)
        ev.add("dtstart", start.astimezone(timezone.utc))
        ev.add("dtend", end.astimezone(timezone.utc))
        ev.add("dtstamp", datetime.now(timezone.utc))
        if email:
            ev.add("attendee", vCalAddress(f"mailto:{email}"))
        cal.add_component(ev)
        self._cal.save_event(cal.to_ical().decode())
        return event_id

    async def freebusy(self, start, end):
        return await asyncio.to_thread(self._freebusy, start, end)

    async def create_event(self, event_id, title, start, end, attendee_email=None, description=""):
        return await asyncio.to_thread(self._create, event_id, title, start, end, attendee_email, description)
