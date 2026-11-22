"""Owner-facing: focus switch (phone automation hook), alert inbox. Admin-token protected."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.agent.alerts import flush_digest
from app.core.bus import bus
from app.db import repo
from app.security.auth import require_admin, tokens_match

router = APIRouter()


def _owner(request: Request):
    d = request.app.state.deps
    o = repo.get_owner(d.db, d.st.owner_handle)
    if not o:
        raise HTTPException(404, "owner not found")
    return d, o


async def _set_focus(request: Request, on: bool) -> dict:
    d, o = _owner(request)
    changed = repo.set_focus(d.db, o.id, on)
    digest = await flush_digest(d.db, d.notifier, o, d.st.public_url) if (changed and not on) else 0
    if changed:
        bus.publish({"type": "focus", "on": on})
    return {"focus": repo.focus_info(d.db, o.id), "changed": changed, "digest_alerts": digest}


class FocusBody(BaseModel):
    on: bool


@router.get("/api/focus", dependencies=[Depends(require_admin)])
def focus_status(request: Request):
    d, o = _owner(request)
    return repo.focus_info(d.db, o.id)


@router.post("/api/focus", dependencies=[Depends(require_admin)])
async def focus_set(body: FocusBody, request: Request):
    return await _set_focus(request, body.on)


@router.api_route("/hook/focus/{state}", methods=["GET", "POST"])
async def focus_hook(state: str, request: Request, key: str = ""):
    """For phone automation (Tasker, MacroDroid, HTTP Shortcuts, iOS Shortcuts): .../hook/focus/on?key=<ADMIN_TOKEN>"""
    exp = request.app.state.deps.st.admin_token
    if not tokens_match(key or request.headers.get("x-admin-token", ""), exp):
        raise HTTPException(401, "bad key")
    if state not in ("on", "off", "toggle"):
        raise HTTPException(404, "use on, off or toggle")
    d, o = _owner(request)
    return await _set_focus(request, (not repo.get_focus(d.db, o.id)) if state == "toggle" else state == "on")


@router.get("/api/inbox", dependencies=[Depends(require_admin)])
def inbox(request: Request, unread: bool = False):
    d, o = _owner(request)
    items = repo.list_alerts(d.db, o.id, unread_only=unread)
    return {"focus": repo.focus_info(d.db, o.id), "unread": sum(1 for i in items if not i["read"]) if not unread else len(items), "items": items}


@router.post("/api/inbox/read", dependencies=[Depends(require_admin)])
def read_all(request: Request):
    d, o = _owner(request)
    return {"marked": repo.mark_read(d.db, o.id)}


@router.post("/api/inbox/{alert_id}/read", dependencies=[Depends(require_admin)])
def read_one(alert_id: int, request: Request):
    d, o = _owner(request)
    return {"marked": repo.mark_read(d.db, o.id, alert_id)}


@router.get("/api/chats", dependencies=[Depends(require_admin)])
def chats(request: Request, channel: str = "", limit: int = 60):
    d, o = _owner(request)
    return repo.list_chats(d.db, o.id, channel=channel, limit=min(limit, 200))


@router.get("/api/chats/{cid}", dependencies=[Depends(require_admin)])
def chat(cid: str, request: Request):
    d, o = _owner(request)
    c = repo.get_chat(d.db, o.id, cid)
    if not c:
        raise HTTPException(404, "conversation not found")
    return c


class ReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@router.post("/api/chats/{cid}/reply", dependencies=[Depends(require_admin)])
async def chat_reply(cid: str, body: ReplyBody, request: Request):
    """The owner answers a WhatsApp sender directly from the inbox. Other channels have no outbound path (the UI offers call / wa.me / mailto instead)."""
    d, o = _owner(request)
    c = repo.get_chat(d.db, o.id, cid)
    if not c:
        raise HTTPException(404, "conversation not found")
    phone = c["caller"]["contact"]
    if c["channel"] != "whatsapp" or not phone.startswith("+"):
        raise HTTPException(400, "replying from here is only available for WhatsApp chats")
    if not await request.app.state.whatsapp.sender.send(phone, body.text.strip()):
        raise HTTPException(502, "WhatsApp could not deliver it (Twilio not configured, or the sender is outside the sandbox/24 h window)")
    repo.add_message(d.db, cid, "owner", body.text.strip())
    bus.publish({"type": "turn", "channel": "whatsapp", "caller": c["caller"]["name"] or phone[-4:], "in": "(you)", "out": body.text.strip()[:140], "flagged": False})
    return {"ok": True}
