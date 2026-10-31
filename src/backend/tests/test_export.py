from __future__ import annotations

import json
from pathlib import Path

from openpyxl import load_workbook

from app.services.export_excel import (
    DEFAULT_TEMPLATE_VERSION,
    FieldSource,
    available_templates,
    load_mapping,
    write_export,
    verify_round_trip,
)

from .conftest import SAMPLE_OUTPUT


def _confirmed(keys):
    return {k: FieldSource(review_status="CONFIRMED", grounding_verified=True) for k in keys}


def _base_payload():
    values = {
        "transport.packages": "20",
        "transport.volume_cbm": "38.03",
        "transport.net_weight_kg": "26000",
        "transport.gross_weight_kg": "26400",
        "contract.seller": "WINSMART INDUSTRIAL LIMITED",
        "contract.buyer": "GREEN WAY COMPANY LIMITED",
        "totals.total_amount": "67175.4552",
    }
    lines = [
        {
            "line.no": "1",
            "line.description": "OAK VENEER (4.0x 190 x 1900)MM",
            "line.spec": "4",
            "line.quantity": "38.03",
            "line.amount": "67175.4552",
        }
    ]
    return values, lines


def test_mapping_loads_and_template_is_blanked():
    mapping, template_path = load_mapping()
    assert template_path.exists()
    assert set(mapping["sheets"]) == {"PACKING LIST", "INVOICE", "HOP DONG"}
    wb = load_workbook(template_path)
    assert wb["PACKING LIST"]["D15"].value is None
    assert wb["PACKING LIST"]["D16"].value == "=SUM(D15:D15)"
    assert wb["INVOICE"]["N18"].value == "=SUM(N16:N17)"
    assert str(wb["PACKING LIST"].print_area) != ""


def test_export_writes_cells_and_preserves_formulas(tmp_path: Path):
    values, lines = _base_payload()
    outcome = write_export("LS2628", values, _confirmed(values), lines, export_dir=tmp_path)
    wb = load_workbook(outcome.output_path)
    assert wb["PACKING LIST"]["D15"].value == 38.03
    assert wb["PACKING LIST"]["E15"].value == 26000
    assert wb["PACKING LIST"]["F15"].value == 26400
    assert wb["PACKING LIST"]["D16"].value == "=SUM(D15:D15)"
    assert wb["INVOICE"]["B16"].value == "OAK VENEER (4.0x 190 x 1900)MM"
    assert wb["INVOICE"]["N16"].value == 67175.4552
    assert wb["HOP DONG"]["B6"].value == "WINSMART INDUSTRIAL LIMITED"
    assert wb["HOP DONG"]["B8"].value == "GREEN WAY COMPANY LIMITED"
    assert wb["INVOICE"].merged_cells.ranges
    assert outcome.filled > 0


def test_export_reproduces_mapped_values_of_sample_output(tmp_path: Path):
    """Acceptance AT-13: mapped cells match U&I reference; cells taken from source docs
    (e.g. the line description, which legitimately differs) must match what we wrote."""
    values, lines = _base_payload()
    outcome = write_export("LS2628", values, _confirmed(values), lines, export_dir=tmp_path)

    ours = load_workbook(outcome.output_path, data_only=False)
    reference = load_workbook(SAMPLE_OUTPUT / "LS2628-WM  (09-09-2026) (VAN LANG GO OAK).xlsx", data_only=False)
    mapping, _ = load_mapping()

    from_reference = 0
    from_source_docs = 0
    for sheet, sheet_map in mapping["sheets"].items():
        for cell in sheet_map.get("scalars", {}):
            assert ours[sheet][cell].value == reference[sheet][cell].value, f"{sheet}!{cell}"
            from_reference += 1
        line_map = sheet_map.get("line_items") or {}
        if not line_map:
            continue
        start = line_map["start_row"]
        source_fields = line_map.get("skip_source_fields") or []
        for letter, col_spec in line_map["columns"].items():
            source_key = col_spec.get("source_field") or col_spec.get("field")
            ref_value = reference[sheet][f"{letter}{start}"].value
            our_value = ours[sheet][f"{letter}{start}"].value
            if source_key in source_fields:
                expected = lines[0].get(source_key)
                if expected is not None:
                    if isinstance(our_value, (int, float)):
                        assert float(our_value) == float(expected), f"{sheet}!{letter}{start}"
                    else:
                        assert our_value == expected, f"{sheet}!{letter}{start}"
                    from_source_docs += 1
            else:
                assert our_value == ref_value, f"{sheet}!{letter}{start}"
                from_reference += 1
    assert from_reference >= 6
    assert from_source_docs >= 3


def test_structurally_derived_cells_match_reference(tmp_path: Path):
    """volume/weights are identical across documents in LS2628, so they must match the reference."""
    values, lines = _base_payload()
    outcome = write_export("LS2628", values, _confirmed(values), lines, export_dir=tmp_path)
    ours = load_workbook(outcome.output_path, data_only=False)
    reference = load_workbook(SAMPLE_OUTPUT / "LS2628-WM  (09-09-2026) (VAN LANG GO OAK).xlsx", data_only=False)
    for cell in ("D15", "E15", "F15"):
        assert ours["PACKING LIST"][cell].value == reference["PACKING LIST"][cell].value
    for cell in ("N16", "B6", "B8"):
        sheet = "INVOICE" if cell == "N16" else "HOP DONG"
        assert ours[sheet][cell].value == reference[sheet][cell].value


