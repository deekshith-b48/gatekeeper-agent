"""Test-only helpers. They implement the REAL interfaces (CalendarProvider, LLM); nothing here ships in app/."""
import json
from datetime import datetime

from app.agent.llm import LlamaGuard
from app.agent.session import Deps
from app.core.config import Settings
from app.db import repo
from app.providers.calendar.base import CalendarProvider, CalendarUnavailable
from app.providers.notify import NtfyNotifier
from app.security.ratelimit import RateLimiter


class InMemoryCalendar(CalendarProvider):
    name = "memory"

    def __init__(self, busy=None):
        self.busy: list[tuple[datetime, datetime]] = list(busy or [])
        self.events: dict[str, tuple] = {}
        self.down = False

    async def freebusy(self, start, end):
        if self.down:
            raise CalendarUnavailable("down")
        return [(s, e) for s, e in self.busy if s < end and start < e]

    async def create_event(self, event_id, title, start, end, attendee_email=None, description=""):
        if self.down:
            raise CalendarUnavailable("down")
        self.events[event_id] = (title, start, end, attendee_email)
        self.busy.append((start, end))
        return event_id


class ScriptedLLM:
    """Replays raw model outputs in order (then repeats the last). Models a worst-case / adversarially obedient model."""

    def __init__(self, outputs: list):
        self.outputs = [o if isinstance(o, str) else json.dumps(o) for o in outputs]
        self.i = 0
        self.seen: list[list[dict]] = []

    async def chat(self, messages):
        self.seen.append(messages)
        o = self.outputs[min(self.i, len(self.outputs) - 1)]
        self.i += 1
        return o


class NoGuard(LlamaGuard):
    async def check(self, text):
        return False, []


def make_deps(tmp_path, llm, calendar=None, **settings) -> Deps:
    st = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/t.db", owner_handle="owner", owner_name="Sam",
                  owner_timezone="Asia/Kolkata", ntfy_topic="", guard_enabled=False, **settings)
    db = repo.Database(st.database_url)
    repo.ensure_owner(db, st)
    return Deps(st, db, llm, NoGuard(st), calendar or InMemoryCalendar(), NtfyNotifier(st.ntfy_url, ""), RateLimiter(1000))
