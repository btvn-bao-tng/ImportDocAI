"""Deterministic evaluation of a TKN export against the audited ground truth.

Metrics are always reported as numerator/denominator, and only INPUT_SUPPORTED
fields count toward eligible coverage. Customs-generated and reference-only fields
are reported separately so an empty cell for "Số tờ khai" is never scored as a miss.

Usage:
    python scripts/evaluate_shipment.py \
        --workbook path/to/export.xlsx \
        --ground-truth ../../evaluation/taeda_ground_truth.csv \
        --line-items ../../evaluation/taeda_line_items.csv \
        --out ../../evaluation/reports/taeda.json
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

ELIGIBLE = "INPUT_SUPPORTED"
ELIGIBILITY_VALUES = {
    "INPUT_SUPPORTED",
    "REFERENCE_ONLY",
    "CUSTOMS_GENERATED",
    "REQUIRES_USER",
    "NOT_APPLICABLE",
    "UNKNOWN",
}


@dataclass
class GroundTruthRow:
    n: int
    target_field: str
    output_cell: str
    expected_value: str
    value_type: str
    source_document: str
    source_ref: str
    eligibility: str
    audit_status: str
    reviewer_confirmed: bool
    note: str = ""


@dataclass
class Evaluation:
    workbook: str
    total: int = 0
    by_eligibility: dict[str, int] = field(default_factory=dict)
    eligible_total: int = 0
    eligible_correct: int = 0
    populated_total: int = 0
    populated_correct: int = 0
    unsupported_populated: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)
    line_items: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def report(self) -> dict[str, Any]:
        return {
            "workbook": self.workbook,
            "fields": {
                "total": self.total,
                "by_eligibility": self.by_eligibility,
                "eligible_field_coverage": _ratio(self.eligible_correct, self.eligible_total),
                "field_accuracy": _ratio(self.populated_correct, self.populated_total),
                "unsupported_value_rate": _ratio(self.unsupported_populated, self.populated_total),
            },
            "line_items": self.line_items,
            "rows": self.rows,
            "notes": self.notes,
        }


def _ratio(num: int, den: int) -> dict[str, Any]:
    return {"numerator": num, "denominator": den, "value": round(num / den, 4) if den else None}


def _split_cell(cell: str) -> tuple[str, str]:
    if "!" in cell:
        sheet, coord = cell.split("!", 1)
        return sheet, coord
    return "TKN", cell


def _normalize(value: Any, value_type: str) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    if value_type in {"decimal", "int"}:
        text = text.replace(" ", "").replace("\u00a0", "")
        if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", text):
            text = text.replace(".", "").replace(",", ".")
        elif text.count(",") == 1 and "." not in text:
            text = text.replace(",", ".")
        try:
            number = float(text)
            return str(int(number)) if number.is_integer() else str(number)
        except ValueError:
            return text.lower()
    return " ".join(text.lower().split())


def _cell_value(wb, cell: str) -> Any:
    sheet, coord = _split_cell(cell)
    if sheet not in wb.sheetnames:
        return None
    return wb[sheet][coord].value


def load_ground_truth(path: Path) -> list[GroundTruthRow]:
    rows: list[GroundTruthRow] = []
    with path.open(encoding="utf-8") as fh:
        for item in csv.DictReader(fh):
            eligibility = (item.get("eligibility") or "UNKNOWN").strip()
            rows.append(
                GroundTruthRow(
                    n=int(item["n"]),
                    target_field=item["target_field"],
                    output_cell=item["output_cell"],
                    expected_value=item.get("expected_value") or "",
                    value_type=item.get("value_type") or "text",
                    source_document=item.get("source_document") or "",
                    source_ref=item.get("source_ref") or "",
                    eligibility=eligibility,
                    audit_status=item.get("audit_status") or "",
                    reviewer_confirmed=(item.get("reviewer_confirmed") or "").strip().lower() == "true",
                    note=item.get("note") or "",
                )
            )
    return rows


def evaluate(
    workbook_path: Path,
    truth: list[GroundTruthRow],
    line_items_path: Path | None = None,
    extraction: list[dict[str, Any]] | None = None,
) -> Evaluation:
    evaluation = Evaluation(workbook=str(workbook_path))
    wb = load_workbook(workbook_path, data_only=True)
    for row in truth:
        if row.eligibility not in ELIGIBILITY_VALUES:
            evaluation.notes.append(f"row {row.n}: unknown eligibility {row.eligibility!r}, treated as UNKNOWN")
        evaluation.total += 1
        evaluation.by_eligibility[row.eligibility] = evaluation.by_eligibility.get(row.eligibility, 0) + 1

        actual = _cell_value(wb, row.output_cell)
        expected_norm = _normalize(row.expected_value, row.value_type)
        actual_norm = _normalize(actual, row.value_type)
        populated = actual_norm != ""
        correct = populated and actual_norm == expected_norm
        if row.eligibility == ELIGIBLE:
            evaluation.eligible_total += 1
            evaluation.eligible_correct += int(correct)
        if populated:
            evaluation.populated_total += 1
            evaluation.populated_correct += int(correct)
            # A populated value the audit classifies as customs/reference/user-only has no
            # allowed input evidence for a NEW_DRAFT.
            if row.eligibility in {"CUSTOMS_GENERATED", "REFERENCE_ONLY", "REQUIRES_USER", "NOT_APPLICABLE"}:
                evaluation.unsupported_populated += 1
        evaluation.rows.append(
            {
                "n": row.n,
                "target_field": row.target_field,
                "cell": row.output_cell,
                "eligibility": row.eligibility,
                "expected": row.expected_value,
                "actual": actual,
                "populated": populated,
                "correct": correct,
                "reviewer_confirmed": row.reviewer_confirmed,
                "note": row.note,
            }
        )
    if not all(row.reviewer_confirmed for row in truth):
        evaluation.notes.append(
            "ground truth is not fully reviewer-confirmed; treat accuracy as a baseline, not a KPI"
        )
    if line_items_path and line_items_path.exists():
        evaluation.line_items = _line_item_recall(wb, line_items_path, extraction)
    return evaluation


def _goods_cells() -> list[tuple[str, str, str, str]]:
    """(quantity_1, quantity_unit, amount, origin) cells for each goods block."""
    from app.services.export_excel import load_mapping

    mapping, _ = load_mapping("tokhai_import_v1")
    blocks = mapping["sheets"]["TKN"]["line_blocks"]
    columns = blocks.get("columns") or {}
    starts = [int(b) for b in blocks.get("block_starts") or []]
    first = starts[0] if starts else 0

    def ref_for(field: str, block_start: int) -> str:
        for ref, spec in columns.items():
            if spec["field"] == field:
                letter = "".join(ch for ch in ref if ch.isalpha())
                row = int("".join(ch for ch in ref if ch.isdigit()))
                return f"{letter}{row + (block_start - first)}"
        raise KeyError(field)

    cells = []
    for start in starts:
        cells.append(
            (
                ref_for("goods.quantity_1", start),
                ref_for("goods.quantity_1_unit", start),
                ref_for("goods.invoice_amount", start),
                ref_for("goods.origin_country_code", start),
            )
        )
    return cells


def _line_values(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        _normalize(row.get("quantity"), "decimal"),
        _normalize(row.get("unit"), "text"),
        _normalize(row.get("amount"), "decimal"),
        _normalize(row.get("origin_country_code"), "text"),
    )


def _match_rows(expected: list[dict[str, Any]], actual: list[tuple[str, str, str, str]]) -> int:
    bucket = list(actual)
    matched = 0
    for row in expected:
        wanted = _line_values(row)
        for idx, candidate in enumerate(bucket):
            if candidate == wanted:
                bucket.pop(idx)
                matched += 1
                break
    return matched


def _line_item_recall(wb, path: Path, extraction: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    expected = list(csv.DictReader(path.open(encoding="utf-8")))
    goods_actual: list[tuple[str, str, str, str]] = []
    if "TKN" in wb.sheetnames:
        ws = wb["TKN"]
        for qty_cell, unit_cell, amount_cell, origin_cell in _goods_cells():
            qty = ws[qty_cell].value
            amount = ws[amount_cell].value
            if qty in (None, "") and amount in (None, ""):
                continue
            goods_actual.append(
                (
                    _normalize(qty, "decimal"),
                    _normalize(ws[unit_cell].value, "text"),
                    _normalize(amount, "decimal"),
                    _normalize(ws[origin_cell].value, "text"),
                )
            )
    result: dict[str, Any] = {}
    customs_expected = [row for row in expected if row["row_type"] == "customs_goods_line"]
    if customs_expected:
        # Customs rows are matched on actual cell values, including unit and origin.
        # Expected units in the CSV are commercial units, so compare unit families.
        normalized_expected = [
            {**row, "unit": "MTQ" if (row.get("unit") or "").upper() in {"CBM", "M3", "MTQ"} else row.get("unit")}
            for row in customs_expected
        ]
        result["customs_goods_line"] = _ratio(
            _match_rows(normalized_expected, goods_actual), len(customs_expected)
        )
    invoice_expected = [row for row in expected if row["row_type"] == "invoice_container_item"]
    if invoice_expected:
        if extraction is None:
            result["invoice_container_item"] = {
                "numerator": None,
                "denominator": len(invoice_expected),
                "value": None,
                "reason": "commercial container rows are not in the workbook; pass --extraction-json",
            }
        else:
            actual_invoice = [
                (
                    _normalize(item.get("quantity"), "decimal"),
                    _normalize(item.get("unit"), "text"),
                    _normalize(item.get("amount"), "decimal"),
                    _normalize(item.get("origin_country_code"), "text"),
                )
                for item in extraction
                if item.get("container_no")
            ]
            result["invoice_container_item"] = _ratio(
                _match_rows(invoice_expected, actual_invoice), len(invoice_expected)
            )
    result["actual_goods_rows"] = goods_actual
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a TKN export against audited ground truth")
    parser.add_argument("--workbook", required=True)
    parser.add_argument("--ground-truth", default=str(Path(__file__).resolve().parents[3] / "evaluation" / "taeda_ground_truth.csv"))
    parser.add_argument("--line-items", default=str(Path(__file__).resolve().parents[3] / "evaluation" / "taeda_line_items.csv"))
    parser.add_argument("--extraction-json", help="Stage-1 line items (JSON list) for invoice-row recall")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    workbook = Path(args.workbook)
    if not workbook.exists():
        print(f"SKIP: workbook not found: {workbook}", file=sys.stderr)
        return 2
    truth_path = Path(args.ground_truth)
    if not truth_path.exists():
        print(f"SKIP: ground truth not found: {truth_path}", file=sys.stderr)
        return 2

    extraction = None
    if args.extraction_json:
        payload = json.loads(Path(args.extraction_json).read_text(encoding="utf-8"))
        extraction = payload if isinstance(payload, list) else payload.get("line_items", [])
    evaluation = evaluate(workbook, load_ground_truth(truth_path), Path(args.line_items), extraction)
    report = evaluation.report()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
