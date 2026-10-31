from __future__ import annotations

from openpyxl.utils import get_column_letter

MAX_GRID_CHARS = 60000


def _sparse_rows(rows: list[list], start_row: int = 1) -> str:
    """Render every non-empty cell as `Ref=value`, including rows past 60.

    The old reader kept only the first 60 rows per sheet; a TKN form puts its goods
    blocks at rows 148/201 and the exchange rate at row 70, so everything below row 60
    was silently dropped. There is deliberately no blank-row early exit: a form can
    have long empty stretches, and guessing where the data ends is how rows get lost.
    """
    lines: list[str] = []
    for offset, row in enumerate(rows):
        cells = [
            f"{get_column_letter(c_idx)}{start_row + offset}={value}"
            for c_idx, value in enumerate(row, start=1)
            if value is not None and str(value).strip() != ""
        ]
        if cells:
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def grid_summary(grid: dict) -> dict:
    """Shape of the workbook: sheet names and the last row/column with data."""
    summary = {"sheets": [], "truncated": False}
    for sheet in grid.get("sheets") or []:
        rows = sheet.get("rows") or []
        last_row = 0
        max_col = 0
        non_empty = 0
        for r_idx, row in enumerate(rows, start=1):
            for c_idx, value in enumerate(row, start=1):
                if value is not None and str(value).strip() != "":
                    last_row = max(last_row, r_idx)
                    max_col = max(max_col, c_idx)
                    non_empty += 1
        summary["sheets"].append(
            {
                "name": sheet.get("name"),
                "rows": len(rows),
                "last_data_row": last_row,
                "last_data_column": get_column_letter(max_col) if max_col else None,
                "non_empty_cells": non_empty,
            }
        )
    return summary


def render_grid(
    grid: dict,
    limit: int | None = None,
    ranges: dict[str, tuple[int, int]] | None = None,
) -> tuple[str, bool]:
    """Render the workbook for a prompt and report whether content was dropped.

    `limit` is only a tail cap for reporting; when the rendered text exceeds the
    character budget the *whole* sheet set is summarised and `truncated` is True so
    the caller can log it. `ranges` maps sheet name to (first_row, last_row) and is the
    intended way to ask for a specific block, e.g. the goods block at rows 148-200.
    """
    chunks: list[str] = []
    truncated = False
    for sheet in grid.get("sheets") or []:
        name = sheet.get("name") or "Sheet"
        rows = sheet.get("rows") or []
        first_row, last_row = 1, len(rows)
        if ranges and name in ranges:
            first_row, last_row = ranges[name]
            first_row = max(1, first_row)
            last_row = min(len(rows), last_row)
        window = rows[first_row - 1:last_row]
        body = _sparse_rows(window, start_row=first_row)
        if limit is not None:
            body_lines = body.splitlines()
            if len(body_lines) > limit:
                body = "\n".join(body_lines[:limit])
                truncated = True
        chunks.append(f"### Sheet: {name} (dòng {first_row}-{last_row})\n{body}")
    text = "\n".join(chunks)
    if len(text) > MAX_GRID_CHARS:
        text = text[:MAX_GRID_CHARS] + "\n...[dữ liệu bị cắt do vượt giới hạn ký tự]"
        truncated = True
    return text, truncated
