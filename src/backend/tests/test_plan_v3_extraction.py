from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pymupdf as fitz
import pytest
from openpyxl import Workbook, load_workbook

from app.services.excel_reader import render_grid
from app.services.ingest import parse_document
from app.services.page_router import (
    STRATEGY_TEXT,
    STRATEGY_TEXT_PLUS_VISION,
    STRATEGY_VISION,
    build_page_manifests,
    group_sections,
)


def _mixed_pdf(path: Path) -> Path:
    """Page 1 has a text layer, page 2 is an image-only scan (the TAEDA invoice shape)."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "SALES CONTRACT\nNo. LS2628\nShipped per YM CONTINENT\n" * 6)
    scan = doc.new_page()
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 68, 68))
    pixmap.set_rect(pixmap.irect, (200, 200, 200))
    scan.insert_image(scan.rect, pixmap=pixmap)
    doc.save(path)
    doc.close()
    return path


def test_page_routing_is_per_page_not_per_file(tmp_path: Path):
    """RC1: one text page in a file must not suppress vision on the scanned page."""
    pdf = _mixed_pdf(tmp_path / "bundle.pdf")
    parsed = parse_document(pdf)
    manifests = build_page_manifests(pdf, parsed["pages"])
    assert manifests[0].strategy == STRATEGY_TEXT
    assert manifests[1].strategy == STRATEGY_VISION
    assert manifests[1].text_chars == 0


def test_sections_group_adjacent_pages_and_report_mixed_bundle(tmp_path: Path):
    pdf = _mixed_pdf(tmp_path / "bundle.pdf")
    parsed = parse_document(pdf)
    manifests = build_page_manifests(pdf, parsed["pages"])
    sections = group_sections(manifests, ["SALES_CONTRACT_PO", "COMMERCIAL_INVOICE"])
    assert [(s.page_start, s.page_end, s.section_type) for s in sections] == [
        (0, 0, "SALES_CONTRACT_PO"),
        (1, 1, "COMMERCIAL_INVOICE"),
    ]


def test_adjacent_pages_of_same_type_form_one_section(tmp_path: Path):
    pdf = _mixed_pdf(tmp_path / "bundle.pdf")
    parsed = parse_document(pdf)
    manifests = build_page_manifests(pdf, parsed["pages"])
    sections = group_sections(manifests, ["PACKING_LIST", "PACKING_LIST"])
    assert len(sections) == 1
    assert (sections[0].page_start, sections[0].page_end) == (0, 1)


class RouteStubLLM:
    """Classifies per page by content and returns two invoice container rows."""

    settings = SimpleNamespace(max_tokens=32000)

    def __init__(self):
        self.image_calls = 0
        self.prompts: list[str] = []

    def extraction_model(self):
        return "vision_sonya"

    async def complete_json_validated(
        self, prompt, schema_name, schema, validator, image_png=None, max_attempts=3,
        max_tokens=None, model_override=None,
    ):
        self.prompts.append(prompt)
        if schema_name == "classification":
            if "SALES CONTRACT" in prompt:
                return validator({"doc_type": "SALES_CONTRACT_PO", "confidence": 0.9})
            if "hình ảnh" in prompt:
                return validator({"doc_type": "COMMERCIAL_INVOICE", "confidence": 0.9})
            return validator({"doc_type": "OTHER", "confidence": 0.5})
        if image_png is not None:
            self.image_calls += 1
            return validator(
                {
                    "doc_number": {"value": "CD2025121801-2"},
                    "issue_date": {"value": "14/04/2026"},
                    "total_amount": {"value": "23.230"},
                    "currency": {"value": "USD"},
                    "number_locale": "period_decimal",
                    "line_items": [
                        {
                            "container_no": {"value": "HMMU6849510"},
                            "description": {"value": "TAEDA PINE KD LUMBER"},
                            "quantity": {"value": "46.46"},
                            "unit": {"value": "CBM"},
                            "unit_price": {"value": "250"},
                            "amount": {"value": "11615"},
                        },
                        {
                            "container_no": {"value": "KOCU4411578"},
                            "description": {"value": "TAEDA PINE KD LUMBER"},
                            "quantity": {"value": "46.46"},
                            "unit": {"value": "CBM"},
                            "unit_price": {"value": "250"},
                            "amount": {"value": "11615"},
                        },
                    ],
                }
            )
        return validator({"number_locale": "period_decimal", "parties": [], "line_items": []})


async def test_mixed_pdf_extracts_scan_page_and_keeps_both_container_rows(tmp_path: Path):
    """RC1/RC2 + A2: the invoice scan is read and both container rows survive with identity."""
    from app.services.extract import ExtractionService

    pdf = _mixed_pdf(tmp_path / "taeda.pdf")
    parsed = parse_document(pdf)
    stub = RouteStubLLM()
    service = ExtractionService(stub)

    classification = await service.classify_document("taeda.pdf", pdf, parsed["pages"])
    assert classification.doc_type in {"COMMERCIAL_INVOICE", "SALES_CONTRACT_PO"}
    assert len(classification.sections) == 2

    rows = await service.extract_sections(
        "taeda.pdf", pdf, parsed["pages"], classification.sections, classification.doc_type
    )
    assert stub.image_calls >= 1, "the scanned invoice page must reach the vision model"
    line_amounts = [r for r in rows if r.field_key == "line.amount"]
    assert len(line_amounts) == 2
    assert {r.container_no for r in line_amounts} == {"HMMU6849510", "KOCU4411578"}
    assert all(r.source_row_index is not None for r in line_amounts)


def test_excel_reader_sees_rows_beyond_sixty(tmp_path: Path):
    """RC4: goods blocks live at rows 148/201 and were cut by the old 60-row dump."""
    path = tmp_path / "tkn.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "TKN"
    ws["A1"] = "Số tờ khai"
    ws["K70"] = "Tỷ giá tính thuế"
    ws["G149"] = "44071110"
    ws["G202"] = "44071190"
    wb.save(path)

    parsed = parse_document(path)
    text, truncated = render_grid(parsed["grid"])
    assert truncated is False
    for token in ("K70=", "G149=", "G202="):
        assert token in text


def test_grid_content_keeps_late_rows():
    from app.services.extract import _grid_content

    grid = {"sheets": [{"name": "TKN", "rows": [[None] * 7 for _ in range(210)], "merges": []}]}
    grid["sheets"][0]["rows"][148][6] = "44071110"
    grid["sheets"][0]["rows"][201][6] = "44071190"
    content = _grid_content(grid)
    assert "G149=44071110" in content
    assert "G202=44071190" in content


def test_blank_templates_hold_no_shipment_identity():
    from app.services.export_excel import load_mapping
    from app.services.template_sanitize import (
        data_cells_with_values,
        identifier_leaks,
    )

    for version in ("tokhai_import_v1", "uandi_import_v1"):
        _, template_path = load_mapping(version)
        assert data_cells_with_values(template_path, version) == [], version
        assert identifier_leaks(template_path, version) == [], version


def test_tokhai_template_is_blank_in_audited_cells():
    from app.services.export_excel import load_mapping

    _, template_path = load_mapping("tokhai_import_v1")
    ws = load_workbook(template_path)["TKN"]
    for cell in ("U25", "H26", "U26"):
        assert ws[cell].value in (None, "")


def _text_plus_image_pdf(path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "INVOICE No. CD2025121801-2\nTotal USD 23,230\n" * 3)
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 68, 68))
    pixmap.set_rect(pixmap.irect, (210, 210, 210))
    page.insert_image(page.rect, pixmap=pixmap)
    doc.save(path)
    doc.close()
    return path


def test_page_with_text_and_image_is_routed_to_both_sources(tmp_path: Path):
    pdf = _text_plus_image_pdf(tmp_path / "both.pdf")
    parsed = parse_document(pdf)
    manifests = build_page_manifests(pdf, parsed["pages"])
    assert manifests[0].strategy == STRATEGY_TEXT_PLUS_VISION


async def test_text_plus_vision_page_always_calls_the_vision_model(tmp_path: Path):
    """RC1 again: a text layer plus an embedded scan must still reach the VLM."""
    from app.services.extract import ExtractionService

    pdf = _text_plus_image_pdf(tmp_path / "both.pdf")
    parsed = parse_document(pdf)
    stub = RouteStubLLM()
    rows = await ExtractionService(stub).extract(
        "COMMERCIAL_INVOICE", "both.pdf", pdf, parsed["pages"], None
    )
    assert stub.image_calls >= 1
    assert len([r for r in rows if r.field_key == "line.amount"]) == 2


class OneLineStubLLM:
    settings = SimpleNamespace(max_tokens=32000)

    def extraction_model(self):
        return "vision_sonya"

    async def complete_json_validated(
        self, prompt, schema_name, schema, validator, image_png=None, max_attempts=3,
        max_tokens=None, model_override=None,
    ):
        if schema_name == "classification":
            return validator({"doc_type": "COMMERCIAL_INVOICE", "confidence": 0.9})
        return validator(
            {
                "number_locale": "period_decimal",
                "parties": [],
                "line_items": [
                    {
                        "description": {"value": "TAEDA PINE KD LUMBER"},
                        "quantity": {"value": "46.46"},
                        "unit": {"value": "CBM"},
                        "unit_price": {"value": "250"},
                        "amount": {"value": "11615"},
                    }
                ],
            }
        )


async def test_two_sections_each_with_one_line_keep_distinct_row_identity(tmp_path: Path):
    from app.services.extract import ExtractionService

    doc = fitz.open()
    for index in range(2):
        page = doc.new_page()
        page.insert_text((72, 72), f"INVOICE PAGE {index + 1}\n" * 8)
    pdf = tmp_path / "two-pages.pdf"
    doc.save(pdf)
    doc.close()
    parsed = parse_document(pdf)
    sections = [
        {"page_start": 0, "page_end": 0, "section_type": "COMMERCIAL_INVOICE", "pages": [0]},
        {"page_start": 1, "page_end": 1, "section_type": "COMMERCIAL_INVOICE", "pages": [1]},
    ]
    rows = await ExtractionService(OneLineStubLLM()).extract_sections(
        "two-pages.pdf", pdf, parsed["pages"], sections, "COMMERCIAL_INVOICE"
    )
    amounts = [row for row in rows if row.field_key == "line.amount"]
    assert len(amounts) == 2, amounts
    assert len({row.line_index for row in amounts}) == 2, "two source rows must not collapse into one"


class DeclarationOnlyStubLLM:
    settings = SimpleNamespace(max_tokens=32000)

    def extraction_model(self):
        return "vision_sonya"

    async def complete_json_validated(
        self, prompt, schema_name, schema, validator, image_png=None, max_attempts=3,
        max_tokens=None, model_override=None,
    ):
        return validator({"declaration": {"declaration_no": {"value": "108299395961"}}})


async def test_declaration_section_renders_its_own_page_not_page_zero(tmp_path, monkeypatch):
    """A scanned declaration that is not page 1 must be read from its own section."""
    from app.services import extract as extract_module
    from app.services.extract import ExtractionService

    doc = fitz.open()
    for _ in range(3):
        page = doc.new_page()
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40))
        pixmap.set_rect(pixmap.irect, (180, 180, 180))
        page.insert_image(page.rect, pixmap=pixmap)
    pdf = tmp_path / "scans.pdf"
    doc.save(pdf)
    doc.close()

    rendered: list[int] = []

    def fake_render(path, index, **kwargs):
        rendered.append(index)
        return b"\x89PNG\r\n\x1a\nfake"

    monkeypatch.setattr(extract_module, "render_pdf_page_png", fake_render)
    rows = await ExtractionService(DeclarationOnlyStubLLM())._extract_declaration(
        "CUSTOMS_DECLARATION_SCAN", "scans.pdf", pdf, ["", "", ""], None, page_indexes=[2]
    )
    assert rendered == [2]
    assert all(row.page == 3 for row in rows)


class BothPassesStubLLM:
    settings = SimpleNamespace(max_tokens=32000)

    def extraction_model(self):
        return "vision_sonya"

    async def complete_json_validated(
        self, prompt, schema_name, schema, validator, image_png=None, max_attempts=3,
        max_tokens=None, model_override=None,
    ):
        if schema_name == "classification":
            return validator({"doc_type": "COMMERCIAL_INVOICE", "confidence": 0.9})
        image_calls = getattr(self, "image_calls", 0)
        if image_png is not None:
            self.image_calls = image_calls + 1
        return validator(
            {
                "number_locale": "period_decimal",
                "parties": [],
                "line_items": [
                    {
                        "description": {"value": "TAEDA PINE KD LUMBER"},
                        "quantity": {"value": "46.46"},
                        "unit": {"value": "CBM"},
                        "unit_price": {"value": "250"},
                        "amount": {"value": "11615"},
                    }
                ],
            }
        )


async def test_same_row_from_text_and_vision_is_not_duplicated(tmp_path: Path):
    from app.services.extract import ExtractionService

    pdf = _text_plus_image_pdf(tmp_path / "both.pdf")
    parsed = parse_document(pdf)
    stub = BothPassesStubLLM()
    rows = await ExtractionService(stub).extract(
        "COMMERCIAL_INVOICE", "both.pdf", pdf, parsed["pages"], None
    )
    assert stub.image_calls >= 1
    amounts = [row for row in rows if row.field_key == "line.amount"]
    assert len(amounts) == 1, f"one physical row, got {len(amounts)}"


class MixedBundleStubLLM:
    """One invoice row in the invoice section, one declared goods line in the declaration."""

    settings = SimpleNamespace(max_tokens=32000)

    def extraction_model(self):
        return "vision_sonya"

    async def complete_json_validated(
        self, prompt, schema_name, schema, validator, image_png=None, max_attempts=3,
        max_tokens=None, model_override=None,
    ):
        if schema_name == "classification":
            return validator({"doc_type": "COMMERCIAL_INVOICE", "confidence": 0.9})
        if "Tờ khai hải quan nhập khẩu" in prompt:
            return validator(
                {
                    "declaration": {
                        "declaration_no": {"value": "108299395961", "source_quote": "108299395961"}
                    },
                    "goods": [
                        {
                            "hs_code": {"value": "44071110"},
                            "description": {"value": "DECLARED GOODS"},
                            "quantity_1": {"value": "46.46"},
                            "taxes": [{"name": {"value": "VAT"}, "amount": {"value": "1000"}}],
                        }
                    ],
                    "taxes": [],
                }
            )
        return validator(
            {
                "number_locale": "period_decimal",
                "parties": [],
                "line_items": [
                    {
                        "description": {"value": "INVOICE ITEM"},
                        "quantity": {"value": "46.46"},
                        "unit": {"value": "CBM"},
                        "unit_price": {"value": "250"},
                        "amount": {"value": "11615"},
                    }
                ],
            }
        )


async def test_invoice_and_declaration_sections_get_separate_indexes(tmp_path: Path):
    from app.services.extract import ExtractionService

    doc = fitz.open()
    for text in ("COMMERCIAL INVOICE\n" * 6, "TO KHAI HAI QUAN\n" * 6):
        page = doc.new_page()
        page.insert_text((72, 72), text)
    pdf = tmp_path / "bundle.pdf"
    doc.save(pdf)
    doc.close()
    parsed = parse_document(pdf)
    sections = [
        {"page_start": 0, "page_end": 0, "section_type": "COMMERCIAL_INVOICE", "pages": [0]},
        {"page_start": 1, "page_end": 1, "section_type": "CUSTOMS_DECLARATION_SCAN", "pages": [1]},
    ]
    rows = await ExtractionService(MixedBundleStubLLM()).extract_sections(
        "bundle.pdf", pdf, parsed["pages"], sections, "COMMERCIAL_INVOICE"
    )
    line_amount = next(r for r in rows if r.field_key == "line.amount")
    goods_desc = next(r for r in rows if r.field_key == "goods.description")
    goods_tax = next(r for r in rows if r.field_key == "goods_tax.name")
    assert line_amount.line_index < 1000
    assert goods_desc.line_index >= 1000
    assert goods_tax.line_index >= 100000


class QuotedDeclarationStubLLM:
    settings = SimpleNamespace(max_tokens=32000)

    def extraction_model(self):
        return "vision_sonya"

    async def complete_json_validated(
        self, prompt, schema_name, schema, validator, image_png=None, max_attempts=3,
        max_tokens=None, model_override=None,
    ):
        return validator(
            {
                "declaration": {
                    "declaration_no": {"value": "108299395961", "source_quote": "108299395961"}
                }
            }
        )


async def test_declaration_text_grounding_uses_the_section_page(tmp_path: Path):
    """Absolute page attribution must not break quote grounding on a local subsection."""
    from app.services.extract import ExtractionService

    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    page = doc.new_page()
    page.insert_text((72, 72), "SO TO KHAI 108299395961\n" * 6)
    pdf = tmp_path / "three.pdf"
    doc.save(pdf)
    doc.close()

    rows = await ExtractionService(QuotedDeclarationStubLLM())._extract_declaration(
        "CUSTOMS_DECLARATION_SCAN",
        "three.pdf",
        pdf,
        ["", "", "SO TO KHAI 108299395961\n" * 6],
        None,
        page_indexes=[2],
    )
    row = next(r for r in rows if r.field_key == "declaration.declaration_no")
    assert row.page == 3
    assert row.grounding_verified is True
    assert row.evidence_state == "FACT_VERIFIED"


class TpvOnlyStubLLM:
    """Returns one row only for the TPV page content (text and vision alike)."""

    settings = SimpleNamespace(max_tokens=32000)

    def extraction_model(self):
        return "vision_sonya"

    async def complete_json_validated(
        self, prompt, schema_name, schema, validator, image_png=None, max_attempts=3,
        max_tokens=None, model_override=None,
    ):
        if schema_name == "classification":
            return validator({"doc_type": "COMMERCIAL_INVOICE", "confidence": 0.9})
        if "INVOICE TABLE PAGE" not in prompt and image_png is None:
            return validator({"number_locale": "period_decimal", "parties": [], "line_items": []})
        return validator(
            {
                "number_locale": "period_decimal",
                "parties": [],
                "line_items": [
                    {
                        "description": {"value": "TAEDA PINE KD LUMBER"},
                        "quantity": {"value": "46.46"},
                        "unit": {"value": "CBM"},
                        "unit_price": {"value": "250"},
                        "amount": {"value": "11615"},
                    }
                ],
            }
        )


async def test_tpv_page_after_a_text_page_keeps_its_own_row_identity(tmp_path: Path):
    """The joined text pass used to label later TEXT_PLUS_VISION rows with page 1."""
    from app.services.extract import ExtractionService

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "COMMERCIAL INVOICE\n" * 8)
    scan = doc.new_page()
    scan.insert_text((72, 72), "INVOICE TABLE PAGE\n" * 4)
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 68, 68))
    pixmap.set_rect(pixmap.irect, (200, 200, 200))
    scan.insert_image(scan.rect, pixmap=pixmap)
    pdf = tmp_path / "mixed.pdf"
    doc.save(pdf)
    doc.close()

    parsed = parse_document(pdf)
    manifests = build_page_manifests(pdf, parsed["pages"])
    assert manifests[0].strategy == STRATEGY_TEXT
    assert manifests[1].strategy == STRATEGY_TEXT_PLUS_VISION

    rows = await ExtractionService(TpvOnlyStubLLM()).extract(
        "COMMERCIAL_INVOICE", "mixed.pdf", pdf, parsed["pages"], None
    )
    amounts = [row for row in rows if row.field_key == "line.amount"]
    assert len(amounts) == 1, f"one physical row on the TPV page, got {len(amounts)}"
    assert amounts[0].page == 2, "the row lives on the TEXT_PLUS_VISION page"
