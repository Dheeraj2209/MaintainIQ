"""Guards that the top-level docs match the shipped XJTU-SY system."""
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_README = _REPO_ROOT / "README.md"
_BASELINE = _REPO_ROOT / "design" / "DESIGN_BASELINE.md"
_CATALOG = _REPO_ROOT / "design" / "FAN_DATASET_CATALOG.md"

_SPEC_NAME = "2026-08-06-xjtu-sy-ml-integration-design"


def test_readme_tagline_drops_temperature():
    text = _README.read_text(encoding="utf-8")
    assert "vibration and temperature data" not in text


def test_readme_links_data_model_and_replay():
    text = _README.read_text(encoding="utf-8")
    assert "docs/DATA_MODEL.md" in text
    assert "replay" in text.lower()


def test_baseline_points_at_spec():
    text = _BASELINE.read_text(encoding="utf-8")
    assert _SPEC_NAME in text


def test_catalog_no_longer_claims_no_rpm_field():
    text = _CATALOG.read_text(encoding="utf-8")
    # speed_rpm is now a real column; the blanket "no RPM field anywhere in the
    # schema" claim must be corrected.
    assert "no acoustic, current, RPM, or pressure field anywhere in the schema" not in text
    assert "speed_rpm" in text
