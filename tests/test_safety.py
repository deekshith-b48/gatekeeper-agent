import os, tempfile, time, unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from gatekeeper.calendar import MockCalendar
from gatekeeper.memory import Memory
from gatekeeper.tools import Tools
import gatekeeper.tools as T

TZ = "UTC"


def nxt(h, m=0, days=2):
    d = datetime.now(ZoneInfo(TZ)) + timedelta(days=days)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.replace(hour=h, minute=m, second=0, microsecond=0)


class Base(unittest.TestCase):
    def setUp(self):
        self.out = []
        self.mem = Memory(os.path.join(tempfile.mkdtemp(), "m.json"))
        self.cal = MockCalendar()
        self.t = Tools(self.cal, self.mem, TZ, notify=self.out.append)

    def book(self, s, e, **kw):
        return self.t.call("book_meeting", {"title": "x", "start": s.isoformat(), "end": e.isoformat(), **kw})


class Safety(Base):
    def test_destructive_tools_do_not_exist(self):
        for n in ["delete_event", "cancel_meeting", "update_event", "list_events", "get_event", "bash"]:
            self.assertIn("not permitted", self.t.call(n, {"id": "1"})["error"])

    def test_only_five_tools_in_schema(self):
        self.assertEqual({s["name"] for s in T.SCHEMAS}, {"get_free_slots", "book_meeting", "notify_owner", "remember", "recall"})

    def test_garbage_input(self):
        self.assertTrue(self.t.call("get_free_slots", {"date_range": "banana"})["retry"])
        self.assertTrue(self.t.call("get_free_slots", {"date_range": ""})["retry"])
        self.assertIn("error", self.t.call("book_meeting", {"title": "x", "start": "soon", "end": "later"}))
        self.assertIn("error", self.t.call("book_meeting", {"bogus": 1}))
        self.assertIn("error", self.t.call("get_free_slots", "not a dict"))

    def test_rules_enforced(self):
        s = nxt(11)
        self.assertIn("past", self.book(s - timedelta(days=30), s - timedelta(days=30, minutes=-30))["error"])
        self.assertIn("between", self.book(nxt(9), nxt(9, 30))["error"])        # before 11:00
        self.assertIn("at most", self.book(nxt(15), nxt(16))["error"])           # > 30 min
        self.assertIn("invalid", self.book(nxt(15), nxt(15, 30), attendee_email="a@b, evil@x.com")["error"])
        self.assertEqual(self.cal.events, [])

    def test_slots_and_booking(self):
        d = nxt(0).date().isoformat()
        slots = self.t.call("get_free_slots", {"date_range": d})["slots"]
        self.assertTrue(slots)
        for s in slots:
            self.assertGreaterEqual(datetime.fromisoformat(s["start"]).hour, 11)
        r = self.t.call("book_meeting", {"title": "Hi", "attendee_email": "a@b.co", **slots[0]})
        self.assertTrue(r["ok"])
        self.assertEqual(self.book(datetime.fromisoformat(slots[0]["start"]), datetime.fromisoformat(slots[0]["end"]))["error"], "that slot was just taken")

    def test_calendar_failure_takes_message(self):
        class Boom(MockCalendar):
            def busy(self, *a): raise RuntimeError("api down")
        t = Tools(Boom(), self.mem, TZ, notify=self.out.append)
        t.call("remember", {"caller": "Sam", "note": "wants chat"})
        r = t.call("get_free_slots", {"date_range": nxt(0).date().isoformat()})
        self.assertEqual(r["error"], "tool_failed")
        self.assertIn("take a message", r["say"])
        self.assertTrue(any("MESSAGE PENDING" in n["note"] for n in self.mem.recall("Sam")["notes"]))
        self.assertTrue(self.out)

    def test_timeout(self):
        class Slow(MockCalendar):
            def busy(self, *a): time.sleep(1)
        T.TIMEOUT = 0.1
        try:
            t = Tools(Slow(), self.mem, TZ, notify=self.out.append)
            self.assertEqual(t.call("get_free_slots", {"date_range": nxt(0).date().isoformat()})["error"], "tool_failed")
        finally:
            T.TIMEOUT = 10


class MemoryFlags(Base):
    def test_repeat_caller_urgent(self):
        self.t.call("remember", {"caller": "Pat", "note": "first"})
        self.t.notify_owner("hello"); self.assertFalse(self.out[-1].startswith("[URGENT]"))
        self.mem._seen.clear()  # new session
        self.t.call("remember", {"caller": "Pat", "note": "again"})
        self.t.notify_owner("hello"); self.assertTrue(self.out[-1].startswith("[URGENT]"))

    def test_vip_urgent(self):
        self.t.call("remember", {"caller": "My Manager", "note": "x"})
        self.t.notify_owner("hi"); self.assertTrue(self.out[-1].startswith("[URGENT]"))


if __name__ == "__main__":
    unittest.main()
