"""FastAPI dependencies."""
from src.storage.db import get_connection


def get_db():
    """Yield one SQLite connection per request, closed on teardown."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()
