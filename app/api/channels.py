"""Inbound channels (WhatsApp via Twilio), live event stream, join QR."""
import io
import json
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from app.channels.whatsapp import normalize_sender, parse_form, twiml, valid_signature
from app.core.bus import bus
from app.security.auth import tokens_match

router = APIRouter()


@router.post("/webhook/whatsapp", include_in_schema=False)
async def whatsapp(request: Request):
    d = request.app.state.deps
    st = d.st
    if not st.twilio_auth_token or not st.public_url:
        raise HTTPException(503, "WhatsApp not configured (TWILIO_AUTH_TOKEN and PUBLIC_URL are required)")
    raw = await request.body()
    params = parse_form(raw)
    url = st.public_url.rstrip("/") + request.url.path + (f"?{request.url.query}" if request.url.query else "")
    if not valid_signature(st.twilio_auth_token, url, params, request.headers.get("x-twilio-signature", "")):
        raise HTTPException(403, "invalid signature")
    form = dict(params)
    phone = normalize_sender(form.get("From", ""))
    chan = request.app.state.whatsapp
    if not phone or chan.duplicate(form.get("MessageSid", "")):
        return Response(twiml(), media_type="text/xml")
    reply = await chan.reply_to(phone, form.get("Body", ""))
    return Response(twiml(reply), media_type="text/xml")


@router.get("/api/stream", include_in_schema=False)
async def stream(request: Request, key: str = ""):
    if not tokens_match(key, request.app.state.deps.st.admin_token):
        raise HTTPException(401, "bad key")

    async def gen():
        yield "retry: 3000\n\n"
        async for ev in bus.stream():
            if await request.is_disconnected():
                return
            yield ": ping\n\n" if ev is None else f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/qr/whatsapp.svg", include_in_schema=False)
def whatsapp_qr(request: Request):
    """Scan to open WhatsApp with the sandbox join message prefilled (the number and code are meant to be shared with judges)."""
    st = request.app.state.deps.st
    number = re.sub(r"\D", "", st.twilio_whatsapp_from)
    if not number:
        raise HTTPException(404)
    import segno
    link = f"https://wa.me/{number}" + (f"?text=join%20{st.whatsapp_join_code}" if st.whatsapp_join_code else "")
    buf = io.BytesIO()
    segno.make(link, error="m").save(buf, kind="svg", scale=8, border=2, xmldecl=False)
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})
