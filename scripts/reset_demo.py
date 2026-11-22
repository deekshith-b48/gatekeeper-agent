"""Pre-demo reset. Removes ONLY test data (callers whose id starts with e2e-/stage-/selftest/ssetest), deletes [TEST]/e2e calendar events, warms every model.
   python -m scripts.reset_demo [--dry] [--focus on|off] [--no-warm]"""
import asyncio, sys, time
from scripts.e2e_lib import *  # noqa
from scripts.e2e_lib import ENV, ROOT, BASE, db, find_test_events, cleanup_test_events

PREFIXES = ("e2e-", "stage-", "selftest", "ssetest")
dry = "--dry" in sys.argv

def purge():
    from sqlmodel import select
    from app.db.models import Alert, AuditLog, Booking, Caller, Conversation, Memory, Message, SlotOffer
    with db().session() as s:
        callers = [c for c in s.exec(select(Caller)).all() if c.contact.startswith(PREFIXES)]
        ids = [c.id for c in callers]
        convs = [c.id for c in s.exec(select(Conversation)).all() if c.caller_id in ids]
        counts = {"callers": len(ids), "conversations": len(convs)}
        for model, col in ((Message, Message.conversation_id), (AuditLog, AuditLog.conversation_id), (Booking, Booking.conversation_id), (SlotOffer, SlotOffer.conversation_id), (Alert, Alert.conversation_id)):
            rows = s.exec(select(model).where(col.in_(convs))).all() if convs else []; counts[model.__name__] = len(rows)
            if not dry:
                for r in rows: s.delete(r)
        mem = s.exec(select(Memory).where(Memory.caller_id.in_(ids))).all() if ids else []; counts["Memory"] = len(mem)
        if not dry:
            for r in mem: s.delete(r)
            for cid in convs: s.delete(s.get(Conversation, cid))
            for c in callers: s.delete(c)
            s.commit()
        return counts

async def warm():
    from app.core.config import Settings
    from app.agent.llm import OpenAICompatLLM, parse_step
    st = Settings(); llm = OpenAICompatLLM(st)
    m = [{"role": "system", "content": 'Reply with one JSON object {"say": "..."}'}, {"role": "user", "content": "hi"}]
    for label, model, base, key in (("primary", st.llm_model, None, None), ("fallback", st.llm_fallback_model, None, None), ("second", st.llm2_model, st.llm2_base_url, st.llm2_api_key)):
        t0 = time.monotonic()
        try: await llm._once(model, m, base, key); print(f"warm {label:9s} {model}: {time.monotonic() - t0:.1f}s")
        except Exception as e: print(f"warm {label:9s} {model}: FAILED {str(e)[:80]}")
    from datetime import datetime, timedelta, timezone
    from scripts.e2e_lib import gcal
    t0 = time.monotonic(); n = datetime.now(timezone.utc); await gcal().freebusy(n, n + timedelta(days=1)); print(f"warm calendar: {time.monotonic() - t0:.1f}s")

print("calendar [TEST]/e2e events found:", len(find_test_events("e2e-")), "| removed:", 0 if dry else cleanup_test_events("e2e-"))
print("db test data:", purge(), "(dry run)" if dry else "removed")
if "--focus" in sys.argv:
    import httpx
    v = sys.argv[sys.argv.index("--focus") + 1]
    print("focus ->", httpx.get(f"{BASE}/hook/focus/{v}", params={"key": ENV["ADMIN_TOKEN"]}).json()["focus"])
if "--no-warm" not in sys.argv and not dry: asyncio.run(warm())
