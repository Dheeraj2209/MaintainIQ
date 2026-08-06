"""Structural consistency checks for docs/DATA_MODEL.md.

These guard against the living data-model doc silently drifting from the
committed schema (a new table added to db.py but never documented) or from
the phase's temperature-removal invariant.
"""
import re
from pathlib import Path

from src.storage import db

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOC = _REPO_ROOT / "docs" / "DATA_MODEL.md"

_REQUIRED_SECTIONS = (
    "Entity-relationship diagram",
    "Tables",
    "Data flow",
    "Ingestion modes",
    "Storage layout",
    "Model heartbeat",
    "Report catalog",
)


def _schema_table_names() -> list[str]:
    return re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", db.SCHEMA)


def test_doc_exists():
    assert _DOC.is_file(), "docs/DATA_MODEL.md must exist"


def test_every_schema_table_is_documented():
    text = _DOC.read_text(encoding="utf-8")
    missing = [t for t in _schema_table_names() if t not in text]
    assert not missing, f"tables missing from DATA_MODEL.md: {missing}"


def test_required_sections_present():
    text = _DOC.read_text(encoding="utf-8")
    missing = [s for s in _REQUIRED_SECTIONS if s not in text]
    assert not missing, f"required sections missing: {missing}"


def test_no_temperature_column_documented():
    text = _DOC.read_text(encoding="utf-8")
    assert "temperature_c" not in text, (
        "temperature_c is not part of the canonical XJTU-SY model; "
        "it must not appear as a documented column"
    )
