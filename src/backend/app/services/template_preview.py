from __future__ import annotations

"""Read-only preview of a fully mapped template.

`build_template_preview` produces the same cell map `write_tokhai_export` writes,
but returns data instead of editing a workbook: for every mapped cell it gives the
value and the stored evidence (document, page, bbox, cell ref) so the UI can show
the filled form and jump back to the source when a value is clicked.

Values come from the stored rows (Declaration / goods / tax families) exactly as
the exporter would read them. Evidence comes from ExtractedField, which is the only
table that keeps page/bbox/cell_ref provenance.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Document, ExtractedField, LineItem
from .declaration import build_declaration_payload
from .export_excel import ExportError, load_mapping
from .export_tokhai import (
    TOKHAI_TEMPLATE_VERSION,
    DeclarationPayload,
    _block_refs,
    split_ref,
)
from .fields import ALL_FIELD_SPECS
from .ingest import parse_document

EVIDENCE_FIELDS = (
    "document_id",
    "filename",
    "page",
    "bbox",
    "cell_ref",
    "provenance_type",
    "review_status",
    "critical",
    "label_vi",
)


def _empty_payload() -> DeclarationPayload:
    return DeclarationPayload(header={}, goods=[], taxes=[], sources={})


def _mapped_payload(mapping: dict, payload: DeclarationPayload) -> dict[str, dict[str, tuple[str, str | None]]]:
    """cell -> (field_key, value) for every cell the TKN exporter owns.

    Mirrors write_tokhai_export block by block; keep the two in sync.
    """
    sheets = mapping.get("sheets") or {}
    out: dict[str, dict[str, tuple[str, str | None]]] = {"TKN": {}, "HANG": {}}

    tkn = sheets.get("TKN") or {}
    for ref, spec in (tkn.get("scalars") or {}).items():
        key = spec["field"]
        for target in spec.get("page_refs") or [ref]:
            out["TKN"][target] = (key, payload.header.get(key))

    line_blocks = tkn.get("line_blocks") or {}
    block_starts = [int(b) for b in (line_blocks.get("block_starts") or [])]
    per_block = int(line_blocks.get("capacity_per_block") or 1)
    first_start = block_starts[0] if block_starts else 1
    for block_idx, block_start in enumerate(block_starts):
        refs = _block_refs(line_blocks.get("columns") or {}, block_start, first_start)
        for slot in range(per_block):
            idx = block_idx * per_block + slot
            if idx >= len(payload.goods):
                break
            goods = payload.goods[idx]
            for ref, col in refs.items():
                out["TKN"][ref] = (col["field"], goods.get(col["field"]))

    tax_rows = tkn.get("tax_rows") or {}
    tax_start = int(tax_rows.get("start_row") or 68)
    for i in range(int(tax_rows.get("capacity") or 0)):
        tax = payload.taxes[i] if i < len(payload.taxes) else {}
        for letter, col in (tax_rows.get("columns") or {}).items():
            out["TKN"][f"{letter}{tax_start + i}"] = (col["field"], tax.get(col["field"]))

    slot_map = tkn.get("goods_tax_slots") or {}
    slot_starts = [int(s) for s in (slot_map.get("slot_starts") or [])]
    if slot_starts:
        first_tax_start = slot_starts[0]
        for block_idx, block_start in enumerate(block_starts):
            line_idx = block_idx * per_block
            goods = payload.goods[line_idx] if line_idx < len(payload.goods) else None
            goods_taxes = (goods or {}).get("taxes") or []
            block_offset = block_start - first_start
            for slot_idx, slot_start in enumerate(slot_starts):
                refs = _block_refs(slot_map.get("columns") or {}, slot_start + block_offset, first_tax_start)
                tax = goods_taxes[slot_idx] if slot_idx < len(goods_taxes) else {}
                for ref, col in refs.items():
                    out["TKN"][ref] = (col["field"], tax.get(col["field"]))

    for idx, lic in enumerate((tkn.get("license_slots") or {}).get("slots") or []):
        if idx >= len(payload.licenses):
            break
        row = payload.licenses[idx]
        out["TKN"][lic["code"]] = ("license.code", row.get("license.code"))
        out["TKN"][lic["number"]] = ("license.number", row.get("license.number"))

    adj_rows = tkn.get("adjustment_rows") or {}
    adj_start = int(adj_rows.get("start_row") or 58)
    for i in range(int(adj_rows.get("capacity") or 0)):
        adj = payload.adjustments[i] if i < len(payload.adjustments) else {}
        for letter, col in (adj_rows.get("columns") or {}).items():
            out["TKN"][f"{letter}{adj_start + i}"] = (col["field"], adj.get(col["field"]))

    ins_rows = tkn.get("instruction_rows") or {}
    ins_start = int(ins_rows.get("start_row") or 90)
    ins_stride = int(ins_rows.get("row_stride") or 1)
    for i in range(int(ins_rows.get("capacity") or 0)):
        ins = payload.instructions[i] if i < len(payload.instructions) else {}
        for letter, col in (ins_rows.get("columns") or {}).items():
            out["TKN"][f"{letter}{ins_start + i * ins_stride}"] = (col["field"], ins.get(col["field"]))

    hang = sheets.get("HANG") or {}
    for ref, spec in (hang.get("scalars") or {}).items():
        out["HANG"][ref] = (spec["field"], payload.header.get(spec["field"]))
    hang_blocks = hang.get("line_blocks") or {}
    if hang_blocks.get("verified_data_cells"):
        stride = int(hang_blocks.get("row_stride") or 1)
        for idx, goods in enumerate(payload.goods):
            for ref, col in (hang_blocks.get("columns") or {}).items():
                letter, row = split_ref(ref)
                out["HANG"][f"{letter}{row + idx * stride}"] = (col["field"], goods.get(col["field"]))
    return out


async def _evidence_index(session: AsyncSession, shipment_id: int) -> dict[str, list[dict]]:
    """field_key -> evidence rows ordered by goods/tax line, best-grounded first."""
    rows = (
        await session.execute(
            select(ExtractedField, Document.filename, LineItem.seq)
            .join(Document, Document.id == ExtractedField.document_id)
            .outerjoin(LineItem, LineItem.id == ExtractedField.line_item_id)
            .where(Document.shipment_id == shipment_id)
        )
    ).all()

    index: dict[str, list[tuple[int, int, dict]]] = {}
    for field, filename, line_seq in rows:
        spec = ALL_FIELD_SPECS.get(field.field_key)
        evidence = {
            "document_id": field.document_id,
            "filename": filename,
            "page": field.page,
            "bbox": field.bbox,
            "cell_ref": field.cell_ref,
            "provenance_type": field.provenance_type,
            "review_status": field.review_status,
            "critical": bool(field.critical),
            "label_vi": spec.label_vi if spec else field.field_key,
        }
        # Header rows have no line; line rows are ordered by the goods seq they came from.
        order = line_seq if line_seq is not None else 0
        # Prefer precise provenance (cell/text with bbox) over page-only scans.
        precision = 0 if (field.cell_ref or field.bbox) else 1
        index.setdefault(field.field_key, []).append((order, precision, evidence))

    return {
        key: [ev for _, _, ev in sorted(items, key=lambda item: (item[0], item[1]))]
        for key, items in index.items()
    }


def _pick_evidence(index: dict[str, list[dict]], field_key: str) -> dict | None:
    entries = index.get(field_key) or []
    return entries[0] if entries else None


def _trim_grid(sheet: dict) -> dict:
    """Drop trailing all-empty rows/columns so the payload stays small."""
    rows = [list(r) for r in (sheet.get("rows") or [])]
    if not rows:
        return {"name": sheet.get("name"), "rows": [], "merges": sheet.get("merges") or []}
    width = max((len(r) for r in rows), default=0)
    while rows and all(v in (None, "") for v in rows[-1]):
        rows.pop()
    while width and all((row[width - 1] if width - 1 < len(row) else None) in (None, "") for row in rows):
        width -= 1
    return {
        "name": sheet.get("name"),
        "rows": [list(row[:width]) + [None] * (width - len(row[:width])) for row in rows],
        "merges": sheet.get("merges") or [],
    }


async def build_template_preview(
    session: AsyncSession, shipment_id: int, template_version: str = TOKHAI_TEMPLATE_VERSION
) -> dict:
    mapping, template_path = load_mapping(template_version)
    parsed = parse_document(template_path)
    grid = parsed.get("grid")
    if grid is None:
        raise ExportError("Mẫu này không có dạng bảng tính")

    payload = await build_declaration_payload(session, shipment_id) or _empty_payload()
    mapped = _mapped_payload(mapping, payload)
    evidences = await _evidence_index(session, shipment_id)

    cells: dict[str, dict[str, dict]] = {}
    for sheet_name, sheet_cells in mapped.items():
        bucket: dict[str, dict] = {}
        for ref, (field_key, value) in sheet_cells.items():
            if value in (None, ""):
                continue
            bucket[ref] = {
                "field": field_key,
                "value": value,
                "evidence": _pick_evidence(evidences, field_key),
            }
        if bucket:
            cells[sheet_name] = bucket

    return {
        "version": template_version,
        "name": mapping.get("name") or template_version,
        "has_data": any(
            v not in (None, "")
            for line in payload.goods
            for k, v in line.items()
            if k != "taxes"
        )
        or any(v not in (None, "") for v in payload.header.values()),
        "sheets": [_trim_grid(s) for s in grid["sheets"]],
        "cells": cells,
    }
