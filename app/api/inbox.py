"""Owner-facing: focus switch (phone automation hook), alert inbox. Admin-token protected."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

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
