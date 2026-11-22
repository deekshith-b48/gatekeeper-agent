"""Shared helpers for the pre-demo scripts. Talks to the RUNNING server exactly like a browser does (WebSocket), reads ground truth from the DB and Google Calendar."""
import asyncio, json, os, re, sys, time, uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import httpx, websockets  # noqa: E402

ENV = {k.strip(): v.strip() for k, _, v in (l.partition("=") for l in open(ROOT / ".env") if "=" in l and not l.startswith("#"))}
BASE = os.environ.get("GK_BASE", "http://localhost:8000")
WS_BASE = BASE.replace("http", "ws", 1)
LOG = Path(os.environ.get("GK_LOG", "/tmp/gk-server.log"))
SLOW_S = 5.0


@dataclass
class Turn:
    text: str
    reply: str
    events: list
    latency: float
    @property
    def types(self): return [e["type"] for e in self.events]
    def ev(self, kind): return [e for e in self.events if e["type"] == kind]


class Conv:
    def __init__(self, caller_id=None, dryrun=False, base=None, token=None):
        self.caller_id = caller_id or f"e2e-{uuid.uuid4().hex[:10]}"
        self.dryrun, self.id, self.turns, self.greeting = dryrun, None, [], ""
        self.base = (base or WS_BASE); self.token = token or ENV["CALL_TOKEN"]; self.ws = None

    async def open(self):
        url = f"{self.base}/ws/call/owner?server_tts=0&caller_id={self.caller_id}&token={self.token}" + ("&dryrun=1" if self.dryrun else "")
        self.ws = await websockets.connect(url, open_timeout=20)
        ev = await self._until("agent_text")
        self.id = next((e["conversation_id"] for e in ev if e["type"] == "session_started"), None)
        self.greeting = ev[-1]["text"]
        return self

    async def _until(self, kind, timeout=90):
        out = []
        while True:
            e = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
            out.append(e)
            if e["type"] == kind:
                return out

    async def say(self, text, timeout=90) -> Turn:
        t0 = time.monotonic()
        await self.ws.send(json.dumps({"type": "text", "text": text}))
        ev = await self._until("agent_text", timeout)
        t = Turn(text, ev[-1]["text"], ev[:-1], time.monotonic() - t0)
        self.turns.append(t)
        return t

    async def close(self):
        try:
            await self.ws.send('{"type":"hangup"}'); await self.ws.close()
        except Exception:
            pass


def server_errors_since(offset: int) -> list[str]:
    """Server log lines (after byte offset) that indicate a crash or unhandled exception."""
    if not LOG.exists():
        return []
    data = LOG.read_bytes()[offset:].decode("utf-8", "replace")
    return [l for l in data.splitlines() if re.search(r"Traceback|call crashed|Exception in ASGI|500 Internal", l)]


def log_offset() -> int:
    return LOG.stat().st_size if LOG.exists() else 0


def db():
    from app.core.config import Settings
    from app.db.repo import Database
    return Database(Settings().database_url)


def audit_rows(conv_id):
    from sqlmodel import select
    from app.db.models import AuditLog
    with db().session() as s:
        return s.exec(select(AuditLog).where(AuditLog.conversation_id == conv_id).order_by(AuditLog.id)).all()


def bookings_for(conv_id):
    from sqlmodel import select
    from app.db.models import Booking
    with db().session() as s:
        return s.exec(select(Booking).where(Booking.conversation_id == conv_id)).all()


CALLER_TOOLS = {"get_free_slots", "book_meeting", "take_message", "recall_caller"}


def foreign_tool_executions(conv_id):
    """Audit rows that are NOT caller tools and were not denied (must always be empty)."""
    return [a for a in audit_rows(conv_id) if a.name not in CALLER_TOOLS | {"input_guard"} and a.error != "denied"]


def gcal():
    from app.providers.calendar.google import GoogleCalendarProvider
    return GoogleCalendarProvider(ENV.get("GOOGLE_CREDENTIALS_PATH", "credentials.json"), ENV.get("GOOGLE_TOKEN_PATH", "token.json"), ENV.get("GOOGLE_CALENDAR_ID", "primary"))


def find_test_events(contact_prefix="e2e-", days=45):
    """ONLY events our test callers created: description carries the caller's contact id (set by book_meeting)."""
    svc = gcal()._service()
    now = datetime.now(timezone.utc)
    out, tok = [], None
    while True:
        r = svc.events().list(calendarId=ENV.get("GOOGLE_CALENDAR_ID", "primary"), timeMin=now.isoformat(), timeMax=(now + timedelta(days=days)).isoformat(),
                              singleEvents=True, maxResults=250, pageToken=tok).execute()
        out += [e for e in r.get("items", []) if f"Contact id: {contact_prefix}" in (e.get("description") or "")]
        tok = r.get("nextPageToken")
        if not tok:
            return out


def cleanup_test_events(contact_prefix="e2e-"):
    svc, n = gcal()._service(), 0
    for e in find_test_events(contact_prefix):
        svc.events().delete(calendarId=ENV.get("GOOGLE_CALENDAR_ID", "primary"), eventId=e["id"]).execute(); n += 1
    return n


class Report:
    def __init__(self, stage): self.stage, self.rows = stage, []
    def add(self, case, ok, detail="", latency=None):
        self.rows.append({"case": case, "status": "PASS" if ok else "FAIL", "detail": detail[:300], "latency_s": round(latency, 2) if latency else None})
        print(f"{'PASS' if ok else 'FAIL'}  {case}" + (f"  [{latency:.1f}s]" if latency else "") + (f"  | {detail[:160]}" if detail and not ok else ""))
    def warn(self, case, detail): self.rows.append({"case": case, "status": "WARN", "detail": detail[:300], "latency_s": None}); print(f"WARN  {case} | {detail[:160]}")
    def save(self):
        (ROOT / "reports").mkdir(exist_ok=True); (ROOT / "reports" / f"{self.stage}.json").write_text(json.dumps(self.rows, indent=1))
        bad = [r for r in self.rows if r["status"] == "FAIL"]; print(f"\n== {self.stage}: {len(self.rows) - len(bad)}/{len(self.rows)} ok, {len(bad)} failed"); return not bad
