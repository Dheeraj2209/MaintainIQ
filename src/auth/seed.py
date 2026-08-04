"""One-time demo-account seeding. Run like src/training/run_pipeline.py:

    python -m src.auth.seed

Idempotent — INSERT OR IGNORE on the unique email, so re-running after the
first seed is a no-op.
"""
from datetime import datetime, timezone

from src.auth.security import hash_password
from src.storage.db import get_connection, init_schema

# (email, name, password, role). Passwords are demo-only, printed to the
# terminal on seed and documented in README.md — not a real secret boundary.
DEMO_USERS = [
    ("admin@maintainiq.local", "Ava Admin", "Admin123!", "admin"),
    ("supervisor@maintainiq.local", "Sam Supervisor", "Supervisor123!", "supervisor"),
    ("operator@maintainiq.local", "Otis Operator", "Operator123!", "operator"),
]


def seed_demo_users(conn) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        """INSERT OR IGNORE INTO users (email, name, hashed_password, role, is_active, created_at)
           VALUES (?, ?, ?, ?, 1, ?)""",
        [(email, name, hash_password(pw), role, now) for email, name, pw, role in DEMO_USERS],
    )
    conn.commit()


if __name__ == "__main__":
    conn = get_connection()
    init_schema(conn)
    seed_demo_users(conn)
    print(f"Seeded {len(DEMO_USERS)} demo users:")
    for email, _name, pw, role in DEMO_USERS:
        print(f"  {role:<10} {email}  /  {pw}")
