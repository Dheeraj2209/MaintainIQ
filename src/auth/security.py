"""Password hashing + JWT issuance/verification for cookie-based auth.

The session token lives in an httpOnly, SameSite=Lax cookie rather than
localStorage: the app is always same-origin (Vite proxy in dev, FastAPI-served
SPA in prod), so a cookie avoids CORS/CSRF complexity while keeping the token
out of reach of injected JS.
"""
import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me-in-production-32bytes-min")
JWT_ALGORITHM = "HS256"
TOKEN_TTL = timedelta(hours=12)
COOKIE_NAME = "miq_session"


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "iat": now, "exp": now + TOKEN_TTL}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
