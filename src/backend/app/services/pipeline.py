from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record_audit
from ..models import DocumentSection, ExtractionRevision, ExtractedField, Job, LineItem, Party, Document, DocumentParty, Shipment
from .constants import IngestError
from .duplicates import detect_duplicates
from .extract import DECLARATION_LINE_OFFSET, ExtractionService
from .flags import legacy_declaration_mirror_enabled
from .ingest import compute_checksum, normalize_doc_type, parse_document
from .page_router import build_page_manifests
from .llm import LLMClient, LLMError

log = logging.getLogger("importdoc.pipeline")

# Files of one job are processed concurrently, but the Tờ khai mirror is shipment-wide
# (one Declaration per shipment, no unique key to lean on). Two declaration files
# finishing together would otherwise both insert one, so the mirror runs under a lock.
_DECLARATION_LOCKS: dict[int, asyncio.Lock] = {}


def _shipment_lock(shipment_id: int) -> asyncio.Lock:
    lock = _DECLARATION_LOCKS.get(shipment_id)
    if lock is None:
        lock = _DECLARATION_LOCKS[shipment_id] = asyncio.Lock()
    return lock


def _release_shipment_lock_if_idle(shipment_id: int) -> None:
    lock = _DECLARATION_LOCKS.get(shipment_id)
    if lock is not None and not lock.locked():
        _DECLARATION_LOCKS.pop(shipment_id, None)


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
        await stage("CLASSIFYING")
        classification = await service.classify_document(
            doc.filename, path, parsed["pages"], parsed["grid"]
        )
        if doc.doc_type_source != "USER" or doc.doc_type in {None, "", "OTHER"}:
            doc.doc_type = normalize_doc_type(classification.doc_type)
            doc.doc_type_source = "AI"
            doc.classification_confidence = classification.confidence
        else:
            doc.doc_type = normalize_doc_type(doc.doc_type)
        sections = classification.sections or await _sections_for_document(doc, path, parsed, doc.doc_type)
        await _replace_sections(session, doc, sections)
        # The section/summary writes above opened this session's SQLite write
        # transaction. Commit it NOW: the extraction phase below awaits LLM calls that
        # can run for minutes, and holding the single writer lock that long makes every
        # sibling document (and its job_document_stages update) fail with
        # "database is locked" once busy_timeout expires.
        await session.commit()

        await stage("EXTRACTING")
        rows = await service.extract_sections(
            doc.filename, path, parsed["pages"], sections, doc.doc_type, parsed["grid"]
        )
        await _record_extraction_revision(session, doc, path, rows)

        await stage("PERSISTING")
        preserved = await _clear_extraction(session, doc)
        await _persist(session, doc, rows, sections=sections, preserved=preserved)
        # Files of one job run in parallel, so the shipment-wide Tờ khai mirror is
        # serialised. Commit first: holding SQLite's write lock while awaiting another
        # coroutine would make the sibling fail with "database is locked".
        await session.commit()
        if legacy_declaration_mirror_enabled():
            try:
                async with _shipment_lock(doc.shipment_id):
                    await _persist_declaration(session, doc, rows)
                    await session.commit()
            finally:
                _release_shipment_lock_if_idle(doc.shipment_id)
        doc.status = "EXTRACTED"
        doc.error = None
        record_audit(
            session,
            "document",
            doc.id,
            "EXTRACTED",
            payload={"doc_type": doc.doc_type, "fields": len([r for r in rows if r.line_index is None])},
        )
        # Duplicate detection re-parses both files (CPU seconds) to produce its diff
        # evidence; commit the EXTRACTED state first so that work does not run under
        # this session's write lock.
        await session.commit()
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
    # `upsert_declaration` enumerates goods positionally, so its tax keys must follow
    # the same positions; the in-memory keys can still carry the bundle offset.
    ordered_goods = [goods[k] for k in sorted(goods)]
    position_of = {key: position for position, key in enumerate(sorted(goods), start=1)}
    positional_goods_taxes = {}
    for seq, slots in goods_taxes.items():
        slot_in = slots
        position = position_of.get(seq)
        if position is None:
            # A tax row without its goods header (or vice versa): keep it keyed as-is
            # so it is visible instead of silently dropped.
            positional_goods_taxes[seq] = [slot_in[k] for k in sorted(slot_in)]
            continue
        positional_goods_taxes[position] = [slot_in[k] for k in sorted(slot_in)]
    await upsert_declaration(
        session,
        doc.shipment_id,
        header=header,
        goods=ordered_goods,
        taxes=[taxes[k] for k in sorted(taxes)],
        goods_taxes=positional_goods_taxes,
        licenses=[licenses[k] for k in sorted(licenses)],
        instructions=[instructions[k] for k in sorted(instructions)],
        adjustments=[adjustments[k] for k in sorted(adjustments)],
        document_id=doc.id,
    )


