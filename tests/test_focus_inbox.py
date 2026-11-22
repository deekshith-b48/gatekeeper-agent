import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.agent.alerts import flush_digest
from app.agent.tools import ToolContext, alert_owner
from app.api.main import create_app
from app.db import repo
from app.db.models import Alert
from app.providers.notify import NtfyNotifier
from tests.support import InMemoryCalendar, ScriptedLLM, make_deps


class Recorder(NtfyNotifier):
    """Real notifier interface; records instead of hitting the network."""
    def __init__(self, ok=True):
        super().__init__("http://x", "t")
        self.sent, self.ok = [], ok

    async def send(self, title, message, urgent=False, click=""):
        self.sent.append({"title": title, "message": message, "urgent": urgent, "click": click})
        return self.ok


def ctx(deps, owner, caller, conv, notifier):
    return ToolContext(deps.st, deps.db, owner, caller, conv.id, InMemoryCalendar(), notifier)


def setup(tmp_path, contact="c1", notifier=None):
    deps = make_deps(tmp_path, ScriptedLLM([{"say": "x"}]))
    owner = repo.get_owner(deps.db, "owner")
    caller, conv = repo.start_conversation(deps.db, owner, contact)
    return deps, owner, caller, conv, notifier or Recorder()


async def test_focus_off_pushes_immediately(tmp_path):
    deps, owner, caller, conv, n = setup(tmp_path)
    _t = await alert_owner(ctx(deps, owner, caller, conv, n), "message", "call me")
    if _t:
        await _t
    assert len(n.sent) == 1 and not n.sent[0]["urgent"]
    assert repo.undelivered_alerts(deps.db, owner.id) == []


async def test_focus_on_holds_routine_but_not_urgent(tmp_path):
    deps, owner, caller, conv, n = setup(tmp_path)
    repo.set_focus(deps.db, owner.id, True)
    _t = await alert_owner(ctx(deps, owner, caller, conv, n), "message", "routine")
    if _t:
        await _t
    assert n.sent == [] and len(repo.undelivered_alerts(deps.db, owner.id)) == 1  # held, but stored for the inbox
    repo.start_conversation(deps.db, owner, "c1")  # 2nd contact within 7 days => urgent
    _t = await alert_owner(ctx(deps, owner, caller, conv, n), "message", "again")
    if _t:
        await _t
    assert len(n.sent) == 1 and n.sent[0]["urgent"]


async def test_focus_end_sends_one_digest_with_inbox_link(tmp_path):
    deps, owner, caller, conv, n = setup(tmp_path)
    repo.set_focus(deps.db, owner.id, True)
    c = ctx(deps, owner, caller, conv, n)
    _t = await alert_owner(c, "booking", "booked Monday 12:00")
    if _t:
        await _t
    _t = await alert_owner(c, "message", "please call back")
    if _t:
        await _t
    assert n.sent == []
    assert await flush_digest(deps.db, n, owner, "https://phone.example") == 2
    assert len(n.sent) == 1 and n.sent[0]["click"] == "https://phone.example/inbox" and "Focus ended" in n.sent[0]["title"]
    assert await flush_digest(deps.db, n, owner) == 0 and len(n.sent) == 1  # never twice


async def test_failed_push_is_retried_in_next_digest(tmp_path):
    deps, owner, caller, conv, _ = setup(tmp_path)
    down = Recorder(ok=False)
    await alert_owner(ctx(deps, owner, caller, conv, down), "message", "x")  # push fails -> stays undelivered
    assert len(repo.undelivered_alerts(deps.db, owner.id)) == 1
    good = Recorder()
    assert await flush_digest(deps.db, good, owner) == 1 and good.sent


