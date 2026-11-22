"""Role-based tool registry. A role can only ever resolve tools in ITS registry; the model is only shown those."""
import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.bus import bus
from app.security.guards import rule_check
from app.agent.slots import compute_slots, spread
from app.core.config import Settings
from app.db import repo
from app.db.models import Booking, Caller, Owner, new_id
from app.db.repo import Database
from app.providers.calendar.base import CalendarProvider, CalendarUnavailable
from app.providers.notify import NtfyNotifier

EMAIL_RX = re.compile(r"^[^@\s,;<>]{1,64}@[^@\s,;<>]{1,255}\.[^@\s,;<>]{2,}$")
CTRL = re.compile(r"[\x00-\x1f\x7f]")


def clean(s: str, n: int) -> str:
    return CTRL.sub(" ", s).strip()[:n]


NAME_RX = re.compile(r"^[\w .,'&()\-]{1,60}$")
TITLE_RX = re.compile(r"^[\w .,'&()\[\]\-/]{1,100}$")
MAX_PER_CALL, MAX_PER_DAY = 2, 3
_push_tasks: set = set()  # keep references so background pushes are not garbage-collected
_book_lock = asyncio.Lock()  # check-then-insert must be atomic or two callers can take the same slot


def _plain(v: str, rx: re.Pattern, what: str) -> str:
    """Names/titles end up in the calendar, alerts and logs: only plain text, never instructions."""
    v = clean(v, 100)
    if not rx.match(v) or rule_check(v, 200).flagged:
        raise ValueError(f"{what} must be plain text (letters, numbers, spaces)")
    return v


@dataclass
class ToolContext:
    st: Settings
    db: Database
    owner: Owner
    caller: Caller
    conversation_id: str
    calendar: CalendarProvider
    notifier: NtfyNotifier
    events: list[dict] = field(default_factory=list)  # UI events emitted by tools
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.owner.timezone)


# ---------- argument models (strict) ----------
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetFreeSlotsArgs(_Strict):
    date_from: date
    date_to: date
    duration_min: int = 30


class BookMeetingArgs(_Strict):
    slot_id: str = Field(min_length=8, max_length=64)
    title: str = "Meeting"
    caller_name: str = Field(min_length=1, max_length=80)
    caller_email: str | None = None

    @field_validator("title")
    @classmethod
    def _t(cls, v):
        return _plain(v, TITLE_RX, "title")

    @field_validator("caller_name")
    @classmethod
    def _n(cls, v):
        return _plain(v, NAME_RX, "caller_name")

    @field_validator("caller_email")
    @classmethod
    def _e(cls, v):
        if v in (None, ""):
            return None
        v = v.strip()
        if not EMAIL_RX.match(v):
            raise ValueError("invalid email address")
        return v


class TakeMessageArgs(_Strict):
    text: str = Field(min_length=1, max_length=1000)
    caller_name: str | None = Field(default=None, max_length=80)

    @field_validator("caller_name")
    @classmethod
    def _n(cls, v):
        return _plain(v, NAME_RX, "caller_name") if v else None

    @field_validator("text")
    @classmethod
    def _c(cls, v):
        return clean(v, 1000)


class RecallArgs(_Strict):
    pass


# ---------- alerts ----------
async def alert_owner(ctx: ToolContext, kind: str, summary: str):
    """Internal (not a model tool). URGENT if VIP (verified contact) or 2+ contacts in 7 days.
    Always stored in the inbox. Pushed immediately unless focus mode is ON and the alert is not urgent."""
    n = repo.contacts_last_7d(ctx.db, ctx.caller.id)
    urgent = ctx.caller.trust_level == "vip" or n >= 2
    who = ctx.caller.name or "Unknown caller"
    reason = " (VIP)" if ctx.caller.trust_level == "vip" else f" ({n} contacts in 7 days)" if n >= 2 else ""
    text = f"{who}{reason}: {summary}"
    hold = repo.get_focus(ctx.db, ctx.owner.id) and not urgent
    alert = repo.add_alert(ctx.db, ctx.owner.id, ctx.conversation_id, ctx.caller.id, kind, text, urgent, delivered=False)
    bus.publish({"type": "alert", "kind": kind, "urgent": urgent, "held": hold, "summary": text[:160]})
    if hold:
        return None  # stays undelivered: goes out in the focus-end digest

    async def push():
        ok = await ctx.notifier.send(f"{'URGENT: ' if urgent else ''}Gatekeeper {kind}", text, urgent=urgent,
                                     click=f"{ctx.st.public_url.rstrip('/')}/inbox" if ctx.st.public_url else "")
        if ok:
            repo.mark_delivered(ctx.db, [alert.id])  # a failed push stays undelivered and is retried in the next digest

    task = asyncio.create_task(push())  # the caller must not wait on the push: it would add ~1 s to the booking turn
    _push_tasks.add(task)
    task.add_done_callback(_push_tasks.discard)
    return task


