"""Admin-only user management (create/list/update). Deletion is deliberately
absent — deactivate via PATCH is_active=false instead, so alert/notification
history keeps a valid user reference."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_db
from src.api.schemas import UserCreate, UserOut, UserUpdate
from src.auth.deps import require_role
from src.auth.security import hash_password

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_role("admin"))])


def _user_out(row) -> UserOut:
    return UserOut(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        role=row["role"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
    )


@router.get("", response_model=list[UserOut])
def list_users(conn=Depends(get_db)):
    rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
    return [_user_out(row) for row in rows]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, conn=Depends(get_db)):
    existing = conn.execute("SELECT id FROM users WHERE email = ?", (payload.email,)).fetchone()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO users (email, name, hashed_password, role, is_active, created_at)
           VALUES (?, ?, ?, ?, 1, ?)""",
        (payload.email, payload.name, hash_password(payload.password), payload.role, now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _user_out(row)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    updates = payload.model_dump(exclude_unset=True)
    if "password" in updates:
        updates["hashed_password"] = hash_password(updates.pop("password"))
    if "is_active" in updates:
        updates["is_active"] = int(updates["is_active"])

    if updates:
        set_clause = ", ".join(f"{key} = :{key}" for key in updates)
        conn.execute(f"UPDATE users SET {set_clause} WHERE id = :id", {**updates, "id": user_id})
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    return _user_out(row)
