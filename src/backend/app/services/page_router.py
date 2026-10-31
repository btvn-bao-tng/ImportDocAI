from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz

STRATEGY_TEXT = "TEXT"
STRATEGY_VISION = "VISION"
STRATEGY_TEXT_PLUS_VISION = "TEXT_PLUS_VISION"

SECTION_UNKNOWN = "UNKNOWN"
SECTION_MIXED = "MIXED_BUNDLE"

MIN_USEFUL_TEXT_CHARS = 120
MIN_USEFUL_TEXT_LINES = 4
MIN_USEFUL_WORDS = 12
MAX_TEXT_CHARS_PER_PAGE = 24000
IMAGE_PAGE_MAX_CHARS = 40  # a page number or watermark, not real content


@dataclass
class PageManifest:
    index: int
    text: str
    text_chars: int
    text_quality: float
    has_images: bool
    image_count: int
    rotation: int
    strategy: str
    reason: str

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "text_chars": self.text_chars,
            "text_quality": round(self.text_quality, 3),
            "has_images": self.has_images,
            "image_count": self.image_count,
            "rotation": self.rotation,
            "strategy": self.strategy,
            "reason": self.reason,
        }


@dataclass
class DocumentSection:
    page_start: int
    page_end: int
    section_type: str
    classification_state: str
    classification_evidence: str
    text_chars: int = 0
    pages: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "page_start": self.page_start,
            "page_end": self.page_end,
            "section_type": self.section_type,
            "classification_state": self.classification_state,
            "classification_evidence": self.classification_evidence,
            "text_chars": self.text_chars,
            "pages": self.pages,
        }


def _text_quality(text: str) -> float:
    stripped = (text or "").strip()
    if not stripped:
        return 0.0
    lines = [line for line in stripped.splitlines() if line.strip()]
    words = stripped.split()
    score = 0.0
    if len(stripped) >= MIN_USEFUL_TEXT_CHARS:
        score += 0.5
    elif len(stripped) > 0:
        score += 0.5 * min(1.0, len(stripped) / MIN_USEFUL_TEXT_CHARS)
    if len(lines) >= MIN_USEFUL_TEXT_LINES:
        score += 0.25
    if len(words) >= MIN_USEFUL_WORDS:
        score += 0.25
    return min(1.0, score)


def _page_image_stats(page) -> tuple[bool, int]:
    try:
        images = page.get_images(full=True)
        count = len(images)
    except Exception:
        count = 0
    return count > 0, count


def build_page_manifests(path: Path, pages: list[str]) -> list[PageManifest]:
    manifests: list[PageManifest] = []
    doc = None
    try:
        doc = fitz.open(path)
    except Exception:
        doc = None
    try:
        for index, text in enumerate(pages):
            has_images, image_count = False, 0
            rotation = 0
            if doc is not None and index < doc.page_count:
                page = doc[index]
                rotation = int(page.rotation or 0)
                has_images, image_count = _page_image_stats(page)
            quality = _text_quality(text)
            strategy, reason = _choose_strategy(len((text or "").strip()), quality, has_images)
            manifests.append(
                PageManifest(
                    index=index,
                    text=text or "",
                    text_chars=len((text or "").strip()),
                    text_quality=quality,
                    has_images=has_images,
                    image_count=image_count,
                    rotation=rotation,
                    strategy=strategy,
                    reason=reason,
                )
            )
    finally:
        if doc is not None:
            doc.close()
    if not manifests:
        manifests.append(
            PageManifest(
                index=0,
                text="",
                text_chars=0,
                text_quality=0.0,
                has_images=True,
                image_count=0,
                rotation=0,
                strategy=STRATEGY_VISION,
                reason="không có lớp văn bản",
            )
        )
    return manifests


def _choose_strategy(text_chars: int, quality: float, has_images: bool) -> tuple[str, str]:
    """A page is routed by what the page still needs, not by whether the file has any text.

    A non-empty text layer is not proof of coverage: a cover page may hold only two
    words (`has_text_layer` used to be True for the whole file), and a text page that
    also embeds a scanned table still needs the image to be read.
    """
    if text_chars == 0:
        return STRATEGY_VISION, "trang không có lớp văn bản"
    if has_images and text_chars <= IMAGE_PAGE_MAX_CHARS:
        return STRATEGY_VISION, "lớp văn bản chỉ là số trang/watermark"
    if has_images:
        # Any real text plus an embedded scan keeps both sources: the text may hold the
        # invoice number while the image holds the table.
        return STRATEGY_TEXT_PLUS_VISION, "có lớp văn bản và ảnh/bảng quét"
    if quality < 0.5 or text_chars < MIN_USEFUL_TEXT_CHARS:
        return STRATEGY_VISION, "văn bản quá thưa để đọc một mình và không có ảnh thay thế"
    return STRATEGY_TEXT, "lớp văn bản đầy đủ"


def page_text_for_prompt(manifest: PageManifest, budget: int = MAX_TEXT_CHARS_PER_PAGE) -> str:
    text = manifest.text or ""
    if len(text) <= budget:
        return text
    return text[:budget]


def group_sections(manifests: list[PageManifest], page_types: list[str | None]) -> list[DocumentSection]:
    """Group adjacent pages of the same logical document.

    `page_types` holds one type per page (already normalised) or None when the page
    could not be classified. Contiguous runs of the same non-null type become one
    section. A page whose type differs from both neighbours, or an UNKNOWN run that
    is surrounded by different types, stays its own UNKNOWN section, and the document
    is reported as MIXED_BUNDLE so the user can correct the boundary.
    """
    if not manifests:
        return []
    sections: list[DocumentSection] = []
    current: DocumentSection | None = None
    for manifest in manifests:
        page_type = (page_types[manifest.index] if manifest.index < len(page_types) else None) or SECTION_UNKNOWN
        if current is not None and current.section_type == page_type and page_type != SECTION_UNKNOWN:
            current.page_end = manifest.index
            current.pages.append(manifest.index)
            current.text_chars += manifest.text_chars
            continue
        current = DocumentSection(
            page_start=manifest.index,
            page_end=manifest.index,
            section_type=page_type,
            classification_state="CLASSIFIED" if page_type != SECTION_UNKNOWN else SECTION_UNKNOWN,
            classification_evidence="",
            text_chars=manifest.text_chars,
            pages=[manifest.index],
        )
        sections.append(current)
    types = {s.section_type for s in sections if s.section_type != SECTION_UNKNOWN}
    if len(types) > 1:
        for section in sections:
            if section.classification_state == SECTION_UNKNOWN:
                section.classification_state = SECTION_MIXED
    return sections


def sections_need_vision(section: DocumentSection, manifests: list[PageManifest]) -> list[int]:
    pages = []
    for index in section.pages:
        if index < len(manifests) and manifests[index].strategy != STRATEGY_TEXT:
            pages.append(index)
    return pages
