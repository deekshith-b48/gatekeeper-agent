import asyncio
import threading
from datetime import datetime, timezone

from app.providers.calendar.base import CalendarProvider

SCOPES = ["https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/calendar.freebusy"]


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


class GoogleCalendarProvider(CalendarProvider):
    name = "google"

    def __init__(self, credentials_path: str, token_path: str, calendar_id: str = "primary"):
        import os
        if not os.path.exists(token_path):
            raise RuntimeError(f"{token_path} missing. Run `python scripts/google_auth.py` once on a machine with a browser "
                               f"(needs {credentials_path} from Google Cloud).")
        self.token_path, self.calendar_id = token_path, calendar_id
        self._lock = threading.Lock()
        self._creds = self._load()

    def _load(self):
        from google.oauth2.credentials import Credentials
        return Credentials.from_authorized_user_file(self.token_path, SCOPES)

    def _service(self):
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        with self._lock:
            if not self._creds.valid and self._creds.refresh_token:
                self._creds.refresh(Request())
                with open(self.token_path, "w") as f:
                    f.write(self._creds.to_json())
        return build("calendar", "v3", credentials=self._creds, cache_discovery=False)  # httplib2 isn't thread-safe: one per call

    def _freebusy(self, start, end):
        r = self._service().freebusy().query(body={
            "timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": self.calendar_id}]}).execute()
        cal = r["calendars"][self.calendar_id if self.calendar_id in r["calendars"] else next(iter(r["calendars"]))]
        if cal.get("errors"):
            raise RuntimeError(f"freebusy error: {cal['errors']}")
        return [(_utc(b["start"]), _utc(b["end"])) for b in cal["busy"]]

    def _create(self, event_id, title, start, end, email, description):
        from googleapiclient.errors import HttpError
        body = {"id": event_id, "summary": title, "description": description,
                "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}}
        if email:
            body["attendees"] = [{"email": email}]
        try:
            ev = self._service().events().insert(calendarId=self.calendar_id, body=body,
                                                 sendUpdates="all" if email else "none").execute()
            return ev["id"]
        except HttpError as e:
            if e.resp.status == 409:  # same id already inserted (retry after timeout) -> success
                return event_id
            raise

    async def freebusy(self, start, end):
        return await asyncio.to_thread(self._freebusy, start, end)

    async def create_event(self, event_id, title, start, end, attendee_email=None, description=""):
        return await asyncio.to_thread(self._create, event_id, title, start, end, attendee_email, description)
