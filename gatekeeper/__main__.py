import os, sys

if os.path.exists(".env"):  # tiny .env loader; real env vars still win
    for line in open(".env"):
        k, sep, v = line.strip().partition("=")
        if sep and not k.startswith("#"):
            os.environ.setdefault(k.strip(), v.strip())

if "--demo" in sys.argv:
    from .demo import run
    run()
    sys.exit()

if "--check-calendar" in sys.argv:  # no Anthropic key needed: verifies OAuth + freebusy only (reads no event details)
    from datetime import datetime, timedelta, timezone
    from .calendar import GoogleCalendar
    now = datetime.now(timezone.utc)
    print(f"OK: Google Calendar connected; {len(GoogleCalendar().busy(now, now + timedelta(days=7)))} busy blocks in the next 7 days.")
    sys.exit()

if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("ANTHROPIC_API_KEY is not set. Add it to .env (see .env.example), or try `python -m gatekeeper --demo` (no key needed).")

from .agent import run

try:
    run()
except Exception as e:
    if type(e).__name__ == "AuthenticationError":
        sys.exit("Invalid ANTHROPIC_API_KEY. Put your real key in .env (see .env.example) or `unset ANTHROPIC_API_KEY` if a bad one is exported.")
    raise
