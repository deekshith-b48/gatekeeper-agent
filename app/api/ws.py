import asyncio
import json
import logging
import time
import uuid

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agent.session import CallSession
from app.db import repo
from app.security.auth import tokens_match

log = logging.getLogger("ws")
router = APIRouter()
_cpu = asyncio.Semaphore(2)  # STT/TTS are CPU-bound; don't let N calls starve each other


async def _send(ws: WebSocket, ev: dict) -> None:
    await ws.send_text(json.dumps(ev))


@router.websocket("/ws/call/{owner_handle}")
async def call(ws: WebSocket, owner_handle: str, caller_id: str = "", token: str = "", server_tts: int = 1):
    app = ws.app.state
    d, st = app.deps, app.deps.st
    ip = ws.client.host if ws.client else "?"
    if st.call_token and not tokens_match(token, st.call_token):
        await ws.close(code=4401)
        return
    if not app.connect_limiter.allow(ip):
        await ws.close(code=4429)
        return
    owner = repo.get_owner(d.db, owner_handle)
    if not owner:
        await ws.close(code=4404)
        return
    await ws.accept()
    # caller_id is a channel-level identifier (phone, device id). Spoken claims never set it.
    caller, conv = repo.start_conversation(d.db, owner, caller_id or f"anon-{uuid.uuid4().hex[:12]}")
    sess = CallSession(d, owner, caller, conv.id)
    status = "ended"
    try:
        await _send(ws, {"type": "session_started", "conversation_id": conv.id, "voice": app.voice is not None})
        r = await sess.open()
        await _reply(ws, app, server_tts, r)

        det = app.voice.new_detector() if app.voice else None
        last_partial = 0.0
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("text") is not None:
                try:
                    j = json.loads(msg["text"])
                except json.JSONDecodeError:
                    await _send(ws, {"type": "error", "code": "bad_json"})
                    continue
                if j.get("type") == "hangup":
                    break
                if j.get("type") == "text":  # typed turn (same pipeline minus STT/TTS)
                    await _send(ws, {"type": "transcript_final", "text": str(j.get("text", ""))[:2000], "lang": ""})
                    await _reply(ws, app, server_tts, await sess.handle_text(str(j.get("text", ""))))
                continue
            pcm = msg.get("bytes")
            if not pcm or det is None:
                if pcm and det is None:
                    await _send(ws, {"type": "error", "code": "voice_unavailable", "message": "Voice dependencies not installed; send text frames."})
                continue
            if len(pcm) > 64000:  # 2 s per frame is already generous
                await _send(ws, {"type": "error", "code": "frame_too_large"})
                continue
            utterances = await asyncio.to_thread(det.feed, pcm)
            if st.stt_partials and det.in_speech and time.monotonic() - last_partial > 1.5 and det.speech_samples > 8000:
                last_partial = time.monotonic()
                async with _cpu:
                    txt, _, _ = await asyncio.to_thread(app.voice.stt.transcribe, det.snapshot())
                if txt:
                    await _send(ws, {"type": "transcript_partial", "text": txt})
            for utt in utterances:
                t0 = time.monotonic()
                async with _cpu:
                    text, lang, stt_ms = await asyncio.to_thread(app.voice.stt.transcribe, utt)
                await _send(ws, {"type": "transcript_final", "text": text, "lang": lang, "stt_ms": stt_ms})
                if not text.strip():
                    continue  # noise / silence: don't burn an LLM turn
                r = await sess.handle_text(text, audio_ms=int(len(utt) / 16))
                r.timings.update(stt_ms=stt_ms, e2e_before_tts_ms=int((time.monotonic() - t0) * 1000))
                await _reply(ws, app, server_tts, r)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("call crashed")
        status = "abandoned"
        try:
            await _send(ws, {"type": "error", "code": "internal"})
        except Exception:  # noqa: BLE001
            pass
    finally:
        sess.close(status)


async def _reply(ws: WebSocket, app, server_tts: int, r) -> None:
    for e in r.events:
        await _send(ws, e)
    if not r.say:
        return
    timings = dict(r.timings)
    audio = None
    if server_tts and app.voice and app.voice.tts:  # browser clients speak for themselves (server_tts=0)
        try:
            async with _cpu:
                audio = await asyncio.to_thread(app.voice.tts.synthesize, r.say)
            timings["tts_ms"] = audio[2]
        except Exception:  # noqa: BLE001
            log.exception("tts failed")
    log.info("turn timings %s", timings)
    await _send(ws, {"type": "agent_text", "text": r.say, "timings": timings})
    if audio:
        await _send(ws, {"type": "agent_audio", "format": "pcm_s16le", "sample_rate": audio[1], "bytes": len(audio[0])})
        await ws.send_bytes(audio[0])