def test_old_database_gets_focus_columns(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE owner (id TEXT PRIMARY KEY, handle TEXT, name TEXT, email TEXT, timezone TEXT, work_start TEXT, work_end TEXT, "
                "min_start TEXT, max_meeting_min INT, buffer_min INT, work_days TEXT, vip_contacts TEXT)")
    con.execute("INSERT INTO owner VALUES ('1','owner','Sam','','UTC','09:00','18:00','11:00',30,10,'0,1,2,3,4','')")
    con.commit(); con.close()
    db = repo.Database(f"sqlite:///{path}")
    assert repo.get_focus(db, "1") is False and repo.set_focus(db, "1", True) and repo.get_focus(db, "1")


async def _run_alert(deps, owner, caller, conv, rec):
    task = await alert_owner(ctx(deps, owner, caller, conv, rec), "message", "call me back")
    if task:
        await task


def test_api_hook_inbox_and_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "sekret")
    from app.core.config import get_settings
    get_settings.cache_clear()
    deps = make_deps(tmp_path, ScriptedLLM([{"say": "x"}]), admin_token="sekret")
    rec = Recorder(); deps.notifier = rec
    try:
        with TestClient(create_app(deps.st, deps, voice=False)) as c:
            assert c.get("/hook/focus/on").status_code == 401 and c.get("/hook/focus/on?key=wrong").status_code == 401
            assert c.get("/api/inbox").status_code == 401
            assert c.get("/hook/focus/on?key=sekret").json()["focus"]["on"] is True
            owner = repo.get_owner(deps.db, "owner"); caller, conv = repo.start_conversation(deps.db, owner, "+911234567890")
            import asyncio
            asyncio.run(_run_alert(deps, owner, caller, conv, rec))
            assert rec.sent == []
            r = c.get("/hook/focus/off?key=sekret").json()
            assert r["focus"]["on"] is False and r["digest_alerts"] == 1 and len(rec.sent) == 1
            assert c.get("/hook/focus/off?key=sekret").json()["changed"] is False  # idempotent: no second digest
            inbox = c.get("/api/inbox", headers={"x-admin-token": "sekret"}).json()
            assert inbox["unread"] == 1 and inbox["items"][0]["caller"]["contact"] == "+911234567890"
            assert c.post("/api/inbox/read", headers={"x-admin-token": "sekret"}).json()["marked"] == 1
            assert c.get("/api/inbox?unread=true", headers={"x-admin-token": "sekret"}).json()["items"] == []
            assert "Gatekeeper Inbox" in c.get("/inbox").text and c.get("/inbox.webmanifest").json()["start_url"] == "/inbox"
    finally:
        get_settings.cache_clear()


def test_chats_api_lists_every_channel_and_owner_can_reply_on_whatsapp(tmp_path, monkeypatch):
    from app.channels.whatsapp import TwilioWhatsApp
    from app.core.config import get_settings
    monkeypatch.setenv("ADMIN_TOKEN", "sekret")
    get_settings.cache_clear()
    deps = make_deps(tmp_path, ScriptedLLM([{"say": "x"}]), admin_token="sekret")
    sent = []

    class Sender(TwilioWhatsApp):
        async def send(self, to, text):
            sent.append((to, text)); return True

    owner = repo.get_owner(deps.db, "owner")
    for contact, channel in (("+911234567890", "whatsapp"), ("web-abc", "web"), ("+919999999999", "voice")):
        caller, conv = repo.start_conversation(deps.db, owner, contact, channel=channel)
        repo.add_message(deps.db, conv.id, "caller", f"hello from {channel}")
        repo.add_message(deps.db, conv.id, "agent", "who is calling?")
    h = {"x-admin-token": "sekret"}
    with TestClient(create_app(deps.st, deps, voice=False)) as c:
        c.app.state.whatsapp.sender = Sender("sid", "tok", "whatsapp:+1")
        assert c.get("/api/chats").status_code == 401
        allc = c.get("/api/chats", headers=h).json()
        assert allc["channels"] == {"whatsapp": 1, "web": 1, "voice": 1} and len(allc["items"]) == 3
        only = c.get("/api/chats?channel=whatsapp", headers=h).json()["items"]
        assert len(only) == 1 and only[0]["topic"] == "hello from whatsapp"
        wa = c.get(f"/api/chats/{only[0]['id']}", headers=h).json()
        assert [m["role"] for m in wa["messages"]] == ["caller", "agent"]
        assert c.post(f"/api/chats/{wa['id']}/reply", json={"text": "On my way, call you in 5"}, headers=h).json() == {"ok": True}
        assert sent == [("+911234567890", "On my way, call you in 5")]
        assert c.get(f"/api/chats/{wa['id']}", headers=h).json()["messages"][-1]["role"] == "owner"
        web = next(i for i in allc["items"] if i["channel"] == "web")
        assert c.post(f"/api/chats/{web['id']}/reply", json={"text": "hi"}, headers=h).status_code == 400  # no outbound path
        assert c.get("/api/chats/nope", headers=h).status_code == 404
    get_settings.cache_clear()