async def calendar_down(ctx: ToolContext, wanted: str) -> dict:
    """Calendar failure path: save a message, alert the owner, tell the model to be honest."""
    msg = f"Calendar unavailable while caller tried to: {wanted}. Caller must be contacted manually."
    repo.add_memory(ctx.db, "caller", "message", msg, caller_id=ctx.caller.id)
    await alert_owner(ctx, "calendar down", msg)
    return {"error": "calendar_unavailable", "message_saved": True,
            "instruction": "Tell the caller honestly the calendar is unreachable, that you saved a message, and the owner will get back to them."}


# ---------- tools ----------
async def get_free_slots(ctx: ToolContext, a: GetFreeSlotsArgs) -> dict:
    now = ctx.now_fn()
    today = now.astimezone(ctx.tz).date()
    if a.date_from < today:
        return {"error": "date_from is in the past", "today": today.isoformat()}
    if a.date_to < a.date_from:
        return {"error": "date_to is before date_from"}
    if (a.date_to - a.date_from).days > 14:
        return {"error": "range too large, max 14 days"}
    try:
        cands_window = (datetime.combine(a.date_from, datetime.min.time(), tzinfo=ctx.tz).astimezone(timezone.utc),
                        datetime.combine(a.date_to + timedelta(days=1), datetime.min.time(), tzinfo=ctx.tz).astimezone(timezone.utc))
        busy = await ctx.calendar.freebusy(*cands_window)
        cands = compute_slots(ctx.owner, busy, a.date_from, a.date_to, a.duration_min, now)
    except CalendarUnavailable:
        return await calendar_down(ctx, "find a free slot")
    except ValueError as e:
        return {"error": str(e)}
    picks = spread(cands, ctx.tz, 5)
    if not picks:
        return {"slots": [], "note": "no availability in that range; try other dates"}
    offers = repo.create_offers(ctx.db, ctx.conversation_id, picks)
    out = [{"slot_id": o.id, "when": f"{o.start.replace(tzinfo=timezone.utc).astimezone(ctx.tz):%A %d %B, %H:%M}",
            "minutes": int((o.end - o.start).total_seconds() // 60)} for o in offers]
    ctx.events.append({"type": "slots_offered", "slots": out, "timezone": ctx.owner.timezone})
    return {"slots": out, "timezone": ctx.owner.timezone}


async def book_meeting(ctx: ToolContext, a: BookMeetingArgs) -> dict:
    if len(repo.bookings_in_conversation(ctx.db, ctx.conversation_id)) >= MAX_PER_CALL or repo.recent_bookings_by_caller(ctx.db, ctx.caller.id, 24) >= MAX_PER_DAY:
        return {"error": "booking limit reached for this caller; offer to take a message instead"}
    async with _book_lock:
        return await _book_locked(ctx, a)


async def _book_locked(ctx: ToolContext, a: BookMeetingArgs) -> dict:
    offer = repo.get_offer(ctx.db, ctx.conversation_id, a.slot_id)
    if not offer:
        return {"error": "unknown, expired or already used slot_id; call get_free_slots and use a slot_id it returned"}
    start, end = offer.start.replace(tzinfo=timezone.utc), offer.end.replace(tzinfo=timezone.utc)
    if start <= ctx.now_fn():
        return {"error": "slot is in the past; offer new slots"}
    buf = timedelta(minutes=ctx.owner.buffer_min)
    try:  # re-check right before insert: someone may have booked it since it was offered
        busy = await ctx.calendar.freebusy(start - buf, end + buf)
        if any(s < end + buf and start - buf < e for s, e in busy):
            repo.mark_offer_used(ctx.db, a.slot_id)
            return {"error": "that slot was just taken; call get_free_slots again"}
        booking_id = new_id()
        desc = f"Booked by Gatekeeper for {a.caller_name}. Contact id: {ctx.caller.contact}."
        event_id = await ctx.calendar.create_event(booking_id, a.title, start, end, a.caller_email, desc)
    except CalendarUnavailable:
        return await calendar_down(ctx, "book a meeting")
    repo.mark_offer_used(ctx.db, a.slot_id)
    repo.create_booking(ctx.db, Booking(id=booking_id, conversation_id=ctx.conversation_id, calendar_event_id=event_id,
                                        title=a.title, start=start.replace(tzinfo=None), end=end.replace(tzinfo=None)))
    repo.update_caller(ctx.db, ctx.caller.id, a.caller_name, a.caller_email)
    ctx.caller.name = a.caller_name
    when = f"{start.astimezone(ctx.tz):%A %d %B, %H:%M}"
    demo = "dry-run" in ctx.calendar.name
    ctx.events.append({"type": "booking_confirmed", "when": when, "timezone": ctx.owner.timezone,
                       "event_id": event_id, "invite_sent": bool(a.caller_email) and not demo, "demo": demo})
    await alert_owner(ctx, "booking", f"booked '{a.title}' {when} ({ctx.owner.timezone})" + (" [demo: not written to the calendar]" if demo else ""))
    return {"ok": True, "when": when, "invite_sent": bool(a.caller_email)}


async def take_message(ctx: ToolContext, a: TakeMessageArgs) -> dict:
    if a.caller_name:
        repo.update_caller(ctx.db, ctx.caller.id, a.caller_name)
        ctx.caller.name = a.caller_name
    repo.add_memory(ctx.db, "caller", "message", a.text, caller_id=ctx.caller.id)
    await alert_owner(ctx, "message", a.text[:200])
    return {"ok": True}


async def recall_caller(ctx: ToolContext, a: RecallArgs) -> dict:
    h = repo.caller_history(ctx.db, ctx.caller.id)
    # counts only: names and notes are unverified claims from earlier calls and must never shape who the agent thinks is calling
    return {"times_contacted": h["times_contacted"], "past_bookings": h["past_bookings"]}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    fn: Callable[[ToolContext, Any], Awaitable[dict]]


CALLER_TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("get_free_slots", "Find real free meeting slots. args: date_from, date_to (YYYY-MM-DD), duration_min (default 30). Returns slot_ids.", GetFreeSlotsArgs, get_free_slots),
    Tool("book_meeting", "Book one of the slots returned by get_free_slots. args: slot_id, title, caller_name, caller_email (optional).", BookMeetingArgs, book_meeting),
    Tool("take_message", "Save a message for the owner when the caller does not want or cannot book. args: text, caller_name (if known).", TakeMessageArgs, take_message),
    Tool("recall_caller", "How many times this caller contacted before (no identity). args: none.", RecallArgs, recall_caller),
]}
OWNER_TOOLS: dict[str, Tool] = {}  # Phase 3. Never exposed to caller sessions.
REGISTRY = {"caller": CALLER_TOOLS, "owner": OWNER_TOOLS}


def tool_specs(role: str) -> str:
    return "\n".join(f"- {t.name}: {t.description}" for t in REGISTRY[role].values())


async def call_tool(ctx: ToolContext, role: str, name: str, raw_args: dict) -> dict:
    """The single choke point. Unknown/foreign-role tools are denied and audited; every call is audited."""
    t0 = time.monotonic()
    tool = REGISTRY.get(role, {}).get(name)
    if tool is None:
        res, err = {"error": f"unknown tool '{name}'. Available: {', '.join(REGISTRY.get(role, {}))}"}, "denied"
    else:
        try:
            res, err = await tool.fn(ctx, tool.args_model.model_validate(raw_args)), ""
        except ValidationError as e:
            res, err = {"error": "invalid arguments: " + "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())}, "validation"
        except Exception as e:  # noqa: BLE001 - tool boundary: never crash a call
            res, err = {"error": "tool failed"}, repr(e)
    repo.audit(ctx.db, ctx.conversation_id, role, name, raw_args, res, err, int((time.monotonic() - t0) * 1000))
    return res
