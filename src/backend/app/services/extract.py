from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz

from .constants import IngestError
from .fields import ALL_FIELD_SPECS, DOCUMENT_LEVEL_ONLY, FieldSpec, fields_for, is_critical
from .llm import LLMClient, LLMError, budget_for, ground_quote, locate_quote, normalize_bbox
from .ingest import extract_pdf_words, render_pdf_page_png
from .normalize import (
    normalize_company,
    normalize_currency,
    normalize_date,
    normalize_unit,
    parse_decimal,
    number_to_str,
)
from .schemas import (
    CLASSIFY_PROMPT,
    EXCEL_EXTRACT_PROMPT,
    EXTRACT_PROMPT,
    render_prompt,
    ClassificationOut,
    ExtractionOut,
)

DECLARATION_DOC_TYPES = {"CUSTOMS_DECLARATION", "CUSTOMS_DECLARATION_SCAN"}

FIELD_TO_MODEL_ATTR = {
    "document.doc_number": "doc_number",
    "document.issue_date": "issue_date",
    "document.issuer": "issuer",
    "transport.vessel": "vessel",
    "transport.voyage": "voyage",
    "transport.waybill_no": "waybill_no",
    "transport.container_no": "container_no",
    "transport.seal_no": "seal_no",
    "transport.packages": "packages",
    "transport.gross_weight_kg": "gross_weight",
    "transport.net_weight_kg": "net_weight",
    "transport.volume_cbm": "volume",
    "totals.total_amount": "total_amount",
    "totals.currency": "currency",
}

LINE_TO_MODEL_ATTR = {
    "line.description": "description",
    "line.spec": "spec",
    "line.quantity": "quantity",
    "line.unit": "unit",
    "line.unit_price": "unit_price",
    "line.amount": "amount",
}

@dataclass
class ExtractedRow:
    field_key: str
    raw_value: str | None
    normalized_value: str | None
    unit: str | None
    page: int | None
    bbox: list[float] | None
    cell_ref: str | None
    confidence: float | None
    provenance_type: str
    grounding_verified: bool
    review_status: str
    critical: bool
    line_index: int | None = None


def normalize_field_value(spec: FieldSpec, raw: str | None, locale: str | None) -> tuple[str | None, str | None]:
    """Return (normalized_value, unit). Unparseable input degrades to None, never guessed."""
    if raw is None:
        return None, None
    if spec.kind == "date":
        return normalize_date(raw), None
    if spec.kind == "currency":
        return normalize_currency(raw), None
    if spec.kind == "unit":
        return normalize_unit(raw), None
    if spec.kind == "int":
        num = parse_decimal(raw, locale)
        return (str(int(num)) if num is not None else None), None
    if spec.kind == "decimal":
        num = parse_decimal(raw, locale)
        if num is None:
            return None, None
        unit = "kg" if spec.key.endswith("_kg") else ("m³" if spec.key.endswith("_cbm") else None)
        return number_to_str(num), unit
    return raw.strip(), None


class _QuoteValue:
    """Small adapter so party strings go through the same grounding path as field values."""

    def __init__(self, value: str | None, quote: str | None = None):
        self.value = value
        self.source_quote = quote
        self.cell_ref = None


IDENTIFIER_PATTERNS = {
    "transport.waybill_no": re.compile(r"^[A-Z0-9-]{8,20}$", re.I),
    "transport.container_no": re.compile(r"^[A-Z]{4}\d{7}$", re.I),
    "transport.seal_no": re.compile(r"^[A-Z0-9-]{4,20}$", re.I),
}


def identifier_matches_shape(field_key: str, raw: str | None) -> bool | None:
    """Return False when a critical identifier clearly has another field's shape."""
    pattern = IDENTIFIER_PATTERNS.get(field_key)
    if pattern is None or not raw:
        return None
    return bool(pattern.match(raw.strip().replace(" ", "")))


def _content_budget(text: str, limit: int = 24000) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "\n...[rút gọn]"


