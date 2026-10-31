from __future__ import annotations

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell

from .export_excel import ExportError, load_mapping


def anchor_of(ws, cell_ref: str) -> str:
    """Merged cells can only be written through their top-left anchor."""
    cell = ws[cell_ref]
    if isinstance(cell, MergedCell):
        for merged in ws.merged_cells.ranges:
            if cell.coordinate in merged:
                return merged.start_cell.coordinate
    return cell_ref


def writable_cells(template_version: str) -> dict[str, list[tuple[str, str]]]:
    """Per sheet: (writing anchor, logical target) pairs the export will touch."""
    mapping, template_path = load_mapping(template_version)
    wb = load_workbook(template_path)
    plan: dict[str, list[tuple[str, str]]] = {}
    for sheet_name, sheet_map in (mapping.get("sheets") or {}).items():
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        entries: list[tuple[str, str]] = []
        for cell_ref in (sheet_map.get("scalars") or {}):
            entries.append((anchor_of(ws, cell_ref), cell_ref))
        line_map = sheet_map.get("line_items") or {}
        start_row = int(line_map.get("start_row") or 16)
        for letter in (line_map.get("columns") or {}):
            ref = f"{letter}{start_row}"
            entries.append((anchor_of(ws, ref), ref))
        plan[sheet_name] = entries
    return plan


def claimed_cells(template_version: str, sheet_name: str, count: int) -> set[str]:
    plan = writable_cells(template_version).get(sheet_name, [])
    taken: set[str] = set()
    for anchor, _target in plan:
        taken.add(anchor)
    return taken


def beyond_print_area(template_version: str, sheet_name: str, max_row: int) -> str | None:
    mapping, template_path = load_mapping(template_version)
    wb = load_workbook(template_path)
    if sheet_name not in wb.sheetnames:
        return f"Template thiếu sheet '{sheet_name}'"
    area = wb[sheet_name].print_area
    if not area:
        return None
    last_row = 0
    for part in str(area).split(","):
        tail = part.split("!")[-1]
        end = tail.split(":")[-1]
        digits = "".join(ch for ch in end if ch.isdigit())
        if digits:
            last_row = max(last_row, int(digits))
    if last_row and max_row > last_row:
        return (
            f"Sheet '{sheet_name}' có {max_row} dòng hàng nhưng vùng in chỉ tới dòng {last_row}; "
            "dữ liệu vượt vùng in — cần review hoặc mở rộng template"
        )
    return None


__all__ = ["anchor_of", "writable_cells", "claimed_cells", "beyond_print_area", "ExportError"]
