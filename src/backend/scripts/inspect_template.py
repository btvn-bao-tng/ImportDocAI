from __future__ import annotations

import json
import sys
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


def dump(path: Path) -> dict:
    wb = load_workbook(path, data_only=False)
    out: dict = {"file": str(path), "sheets": {}}
    for ws in wb.worksheets:
        cells = []
        merges = [str(r) for r in ws.merged_cells.ranges]
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                cells.append(
                    {
                        "ref": cell.coordinate,
                        "value": str(cell.value),
                        "is_formula": isinstance(cell.value, str) and cell.value.startswith("="),
                        "number_format": cell.number_format,
                    }
                )
        out["sheets"][ws.title] = {
            "max_row": ws.max_row,
            "max_col": ws.max_column,
            "merged": merges,
            "print_area": str(ws.print_area) if ws.print_area else None,
            "cells": cells,
        }
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1])
    Path(sys.argv[2]).write_text(json.dumps(dump(target), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {sys.argv[2]}")