def _grid_content(grid: dict, limit: int = 60) -> str:
    from openpyxl.utils import get_column_letter

    chunks = []
    for sheet in grid.get("sheets") or []:
        name = sheet.get("name") or "Sheet"
        chunks.append(f"### Sheet: {name}")
        for r_idx, row in enumerate((sheet.get("rows") or [])[:limit], start=1):
            cells = []
            for c_idx, value in enumerate(row, start=1):
                if value is None or str(value).strip() == "":
                    continue
                cells.append(f"{get_column_letter(c_idx)}{r_idx}={value}")
            if cells:
                chunks.append(" | ".join(cells))
    return "\n".join(chunks)


class ExtractionService:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def classify(
        self, filename: str, path: Path, has_text: bool, pages: list[str], grid: dict | None = None
    ) -> ClassificationOut:
        if grid is not None:
            raw = await self.llm.complete_json_validated(
                render_prompt(CLASSIFY_PROMPT, filename=filename, content=_content_budget(_grid_content(grid), 6000)),
                "classification",
                None,
                lambda data: ClassificationOut.model_validate(data),
                max_tokens=budget_for(3, False),
                model_override=self.llm.extraction_model(),
            )
            return raw
        if has_text:
            content = _content_budget("\n".join(pages[:2]))
            raw = await self.llm.complete_json_validated(
                render_prompt(CLASSIFY_PROMPT, filename=filename, content=content),
                "classification",
                None,
                lambda data: ClassificationOut.model_validate(data),
                max_tokens=budget_for(3, False),
                model_override=self.llm.extraction_model(),
            )
            return raw
        png = render_pdf_page_png(path, 0) if path.suffix.lower() == ".pdf" else path.read_bytes()
        raw = await self.llm.complete_json_validated(
            render_prompt(CLASSIFY_PROMPT, filename=filename, content="(xem hình ảnh trang 1)"),
            "classification",
            None,
            lambda data: ClassificationOut.model_validate(data),
            image_png=png,
            max_tokens=budget_for(3, False),
            model_override=self.llm.extraction_model(),
        )
        return raw

    async def extract(self, doc_type: str, filename: str, path: Path, pages: list[str], grid: dict | None) -> list[ExtractedRow]:
        if doc_type in DECLARATION_DOC_TYPES:
            return await self._extract_declaration(doc_type, filename, path, pages, grid)
        specs = fields_for(doc_type)
        metadata_only = doc_type in DOCUMENT_LEVEL_ONLY
        if grid is not None:
            content = _grid_content(grid)
            result = await self.llm.complete_json_validated(
                render_prompt(EXCEL_EXTRACT_PROMPT, doc_type=doc_type, content=content, limit=60),
                "extraction",
                None,
                lambda data: ExtractionOut.model_validate(data),
                max_tokens=budget_for(len(specs), not metadata_only),
                model_override=self.llm.extraction_model(),
            )
            if metadata_only:
                result = _strip_line_items(result)
            return self._rows_from_result(specs, result, provenance="CELL", pages=[], grid=grid, path=path, doc_type=doc_type)

        rows: list[ExtractedRow] = []
        text_pages = [i for i, p in enumerate(pages) if len(p.strip()) > 30]
        if text_pages:
            joined = _content_budget("\n\n".join(pages[i] for i in text_pages))
            result = await self.llm.complete_json_validated(
                render_prompt(EXTRACT_PROMPT, doc_type=doc_type, filename=filename, content=joined),
                "extraction",
                None,
                lambda data: ExtractionOut.model_validate(data),
                max_tokens=budget_for(len(specs), not metadata_only),
                model_override=self.llm.extraction_model(),
            )
            if metadata_only:
                result = _strip_line_items(result)
            # locate each quote on the page that actually contains it, not always page 1
            for page_index in text_pages:
                page_rows = self._rows_from_result(
                    specs, result, provenance="TEXT_LAYER", pages=pages, grid=None, path=path, doc_type=doc_type, page=page_index
                )
                for row in page_rows:
                    if row.raw_value is not None and row.grounding_verified:
                        row.confidence = row.confidence or 1.0
                if page_index == text_pages[0]:
                    rows = page_rows
                else:
                    rows = _merge_located(rows, page_rows)
            return rows


        for page_index in range(len(pages) or 1):
            png = render_pdf_page_png(path, page_index) if path.suffix.lower() == ".pdf" else path.read_bytes()
            result = await self.llm.complete_json_validated(
                render_prompt(EXTRACT_PROMPT, doc_type=doc_type, filename=filename, content=f"(hình ảnh trang {page_index + 1})"),
                "extraction",
                None,
                lambda data: ExtractionOut.model_validate(data),
                image_png=png,
            )
            rows.extend(
                self._rows_from_result(
                    specs, result, provenance="IMAGE", pages=[], grid=None, path=path, doc_type=doc_type, page=page_index
                )
            )
        return self._merge_pages(rows)

    async def _extract_declaration(
        self, doc_type: str, filename: str, path: Path, pages: list[str], grid: dict | None
    ) -> list[ExtractedRow]:
        """Extract a Tờ khai: header + goods blocks + tax table.

        Values are kept exactly as printed (see DECLARATION_EXTRACT_PROMPT); the row
        builder does no number normalisation so '23.230' cannot become '23230'.
        """
        from .schemas import DeclarationExtractionOut, DECLARATION_EXTRACT_PROMPT

        text_pages = [i for i, p in enumerate(pages) if len(p.strip()) > 30]
        result: DeclarationExtractionOut
        provenance: str
        page_no: int | None
        if grid is not None:
            raw = await self.llm.complete_json_validated(
                render_prompt(DECLARATION_EXTRACT_PROMPT, filename=filename, content=_grid_content(grid)),
                "extraction",
                None,
                lambda data: DeclarationExtractionOut.model_validate(data),
                max_tokens=budget_for(90, True),
                model_override=self.llm.extraction_model(),
            )
            result, provenance, page_no = raw, "CELL", None
        elif text_pages:
            joined = _content_budget("\n\n".join(pages[i] for i in text_pages))
            result = await self.llm.complete_json_validated(
                render_prompt(DECLARATION_EXTRACT_PROMPT, filename=filename, content=joined),
                "extraction",
                None,
                lambda data: DeclarationExtractionOut.model_validate(data),
                max_tokens=budget_for(90, True),
                model_override=self.llm.extraction_model(),
            )
            provenance, page_no = "TEXT_LAYER", text_pages[0] + 1
        else:
            png = render_pdf_page_png(path, 0) if path.suffix.lower() == ".pdf" else path.read_bytes()
            result = await self.llm.complete_json_validated(
                render_prompt(DECLARATION_EXTRACT_PROMPT, filename=filename, content="(xem hình ảnh tờ khai)"),
                "extraction",
                None,
                lambda data: DeclarationExtractionOut.model_validate(data),
                image_png=png,
                max_tokens=budget_for(90, True),
            )
            provenance, page_no = "IMAGE", 1

        rows: list[ExtractedRow] = []

        def add(field_key: str, fv, line_index: int | None = None) -> None:
            value = getattr(fv, "value", None)
            if value in (None, "") and not getattr(fv, "source_quote", None):
                return
            spec = ALL_FIELD_SPECS.get(field_key)
            quote = getattr(fv, "source_quote", None)
            grounded = False
            if quote and provenance == "TEXT_LAYER" and page_no and page_no - 1 < len(pages):
                grounded = quote.strip() in pages[page_no - 1]
            rows.append(
                ExtractedRow(
                    field_key=field_key,
                    raw_value=value,
                    normalized_value=value,
                    unit=None,
                    page=page_no,
                    bbox=None,
                    cell_ref=getattr(fv, "cell_ref", None) if provenance == "CELL" else None,
                    confidence=getattr(fv, "confidence", None),
                    provenance_type=provenance,
                    # page-level provenance is only ever located at page granularity
                    grounding_verified=grounded if value else False,
                    review_status="UNREVIEWED",
                    critical=is_critical(field_key),
                    line_index=line_index,
                )
            )

        for key in result.declaration.model_fields:
            add(f"declaration.{key}", getattr(result.declaration, key))
        for idx, goods in enumerate(result.goods, start=1):
            for key in goods.model_fields:
                add(f"goods.{key}", getattr(goods, key), line_index=idx)
        for idx, tax in enumerate(result.taxes, start=1):
            for key in tax.model_fields:
                add(f"tax.{key}", getattr(tax, key), line_index=idx)
        return rows

    def _rows_from_result(
        self,
        specs: tuple[FieldSpec, ...],
        result: ExtractionOut,
        provenance: str,
        pages: list[str],
        grid: dict | None,
        path: Path,
        doc_type: str,
        page: int = 0,
    ) -> list[ExtractedRow]:
        rows: list[ExtractedRow] = []
        locale = result.number_locale
        page_text = pages[page] if provenance == "TEXT_LAYER" and page < len(pages) else ""
        words = None
        page_size = None
        if provenance == "TEXT_LAYER" and path.suffix.lower() == ".pdf" and page_text:
            try:
                words = extract_pdf_words(path, page)
                with fitz.open(path) as doc:
                    rect = doc[page].rect
                    page_size = (rect.width, rect.height)
            except Exception:
                words, page_size = None, None

        for spec in specs:
            attr = FIELD_TO_MODEL_ATTR.get(spec.key)
            if not attr:
                continue
            fv = getattr(result, attr)
            normalized, unit = normalize_field_value(spec, fv.value, locale)
            grounding, bbox = self._ground(fv, provenance, page_text, words, page_size, grid)
            rows.append(
                ExtractedRow(
                    field_key=spec.key,
                    raw_value=fv.value,
                    normalized_value=normalized,
                    unit=unit,
                    page=page + 1 if (provenance == "TEXT_LAYER" or provenance == "IMAGE") else None,
                    bbox=bbox,
                    cell_ref=fv.cell_ref if provenance == "CELL" else None,
                    confidence=fv.confidence,
                    provenance_type=provenance,
                    grounding_verified=grounding,
                    review_status=self._review_status(spec, provenance, grounding, fv.value),
                    critical=is_critical(spec.key),
                )
            )

        party_map = {f"party.{p.role.lower()}": p for p in result.parties if p.role and p.name}
        for field_key, party in party_map.items():
            spec = next((s for s in specs if s.key == field_key), FieldSpec(field_key, field_key, "text"))
            quote = party.source_quote if hasattr(party, "source_quote") else None
            grounding, bbox = self._ground(_QuoteValue(party.name, quote), provenance, page_text, words, page_size, grid)
            rows.append(
                ExtractedRow(
                    field_key=field_key,
                    raw_value=party.name,
                    normalized_value=normalize_company(party.name),
                    unit=None,
                    page=page + 1 if provenance in {"TEXT_LAYER", "IMAGE"} else None,
                    bbox=bbox,
                    cell_ref=None,
                    confidence=None,
                    provenance_type=provenance,
                    grounding_verified=grounding,
                    review_status="UNREVIEWED",
                    critical=False,
                )
            )

        line_specs = [s for s in specs if s.key.startswith("line.")]
        for idx, item in enumerate(result.line_items):
            for spec in line_specs:
                attr = LINE_TO_MODEL_ATTR.get(spec.key)
                if not attr:
                    continue
                fv = getattr(item, attr)
                normalized, unit = normalize_field_value(spec, fv.value, locale)
                grounding, bbox = self._ground(fv, provenance, page_text, words, page_size, grid)
                rows.append(
                    ExtractedRow(
                        field_key=spec.key,
                        raw_value=fv.value,
                        normalized_value=normalized,
                        unit=unit,
                        page=page + 1 if provenance in {"TEXT_LAYER", "IMAGE"} else None,
                        bbox=bbox,
                        cell_ref=fv.cell_ref if provenance == "CELL" else None,
                        confidence=fv.confidence,
                        provenance_type=provenance,
                        grounding_verified=grounding,
                        review_status=self._review_status(spec, provenance, grounding, fv.value),
                        critical=is_critical(spec.key),
                        line_index=idx,
                    )
                )
        return rows

    def _ground(self, fv, provenance, page_text, words, page_size, grid) -> tuple[bool, list[float] | None]:
        if fv.value is None:
            return False, None
        if provenance == "TEXT_LAYER":
            ok = ground_quote(fv.source_quote, page_text) or ground_quote(fv.value, page_text)
            bbox = None
            if ok and words and page_size:
                raw = locate_quote(fv.source_quote, words) or locate_quote(fv.value, words)
                bbox = normalize_bbox(raw, page_size[0], page_size[1])
            return ok, bbox
        if provenance == "CELL":
            if not fv.cell_ref or grid is None:
                return False, None
            return _cell_contains(grid, fv.cell_ref, fv.value), None
        return False, None

    def _review_status(self, spec: FieldSpec, provenance: str, grounding: bool, raw: str | None = None) -> str:
        if identifier_matches_shape(spec.key, raw) is False:
            return "NEEDS_REVIEW"
        if provenance == "IMAGE" and spec.critical:
            return "NEEDS_REVIEW"
        if provenance in {"TEXT_LAYER", "CELL"} and spec.critical and not grounding:
            return "NEEDS_REVIEW"
        return "UNREVIEWED"

    def _merge_pages(self, rows: list[ExtractedRow]) -> list[ExtractedRow]:
        merged: dict[str, ExtractedRow] = {}
        line_rows: list[ExtractedRow] = []
        for row in rows:
            if row.line_index is not None:
                line_rows.append(row)
                continue
            if row.raw_value is None:
                continue
            existing = merged.get(row.field_key)
            if existing is None or existing.raw_value is None:
                merged[row.field_key] = row
        ordered = [merged[k] for k in sorted(merged)]
        base = self._line_base(rows)
        line_ordered = sorted(line_rows, key=lambda r: (r.line_index or 0, r.field_key))
        result = []
        for row in base:
            chosen = merged.get(row.field_key)
            result.append(chosen if chosen is not None else row)
        return result + line_ordered

    @staticmethod
    def _line_base(rows: list[ExtractedRow]) -> list[ExtractedRow]:
        seen: dict[str, ExtractedRow] = {}
        for row in rows:
            if row.line_index is None and row.field_key not in seen:
                seen[row.field_key] = row
        return list(seen.values())


