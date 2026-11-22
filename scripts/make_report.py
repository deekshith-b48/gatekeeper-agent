"""Builds TEST_REPORT.md from reports/*.json (written by the stage scripts) plus a fresh unit-test run."""
import json, re, subprocess, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
stages = [("stage0_preflight", "Stage 0 · Preflight"), ("stage1_happy", "Stage 1 · Happy path (real calendar)"), ("stage2_stranger", "Stage 2 · Stranger simulation"),
          ("stage3_redteam", "Stage 3 · Red team (judged from AuditLog/DB)"), ("stage4_failure", "Stage 4 · Failure injection")]
data = {k: json.loads((ROOT / "reports" / f"{k}.json").read_text()) for k, _ in stages}
ut = subprocess.run([str(ROOT / ".venv/bin/python"), "-m", "pytest", "-q", "-p", "no:cacheprovider", "-W", "ignore"], cwd=ROOT, capture_output=True, text=True).stdout.strip().splitlines()[-1]
lat = [r["latency_s"] for rows in data.values() for r in rows if r.get("latency_s")]
out = [f"# Gatekeeper pre-demo test report\n\nGenerated {time.strftime('%Y-%m-%d %H:%M')} · unit/integration suite: **{ut}**\n"]
out.append("## Summary\n\n| Stage | Pass | Fail | Warn |\n|---|---|---|---|")
for k, t in stages:
    r = data[k]; out.append(f"| {t} | {sum(x['status']=='PASS' for x in r)} | {sum(x['status']=='FAIL' for x in r)} | {sum(x['status']=='WARN' for x in r)} |")
out.append(f"\nTurn latency across all stages: **avg {sum(lat)/len(lat):.1f}s, max {max(lat):.1f}s** ({len(lat)} timed cases). Warn threshold: 5s per turn.\n")
for k, t in stages:
    out.append(f"## {t}\n\n| Case | Result | Latency | Notes |\n|---|---|---|---|")
    for r in data[k]:
        out.append(f"| {r['case']} | {r['status']} | {str(r['latency_s'])+'s' if r['latency_s'] else ''} | {r['detail'].replace('|','/').replace(chr(10),' ')[:140]} |")
    out.append("")
