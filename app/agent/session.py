import logging
import time
from dataclasses import dataclass, field

from app.agent import fallbacks, prompts
from app.agent.llm import LlamaGuard, LLM
from app.agent.planner import Planner, TurnResult
from app.agent.tools import ToolContext
from app.core.config import Settings
from app.db import repo
from app.db.models import Caller, Owner
from app.db.repo import Database
from app.providers.calendar.base import CalendarProvider
from app.providers.notify import NtfyNotifier
from app.security.guards import normalize, rule_check
from app.security.ratelimit import RateLimiter

log = logging.getLogger("session")


@dataclass
class Deps:
    st: Settings
    db: Database
    llm: LLM
    guard: LlamaGuard
    calendar: CalendarProvider
    notifier: NtfyNotifier
    turn_limiter: RateLimiter


@dataclass
class CallSession:
    deps: Deps
    owner: Owner
    caller: Caller
    conversation_id: str
    role: str = "caller"  # WS sessions are always "caller"; owner role arrives in Phase 3
    history: list[dict] = field(default_factory=list)

    def _ctx(self) -> ToolContext:
        d = self.deps
        return ToolContext(d.st, d.db, self.owner, self.caller, self.conversation_id, d.calendar, d.notifier)

    async def open(self) -> TurnResult:
        r = await Planner(self.deps.llm, self.role).run_turn(self._ctx(), self.history, None, prompts.greeting_hint(self.owner))
        repo.add_message(self.deps.db, self.conversation_id, "agent", r.say)
        return r

    async def handle_text(self, text: str, audio_ms: int = 0) -> TurnResult:
        d, t0 = self.deps, time.monotonic()
        text = (text or "").strip()
        if not d.turn_limiter.allow(self.conversation_id):
            return TurnResult("", [{"type": "error", "code": "rate_limited", "message": "Too many messages, slow down."}])
        if not normalize(text):  # silence / empty
            r = await Planner(d.llm, self.role).run_turn(self._ctx(), self.history, None, prompts.silence_hint())
            repo.add_message(d.db, self.conversation_id, "agent", r.say)
            return r
        repo.add_message(d.db, self.conversation_id, "caller", text[:2000], audio_ms)

        t1 = time.monotonic()
        v = rule_check(text, d.st.max_input_chars)
        reasons = list(v.reasons)
        if not v.flagged:  # don't spend a guard-model call on text already blocked
            flagged, why = await d.guard.check(text)
            if flagged:
                v.flagged, reasons = True, why
        guard_ms = int((time.monotonic() - t1) * 1000)
        if v.flagged:
            repo.audit(d.db, self.conversation_id, self.role, "input_guard", {"reasons": reasons, "chars": len(text)}, {"action": "refused"})
            r = await Planner(d.llm, self.role).run_turn(self._ctx(), self.history, None, prompts.refusal_hint())
            r.events.append({"type": "guard_flagged", "reasons": reasons})
        else:
            r = await Planner(d.llm, self.role).run_turn(self._ctx(), self.history, text)
        r.timings["guard_ms"] = guard_ms
        r.timings["total_ms"] = int((time.monotonic() - t0) * 1000)
        repo.add_message(d.db, self.conversation_id, "agent", r.say)
        return r

    def close(self, status: str = "ended") -> None:
        repo.end_conversation(self.deps.db, self.conversation_id, status)
