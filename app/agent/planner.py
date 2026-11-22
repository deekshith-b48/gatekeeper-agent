import html
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import timezone

from pydantic import ValidationError

from app.agent import fallbacks, prompts
from app.agent.llm import LLM, parse_step
from app.agent.tools import ToolContext, alert_owner, call_tool, REGISTRY
from app.db import repo

log = logging.getLogger("planner")
MAX_STEPS = 6
HISTORY_LIMIT = 24


def wrap_untrusted(text: str) -> str:
    """Caller text is data. Escape so it can't close the tag or fake <tool_result>/<system> blocks."""
    return f"<caller_speech>{html.escape(text, quote=False)}</caller_speech>"


@dataclass
class TurnResult:
    say: str
    events: list[dict] = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    llm_ok: bool = True


class Planner:
    def __init__(self, llm: LLM, role: str = "caller"):
        self.llm, self.role = llm, role

    async def run_turn(self, ctx: ToolContext, history: list[dict], user_text: str | None, hint: str | None = None) -> TurnResult:
        """history: prior {role, content} messages (mutated: this turn is appended)."""
        t0 = time.monotonic()
        system = prompts.caller_system_prompt(ctx.owner, ctx.now_fn().astimezone(ctx.tz), repo.get_focus(ctx.db, ctx.owner.id))
        if user_text is not None:
            history.append({"role": "user", "content": wrap_untrusted(user_text)})
        if hint:
            history.append({"role": "user", "content": f"<system_note>{hint}</system_note>"})
        scratch: list[dict] = []  # this turn's tool round-trips
        repaired, llm_ms = False, 0

        def done(say: str, ok: bool = True) -> TurnResult:
            history.append({"role": "assistant", "content": json.dumps({"say": say})})
            if hint:  # the note was for one turn only
                history[:] = [m for m in history if not m["content"].startswith("<system_note>")]
            del history[:-HISTORY_LIMIT]
            return TurnResult(say, ctx.events[:], {"llm_ms": llm_ms, "turn_ms": int((time.monotonic() - t0) * 1000)}, ok)

        for _ in range(MAX_STEPS):
            msgs = [{"role": "system", "content": system}, *history, *scratch]
            t1 = time.monotonic()
            try:
                raw = await self.llm.chat(msgs)
            except Exception as e:  # noqa: BLE001
                log.error("LLM unavailable: %r", e)
                repo.add_memory(ctx.db, "caller", "message", "LLM unavailable during call; caller may need a callback.", caller_id=ctx.caller.id)
                await alert_owner(ctx, "agent down", "LLM unreachable during a call; caller should be called back.")
                return done(fallbacks.LLM_DOWN, ok=False)
            llm_ms += int((time.monotonic() - t1) * 1000)
            try:
                step = parse_step(raw)
            except (ValidationError, ValueError) as e:
                if repaired:
                    log.warning("planner output invalid twice: %s", raw[:200])
                    return done(fallbacks.UNCLEAR)
                repaired = True
                scratch += [{"role": "assistant", "content": raw[:500]},
                            {"role": "user", "content": f"<system_note>Invalid output ({str(e)[:200]}). Reply with ONE valid JSON object: "
                                                        f'{{"tool": ..., "args": {{...}}}} or {{"say": "..."}}.</system_note>'}]
                continue
            if step.tool is None:
                return done(step.say.strip())
            result = await call_tool(ctx, self.role, step.tool, step.args)
            scratch += [{"role": "assistant", "content": json.dumps({"tool": step.tool, "args": step.args})},
                        {"role": "user", "content": f"<tool_result name=\"{html.escape(step.tool)}\">{json.dumps(result, default=str)}</tool_result>"}]
            if result.get("error") == "calendar_unavailable":
                history += scratch  # keep context so the next model call can phrase the apology
        log.warning("planner hit MAX_STEPS")
        return done(fallbacks.UNCLEAR)
