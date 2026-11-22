import os, sys

if os.path.exists(".env"):  # tiny .env loader; real env vars still win
    for line in open(".env"):
        k, sep, v = line.strip().partition("=")
        if sep and not k.startswith("#"):
            os.environ.setdefault(k.strip(), v.strip())

from .agent import run

try:
    run()
except Exception as e:
    if type(e).__name__ == "AuthenticationError":
        sys.exit("Invalid ANTHROPIC_API_KEY. Put your real key in .env (see .env.example) or `unset ANTHROPIC_API_KEY` if a bad one is exported.")
    raise
