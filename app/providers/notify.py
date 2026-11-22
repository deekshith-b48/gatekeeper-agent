import logging

import httpx

from app.core.resilience import call_with_retry

log = logging.getLogger("notify")


class NtfyNotifier:
    def __init__(self, url: str, topic: str):
        self.url, self.topic = url.rstrip("/"), topic

    async def send(self, title: str, message: str, urgent: bool = False) -> bool:
        if not self.topic:
            log.warning("NTFY_TOPIC not set; owner alert NOT delivered: %s | %s", title, message)
            return False

        async def _post():
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.post(f"{self.url}/{self.topic}", content=message.encode(),
                                 headers={"Title": title.encode("ascii", "ignore").decode(),
                                          "Priority": "urgent" if urgent else "default",
                                          "Tags": "rotating_light" if urgent else "calendar"})
                r.raise_for_status()

        try:
            await call_with_retry(_post, timeout=6, retries=2)
            return True
        except Exception as e:  # noqa: BLE001
            log.error("ntfy delivery failed: %r | %s", e, message)
            return False
