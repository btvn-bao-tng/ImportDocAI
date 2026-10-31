from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record_audit
from ..models import ExtractedField, Job, LineItem, Party, Document, DocumentParty, Shipment
from .constants import IngestError
from .duplicates import detect_duplicates
from .extract import ExtractionService
from .ingest import normalize_doc_type, parse_document
from .llm import LLMClient, LLMError

log = logging.getLogger("importdoc.pipeline")

# Field families that never own a review-store LineItem: their rows are either
# declaration-level child tables (license/instruction/adjustment) or attach to the
# goods line (goods_tax uses the compound line_index below).
NO_LINE_ITEM_PREFIXES = ("goods_tax.", "license.", "instruction.", "adjustment.")

# goods_tax.* rows encode their position as goods_seq * 100 + tax_slot so one flat
# line_index column can address "tax slot k of goods line n". Decoded identically in
# _persist_declaration and _persist; extract._extract_declaration builds it.
GOODS_TAX_ENCODING = 100

async def process_document(
    session: AsyncSession,
    llm: LLMClient,
    document_id: int,
    force: bool = False,
    on_stage: Callable[[int, str], Awaitable[None]] | None = None,
) -> None:
    async def stage(name: str) -> None:
        if on_stage is not None:
            await on_stage(document_id, name)

    service = ExtractionService(llm)
    doc = await session.get(Document, document_id)
    if doc is None:
        return
    if doc.status in {"EXTRACTED"} and not force:
        return
    if doc.status in {"IS_DUPLICATE_OF", "SUPERSEDED_BY"} and not force:
        return

    path = Path(doc.storage_path)
    doc.status = "PROCESSING"
    await session.commit()

    try:
        await stage("PARSING")
        # pdfplumber/openpyxl are synchronous and CPU-bound: run them in a worker thread
        # so a large file does not stall every other document in the same job.
        parsed = await asyncio.to_thread(parse_document, path)
        doc.page_count = parsed["page_count"]
        doc.has_text_layer = bool(parsed["has_text_layer"])

        # Re-classify when the only thing we have is the OTHER fallback or a user override is absent.
        needs_classification = not doc.doc_type or doc.doc_type_source is None or (
            doc.doc_type_source == "AI" and doc.doc_type in {None, "", "OTHER"}
        )
        if needs_classification:
            await stage("CLASSIFYING")
            classification = await service.classify(
                doc.filename, path, doc.has_text_layer, parsed["pages"], parsed["grid"]
            )
            doc.doc_type = normalize_doc_type(classification.doc_type)
            doc.doc_type_source = "AI"
            doc.classification_confidence = classification.confidence
        else:
            doc.doc_type = normalize_doc_type(doc.doc_type)

        await stage("EXTRACTING")
        rows = await service.extract(
            doc.doc_type, doc.filename, path, parsed["pages"], parsed["grid"]
        )

        await stage("PERSISTING")
        await _clear_extraction(session, doc)
        await _persist(session, doc, rows)
        await _persist_declaration(session, doc, rows)
        doc.status = "EXTRACTED"
        doc.error = None
        record_audit(
            session,
            "document",
            doc.id,
            "EXTRACTED",
            payload={"doc_type": doc.doc_type, "fields": len([r for r in rows if r.line_index is None])},
        )
        await detect_duplicates(session, doc)
        await session.commit()
    except (IngestError, LLMError) as exc:
        await session.rollback()
        doc = await session.get(Document, document_id)
        if doc is not None:
            doc.status = "FAILED"
            doc.error = str(exc)
            record_audit(session, "document", doc.id, "EXTRACT_FAILED", payload={"error": str(exc)})
            await session.commit()
        log.warning("document %s failed: %s", document_id, exc)
    except Exception as exc:  # unexpected
        await session.rollback()
        doc = await session.get(Document, document_id)
        if doc is not None:
            doc.status = "FAILED"
            doc.error = f"Lỗi không mong đợi: {exc}"
            record_audit(session, "document", doc.id, "EXTRACT_FAILED", payload={"error": str(exc)})
            await session.commit()
        log.exception("document %s crashed", document_id)


async def _persist_declaration(session: AsyncSession, doc: Document, rows) -> None:
    """Mirror declaration/goods/tax fields into the Tờ khai tables.

    Only runs for customs-declaration documents; every other family is untouched.
    Values are stored exactly as extracted (strings), never re-formatted here.
    """
    if doc.doc_type not in {"CUSTOMS_DECLARATION", "CUSTOMS_DECLARATION_SCAN"}:
        return
    from .declaration import upsert_declaration

    header: dict[str, str | None] = {}
    goods: dict[int, dict[str, str | None]] = {}
    taxes: dict[int, dict[str, str | None]] = {}
    goods_taxes: dict[int, dict[int, dict[str, str | None]]] = {}
    licenses: dict[int, dict[str, str | None]] = {}
    instructions: dict[int, dict[str, str | None]] = {}
    adjustments: dict[int, dict[str, str | None]] = {}
    for row in rows:
        key = row.field_key
        if key.startswith("declaration."):
            header[key] = row.normalized_value
        elif key.startswith("goods_tax."):
            goods_seq = (row.line_index or 0) // GOODS_TAX_ENCODING
            slot = (row.line_index or 0) % GOODS_TAX_ENCODING
            goods_taxes.setdefault(goods_seq, {}).setdefault(slot, {})[key] = row.normalized_value
        elif key.startswith("goods."):
            goods.setdefault(row.line_index or 1, {})[key] = row.normalized_value
        elif key.startswith("tax."):
            taxes.setdefault(row.line_index or 1, {})[key] = row.normalized_value
        elif key.startswith("license."):
            licenses.setdefault(row.line_index or 1, {})[key] = row.normalized_value
        elif key.startswith("instruction."):
            instructions.setdefault(row.line_index or 1, {})[key] = row.normalized_value
        elif key.startswith("adjustment."):
            adjustments.setdefault(row.line_index or 1, {})[key] = row.normalized_value
    if not (header or goods or taxes or goods_taxes or licenses or instructions or adjustments):
        return
    await upsert_declaration(
        session,
        doc.shipment_id,
        header=header,
        goods=[goods[k] for k in sorted(goods)],
        taxes=[taxes[k] for k in sorted(taxes)],
        goods_taxes={seq: [slots[k] for k in sorted(slots)] for seq, slots in goods_taxes.items()},
        licenses=[licenses[k] for k in sorted(licenses)],
        instructions=[instructions[k] for k in sorted(instructions)],
        adjustments=[adjustments[k] for k in sorted(adjustments)],
        document_id=doc.id,
    )


