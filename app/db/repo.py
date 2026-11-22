import json
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import Settings
from app.db.models import (Alert, AuditLog, Booking, Caller, Conversation, Memory, Message, Owner,
                           SlotOffer, utcnow)


class Database:
    def __init__(self, url: str):
        if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
            import os
            os.makedirs(os.path.dirname(url.removeprefix("sqlite:///")) or ".", exist_ok=True)
        self.engine = create_engine(url, connect_args={"check_same_thread": False})
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _pragma(conn, _):  # noqa: ANN001
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
        SQLModel.metadata.create_all(self.engine)
        if url.startswith("sqlite"):
            self._migrate()

    def _migrate(self) -> None:
        """create_all never alters existing tables; add columns introduced after a DB was first created."""
        add = {"owner": {"focus_on": "BOOLEAN DEFAULT 0", "focus_since": "DATETIME"}}
        with self.engine.begin() as c:
            for table, cols in add.items():
                have = {r[1] for r in c.exec_driver_sql(f"PRAGMA table_info({table})")}
                for name, ddl in cols.items():
                    if name not in have:
                        c.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    @contextmanager
    def session(self):
        with Session(self.engine, expire_on_commit=False) as s:
            yield s


def ensure_owner(db: Database, st: Settings) -> Owner:
    with db.session() as s:
        o = s.exec(select(Owner).where(Owner.handle == st.owner_handle)).first()
        if o:
            return o
        o = Owner(handle=st.owner_handle, name=st.owner_name, email=st.owner_email,
                  timezone=st.owner_timezone, work_start=st.owner_work_start,
                  work_end=st.owner_work_end, min_start=st.owner_min_start,
                  max_meeting_min=st.owner_max_meeting_min, buffer_min=st.owner_buffer_min,
                  work_days=st.owner_work_days, vip_contacts=st.owner_vip_contacts)
        s.add(o)
        s.commit()
        return o


def get_owner(db: Database, handle: str) -> Owner | None:
    with db.session() as s:
        return s.exec(select(Owner).where(Owner.handle == handle)).first()


def start_conversation(db: Database, owner: Owner, contact: str, channel: str = "voice") -> tuple[Caller, Conversation]:
    with db.session() as s:
        c = s.exec(select(Caller).where(Caller.contact == contact)).first()
        if not c:
            c = Caller(contact=contact)
        c.contact_count += 1
        vips = {v.strip().lower() for v in owner.vip_contacts.split(",") if v.strip()}
        if contact.lower() in vips:
            c.trust_level = "vip"
        elif c.name:
            c.trust_level = "known" if c.trust_level != "vip" else "vip"
        s.add(c)
        s.commit()
        conv = Conversation(owner_id=owner.id, caller_id=c.id, channel=channel)
        s.add(conv)
        s.commit()
        return c, conv


def contacts_last_7d(db: Database, caller_id: str) -> int:
    with db.session() as s:
        since = utcnow() - timedelta(days=7)
        return len(s.exec(select(Conversation.id).where(Conversation.caller_id == caller_id,
                                                        Conversation.started_at >= since)).all())


def end_conversation(db: Database, conversation_id: str, status: str = "ended") -> None:
    with db.session() as s:
        c = s.get(Conversation, conversation_id)
        if c and c.status == "active":
            c.status, c.ended_at = status, utcnow()
            s.add(c)
            s.commit()


def add_message(db: Database, conversation_id: str, role: str, text: str, audio_ms: int = 0) -> None:
    with db.session() as s:
        s.add(Message(conversation_id=conversation_id, role=role, text=text[:4000], audio_ms=audio_ms))
        s.commit()


def audit(db: Database, conversation_id: str, role: str, name: str, args=None, result=None,
          error: str = "", latency_ms: int = 0) -> None:
    with db.session() as s:
        s.add(AuditLog(conversation_id=conversation_id, actor_role=role, name=name,
                       args=json.dumps(args, default=str)[:2000] if args is not None else "",
                       result=json.dumps(result, default=str)[:2000] if result is not None else "",
                       error=error[:500], latency_ms=latency_ms))
        s.commit()


def create_offers(db: Database, conversation_id: str, slots: list[tuple[datetime, datetime]], ttl_min: int = 30) -> list[SlotOffer]:
    with db.session() as s:
        offers = [SlotOffer(conversation_id=conversation_id, start=a, end=b,
                            expires_at=utcnow() + timedelta(minutes=ttl_min)) for a, b in slots]
        s.add_all(offers)
        s.commit()
        return offers


def get_offer(db: Database, conversation_id: str, slot_id: str) -> SlotOffer | None:
    """Only returns offers issued in THIS conversation, unused and unexpired."""
    with db.session() as s:
        o = s.get(SlotOffer, slot_id)
        if not o or o.conversation_id != conversation_id or o.used or o.expires_at < utcnow():
            return None
        return o


def mark_offer_used(db: Database, slot_id: str) -> None:
    with db.session() as s:
        o = s.get(SlotOffer, slot_id)
        if o:
            o.used = True
            s.add(o)
            s.commit()


def create_booking(db: Database, booking: Booking) -> Booking:
    with db.session() as s:
        s.add(booking)
        s.commit()
        return booking


def update_caller(db: Database, caller_id: str, name: str | None = None, email: str | None = None) -> None:
    with db.session() as s:
        c = s.get(Caller, caller_id)
        if c:
            if name:
                c.name = name[:80]
                if c.trust_level == "unknown":
                    c.trust_level = "known"
            if email:
                c.email = email[:200]
            s.add(c)
            s.commit()


