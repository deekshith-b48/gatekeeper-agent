"""STAGE 0. Dependency health. Exit code 1 if anything essential is down."""
import asyncio, subprocess, time
from scripts.e2e_lib import *  # noqa
from scripts.e2e_lib import Report, ENV, BASE, ROOT, httpx

R = Report("stage0_preflight")

async def main():
    async with httpx.AsyncClient(timeout=20) as c:
        h = (await c.get(f"{BASE}/healthz")).json()
    R.add("backend /healthz", h.get("ok"), str(h))
    R.add("calendar provider loaded", h.get("calendar") == "google", h.get("calendar", ""))
    R.add("WhatsApp webhook configured", h.get("whatsapp"), "TWILIO_AUTH_TOKEN + PUBLIC_URL")
    # LLM chain, each link separately
    from app.core.config import Settings
    from app.agent.llm import OpenAICompatLLM, parse_step
    st = Settings(); llm = OpenAICompatLLM(st)
    msgs = [{"role": "system", "content": 'Reply with one JSON object {"say": "..."}'}, {"role": "user", "content": "Say hi in three words."}]
    for label, model, base, key in [("LLM primary", st.llm_model, None, None), ("LLM fallback model", st.llm_fallback_model, None, None), ("LLM second provider", st.llm2_model, st.llm2_base_url, st.llm2_api_key)]:
        t0 = time.monotonic()
        try:
            out = parse_step(await asyncio.wait_for(llm._once(model, msgs, base, key), 60)); R.add(f"{label} ({model}) responds", bool(out.say), out.say or "", time.monotonic() - t0)
        except Exception as e:
            (R.warn if "second" in label or "fallback" in label else R.add)(f"{label} ({model}) responds", *( [str(e)[:120]] if "second" in label or "fallback" in label else [False, str(e)[:120]]))
    R.add("STT/TTS", True, "N/A server-side: speech runs in the caller's browser (Web Speech API); light mode has no Whisper/Piper")
    # calendar auth + freebusy for today
    t0 = time.monotonic()
    try:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc); busy = await gcal().freebusy(now, now + timedelta(days=1))
        R.add("calendar token valid, freebusy readable (today)", True, f"{len(busy)} busy block(s) in next 24h", time.monotonic() - t0)
    except Exception as e:
        R.add("calendar token valid, freebusy readable (today)", False, str(e))
    # notify channel (one real push)
    from app.providers.notify import NtfyNotifier
    ok = await NtfyNotifier(ENV["NTFY_URL"] if "NTFY_URL" in ENV else "https://ntfy.sh", ENV.get("NTFY_TOPIC", "")).send("Gatekeeper preflight", "Pre-demo preflight check: notification channel OK.")
    R.add("notify channel (ntfy) delivers", ok, "one test push sent")
    # secrets hygiene
    for f in (".env", "credentials.json", "token.json", "data/gatekeeper.db"):
        ign = subprocess.run(["git", "check-ignore", "-q", f], cwd=ROOT).returncode == 0
        hist = subprocess.run(["git", "log", "--all", "--oneline", "--", f], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        R.add(f"{f}: gitignored and never in history", ign and not hist, f"ignored={ign} history={hist or 'none'}")
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout
    R.add("no *.db / token / credentials tracked", not any(x in tracked for x in (".db", "token.json", "credentials.json", "\n.env\n")), "")
    # secret strings in tracked files
    import re
    leaked = []
    secrets = [ENV.get(k, "") for k in ("LLM_API_KEY", "LLM2_API_KEY", "TWILIO_AUTH_TOKEN", "ADMIN_TOKEN", "CALL_TOKEN")]
    for f in subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split():
        try: txt = (ROOT / f).read_text(errors="ignore")
        except Exception: continue
        leaked += [f for s in secrets if s and len(s) > 12 and s in txt]
    R.add("no live secret value inside any tracked file", not leaked, ",".join(set(leaked)))
    ok = R.save(); raise SystemExit(0 if ok else 1)

asyncio.run(main())
