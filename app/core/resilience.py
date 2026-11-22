"""Timeout + exponential-backoff retry + circuit breaker for every external call."""
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

log = logging.getLogger("resilience")
T = TypeVar("T")


class CircuitOpen(Exception):
    pass


class CircuitBreaker:
    def __init__(self, threshold: int = 3, reset_s: float = 30.0):
        self.threshold, self.reset_s = threshold, reset_s
        self.failures, self.opened_at = 0, 0.0

    def allow(self) -> bool:
        if self.failures < self.threshold:
            return True
        if time.monotonic() - self.opened_at >= self.reset_s:  # half-open: let one through
            return True
        return False

    def record(self, ok: bool) -> None:
        if ok:
            self.failures = 0
        else:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()


async def call_with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    timeout: float,
    retries: int = 2,
    base_delay: float = 0.5,
    breaker: CircuitBreaker | None = None,
    no_retry: tuple[type[BaseException], ...] = (),
) -> T:
    if breaker and not breaker.allow():
        raise CircuitOpen("circuit open")
    last: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            result = await asyncio.wait_for(fn(), timeout)
            if breaker:
                breaker.record(True)
            return result
        except no_retry:
            raise
        except Exception as e:  # noqa: BLE001 - external boundary
            last = e
            log.warning("call failed (attempt %d/%d): %s", attempt + 1, retries + 1, repr(e))
            if attempt < retries:
                await asyncio.sleep(base_delay * 2**attempt)
    if breaker:
        breaker.record(False)
    assert last is not None
    raise last
