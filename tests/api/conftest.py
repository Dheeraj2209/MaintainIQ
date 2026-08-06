"""Local fixture overrides for tests/api.

The shared `db_path` fixture in tests/conftest.py seeds two `predictions`
rows for machine m1 (and two for m2) so KPI/alert/maintenance tests have
deterministic history to assert on. The RUL persistence tests in this
directory count `predictions`/`model_inference_log` rows written by a single
`/predictions/rul` call, so they need a clean `predictions` table. Overriding
`db_path` here (pytest fixture overriding: a fixture can depend on the
same-named fixture from a parent conftest) keeps the shared fixture and its
other consumers untouched.
"""
import sqlite3

import pytest


@pytest.fixture
def db_path(db_path):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM predictions")
        conn.commit()
    finally:
        conn.close()
    return db_path
