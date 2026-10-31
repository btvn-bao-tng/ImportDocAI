from __future__ import annotations

from pathlib import Path

import pytest

from app.services.constants import IngestError
from app.services.ingest import (
    compute_checksum,
    normalize_doc_type,
    parse_document,
    validate_upload,
)

from .conftest import SAMPLE_INPUT


def test_checksum_is_stable_and_distinct():
    a = compute_checksum(b"hello")
    b = compute_checksum(b"hello")
    c = compute_checksum(b"hello ")
    assert a == b
    assert a != c
    assert len(a) == 64


def test_validate_upload_rejects_unsupported_and_empty():
    with pytest.raises(IngestError):
        validate_upload("notes.txt", b"data")
    with pytest.raises(IngestError):
        validate_upload("scan.pdf", b"")
    assert validate_upload("scan.PDF", b"%PDF-1.4") == ".pdf"


def test_parse_text_pdf_has_text_layer():
    parsed = parse_document(SAMPLE_INPUT / "bill.pdf")
    assert parsed["page_count"] == 1
    assert parsed["has_text_layer"] is True
    assert "YMJAI245332642" in parsed["pages"][0]


def test_parse_scanned_pdf_has_no_text_layer():
    """CONTRACT DALIAN CHUNLING is a scan: it must be routed to the vision path."""
    parsed = parse_document(SAMPLE_INPUT / "CONTRACT DALIAN CHUNLING LS2628.pdf")
    assert parsed["page_count"] == 1
    assert parsed["has_text_layer"] is False


def test_parse_arrival_notice_text_layer():
    parsed = parse_document(SAMPLE_INPUT / "I2453326421.PDF")
    assert parsed["page_count"] == 2
    assert parsed["has_text_layer"] is True
    joined = " ".join(parsed["pages"])
    assert "YMJAI245332642" in joined


def test_render_pdf_page_png_produces_png():
    from app.services.ingest import render_pdf_page_png

    png = render_pdf_page_png(SAMPLE_INPUT / "CONTRACT DALIAN CHUNLING LS2628.pdf", 0, dpi=100)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 1000


def test_parse_multi_page_pdf():
    parsed = parse_document(SAMPLE_INPUT / "保单.pdf")
    assert parsed["page_count"] == 10


def test_parse_legacy_xls():
    parsed = parse_document(SAMPLE_INPUT / "箱单发票.xls")
    assert parsed["grid"] is not None
    sheets = parsed["grid"]["sheets"]
    assert sheets[0]["name"] == "箱单&发票"
    flat = " ".join(str(c) for c in sheets[0]["rows"][0])
    assert "COMMERCIAL INVOICE" in flat.upper()


def test_parse_xlsx():
    parsed = parse_document(SAMPLE_INPUT / "春岭对外销售合同LS2628（修改收货人）.xlsx")
    assert parsed["grid"] is not None


def test_parse_corrupt_pdf_raises(tmp_path: Path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 not really a pdf")
    with pytest.raises(IngestError):
        parse_document(broken)


def test_doc_type_aliases():
    assert normalize_doc_type("Purchase Order") == "SALES_CONTRACT_PO"
    assert normalize_doc_type("INVOICE") == "COMMERCIAL_INVOICE"
    assert normalize_doc_type("certificate-of-origin") == "CERTIFICATE_OF_ORIGIN"
    assert normalize_doc_type("something odd") == "OTHER"
    assert normalize_doc_type(None) == "OTHER"


def test_grid_content_is_usable_for_classification():
    from app.services.extract import _grid_content

    parsed = parse_document(SAMPLE_INPUT / "箱单发票.xls")
    content = _grid_content(parsed["grid"])
    assert "COMMERCIAL INVOICE" in content
    assert "箱单&发票" in content
