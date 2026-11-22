"""Real availability: owner rules + calendar freebusy. Pure function, no I/O."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.db.models import Owner

LEAD_MIN = 15  # never offer a slot starting sooner than this


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def compute_slots(owner: Owner, busy: list[tuple[datetime, datetime]], date_from: date, date_to: date,
                  duration_min: int, now: datetime, step_min: int = 30) -> list[tuple[datetime, datetime]]:
    """Returns candidate (start,end) in aware UTC, sorted. `now` must be aware."""
    if duration_min > owner.max_meeting_min or duration_min < 5:
        raise ValueError(f"duration must be 5-{owner.max_meeting_min} minutes")
    tz = ZoneInfo(owner.timezone)
    days = {int(d) for d in owner.work_days.split(",") if d.strip()}
    buf = timedelta(minutes=owner.buffer_min)
    blocked = [(s - buf, e + buf) for s, e in busy]
    open_t = max(_t(owner.work_start), _t(owner.min_start))
    close_t = _t(owner.work_end)
    out, d = [], date_from
    while d <= date_to:
        if d.weekday() in days:
            start = datetime.combine(d, open_t, tzinfo=tz)
            close = datetime.combine(d, close_t, tzinfo=tz)
            while start + timedelta(minutes=duration_min) <= close:
                end = start + timedelta(minutes=duration_min)
                s_utc, e_utc = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
                if s_utc >= now + timedelta(minutes=LEAD_MIN) and not any(s_utc < be and bs < e_utc for bs, be in blocked):
                    out.append((s_utc, e_utc))
                start += timedelta(minutes=step_min)
        d += timedelta(days=1)
    return out


def spread(slots: list[tuple[datetime, datetime]], tz: ZoneInfo, n: int = 5):
    """Round-robin across days so the caller hears variety, not 5 slots on one morning."""
    by_day: dict[date, list] = {}
    for s in slots:
        by_day.setdefault(s[0].astimezone(tz).date(), []).append(s)
    picks, queues = [], list(by_day.values())
    while queues and len(picks) < n:
        for q in list(queues):
            picks.append(q.pop(0))
            if not q:
                queues.remove(q)
            if len(picks) >= n:
                break
    return sorted(picks)
