from __future__ import annotations

"""Sanitation contract for the shipped Excel templates.

A template is a blank form plus static labels/formulas. Any other prefilled value is
either a leaked shipment (TAEDA/LS2628) or an unowned cell the exporter never clears,
so it would appear in every export. `TEMPLATE_DATA_CELLS` enumerates the writable data
cells that must start empty; tests and the maintenance script both read this list.
"""

from pathlib import Path

from openpyxl import load_workbook

# Cells that carry transaction data, not labels. They are the ones the audit found
# leaking (TKN U25/H26/U26 = the previous exporter's address) plus the remaining
# unowned data cells in the U&I workbook.
TEMPLATE_DATA_CELLS: dict[str, dict[str, tuple[str, ...]]] = {
    "tokhai_import_v1": {
        "TKN": ("U25", "H26", "U26"),
    },
    "uandi_import_v1": {
        "PACKING LIST": ("F5", "F6", "B10", "B11", "A12"),
        "INVOICE": ("J8", "D19"),
        "HOP DONG": ("H3", "B4", "B7", "B9", "B10", "D26", "E29"),
    },
}

# Identifiers that must never appear in a blank template.
FORBIDDEN_IDENTIFIERS = (
    "TAEDA",
    "KINGSMAN",
    "LS2628",
    "LAS MARIAS",
    "BERTEC",
    "NANJING JOYCE",
    "DALIAN",
    "YM CONTINENT",
    "YMMU6509610",
    "KHANH BINH",
    "TAICHUNG",
    "WURI DISTRICT",
)


def sanitize_workbook(template_path: Path, version: str) -> list[str]:
    """Clear known data cells, preserving merged cells, styles and formulas.

    Returns the coordinates that were actually cleared. Only cell values are touched;
    `openpyxl` keeps the surrounding formatting, so labels and print areas survive.
    """
    cells = TEMPLATE_DATA_CELLS.get(version)
    if not cells:
        return []
    wb = load_workbook(template_path)
    cleared: list[str] = []
    for sheet, coordinates in cells.items():
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        for coord in coordinates:
            cell = ws[coord]
            if cell.value not in (None, ""):
                cell.value = None
                cleared.append(f"{sheet}!{coord}")
    if cleared:
        wb.save(template_path)
    return cleared


def data_cells_with_values(template_path: Path, version: str) -> list[str]:
    """Data cells that still hold a value: a build must fail when this is non-empty."""
    cells = TEMPLATE_DATA_CELLS.get(version) or {}
    wb = load_workbook(template_path)
    offenders: list[str] = []
    for sheet, coordinates in cells.items():
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        for coord in coordinates:
            value = ws[coord].value
            if value not in (None, ""):
                offenders.append(f"{sheet}!{coord}={value!r}")
    return offenders


def identifier_leaks(template_path: Path, version: str) -> list[str]:
    """Any known shipment identifier anywhere in the sheets the exporter writes."""
    cells = TEMPLATE_DATA_CELLS.get(version) or {}
    wb = load_workbook(template_path)
    leaks: list[str] = []
    for sheet in cells:
        if sheet not in wb.sheetnames:
            continue
        for row in wb[sheet].iter_rows():
            for cell in row:
                if not isinstance(cell.value, str):
                    continue
                upper = cell.value.upper()
                for needle in FORBIDDEN_IDENTIFIERS:
                    if needle in upper:
                        leaks.append(f"{sheet}!{cell.coordinate}={cell.value!r}")
                        break
    return leaks
