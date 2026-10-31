from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz

from .constants import IngestError
from .fields import ALL_FIELD_SPECS, DOCUMENT_LEVEL_ONLY, FieldSpec, fields_for, is_critical
from .llm import LLMClient, LLMError, budget_for, ground_quote, locate_quote, normalize_bbox
from .ingest import extract_pdf_words, normalize_doc_type, render_pdf_page_png
from .page_router import build_page_manifests
from .normalize import (
    normalize_company,
    normalize_currency,
    normalize_date,
    normalize_unit,
    parse_decimal,
    number_to_str,
)
from .schemas import (
    CLASSIFY_PAGE_PROMPT,
    CLASSIFY_PROMPT,
    EXCEL_EXTRACT_PROMPT,
    EXTRACT_PROMPT,
    render_prompt,
    ClassificationOut,
    ExtractionOut,
)

DECLARATION_DOC_TYPES = {"CUSTOMS_DECLARATION", "CUSTOMS_DECLARATION_SCAN"}
# Declaration rows keep their sparse encoding but shift past business line indexes.
DECLARATION_LINE_OFFSET = 1000
GOODS_TAX_ENCODING = 100

FIELD_TO_MODEL_ATTR = {
    "document.doc_number": "doc_number",
    "document.issue_date": "issue_date",
    "document.issuer": "issuer",
    "transport.vessel": "vessel",
    "transport.voyage": "voyage",
    "transport.waybill_no": "waybill_no",
    "transport.container_no": "container_no",
    "transport.container_count": "container_count",
    "transport.seal_no": "seal_no",
    "transport.packages": "packages",
    "transport.package_unit": "package_unit",
    "transport.gross_weight_kg": "gross_weight",
    "transport.gross_weight_unit": "gross_weight_unit",
    "transport.net_weight_kg": "net_weight",
    "transport.volume_cbm": "volume",
    "transport.origin_country_code": "origin_country_code",
    "transport.arrival_date": "arrival_date",
    "transport.payment_method": "payment_method",
    "transport.invoice_terms": "invoice_terms",
    "totals.total_amount": "total_amount",
    "totals.currency": "currency",
}

LINE_TO_MODEL_ATTR = {
    "line.description": "description",
    "line.spec": "spec",
    "line.hs_code": "hs_code",
    "line.origin_country_code": "origin_country_code",
    "line.container_no": "container_no",
    "line.quantity": "quantity",
    "line.unit": "unit",
    "line.unit_price": "unit_price",
    "line.amount": "amount",
    "line.currency": "currency",
}

PARTY_ATTRIBUTE_KEYS = ("address", "tax_code", "phone", "country_code")

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
    section_type: str | None = None
    source_row_index: int | None = None
    container_no: str | None = None
    evidence_state: str | None = None


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


def _grid_content(grid: dict, limit: int | None = None, ranges: dict | None = None) -> str:
    """Full sparse dump of the workbook (every non-empty cell), delegated to excel_reader.

    `limit`/`ranges` are forwarded for callers that ask for one block; the default keeps
    all rows so goods blocks at row 148/201 are not dropped.
    """
    from .excel_reader import render_grid

    text, _truncated = render_grid(grid, limit=limit, ranges=ranges)
    return text


