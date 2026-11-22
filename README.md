# Gatekeeper

A voice agent backend that answers people trying to reach you during focus time, checks your **real** calendar, and books meetings
instead of interrupting you. Free and open source end to end: Ollama (Qwen + Llama Guard), faster-whisper, Piper, silero-vad, ntfy.

```
browser/CLI mic ──16kHz PCM──► /ws/call/{owner}
   silero-vad (utterance end) → faster-whisper → input guard (rules + Llama Guard 3) → planner loop (Ollama, ≤6 steps)
        → role-gated tools → Google Calendar / CalDAV (freebusy → slot_ids → re-check → insert)
        → Piper TTS ──PCM──► speaker          + JSON events: transcript_final, agent_text, slots_offered, booking_confirmed, error
   owner alerts → ntfy (URGENT for VIP contact ids or 2+ contacts in 7 days)
```

## Run it
```bash
cp .env.example .env            # set OWNER_*, NTFY_TOPIC, ...
python3.11 -m venv .venv && .venv/bin/pip install -e ".[cli]" && .venv/bin/python scripts/google_auth.py   # once, on a machine with a browser (needs credentials.json)
docker compose up --build       # Ollama + model pulls + backend on :8000   (first start downloads models)
.venv/bin/python scripts/call_cli.py --owner owner      # talk to it (headphones!)
```
`credentials.json` and `token.json` must exist before `docker compose up` (they are bind-mounted). For CalDAV instead:
`CALENDAR_PROVIDER=caldav`, set `CALDAV_*`, and `docker compose --profile caldav up` for a local Radicale.

Without the `voice` extra the backend still runs in **text mode**: send `{"type":"text","text":"..."}` frames on the WebSocket.

## Layout
`app/api` REST+WS · `app/agent` planner, tools, prompts, slots · `app/voice` vad/stt/tts · `app/providers` calendar (Google, CalDAV), ntfy ·
`app/db` models+repo · `app/security` guards, auth, rate limits · `app/core` config, logging, resilience.

## Safety model (enforced in code)
- **Role-gated tools.** Caller sessions get exactly `get_free_slots`, `book_meeting`, `take_message`, `recall_caller`. They are the only tools shown to the
  model and the only ones `call_tool` resolves for the caller role; anything else is denied and audited. No delete/update/read-event function exists.
  Owner tools are Phase 3 and live in a separate registry that WebSocket sessions never use.
- **No invented times.** `get_free_slots` computes real availability (freebusy + owner rules: working hours, min start, max length, buffer) and stores
  `slot_id`s server-side. `book_meeting` accepts only a single-use, unexpired `slot_id` issued in *this* conversation, re-checks freebusy, and inserts with an
  idempotent event id (a retry can't double-book). Extra args (e.g. a model-supplied start time) are rejected.
- **Untrusted input.** Caller speech is wrapped in `<caller_speech>` (escaped) and never enters the system prompt. Before the planner sees it: length cap,
  rule-based injection detector (overrides, role claims, calendar probing, fake tool calls, tag injection, unicode tricks) and Llama Guard 3.
  Flagged text is refused (reply generated without the text), audited, and kept out of history. Rules are defence in depth; the tool gating is the guarantee.
- **VIP is not claimable.** Trust is matched on the channel `caller_id` (`OWNER_VIP_CONTACTS`), never on what the caller says.
- **Failure handling.** Every external call has a timeout, 2 retries with exponential backoff and a circuit breaker. Calendar down → message saved, owner alerted,
  caller told honestly. LLM down → message saved, owner alerted, short canned apology (`app/agent/fallbacks.py`: the only canned text in the app).
- Rate limits per IP (connections) and per conversation (turns). `ADMIN_TOKEN` empty disables admin REST; `CALL_TOKEN` gates calls. Every tool call lands in `AuditLog`.

## Tests
```bash
.venv/bin/pytest                         # unit + adversarial (worst-case model) + WS API; no GPU, no network
REDTEAM_LIVE=1 .venv/bin/pytest -k live  # same 28 attacks against real Ollama + Llama Guard
GATEKEEPER_TEST_CALENDAR_ID=<dedicated test calendar> .venv/bin/pytest tests/test_integration_calendar.py   # real Google Calendar
```
`tests/redteam.yaml` has 28 cases. The default suite swaps in a *maximally obedient* scripted model that tries owner tools and fake slot ids after every message,
and asserts zero foreign tools executed and zero events created. Test doubles live only under `tests/`.

## Status / honest limits
Phase 1. Latency target (<3 s/turn) depends on your CPU: the 7B model is the bottleneck, use `LLM_MODEL=qwen2.5:3b` on small machines. Per-stage timings are logged and
returned in every `agent_text` event. Rate limiting and circuit breakers are in-process (single instance).
