import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select

from app.agent.tools import CALLER_TOOLS, OWNER_TOOLS, REGISTRY, ToolContext, call_tool
from app.db import repo
from app.db.models import AuditLog, Memory
from app.providers.notify import NtfyNotifier


def ctx_of(sess, deps, cal):
    return ToolContext(deps.st, deps.db, sess.owner, sess.caller, sess.conversation_id, cal, NtfyNotifier("http://x", ""))


def day_ahead(n=3):
    d = datetime.now(timezone.utc) + timedelta(days=n)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.date().isoformat()


async def offer(ctx, d=None):
    d = d or day_ahead()
    r = await call_tool(ctx, "caller", "get_free_slots", {"date_from": d, "date_to": d})
    return r["slots"]


def test_caller_toolset_is_exactly_four_and_owner_tools_separate():
    assert set(CALLER_TOOLS) == {"get_free_slots", "book_meeting", "take_message", "recall_caller"}
    assert not set(OWNER_TOOLS) & set(CALLER_TOOLS)
    assert "caller" in REGISTRY and REGISTRY["caller"] is CALLER_TOOLS


@pytest.mark.parametrize("name", ["delete_event", "cancel_meeting", "list_events", "update_event", "notify_owner", "bash"])
async def test_unknown_tools_denied_and_audited(make_session, cal, name):
    sess, deps = make_session()
    r = await call_tool(ctx_of(sess, deps, cal), "caller", name, {"id": "1"})
    assert "unknown tool" in r["error"]
    with deps.db.session() as s:
        a = s.exec(select(AuditLog).where(AuditLog.name == name)).one()
        assert a.error == "denied" and a.actor_role == "caller"


async def test_full_booking_flow_creates_real_event(make_session, cal):
    sess, deps = make_session()
    c = ctx_of(sess, deps, cal)
    slots = await offer(c)
    assert slots and all(int(s["when"][-5:-3]) >= 11 for s in slots)
    r = await call_tool(c, "caller", "book_meeting", {"slot_id": slots[0]["slot_id"], "title": "Chat", "caller_name": "Priya", "caller_email": "p@acme.com"})
    assert r["ok"] and r["invite_sent"]
    assert len(cal.events) == 1 and list(cal.events.values())[0][3] == "p@acme.com"
    assert {e["type"] for e in c.events} == {"slots_offered", "booking_confirmed"}
    again = await call_tool(c, "caller", "book_meeting", {"slot_id": slots[0]["slot_id"], "caller_name": "Priya"})
    assert "error" in again and len(cal.events) == 1  # slot_id is single-use


async def test_invented_and_foreign_slot_ids_rejected(make_session, cal):
    a, deps = make_session(contact="a")
    c = ctx_of(a, deps, cal)
    slots = await offer(c)
    b_owner = repo.get_owner(deps.db, "owner")
    bc, bconv = repo.start_conversation(deps.db, b_owner, "b")
    cb = ToolContext(deps.st, deps.db, b_owner, bc, bconv.id, cal, c.notifier)
    for sid in ["invented-slot-id-123", slots[0]["slot_id"]]:  # invented, and issued to ANOTHER conversation
        r = await call_tool(cb, "caller", "book_meeting", {"slot_id": sid, "caller_name": "Eve"})
        assert "slot_id" in r["error"]
    assert not cal.events


async def test_double_booking_prevented_by_recheck(make_session, cal):
    sess, deps = make_session()
    c = ctx_of(sess, deps, cal)
    slots = await offer(c)
    o = repo.get_offer(deps.db, sess.conversation_id, slots[0]["slot_id"])
    cal.busy.append((o.start.replace(tzinfo=timezone.utc), o.end.replace(tzinfo=timezone.utc)))  # someone else booked it
    r = await call_tool(c, "caller", "book_meeting", {"slot_id": slots[0]["slot_id"], "caller_name": "Priya"})
    assert "just taken" in r["error"] and not cal.events


async def test_validation(make_session, cal):
    sess, deps = make_session()
    c = ctx_of(sess, deps, cal)
    past = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
    assert "past" in (await call_tool(c, "caller", "get_free_slots", {"date_from": past, "date_to": past}))["error"]
    assert "invalid" in (await call_tool(c, "caller", "get_free_slots", {"date_from": "banana", "date_to": ""}))["error"]
    assert "range" in (await call_tool(c, "caller", "get_free_slots", {"date_from": day_ahead(1), "date_to": day_ahead(60)}))["error"]
    assert "duration" in (await call_tool(c, "caller", "get_free_slots", {"date_from": day_ahead(), "date_to": day_ahead(), "duration_min": 120}))["error"]
    slots = await offer(c)
    bad = await call_tool(c, "caller", "book_meeting", {"slot_id": slots[0]["slot_id"], "caller_name": "X", "caller_email": "a@b, evil@x.com"})
    assert "email" in bad["error"]
    extra = await call_tool(c, "caller", "book_meeting", {"slot_id": slots[0]["slot_id"], "caller_name": "X", "start": "2030-01-01T03:00"})
    assert "invalid arguments" in extra["error"]  # model cannot smuggle its own times
    assert not cal.events


async def test_calendar_down_takes_message(make_session, cal):
    sess, deps = make_session()
    cal.down = True
    r = await call_tool(ctx_of(sess, deps, cal), "caller", "get_free_slots", {"date_from": day_ahead(), "date_to": day_ahead()})
    assert r["error"] == "calendar_unavailable" and r["message_saved"]
    with deps.db.session() as s:
        assert s.exec(select(Memory).where(Memory.caller_id == sess.caller.id)).one().key == "message"


async def test_recall_and_urgency(make_session, cal):
    sess, deps = make_session(contact="rep")
    repo.start_conversation(deps.db, sess.owner, "rep")  # second contact within 7 days
    assert repo.contacts_last_7d(deps.db, sess.caller.id) == 2
    r = await call_tool(ctx_of(sess, deps, cal), "caller", "recall_caller", {})
    assert r["times_contacted"] == 2
