from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_shipment.py"
GROUND_TRUTH = ROOT / "evaluation" / "taeda_ground_truth.csv"
LINE_ITEMS = ROOT / "evaluation" / "taeda_line_items.csv"


def _load_evaluator():
    import sys

    spec = importlib.util.spec_from_file_location("evaluate_shipment", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _eligible_count() -> int:
    with GROUND_TRUTH.open(encoding="utf-8") as fh:
        return sum(1 for row in csv.DictReader(fh) if row["eligibility"] == "INPUT_SUPPORTED")


def test_ground_truth_covers_the_51_audit_items():
    rows = list(csv.DictReader(GROUND_TRUTH.open(encoding="utf-8")))
    assert len(rows) == 51
    assert {row["eligibility"] for row in rows} <= {
        "INPUT_SUPPORTED",
        "REFERENCE_ONLY",
        "CUSTOMS_GENERATED",
        "REQUIRES_USER",
        "NOT_APPLICABLE",
        "UNKNOWN",
    }
    assert not any(row["reviewer_confirmed"] == "true" for row in rows)


def test_evaluation_reports_denominators_and_separates_customs_fields(tmp_path):
    module = _load_evaluator()
    mapping, template_path = __import__("app.services.export_excel", fromlist=["load_mapping"]).load_mapping(
        "tokhai_import_v1"
    )
    wb = load_workbook(template_path)
    tkn = wb["TKN"]
    for cell, value in {
        "H10": "3702474140",
        "K36": "28",
        "K37": "52312",
        "K38": "2",
        "J43": "14/04/2026",
        "G149": "44071100",
        "V153": "46.46",
        "I155": "11615",
    }.items():
        tkn[cell] = value
    # Simulate the old leak: a customs-generated declaration number in a DRAFT.
    tkn["E4"] = "108299395961"
    workbook = tmp_path / "eval.xlsx"
    wb.save(workbook)

    evaluation = module.evaluate(workbook, module.load_ground_truth(GROUND_TRUTH), LINE_ITEMS)
    report = evaluation.report()
    fields_report = report["fields"]

    assert fields_report["total"] == 51
    assert fields_report["eligible_field_coverage"]["denominator"] == _eligible_count()
    assert fields_report["eligible_field_coverage"]["numerator"] == 7
    assert fields_report["eligible_field_coverage"]["value"] == round(7 / _eligible_count(), 4)
    assert fields_report["unsupported_value_rate"]["numerator"] >= 1
    assert fields_report["field_accuracy"]["denominator"] == 9
    assert any("not fully reviewer-confirmed" in note for note in report["notes"])
    assert "invoice_container_item" in report["line_items"]
    assert "customs_goods_line" in report["line_items"]


def test_evaluation_skips_cleanly_when_workbook_is_missing(tmp_path, capsys):
    module = _load_evaluator()
    code = module.main(["--workbook", str(tmp_path / "absent.xlsx")])
    assert code == 2
    assert "SKIP" in capsys.readouterr().err


def test_evaluation_writes_a_reproducible_artifact(tmp_path):
    module = _load_evaluator()
    _, template_path = __import__("app.services.export_excel", fromlist=["load_mapping"]).load_mapping(
        "tokhai_import_v1"
    )
    wb = load_workbook(template_path)
    wb["TKN"]["K37"] = "52312"
    workbook = tmp_path / "eval.xlsx"
    wb.save(workbook)
    out = tmp_path / "reports" / "taeda.json"

    code = module.main(["--workbook", str(workbook), "--out", str(out)])
    assert code == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["fields"]["eligible_field_coverage"]["denominator"] == _eligible_count()
    assert report["rows"][20]["target_field"] == "declaration.gross_weight"
    assert report["rows"][20]["correct"] is True


def test_line_item_recall_uses_values_not_row_count(tmp_path):
    module = _load_evaluator()
    _, template_path = __import__("app.services.export_excel", fromlist=["load_mapping"]).load_mapping(
        "tokhai_import_v1"
    )
    wb = load_workbook(template_path)
    tkn = wb["TKN"]
    for qty, unit, amount, origin in (
        ("V153", "AE153", "I155", "X160"),
        ("V206", "AE206", "I208", "X213"),
    ):
        tkn[qty] = "46.46"
        tkn[unit] = "MTQ"
        tkn[amount] = "11615"
        tkn[origin] = "AR"
    good = tmp_path / "good.xlsx"
    wb.save(good)
    report = module.evaluate(good, module.load_ground_truth(GROUND_TRUTH), LINE_ITEMS).report()
    assert report["line_items"]["customs_goods_line"]["numerator"] == 2
    assert report["line_items"]["customs_goods_line"]["denominator"] == 2

    # A wrong quantity must reduce recall; the old metric only counted rows.
    tkn["V206"] = "99.99"
    bad = tmp_path / "bad.xlsx"
    wb.save(bad)
    report = module.evaluate(bad, module.load_ground_truth(GROUND_TRUTH), LINE_ITEMS).report()
    assert report["line_items"]["customs_goods_line"]["numerator"] == 1
    assert report["line_items"]["customs_goods_line"]["denominator"] == 2


def test_invoice_row_recall_requires_stage_one_extraction(tmp_path):
    module = _load_evaluator()
    _, template_path = __import__("app.services.export_excel", fromlist=["load_mapping"]).load_mapping(
        "tokhai_import_v1"
    )
    wb = load_workbook(template_path)
    workbook = tmp_path / "rows.xlsx"
    wb.save(workbook)

    without = module.evaluate(workbook, module.load_ground_truth(GROUND_TRUTH), LINE_ITEMS).report()
    invoice = without["line_items"]["invoice_container_item"]
    assert invoice["value"] is None
    assert invoice["denominator"] == 2
    assert "not in the workbook" in invoice["reason"]

    extraction = [
        {"container_no": "HMMU6849510", "quantity": "46.46", "unit": "CBM", "amount": "11615", "origin_country_code": "AR"},
        {"container_no": "KOCU4411578", "quantity": "46.46", "unit": "CBM", "amount": "11615", "origin_country_code": "AR"},
    ]
    withrows = module.evaluate(
        workbook, module.load_ground_truth(GROUND_TRUTH), LINE_ITEMS, extraction
    ).report()
    assert withrows["line_items"]["invoice_container_item"]["numerator"] == 2
    assert withrows["line_items"]["invoice_container_item"]["denominator"] == 2
