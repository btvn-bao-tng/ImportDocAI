from __future__ import annotations

"""Export writer for the Tờ khai hải quan nhập khẩu template (`tokhai_import_v1`).

Layout differs from the U&I template, so it gets its own writer:
- TKN page 1 holds the declaration header;
- pages 2..N repeat the header block and add one goods block per page;
- HANG (veryHidden) mirrors the goods rows;
- page counters ("1/4") and the declaration-number banner are computed, never guessed.

Same contract as the U&I writer: only mapped cells are written, unmapped template
cells are never touched, capacity overflow fails loudly, and FINAL requires every
critical mapped field to be present and reviewed.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell

from .export_excel import (
    ExportError,
    FieldSource,
    _demote_to_draft,
    _same_value,
    _write_cell,
    load_mapping,
)
from .fields import ALL_FIELD_SPECS, is_critical

log = logging.getLogger("importdoc.export.tokhai")

TOKHAI_TEMPLATE_VERSION = "tokhai_import_v1"
DATA_FONT = "Courier New"


@dataclass
class TokhaiOutcome:
    output_path: Path
    report_path: Path
    mode: str
    filled: int
    missing: list[str]
    warnings: list[str]


UNREVIEWED_STATUSES = {"UNREVIEWED", "NEEDS_REVIEW", "EDITED"}


@dataclass
class DeclarationPayload:
    """Everything the TKN template needs. Values stay strings exactly as printed."""

    header: dict[str, str | None]
    goods: list[dict[str, str | None]]
    taxes: list[dict[str, str | None]]
    sources: dict[str, FieldSource]
    review_status: str = "UNREVIEWED"
    goods_reviewed: bool = False
    licenses: list[dict[str, str | None]] = field(default_factory=list)
    instructions: list[dict[str, str | None]] = field(default_factory=list)
    adjustments: list[dict[str, str | None]] = field(default_factory=list)
    # For a NEW_DRAFT the required set comes from the target catalog: fields the
    # customs system generates (declaration number, exchange rate, channel) are
    # critical to the finished form but must not block a draft that cannot have them.
    required_keys: list[str] | None = None
    # Target keys a human has confirmed. When supplied this is authoritative per field,
    # so one unconfirmed non-required value cannot block an otherwise complete draft.
    confirmed_keys: set[str] | None = None

    def _required(self, key: str) -> bool:
        if self.required_keys is None:
            return is_critical(key)
        return key in self.required_keys

    def unreviewed_critical(self) -> list[str]:
        """Critical fields that exist but no human has confirmed.

        Presence alone is not enough for FINAL: the plan requires critical fields to be
        reviewed. Fields sourced from a scan must always be reviewed (page-level only).
        """
        unreviewed: list[str] = []
        for key, value in self.header.items():
            if value in (None, "") or not self._required(key):
                continue
            if self.confirmed_keys is not None:
                if key not in self.confirmed_keys:
                    unreviewed.append(key)
            elif self.review_status in UNREVIEWED_STATUSES:
                unreviewed.append(key)
        for idx, line in enumerate(self.goods, start=1):
            for key, value in line.items():
                if key == "taxes" or value in (None, "") or not self._required(key):
                    continue
                if self.confirmed_keys is not None:
                    attr = key.split(".", 1)[-1]
                    if f"goods.{idx}.{attr}" not in self.confirmed_keys:
                        unreviewed.append(f"goods[{idx}].{key}")
                elif not self.goods_reviewed:
                    unreviewed.append(f"goods[{idx}].{key}")
        return unreviewed

    def critical_missing(self) -> list[str]:
        missing = []
        for key in self.header:
            if self.header.get(key) in (None, "") and self._required(key):
                missing.append(key)
        for idx, line in enumerate(self.goods, start=1):
            for key, value in line.items():
                if key == "taxes":
                    continue
                if value in (None, "") and self._required(key):
                    missing.append(f"goods[{idx}].{key}")
        return missing


def _as_display(value: str | None, field_key: str) -> Any:
    """Tờ khai values are printed verbatim, exactly as the source system shows them.

    '23.230' means twenty-three thousand two hundred thirty (Vietnamese grouping) and
    '46,46' means 46.46. Converting them to floats would print '23230'/'46.46' and break
    the customs layout, so the string is preserved. Only the *checks* parse numbers.
    """
    return value


_VN_GROUPED = re.compile(r"^-?\d{1,3}(\.\d{3})+$")
_VN_GROUPED_DEC = re.compile(r"^-?\d{1,3}(\.\d{3})+,\d+$")
_PLAIN_DEC = re.compile(r"^-?\d+([.,]\d+)?$")


def parse_vn_number(value: str | None) -> Decimal | None:
    """Parse a printed Vietnamese number. Returns None when the intent is ambiguous.

    '607.301.890' -> 607301890 ; '46,46' -> 46.46 ; '23.230' -> 23230 ;
    A single dot with three trailing digits ('250.500') is read as thousands grouping
    (250500), matching how the customs printout groups values; mixed separators that
    cannot be resolved ('1,234.56' vs '1.234,56') return None so callers can warn.
    """
    if value in (None, ""):
        return None
    text = value.strip().replace(" ", "").replace("\u00a0", "")
    if not text:
        return None
    negative = text.startswith("-")
    body = text[1:] if negative else text
    try:
        if _VN_GROUPED.search(body):
            digits = body.replace(".", "")
            dec = Decimal(digits)
        elif _VN_GROUPED_DEC.search(body):
            whole, frac = body.split(",", 1)
            dec = Decimal(whole.replace(".", "") + "." + frac)
        elif "," in body and "." in body:
            return None
        elif body.count(",") == 1:
            dec = Decimal(body.replace(",", "."))
        else:
            dec = Decimal(body)
    except InvalidOperation:
        return None
    return -dec if negative else dec


def _looks_grouped(value: str) -> bool:
    """Kept for display decisions: any dot/comma pattern that is not a plain integer."""
    return not re.fullmatch(r"-?\d+", value.strip())


def _digits_only(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits or None


_REF_RE = re.compile(r"^([A-Z]+)(\d+)$")


def split_ref(ref: str) -> tuple[str, int]:
    match = _REF_RE.match(ref.strip())
    if match is None:
        raise ExportError(f"Ô không hợp lệ trong mapping: {ref!r}")
    return match.group(1), int(match.group(2))


def _block_refs(columns: dict[str, Any], block_start: int, first_start: int) -> dict[str, str]:
    """Translate block-relative refs (authored at the first block) to this block's rows."""
    offset = block_start - first_start
    out: dict[str, str] = {}
    for ref, spec in columns.items():
        letter, row = split_ref(ref)
        out[f"{letter}{row + offset}"] = spec
    return out


