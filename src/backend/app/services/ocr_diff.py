from __future__ import annotations

import asyncio
import re
from pathlib import Path

from ..models import Document
from .constants import EXCEL_EXT, PDF_EXT
from .ingest import parse_document


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+", (text or "").lower())


def difference_between(pages_a: list[str], pages_b: list[str], limit: int = 60) -> str | None:
    """Describe the first meaningful divergence between two texts (used for version evidence)."""
    cap = limit * 20
    tokens_a = _tokens(" ".join(pages_a))
    tokens_b = _tokens(" ".join(pages_b))
    a, b = tokens_a[:cap], tokens_b[:cap]
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            context = " ".join(b[max(0, i - 3) : i + 4])
            return f"từ/bộ số khác nhau: '{a[i]}' ≠ '{b[i]}' quanh “{context}”"
    if len(a) != len(b):
        return f"độ dài khác nhau: {len(a)} vs {len(b)} token"
    if len(tokens_a) > cap or len(tokens_b) > cap:
        # The comparison was capped: a difference past the compared head is
        # invisible here, and callers treat None as "no difference at all"
        # (duplicate confidence HIGH), so this must never be reported as equal.
        return "giống nhau trong phần đầu đã so sánh (giới hạn so sánh)"
    return None


async def first_difference(doc: Document, other: Document) -> str | None:
    try:
        pa, pb = await asyncio.gather(
            asyncio.to_thread(parse_document, Path(doc.storage_path)),
            asyncio.to_thread(parse_document, Path(other.storage_path)),
        )
    except Exception:
        return None
    if pa["ext"] in PDF_EXT and pb["ext"] in PDF_EXT:
        return difference_between(pa["pages"], pb["pages"])
    if pa["ext"] in EXCEL_EXT and pb["ext"] in EXCEL_EXT:
        flat_a = [" ".join(str(c) for c in row if c not in (None, "")) for row in (pa["grid"]["sheets"][0]["rows"] or [])]
        flat_b = [" ".join(str(c) for c in row if c not in (None, "")) for row in (pb["grid"]["sheets"][0]["rows"] or [])]
        return difference_between(flat_a, flat_b)
    return None
