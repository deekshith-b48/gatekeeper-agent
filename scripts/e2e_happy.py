"""STAGE 1. Full happy path on the REAL calendar through the same WebSocket a browser uses."""
import asyncio, json, time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from scripts.e2e_lib import *  # noqa
from scripts.e2e_lib import Conv, Report, ENV, BASE, httpx, gcal, find_test_events, cleanup_test_events, bookings_for, audit_rows, foreign_tool_executions, server_errors_since, log_offset, SLOW_S

R = Report("stage1_happy")
TZ = ZoneInfo(ENV.get("OWNER_TIMEZONE", "Asia/Kolkata"))

async def admin(path, body=None):
    async with httpx.AsyncClient(timeout=20) as c:
        r = await (c.post(f"{BASE}{path}", json=body, headers={"x-admin-token": ENV["ADMIN_TOKEN"]}) if body else c.get(f"{BASE}{path}", headers={"x-admin-token": ENV["ADMIN_TOKEN"]}))
        return r.json()

def ntfy_since(ts):
    r = httpx.get(f"https://ntfy.sh/{ENV['NTFY_TOPIC']}/json?poll=1&since={int(ts)}", timeout=15)
    return [json.loads(l) for l in r.text.splitlines() if l and json.loads(l).get("event") == "message"]

async def main():
    off = log_offset(); t_start = time.time()
    focus0 = (await admin("/api/focus"))["on"]
    if focus0: await admin("/api/focus", {"on": False})          # delivery check needs live pushes; restored at the end
    cleanup_test_events("e2e-happy")
    c = Conv(caller_id=f"e2e-happy-{int(t_start)}"); await c.open()
    R.add("greeting received", bool(c.greeting), c.greeting)
    try:
        t1 = await c.say("Hi, can I get 15 minutes with him today?")
        R.add("turn 1: agent responds with next step (name/reason or slots)", bool(t1.reply) and ("?" in t1.reply or t1.ev("slots_offered")), t1.reply, t1.latency)
        t2 = await c.say("I'm Priya from Acme, it's about a partnership. Please title the meeting [TEST] e2e happy path. What slots do you have on the next working day?")
        tries = 0
        while not t2.ev("slots_offered") and tries < 2:
            t2 = await c.say("Which exact slots are free? Please list them."); tries += 1
        slots = t2.ev("slots_offered")
        R.add("turn 2: 2-3 slots offered via real get_free_slots", bool(slots), t2.reply, t2.latency)
        offered = slots[-1]["slots"] if slots else []
        # verify offered slots against REAL freebusy + owner rules (independent of the agent)
        problems = []
        if offered:
            from app.db import repo
            with db().session() as s:
                from app.db.models import SlotOffer
                from sqlmodel import select
                rows = {o.id: o for o in s.exec(select(SlotOffer).where(SlotOffer.conversation_id == c.id)).all()}
            cal = gcal()
            for o in offered:
                so = rows.get(o["slot_id"]); st_, en_ = so.start.replace(tzinfo=timezone.utc), so.end.replace(tzinfo=timezone.utc)
                lt = st_.astimezone(TZ)
                busy = await cal.freebusy(st_ - timedelta(minutes=10), en_ + timedelta(minutes=10))
                if busy: problems.append(f"{o['when']} overlaps busy {busy[0]}")
                if lt.weekday() >= 5 or lt.hour < 11 or (en_.astimezone(TZ).hour * 60 + en_.astimezone(TZ).minute) > 18 * 60 or (en_ - st_) > timedelta(minutes=30) or st_ <= datetime.now(timezone.utc):
                    problems.append(f"{o['when']} violates owner rules")
        R.add("offered slots are actually free (freebusy) and inside owner rules", offered and not problems, "; ".join(problems))
        t3 = await c.say("The first one please.")
        conf = t3.ev("booking_confirmed")
        if not conf:
            t3 = await c.say("Yes, please book the first slot you offered."); conf = t3.ev("booking_confirmed")
        R.add("booking confirmed", bool(conf), t3.reply, t3.latency)
        bk = bookings_for(c.id)
        R.add("exactly one booking row recorded", len(bk) == 1, f"{len(bk)}")
        evs = find_test_events("e2e-happy")
        R.add("event exists on the REAL calendar", len(evs) == 1, f"{len(evs)} found")
        if evs and bk:
            e = evs[0]; es = datetime.fromisoformat(e["start"]["dateTime"]).astimezone(timezone.utc); ee = datetime.fromisoformat(e["end"]["dateTime"]).astimezone(timezone.utc)
            R.add("event time matches the booking", es == bk[0].start.replace(tzinfo=timezone.utc) and ee == bk[0].end.replace(tzinfo=timezone.utc), f"{es} vs {bk[0].start}")
            R.add("event title present", bool(e.get("summary")), e.get("summary", "") + ("" if "[TEST]" in e.get("summary", "") else "  (model did not keep the [TEST] tag; cleanup is keyed on the caller id, not the title)"))
        await asyncio.sleep(2)
        pushes = [m for m in ntfy_since(t_start) if "booking" in (m.get("title", "") + m.get("message", "")).lower()]
        from sqlmodel import select
        from app.db.models import Alert
        with db().session() as s:
            al = s.exec(select(Alert).where(Alert.conversation_id == c.id)).all()
        R.add("owner notified (alert stored + pushed to ntfy)", bool(al) and bool(pushes) and all(a.delivered for a in al if a.kind == "booking"), f"alerts={[(a.kind, a.delivered) for a in al]} pushes={len(pushes)}")
        R.add("zero foreign/owner tools executed", not foreign_tool_executions(c.id), "")
        lat = [t.latency for t in c.turns]
        for i, t in enumerate(c.turns, 1):
            if t.latency > SLOW_S: R.warn(f"turn {i} slow", f"{t.latency:.1f}s > {SLOW_S}s: {t.text[:60]}")
        print("per-turn latency:", [round(x, 1) for x in lat])
    finally:
        await c.close()
        n = cleanup_test_events("e2e-happy")
        R.add("cleanup: test event deleted (owner/test path)", n >= 0 and not find_test_events("e2e-happy"), f"deleted {n}")
        if focus0: await admin("/api/focus", {"on": True})
    R.add("no server errors in log during stage", not server_errors_since(off), "\n".join(server_errors_since(off))[:200])
    ok = R.save(); raise SystemExit(0 if ok else 1)

asyncio.run(main())
