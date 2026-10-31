from __future__ import annotations

from pathlib import Path

import yaml

from app.services.export_excel import load_mapping
from app.services.fields import ALL_FIELD_SPECS

CATALOG = Path(__file__).resolve().parents[3] / "docs" / "field_catalog.yaml"


def _catalog() -> dict:
    return yaml.safe_load(CATALOG.read_text(encoding="utf-8"))


def test_catalog_covers_every_mapped_cell():
    catalog = _catalog()["cells"]
    mapping, _ = load_mapping()
    for sheet, sheet_map in mapping["sheets"].items():
        for cell_ref, spec in (sheet_map.get("scalars") or {}).items():
            key = f"{sheet}!{cell_ref}"
            assert key in catalog, f"thiếu catalog cho {key}"
            assert catalog[key]["canonical_field"] == spec["field"], key
        line_map = sheet_map.get("line_items") or {}
        start = line_map.get("start_row")
        for letter, col in (line_map.get("columns") or {}).items():
            key = f"{sheet}!{letter}{start}"
            assert key in catalog, f"thiếu catalog cho {key}"
            assert catalog[key]["canonical_field"] == col["field"], key


def test_catalog_fields_resolve_to_known_specs():
    catalog = _catalog()["cells"]
    for key, entry in catalog.items():
        field = entry.get("canonical_field")
        if field is None:
            continue
        assert field in ALL_FIELD_SPECS, f"{key}: '{field}' không có trong ALL_FIELD_SPECS"
        assert bool(entry.get("critical")) == ALL_FIELD_SPECS[field].critical, f"{key}: critical lệch với fields.py"


def test_catalog_policies_are_known():
    catalog = _catalog()
    allowed = set(catalog["review_policy"])
    for key, entry in catalog["cells"].items():
        assert entry["policy"] in allowed, f"{key}: policy lạ '{entry['policy']}'"