class ExtractionService:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def classify(
        self, filename: str, path: Path, has_text: bool, pages: list[str], grid: dict | None = None
    ) -> ClassificationOut:
        """Classify a whole file. Kept for Excel and single-page callers."""
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

    async def classify_document(
        self, filename: str, path: Path, pages: list[str], grid: dict | None = None
    ) -> ClassificationOut:
        """Classify page by page and group adjacent pages into document sections.

        The old classifier looked only at `pages[:2]` (or page 1 for a scan), so a
        10-page bundle whose first two pages were blank got `OTHER`, and a mixed PDF
        could never report more than one document inside it.
        """
        from .page_router import (
            SECTION_UNKNOWN,
            STRATEGY_TEXT,
            STRATEGY_TEXT_PLUS_VISION,
            STRATEGY_VISION,
            build_page_manifests,
            group_sections,
        )

        if grid is not None or len(pages) <= 1:
            if grid is not None:
                return await self.classify(filename, path, True, pages, grid)
            manifests = build_page_manifests(path, pages)
            manifest = manifests[0]
            if manifest.strategy == STRATEGY_VISION:
                # A page number or watermark is not content: classify from the image.
                png = (
                    render_pdf_page_png(path, manifest.index)
                    if path.suffix.lower() == ".pdf"
                    else path.read_bytes()
                )
                base = await self.llm.complete_json_validated(
                    render_prompt(CLASSIFY_PAGE_PROMPT, filename=filename, page=1, content="(hình ảnh trang 1)"),
                    "classification",
                    None,
                    lambda data: ClassificationOut.model_validate(data),
                    image_png=png,
                    max_tokens=budget_for(3, False),
                    model_override=self.llm.extraction_model(),
                )
            else:
                base = await self.classify(filename, path, bool(manifest.text_chars), pages, None)
            sections = group_sections(manifests, [normalize_doc_type(base.doc_type)])
            base.sections = [s.as_dict() for s in sections]
            return base

        manifests = build_page_manifests(path, pages)
        page_types: list[str | None] = []
        confidences: list[float] = []
        for manifest in manifests:
            try:
                if manifest.strategy in {STRATEGY_TEXT, STRATEGY_TEXT_PLUS_VISION} and manifest.text_chars > 0:
                    content = _content_budget(manifest.text, 6000)
                    raw = await self.llm.complete_json_validated(
                        render_prompt(
                            CLASSIFY_PAGE_PROMPT,
                            filename=filename,
                            page=manifest.index + 1,
                            content=content,
                        ),
                        "classification",
                        None,
                        lambda data: ClassificationOut.model_validate(data),
                        max_tokens=budget_for(3, False),
                        model_override=self.llm.extraction_model(),
                    )
                else:
                    png = (
                        render_pdf_page_png(path, manifest.index)
                        if path.suffix.lower() == ".pdf"
                        else path.read_bytes()
                    )
                    raw = await self.llm.complete_json_validated(
                        render_prompt(
                            CLASSIFY_PAGE_PROMPT,
                            filename=filename,
                            page=manifest.index + 1,
                            content=f"(hình ảnh trang {manifest.index + 1})",
                        ),
                        "classification",
                        None,
                        lambda data: ClassificationOut.model_validate(data),
                        image_png=png,
                        max_tokens=budget_for(3, False),
                        model_override=self.llm.extraction_model(),
                    )
                page_types.append(normalize_doc_type(raw.doc_type))
                if raw.confidence is not None:
                    confidences.append(float(raw.confidence))
            except Exception:
                page_types.append(None)

        sections = group_sections(manifests, page_types)
        counts: dict[str, int] = {}
        for page_type in page_types:
            key = page_type or SECTION_UNKNOWN
            counts[key] = counts.get(key, 0) + 1
        ranked = sorted(
            ((k, v) for k, v in counts.items() if k not in {SECTION_UNKNOWN, "OTHER"}),
            key=lambda item: (-item[1], item[0]),
        )
        dominant = ranked[0][0] if ranked else "OTHER"
        confidence = sum(confidences) / len(confidences) if confidences else None
        return ClassificationOut(
            doc_type=dominant,
            confidence=confidence,
            reason=f"{len(sections)} đoạn từ {len(pages)} trang",
            sections=[s.as_dict() for s in sections],
        )


    async def extract(self, doc_type: str, filename: str, path: Path, pages: list[str], grid: dict | None) -> list[ExtractedRow]:
        if doc_type in DECLARATION_DOC_TYPES:
            return await self._extract_declaration(doc_type, filename, path, pages, grid)
        specs = fields_for(doc_type)
        metadata_only = doc_type in DOCUMENT_LEVEL_ONLY
        if grid is not None:
            content = _grid_content(grid)
            result = await self.llm.complete_json_validated(
                render_prompt(EXCEL_EXTRACT_PROMPT, doc_type=doc_type, content=content, limit="toàn bộ"),
                "extraction",
                None,
                lambda data: ExtractionOut.model_validate(data),
                max_tokens=budget_for(len(specs), not metadata_only),
                model_override=self.llm.extraction_model(),
            )
            if metadata_only:
                result = _strip_line_items(result)
            return self._rows_from_result(specs, result, provenance="CELL", pages=[], grid=grid, path=path, doc_type=doc_type)

        # Route per page, not per file: one text page anywhere used to suppress the
        # vision pass for every scanned page (the TAEDA invoice lives on a scan).
        manifests = build_page_manifests(path, pages)
        return await self._extract_by_page_routing(
            specs, metadata_only, filename, path, doc_type, pages, manifests
        )

    async def _extract_by_page_routing(
        self,
        specs: tuple[FieldSpec, ...],
        metadata_only: bool,
        filename: str,
        path: Path,
        doc_type: str,
        pages: list[str],
        manifests: list,
    ) -> list[ExtractedRow]:
        from .page_router import (
            STRATEGY_TEXT,
            STRATEGY_TEXT_PLUS_VISION,
            STRATEGY_VISION,
        )

        # Follow the router: a page routed VISION or TEXT_PLUS_VISION always gets an
        # image call, even when its text layer is non-empty.
        text_only = [
            m.index for m in manifests if m.strategy == STRATEGY_TEXT and m.text_chars > 0
        ]
        tpv_pages = [
            m.index for m in manifests if m.strategy == STRATEGY_TEXT_PLUS_VISION
        ]
        vision_pages = [
            m.index for m in manifests if m.strategy in {STRATEGY_VISION, STRATEGY_TEXT_PLUS_VISION}
        ]

        rows: list[ExtractedRow] = []
        if text_only:
            joined = _content_budget("\n\n".join(pages[i] for i in text_only))
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
            text_rows = self._rows_from_result(
                specs, result, provenance="TEXT_LAYER", pages=pages, grid=None, path=path,
                doc_type=doc_type, page=text_only[0],
            )
            rows = _merge_evidence(rows, text_rows)

        # A TEXT_PLUS_VISION page needs its own text call: a joined pass would label the
        # row with the first text page, and the text/vision dedupe key would never match.
        for page_index in tpv_pages:
            result = await self.llm.complete_json_validated(
                render_prompt(
                    EXTRACT_PROMPT,
                    doc_type=doc_type,
                    filename=filename,
                    content=_content_budget(pages[page_index]),
                ),
                "extraction",
                None,
                lambda data: ExtractionOut.model_validate(data),
                max_tokens=budget_for(len(specs), not metadata_only),
                model_override=self.llm.extraction_model(),
            )
            if metadata_only:
                result = _strip_line_items(result)
            text_rows = self._rows_from_result(
                specs, result, provenance="TEXT_LAYER", pages=pages, grid=None, path=path,
                doc_type=doc_type, page=page_index,
            )
            rows = _merge_evidence(rows, text_rows)

        for page_index in vision_pages:
            png = render_pdf_page_png(path, page_index) if path.suffix.lower() == ".pdf" else path.read_bytes()
            result = await self.llm.complete_json_validated(
                render_prompt(EXTRACT_PROMPT, doc_type=doc_type, filename=filename, content=f"(hình ảnh trang {page_index + 1})"),
                "extraction",
                None,
                lambda data: ExtractionOut.model_validate(data),
                image_png=png,
            )
            if metadata_only:
                result = _strip_line_items(result)
            vision_rows = self._rows_from_result(
                specs, result, provenance="IMAGE", pages=[], grid=None, path=path,
                doc_type=doc_type, page=page_index,
            )
            rows = _merge_evidence(rows, vision_rows)
        return rows


    async def extract_sections(
        self,
        filename: str,
        path: Path,
        pages: list[str],
        sections: list[dict],
        fallback_doc_type: str,
        grid: dict | None = None,
    ) -> list[ExtractedRow]:
        """Extract each logical section with the schema of its own type.

        Sections are annotated page ranges; the uploaded file is never split or copied.
        A single-section document is extracted exactly as before, so the per-file
        behaviour is unchanged when the bundle really holds one document.
        """
        if grid is not None or not sections:
            return await self.extract(fallback_doc_type, filename, path, pages, grid)
        if len(sections) == 1:
            section_type = sections[0].get("section_type") or fallback_doc_type
            if section_type in {None, "", "UNKNOWN", "MIXED_BUNDLE"}:
                section_type = fallback_doc_type
            return await self.extract(section_type, filename, path, pages, None)

        rows: list[ExtractedRow] = []
        declaration_sections_seen = 0
        for section in sections:
            section_type = section.get("section_type") or fallback_doc_type
            if section_type in {"UNKNOWN", "MIXED_BUNDLE", ""}:
                section_type = fallback_doc_type
            page_indexes = [int(p) for p in section.get("pages") or []]
            section_pages = [
                pages[i] if 0 <= i < len(pages) else "" for i in page_indexes
            ]
            if not section_pages:
                continue
            if section_type in DECLARATION_DOC_TYPES:
                section_rows = await self._extract_declaration(
                    section_type, filename, path, section_pages, None, page_indexes
                )
                if len(sections) > 1:
                    # Declaration rows share the flat line_index/compound tax encoding;
                    # in a bundle they must not collide with the invoice's line indexes,
                    # and two declaration sections must not collide with each other.
                    declaration_sections_seen += 1
                    goods_offset = DECLARATION_LINE_OFFSET * declaration_sections_seen
                    for row in section_rows:
                        if row.line_index is None:
                            continue
                        if row.field_key.startswith("goods_tax."):
                            row.line_index += goods_offset * GOODS_TAX_ENCODING
                        elif row.field_key.startswith("goods."):
                            row.line_index += goods_offset
            else:
                section_rows = await self._extract_pages_as_type(
                    section_type, filename, path, pages, page_indexes
                )
            for row in section_rows:
                row.section_type = section_type
            rows = _merge_evidence(rows, section_rows)
        return rows

    async def _extract_pages_as_type(
        self,
        doc_type: str,
        filename: str,
        path: Path,
        pages: list[str],
        page_indexes: list[int],
    ) -> list[ExtractedRow]:
        from .page_router import STRATEGY_TEXT, STRATEGY_TEXT_PLUS_VISION

        specs = fields_for(doc_type)
        metadata_only = doc_type in DOCUMENT_LEVEL_ONLY
        manifests = build_page_manifests(path, pages)

        def strategy_of(index: int) -> str:
            if index < len(manifests):
                return manifests[index].strategy
            return STRATEGY_TEXT

        rows: list[ExtractedRow] = []
        text_only = [
            i for i in page_indexes if i < len(pages) and strategy_of(i) == STRATEGY_TEXT and pages[i].strip()
        ]
        tpv_indexes = [i for i in page_indexes if i < len(pages) and strategy_of(i) == STRATEGY_TEXT_PLUS_VISION]
        if text_only:
            joined = _content_budget("\n\n".join(pages[i] for i in text_only))
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
            rows = _merge_evidence(
                rows,
                self._rows_from_result(
                    specs, result, provenance="TEXT_LAYER", pages=pages, grid=None,
                    path=path, doc_type=doc_type, page=text_only[0],
                ),
            )
        # Per-page text call for TEXT_PLUS_VISION pages, so the row keeps its own page
        # label and the text/vision identity key can match.
        for page_index in tpv_indexes:
            result = await self.llm.complete_json_validated(
                render_prompt(
                    EXTRACT_PROMPT,
                    doc_type=doc_type,
                    filename=filename,
                    content=_content_budget(pages[page_index]),
                ),
                "extraction",
                None,
                lambda data: ExtractionOut.model_validate(data),
                max_tokens=budget_for(len(specs), not metadata_only),
                model_override=self.llm.extraction_model(),
            )
            if metadata_only:
                result = _strip_line_items(result)
            rows = _merge_evidence(
                rows,
                self._rows_from_result(
                    specs, result, provenance="TEXT_LAYER", pages=pages, grid=None,
                    path=path, doc_type=doc_type, page=page_index,
                ),
            )
        for page_index in page_indexes:
            manifest = manifests[page_index] if page_index < len(manifests) else None
            if manifest is None or manifest.strategy not in {"VISION", "TEXT_PLUS_VISION"}:
                continue
            png = (
                render_pdf_page_png(path, page_index)
                if path.suffix.lower() == ".pdf"
                else path.read_bytes()
            )
            result = await self.llm.complete_json_validated(
                render_prompt(
                    EXTRACT_PROMPT,
                    doc_type=doc_type,
                    filename=filename,
                    content=f"(hình ảnh trang {page_index + 1})",
                ),
                "extraction",
                None,
                lambda data: ExtractionOut.model_validate(data),
                image_png=png,
            )
            if metadata_only:
                result = _strip_line_items(result)
            rows = _merge_evidence(
                rows,
                self._rows_from_result(
                    specs, result, provenance="IMAGE", pages=[], grid=None,
                    path=path, doc_type=doc_type, page=page_index,
                ),
            )
        return rows

    async def _extract_declaration(
        self,
        doc_type: str,
        filename: str,
        path: Path,
        pages: list[str],
        grid: dict | None,
        page_indexes: list[int] | None = None,
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
        local_page_no: int | None = None
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
            first_local = text_pages[0]
            local_page_no = first_local
            absolute = page_indexes[first_local] if page_indexes and first_local < len(page_indexes) else first_local
            provenance, page_no = "TEXT_LAYER", absolute + 1
        else:
            absolute = page_indexes[0] if page_indexes else 0
            png = render_pdf_page_png(path, absolute) if path.suffix.lower() == ".pdf" else path.read_bytes()
            result = await self.llm.complete_json_validated(
                render_prompt(DECLARATION_EXTRACT_PROMPT, filename=filename, content="(xem hình ảnh tờ khai)"),
                "extraction",
                None,
                lambda data: DeclarationExtractionOut.model_validate(data),
                image_png=png,
                max_tokens=budget_for(90, True),
            )
            provenance, page_no = "IMAGE", absolute + 1

        rows: list[ExtractedRow] = []

        def add(field_key: str, fv, line_index: int | None = None) -> None:
            value = getattr(fv, "value", None)
            if value in (None, "") and not getattr(fv, "source_quote", None):
                return
            spec = ALL_FIELD_SPECS.get(field_key)
            quote = getattr(fv, "source_quote", None)
            grounded = False
            if quote and provenance == "TEXT_LAYER" and local_page_no is not None and local_page_no < len(pages):
                grounded = quote.strip() in pages[local_page_no]
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
                    evidence_state=self._evidence_state(provenance, bool(grounded)),
                )
            )

        for key in type(result.declaration).model_fields:
            add(f"declaration.{key}", getattr(result.declaration, key))
        for idx, goods in enumerate(result.goods, start=1):
            for key in type(goods).model_fields:
                if key == "taxes":
                    # Compound line_index: goods_seq * 100 + tax_slot (1..5).
                    # Decoded in pipeline._persist_declaration / _persist with the
                    # same encoding — see GOODS_TAX_ENCODING there.
                    for t_idx, tax in enumerate(goods.taxes or [], start=1):
                        for t_key in type(tax).model_fields:
                            add(f"goods_tax.{t_key}", getattr(tax, t_key), line_index=idx * 100 + t_idx)
                else:
                    add(f"goods.{key}", getattr(goods, key), line_index=idx)
        for idx, tax in enumerate(result.taxes, start=1):
            for key in type(tax).model_fields:
                add(f"tax.{key}", getattr(tax, key), line_index=idx)
        for idx, lic in enumerate(result.licenses, start=1):
            for key in type(lic).model_fields:
                add(f"license.{key}", getattr(lic, key), line_index=idx)
        for idx, ins in enumerate(result.instructions, start=1):
            for key in type(ins).model_fields:
                add(f"instruction.{key}", getattr(ins, key), line_index=idx)
        for idx, adj in enumerate(result.adjustments, start=1):
            for key in type(adj).model_fields:
                add(f"adjustment.{key}", getattr(adj, key), line_index=idx)
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
                    evidence_state=self._evidence_state(provenance, grounding),
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
                    evidence_state=self._evidence_state(provenance, grounding),
                )
            )
            role = (party.role or "").lower()
            for attr in PARTY_ATTRIBUTE_KEYS:
                value = getattr(party, attr, None)
                if not value:
                    continue
                rows.append(
                    ExtractedRow(
                        field_key=f"party.{role}.{attr}",
                        raw_value=value,
                        normalized_value=normalize_company(value) if attr == "address" else str(value).strip(),
                        unit=None,
                        page=page + 1 if provenance in {"TEXT_LAYER", "IMAGE"} else None,
                        bbox=None,
                        cell_ref=None,
                        confidence=None,
                        provenance_type=provenance,
                        grounding_verified=False,
                        review_status="NEEDS_REVIEW",
                        critical=False,
                        evidence_state="EVIDENCE_LOCATED",
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
                        source_row_index=idx,
                        container_no=_row_container_no(item),
                        evidence_state=self._evidence_state(provenance, grounding),
                    )
                )
        return rows

    def _evidence_state(self, provenance: str, grounding: bool) -> str:
        """A machine-checked quote is FACT_VERIFIED; a scan is only EVIDENCE_LOCATED."""
        if provenance in {"TEXT_LAYER", "CELL"} and grounding:
            return "FACT_VERIFIED"
        return "EVIDENCE_LOCATED"

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


