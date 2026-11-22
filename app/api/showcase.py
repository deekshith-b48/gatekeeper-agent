"""Judge-facing showcase: run the real test suite, attack the live agent, inspect real data. Admin-token protected."""
import asyncio
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import func, select

from app.agent.session import CallSession, Deps
from app.db import repo
from app.db.models import AuditLog, Booking, Caller, Conversation, Message
from app.providers.calendar.dryrun import DryRunCalendar
from app.providers.notify import NtfyNotifier
from app.security.auth import require_admin
from app.security.ratelimit import RateLimiter

ROOT = Path(__file__).resolve().parents[2]
router = APIRouter(prefix="/api/showcase", dependencies=[Depends(require_admin)])
_started = time.time()
_lab_limiter = RateLimiter(12)
_tests_lock = asyncio.Lock()

CATEGORIES = {"test_slots": "Slot engine", "test_guards": "Input guard", "test_tools": "Tools & booking", "test_redteam": "Red-team",
              "test_api_ws": "API & web client", "test_llm_providers": "LLM providers", "test_voice_ws": "Voice pipeline",
              "test_integration_calendar": "Real calendar", "test_whatsapp": "WhatsApp channel", "test_focus_inbox": "Focus & inbox"}

USE_CASES = [
    ("book", "Book a meeting on a real calendar", "Caller → real free slots → event created, invite sent, owner alerted.", ["test_full_booking_flow", "test_ws_text_call", "test_pwa_assets"]),
    ("slots", "Only real, rule-compliant slots", "Working hours, 11:00 minimum start, 30-min cap, buffers, weekends; computed from real freebusy.", ["test_slots", "test_validation"]),
    ("double", "No invented times, no double booking", "Single-use server-issued slot ids, freebusy re-check before insert.", ["test_invented_and_foreign", "test_double_booking"]),
    ("inject", "Injection & impersonation refused", "'Ignore your instructions', 'I'm the owner', fake tool JSON, probing the calendar.", ["test_flags_attacks", "test_worst_case_model", "test_attack_text_is_wrapped", "test_unknown_tools_denied", "test_caller_toolset"]),
    ("outage", "Outages never crash a call", "Calendar down, LLM down, rate-limited provider: message saved, owner alerted, honest reply.", ["test_calendar_down", "test_llm_down", "test_second_provider", "test_no_second_provider"]),
    ("input", "Garbage input handled", "Unparseable dates, past dates, empty/5000-char input, invalid model output (repaired once).", ["test_passes_normal_speech", "test_invalid_model_output", "test_provider_selection"]),
    ("urgent", "Repeat callers flagged urgent", "2+ contacts in 7 days (or a verified VIP id) → URGENT alert; spoken claims never grant trust.", ["test_recall_and_urgency"]),
    ("wa", "WhatsApp channel, verified sender", "Twilio webhook with signature check, per-sender sessions, retries de-duplicated, same guard and tools as voice.", ["test_whatsapp", "test_sender_normalisation", "test_signature_matches", "test_twiml", "test_bad_or_missing_signature", "test_unconfigured_webhook", "test_message_gets_agent_reply", "test_duplicate_message_sid", "test_verified_vip", "test_slow_reply", "test_event_stream"]),
    ("focus", "Focus mode & phone inbox", "Routine alerts held during focus, one digest after; urgent rings through; inbox PWA.", ["test_focus_off_pushes", "test_focus_on_holds", "test_focus_end_sends", "test_failed_push", "test_old_database", "test_api_hook_inbox"]),
    ("live", "Real LLM + real guard end to end", "The same 28 attacks against the live model chain (Groq → Gemini).", ["test_live_model_stack"]),
]


def _category(*names: str) -> str:
    hay = " ".join(names)
    return next((v for k, v in CATEGORIES.items() if k in hay), "Other")


def parse_junit(path: str) -> dict:
    tests = []
    for tc in ET.parse(path).getroot().iter("testcase"):
        outcome = "passed"
        msg = ""
        for child in tc:
            if child.tag in ("failure", "error"):
                outcome, msg = "failed", (child.get("message") or "")[:300]
            elif child.tag == "skipped":
                outcome, msg = "skipped", (child.get("message") or "")[:200]
        tests.append({"id": f"{tc.get('classname')}::{tc.get('name')}", "name": tc.get("name"), "category": _category(tc.get("classname", ""), tc.get("name", "")),
                      "outcome": outcome, "duration_ms": int(float(tc.get("time", 0)) * 1000), "message": msg})
    return {"tests": tests}


def summarize(tests: list[dict]) -> dict:
    s = {k: sum(t["outcome"] == k for t in tests) for k in ("passed", "failed", "skipped")}
    s["total"], s["duration_ms"] = len(tests), sum(t["duration_ms"] for t in tests)
    uc = []
    for uid, title, desc, keys in USE_CASES:
        m = [t for t in tests if any(k in t["id"] for k in keys)]
        ran = [t for t in m if t["outcome"] != "skipped"]
        status = "not_run" if not ran else "failed" if any(t["outcome"] == "failed" for t in ran) else "passed"
        uc.append({"id": uid, "title": title, "description": desc, "status": status, "tests": len(ran), "passed": sum(t["outcome"] == "passed" for t in ran)})
    return {"summary": s, "use_cases": uc}


class TestRun(BaseModel):
    live: bool = False