async def _sections_for_document(doc: Document, path: Path, parsed: dict, doc_type: str) -> list[dict]:
    """Fallback: one section covering the file when classification returned none."""
    if parsed["grid"] is not None or not parsed["pages"]:
        return []
    manifests = build_page_manifests(path, parsed["pages"])
    return [
        {
            "page_start": 0,
            "page_end": len(manifests) - 1,
            "section_type": doc_type,
            "classification_state": "CLASSIFIED",
            "classification_evidence": "fallback: toàn bộ file",
            "text_chars": sum(m.text_chars for m in manifests),
            "pages": [m.index for m in manifests],
        }
    ]


async def _replace_sections(session: AsyncSession, doc: Document, sections: list[dict]) -> None:
    from sqlalchemy import update

    from ..models import DeclarationFieldSource

    existing = (
        await session.execute(select(DocumentSection).where(DocumentSection.document_id == doc.id))
    ).scalars().all()
    section_ids = [row.id for row in existing]
    if section_ids:
        # SQLite runs with foreign_keys=ON in production: the new evidence rows from the
        # previous run still point at these sections, so the references must be cleared
        # before the sections can be deleted.
        await session.execute(
            update(ExtractedField).where(ExtractedField.section_id.in_(section_ids)).values(section_id=None)
        )
        await session.execute(
            update(DeclarationFieldSource)
            .where(DeclarationFieldSource.section_id.in_(section_ids))
            .values(section_id=None)
        )
    for row in existing:
        await session.delete(row)
    await session.flush()
    for section in sections:
        session.add(
            DocumentSection(
                document_id=doc.id,
                page_start=int(section.get("page_start") or 0),
                page_end=int(section.get("page_end") or 0),
                section_type=section.get("section_type") or "UNKNOWN",
                classification_state=section.get("classification_state") or "UNKNOWN",
                classification_evidence=section.get("classification_evidence") or "",
                text_chars=int(section.get("text_chars") or 0),
            )
        )
    await session.flush()


async def _record_extraction_revision(session: AsyncSession, doc: Document, path: Path, rows) -> None:
    digest = doc.checksum
    try:
        digest = compute_checksum(path.read_bytes())
    except Exception:
        digest = doc.checksum
    session.add(
        ExtractionRevision(
            document_id=doc.id,
            source_hash=digest,
            prompt_version="extract_v2",
            model_name="vision_sonya",
            schema_version="extraction_v2",
            status="DONE",
        )
    )
    await session.flush()


async def _clear_extraction(session: AsyncSession, doc: Document):
    """Delete source facts for a re-run, but hand back human-reviewed values.

    Re-extraction used to delete every `ExtractedField`, including CONFIRMED/EDITED
    rows, so an approved review disappeared on retry. Reviewed rows are captured here
    and re-applied onto the rebuilt rows in `_persist`.
    """
    existing_fields = (
        await session.execute(select(ExtractedField).where(ExtractedField.document_id == doc.id))
    ).scalars().all()
    preserved = []
    item_seq: dict[int, int] = {}
    for item in (
        await session.execute(select(LineItem).where(LineItem.document_id == doc.id))
    ).scalars().all():
        item_seq[item.id] = item.seq
    for f in existing_fields:
        if f.review_status in {"EDITED", "CONFIRMED"}:
            preserved.append(
                {
                    "field_key": f.field_key,
                    "line_seq": item_seq.get(f.line_item_id) if f.line_item_id is not None else None,
                    "raw_value": f.raw_value,
                    "normalized_value": f.normalized_value,
                    "unit": f.unit,
                    "page": f.page,
                    "bbox": f.bbox,
                    "cell_ref": f.cell_ref,
                    "provenance_type": f.provenance_type,
                    "grounding_verified": f.grounding_verified,
                    "review_status": f.review_status,
                    "critical": f.critical,
                    "section_type": f.section_type,
                    "source_row_index": f.source_row_index,
                    "container_no": f.container_no,
                    "evidence_state": f.evidence_state,
                }
            )

    # Clear every child pointer BEFORE any delete is staged: autoflush would otherwise
    # emit DELETE extracted_fields first and foreign_keys=ON would abort the retry.
    # `field_value_history` rows are written by the review API (edit/confirm), so the
    # fields most likely to be deleted here are exactly the ones with history children.
    field_ids = [f.id for f in existing_fields]
    if field_ids:
        from sqlalchemy import delete as sa_delete
        from sqlalchemy import update

        from ..models import DeclarationFieldSource, FieldValueHistory

        await session.execute(
            update(DeclarationFieldSource)
            .where(DeclarationFieldSource.source_field_id.in_(field_ids))
            .values(source_field_id=None)
        )
        await session.execute(sa_delete(FieldValueHistory).where(FieldValueHistory.field_id.in_(field_ids)))
        await session.flush()
    for f in existing_fields:
        await session.delete(f)
    existing_items = (
        await session.execute(select(LineItem).where(LineItem.document_id == doc.id))
    ).scalars().all()
    for item in existing_items:
        await session.delete(item)
    links = (await session.execute(select(DocumentParty).where(DocumentParty.document_id == doc.id))).scalars().all()
    for link in links:
        await session.delete(link)
    await session.flush()
    return preserved


