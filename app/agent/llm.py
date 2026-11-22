import json
import logging
import re
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ValidationError, model_validator

from app.core.config import Settings
from app.core.resilience import CircuitBreaker, call_with_retry

log = logging.getLogger("llm")


class AgentStep(BaseModel):
    """One planner output: call a tool OR speak to the caller."""
    tool: str | None = None
    args: dict[str, Any] = {}
    say: str | None = None

    @model_validator(mode="after")
    def _one_of(self):
        if self.tool:
            self.say = None
        elif not (self.say and self.say.strip()):
            raise ValueError("either `tool` or non-empty `say` is required")
        return self


class LLM(Protocol):
    async def chat(self, messages: list[dict]) -> str: ...


class OllamaLLM:
    def __init__(self, st: Settings):
        self.st, self.breaker = st, CircuitBreaker(3, 20)
        self.schema = AgentStep.model_json_schema()

    async def _once(self, model: str, messages: list[dict]) -> str:
        async with httpx.AsyncClient(timeout=self.st.llm_timeout_s) as c:
            r = await c.post(f"{self.st.ollama_url}/api/chat", json={
                "model": model, "messages": messages, "stream": False, "format": self.schema,
                "options": {"temperature": 0.2, "num_ctx": 4096}})
            r.raise_for_status()
            return r.json()["message"]["content"]

    async def chat(self, messages: list[dict]) -> str:
        try:
            return await call_with_retry(lambda: self._once(self.st.llm_model, messages),
                                         timeout=self.st.llm_timeout_s + 5, retries=1, breaker=self.breaker)
        except Exception as e:  # noqa: BLE001
            log.warning("primary model %s failed (%r); trying fallback %s", self.st.llm_model, e, self.st.llm_fallback_model)
            return await call_with_retry(lambda: self._once(self.st.llm_fallback_model, messages),
                                         timeout=self.st.llm_timeout_s + 5, retries=1)

    async def alive(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                return (await c.get(f"{self.st.ollama_url}/api/tags")).status_code == 200
        except Exception:  # noqa: BLE001
            return False


def parse_step(raw: str) -> AgentStep:
    """Strict parse; tolerates a markdown fence around the JSON, nothing else."""
    s = raw.strip()
    m = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", s, re.S)
    return AgentStep.model_validate_json(m.group(1) if m else s)


class LlamaGuard:
    """Llama Guard 3 via Ollama. Returns (flagged, reasons). Fails open (rules + code-level tool gating still apply)."""

    def __init__(self, st: Settings):
        self.st = st
        self.breaker = CircuitBreaker(3, 60)

    async def check(self, text: str) -> tuple[bool, list[str]]:
        if not self.st.guard_enabled:
            return False, []

        async def _go():
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(f"{self.st.ollama_url}/api/chat", json={
                    "model": self.st.guard_model, "stream": False,
                    "messages": [{"role": "user", "content": text}], "options": {"temperature": 0}})
                r.raise_for_status()
                return r.json()["message"]["content"].strip().lower()

        try:
            out = await call_with_retry(_go, timeout=25, retries=1, breaker=self.breaker)
        except Exception as e:  # noqa: BLE001
            log.warning("llama-guard unavailable (%r); continuing with rule guard only", e)
            return False, []
        if out.startswith("unsafe"):
            return True, ["llama_guard:" + (out.split("\n")[1] if "\n" in out else "unsafe")]
        return False, []
