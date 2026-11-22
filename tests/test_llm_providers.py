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


async def test_second_provider_used_after_primary_provider_fails():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append((req.url.host, body["model"], req.headers["authorization"]))
        if req.url.host == "api.groq.test":
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"say": "from gemini"}'}}]})

    st = Settings(_env_file=None, llm_provider="openai", llm_api_key="g", llm_base_url="https://api.groq.test/v1",
                  llm_model="big", llm_fallback_model="small", llm2_api_key="k2", llm2_base_url="https://gem.test/v1beta/openai", llm2_model="gem")
    out = await OpenAICompatLLM(st, transport=httpx.MockTransport(handler)).chat([{"role": "user", "content": "x"}])
    assert parse_step(out).say == "from gemini"
    assert [s[1] for s in seen if s[0] == "api.groq.test"][0] == "big" and "small" in [s[1] for s in seen]
    assert seen[-1] == ("gem.test", "gem", "Bearer k2")


async def test_no_second_provider_configured_raises():
    import pytest
    st = Settings(_env_file=None, llm_provider="openai", llm_api_key="g", llm_base_url="https://api.groq.test/v1", llm2_api_key="")
    llm = OpenAICompatLLM(st, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(Exception):
        await llm.chat([{"role": "user", "content": "x"}])


async def test_groq_native_tool_call_shape_is_salvaged_and_429_skips_same_model_retry():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        calls.append(body["model"])
        if body["model"] == "big":
            return httpx.Response(429, json={"error": {"message": "rate limit"}})
        assert body.get("reasoning_effort") == "low" or "gpt-oss" not in body["model"]
        return httpx.Response(400, json={"error": {"code": "tool_use_failed", "failed_generation": '{"name": "book_meeting", "arguments": {"slot_id": "abc"}}'}})

    st = Settings(_env_file=None, llm_provider="openai", llm_api_key="k", llm_base_url="https://x.test/v1", llm_model="big", llm_fallback_model="openai/gpt-oss-20b")
    out = parse_step(await OpenAICompatLLM(st, transport=httpx.MockTransport(handler)).chat([{"role": "user", "content": "x"}]))
    assert out.tool == "book_meeting" and out.args == {"slot_id": "abc"}
    assert calls.count("big") == 1  # 429: no second try on the same model


async def test_when_every_model_is_rate_limited_wait_out_the_short_limit_instead_of_failing():
    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] <= 2:  # primary + fallback both limited; primary says 0.3s
            return httpx.Response(429, text='{"error":{"message":"Rate limit reached ... Please try again in 0.3s."}}')
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"say": "recovered"}'}}]})

    st = Settings(_env_file=None, llm_provider="openai", llm_api_key="k", llm_base_url="https://x.test/v1", llm_model="a", llm_fallback_model="b")
    out = await OpenAICompatLLM(st, transport=httpx.MockTransport(handler)).chat([{"role": "user", "content": "x"}])
    assert parse_step(out).say == "recovered" and state["n"] == 3