def _pages_used(goods_count: int, blocks: list[int], per_block: int) -> int:
    """Page 1 is the header, page 2 the fixed continuation sheet (ghi chú / chỉ thị /
    trưởng đơn vị); each used goods block adds one more page.

    The reference declarations print "1/4..4/4" for two goods lines, i.e. pages =
    2 fixed pages + one page per used block, capped by the number of blocks in the
    template.
    """
    if goods_count <= 0:
        return 2
    needed = -(-goods_count // max(per_block, 1))
    return 2 + min(needed, len(blocks))


def _set_page_counter(ws, cell_ref: str, page: int, total: int) -> None:
    """Counters sit in merged runs; write through the anchor so nothing is dropped."""
    cell = ws[cell_ref]
    if isinstance(cell, MergedCell):
        for merged in ws.merged_cells.ranges:
            if cell.coordinate in merged:
                ws[merged.start_cell.coordinate] = f"{page}/{total}"
                return
        raise ExportError(f"TKN!{cell_ref} là merged cell không xác định — kiểm tra mapping")
    cell.value = f"{page}/{total}"


def _write_banner(ws, declaration_no: str | None) -> None:
    digits = _digits_only(declaration_no)
    ws["AA3"] = f"*{digits}*" if digits else None


def write_tokhai_export(
    shipment_code: str,
    payload: DeclarationPayload,
    template_version: str = TOKHAI_TEMPLATE_VERSION,
    export_dir: Path | None = None,
    today: date | None = None,
    requested_mode: str | None = None,
    mode_label: str | None = None,
    required_keys: list[str] | None = None,
) -> TokhaiOutcome:
    if required_keys is not None:
        payload.required_keys = list(required_keys)
    mapping, template_path = load_mapping(template_version)
    sheets = mapping.get("sheets") or {}
    tkn_map = sheets.get("TKN") or {}
    hang_map = sheets.get("HANG") or {}
    line_blocks = tkn_map.get("line_blocks") or {}
    tax_rows = tkn_map.get("tax_rows") or {}

    max_lines = len(line_blocks.get("block_starts") or []) * int(line_blocks.get("capacity_per_block") or 1)
    # A blanked numbered row (its source disappeared on re-extraction) stays in the
    # payload so `goods.<seq>.*` addressing keeps working, but it must not consume a
    # template line block, inflate the printed goods count, or add a page.
    live_goods = [
        g for g in payload.goods if any(k != "taxes" and v not in (None, "") for k, v in g.items())
    ]
    if len(live_goods) > max_lines:
        raise ExportError(
            f"Template Tờ khai chứa tối đa {max_lines} dòng hàng nhưng hồ sơ có {len(live_goods)} dòng. "
            "Cần tách tờ khai hoặc mở rộng template — không tự chèn trang."
        )

    wb = load_workbook(template_path)
    missing: list[str] = []
    warnings: list[str] = []
    cells_report: list[dict[str, Any]] = []
    filled = 0

    frames = tkn_map.get("page_frames") or []
    block_starts = [int(b) for b in (line_blocks.get("block_starts") or [])]
    per_block = int(line_blocks.get("capacity_per_block") or 1)
    if not block_starts:
        raise ExportError("Mapping Tờ khai thiếu line_blocks.block_starts")
    page_total = _pages_used(len(live_goods), block_starts, per_block)

    def write(ws, sheet: str, ref: str, field_key: str, value: str | None, *, required: bool) -> None:
        nonlocal filled
        if value in (None, ""):
            if required:
                missing.append(f"{sheet}!{ref} ← {field_key}")
                cells_report.append(
                    {"sheet": sheet, "cell": ref, "field": field_key, "value": None, "status": "MISSING_FIELD"}
                )
            return
        _write_cell(ws, ref, _as_display(value, field_key))
        filled += 1
        cells_report.append(
            {"sheet": sheet, "cell": ref, "field": field_key, "value": value, "status": "FILLED"}
        )

    # --- TKN header (page 1) and repeated header blocks on continuation pages -------
    tkn = wb["TKN"]
    # The per-cell required decisions must agree with `payload.critical_missing()` /
    # `unreviewed_critical()`, which already use `payload.required_keys`: when the
    # caller did not pass an explicit set, a payload built by `build_new_draft_payload`
    # still carries the catalog's required keys and must not fall back to the raw
    # mapping-critical flags (those include customs-generated cells a new draft can
    # never fill, which would make every such export report phantom missing fields).
    required_set = (
        set(required_keys) if required_keys is not None
        else (set(payload.required_keys) if payload.required_keys is not None else None)
    )
    for ref, spec in (tkn_map.get("scalars") or {}).items():
        key = spec["field"]
        for target in spec.get("page_refs") or [ref]:
            required = (
                bool(spec.get("critical"))
                if required_set is None
                else bool(spec.get("critical")) and key in required_set
            )
            required = required and target == ref
            write(tkn, "TKN", target, key, payload.header.get(key), required=required)

    _write_banner(tkn, payload.header.get("declaration.declaration_no"))
    write(tkn, "TKN", "U75", "declaration.page_count", str(page_total), required=False)
    write(tkn, "TKN", "AF75", "declaration.goods_line_count", str(len(live_goods)), required=False)
    for frame in frames:
        if int(frame.get("page") or 0) <= page_total:
            _set_page_counter(tkn, frame["counter_cell"], int(frame["page"]), page_total)

    start_row = int(tax_rows.get("start_row") or 68)
    tax_capacity = int(tax_rows.get("capacity") or 0)
    if len(payload.taxes) > tax_capacity:
        warnings.append(
            f"Tờ khai có {len(payload.taxes)} dòng thuế khai tổng nhưng template chỉ chứa "
            f"{tax_capacity} dòng — phần vượt bị bỏ qua"
        )
    for i in range(tax_capacity):
        row = start_row + i
        tax = payload.taxes[i] if i < len(payload.taxes) else {}
        for letter, col in (tax_rows.get("columns") or {}).items():
            key = col["field"]
            write(tkn, "TKN", f"{letter}{row}", key, tax.get(key), required=False)

    # --- goods blocks --------------------------------------------------------------
    first_start = block_starts[0]
    seq_letter = line_blocks.get("block_seq_letter") or "C"
    for block_idx, block_start in enumerate(block_starts):
        refs = _block_refs(line_blocks.get("columns") or {}, block_start, first_start)
        if block_idx * per_block < len(live_goods):
            tkn[f"{seq_letter}{block_start}"] = f"<{block_idx + 1:02d}>"
        for slot in range(per_block):
            idx = block_idx * per_block + slot
            if idx >= len(live_goods):
                break
            goods = live_goods[idx]
            for ref, col in refs.items():
                key = col["field"]
                required = (
                    bool(col.get("critical"))
                    if required_set is None
                    else key in required_set
                )
                write(tkn, "TKN", ref, key, goods.get(key), required=required)

    # --- goods tax slots ("Thuế và thu khác" inside each goods block) ---------------
    tax_slot_map = tkn_map.get("goods_tax_slots") or {}
    tax_slot_starts = [int(s) for s in (tax_slot_map.get("slot_starts") or [])]
    if tax_slot_starts:
        first_tax_start = tax_slot_starts[0]
        for block_idx, block_start in enumerate(block_starts):
            line_idx = block_idx * per_block
            goods = live_goods[line_idx] if line_idx < len(live_goods) else None
            goods_taxes = (goods or {}).get("taxes") or []
            if len(goods_taxes) > len(tax_slot_starts):
                warnings.append(
                    f"Dòng hàng {line_idx + 1} có {len(goods_taxes)} khoản thuế nhưng template chỉ chứa "
                    f"{len(tax_slot_starts)} slot — phần vượt bị bỏ qua"
                )
            block_offset = block_start - first_start
            for slot_idx, slot_start in enumerate(tax_slot_starts):
                refs = _block_refs(tax_slot_map.get("columns") or {}, slot_start + block_offset, first_tax_start)
                tax = goods_taxes[slot_idx] if slot_idx < len(goods_taxes) else {}
                for ref, col in refs.items():
                    key = col["field"]
                    write(tkn, "TKN", ref, key, tax.get(key), required=False)

    # --- import licenses ("Giấy phép nhập khẩu", 5 slots) ----------------------------
    license_slots = tkn_map.get("license_slots") or {}
    license_slot_rows = license_slots.get("slots") or []
    if len(payload.licenses) > len(license_slot_rows):
        warnings.append(
            f"Hồ sơ có {len(payload.licenses)} giấy phép nhập khẩu nhưng template chỉ chứa "
            f"{len(license_slot_rows)} slot — phần vượt bị bỏ qua"
        )
    for idx, lic in enumerate(payload.licenses[: len(license_slot_rows)]):
        slot = license_slot_rows[idx]
        write(tkn, "TKN", slot["code"], "license.code", lic.get("license.code"), required=False)
        write(tkn, "TKN", slot["number"], "license.number", lic.get("license.number"), required=False)

    # --- valuation adjustments ("Các khoản điều chỉnh", 5 rows) ----------------------
    adj_rows = tkn_map.get("adjustment_rows") or {}
    adj_start = int(adj_rows.get("start_row") or 58)
    adj_capacity = int(adj_rows.get("capacity") or 0)
    if len(payload.adjustments) > adj_capacity:
        warnings.append(
            f"Hồ sơ có {len(payload.adjustments)} khoản điều chỉnh nhưng template chỉ chứa "
            f"{adj_capacity} dòng — phần vượt bị bỏ qua"
        )
    for i in range(adj_capacity):
        adj = payload.adjustments[i] if i < len(payload.adjustments) else {}
        for letter, col in (adj_rows.get("columns") or {}).items():
            write(tkn, "TKN", f"{letter}{adj_start + i}", col["field"], adj.get(col["field"]), required=False)

    # --- customs instructions ("Phân loại chỉ thị của Hải quan", 10 rows, stride 3) -
    ins_rows = tkn_map.get("instruction_rows") or {}
    ins_start = int(ins_rows.get("start_row") or 90)
    ins_stride = int(ins_rows.get("row_stride") or 1)
    ins_capacity = int(ins_rows.get("capacity") or 0)
    if len(payload.instructions) > ins_capacity:
        warnings.append(
            f"Hồ sơ có {len(payload.instructions)} chỉ thị hải quan nhưng template chỉ chứa "
            f"{ins_capacity} dòng — phần vượt bị bỏ qua"
        )
    for i in range(ins_capacity):
        ins = payload.instructions[i] if i < len(payload.instructions) else {}
        row = ins_start + i * ins_stride
        for letter, col in (ins_rows.get("columns") or {}).items():
            write(tkn, "TKN", f"{letter}{row}", col["field"], ins.get(col["field"]), required=False)

    # --- HANG mirror ---------------------------------------------------------------
    if "HANG" in wb.sheetnames:
        hang = wb["HANG"]
        for ref, spec in (hang_map.get("scalars") or {}).items():
            write(hang, "HANG", ref, spec["field"], payload.header.get(spec["field"]), required=False)
        hang_blocks = hang_map.get("line_blocks") or {}
        if not hang_blocks.get("verified_data_cells", False):
            warnings.append(
                "HANG: chưa xác minh được dòng dữ liệu trong bản mẫu — bỏ qua (chỉ TKN được ghi)"
            )
        _, anchor_row = split_ref(hang_blocks.get("anchor_ref") or "G17")
        stride = int(hang_blocks.get("row_stride") or 1)
        for idx, goods in enumerate(live_goods[: len(block_starts) * per_block]):
            for ref, col in (hang_blocks.get("columns") or {}).items():
                letter, row = split_ref(ref)
                write(hang, "HANG", f"{letter}{row + idx * stride}",
                    col["field"],
                    goods.get(col["field"]),
                    required=False,
                )

    no_goods = not live_goods
    if no_goods:
        warnings.append("Tờ khai không có dòng hàng — không thể xuất FINAL")
    unresolved = payload.critical_missing()
    if unresolved:
        warnings.append("Thiếu trường bắt buộc: " + ", ".join(unresolved))
    unreviewed = payload.unreviewed_critical()
    if unreviewed:
        warnings.append(f"Chưa xác nhận {len(unreviewed)} trường bắt buộc — file ở chế độ DRAFT")
    mode = "DRAFT" if missing or unresolved or no_goods or unreviewed else "FINAL"
    if requested_mode == "FINAL" and mode != "FINAL":
        raise ExportError(
            "Không thể xuất FINAL: còn trường bắt buộc chưa có hoặc chưa được xác nhận. "
            + "; ".join(warnings[-3:])
        )
    if mode_label == "REFERENCE_MIRROR":
        # Audit-only mirror of one named reference: it never claims to be a new draft.
        mode = "REFERENCE_MIRROR"
    elif mode_label and mode == "FINAL":
        mode = mode_label

    total_invoice = _declared_sum(payload.goods, "goods.invoice_amount")
    if total_invoice is not None:
        declared = _decimal(payload.header.get("declaration.invoice_total"))
        if declared is not None and abs(declared - total_invoice) > Decimal("0.01"):
            warnings.append(
                f"Tổng trị giá hóa đơn khai báo ({declared}) khác tổng dòng hàng ({total_invoice}) — cần kiểm tra"
            )

    out_dir = export_dir or (Path("data/exports") / shipment_code)
    out_dir.mkdir(parents=True, exist_ok=True)
    date_tag = (today or date.today()).strftime("%Y%m%d")
    stamp = datetime.now().strftime("%H%M%S")
    file_code = shipment_code or mapping.get("file_code_prefix") or "TOKHAI"
    base = f"{file_code}_{shipment_code}_{template_version}_{mode}_{date_tag}_{stamp}"
    out_path = out_dir / f"{base}.xlsx"
    seq = 2
    while out_path.exists():
        out_path = out_dir / f"{base}_{seq}.xlsx"
        seq += 1
    wb.save(out_path)

    problems = verify_tokhai_round_trip(out_path, mapping, payload)
    warnings.extend(problems)
    if problems and mode == "FINAL":
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
                "sources": {k: v.__dict__ for k, v in payload.sources.items()},
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return TokhaiOutcome(out_path, report_path, mode, filled, missing, warnings)


def _decimal(value: str | None) -> Decimal | None:
    return parse_vn_number(value)


def _declared_sum(goods: list[dict[str, str | None]], key: str) -> Decimal | None:
    values = [_decimal(item.get(key)) for item in goods]
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def verify_tokhai_round_trip(path: Path, mapping: dict[str, Any], payload: DeclarationPayload) -> list[str]:
    """Re-open the written workbook and confirm each mapped cell kept its value."""
    problems: list[str] = []
    wb = load_workbook(path, data_only=True)
    tkn_map = mapping["sheets"]["TKN"]
    line_blocks = tkn_map["line_blocks"]

    for ref, spec in (tkn_map.get("scalars") or {}).items():
        expected = payload.header.get(spec["field"])
        if expected in (None, ""):
            continue
        if _norm_empty(wb["TKN"][ref].value) != _norm_empty(expected):
            problems.append(f"Round-trip lệch TKN!{ref}: {wb['TKN'][ref].value!r} != {expected!r}")

    # page counters, banner and derived counts
    blocks = [int(b) for b in (line_blocks.get("block_starts") or [])]
    per_block = int(line_blocks.get("capacity_per_block") or 1)
    # Mirror the writer's live-goods filter: a blanked numbered bucket stays in the
    # payload for addressing but must not drive page counts or cell expectations.
    live_goods = [
        g for g in payload.goods if any(k != "taxes" and v not in (None, "") for k, v in g.items())
    ]
    total_pages = _pages_used(len(live_goods), blocks, per_block) if blocks else 1
    for frame in tkn_map.get("page_frames") or []:
        if int(frame.get("page") or 0) <= total_pages:
            expected_counter = f"{frame['page']}/{total_pages}"
            actual = wb["TKN"][frame["counter_cell"]].value
            if _norm_empty(actual) != expected_counter:
                problems.append(f"Round-trip lệch TKN!{frame['counter_cell']} ({actual!r} != {expected_counter!r})")
        if frame.get("banner_cell"):
            expected_banner = (
                f"*{_digits_only(payload.header.get('declaration.declaration_no'))}*"
                if _digits_only(payload.header.get("declaration.declaration_no"))
                else None
            )
            if _norm_empty(wb["TKN"][frame["banner_cell"]].value) != _norm_empty(expected_banner):
                problems.append(f"Round-trip lệch TKN!{frame['banner_cell']} (banner)")

    # tax rows
    tax_rows = tkn_map.get("tax_rows") or {}
    for i in range(int(tax_rows.get("capacity") or 0)):
        row = int(tax_rows["start_row"]) + i
        tax = payload.taxes[i] if i < len(payload.taxes) else {}
        for letter, col in (tax_rows.get("columns") or {}).items():
            expected = tax.get(col["field"])
            if expected in (None, ""):
                continue
            if _norm_empty(wb["TKN"][f"{letter}{row}"].value) != _norm_empty(expected):
                problems.append(f"Round-trip lệch TKN!{letter}{row} ({col['field']})")

    # license slots
    license_slots = tkn_map.get("license_slots") or {}
    license_slot_rows = license_slots.get("slots") or []
    for idx, lic in enumerate(payload.licenses[: len(license_slot_rows)]):
        for slot_key, field_key in (("code", "license.code"), ("number", "license.number")):
            expected = lic.get(field_key)
            if expected in (None, ""):
                continue
            ref = license_slot_rows[idx][slot_key]
            if _norm_empty(wb["TKN"][ref].value) != _norm_empty(expected):
                problems.append(f"Round-trip lệch TKN!{ref} ({field_key})")

    # adjustment rows
    adj_rows = tkn_map.get("adjustment_rows") or {}
    adj_start = int(adj_rows.get("start_row") or 58)
    for i, adj in enumerate(payload.adjustments[: int(adj_rows.get("capacity") or 0)]):
        for letter, col in (adj_rows.get("columns") or {}).items():
            expected = adj.get(col["field"])
            if expected in (None, ""):
                continue
            if _norm_empty(wb["TKN"][f"{letter}{adj_start + i}"].value) != _norm_empty(expected):
                problems.append(f"Round-trip lệch TKN!{letter}{adj_start + i} ({col['field']})")

    # instruction rows
    ins_rows = tkn_map.get("instruction_rows") or {}
    ins_start = int(ins_rows.get("start_row") or 90)
    ins_stride = int(ins_rows.get("row_stride") or 1)
    for i, ins in enumerate(payload.instructions[: int(ins_rows.get("capacity") or 0)]):
        for letter, col in (ins_rows.get("columns") or {}).items():
            expected = ins.get(col["field"])
            if expected in (None, ""):
                continue
            if _norm_empty(wb["TKN"][f"{letter}{ins_start + i * ins_stride}"].value) != _norm_empty(expected):
                problems.append(f"Round-trip lệch TKN!{letter}{ins_start + i * ins_stride} ({col['field']})")

    # goods tax slots
    tax_slot_map = tkn_map.get("goods_tax_slots") or {}
    tax_slot_starts = [int(s) for s in (tax_slot_map.get("slot_starts") or [])]
    if tax_slot_starts:
        first_tax_start = tax_slot_starts[0]
        for block_idx, block_start in enumerate(blocks):
            line_idx = block_idx * per_block
            goods = live_goods[line_idx] if line_idx < len(live_goods) else None
            goods_taxes = (goods or {}).get("taxes") or []
            block_offset = block_start - blocks[0]
            for slot_idx, slot_start in enumerate(tax_slot_starts):
                refs = _block_refs(tax_slot_map.get("columns") or {}, slot_start + block_offset, first_tax_start)
                tax = goods_taxes[slot_idx] if slot_idx < len(goods_taxes) else {}
                for ref, col in refs.items():
                    expected = tax.get(col["field"])
                    if expected in (None, ""):
                        continue
                    if _norm_empty(wb["TKN"][ref].value) != _norm_empty(expected):
                        problems.append(f"Round-trip lệch TKN!{ref} ({col['field']})")

    # HANG mirror cells that the exporter owns
    hang_blocks = (mapping["sheets"].get("HANG") or {}).get("line_blocks") or {}
    if hang_blocks.get("verified_data_cells") and "HANG" in wb.sheetnames:
        _, anchor_row = split_ref(hang_blocks.get("anchor_ref") or "G17")
        stride = int(hang_blocks.get("row_stride") or 1)
        for idx, goods in enumerate(live_goods):
            for ref, col in (hang_blocks.get("columns") or {}).items():
                letter, row = split_ref(ref)
                expected = goods.get(col["field"])
                if expected in (None, ""):
                    continue
                target = f"{letter}{row + idx * stride}"
                if _norm_empty(wb["HANG"][target].value) != _norm_empty(expected):
                    problems.append(f"Round-trip lệch HANG!{target} ({col['field']})")

    block_starts = [int(b) for b in (line_blocks.get("block_starts") or [])]
    if block_starts:
        first_start = block_starts[0]
        for block_idx, block_start in enumerate(block_starts):
            refs = _block_refs(line_blocks.get("columns") or {}, block_start, first_start)
            goods = live_goods[block_idx] if block_idx < len(live_goods) else None
            if goods is None:
                break
            for ref, col in refs.items():
                expected = goods.get(col["field"])
                if expected in (None, ""):
                    continue
                if _norm_empty(wb["TKN"][ref].value) != _norm_empty(expected):
                    problems.append(
                        f"Round-trip lệch TKN!{ref} ({col['field']}): {wb['TKN'][ref].value!r} != {expected!r}"
                    )
    return problems


def owned_cells(mapping: dict[str, Any], sheet: str) -> set[str]:
    """Every coordinate the Tờ khai exporter may write on `sheet`."""
    sm = (mapping.get("sheets") or {}).get(sheet) or {}
    owned: set[str] = set()
    for ref, spec in (sm.get("scalars") or {}).items():
        owned.add(ref)
        owned.update(spec.get("page_refs") or [])
    tb = sm.get("tax_rows")
    if tb:
        for i in range(int(tb.get("capacity") or 0)):
            for letter in (tb.get("columns") or {}):
                owned.add(f"{letter}{int(tb['start_row']) + i}")
    lb = sm.get("line_blocks")
    if lb:
        starts = [int(b) for b in (lb.get("block_starts") or [])]
        for start in starts:
            offset = start - starts[0]
            for ref in (lb.get("columns") or {}):
                letter, row = split_ref(ref)
                owned.add(f"{letter}{row + offset}")
    gts = sm.get("goods_tax_slots")
    if gts:
        starts = [int(b) for b in (lb.get("block_starts") or [])] if lb else []
        slot_starts = [int(s) for s in (gts.get("slot_starts") or [])]
        for start in starts:
            block_offset = start - starts[0]
            for slot_start in slot_starts:
                offset = slot_start + block_offset - slot_starts[0]
                for ref in (gts.get("columns") or {}):
                    letter, row = split_ref(ref)
                    owned.add(f"{letter}{row + offset}")
    ls = sm.get("license_slots")
    if ls:
        for slot in (ls.get("slots") or []):
            owned.add(slot["code"])
            owned.add(slot["number"])
        owned.update(ls.get("extra_owned_cells") or [])
    ar = sm.get("adjustment_rows")
    if ar:
        for i in range(int(ar.get("capacity") or 0)):
            for letter in (ar.get("columns") or {}):
                owned.add(f"{letter}{int(ar['start_row']) + i}")
    ir = sm.get("instruction_rows")
    if ir:
        stride = int(ir.get("row_stride") or 1)
        for i in range(int(ir.get("capacity") or 0)):
            for letter in (ir.get("columns") or {}):
                owned.add(f"{letter}{int(ir['start_row']) + i * stride}")
    if sheet == "TKN":
        for frame in sm.get("page_frames") or []:
            owned.add(frame["counter_cell"])
            if frame.get("banner_cell"):
                owned.add(frame["banner_cell"])
    return owned


def _norm_empty(value: Any) -> Any:
    return None if value in (None, "") else value


def template_diff_problems(mapping: dict[str, Any], source: Path, template: Path) -> list[str]:
    """Template must differ from the reference only in cells the exporter owns.

    Any other difference means either stale customer data leaked into the template or a
    label/layout cell was destroyed. Both are release blockers.
    """
    src = load_workbook(source)
    tpl = load_workbook(template)
    problems: list[str] = []
    for sheet in (mapping.get("sheets") or {}):
        if sheet not in src.sheetnames or sheet not in tpl.sheetnames:
            problems.append(f"thiếu sheet {sheet}")
            continue
        owned = owned_cells(mapping, sheet)
        s_ws, t_ws = src[sheet], tpl[sheet]
        for row in s_ws.iter_rows():
            for cell in row:
                if isinstance(cell, MergedCell):
                    continue
                if cell.coordinate in owned:
                    if t_ws[cell.coordinate].value not in (None, ""):
                        problems.append(f"{sheet}!{cell.coordinate} còn dữ liệu: {t_ws[cell.coordinate].value!r}")
                    continue
                if _norm_empty(t_ws[cell.coordinate].value) != _norm_empty(cell.value):
                    problems.append(
                        f"{sheet}!{cell.coordinate} lệch nhãn: {t_ws[cell.coordinate].value!r} != {cell.value!r}"
                    )
    return problems
