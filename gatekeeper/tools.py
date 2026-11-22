"""The ONLY capabilities a caller session has. No cancel/update/delete/read-details exist here."""
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TIMEOUT = 10
EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
_pool = ThreadPoolExecutor(max_workers=4)

SCHEMAS = [
    {"name": "get_free_slots", "description": "Free 30-min slots inside the owner's working hours. date_range: 'YYYY-MM-DD/YYYY-MM-DD' (max 7 days).",
     "input_schema": {"type": "object", "properties": {"date_range": {"type": "string"}}, "required": ["date_range"]}},
    {"name": "book_meeting", "description": "Book a meeting at a slot returned by get_free_slots. start/end ISO-8601.",
     "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
                                                       "attendee_email": {"type": "string"}}, "required": ["title", "start", "end"]}},
    {"name": "notify_owner", "description": "Send the owner a one-line summary.",
     "input_schema": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}},
    {"name": "remember", "description": "Save a note about the caller. Call once you know their name and reason.",
     "input_schema": {"type": "object", "properties": {"caller": {"type": "string"}, "note": {"type": "string"}}, "required": ["caller", "note"]}},
    {"name": "recall", "description": "Look up what is known about this caller.",
     "input_schema": {"type": "object", "properties": {"caller": {"type": "string"}}, "required": ["caller"]}},
]


class Tools:
    def __init__(self, cal, mem, tz="UTC", notify=print):
        self.cal, self.mem, self.tz, self._notify = cal, mem, ZoneInfo(tz), notify
        self.caller = None  # last caller name given to remember/recall

    # ---- dispatcher: allowlist + timeout + never raises ----
    def call(self, name, args):
        fn = {"get_free_slots": self.get_free_slots, "book_meeting": self.book_meeting, "notify_owner": self.notify_owner,
              "remember": self.remember, "recall": self.recall}.get(name)
        if fn is None:
            return {"error": "not permitted: callers may only request a booking"}
        try:
            if not isinstance(args, dict):
                return {"error": "invalid arguments"}
            return _pool.submit(fn, **args).result(timeout=TIMEOUT)
        except (FTimeout, Exception) as e:
            return self._failure(name, e)

    def _failure(self, name, e):
        who = self.caller or "unknown caller"
        try:
            self.mem.remember(who, f"MESSAGE PENDING: {name} failed ({type(e).__name__})")
            self._notify(f"[Gatekeeper] {name} failed for {who}; they left a message. ({type(e).__name__})")
        except Exception:
            pass
        return {"error": "tool_failed", "say": "I'll take a message and he'll get back to you."}

    # ---- helpers ----
    def _parse(self, s):
        dt = datetime.fromisoformat(str(s).strip())
        return dt.replace(tzinfo=self.tz) if dt.tzinfo is None else dt.astimezone(self.tz)

    def _free(self, start, end, busy):
        return not any(s < end and start < e for s, e in busy)

    def _check_rules(self, start, end):
        r = self.mem.rules
        if start <= datetime.now(self.tz):
            return "that time is in the past"
        if start.weekday() >= 5:
            return "he doesn't take meetings on weekends"
        if end - start > timedelta(minutes=r["max_minutes"]) or end <= start:
            return f"meetings are at most {r['max_minutes']} minutes"
        if start.hour < r["earliest_hour"] or end.date() != start.date() or end > start.replace(hour=r["latest_hour"], minute=0, second=0, microsecond=0):
            return f"meetings only happen between {r['earliest_hour']}:00 and {r['latest_hour']}:00"
        return None

    # ---- the five tools ----
    def get_free_slots(self, date_range):
        try:
            a, _, b = str(date_range).partition("/")
            d0 = datetime.fromisoformat(a.strip()).replace(tzinfo=self.tz)
            d1 = datetime.fromisoformat((b or a).strip()).replace(tzinfo=self.tz)
        except ValueError:
            return {"error": "unparseable date_range; ask the caller for a date and use YYYY-MM-DD/YYYY-MM-DD", "retry": True}
        if d1 < d0 or (d1 - d0).days > 7:
            return {"error": "range must be 0-7 days and ordered", "retry": True}
        r, now = self.mem.rules, datetime.now(self.tz)
        d1 = d1.replace(hour=23, minute=59)
        busy = self.cal.busy(max(d0, now), d1)
        by_day, d = {}, d0.replace(hour=0, minute=0)
        while d <= d1:
            t = d.replace(hour=r["earliest_hour"])
            while d.weekday() < 5 and t + timedelta(minutes=30) <= d.replace(hour=r["latest_hour"]):
                if t > now and self._free(t, t + timedelta(minutes=30), busy):
                    by_day.setdefault(d.date(), []).append(t)
                t += timedelta(minutes=30)
            d += timedelta(days=1)
        # spread suggestions across days: first of each day, then second, ...
        picks = [s for tier in zip(*[v for v in by_day.values()]) for s in tier] or []
        picks += [s for v in by_day.values() for s in v if s not in picks]
        if not picks:
            return {"slots": [], "note": "nothing free in that range; try another range"}
        return {"slots": [{"start": s.isoformat(), "end": (s + timedelta(minutes=30)).isoformat()} for s in picks[:3]]}

    def book_meeting(self, title, start, end, attendee_email=None):
        try:
            s, e = self._parse(start), self._parse(end)
        except ValueError:
            return {"error": "unparseable start/end; re-offer slots", "retry": True}
        if (bad := self._check_rules(s, e)):
            return {"error": bad, "retry": True}
        if attendee_email and not EMAIL.match(str(attendee_email).strip()):
            return {"error": "that email looks invalid; ask again or book without an invite", "retry": True}
        if not self._free(s, e, self.cal.busy(s - timedelta(hours=1), e + timedelta(hours=1))):
            return {"error": "that slot was just taken", "retry": True}
        title = re.sub(r"[\x00-\x1f]", " ", str(title))[:100].strip() or "Meeting"
        email = str(attendee_email).strip() if attendee_email else None
        self.cal.insert(title, s, e, email)
        return {"ok": True, "booked": s.isoformat()}

    def notify_owner(self, summary):
        urgent = self.caller and (self.mem.times_contacted(self.caller) >= 2 or self.mem.is_vip(self.caller))
        self._notify(("[URGENT] " if urgent else "") + "[Gatekeeper] " + str(summary).replace("\n", " ")[:300])
        return {"ok": True}

    def remember(self, caller, note):
        self.caller = str(caller)
        return self.mem.remember(caller, note)

    def recall(self, caller):
        self.caller = str(caller)
        return self.mem.recall(caller)
