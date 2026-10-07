"""FastAPI dependencies."""
import logging

from src.storage import migrations
from src.storage.db import get_connection

logger = logging.getLogger(__name__)


def get_db():
    """Yield one SQLite connection per request, closed on teardown.

    The first request against a stale (versioned, pre-latest) file upgrades it
    (work-orders design, decision 11): every alert SELECT reads the migration-4
    columns, and migrations do not run at app start. After that it is one
    memoised lookup per request. A failed upgrade is logged and the request
    goes ahead on the old schema rather than failing outright.
    """
    conn = get_connection()
    try:
        try:
            migrations.ensure_current_schema(conn)
        except Exception:
            logger.exception("Could not bring the database schema up to date")
        yield conn
    finally:
        conn.close()
