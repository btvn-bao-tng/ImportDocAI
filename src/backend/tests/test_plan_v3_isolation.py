from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Base,
    Declaration,
    DeclarationFieldSource,
    Document,
    ExtractedField,
    LineItem,
    ResolvedField,
    Shipment,
)
from app.services import pipeline
from app.services.extract import ExtractedRow
from app.services.flags import legacy_declaration_mirror_enabled
from app.services.schemas import ClassificationOut


def _text_pdf(path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "TO KHAI HAI QUAN SO 108299395961\n" * 5)
    doc.save(path)
    doc.close()
    return path


def _row(key: str, value: str) -> ExtractedRow:
    return ExtractedRow(
        field_key=key,
        raw_value=value,
        normalized_value=value,
        unit=None,
        page=1,
        bbox=None,
        cell_ref=None,
        confidence=None,
        provenance_type="TEXT_LAYER",
        grounding_verified=True,
        review_status="UNREVIEWED",
        critical=True,
        evidence_state="FACT_VERIFIED",
    )


class DeclarationStubService:
    def __init__(self, llm=None):
        pass

    async def classify_document(self, filename, path, pages, grid=None):
        return ClassificationOut(doc_type="CUSTOMS_DECLARATION_SCAN", confidence=0.9, sections=[])

    async def extract_sections(self, filename, path, pages, sections, fallback_doc_type, grid=None):
        return [_row("declaration.declaration_no", "108299395961"), _row("declaration.importer_name", "KINGSMAN")]


