"""Auth dependencies: current-user resolution and role gating.

Kept separate from src/api/deps.py (which only holds the DB-connection
dependency) so src.auth can import src.api.deps without src.api needing to
import src.auth back.
"""
import jwt
from fastapi import Depends, HTTPException, Request, status

from src.api.deps import get_db
from src.auth.security import COOKIE_NAME, decode_token


def _lookup_active_user(conn, user_id: int) -> dict | None:
    cur = conn.execute(
        "SELECT id, email, name, role, is_active, created_at FROM users WHERE id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    if row is None or not row["is_active"]:
        return None
    return dict(row)


def get_current_user(request: Request, conn=Depends(get_db)) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session")

    user = _lookup_active_user(conn, int(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")

    return user


def get_user_from_token(conn, token: str | None) -> dict | None:
    """Same lookup as get_current_user, for callers with a raw cookie value
    instead of a Request (the WebSocket handshake has no Request to inject) —
    returns None on any failure instead of raising."""
    if not token:
        return None
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        return None
    return _lookup_active_user(conn, int(payload["sub"]))


def require_role(*roles: str):
    """Dependency factory: 403s unless the current user's role is in `roles`."""

    def _check(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return user

    return _check