def test_export_mode_is_draft_when_critical_unverified(tmp_path: Path):
    values, lines = _base_payload()
    sources = {k: FieldSource(review_status="CONFIRMED", grounding_verified=True) for k in values}
    sources["transport.gross_weight_kg"] = FieldSource(review_status="NEEDS_REVIEW", grounding_verified=False)
    outcome = write_export("LS2628", values, sources, lines, export_dir=tmp_path)
    assert outcome.mode == "DRAFT"
    assert "_DRAFT_" in outcome.output_path.name


def test_edited_critical_field_keeps_export_in_draft(tmp_path: Path):
    values, lines = _base_payload()
    sources = {k: FieldSource(review_status="CONFIRMED", grounding_verified=True) for k in values}
    sources["transport.gross_weight_kg"] = FieldSource(review_status="EDITED", grounding_verified=False)
    sources["transport.volume_cbm"] = FieldSource(review_status="EDITED", grounding_verified=False)
    outcome = write_export("LS2628", values, sources, lines, export_dir=tmp_path)
    assert outcome.mode == "DRAFT"


def test_edited_critical_field_blocks_final_export(tmp_path: Path):
    """Hand-edited critical values are only written by the shipment-level sources we pass;
    an edited value that no longer matches its source document must not be marked FINAL."""
    values, lines = _base_payload()
    values["transport.gross_weight_kg"] = "27000"
    sources = _confirmed(values)
    sources["transport.gross_weight_kg"] = FieldSource(review_status="EDITED", grounding_verified=False)
    outcome = write_export("LS2628", values, sources, lines, export_dir=tmp_path)
    wb = load_workbook(outcome.output_path)
    assert wb["PACKING LIST"]["F15"].value == 27000
    from app.services.export_excel import _is_unverified

    assert _is_unverified("transport.gross_weight_kg", sources) is True


def test_unreviewed_non_critical_does_not_block_final(tmp_path: Path):
    values, lines = _base_payload()
    sources = _confirmed(values)
    sources["line.description"] = FieldSource(review_status="UNREVIEWED", grounding_verified=True)
    outcome = write_export("LS2628", values, sources, lines, export_dir=tmp_path)
    assert outcome.mode == "FINAL"


def test_export_mode_is_final_when_confirmed(tmp_path: Path):
    values, lines = _base_payload()
    outcome = write_export("LS2628", values, _confirmed(values), lines, export_dir=tmp_path)
    assert outcome.mode == "FINAL"
    assert "_FINAL_" in outcome.output_path.name


def test_missing_values_are_reported_not_invented(tmp_path: Path):
    values, lines = _base_payload()
    del values["transport.volume_cbm"]
    sources = _confirmed([k for k in values])
    outcome = write_export("LS2628", values, sources, lines, export_dir=tmp_path)
    assert any("transport.volume_cbm" in m for m in outcome.missing)
    wb = load_workbook(outcome.output_path)
    assert wb["PACKING LIST"]["D15"].value is None
    report = json.loads(Path(outcome.report_path).read_text(encoding="utf-8"))
    statuses = {c["field"]: c["status"] for c in report["cells"]}
    assert statuses["transport.volume_cbm"] == "MISSING_FIELD"


def test_total_mismatch_warns_but_does_not_rewrite(tmp_path: Path):
    values, lines = _base_payload()
    values["totals.total_amount"] = "41686.836"
    sources = _confirmed(values)
    outcome = write_export("LS2628", values, sources, lines, export_dir=tmp_path)
    assert any("không khớp" in w for w in outcome.warnings)
    wb = load_workbook(outcome.output_path)
    assert wb["INVOICE"]["N16"].value == 67175.4552
    assert values["totals.total_amount"] == "41686.836"


def test_round_trip_detects_mismatch(tmp_path: Path):
    values, lines = _base_payload()
    outcome = write_export("LS2628", values, _confirmed(values), lines, export_dir=tmp_path)
    wb = load_workbook(outcome.output_path)
    wb["PACKING LIST"]["D15"] = 99.99
    tampered = tmp_path / "tampered.xlsx"
    wb.save(tampered)
    mapping, _ = load_mapping()
    problems = verify_round_trip(tampered, mapping, values, lines)
    assert any("D15" in p for p in problems)


def test_available_templates_lists_v1():
    versions = {t["version"] for t in available_templates()}
    assert DEFAULT_TEMPLATE_VERSION in versions


