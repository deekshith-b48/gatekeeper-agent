"""WhatsApp via Twilio. Same agent, same tools, same guard as the voice/web channel; only the transport differs."""
import asyncio
import base64
import hashlib
import hmac
import logging
import re
import time
from collections import OrderedDict
from urllib.parse import parse_qsl
from xml.sax.saxutils import escape

import httpx

from app.agent.session import CallSession, Deps
from app.core.resilience import call_with_retry
from app.db import repo

log = logging.getLogger("whatsapp")
IDLE_S = 30 * 60
MAX_REPLY = 1500  # Twilio WhatsApp body limit is 1600


def twilio_signature(auth_token: str, url: str, params: list[tuple[str, str]]) -> str:
    """Twilio's documented scheme: HMAC-SHA1(token, url + sorted(key+value...)), base64."""
    data = url + "".join(k + v for k, v in sorted(params))
    return base64.b64encode(hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()).decode()


def valid_signature(auth_token: str, url: str, params: list[tuple[str, str]], header: str) -> bool:
    return bool(auth_token and header) and hmac.compare_digest(twilio_signature(auth_token, url, params), header)


def parse_form(raw: bytes) -> list[tuple[str, str]]:
    return parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True)


def normalize_sender(wa_from: str) -> str | None:
    """'whatsapp:+911234567890' -> '+911234567890' (the platform-verified id; matches OWNER_VIP_CONTACTS)."""
    n = wa_from.removeprefix("whatsapp:").strip()
    return n if re.fullmatch(r"\+[1-9][0-9]{6,14}", n) else None


def twiml(text: str = "") -> str:
    body = f"<Message>{escape(text[:MAX_REPLY])}</Message>" if text else ""
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'


class TwilioWhatsApp:
    """Outbound sender (REST). Used when a reply takes longer than the webhook window."""

    def __init__(self, sid: str, token: str, from_number: str):
        self.sid, self.token, self.from_number = sid, token, from_number

    @property
    def configured(self) -> bool:
        return bool(self.sid and self.token and self.from_number)

    async def send(self, to_phone: str, text: str) -> bool:
        if not self.configured:
            log.warning("late WhatsApp reply could not be sent: TWILIO_ACCOUNT_SID / TWILIO_WHATSAPP_FROM not set")
            return False

        async def _post():
            async with httpx.AsyncClient(timeout=10, auth=(self.sid, self.token)) as c:
                r = await c.post(f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json",
                                 data={"From": self.from_number, "To": f"whatsapp:{to_phone}", "Body": text[:MAX_REPLY]})
                r.raise_for_status()

        try:
            await call_with_retry(_post, timeout=12, retries=2)
            return True
        except Exception as e:  # noqa: BLE001
            log.error("twilio send failed: %r", e)
            return False


class WhatsAppChannel:
    def __init__(self, deps: Deps, owner_handle: str, sender: TwilioWhatsApp, inline_timeout_s: float = 12.0):
        self.deps, self.owner_handle, self.sender, self.inline_timeout_s = deps, owner_handle, sender, inline_timeout_s
        self._sessions: dict[str, tuple[CallSession, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._seen: OrderedDict[str, None] = OrderedDict()  # MessageSid dedupe: Twilio retries on slow responses

    def duplicate(self, sid: str) -> bool:
        if not sid:
            return False
        if sid in self._seen:
            return True
        self._seen[sid] = None
        while len(self._seen) > 2000:
            self._seen.popitem(last=False)
        return False

    def _session(self, phone: str) -> CallSession | None:
        d = self.deps
        cur = self._sessions.get(phone)
        if cur and time.monotonic() - cur[1] < IDLE_S:
            self._sessions[phone] = (cur[0], time.monotonic())
            return cur[0]
        if cur:
            cur[0].close("ended")
        owner = repo.get_owner(d.db, self.owner_handle)
        if not owner:
            return None
        caller, conv = repo.start_conversation(d.db, owner, phone, channel="whatsapp")
        s = CallSession(d, owner, caller, conv.id, channel="whatsapp")
        self._sessions[phone] = (s, time.monotonic())
        return s

    async def reply_to(self, phone: str, text: str) -> str:
        """Returns the reply to send inline ('' if it will be pushed later)."""
        lock = self._locks.setdefault(phone, asyncio.Lock())
        if not self.deps.turn_limiter.allow("wa:" + phone):
            return "You're sending messages very quickly. Please wait a moment."
        async with lock:
            sess = self._session(phone)
            if sess is None:
                return ""
            task = asyncio.create_task(sess.handle_text(text))
            try:
                r = await asyncio.wait_for(asyncio.shield(task), self.inline_timeout_s)
                return r.say
            except asyncio.TimeoutError:
                async def later():
                    try:
                        r = await task
                        if r.say:
                            await self.sender.send(phone, r.say)
                    except Exception:  # noqa: BLE001
                        log.exception("late reply failed")
                asyncio.create_task(later())
                return ""
