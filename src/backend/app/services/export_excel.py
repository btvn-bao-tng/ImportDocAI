from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import column_index_from_string, get_column_letter

from .fields import ALL_FIELD_SPECS, FieldSpec, is_critical

log = logging.getLogger("importdoc.export")

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates_config"
ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets" / "templates"
DEFAULT_TEMPLATE_VERSION = "uandi_import_v1"
MONEY_TOLERANCE = Decimal("0.01")


@dataclass
class FieldSource:
    document_filename: str | None = None
    document_id: int | None = None
    page: int | None = None
    cell_ref: str | None = None
    provenance_type: str | None = None
    review_status: str | None = None
    grounding_verified: bool | None = None
    confidence: float | None = None


@dataclass
class ExportOutcome:
    output_path: Path
    report_path: Path
    mode: str
    filled: int
    missing: list[str]
    warnings: list[str]


class ExportError(Exception):
    pass


def load_mapping(template_version: str = DEFAULT_TEMPLATE_VERSION) -> tuple[dict[str, Any], Path]:
    base = TEMPLATES_DIR / template_version
    candidates = sorted(base.glob("*.json")) if base.is_dir() else []
    if not candidates and (TEMPLATES_DIR / f"{template_version}.json").exists():
        candidates = [TEMPLATES_DIR / f"{template_version}.json"]
    if not candidates:
        raise ExportError(f"Không tìm thấy mapping cho template '{template_version}'")
    mapping = json.loads(candidates[-1].read_text(encoding="utf-8"))
    template_file = Path(mapping.get("template_file") or "")
    if not template_file.name:
        raise ExportError("Mapping thiếu 'template_file'")
    template_path = template_file if template_file.is_absolute() else ASSETS_DIR / template_file.name
    if not template_path.exists():
        raise ExportError(f"Không tìm thấy file template: {template_path}")
    return mapping, template_path


VERIFY_TEMPLATE_VERSION = "verify_sheet_v1"


def load_verify_mapping() -> dict[str, Any]:
    """Mapping generated on the fly: every extracted field lands in its own row.

    The verify page shows the real extracted fields side by side with the source file,
    so this sheet is a faithful, template-free projection of what was extracted — not a
    form that customs would accept.
    """
    return {
        "template_version": VERIFY_TEMPLATE_VERSION,
        "name": "Bảng đối chiếu trích xuất",
        "layout": "verify",
        "sheets": {},
    }


