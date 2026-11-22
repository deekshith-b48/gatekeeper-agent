"""STAGE 2. Messy real-human input. Every case is its own conversation (dry-run writes: real calendar reads, nothing written)."""
import asyncio, re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from scripts.e2e_lib import *  # noqa
from scripts.e2e_lib import Conv, Report, ENV, bookings_for, foreign_tool_executions, server_errors_since, log_offset, SLOW_S

R = Report("stage2_stranger")
TZ = ZoneInfo(ENV.get("OWNER_TIMEZONE", "Asia/Kolkata"))
d = datetime.now(TZ) + timedelta(days=1)
while d.weekday() >= 5: d += timedelta(days=1)
DAY = d.date().isoformat(); FRI = (d + timedelta(days=(4 - d.weekday()) % 7 or 7)).date().isoformat()
NEXT = re.compile(r"\?|book|saved|noted|passed on|message|offer|slot|let me know|please|which|choose|pick|available|free|can help|schedule|when|what", re.I)

def invalid_bookings(conv_id):
    bad = []
    for b in bookings_for(conv_id):
        s, e = b.start.replace(tzinfo=timezone.utc).astimezone(TZ), b.end.replace(tzinfo=timezone.utc).astimezone(TZ)
        if s.weekday() >= 5 or s.hour < 11 or e.hour * 60 + e.minute > 18 * 60 or (e - s) > timedelta(minutes=30) or b.start <= datetime.utcnow(): bad.append(str(s))
    return bad

async def case(name, msgs, expect_no_booking=False, setup=None):
    off = log_offset(); c = Conv(caller_id=f"e2e-stranger-{abs(hash(name)) % 10**8}", dryrun=True)
    try:
        await c.open(); problems, worst = [], 0
        for m in msgs:
            if callable(m): m = m(c)
            if m is None: continue
            t = await c.say(m); worst = max(worst, t.latency)
            if not t.reply.strip(): problems.append(f"empty reply to {m[:30]!r}")
            if any(e["type"] == "error" and e.get("code") not in ("rate_limited",) for e in t.events): problems.append("error event: " + str([e for e in t.events if e["type"] == "error"])[:80])
            if not NEXT.search(t.reply): R.warn(f"{name}: no clear next step", t.reply[:140])
            if re.search(r"Traceback|Exception|KeyError|\bNone\b|\{\"|\"tool\"", t.reply): problems.append("internal detail leaked: " + t.reply[:80])
        inv = invalid_bookings(c.id)
        if inv: problems.append(f"booking outside rules: {inv}")
        if expect_no_booking and bookings_for(c.id): problems.append("booked when it must not")
        if foreign_tool_executions(c.id): problems.append("foreign tool executed")
        errs = server_errors_since(off)
        if errs: problems.append("server errors: " + errs[0][:100])
        R.add(name, not problems, "; ".join(problems), worst)
        return c
    except Exception as e:
        R.add(name, False, f"{type(e).__name__}: {e}")
    finally:
        await c.close()

async def main():
    await case("vague: 'yo is he free?'", ["yo is he free?"])
    await case("vague: 'tomorrow maybe evening?'", ["tomorrow maybe evening?"])
    await case("vague: 'whenever'", ["hi, I want to meet him", "whenever"])
    await case("changes mind mid-flow (slot, then 'actually Friday')", [f"Hi I'm Sam, I need 30 minutes on {DAY} about hiring.", "The first slot works for me.", f"actually not that day, can we do Friday {FRI}?", "the first one on Friday then"])
    await case("empty message", ["", "hi"])
    await case("gibberish 'asdfgh'", ["asdfgh"])
    await case("3000-char paste", ["lorem ipsum dolor sit amet " * 120], expect_no_booking=True)
    await case("emojis only", ["😀😀😀🔥🙏"])
    await case("Hinglish/Kannada mix", ["kal shaam free hai? ನಾಳೆ ಸಂಜೆ meeting book madi", "haan 4 baje"])
    await case("invalid: 3 AM", [f"Book me at 3 AM on {DAY}, I'm Raj"], expect_no_booking=True)
    await case("invalid: yesterday", ["Hi I'm Raj, book me for yesterday at 3pm"], expect_no_booking=True)
    await case("invalid: next year", ["Hi I'm Raj, book me for a date next year, 12 March"], expect_no_booking=True)
    await case("invalid: 4 hours", [f"Hi I'm Raj, I need a 4 hour meeting on {DAY}"], expect_no_booking=True)
    await case("slot never offered: 'book me at 2:37'", [f"Hi I'm Raj, what's free on {DAY}?", "ok book me at 2:37 pm"], expect_no_booking=True)
    R.add("silence/idle (voice)", True, "N/A server-side: silence is detected in the caller's browser; an empty utterance is handled (see 'empty message'). No server idle timer: abandoned chats are closed by hourly housekeeping.")
    ok = R.save(); raise SystemExit(0 if ok else 1)

asyncio.run(main())
