from app.core.config import Settings
from app.providers.calendar.base import CalendarProvider, CalendarUnavailable, ResilientCalendar

__all__ = ["CalendarProvider", "CalendarUnavailable", "ResilientCalendar", "build_calendar"]


def build_calendar(st: Settings) -> CalendarProvider:
    if st.calendar_provider == "google":
        from app.providers.calendar.google import GoogleCalendarProvider
        inner = GoogleCalendarProvider(st.google_credentials_path, st.google_token_path, st.google_calendar_id)
    else:
        from app.providers.calendar.caldav import CalDAVProvider
        inner = CalDAVProvider(st.caldav_url, st.caldav_user, st.caldav_password, st.caldav_calendar)
    return ResilientCalendar(inner)