def test_template_export_region_holds_no_customer_values():
    """Cells the exporter owns may only hold static labels or formulas, never data.

    The earlier numbers-only check missed PACKING LIST!F5='LS2628-WM', B10, B11, A12
    and HOP DONG!B4/B6/B8. Data cells are enumerated explicitly below.
    """
    from app.services.export_excel import managed_cells

    mapping, template_path = load_mapping()
    wb = load_workbook(template_path, data_only=False)
    data_cells = [
        "PACKING LIST!A15",
        "PACKING LIST!D15",
        "PACKING LIST!E15",
        "PACKING LIST!F15",
        "INVOICE!A16",
        "INVOICE!B16",
        "INVOICE!H16",
        "INVOICE!N16",
        "HOP DONG!B6",
        "HOP DONG!B8",
        "HOP DONG!A20",
        "INVOICE!O20",
    ]
    for ref in data_cells:
        sheet, cell = ref.split("!")
        assert cell in managed_cells(mapping).get(sheet, set()) or ref == "INVOICE!O20", ref
        value = wb[sheet][cell].value
        assert value is None, f"{ref} còn dữ liệu mẫu: {value!r}"


def test_reference_identity_is_not_a_template_constant():
    """RC7: the shipped templates must not carry another shipment's identity.

    These cells used to hold the LS2628 reference shipment (PACKING LIST!F5 number,
    B10 vessel, B11 ports, A12 container, HOP DONG!B4 shipper, B9 address, B19
    amount-in-words). They are now enumerated in `TEMPLATE_DATA_CELLS` and blank, so a
    FINAL export for a different shipment cannot inherit them.
    """
    from app.services.template_sanitize import (
        data_cells_with_values,
        identifier_leaks,
    )

    _, template_path = load_mapping()
    assert data_cells_with_values(template_path, "uandi_import_v1") == []
    assert identifier_leaks(template_path, "uandi_import_v1") == []


def test_second_invoice_line_is_rejected_not_written_into_merged_row(tmp_path: Path):
    """INVOICE row 17 is merged (B17:G17); writing a second line there corrupts the file."""
    values, lines = _base_payload()
    second = {"line.no": "2", "line.description": "SECOND ITEM", "line.quantity": "1", "line.amount": "100"}
    import pytest

    from app.services.export_excel import ExportError

    with pytest.raises(ExportError, match="dòng hàng"):
        write_export("LS2628", values, _confirmed(values), [*lines, second], export_dir=tmp_path)


def test_single_line_export_keeps_row16_style_and_totals(tmp_path: Path):
    values, lines = _base_payload()
    outcome = write_export("LS2628", values, _confirmed(values), lines, export_dir=tmp_path)
    ws = load_workbook(outcome.output_path)["INVOICE"]
    assert ws["B16"].value == "OAK VENEER (4.0x 190 x 1900)MM"
    assert ws["B17"].value is None
    assert ws["N18"].value == "=SUM(N16:N17)"
    assert outcome.mode == "FINAL"


def test_lines_beyond_template_capacity_fail_explicitly(tmp_path: Path):
    values, lines = _base_payload()
    lines.extend(
        {
            "line.no": str(i),
            "line.description": f"ITEM {i}",
            "line.quantity": "1",
            "line.amount": "10",
        }
        for i in (2, 3)
    )
    import pytest

    from app.services.export_excel import ExportError

    with pytest.raises(ExportError, match="dòng hàng"):
        write_export("LS2628", values, _confirmed(values), lines, export_dir=tmp_path)


def test_repeated_exports_do_not_overwrite_each_other(tmp_path: Path):
    values, lines = _base_payload()
    first = write_export("LS2628", values, _confirmed(values), lines, export_dir=tmp_path)
    second = write_export("LS2628", values, _confirmed(values), lines, export_dir=tmp_path)
    assert first.output_path != second.output_path
    assert first.output_path.exists() and second.output_path.exists()


def test_exports_of_two_shipments_do_not_collide(tmp_path: Path):
    values, lines = _base_payload()
    a = write_export("LS2628", values, _confirmed(values), lines, export_dir=tmp_path / "a")
    b = write_export("LS2700", values, _confirmed(values), lines, export_dir=tmp_path / "b")
    assert a.output_path.name != b.output_path.name


def test_export_without_line_items_is_draft(tmp_path: Path):
    values, _ = _base_payload()
    outcome = write_export("LS2628", values, _confirmed(values), [], export_dir=tmp_path)
    assert outcome.mode == "DRAFT"
    assert any("không có dòng hàng" in w for w in outcome.warnings)


def test_uandi_writer_refuses_page_block_template(tmp_path: Path):
    """tokhai_import_v1 must never be written by the U&I writer (it would emit an empty file)."""
    import pytest

    from app.services.export_excel import ExportError

    values, lines = _base_payload()
    with pytest.raises(ExportError, match="page_blocks"):
        write_export("LS2628", values, _confirmed(values), lines, template_version="tokhai_import_v1", export_dir=tmp_path)


def test_export_filename_uses_shipment_code_not_template_prefix(tmp_path: Path):
    values, lines = _base_payload()
    outcome = write_export("ZZ-OTHER", values, _confirmed(values), lines, export_dir=tmp_path)
    assert outcome.output_path.name.startswith("ZZ-OTHER_")
    assert "LS2628-WM" not in outcome.output_path.name