async def _clear_extraction(session: AsyncSession, doc: Document) -> None:
    existing_fields = (await session.execute(select(ExtractedField).where(ExtractedField.document_id == doc.id))).scalars().all()
    for f in existing_fields:
        await session.delete(f)
    existing_items = (await session.execute(select(LineItem).where(LineItem.document_id == doc.id))).scalars().all()
    for item in existing_items:
        await session.delete(item)
    links = (await session.execute(select(DocumentParty).where(DocumentParty.document_id == doc.id))).scalars().all()
    for link in links:
        await session.delete(link)
    await session.flush()


async def _persist(session: AsyncSession, doc: Document, rows) -> None:
    line_ids: dict[int, LineItem] = {}
    for row in rows:
        if row.line_index is None or row.field_key.startswith(NO_LINE_ITEM_PREFIXES):
            continue
        if row.line_index not in line_ids and row.field_key == "line.description":
            item = LineItem(document_id=doc.id, seq=row.line_index, raw_description=row.raw_value, description=row.normalized_value)
            session.add(item)
            line_ids[row.line_index] = item
    for row in rows:
        if row.line_index is None or row.field_key.startswith(NO_LINE_ITEM_PREFIXES):
            continue
        if row.line_index not in line_ids:
            item = LineItem(document_id=doc.id, seq=row.line_index)
            session.add(item)
            line_ids[row.line_index] = item
    await session.flush()

    for row in rows:
        item = None
        if row.line_index is not None:
            if row.field_key.startswith("goods_tax."):
                item = line_ids.get(row.line_index // GOODS_TAX_ENCODING)
            elif not row.field_key.startswith(NO_LINE_ITEM_PREFIXES):
                item = line_ids.get(row.line_index)
        if item is not None and row.field_key.split(".", 1)[0] in {"line", "goods"}:
            # Only the review-store families mirror onto LineItem columns; tax families
            # (tax.*, goods_tax.*) must never clobber quantity/amount of the goods line.
            attr = row.field_key.split(".", 1)[1]
            if attr == "no":
                item.seq = int(row.normalized_value) if (row.normalized_value or "").isdigit() else item.seq
            elif attr == "description":
                item.description = row.normalized_value
                item.raw_description = row.raw_value
            elif attr == "quantity":
                item.quantity = _dec(row.normalized_value)
                item.unit = row.unit or item.unit
            elif attr == "unit_price":
                item.unit_price = _dec(row.normalized_value)
            elif attr == "amount":
                item.amount = _dec(row.normalized_value)
            elif attr == "unit":
                item.unit = row.normalized_value
            elif attr == "spec":
                item.spec = row.normalized_value
        session.add(
            ExtractedField(
                document_id=doc.id,
                line_item_id=item.id if item is not None else None,
                field_key=row.field_key,
                raw_value=row.raw_value,
                normalized_value=row.normalized_value,
                unit=row.unit,
                page=row.page,
                bbox=row.bbox,
                cell_ref=row.cell_ref,
                confidence=row.confidence,
                provenance_type=row.provenance_type,
                grounding_verified=row.grounding_verified,
                review_status=row.review_status,
                critical=row.critical,
            )
        )
    await session.flush()


def _dec(value: str | None):
    from decimal import Decimal, InvalidOperation

    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


async def upsert_parties_and_transactions(session: AsyncSession, shipment_id: int) -> None:
    """Build party/transaction records from extracted party fields.

    Parties are never merged by fuzzy name; identical raw names (whitespace-normalized)
    reuse one record within a shipment.
    """
    docs = (await session.execute(select(Document).where(Document.shipment_id == shipment_id))).scalars().all()
    seen_names: dict[str, Party] = {}
    for doc in docs:
        fields = (await session.execute(select(ExtractedField).where(ExtractedField.document_id == doc.id))).scalars().all()
        for f in fields:
            if not f.field_key.startswith("party."):
                continue
            role = f.field_key.split(".", 1)[1].upper()
            name = (f.normalized_value or "").strip()
            if not name:
                continue
            party = seen_names.get(name.lower())
            if party is None:
                party = Party(shipment_id=shipment_id, raw_name=name)
                session.add(party)
                await session.flush()
                seen_names[name.lower()] = party
            exists = (
                await session.execute(
                    select(DocumentParty).where(
                        DocumentParty.document_id == doc.id,
                        DocumentParty.party_id == party.id,
                        DocumentParty.role == role,
                    )
                )
            ).scalars().first()
            if exists is None:
                session.add(DocumentParty(document_id=doc.id, party_id=party.id, role=role))
    await session.commit()
