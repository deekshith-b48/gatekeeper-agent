"""Calendar backends. Only freebusy + insert exist — no read-details/update/delete anywhere."""
import os
from datetime import datetime, timedelta

SCOPES = ["https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/calendar.freebusy"]


class GoogleCalendar:
    def __init__(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = Credentials.from_authorized_user_file("token.json", SCOPES) if os.path.exists("token.json") else None
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists("credentials.json"):
                    raise SystemExit("credentials.json not found. Download your Google OAuth Desktop client JSON into the repo root (see README Setup step 3).")
                creds = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES).run_local_server(port=0)
            with open("token.json", "w") as f:
                f.write(creds.to_json())
        self.svc = build("calendar", "v3", credentials=creds, cache_discovery=False)

    def busy(self, start: datetime, end: datetime):
        r = self.svc.freebusy().query(body={"timeMin": start.isoformat(), "timeMax": end.isoformat(),
                                            "items": [{"id": "primary"}]}).execute()
        return [(datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
                 datetime.fromisoformat(b["end"].replace("Z", "+00:00")))
                for b in r["calendars"]["primary"]["busy"]]

    def insert(self, title, start, end, email=None):
        body = {"summary": title, "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}}
        if email:
            body["attendees"] = [{"email": email}]
        ev = self.svc.events().insert(calendarId="primary", body=body, sendUpdates="all" if email else "none").execute()
        return ev["id"]


class MockCalendar:
    """In-memory calendar for demos/tests: busy 13:00-14:00 every day."""

    def __init__(self):
        self.events = []

    def busy(self, start, end):
        out, d = [], start.replace(hour=13, minute=0, second=0, microsecond=0)
        while d < end:
            out.append((d, d.replace(hour=14)))
            d += timedelta(days=1)
        return out + [(s, e) for s, e, *_ in self.events]

    def insert(self, title, start, end, email=None):
        self.events.append((start, end, title, email))
        return f"mock-{len(self.events)}"
