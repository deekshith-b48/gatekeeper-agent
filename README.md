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

## The two-sided live demo: `/stage`
One screen, both sides: a **caller phone** (built-in WhatsApp-style chat; voice, typed or tapped) on the left, the **owner phone** (focus switch + inbox, updating live) on the right, and a **Play demo** button in the middle that runs: Focus ON → caller asks for slots → attack ("cancel all his meetings") is blocked → booking → a message is held → Focus OFF → one digest push. Demo mode reads the real calendar but does not write bookings (toggle the flask icon for real ones). Pages: `/` chat, `/inbox` owner, `/showcase` dashboard + tests + red-team lab, `/stage` both sides.

## Focus mode + phone inbox (Android / any phone)
While **Focus is ON**, Gatekeeper still answers callers, but routine alerts (bookings, messages) are *held*. URGENT ones (VIP contact id, or 2+ contacts in 7 days) still ring immediately.
When Focus turns **OFF**, you get **one** push, "Focus ended: 2 messages, 1 booking", and tapping it opens your **inbox** (`/inbox`): unread list, conversation transcripts, 📞 call back, ✉ email, mark read.

Setup (once):
1. Install the **ntfy** app and subscribe to your `NTFY_TOPIC`.
2. Set `PUBLIC_URL` in `.env` to the address your phone reaches (the tunnel URL) so the push can open the inbox.
3. On the phone open `PUBLIC_URL/inbox?admin=<ADMIN_TOKEN>` once, then browser menu → *Install app / Add to Home screen*. The token is stored on that phone only.
4. Turn focus on/off, either:
   - **Manually:** the switch at the top of the inbox.
   - **Automatically with your phone's Focus / Do Not Disturb:** in MacroDroid, Tasker or HTTP Shortcuts, trigger on "Do Not Disturb turns on/off" and make an HTTP GET to
     `PUBLIC_URL/hook/focus/on?key=<ADMIN_TOKEN>` (and `/off`). `toggle` also works. Whether your phone's own Focus mode can trigger an automation depends on the phone and app; Do Not Disturb is the most widely supported trigger.

A free quick tunnel gets a new address each restart, so update `PUBLIC_URL` and your automation URLs then. For a permanent address use a named Cloudflare tunnel or Tailscale.

## WhatsApp (Twilio) and live events
Messages to your Twilio WhatsApp number reach the **same agent, tools and guard** as voice. The webhook is `POST PUBLIC_URL/webhook/whatsapp`; every request's `X-Twilio-Signature` is verified (cross-checked in tests against Twilio's own validator), retries are de-duplicated by `MessageSid`, each sender keeps a conversation, and the sender's WhatsApp number is the verified caller id (so `OWNER_VIP_CONTACTS=+91…` works; spoken claims never do). Replies go back inline; if the model is slow they are pushed over the REST API.

Setup: Twilio Console → Messaging → *Try it out* → *Send a WhatsApp message* (sandbox). Put `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, the sandbox number as `TWILIO_WHATSAPP_FROM` and its join word as `WHATSAPP_JOIN_CODE` in `.env`, set `PUBLIC_URL` to your tunnel address, restart, and in the sandbox settings set *When a message comes in* to `PUBLIC_URL/webhook/whatsapp` (POST). Send `join <code>` from your WhatsApp once, then chat. The showcase shows a QR that opens WhatsApp with the join message prefilled.

`GET /api/stream?key=<ADMIN_TOKEN>` is a server-sent-events feed of every turn and alert; the inbox and the showcase "Live feed" use it to update instantly.

## Live showcase for judges: `/showcase`
Open `http://localhost:8000/showcase` (token via `?admin=<ADMIN_TOKEN>`, then stripped from the URL). It has:
- **Test dashboard**: runs the real pytest suite on the server and shows every test by category, plus 8 use-case cards that go green only when their real tests pass. "Run incl. live LLM red-team" replays all 28 attacks against the real configured model chain.
- **Red-team lab**: send any text through the real pipeline (real LLM, real guard, real calendar reads; writes are dry-run). Shows the guard verdict, every tool call the model attempted (allowed/denied), the reply and a safety scorecard. Dynamic-date scenarios (find slots, book the first slot, 2-hour request, 6am request) exercise the booking workflow.
- **Live data**: conversations, bookings, tool-call counts/latency and blocked attacks from the real database.

Last full run: 104 passed, 1 skipped (voice pipeline test, needs the heavy `voice` extra). The real-calendar integration test creates and deletes a temporary event when `TEST_CALENDAR_ID` is set.

## Light mode: small disk, works on phone and laptop (recommended)
No Ollama, no Whisper/Piper/torch (those were ~1.3 GB here plus 2-6 GB of models). The **browser** does speech-to-text and text-to-speech; the LLM is a free hosted API.

| | Light mode | Local mode |
|---|---|---|
| Disk | ~250 MB venv, 0 GB models | 1.3 GB venv + 2 to 6 GB models |
| LLM | free API key (Groq / Gemini / OpenRouter) via `LLM_PROVIDER=openai` | Ollama (`LLM_PROVIDER=ollama`) |
| Speech | browser Web Speech API (Chrome, Edge, Safari) | faster-whisper + Piper on the server |
| Privacy | caller text goes to the API provider; check their free-tier data policy | everything stays on your machine |

```bash
python3.11 -m venv .venv && .venv/bin/pip install -e .          # no [voice] extra
cp .env.example .env     # set LLM_API_KEY, OWNER_*, CALL_TOKEN, NTFY_TOPIC
.venv/bin/python scripts/google_auth.py                           # once (needs credentials.json); writes token.json
.venv/bin/python -m app.api.main                                  # open http://localhost:8000 on the laptop
```
**Phone:** mobile browsers only allow the microphone on HTTPS, so expose the laptop with a free tunnel (set `CALL_TOKEN` first):
```bash
brew install cloudflared && cloudflared tunnel --url http://localhost:8000   # prints https://<random>.trycloudflare.com
```
Open that URL on your phone (add `?token=<CALL_TOKEN>`; works as an installable home-screen page). Tailscale (`tailscale serve`) is the private alternative.
Phone call quality depends on the browser: Chrome on Android is best; iOS Safari supports speech recognition but is less reliable, and typing always works.
To keep it running without your laptop, deploy the same container to any free host (token.json goes in as a secret); free tiers sleep when idle, so the first call may be slow.

**Free disk, if you already installed the heavy stuff:**
```bash
.venv/bin/pip uninstall -y torch torchaudio faster-whisper silero-vad piper-tts onnxruntime ctranslate2   # ~700 MB
rm -rf voices ~/.cache/huggingface/hub/*whisper*                                                          # ~200 MB
```

## Run it (local mode: server-side voice + Ollama)
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
