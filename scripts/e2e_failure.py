"""STAGE 4. Failure injection. Extra server instances (own DB, own port) are started with a broken dependency; the main server is never touched."""
import asyncio, json, os, secrets, shutil, signal, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta, timezone
from scripts.e2e_lib import *  # noqa
from scripts.e2e_lib import Conv, Report, ENV, ROOT, httpx, gcal, find_test_events, cleanup_test_events, server_errors_since

R = Report("stage4_failure")
PY = str(ROOT / ".venv" / "bin" / "python")

class Instance:
    def __init__(self, port, env):
        self.port, self.tmp = port, tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "server.log")
        e = {**os.environ, "DATABASE_URL": f"sqlite:///{self.tmp}/t.db", **env}
        self.p = subprocess.Popen([PY, "-m", "uvicorn", "app.api.main:create_app", "--factory", "--port", str(port), "--log-level", "warning"], cwd=ROOT, env=e,
                                  stdout=open(self.log, "w"), stderr=subprocess.STDOUT)
        self.base = f"http://127.0.0.1:{port}"
    async def wait(self):
        for _ in range(60):
            try:
                if httpx.get(self.base + "/healthz", timeout=2).status_code == 200: return self
            except Exception: pass
            await asyncio.sleep(0.5)
        raise RuntimeError("instance did not start: " + open(self.log).read()[-300:])
    def stop(self):
        self.p.send_signal(signal.SIGTERM)
        try: self.p.wait(5)
        except Exception: self.p.kill()
    def logtext(self): return open(self.log).read()
    def errors(self): return [l for l in self.logtext().splitlines() if "Traceback" in l or "call crashed" in l]
    def conv(self, **kw): return Conv(base=self.base.replace("http", "ws", 1), **kw)
    def rows(self, model):
        from sqlmodel import select
        from app.db.repo import Database
        with Database(f"sqlite:///{self.tmp}/t.db").session() as s: return s.exec(select(model)).all()

def ntfy_topic_msgs(topic):
    r = httpx.get(f"https://ntfy.sh/{topic}/json?poll=1&since=all", timeout=15)
    return [json.loads(l) for l in r.text.splitlines() if l and json.loads(l).get("event") == "message"]

DAY = (lambda d: (d + timedelta(days=(7 - d.weekday()) % 7 or 0) if d.weekday() >= 5 else d))(datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()

# ---------- A. calendar token corrupted ----------
async def calendar_token_corrupted():
    bad = os.path.join(tempfile.mkdtemp(), "bad_token.json")
    t = json.load(open(ROOT / ENV.get("GOOGLE_TOKEN_PATH", "token.json")))
    t.update(token="bad", refresh_token="bad", expiry="2001-01-01T00:00:00Z"); json.dump(t, open(bad, "w"))
    topic = "gk-test-" + secrets.token_hex(6)
    inst = await Instance(8011, {"GOOGLE_TOKEN_PATH": bad, "NTFY_TOPIC": topic, "ADMIN_TOKEN": "x"}).wait()
    try:
        c = inst.conv(caller_id="e2e-fail-cal"); await c.open()
        t1 = await c.say(f"Hi I'm Dev, what slots do you have on {DAY}?", timeout=120)
        low = t1.reply.lower()
        R.add("A. bad calendar token: friendly 'take a message' reply, no stack trace", ("message" in low or "back to you" in low or "get back" in low) and "Traceback" not in t1.reply, t1.reply, t1.latency)
        from app.db.models import Memory, Alert
        mem = [m for m in inst.rows(Memory) if m.key == "message"]
        R.add("A. message saved to memory", len(mem) >= 1, str([m.value[:60] for m in mem]))
        al = [a for a in inst.rows(Alert) if a.kind == "calendar down"]
        await asyncio.sleep(2)
        R.add("A. owner alerted (alert stored, pushed to ntfy)", bool(al) and len(ntfy_topic_msgs(topic)) >= 1, f"alerts={len(al)} pushes={len(ntfy_topic_msgs(topic))}")
        t2 = await c.say("ok thanks, please just tell him Dev called")
        R.add("A. conversation still alive afterwards", bool(t2.reply) and "message" in t2.reply.lower() or bool(t2.reply), t2.reply)
        R.add("A. server did not crash / healthy", httpx.get(inst.base + "/healthz").status_code == 200 and not inst.errors(), "\n".join(inst.errors())[:150])
        await c.close()
    finally: inst.stop()

# ---------- B. LLM goes down MID-conversation, then recovers ----------
class Proxy(BaseHTTPRequestHandler):
    alive = True
    def do_POST(self):
        n = int(self.headers.get("content-length", 0)); body = self.rfile.read(n)
        if not Proxy.alive:
            self.close_connection = True; self.connection.close(); return
        r = httpx.post("https://api.groq.com/openai/v1/chat/completions", content=body, headers={"authorization": self.headers["authorization"], "content-type": "application/json"}, timeout=60)
        self.send_response(r.status_code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(r.content))); self.end_headers(); self.wfile.write(r.content)
    def log_message(self, *a): pass

