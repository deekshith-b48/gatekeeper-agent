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


class OpenAICompatLLM:
    """Any OpenAI-compatible chat API (Groq, Gemini's OpenAI endpoint, OpenRouter, ...). JSON mode, same planner contract."""

    def __init__(self, st: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.st, self.transport, self.breaker = st, transport, CircuitBreaker(3, 20)
        if not st.llm_api_key and transport is None:
            raise RuntimeError("LLM_API_KEY is required when LLM_PROVIDER=openai")

    def _client(self, timeout: float, key: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self.transport, headers={"Authorization": f"Bearer {key}"})

    async def _once(self, model: str, messages: list[dict], base: str | None = None, key: str | None = None) -> str:
        async with self._client(self.st.llm_timeout_s, key or self.st.llm_api_key) as c:
            r = await c.post(f"{(base or self.st.llm_base_url).rstrip('/')}/chat/completions", json={
                "model": model, "messages": messages, "temperature": 0.2, "max_tokens": 300,
                "response_format": {"type": "json_object"}})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    async def chat(self, messages: list[dict]) -> str:
        try:
            return await call_with_retry(lambda: self._once(self.st.llm_model, messages),
                                         timeout=self.st.llm_timeout_s + 5, retries=1, breaker=self.breaker)
        except Exception as e:  # noqa: BLE001
            log.warning("primary model %s failed (%r); trying fallback %s", self.st.llm_model, e, self.st.llm_fallback_model)
        try:
            return await call_with_retry(lambda: self._once(self.st.llm_fallback_model, messages),
                                         timeout=self.st.llm_timeout_s + 5, retries=1)
        except Exception as e:  # noqa: BLE001
            if not self.st.llm2_api_key:
                raise
            log.warning("fallback model %s failed (%r); trying second provider %s", self.st.llm_fallback_model, e, self.st.llm2_model)
        return await call_with_retry(lambda: self._once(self.st.llm2_model, messages, self.st.llm2_base_url, self.st.llm2_api_key),
                                     timeout=self.st.llm_timeout_s + 5, retries=1)

    async def alive(self) -> bool:
        return bool(self.st.llm_api_key)  # don't spend quota on health checks


def build_llm(st: Settings):
    return OpenAICompatLLM(st) if st.llm_provider == "openai" else OllamaLLM(st)


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
                if self.st.llm_provider == "openai":
                    r = await c.post(f"{self.st.llm_base_url.rstrip('/')}/chat/completions",
                                     headers={"Authorization": f"Bearer {self.st.llm_api_key}"},
                                     json={"model": self.st.guard_model, "temperature": 0,
                                           "messages": [{"role": "user", "content": text}]})
                    r.raise_for_status()
                    return r.json()["choices"][0]["message"]["content"].strip().lower()
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
