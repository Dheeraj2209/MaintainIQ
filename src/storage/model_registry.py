"""The one definition of "the active model" in model_registry.

register_active_model keeps exactly one row active, but nothing in the schema
enforces that, so every reader picks deterministically when more than one row
is flagged: the most recently deployed wins, ties broken by model_version.
"""
from src.storage.db import table_exists

ACTIVE_MODEL_SQL = """
    SELECT model_version, artifact_path, algorithm, trained_at, metrics_json,
           deployed_at, is_active
    FROM model_registry
    WHERE is_active = 1
    ORDER BY deployed_at DESC, model_version DESC
    LIMIT 1
"""


def active_model(conn):
    """The active model_registry row, or None (also when the table is absent)."""
    if not table_exists(conn, "model_registry"):
        return None
    return conn.execute(ACTIVE_MODEL_SQL).fetchone()
