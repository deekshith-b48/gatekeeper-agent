"""Real Google Calendar. Runs only when GATEKEEPER_TEST_CALENDAR_ID (a dedicated test calendar) and token.json exist."""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.providers.calendar.google import GoogleCalendarProvider

CAL = os.environ.get("GATEKEEPER_TEST_CALENDAR_ID")
TOKEN = os.environ.get("GOOGLE_TOKEN_PATH", "token.json")
pytestmark = pytest.mark.skipif(not (CAL and os.path.exists(TOKEN)), reason="no test calendar/token configured")


async def test_real_create_freebusy_idempotent_and_cleanup():
    p = GoogleCalendarProvider(os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json"), TOKEN, CAL)
    start = (datetime.now(timezone.utc) + timedelta(days=30)).replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(minutes=30)
    eid = uuid.uuid4().hex
    try:
        assert await p.create_event(eid, "Gatekeeper integration test", start, end) == eid
        assert await p.create_event(eid, "Gatekeeper integration test", start, end) == eid  # retry-safe (409 -> ok)
        busy = await p.freebusy(start - timedelta(hours=1), end + timedelta(hours=1))
        assert any(s <= start and e >= end for s, e in busy)
    finally:
        p._service().events().delete(calendarId=CAL, eventId=eid).execute()  # test-only cleanup; the app has no delete
