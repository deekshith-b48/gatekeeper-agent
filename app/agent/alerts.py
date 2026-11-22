import logging

from app.db import repo
from app.db.models import Owner
from app.db.repo import Database
from app.providers.notify import NtfyNotifier

log = logging.getLogger("alerts")


async def flush_digest(db: Database, notifier: NtfyNotifier, owner: Owner, public_url: str = "") -> int:
    """Focus ended: push ONE summary of everything held. Returns how many alerts it covered (0 = nothing sent)."""
    held = repo.undelivered_alerts(db, owner.id)
    if not held:
        return 0
    counts: dict[str, int] = {}
    for a in held:
        counts[a.kind] = counts.get(a.kind, 0) + 1
    urgent = any(a.urgent for a in held)
    head = ", ".join(f"{n} {k}{'s' if n > 1 and not k.endswith('down') else ''}" for k, n in counts.items())
    lines = [f"• {a.summary[:110]}" for a in held[:6]] + ([f"…and {len(held) - 6} more"] if len(held) > 6 else [])
    ok = await notifier.send(f"Focus ended: {head}", "\n".join(lines) + "\nTap to open your inbox.", urgent=urgent,
                             click=f"{public_url.rstrip('/')}/inbox" if public_url else "")
    if ok:
        repo.mark_delivered(db, [a.id for a in held])
    else:
        log.error("digest push failed; %d alerts remain undelivered (still in the inbox)", len(held))
    return len(held) if ok else 0