out.append("""## Run notes

- Stage 1's saved run used `gpt-oss-20b` as primary (a short experiment to spread the daily token budget; reverted because it was slower: greeting 3.2 s vs 0.8 s). Earlier 120b runs: booking turn 5.1 s and 6.9 s.
- Stage 3 shows the last healthy results: 8 cases from the final full run, plus "book 20 meetings" from a targeted rerun after that case's follow-up turn hit the provider's rate limit. Every run's safety assertions (no foreign tool executed, no real event title leaked, no malicious name stored, booking cap) passed.
- Stage 4 starts extra server instances on ports 8011-8013 with their own databases, so the main server and its data are never touched.

## What the pass found and fixed

| # | Finding | Fix |
|---|---|---|
| 1 | Booking turn took **13.5 s**: Groq 400/429 were retried against the same model, then fell through to a slow Gemini | 4xx are no longer retried; 429 moves to the next model immediately; Groq's invalid-JSON output (`failed_generation`, incl. gpt-oss native `{"name","arguments"}` shape) is salvaged; `reasoning_effort=low`; short rate limits (<=7 s) are waited out instead of failing the turn. Booking turn now ~5 s when the provider is healthy |
| 2 | **Unsafe path:** nothing stopped a caller booking many slots ("book 20 meetings") | Max 2 bookings per call and 3 per caller per 24 h, enforced in `book_meeting` |
| 3 | **Race:** two callers could take the same slot (check-then-insert not atomic) | Booking is serialized by a lock around the freebusy re-check + insert; proven on the real calendar (one event, loser re-offered) |
| 4 | **Stored prompt injection:** caller name and saved notes were pasted into the *system* prompt | Caller record is now an escaped, quoted data message; names/titles must be plain text and are checked by the injection guard |
| 5 | Every visitor behind the tunnel looked like one IP, so the 10/min connect limit applied to the whole room | Real client IP from `CF-Connecting-IP`; limit raised to 30/min per visitor |
| 6 | WebSocket rejections (bad token, unknown owner, rate limit) were opaque HTTP 403s, so the UI showed "connection lost" | Socket is accepted then closed with 4401/4404/4429 so the UI says why |
| 7 | Owner push notification (ntfy) blocked the booking reply by ~1 s | Push now runs in the background; failures stay undelivered and are retried in the next digest |
| 8 | Agent repeated a booking confirmation instead of saving a message asked for right after booking | Prompt rule + compacted prompt (~760 to ~500 tokens) |

## Known issues and risks (read before the demo)

- **Groq free-tier token limits are the main risk.** `gpt-oss-120b`: 8,000 tokens/min and 200,000 tokens/day. Heavy testing today used up most of the daily budget (two live runs of the 28-attack suite plus these stages). When both Groq models are limited the chain falls back to Gemini, which showed 503 "high demand" spikes; if everything fails the caller gets the safe fallback ("I'll take a message") and the owner is alerted. **Do not run live-LLM suites in the hour before the demo.**
- The last full Stage 3 run had one case fail only on its *follow-up* turn because the provider was rate-limiting (safety assertions for that case still passed); a rerun of that case alone passed. Tests now classify "LLM unavailable" as WARN, never as a safety result.
- Booking turn latency is 5 to 10 s under load (two model calls + calendar re-check + insert); simple turns are 1 to 3 s.
- Not applicable in this deployment: Ollama and server-side STT/TTS (light mode uses a hosted LLM and the browser's speech). There is no server-side silence timer; abandoned chats close via hourly housekeeping.
- The voice-pipeline unit test is skipped (heavy `voice` extra not installed). WhatsApp is **not yet verified with a real WhatsApp message**: the Twilio sandbox webhook still has to be set (needs a Twilio console sign-in) and the tunnel URL changes on every restart.
- The app has no event-delete function by design (callers can only book). Test events are removed by the test scripts only, keyed on the test caller's id in the event description.
- Focus mode ON holds routine pushes until Focus is turned OFF: if you want a live buzz on booking during the demo, turn Focus OFF first.
- Rate limiter and live-event bus are in-process (single server instance).

## Demo safety: do this before 4:30 PM

1. Start the server: `.venv/bin/python -m app.api.main`. Start the tunnel: `cloudflared tunnel --url http://localhost:8000`, then put the new address in `.env` as `PUBLIC_URL` and in the Twilio sandbox webhook (`/webhook/whatsapp`), and restart the server.
2. Reset state and warm models: `.venv/bin/python -m scripts.reset_demo --focus on` (or `--focus off`). It deletes only test callers (`e2e-`, `stage-`, `selftest`) and their rows, removes any leftover `e2e` calendar events, sets Focus, and sends one tiny request to every model plus a freebusy read.
3. Preflight (does not use the LLM much): `.venv/bin/python -m scripts.preflight` must be all PASS (it sends one ntfy test push).
4. Check Google Calendar by eye for any `[TEST]` event; there should be none. Confirm the ntfy app is subscribed and Android battery optimisation is off for it.
5. Open `/stage?admin=<ADMIN_TOKEN>` on the demo laptop, press Play once only if the LLM budget allows. Keep the flask icon on *demo* so no real events are written. For the stranger test use `/?token=<CALL_TOKEN>` on the phone (real booking; delete the event afterwards in Google Calendar).
6. Fallback if the LLM is rate-limited on stage: show the red-team lab results and the dashboard test rings (no LLM needed), and explain that the agent degrades to "take a message" + alert by design.
7. After the demo rotate `ADMIN_TOKEN`, `CALL_TOKEN`, the Groq, Gemini and Twilio credentials.
""")
(ROOT / "TEST_REPORT.md").write_text("\n".join(out)); print("wrote TEST_REPORT.md", ut)
