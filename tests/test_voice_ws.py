"""Real VAD + Whisper + Piper over the WebSocket (LLM scripted). Skipped without the `voice` extra / Piper voice file."""
import json
import os

import numpy as np
import pytest

pytest.importorskip("faster_whisper")
pytest.importorskip("silero_vad")
pytest.importorskip("piper")
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.core.config import Settings
from app.voice import VoiceStack
from tests.support import InMemoryCalendar, ScriptedLLM, make_deps

VOICE = "en_US-lessac-medium"
pytestmark = pytest.mark.skipif(not os.path.exists(f"voices/{VOICE}.onnx"), reason="Piper voice not downloaded")


def test_spoken_turn_roundtrip(tmp_path):
    voice = VoiceStack(Settings(_env_file=None))
    pcm, sr, _ = voice.tts.synthesize("Hello, my name is Priya and I would like to book a meeting tomorrow.")
    x = np.frombuffer(pcm, np.int16).astype(np.float32)
    x16 = np.interp(np.linspace(0, len(x) - 1, int(len(x) * 16000 / sr)), np.arange(len(x)), x).astype(np.int16)
    stream = np.concatenate([np.zeros(8000, np.int16), x16, np.zeros(24000, np.int16)]).tobytes()
    llm = ScriptedLLM([{"say": "Hi, who is calling?"}, {"say": "Thanks Priya, which day works?"}])
    deps = make_deps(tmp_path, llm, InMemoryCalendar())
    with TestClient(create_app(deps.st, deps, voice=voice)) as c, c.websocket_connect("/ws/call/owner?caller_id=t1") as ws:
        seen = []
        def drain_until(kind):
            while True:
                m = ws.receive()
                if m.get("bytes") is not None:
                    seen.append("audio_bytes"); continue
                e = json.loads(m["text"]); seen.append(e["type"])
                if e["type"] == kind:
                    return e
        assert drain_until("agent_text")["text"] == "Hi, who is calling?"
        for i in range(0, len(stream), 2048):
            ws.send_bytes(stream[i:i + 2048])
        tf = drain_until("transcript_final")
        assert "priya" in tf["text"].lower() and tf["lang"] == "en"
        reply = drain_until("agent_text")
        assert reply["text"].startswith("Thanks Priya") and {"stt_ms", "tts_ms", "llm_ms"} <= set(reply["timings"])
        assert "agent_audio" in seen and "audio_bytes" in seen