def _cell_contains(grid: dict, cell_ref: str, value: str) -> bool:
    from openpyxl.utils import column_index_from_string

    try:
        sheet_name, coord = cell_ref.split("!", 1)
    except ValueError:
        return False
    sheet = next((s for s in grid.get("sheets") or [] if (s.get("name") or "").strip() == sheet_name.strip()), None)
    if sheet is None:
        return False
    import re

    m = re.match(r"([A-Za-z]+)(\d+)", coord.strip())
    if not m:
        return False
    col = column_index_from_string(m.group(1).upper())
    row = int(m.group(2))
    rows = sheet.get("rows") or []
    if row < 1 or row > len(rows):
        return False
    cells = rows[row - 1]
    if col < 1 or col > len(cells):
        return False
    cell_value = cells[col - 1]
    if cell_value is None:
        return False
    target = str(value).strip().lower().replace(" ", "")
    actual = str(cell_value).strip().lower().replace(" ", "")
    return target == actual or target in actual or actual in target


def _strip_line_items(result: ExtractionOut) -> ExtractionOut:
    """Supporting documents only contribute metadata; line items are left to invoices/packing lists."""
    return result.model_copy(update={"line_items": []})


def _merge_located(primary: list[ExtractedRow], located: list[ExtractedRow]) -> list[ExtractedRow]:
    """Keep the first grounded row per field key, filling in ones only located on later pages."""
    by_key = {row.field_key: row for row in primary if row.line_index is None}
    line_rows = [row for row in primary if row.line_index is not None]
    for row in located:
        if row.line_index is not None:
            continue
        current = by_key.get(row.field_key)
        if row.raw_value is not None and row.grounding_verified and (current is None or not current.grounding_verified):
            by_key[row.field_key] = row
    return [by_key[k] for k in sorted(by_key)] + line_rows
