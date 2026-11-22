"""All datetimes are stored as naive UTC."""
import uuid
from datetime import datetime, timezone

from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return uuid.uuid4().hex


class Owner(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    handle: str = Field(unique=True, index=True)
    name: str
    email: str = ""
    timezone: str = "UTC"
    work_start: str = "09:00"
    work_end: str = "18:00"
    min_start: str = "11:00"
    max_meeting_min: int = 30
    buffer_min: int = 10
    work_days: str = "0,1,2,3,4"
    vip_contacts: str = ""  # comma-separated, matched on verified contact id only
    focus_on: bool = False
    focus_since: NaiveDatetime | None = None


class Caller(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    contact: str = Field(unique=True, index=True)  # channel-level identifier, not spoken
    name: str = ""
    email: str = ""
    first_seen: NaiveDatetime = Field(default_factory=utcnow)
    contact_count: int = 0
    trust_level: str = "unknown"  # unknown | known | vip


class Conversation(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    owner_id: str = Field(index=True)
    caller_id: str = Field(index=True)
    channel: str = "voice"
    status: str = "active"  # active | ended | abandoned
    started_at: NaiveDatetime = Field(default_factory=utcnow)
    ended_at: NaiveDatetime | None = None


class Message(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    conversation_id: str = Field(index=True)
    role: str  # caller | agent | system
    text: str
    audio_ms: int = 0
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class SlotOffer(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)  # the slot_id the model sees
    conversation_id: str = Field(index=True)
    start: NaiveDatetime
    end: NaiveDatetime
    expires_at: NaiveDatetime
    used: bool = False


class Booking(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    conversation_id: str = Field(index=True)
    calendar_event_id: str = ""
    title: str = ""
    start: NaiveDatetime
    end: NaiveDatetime
    status: str = "confirmed"


class Memory(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    scope: str  # owner | caller
    owner_id: str | None = Field(default=None, index=True)
    caller_id: str | None = Field(default=None, index=True)
    key: str
    value: str
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class AuditLog(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    conversation_id: str = Field(index=True)
    actor_role: str
    name: str
    args: str = ""
    result: str = ""
    error: str = ""
    latency_ms: int = 0
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class Alert(SQLModel, table=True):
    """Everything the owner should know. Held while focus is ON (unless urgent), delivered as one digest when focus ends."""
    id: int | None = Field(default=None, primary_key=True)
    owner_id: str = Field(index=True)
    conversation_id: str = Field(index=True)
    caller_id: str = ""
    kind: str  # booking | message | calendar down | agent down
    summary: str
    urgent: bool = False
    delivered: bool = False  # pushed to the phone
    read: bool = False  # seen in the inbox
    created_at: NaiveDatetime = Field(default_factory=utcnow)
