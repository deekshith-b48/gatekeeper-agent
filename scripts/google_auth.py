"""One-time Google OAuth (needs a browser). Writes GOOGLE_TOKEN_PATH. Scopes: calendar.events + calendar.freebusy only."""
import os

from google_auth_oauthlib.flow import InstalledAppFlow

from app.providers.calendar.google import SCOPES

cred = os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json")
tok = os.environ.get("GOOGLE_TOKEN_PATH", "token.json")
creds = InstalledAppFlow.from_client_secrets_file(cred, SCOPES).run_local_server(port=0)
open(tok, "w").write(creds.to_json())
print(f"wrote {tok}")
