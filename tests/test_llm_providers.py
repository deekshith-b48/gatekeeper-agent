import json

import httpx

from app.agent.llm import OpenAICompatLLM, build_llm, parse_step
from app.core.config import Settings


async def test_openai_compat_request_shape_and_fallback():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        calls.append((str(req.url), body["model"], req.headers["authorization"], body["response_format"]))
        if body["model"] == "big":
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"say": "hi"}'}}]})

    st = Settings(_env_file=None, llm_provider="openai", llm_api_key="k", llm_base_url="https://api.example/v1", llm_model="big", llm_fallback_model="small")
    llm = OpenAICompatLLM(st, transport=httpx.MockTransport(handler))
    out = await llm.chat([{"role": "user", "content": "x"}])
    assert parse_step(out).say == "hi"
    assert calls[0][:3] == ("https://api.example/v1/chat/completions", "big", "Bearer k")
    assert calls[-1][1] == "small" and calls[-1][3] == {"type": "json_object"}  # rate-limited primary -> fallback model


def test_provider_selection_and_key_required():
    import pytest
    assert type(build_llm(Settings(_env_file=None))).__name__ == "OllamaLLM"
    with pytest.raises(RuntimeError):
        build_llm(Settings(_env_file=None, llm_provider="openai"))
    assert type(build_llm(Settings(_env_file=None, llm_provider="openai", llm_api_key="k"))).__name__ == "OpenAICompatLLM"