def _row_container_no(item) -> str | None:
    """Container-level invoice rows keep their container id as row identity."""
    import re

    for attr in ("container_no", "description", "spec"):
        fv = getattr(item, attr, None)
        value = getattr(fv, "value", None) if fv is not None else None
        if not value:
            continue
        match = re.search(r"\b([A-Z]{4}\d{7})\b", str(value))
        if match:
            return match.group(1)
    return None


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


def _merge_evidence(primary: list[ExtractedRow], extra: list[ExtractedRow]) -> list[ExtractedRow]:
    """Merge two extraction passes, keeping every disagreeing candidate.

    The old code let the text-layer pass win outright, so a scanned page that carried
    a different (or the only) value was silently dropped. Here document-level fields
    are merged by value: identical values collapse to one row (preferring a grounded
    one), while different values are kept side by side and both marked NEEDS_REVIEW.
    Line items are never de-duplicated across passes, because two pages showing the
    same product are still two source rows.
    """
    rows = list(primary)
    by_key: dict[str, list[ExtractedRow]] = {}
    for row in rows:
        if row.line_index is None and row.raw_value is not None:
            by_key.setdefault(row.field_key, []).append(row)
    # Each extraction pass numbers its rows from 0. Merging two passes (two scanned
    # pages, or a bundle's invoice and packing sections) would otherwise collapse two
    # different rows into one LineItem, so give each source row a fresh document index.
    used_indexes = [r.line_index for r in rows if r.line_index is not None]
    next_index = (max(used_indexes) + 1) if used_indexes else 1
    line_key_map: dict[tuple, int] = {}
    # Seed from the primary pass so the same physical row read by both text and vision
    # is recognised instead of becoming a second LineItem.
    for row in rows:
        if row.field_key.startswith("line.") and row.line_index is not None:
            key = (row.page, row.section_type, row.source_row_index, row.container_no)
            line_key_map.setdefault(key, row.line_index)
    for candidate in extra:
        if candidate.line_index is not None:
            if candidate.field_key.startswith("line."):
                # Identity is where the row sits in the document, not the pass-local
                # index the model happened to return.
                key = (
                    candidate.page,
                    candidate.section_type,
                    candidate.source_row_index,
                    candidate.container_no,
                )
                existing_index = line_key_map.get(key)
                if existing_index is None:
                    line_key_map[key] = next_index
                    candidate.line_index = next_index
                    next_index += 1
                    rows.append(candidate)
                    continue
                candidate.line_index = existing_index
                peer = next(
                    (
                        r
                        for r in rows
                        if r.line_index == existing_index and r.field_key == candidate.field_key
                    ),
                    None,
                )
                if peer is not None and (
                    (peer.normalized_value or peer.raw_value)
                    == (candidate.normalized_value or candidate.raw_value)
                ):
                    # Same physical row read twice (text and vision): keep one fact.
                    continue
                if peer is not None:
                    peer.review_status = "NEEDS_REVIEW"
                    candidate.review_status = "NEEDS_REVIEW"
                rows.append(candidate)
                continue
            rows.append(candidate)
            continue
        if candidate.raw_value is None:
            continue
        existing = by_key.get(candidate.field_key)
        if not existing:
            rows.append(candidate)
            by_key[candidate.field_key] = [candidate]
            continue
        same = next(
            (r for r in existing if (r.normalized_value or r.raw_value) == (candidate.normalized_value or candidate.raw_value)),
            None,
        )
        if same is not None:
            if candidate.grounding_verified and not same.grounding_verified:
                same.grounding_verified = True
                same.page = candidate.page
                same.bbox = candidate.bbox
            if same.confidence is None:
                same.confidence = candidate.confidence
            continue
        candidate.review_status = "NEEDS_REVIEW"
        for other in existing:
            other.review_status = "NEEDS_REVIEW"
        rows.append(candidate)
        existing.append(candidate)
    return rows