def add_memory(db: Database, scope: str, key: str, value: str, owner_id: str | None = None, caller_id: str | None = None) -> None:
    with db.session() as s:
        s.add(Memory(scope=scope, owner_id=owner_id, caller_id=caller_id, key=key, value=value[:2000]))
        s.commit()


def caller_history(db: Database, caller_id: str) -> dict:
    with db.session() as s:
        c = s.get(Caller, caller_id)
        notes = s.exec(select(Memory).where(Memory.caller_id == caller_id, Memory.scope == "caller")
                       .order_by(Memory.id.desc()).limit(3)).all()
        convs = s.exec(select(Conversation.id).where(Conversation.caller_id == caller_id)).all()
        booked = 0
        if convs:
            booked = len(s.exec(select(Booking.id).where(Booking.conversation_id.in_(convs))).all())
        return {"name": c.name or None, "times_contacted": c.contact_count,
                "past_bookings": booked, "recent_notes": [n.value for n in notes]}


def housekeeping(db: Database) -> dict:
    """Scheduled: drop expired slot offers, close abandoned conversations."""
    with db.session() as s:
        gone = s.exec(select(SlotOffer).where(SlotOffer.expires_at < utcnow() - timedelta(hours=1))).all()
        for g in gone:
            s.delete(g)
        stale = s.exec(select(Conversation).where(Conversation.status == "active",
                                                  Conversation.started_at < utcnow() - timedelta(hours=1))).all()
        for c in stale:
            c.status, c.ended_at = "abandoned", utcnow()
            s.add(c)
        s.commit()
        return {"offers_deleted": len(gone), "abandoned": len(stale)}


# ---------- focus mode + alert inbox ----------
def get_focus(db: Database, owner_id: str) -> bool:
    with db.session() as s:
        o = s.get(Owner, owner_id)
        return bool(o and o.focus_on)


def set_focus(db: Database, owner_id: str, on: bool) -> bool:
    """Returns True if the state actually changed."""
    with db.session() as s:
        o = s.get(Owner, owner_id)
        if not o or bool(o.focus_on) == on:
            return False
        o.focus_on, o.focus_since = on, (utcnow() if on else None)
        s.add(o)
        s.commit()
        return True


def focus_info(db: Database, owner_id: str) -> dict:
    with db.session() as s:
        o = s.get(Owner, owner_id)
        return {"on": bool(o.focus_on), "since": o.focus_since.isoformat(timespec="seconds") + "Z" if o.focus_since else None}


def add_alert(db: Database, owner_id: str, conversation_id: str, caller_id: str, kind: str, summary: str,
              urgent: bool, delivered: bool) -> Alert:
    with db.session() as s:
        a = Alert(owner_id=owner_id, conversation_id=conversation_id, caller_id=caller_id, kind=kind,
                  summary=summary[:500], urgent=urgent, delivered=delivered)
        s.add(a)
        s.commit()
        return a


def undelivered_alerts(db: Database, owner_id: str) -> list[Alert]:
    with db.session() as s:
        return list(s.exec(select(Alert).where(Alert.owner_id == owner_id, Alert.delivered == False)  # noqa: E712
                           .order_by(Alert.id)).all())


def mark_delivered(db: Database, ids: list[int]) -> None:
    with db.session() as s:
        for a in s.exec(select(Alert).where(Alert.id.in_(ids))).all():
            a.delivered = True
            s.add(a)
        s.commit()


def list_alerts(db: Database, owner_id: str, unread_only: bool = False, limit: int = 100) -> list[dict]:
    with db.session() as s:
        q = select(Alert).where(Alert.owner_id == owner_id)
        if unread_only:
            q = q.where(Alert.read == False)  # noqa: E712
        out = []
        for a in s.exec(q.order_by(Alert.id.desc()).limit(limit)).all():
            c = s.get(Caller, a.caller_id) if a.caller_id else None
            cv = s.get(Conversation, a.conversation_id)
            msgs = s.exec(select(Message).where(Message.conversation_id == a.conversation_id).order_by(Message.id)).all()
            out.append({"id": a.id, "kind": a.kind, "summary": a.summary, "urgent": a.urgent, "delivered": a.delivered, "read": a.read,
                        "created": a.created_at.isoformat(timespec="seconds") + "Z", "conversation_id": a.conversation_id, "channel": cv.channel if cv else "",
                        "caller": {"name": c.name if c else "", "contact": c.contact if c else "", "email": c.email if c else "",
                                   "times_contacted": c.contact_count if c else 0},
                        "transcript": [{"role": m.role, "text": m.text[:400]} for m in msgs[:30]]})
        return out


def mark_read(db: Database, owner_id: str, alert_id: int | None = None) -> int:
    with db.session() as s:
        q = select(Alert).where(Alert.owner_id == owner_id, Alert.read == False)  # noqa: E712
        if alert_id is not None:
            q = q.where(Alert.id == alert_id)
        rows = s.exec(q).all()
        for a in rows:
            a.read = True
            s.add(a)
        s.commit()
        return len(rows)


def bookings_in_conversation(db: Database, conversation_id: str) -> list:
    with db.session() as s:
        return list(s.exec(select(Booking).where(Booking.conversation_id == conversation_id, Booking.status == "confirmed")).all())


def recent_bookings_by_caller(db: Database, caller_id: str, hours: int = 24) -> int:
    with db.session() as s:
        convs = s.exec(select(Conversation.id).where(Conversation.caller_id == caller_id, Conversation.started_at >= utcnow() - timedelta(hours=hours))).all()
        return len(s.exec(select(Booking.id).where(Booking.conversation_id.in_(convs), Booking.status == "confirmed")).all()) if convs else 0
