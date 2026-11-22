"""In-process pub/sub so the inbox and showcase update the moment something happens (single instance)."""
import asyncio
from collections.abc import AsyncIterator


class Bus:
    def __init__(self):
        self._subs: set[asyncio.Queue] = set()

    def publish(self, event: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # slow client: drop rather than block a call
                pass

    async def stream(self, heartbeat_s: float = 15.0) -> AsyncIterator[dict | None]:
        q: asyncio.Queue = asyncio.Queue(100)
        self._subs.add(q)
        try:
            while True:
                try:
                    yield await asyncio.wait_for(q.get(), heartbeat_s)
                except asyncio.TimeoutError:
                    yield None  # heartbeat keeps tunnels/proxies from closing the connection
        finally:
            self._subs.discard(q)


bus = Bus()