@pytest.fixture()
async def db(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'iso.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _seed(maker, tmp_path: Path) -> tuple[int, int]:
    async with maker() as session:
        shipment = Shipment(code="ISO-1", status="PROCESSING")
        session.add(shipment)
        await session.flush()
        doc = Document(
            shipment_id=shipment.id,
            filename="tokhai.pdf",
            file_ext=".pdf",
            storage_path=str(_text_pdf(tmp_path / "tokhai.pdf")),
            checksum="iso-1",
            status="UPLOADED",
        )
        session.add(doc)
        await session.commit()
        return doc.id, shipment.id


async def test_legacy_declaration_mirror_is_off_by_default(db, tmp_path, monkeypatch):
    """A4: the per-document last-write-wins mirror must not create a new-draft declaration."""
    monkeypatch.delenv("IMPORTDOC_LEGACY_DECLARATION_MIRROR", raising=False)
    monkeypatch.setattr(pipeline, "ExtractionService", DeclarationStubService)
    doc_id, _shipment_id = await _seed(db, tmp_path)

    async with db() as session:
        await pipeline.process_document(session, llm=None, document_id=doc_id)
    assert legacy_declaration_mirror_enabled() is False

    async with db() as session:
        assert (await session.execute(select(func.count()).select_from(Declaration))).scalar_one() == 0
        fields = (await session.execute(select(ExtractedField))).scalars().all()
        assert {f.field_key for f in fields} == {"declaration.declaration_no", "declaration.importer_name"}


async def test_legacy_mirror_still_runs_behind_its_flag(db, tmp_path, monkeypatch):
    monkeypatch.setenv("IMPORTDOC_LEGACY_DECLARATION_MIRROR", "1")
    monkeypatch.setattr(pipeline, "ExtractionService", DeclarationStubService)
    doc_id, _shipment_id = await _seed(db, tmp_path)

    async with db() as session:
        await pipeline.process_document(session, llm=None, document_id=doc_id)

    assert legacy_declaration_mirror_enabled() is True
    async with db() as session:
        declaration = (await session.execute(select(Declaration))).scalars().first()
        assert declaration is not None
        assert declaration.declaration_no == "108299395961"


async def test_party_attributes_do_not_become_parties(db):
    """`party.buyer.address` describes a company; it must not be stored as one.

    The parties endpoint lists every Party row, so an address or tax code stored as
    `raw_name` (with a DocumentParty role like BUYER.ADDRESS) pollutes the review UI.
    """
    from app.models import DocumentParty, Party

    async with db() as session:
        shipment = Shipment(code="ISO-PARTY", status="PROCESSING")
        session.add(shipment)
        await session.flush()
        doc = Document(
            shipment_id=shipment.id,
            filename="invoice.pdf",
            file_ext=".pdf",
            storage_path="/tmp/invoice.pdf",
            checksum="party-1",
            doc_type="COMMERCIAL_INVOICE",
            doc_type_source="AI",
            status="EXTRACTED",
            page_count=1,
        )
        session.add(doc)
        await session.flush()
        for key, value in (
            ("party.buyer", "CÔNG TY TNHH GỖ KINGSMAN VIỆT NAM"),
            ("party.buyer.address", "Thửa đất số 334, Phường Tân Uyên, TP.HCM"),
            ("party.buyer.tax_code", "3702474140"),
            ("party.buyer.phone", "0274 3800882"),
            ("party.seller", "BERTEC ENTERPRISE CO., LTD."),
            ("party.seller.country_code", "TW"),
        ):
            session.add(
                ExtractedField(
                    document_id=doc.id,
                    field_key=key,
                    raw_value=value,
                    normalized_value=value,
                    page=1,
                    provenance_type="TEXT_LAYER",
                    grounding_verified=True,
                    review_status="UNREVIEWED",
                )
            )
        await session.commit()
        shipment_id = shipment.id

    async with db() as session:
        await pipeline.upsert_parties_and_transactions(session, shipment_id)

    async with db() as session:
        parties = (await session.execute(select(Party).where(Party.shipment_id == shipment_id))).scalars().all()
        assert {p.raw_name for p in parties} == {
            "CÔNG TY TNHH GỖ KINGSMAN VIỆT NAM",
            "BERTEC ENTERPRISE CO., LTD.",
        }
        links = (await session.execute(select(DocumentParty))).scalars().all()
        assert {l.role for l in links} == {"BUYER", "SELLER"}


async def test_reextraction_preserves_a_confirmed_review(db, tmp_path, monkeypatch):
    """Phase A DoD: retrying must not drop an approved human edit."""
    monkeypatch.delenv("IMPORTDOC_LEGACY_DECLARATION_MIRROR", raising=False)
    monkeypatch.setattr(pipeline, "ExtractionService", DeclarationStubService)
    doc_id, _shipment_id = await _seed(db, tmp_path)

    async with db() as session:
        await pipeline.process_document(session, llm=None, document_id=doc_id)
    async with db() as session:
        field = (
            await session.execute(
                select(ExtractedField).where(ExtractedField.field_key == "declaration.importer_name")
            )
        ).scalars().one()
        field.raw_value = "CÔNG TY ĐÃ SỬA"
        field.normalized_value = "CÔNG TY ĐÃ SỬA"
        field.review_status = "CONFIRMED"
        await session.commit()

    async with db() as session:
        await pipeline.process_document(session, llm=None, document_id=doc_id, force=True)

    async with db() as session:
        fields = (
            await session.execute(
                select(ExtractedField).where(ExtractedField.field_key == "declaration.importer_name")
            )
        ).scalars().all()
        assert len(fields) == 1
        assert fields[0].normalized_value == "CÔNG TY ĐÃ SỬA"
        assert fields[0].review_status == "CONFIRMED"


def test_no_secret_material_in_tracked_source():
    """Operational invariant: no API key literal in source or docs."""
    import re
    import subprocess

    root = Path(__file__).resolve().parents[3]
    tracked = subprocess.run(
        ["git", "ls-files", "src", "docs", "evaluation"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    pattern = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")
    offenders = []
    for name in tracked:
        path = root / name
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if pattern.search(text):
            offenders.append(name)
    assert offenders == []


@pytest.fixture()
async def fk_db(tmp_path: Path):
    """Engine with the production PRAGMA: foreign_keys=ON."""
    from sqlalchemy import event

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fk.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys_on(dbapi_conn, _record):  # pragma: no cover - driver hook
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def test_reprocessing_after_resolution_respects_foreign_keys(fk_db, tmp_path, monkeypatch):
    """Evidence points at sections and fields; a retry must clear those refs, not abort."""
    monkeypatch.delenv("IMPORTDOC_LEGACY_DECLARATION_MIRROR", raising=False)
    monkeypatch.setattr(pipeline, "ExtractionService", DeclarationStubService)
    doc_id, shipment_id = await _seed(fk_db, tmp_path)

    async with fk_db() as session:
        await pipeline.process_document(session, llm=None, document_id=doc_id)
    async with fk_db() as session:
        from app.services.resolution import resolve_shipment

        await resolve_shipment(session, shipment_id, "tokhai_import_v1", None)
        await session.commit()

    # Real field-level evidence pointing at a source fact: this is the FK edge the
    # previous test missed (no candidates meant no DeclarationFieldSource rows).
    async with fk_db() as session:
        field = (await session.execute(select(ExtractedField))).scalars().first()
        resolved = (
            await session.execute(
                select(ResolvedField).where(
                    ResolvedField.shipment_id == shipment_id,
                    ResolvedField.target_key == "declaration.importer_name",
                )
            )
        ).scalars().first()
        if resolved is None:
            resolved = ResolvedField(
                shipment_id=shipment_id,
                template_version="tokhai_import_v1",
                target_key="declaration.importer_name",
                value="KINGSMAN",
                state="RESOLVED",
                origin="INPUT_DERIVED",
                document_role="BUSINESS_INPUT",
                resolution_method="DIRECT",
            )
            session.add(resolved)
            await session.flush()
        session.add(
            DeclarationFieldSource(
                resolved_field_id=resolved.id,
                source_field_id=field.id,
                document_id=doc_id,
                evidence_role="PRIMARY",
            )
        )
        await session.commit()

    async with fk_db() as session:
        await pipeline.process_document(session, llm=None, document_id=doc_id, force=True)

    async with fk_db() as session:
        doc = await session.get(Document, doc_id)
        assert doc.status == "EXTRACTED", doc.error


async def test_reprocessing_after_review_api_edit_respects_foreign_keys(fk_db, tmp_path, monkeypatch):
    """A review edit writes field_value_history rows; re-extraction must not abort."""
    monkeypatch.delenv("IMPORTDOC_LEGACY_DECLARATION_MIRROR", raising=False)
    monkeypatch.setattr(pipeline, "ExtractionService", DeclarationStubService)
    doc_id, shipment_id = await _seed(fk_db, tmp_path)

    async with fk_db() as session:
        await pipeline.process_document(session, llm=None, document_id=doc_id)

    # Simulate the review API write path: EDITED status plus a FieldValueHistory child.
    async with fk_db() as session:
        from app.models import FieldValueHistory

        field = (
            await session.execute(
                select(ExtractedField).where(ExtractedField.field_key == "declaration.importer_name")
            )
        ).scalars().one()
        field.raw_value = "CÔNG TY ĐÃ SỬA"
        field.normalized_value = "CÔNG TY ĐÃ SỬA"
        field.review_status = "EDITED"
        session.add(
            FieldValueHistory(field_id=field.id, old_value="KINGSMAN", new_value="CÔNG TY ĐÃ SỬA", reason="review")
        )
        await session.commit()

    async with fk_db() as session:
        await pipeline.process_document(session, llm=None, document_id=doc_id, force=True)

    async with fk_db() as session:
        doc = await session.get(Document, doc_id)
        assert doc.status == "EXTRACTED", doc.error
        field = (
            await session.execute(
                select(ExtractedField).where(ExtractedField.field_key == "declaration.importer_name")
            )
        ).scalars().one()
        assert field.normalized_value == "CÔNG TY ĐÃ SỬA"
        assert field.review_status == "EDITED"


async def test_invoice_and_declaration_rows_keep_separate_line_items(db, tmp_path, monkeypatch):
    """A bundle's invoice line and declaration goods line must not share one LineItem."""
    from app.services.pipeline import _persist

    doc_id, _ = await _seed(db, tmp_path)
    async with db() as session:
        doc = await session.get(Document, doc_id)

        def row(key, value, line_index):
            return ExtractedRow(
                field_key=key,
                raw_value=value,
                normalized_value=value,
                unit=None,
                page=1,
                bbox=None,
                cell_ref=None,
                confidence=None,
                provenance_type="TEXT_LAYER",
                grounding_verified=True,
                review_status="UNREVIEWED",
                critical=False,
                line_index=line_index,
                evidence_state="FACT_VERIFIED",
            )

        await _persist(
            session,
            doc,
            [
                row("line.description", "INVOICE ITEM", 1),
                row("line.amount", "11615", 1),
                row("goods.description", "DECLARED GOODS", 1001),
                row("goods.hs_code", "44071110", 1001),
                row("goods_tax.name", "VAT", 100101),
            ],
        )
        await session.commit()

    async with db() as session:
        items = (await session.execute(select(LineItem).order_by(LineItem.seq))).scalars().all()
        assert {item.seq for item in items} == {1, 1001}
        by_seq = {item.seq: item.description for item in items}
        assert by_seq[1] == "INVOICE ITEM"
        assert by_seq[1001] == "DECLARED GOODS"
        fields = (await session.execute(select(ExtractedField))).scalars().all()
        goods_tax = next(f for f in fields if f.field_key == "goods_tax.name")
        assert goods_tax.line_item_id == next(i.id for i in items if i.seq == 1001)