async def _persist(
    session: AsyncSession,
    doc: Document,
    rows,
    sections: list[dict] | None = None,
    preserved: list[dict] | None = None,
) -> None:
    section_rows = (
        await session.execute(select(DocumentSection).where(DocumentSection.document_id == doc.id))
    ).scalars().all()

    def section_id_for(page: int | None) -> int | None:
        if page is None:
            return None
        for section in section_rows:
            if int(section.page_start) + 1 <= page <= int(section.page_end) + 1:
                return section.id
        return None

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

    preserved = preserved or []
    preserved_by_key: dict[tuple[str, int | None], dict] = {
        (p["field_key"], p["line_seq"]): p for p in preserved
    }
    written: set[tuple[str, int | None]] = set()

    def emit(field_key: str, row, item: LineItem | None, line_seq: int | None) -> None:
        key = (field_key, line_seq)
        review = preserved_by_key.get(key)
        value_raw = row.raw_value if row is not None else None
        value_norm = row.normalized_value if row is not None else None
        unit = row.unit if row is not None else None
        review_status = row.review_status if row is not None else "UNREVIEWED"
        grounding = row.grounding_verified if row is not None else False
        if review is not None:
            # A human decision outranks the fresh extraction; the new value is not lost,
            # it is simply not written over the reviewed one.
            value_raw = review["raw_value"]
            value_norm = review["normalized_value"]
            unit = review["unit"]
            review_status = review["review_status"]
            grounding = review["grounding_verified"]
        session.add(
            ExtractedField(
                document_id=doc.id,
                line_item_id=item.id if item is not None else None,
                field_key=field_key,
                raw_value=value_raw,
                normalized_value=value_norm,
                unit=unit,
                page=row.page if row is not None else (review or {}).get("page"),
                bbox=row.bbox if row is not None else (review or {}).get("bbox"),
                cell_ref=row.cell_ref if row is not None else (review or {}).get("cell_ref"),
                confidence=row.confidence if row is not None else None,
                provenance_type=row.provenance_type if row is not None else (review or {}).get("provenance_type", "TEXT_LAYER"),
                grounding_verified=grounding,
                review_status=review_status,
                critical=row.critical if row is not None else bool((review or {}).get("critical")),
                section_id=section_id_for(row.page if row is not None else (review or {}).get("page")),
                section_type=(row.section_type if row is not None else (review or {}).get("section_type")),
                source_row_index=(row.source_row_index if row is not None else (review or {}).get("source_row_index")),
                container_no=(row.container_no if row is not None else (review or {}).get("container_no")),
                evidence_state=(row.evidence_state if row is not None else (review or {}).get("evidence_state")),
            )
        )
        written.add(key)

    mirrored_attrs = {"no", "description", "quantity", "unit_price", "amount", "unit", "spec", "currency"}
    for row in rows:
        item = None
        line_seq: int | None = None
        if row.line_index is not None:
            if row.field_key.startswith("goods_tax."):
                item = line_ids.get(row.line_index // GOODS_TAX_ENCODING)
            elif not row.field_key.startswith(NO_LINE_ITEM_PREFIXES):
                item = line_ids.get(row.line_index)
            line_seq = row.line_index
        if item is not None and row.field_key.split(".", 1)[0] in {"line", "goods"}:
            # Only the review-store families mirror onto LineItem columns; tax families
            # (tax.*, goods_tax.*) must never clobber quantity/amount of the goods line.
            attr = row.field_key.split(".", 1)[1]
            if attr not in mirrored_attrs:
                pass
            elif attr == "no":
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
            elif attr == "currency":
                item.currency = row.normalized_value
        emit(row.field_key, row, item, line_seq)

    # A reviewed field whose source row no longer exists still belongs to the record:
    # recreate it so a retry cannot silently drop the reviewer's decision.
    seq_to_item = {item.seq: item for item in line_ids.values()}
    for (field_key, line_seq), review in preserved_by_key.items():
        if (field_key, line_seq) in written:
            continue
        item = None
        if line_seq is not None:
            item = seq_to_item.get(line_seq)
            if item is None and line_seq >= DECLARATION_LINE_OFFSET:
                # A re-run may group the file differently (bundle vs single document),
                # shifting declaration rows by the offset; keep the review attached.
                item = seq_to_item.get(line_seq % DECLARATION_LINE_OFFSET)
        emit(field_key, None, item, item.seq if item is not None else line_seq)
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
            # `party.buyer` is a company name; `party.buyer.address` / `.tax_code` /
            # `.phone` / `.country_code` describe that company. Only the name row
            # owns a Party record — attributes must not become companies.
            if len(f.field_key.split(".")) != 2:
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
