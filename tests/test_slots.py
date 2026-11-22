from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.agent.slots import compute_slots, spread
from app.db.models import Owner

IST = ZoneInfo("Asia/Kolkata")
OWNER = Owner(handle="o", name="Sam", timezone="Asia/Kolkata", work_start="09:00", work_end="18:00", min_start="11:00",
              max_meeting_min=30, buffer_min=10, work_days="0,1,2,3,4")
MON, TUE = date(2030, 1, 7), date(2030, 1, 8)
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def local(d, h, m=0):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=IST)


def test_respects_min_start_and_close():
    s = compute_slots(OWNER, [], MON, MON, 30, NOW)
    starts = [a.astimezone(IST) for a, _ in s]
    assert starts[0] == local(MON, 11)
    assert starts[-1] == local(MON, 17, 30)


def test_busy_and_buffer_block_neighbours():
    busy = [(local(MON, 13).astimezone(timezone.utc), local(MON, 14).astimezone(timezone.utc))]
    starts = {a.astimezone(IST).strftime("%H:%M") for a, _ in compute_slots(OWNER, busy, MON, MON, 30, NOW)}
    assert not starts & {"12:30", "13:00", "13:30", "14:00"}  # 10-min buffer kills 12:30-13:00 and 14:00-14:30
    assert {"12:00", "14:30"} <= starts


def test_weekend_and_past_and_lead_time():
    sat = date(2030, 1, 5)
    assert compute_slots(OWNER, [], sat, sat, 30, NOW) == []
    now = local(MON, 11, 10).astimezone(timezone.utc)
    first = compute_slots(OWNER, [], MON, MON, 30, now)[0][0].astimezone(IST)
    assert first >= local(MON, 11, 25)


def test_duration_limits():
    with pytest.raises(ValueError):
        compute_slots(OWNER, [], MON, MON, 60, NOW)
    with pytest.raises(ValueError):
        compute_slots(OWNER, [], MON, MON, 1, NOW)


def test_spread_across_days():
    s = compute_slots(OWNER, [], MON, TUE, 30, NOW)
    days = {a.astimezone(IST).date() for a, _ in spread(s, IST, 4)}
    assert days == {MON, TUE}
