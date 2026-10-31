from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf as fitz
import pdfplumber
import xlrd
from openpyxl import load_workbook

from .constants import (
    DOC_TYPES,
    EXCEL_EXT,
    IMAGE_EXT,
    MAX_UPLOAD_BYTES,
    PDF_EXT,
    IngestError,
    detect_ext,
)


def compute_checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_upload(filename: str, data: bytes) -> str:
    ext = detect_ext(filename)
    if ext not in PDF_EXT | EXCEL_EXT | IMAGE_EXT:
        raise IngestError(f"Định dạng không hỗ trợ: {ext or '(không rõ)'}")
    if not data:
        raise IngestError("File rỗng")
    if len(data) > MAX_UPLOAD_BYTES:
        raise IngestError("File vượt quá giới hạn 50MB")
    return ext


def _read_pdf(path: Path) -> tuple[int, bool, list[str]]:
    try:
        doc = fitz.open(path)
    except Exception as exc:  # corrupt or unsupported PDF
        raise IngestError(f"Không đọc được PDF: {exc}") from exc
    if doc.needs_pass:
        doc.close()
        raise IngestError("PDF được mã hoá bằng mật khẩu")
    page_count = doc.page_count
    pages: list[str] = []
    has_text = False
    try:
        for i in range(page_count):
            txt = doc[i].get_text("text") or ""
            pages.append(txt)
            if len(txt.strip()) > 30:
                has_text = True
    finally:
        doc.close()
    return page_count, has_text, pages


MAX_IMAGE_BYTES = 1_500_000


def render_pdf_page_png(path: Path, page_index: int, dpi: int = 150, max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
    """Render a page for the vision model, downscaling until the payload is economical."""
    doc = fitz.open(path)
    try:
        if page_index < 0 or page_index >= doc.page_count:
            raise IngestError(f"Trang {page_index} ngoài phạm vi")
        page = doc[page_index]
        for attempt_dpi in (dpi, 110, 90, 72):
            png = page.get_pixmap(dpi=attempt_dpi).tobytes("png")
            if len(png) <= max_bytes or attempt_dpi == 72:
                return png
        return png
    finally:
        doc.close()


def extract_pdf_words(path: Path, page_index: int) -> list[dict]:
    """Word list with bounding boxes for a page, used for provenance."""
    with pdfplumber.open(str(path)) as pdf:
        if page_index < 0 or page_index >= len(pdf.pages):
            return []
        page = pdf.pages[page_index]
        words = page.extract_words(keep_blank_chars=False) or []
        return [
            {"text": w["text"], "x0": w["x0"], "top": w["top"], "x1": w["x1"], "bottom": w["bottom"]}
            for w in words
        ]


def _read_xls(path: Path) -> dict:
    try:
        book = xlrd.open_workbook(str(path))
    except Exception as exc:
        raise IngestError(f"Không đọc được XLS: {exc}") from exc
    sheets = []
    for sheet in book.sheets():
        rows = []
        for r in range(sheet.nrows):
            row = []
            for c in range(sheet.ncols):
                cell = sheet.cell(r, c)
                value = cell.value
                if cell.ctype == xlrd.XL_CELL_NUMBER and float(value).is_integer():
                    value = int(value)
                row.append(value if value != "" else None)
            rows.append(row)
        sheets.append({"name": sheet.name, "rows": rows})
    return {"sheets": sheets}


def _read_xlsx(path: Path) -> dict:
    try:
        wb = load_workbook(str(path), data_only=True)
    except Exception as exc:
        raise IngestError(f"Không đọc được XLSX: {exc}") from exc
    sheets = []
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        sheets.append({"name": ws.title, "rows": rows})
    return {"sheets": sheets}


def parse_document(path: Path) -> dict:
    ext = path.suffix.lower()
    if ext in PDF_EXT:
        page_count, has_text, pages = _read_pdf(path)
        return {"ext": ext, "page_count": page_count, "has_text_layer": has_text, "pages": pages, "grid": None}
    if ext == ".xls":
        grid = _read_xls(path)
        return {"ext": ext, "page_count": 1, "has_text_layer": True, "pages": [], "grid": grid}
    if ext in {".xlsx", ".xlsm"}:
        grid = _read_xlsx(path)
        return {"ext": ext, "page_count": 1, "has_text_layer": True, "pages": [], "grid": grid}
    if ext in IMAGE_EXT:
        try:
            with fitz.open(path) as doc:
                page_count = doc.page_count or 1
        except Exception:
            page_count = 1
        return {"ext": ext, "page_count": page_count, "has_text_layer": False, "pages": [], "grid": None}
    raise IngestError(f"Định dạng không hỗ trợ: {ext}")


def normalize_doc_type(value: str | None) -> str:
    if not value:
        return "OTHER"
    key = value.strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "INVOICE": "COMMERCIAL_INVOICE",
        "PACKING_SPEC": "PACKING_LIST",
        "PACKING_SPECIFICATION": "PACKING_LIST",
        "BILL_OF_LADING": "TRANSPORT_DOCUMENT",
        "WAYBILL": "TRANSPORT_DOCUMENT",
        "B_L": "TRANSPORT_DOCUMENT",
        "PURCHASE_ORDER": "SALES_CONTRACT_PO",
        "PO": "SALES_CONTRACT_PO",
        "CONTRACT": "SALES_CONTRACT_PO",
        "CO": "CERTIFICATE_OF_ORIGIN",
        "FORM_E": "CERTIFICATE_OF_ORIGIN",
        "PHYTO": "PHYTOSANITARY_CERTIFICATE",
        "CUSTOMS_DECLARATION": "CUSTOMS_DECLARATION_SCAN",
        "TO_KHAI": "CUSTOMS_DECLARATION_SCAN",
        "TOKHAI": "CUSTOMS_DECLARATION_SCAN",
        "UNKNOWN": "OTHER",
    }
    key = aliases.get(key, key)
    return key if key in DOC_TYPES else "OTHER"
