from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DOC_TYPES = (
    "COMMERCIAL_INVOICE",
    "PACKING_LIST",
    "TRANSPORT_DOCUMENT",
    "ARRIVAL_NOTICE",
    "SALES_CONTRACT_PO",
    "SERVICE_CONTRACT",
    "EXPORT_DECLARATION",
    "CUSTOMS_DECLARATION",
    "CUSTOMS_DECLARATION_SCAN",
    "CERTIFICATE_OF_ORIGIN",
    "INSURANCE_POLICY",
    "PHYTOSANITARY_CERTIFICATE",
    "CUSTOMS_DECLARATION_SCAN",
    "OTHER",
)

PDF_EXT = {".pdf"}
EXCEL_EXT = {".xls", ".xlsx", ".xlsm"}
IMAGE_EXT = {".jpg", ".jpeg", ".png"}
SUPPORTED_EXT = PDF_EXT | EXCEL_EXT | IMAGE_EXT

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@dataclass
class ParsedDocument:
    ext: str
    page_count: int = 0
    has_text_layer: bool = False
    pages: list[str] = field(default_factory=list)
    grid: dict | None = None


class IngestError(Exception):
    """Raised when a file cannot be ingested (corrupt, encrypted, unsupported)."""


def detect_ext(filename: str) -> str:
    return Path(filename).suffix.lower()
