"""Login/logout/current-user endpoints.

The session is an httpOnly JWT cookie — see src/auth/security.py for why
cookie-over-localStorage. This router is intentionally NOT behind
get_current_user in app.py (you can't require a session to create one).
"""
from fastapi import APIRouter, Depends, HTTPException, Response, status

from src.api.deps import get_db
from src.api.schemas import LoginRequest, UserOut
from src.auth.deps import get_current_user
from src.auth.security import COOKIE_NAME, TOKEN_TTL, create_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_out(row) -> UserOut:
    return UserOut(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        role=row["role"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
    )


@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, response: Response, conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM users WHERE email = ?", (payload.email,)).fetchone()
    if row is None or not row["is_active"] or not verify_password(payload.password, row["hashed_password"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    token = create_token(row["id"])
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=int(TOKEN_TTL.total_seconds()),
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return _user_out(row)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok"}


@router.get("/me", response_model=UserOut)
def me(response: Response, user: dict = Depends(get_current_user)):
    # No-store so no intermediary (or the browser's own HTTP cache) can ever
    # serve a stale identity for this URL across requests/tabs.
    response.headers["Cache-Control"] = "no-store"
    return _user_out(user)