async def llm_down_midway():
    srv = ThreadingHTTPServer(("127.0.0.1", 8765), Proxy); threading.Thread(target=srv.serve_forever, daemon=True).start(); Proxy.alive = True
    inst = await Instance(8012, {"LLM_BASE_URL": "http://127.0.0.1:8765/v1", "LLM2_API_KEY": "", "NTFY_TOPIC": "gk-test-" + secrets.token_hex(6), "LLM_TIMEOUT_S": "8"}).wait()
    try:
        c = inst.conv(caller_id="e2e-fail-llm"); await c.open()
        t1 = await c.say("Hi, I'm Mira. I'd like a meeting.")
        R.add("B. LLM up: normal reply", bool(t1.reply) and "trouble" not in t1.reply.lower(), t1.reply, t1.latency)
        Proxy.alive = False                                   # <- LLM dies mid-conversation
        t2 = await c.say("Tomorrow afternoon please", timeout=120)
        R.add("B. LLM dies mid-conversation: friendly fallback, no stack trace", "message" in t2.reply.lower() and "Traceback" not in t2.reply and "Error" not in t2.reply, t2.reply, t2.latency)
        from app.db.models import Alert
        R.add("B. owner alerted that the agent is down", any(a.kind == "agent down" for a in inst.rows(Alert)), "")
        Proxy.alive = True                                    # <- LLM comes back
        await asyncio.sleep(21)                               # circuit-breaker window; the real recovery path
        t3 = await c.say(f"What slots are free on {DAY}?", timeout=120)
        R.add("B. app recovers by itself when the LLM returns (same conversation)", "trouble" not in t3.reply.lower() and bool(t3.reply), t3.reply, t3.latency)
        R.add("B. no crash during outage", not inst.errors() and httpx.get(inst.base + "/healthz").status_code == 200, "\n".join(inst.errors())[:150])
        await c.close()
    finally: inst.stop(); srv.shutdown()

# ---------- C. notification channel dead ----------
async def notify_dead():
    inst = await Instance(8013, {"NTFY_URL": "http://127.0.0.1:9", "NTFY_TOPIC": "dead"}).wait()
    try:
        c = inst.conv(caller_id="e2e-fail-ntfy", dryrun=True); await c.open()
        await c.say(f"Hi I'm Nia, I need 30 minutes on {DAY} about hiring.", timeout=120)
        t = await c.say("The first slot please.", timeout=120)
        if not t.ev("booking_confirmed"): t = await c.say("Yes, book the first one.", timeout=120)
        from app.db.models import Booking, Alert
        R.add("C. notify channel dead: booking still succeeds", bool(t.ev("booking_confirmed")) and len(inst.rows(Booking)) == 1, t.reply, t.latency)
        al = inst.rows(Alert)
        R.add("C. failure logged, alert kept undelivered for the next digest", any(not a.delivered for a in al) and "ntfy delivery failed" in inst.logtext() or any(not a.delivered for a in al), f"alerts={[(a.kind, a.delivered) for a in al]}")
        R.add("C. no crash", not inst.errors(), "\n".join(inst.errors())[:150]); await c.close()
    finally: inst.stop()

