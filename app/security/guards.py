"""Input guard: rule-based injection detector + Llama Guard 3 (via Ollama).
Guards are defence in depth. The hard guarantee is code-level: callers only ever get caller tools."""
import re
import unicodedata
from dataclasses import dataclass, field

TOOL_NAMES = ["get_free_slots", "book_meeting", "take_message", "recall_caller", "cancel", "delete_event",
              "update_event", "list_events", "get_events", "notify_owner", "owner_tools"]

_RULES: list[tuple[str, re.Pattern]] = [(n, re.compile(p, re.I)) for n, p in [
    ("instruction_override", r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(previous|prior|above|all|your|the|any)\b.{0,30}\b(instructions?|rules?|prompts?|guidelines?|restrictions?)\b"),
    ("instruction_override", r"\b(new|updated)\s+(instructions?|rules?|system prompt)\b"),
    ("role_claim", r"\b(i am|i'm|this is|im)\b.{0,25}\b(the\s+)?(owner|admin|administrator|developer|root|his manager|her manager|the boss|ceo)\b"),
    ("role_claim", r"\b(switch|change|enter|enable|activate)\b.{0,25}\b(admin|developer|debug|god|owner|sudo|root)\b.{0,10}\b(mode|access|role)?"),
    ("system_reference", r"\b(system\s*(prompt|override|message|instruction)|developer\s*mode|jailbreak|\bDAN\b)\b"),
    ("system_reference", r"<\s*/?\s*(system|assistant|tool|caller_speech|tool_result)\b"),
    ("prompt_leak", r"\b(reveal|show|print|repeat|leak|tell me)\b.{0,30}\b(system prompt|your (instructions|prompt|rules)|hidden)\b"),
    ("calendar_probe", r"\b(list|show|read|tell|what|who|see|check|view)\b.{0,40}\b(his|her|the owner'?s?|their)\b.{0,25}\b(meetings?|calendar|schedule|events?|appointments?|attendees?|agenda)\b"),
    ("destructive_request", r"\b(cancel|delete|remove|move|reschedule|postpone|edit|modify|update|clear)\b.{0,40}\b(all|every|his|her|the|my)\b.{0,25}\b(meetings?|events?|appointments?|calendar|bookings?)\b"),
    ("fake_tool_call", r"\{\s*\"?(tool|name|function|action)\"?\s*:\s*\"?(\w+)\"?"),
    ("fake_tool_call", r"\b(" + "|".join(TOOL_NAMES) + r")\s*\("),
    ("fake_tool_call", r"\b(cancel|delete|remove|update|list|get|move|edit|reschedule)_\w+\s*\("),
    ("tool_name_mention", r"\b(" + "|".join(TOOL_NAMES) + r")\b"),
]]


@dataclass
class GuardVerdict:
    flagged: bool = False
    reasons: list[str] = field(default_factory=list)


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    t = "".join(ch for ch in t if ch == "\n" or unicodedata.category(ch)[0] != "C")  # strip control/zero-width
    return re.sub(r"\s+", " ", t).strip()


def rule_check(text: str, max_chars: int) -> GuardVerdict:
    v = GuardVerdict()
    if len(text) > max_chars:
        v.flagged = True
        v.reasons.append("too_long")
        return v
    t = normalize(text)
    for name, rx in _RULES:
        if rx.search(t) and name not in v.reasons:
            v.flagged = True
            v.reasons.append(name)
    return v