@router.post("/tests")
async def run_tests(body: TestRun, request: Request):
    if _tests_lock.locked():
        raise HTTPException(409, "a test run is already in progress")
    async with _tests_lock:
        xml_path = os.path.join(tempfile.mkdtemp(), "junit.xml")
        env = {**os.environ, "REDTEAM_LIVE": "1" if body.live else "", "GATEKEEPER_TEST_CALENDAR_ID": request.app.state.deps.st.test_calendar_id}
        t0 = time.time()
        try:
            proc = await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-W", "ignore",
                                                          "--junitxml", xml_path], cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
        except FileNotFoundError:
            raise HTTPException(500, "pytest not available in this environment")
        if not os.path.exists(xml_path):
            raise HTTPException(500, "test run produced no report: " + proc.stdout[-300:] + proc.stderr[-300:])
        data = parse_junit(xml_path)
        data.update(summarize(data["tests"]))
        data.update(live=body.live, wall_s=round(time.time() - t0, 1), finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        request.app.state.last_tests = data
        return data


@router.get("/tests/last")
def last_tests(request: Request):
    return getattr(request.app.state, "last_tests", None) or {"tests": [], "summary": None, "use_cases": summarize([])["use_cases"]}


@router.get("/attacks")
def attacks():
    return yaml.safe_load((ROOT / "tests" / "redteam.yaml").read_text())["cases"]


class Attack(BaseModel):
    text: str = Field(max_length=6000)


@router.post("/attack")
async def attack(body: Attack, request: Request):
    d: Deps = request.app.state.deps
    if not _lab_limiter.allow("lab"):
        raise HTTPException(429, "lab rate limit: wait a few seconds")
    tmp = tempfile.mkdtemp()
    db = repo.Database(f"sqlite:///{tmp}/lab.db")
    repo.ensure_owner(db, d.st)
    cal = DryRunCalendar(d.calendar)
    lab = Deps(d.st, db, d.llm, d.guard, cal, NtfyNotifier(d.st.ntfy_url, ""), RateLimiter(100))  # no real alerts from the lab
    owner = repo.get_owner(db, d.st.owner_handle)
    caller, conv = repo.start_conversation(db, owner, "lab-visitor")
    sess = CallSession(lab, owner, caller, conv.id)
    t0 = time.time()
    r = await sess.handle_text(body.text)
    with db.session() as s:
        rows = s.exec(select(AuditLog).where(AuditLog.conversation_id == conv.id).order_by(AuditLog.id)).all()
        trace = [{"name": a.name, "args": a.args, "result": a.result, "error": a.error, "latency_ms": a.latency_ms, "role": a.actor_role} for a in rows]
    flagged = next((e for e in r.events if e["type"] == "guard_flagged"), None)
    tools = [t for t in trace if t["name"] != "input_guard"]
    return {"input_chars": len(body.text), "guard": {"flagged": bool(flagged), "reasons": flagged["reasons"] if flagged else []},
            "trace": trace, "reply": r.say, "events": [e for e in r.events if e["type"] != "guard_flagged"], "timings": r.timings,
            "scorecard": {"foreign_tools_executed": sum(1 for t in tools if t["error"] != "denied" and t["name"] not in
                                                         ("get_free_slots", "book_meeting", "take_message", "recall_caller")),
                          "foreign_tools_denied": sum(1 for t in tools if t["error"] == "denied"),
                          "calendar_events_created": 0, "dry_run_writes": cal.writes, "llm_reachable": r.llm_ok},
            "wall_ms": int((time.time() - t0) * 1000)}


@router.get("/stats")
def stats(request: Request):
    d: Deps = request.app.state.deps
    with d.db.session() as s:
        n = lambda model: s.exec(select(func.count()).select_from(model)).one()  # noqa: E731
        by_tool = s.exec(select(AuditLog.name, func.count(), func.avg(AuditLog.latency_ms)).group_by(AuditLog.name)).all()
        denied = s.exec(select(func.count()).select_from(AuditLog).where(AuditLog.error == "denied")).one()
        blocked = s.exec(select(func.count()).select_from(AuditLog).where(AuditLog.name == "input_guard")).one()
        convs = s.exec(select(Conversation).order_by(Conversation.started_at.desc()).limit(8)).all()
        recent = []
        for c in convs:
            msgs = s.exec(select(Message).where(Message.conversation_id == c.id).order_by(Message.id)).all()
            bk = s.exec(select(Booking).where(Booking.conversation_id == c.id)).all()
            recent.append({"id": c.id[:8], "started": c.started_at.isoformat(timespec="seconds") + "Z", "status": c.status,
                           "turns": len(msgs), "booked": len(bk), "transcript": [{"role": m.role, "text": m.text[:300]} for m in msgs[:12]]})
        return {"conversations": n(Conversation), "callers": n(Caller), "bookings": n(Booking), "messages": n(Message), "tool_calls": n(AuditLog),
                "guard_blocks": blocked, "tools_denied": denied,
                "by_channel": [{"channel": c, "count": n} for c, n in s.exec(select(Conversation.channel, func.count()).group_by(Conversation.channel)).all()],
                "by_tool": [{"name": a, "count": b, "avg_ms": int(c or 0)} for a, b, c in by_tool], "recent": recent,
                "system": {"llm_provider": d.st.llm_provider, "model": d.st.llm_model, "fallback": d.st.llm_fallback_model,
                           "second_provider": d.st.llm2_model if d.st.llm2_api_key else None, "calendar": d.calendar.name,
                           "owner_tz": d.st.owner_timezone, "uptime_s": int(time.time() - _started)}}