def write_verify_export(
    shipment_code: str,
    rows: list[dict[str, Any]],
    template_version: str = VERIFY_TEMPLATE_VERSION,
    export_dir: Path | None = None,
    today: date | None = None,
) -> ExportOutcome:
    """Write one row per extracted field so the user can check files against the final xlsx."""
    wb = Workbook()
    ws = wb.active
    ws.title = "DOI_CHIEU"
    headers = ["STT", "Chứng từ", "Loại", "Trường", "Tên trường", "Giá trị", "Đơn vị", "Trang/ô", "Trạng thái", "Nguồn"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="EAF2FD")
    widths = [6, 28, 18, 34, 30, 34, 10, 14, 16, 22]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width

    filled = 0
    missing: list[str] = []
    for seq, row in enumerate(rows, start=1):
        location = ""
        if row.get("page"):
            location = f"trang {row['page']}"
        if row.get("cell_ref"):
            location = row["cell_ref"]
        status = "Đã xác nhận" if row.get("review_status") == "CONFIRMED" else (
            "Cần xem lại" if row.get("critical") else "Chưa xem"
        )
        ws.append(
            [
                seq,
                row.get("filename"),
                row.get("doc_type"),
                row.get("field_key"),
                row.get("label_vi"),
                row.get("value"),
                row.get("unit"),
                location,
                status,
                row.get("provenance"),
            ]
        )
        if row.get("value") in (None, ""):
            missing.append(row.get("field_key"))
        else:
            filled += 1
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:J{max(1, len(rows) + 1)}"

    file_code = shipment_code or "EXPORT"
    date_tag = (today or date.today()).strftime("%Y%m%d")
    out_dir = export_dir or (Path("data/exports") / shipment_code)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    base_name = f"{file_code}_{template_version}_FINAL_{date_tag}_{stamp}"
    out_path = out_dir / f"{base_name}.xlsx"
    seq = 2
    while out_path.exists():
        out_path = out_dir / f"{base_name}_{seq}.xlsx"
        seq += 1
    wb.save(out_path)

    warnings: list[str] = []
    if missing:
        warnings.append(f"{len(missing)} trường chưa có giá trị")
    report_path = out_dir / f"{out_path.stem}_report.json"
    report_path.write_text(
        json.dumps({"shipment": shipment_code, "filled": filled, "missing": missing}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return ExportOutcome(
        output_path=out_path,
        report_path=report_path,
        mode="FINAL",
        filled=filled,
        missing=missing,
        warnings=warnings,
    )


def available_templates() -> list[dict[str, Any]]:
    result = []
    for path in sorted(TEMPLATES_DIR.glob("*/")):
        for json_file in path.glob("*.json"):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                result.append({"version": data.get("template_version"), "name": data.get("name")})
            except Exception:
                continue
    return result


def _display_value(value: str | None, spec: FieldSpec | None) -> Any:
    if value is None:
        return None
    if spec is None:
        return value
    if spec.kind == "decimal":
        try:
            d = Decimal(value)
            return int(d) if d == d.to_integral_value() else float(d)
        except Exception:
            return value
    if spec.kind == "int":
        try:
            return int(value)
        except Exception:
            return value
    return value


def write_export(
    shipment_code: str,
    values: dict[str, str | None],
    sources: dict[str, FieldSource],
    line_items: list[dict[str, str | None]] | dict[str, list[dict[str, str | None]]],
    template_version: str = DEFAULT_TEMPLATE_VERSION,
    export_dir: Path | None = None,
    today: date | None = None,
) -> ExportOutcome:
    mapping, template_path = load_mapping(template_version)
    if mapping.get("layout") == "page_blocks":
        raise ExportError(
            f"Template '{template_version}' dùng layout page_blocks — phải xuất qua export_tokhai, "
            "không dùng write_export của U&I."
        )
    wb = load_workbook(template_path)
    _clear_stale_cells(wb, mapping)

    filled = 0
    missing: list[str] = []
    warnings: list[str] = []
    cells_report: list[dict[str, Any]] = []
    critical_unreviewed = False

    for sheet_name, sheet_map in (mapping.get("sheets") or {}).items():
        if sheet_name not in wb.sheetnames:
            warnings.append(f"Template thiếu sheet '{sheet_name}'")
            continue
        ws = wb[sheet_name]

        for cell_ref, spec in (sheet_map.get("scalars") or {}).items():
            field_key = spec["field"]
            field_spec = ALL_FIELD_SPECS.get(field_key)
            value = values.get(field_key)
            if value is None and spec.get("optional"):
                continue
            if value is None:
                missing.append(f"{sheet_name}!{cell_ref} ← {field_key}")
                cells_report.append(
                    {"sheet": sheet_name, "cell": cell_ref, "field": field_key, "value": None, "status": "MISSING_FIELD"}
                )
                continue
            _write_cell(ws, cell_ref, _display_value(value, field_spec))
            filled += 1
            cells_report.append({"sheet": sheet_name, "cell": cell_ref, "field": field_key, "value": value, "status": "FILLED"})
            if _is_unverified(field_key, sources):
                critical_unreviewed = True

        line_map = sheet_map.get("line_items") or {}
        if not line_map:
            continue
        sheet_lines = _lines_for_sheet(line_items, sheet_name)
        start_row = int(line_map.get("start_row") or 16)
        columns = line_map.get("columns") or {}
        row_of = _allocate_rows(len(sheet_lines), line_map)
        if len(sheet_lines) > len(row_of):
            raise ExportError(
                f"Sheet '{sheet_name}' chỉ chứa được {len(row_of)} dòng hàng nhưng hồ sơ có "
                f"{len(sheet_lines)} dòng. Cần tách hồ sơ hoặc mở rộng template — không tự chèn dòng."
            )
        for idx, item in enumerate(sheet_lines):
            row_idx = row_of[idx]
            _copy_row_style(ws, start_row, row_idx)
            for letter, col_spec in columns.items():
                source_key = col_spec.get("source_field") or col_spec.get("field")
                value = item.get(source_key)
                if value is None:
                    continue
                _write_cell(ws, f"{letter}{row_idx}", _display_value(value, ALL_FIELD_SPECS.get(col_spec.get("field"))))
                filled += 1
                cells_report.append(
                    {"sheet": sheet_name, "cell": f"{letter}{row_idx}", "field": source_key, "value": value, "status": "FILLED"}
                )
                if _is_unverified(source_key, sources):
                    critical_unreviewed = True
        if not sheet_lines:
            warnings.append(f"Sheet '{sheet_name}': không có dòng hàng để điền")
        else:
            last_row = max(row_of) if row_of else start_row
            overflow = _print_area_overflow(wb, sheet_name, last_row)
            if overflow:
                warnings.append(overflow)

    _check_total(values, _lines_for_sheet(line_items, "INVOICE"), warnings)

    empty_line_sheets = [
        sheet
        for sheet, sheet_map in (mapping.get("sheets") or {}).items()
        if (sheet_map.get("line_items") or {}) and not _lines_for_sheet(line_items, sheet)
    ]
    mode = "DRAFT" if critical_unreviewed or missing or empty_line_sheets else "FINAL"
    # The shipment code identifies the file; the template prefix is only a fallback so a
    # new shipment never inherits a previous job's code (e.g. LS2628-WM).
    file_code = shipment_code or mapping.get("file_code_prefix") or "EXPORT"
    date_tag = (today or date.today()).strftime("%Y%m%d")
    out_dir = export_dir or (Path("data/exports") / shipment_code)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    base_name = f"{file_code}_{template_version}_{mode}_{date_tag}_{stamp}"
    out_path = out_dir / f"{base_name}.xlsx"
    seq = 2
    while out_path.exists():
        out_path = out_dir / f"{base_name}_{seq}.xlsx"
        seq += 1
    wb.save(out_path)

    problems = verify_round_trip(out_path, mapping, values, line_items)
    warnings.extend(problems)
    if problems and mode == "FINAL":
        # A file that fails its own round-trip is never FINAL, whatever the field gate said.
        out_path = _demote_to_draft(out_path)
        mode = "DRAFT"

    report_path = out_dir / f"{out_path.stem}_report.json"
    report_path.write_text(
        json.dumps(
            {
                "shipment": shipment_code,
                "template_version": template_version,
                "mode": mode,
                "filled": filled,
                "missing": missing,
                "warnings": warnings,
                "cells": cells_report,
                "sources": {k: v.__dict__ for k, v in sources.items()},
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return ExportOutcome(out_path, report_path, mode, filled, missing, warnings)


def _demote_to_draft(path: Path) -> Path:
    if "_FINAL_" not in path.name:
        return path
    target = path.with_name(path.name.replace("_FINAL_", "_DRAFT_"))
    seq = 2
    base = target
    while target.exists():
        target = base.with_name(f"{base.stem}_{seq}{base.suffix}")
        seq += 1
    path.rename(target)
    return target


def _lines_for_sheet(
    line_items: list[dict[str, str | None]] | dict[str, list[dict[str, str | None]]], sheet_name: str
) -> list[dict[str, str | None]]:
    if isinstance(line_items, dict):
        return line_items.get(sheet_name, [])
    return line_items


def _write_cell(ws, cell_ref: str, value: Any) -> None:
    cell = ws[cell_ref]
    if isinstance(cell, MergedCell):
        for merged in ws.merged_cells.ranges:
            if cell.coordinate in merged:
                ws[merged.start_cell.coordinate] = value
                return
        raise ExportError(f"{ws.title}!{cell_ref} là merged cell không ghi được — kiểm tra mapping")
    cell.value = value


def managed_cells(mapping: dict[str, Any]) -> dict[str, set[str]]:
    """Every cell the exporter owns, per sheet. Single source of truth for clearing,
    writing and stale-data checks so they can never drift apart."""
    plan: dict[str, set[str]] = {}
    for sheet_name, sheet_map in (mapping.get("sheets") or {}).items():
        cells: set[str] = set()
        for cell_ref in (sheet_map.get("scalars") or {}):
            cells.add(cell_ref)
        line_map = sheet_map.get("line_items") or {}
        start_row = int(line_map.get("start_row") or 16)
        span = int(line_map.get("clear_rows") or 60)
        for offset in range(span):
            for letter in (line_map.get("columns") or {}):
                cells.add(f"{letter}{start_row + offset}")
        plan[sheet_name] = cells
    return plan


def _clear_stale_cells(wb, mapping: dict[str, Any]) -> None:
    """Blank every cell the export may write so stale reference data can never leak."""
    for sheet_name, cells in managed_cells(mapping).items():
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        for cell_ref in cells:
            _clear_cell(ws, cell_ref)


def _clear_cell(ws, cell_ref: str) -> None:
    cell = ws[cell_ref]
    if isinstance(cell, MergedCell):
        return
    if isinstance(cell.value, str) and cell.value.startswith("="):
        return
    cell.value = None


def _print_area_overflow(wb, sheet_name: str, last_row: int) -> str | None:
    ws = wb[sheet_name]
    area = ws.print_area
    if not area:
        return None
    limit = 0
    for part in str(area).split(","):
        end = part.split("!")[-1].split(":")[-1]
        digits = "".join(ch for ch in end if ch.isdigit())
        if digits:
            limit = max(limit, int(digits))
    if limit and last_row > limit:
        return (
            f"Sheet '{sheet_name}': {last_row} dòng hàng vượt vùng in (tới dòng {limit}); "
            "dữ liệu ngoài vùng in cần review hoặc mở rộng template"
        )
    return None


def _is_unverified(field_key: str, sources: dict[str, FieldSource]) -> bool:
    """Only critical matter blocks a FINAL export; other fields may stay unreviewed."""
    if not is_critical(field_key):
        return False
    src = sources.get(field_key)
    if src is None:
        return False
    return src.review_status in {"NEEDS_REVIEW", "EDITED"} or src.grounding_verified is False


def _capacity(line_map: dict[str, Any]) -> int:
    """Declared capacity wins; otherwise derive it from end_row, else from clear_rows."""
    if line_map.get("capacity") is not None:
        return int(line_map["capacity"])
    if line_map.get("end_row") is not None:
        return int(line_map["end_row"]) - int(line_map.get("start_row") or 16) + 1
    return int(line_map.get("clear_rows") or 60)


def _allocate_rows(count: int, line_map: dict[str, Any]) -> list[int]:
    """Contiguous rows from `start_row`, never more than the declared capacity."""
    start_row = int(line_map.get("start_row") or 16)
    return [start_row + i for i in range(min(count, _capacity(line_map)))]


def _copy_row_style(ws, source_row: int, target_row: int) -> None:
    """Carry cell styles from the template's first item row to appended rows."""
    if source_row == target_row:
        return
    from copy import copy

    for col_idx in range(1, ws.max_column + 1):
        src = ws.cell(row=source_row, column=col_idx)
        dst = ws.cell(row=target_row, column=col_idx)
        if isinstance(dst, MergedCell):
            continue
        if src.has_style:
            dst._style = copy(src._style)


def _check_total(values: dict[str, str | None], line_items: list[dict[str, str | None]], warnings: list[str]) -> None:
    declared = values.get("totals.total_amount")
    if declared is None:
        return
    amounts: list[Decimal] = []
    for item in line_items:
        raw = item.get("line.amount")
        if raw is None:
            continue
        try:
            amounts.append(Decimal(raw))
        except Exception:
            return
    if not amounts:
        return
    expected = sum(amounts)
    try:
        declared_dec = Decimal(declared)
    except Exception:
        return
    if abs(expected - declared_dec) > MONEY_TOLERANCE:
        warnings.append(f"Tổng tiền chứng từ ({declared_dec}) không khớp tổng dòng hàng ({expected}) — cần người xác minh")


def verify_round_trip(
    path: Path,
    mapping: dict[str, Any],
    values: dict[str, str | None],
    line_items: list[dict[str, str | None]] | dict[str, list[dict[str, str | None]]],
) -> list[str]:
    problems: list[str] = []
    wb = load_workbook(path, data_only=True)
    for sheet_name, sheet_map in (mapping.get("sheets") or {}).items():
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        for cell_ref, spec in (sheet_map.get("scalars") or {}).items():
            expected = values.get(spec["field"])
            if expected is None:
                continue
            if not _same_value(expected, ws[cell_ref].value):
                problems.append(f"Round-trip lệch tại {sheet_name}!{cell_ref}: {ws[cell_ref].value!r} != {expected!r}")
        line_map = sheet_map.get("line_items") or {}
        if not line_map:
            continue
        sheet_lines = _lines_for_sheet(line_items, sheet_name)
        start_row = int(line_map.get("start_row") or 16)
        columns = line_map.get("columns") or {}
        row_of = _allocate_rows(len(sheet_lines), line_map)
        for idx, item in enumerate(sheet_lines):
            for letter, col_spec in columns.items():
                source_key = col_spec.get("source_field") or col_spec.get("field")
                expected = item.get(source_key)
                if expected is None:
                    continue
                actual = ws[f"{letter}{row_of[idx]}"].value
                if not _same_value(expected, actual):
                    problems.append(
                        f"Round-trip lệch tại {sheet_name}!{letter}{row_of[idx]} ({source_key}): {actual!r} != {expected!r}"
                    )
    return problems


def _same_value(expected: Any, actual: Any) -> bool:
    if expected is None:
        return actual in (None, "")
    try:
        return Decimal(str(actual)) == Decimal(str(expected))
    except Exception:
        return str(expected).strip() == str(actual).strip()
