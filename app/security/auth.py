import hmac

from fastapi import Header, HTTPException

from app.core.config import get_settings


def tokens_match(supplied: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(supplied.encode(), expected.encode())


def require_admin(x_admin_token: str = Header(default="")) -> None:
    exp = get_settings().admin_token
    if not exp:
        raise HTTPException(403, "admin API disabled (set ADMIN_TOKEN)")
    if not tokens_match(x_admin_token, exp):
        raise HTTPException(401, "bad admin token")