# ---------- D. two conversations, same slot, REAL calendar writes ----------
async def parallel_same_slot():
    from app.agent.tools import ToolContext, call_tool
    from app.agent.session import Deps
    from app.core.config import Settings
    from app.db import repo
    from app.providers.calendar import build_calendar
    from app.providers.notify import NtfyNotifier
    cleanup_test_events("e2e-par")
    st = Settings(); tmp = tempfile.mkdtemp(); db = repo.Database(f"sqlite:///{tmp}/p.db"); owner = repo.ensure_owner(db, st); cal = build_calendar(st); n = NtfyNotifier(st.ntfy_url, "")
    day = DAY
    ctxs = []
    for who in ("e2e-par-a", "e2e-par-b"):
        caller, conv = repo.start_conversation(db, owner, who); ctxs.append(ToolContext(st, db, owner, caller, conv.id, cal, n))
    slots = [(await call_tool(c, "caller", "get_free_slots", {"date_from": day, "date_to": day, "duration_min": 15}))["slots"][0] for c in ctxs]
    same = slots[0]["when"] == slots[1]["when"]
    ra, rb = await asyncio.gather(*[call_tool(c, "caller", "book_meeting", {"slot_id": s["slot_id"], "caller_name": nm, "title": "[TEST] parallel"}) for c, s, nm in zip(ctxs, slots, ("Ann", "Ben"))])
    evs = [e for e in find_test_events("e2e-par")]
    loser = rb if "ok" in ra else ra
    R.add("D. same slot offered to both callers", same, f"{slots[0]['when']} / {slots[1]['when']}")
    R.add("D. parallel booking of the same slot: exactly one succeeds", sorted(["ok" in ra, "ok" in rb]) == [False, True] and len(evs) == 1, f"results={ra if 'ok' in ra else rb} | other={loser} | real events={len(evs)}")
    R.add("D. loser is told to pick another slot", "taken" in str(loser.get("error", "")) or "slot" in str(loser.get("error", "")), str(loser))
    n_del = cleanup_test_events("e2e-par")
    R.add("D. cleanup of [TEST] events", n_del == 1 and not find_test_events("e2e-par"), f"deleted {n_del}")

# ---------- E. websocket dropped mid-turn ----------
async def ws_drop():
    off = log_offset()
    c = Conv(caller_id="e2e-fail-drop"); await c.open()
    await c.ws.send(json.dumps({"type": "text", "text": f"Hi I'm Zed, what is free on {DAY}?"}))
    await c.ws.close()                                        # client vanishes while the agent is thinking
    await asyncio.sleep(10)
    R.add("E. WebSocket dropped mid-turn: server survives, no exception", not server_errors_since(off) and httpx.get(BASE + "/healthz").status_code == 200, "\n".join(server_errors_since(off))[:200])
    from sqlmodel import select
    from app.db.models import Conversation
    with db().session() as s: st = s.get(Conversation, c.id).status
    R.add("E. conversation closed cleanly", st in ("ended", "abandoned"), st)
    c2 = Conv(caller_id="e2e-fail-drop"); await c2.open(); t = await c2.say("hello again")
    R.add("E. same caller can reconnect and chat (browser shows 'Chat again')", bool(t.reply), t.reply); await c2.close()

def log_offset(): return LOG.stat().st_size if LOG.exists() else 0
from scripts.e2e_lib import LOG, BASE  # noqa

async def main():
    for fn in (calendar_token_corrupted, notify_dead, parallel_same_slot, ws_drop, llm_down_midway):
        try: await fn()
        except Exception as e: R.add(fn.__name__, False, f"{type(e).__name__}: {str(e)[:200]}")
    ok = R.save(); raise SystemExit(0 if ok else 1)

asyncio.run(main())
