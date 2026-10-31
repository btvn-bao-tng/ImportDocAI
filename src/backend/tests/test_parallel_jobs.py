from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Document, Job, JobDocumentStage, Shipment


@pytest.fixture()
def engine_db(tmp_path):
    from app.config import LLMSettings, Settings
    from app.db import create_all, dispose, init_engine, session_scope

    init_engine(
        Settings(
            llm=LLMSettings(
                api_key="test",
                base_url="http://example.invalid/v1",
                text_model="sonya",
                vision_model="vision_sonya",
            ),
            data_dir=tmp_path / "storage",
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'par.db'}",
        )
    )
    return create_all, session_scope, dispose


async def test_two_declaration_files_mirror_into_one_declaration(engine_db):
    """Parallel files of one shipment must not create two Tờ khai header rows."""
    create_all, session_scope, dispose = engine_db
    await create_all()
    from sqlalchemy import func, select

    from app.models import Declaration
    from app.services.declaration import upsert_declaration

    async with session_scope() as session:
        session.add(Shipment(code="PAR-TKN", status="PROCESSING"))
        await session.flush()
        shipment_id = (await session.execute(select(Shipment.id))).scalar_one()
        for name in ("tkn-1.xlsx", "tkn-2.xlsx"):
            session.add(
                Document(
                    shipment_id=shipment_id,
                    filename=name,
                    file_ext="xlsx",
                    storage_path=str(Path("/tmp") / name),
                    checksum=name,
                    page_count=1,
                    has_text_layer=True,
                    status="PROCESSING",
                )
            )
        await session.flush()
        doc_ids = list((await session.execute(select(Document.id).order_by(Document.id))).scalars())

    async def mirror(document_id: int, number: str) -> None:
        async with session_scope() as session:
            await upsert_declaration(
                session,
                shipment_id,
                header={"declaration.declaration_no": number},
                goods=[{"goods.hs_code": "44071110", "goods.description": f"line {number}"}],
                taxes=[],
                document_id=document_id,
            )

    await asyncio.gather(mirror(doc_ids[0], "111"), mirror(doc_ids[1], "222"))

    async with session_scope() as session:
        count = (
            await session.execute(select(func.count()).select_from(Declaration).where(Declaration.shipment_id == shipment_id))
        ).scalar_one()
        assert count == 1
    await dispose()


async def test_parallel_documents_do_not_lock_each_other_out(engine_db, tmp_path, monkeypatch):
    """The extraction phase must never hold SQLite's write lock.

    A sibling document (or the job's own stage update) writes through another
    connection with a short busy timeout; if the pipeline keeps a write transaction
    open across the LLM await — minutes in production — those writers die with
    "database is locked". The probe here fails fast when the lock is held.
    """
    create_all, session_scope, dispose = engine_db
    await create_all()
    import pymupdf as fitz
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.services import pipeline
    from app.services.schemas import ClassificationOut

    probe_engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'par.db'}", connect_args={"timeout": 0.5}
    )
    probe_failures: list[str] = []

    for name in ("slow-a.pdf", "slow-b.pdf"):
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "COMMERCIAL INVOICE slow extraction\n" * 6)
        doc.save(tmp_path / name)
        doc.close()

    async with session_scope() as session:
        session.add(Shipment(code="PAR-SLOW", status="PROCESSING"))
        await session.flush()
        shipment_id = (await session.execute(select(Shipment.id))).scalar_one()
        for name in ("slow-a.pdf", "slow-b.pdf"):
            source = tmp_path / name
            session.add(
                Document(
                    shipment_id=shipment_id,
                    filename=name,
                    file_ext="pdf",
                    storage_path=str(source),
                    checksum=name,
                    page_count=1,
                    has_text_layer=True,
                    doc_type="COMMERCIAL_INVOICE",
                    doc_type_source="USER",
                    status="UPLOADED",
                )
            )
        await session.flush()
        doc_ids = list((await session.execute(select(Document.id).order_by(Document.id))).scalars())

    async def probe(holder: str) -> None:
        try:
            async with probe_engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO audit_events (entity, entity_id, action, actor, created_at) "
                        "VALUES ('probe', '0', 'PROBE', :who, CURRENT_TIMESTAMP)"
                    ),
                    {"who": holder},
                )
        except Exception as exc:  # OperationalError "database is locked" lands here
            probe_failures.append(f"{holder}: {exc}")

    class SlowService:
        def __init__(self, llm=None):
            pass

        async def classify_document(self, filename, path, pages, grid=None):
            return ClassificationOut(doc_type="COMMERCIAL_INVOICE", confidence=0.9, sections=[])

        async def extract_sections(self, filename, path, pages, sections, fallback_doc_type, grid=None):
            # Stand-in for minutes of LLM calls: a second writer must still get through.
            await probe(filename)
            await asyncio.sleep(0.2)
            await probe(f"{filename}:still")
            return []

    monkeypatch.setattr(pipeline, "ExtractionService", SlowService)

    async def run(document_id: int) -> None:
        async with session_scope() as session:
            await pipeline.process_document(session, llm=None, document_id=document_id)

    await asyncio.gather(*(run(doc_id) for doc_id in doc_ids))

    async with session_scope() as session:
        statuses = list(
            (await session.execute(select(Document.status).where(Document.id.in_(doc_ids)))).scalars()
        )
    assert probe_failures == [], probe_failures
    assert statuses == ["EXTRACTED", "EXTRACTED"]
    await probe_engine.dispose()
    await dispose()


async def test_requeued_job_drops_stage_rows_of_the_abandoned_attempt(engine_db):
    create_all, session_scope, dispose = engine_db
    await create_all()
    from datetime import datetime, timedelta, timezone

    from app import worker

    stale = datetime.now(timezone.utc) - timedelta(seconds=600)
    async with session_scope() as session:
        session.add(
            Job(
                kind="PROCESS_DOCUMENTS",
                payload={},
                status="RUNNING",
                attempts=1,
                idempotency_key="lease",
                lease_until=stale,
            )
        )
        await session.flush()
        session.add(JobDocumentStage(job_id=1, document_id=7, stage="EXTRACTING", attempt=1))

    assert await worker.requeue_expired_leases() == 1

    async with session_scope() as session:
        assert list((await session.execute(select(JobDocumentStage))).scalars()) == []
        assert (await session.get(Job, 1)).status == "PENDING"
    await dispose()
