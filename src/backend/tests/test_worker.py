from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import worker
from app.models import Base, Job


@pytest.fixture()
async def job_db(tmp_path):
    from app.config import Settings
    from app.db import dispose, init_engine, session_scope

    settings = Settings.model_validate(
        {
            "llm": {
                "api_key": "test",
                "base_url": "https://example.test/v1",
                "text_model": "sonya",
                "vision_model": "vision_sonya",
            },
            "data_dir": tmp_path / "storage",
            "database_url": f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}",
        }
    )
    init_engine(settings)
    from app.db import create_all

    await create_all()
    yield session_scope
    await dispose()


async def test_claim_increments_attempts_and_sets_lease(job_db):
    async with job_db() as session:
        session.add(Job(kind="PROCESS_DOCUMENTS", payload={}, idempotency_key="k1"))
    job_id = await worker.claim_job()
    assert job_id is not None
    async with job_db() as session:
        job = await session.get(Job, job_id)
        assert job.status == "RUNNING"
        assert job.attempts == 1
        assert job.lease_until is not None


async def test_failed_job_is_requeued_until_budget_is_spent(job_db):
    async with job_db() as session:
        session.add(Job(kind="PROCESS_DOCUMENTS", payload={}, idempotency_key="k2"))

    for expected_attempt in (1, 2, 3):
        job_id = await worker.claim_job()
        assert job_id is not None
        await worker._finish_job(job_id, failed=True)
        async with job_db() as session:
            job = await session.get(Job, job_id)
            assert job.attempts == expected_attempt
            if expected_attempt < worker.MAX_ATTEMPTS:
                assert job.status == "PENDING"
            else:
                assert job.status == "FAILED"

    assert await worker.claim_job() is None


async def test_expired_lease_is_requeued(job_db):
    async with job_db() as session:
        session.add(
            Job(
                kind="PROCESS_DOCUMENTS",
                payload={},
                idempotency_key="k3",
                status="RUNNING",
                attempts=1,
                lease_until=worker._now() - timedelta(seconds=5),
            )
        )
    assert await worker.requeue_expired_leases() == 1
    assert await worker.claim_job() is not None


async def test_live_lease_is_not_stolen(job_db):
    async with job_db() as session:
        session.add(
            Job(
                kind="PROCESS_DOCUMENTS",
                payload={},
                idempotency_key="k4",
                status="RUNNING",
                attempts=1,
                lease_until=worker._now() + timedelta(seconds=60),
            )
        )
    assert await worker.requeue_expired_leases() == 0
    assert await worker.claim_job() is None


async def test_run_process_job_does_not_double_count_attempts(job_db, monkeypatch):
    """claim_job is the only writer of attempts; the runner must not add a second one."""
    from app.services import job_runner

    async with job_db() as session:
        session.add(Job(kind="PROCESS_DOCUMENTS", payload={"shipment_id": None, "document_ids": []}, idempotency_key="k5"))

    job_id = await worker.claim_job()
    ok = await job_runner.run_process_job(job_id, llm=None)
    assert ok is True
    async with job_db() as session:
        job = await session.get(Job, job_id)
        assert job.attempts == 1

    await worker._finish_job(job_id, failed=False)
    async with job_db() as session:
        job = await session.get(Job, job_id)
        assert job.status == "DONE"
        assert job.lease_until is None


async def test_run_process_job_reports_failure(job_db, monkeypatch):
    from app.services import job_runner, pipeline

    async def boom(*args, **kwargs):
        raise RuntimeError("doc exploded")

    monkeypatch.setattr(pipeline, "process_document", boom)
    async with job_db() as session:
        session.add(Job(kind="PROCESS_DOCUMENTS", payload={"document_ids": [999]}, idempotency_key="k6"))

    job_id = await worker.claim_job()
    assert await job_runner.run_process_job(job_id, llm=None) is False
    async with job_db() as session:
        job = await session.get(Job, job_id)
        assert job.error and "999" in job.error


async def test_run_process_job_tracks_document_progress(job_db, monkeypatch):
    from app.services import job_runner, pipeline

    seen: list[tuple[int, str]] = []

    async def fake_process(session, llm, document_id, force=False, on_stage=None):
        await on_stage(document_id, "EXTRACTING")
        seen.append((document_id, "EXTRACTING"))

    monkeypatch.setattr(pipeline, "process_document", fake_process)
    async with job_db() as session:
        session.add(Job(kind="PROCESS_DOCUMENTS", payload={"document_ids": [11, 12]}, idempotency_key="p1"))

    job_id = await worker.claim_job()
    assert await job_runner.run_process_job(job_id, llm=None) is True
    assert sorted(seen) == [(11, "EXTRACTING"), (12, "EXTRACTING")]
    async with job_db() as session:
        job = await session.get(Job, job_id)
        assert job.document_total == 2
        assert job.document_done == 2
        assert job.current_stage == "FINALIZING"
        assert await _live_stages(session, job_id) == {}


async def test_run_process_job_runs_documents_in_parallel(job_db, monkeypatch):
    """Two files must be in flight at the same time."""
    import asyncio

    from app.services import job_runner, pipeline

    both_running = asyncio.Event()
    started = 0

    async def fake_process(session, llm, document_id, force=False, on_stage=None):
        nonlocal started
        started += 1
        await on_stage(document_id, "EXTRACTING")
        if started == 2:
            both_running.set()
        await asyncio.wait_for(both_running.wait(), timeout=2)

    monkeypatch.setattr(pipeline, "process_document", fake_process)
    async with job_db() as session:
        session.add(Job(kind="PROCESS_DOCUMENTS", payload={"document_ids": [21, 22]}, idempotency_key="par"))

    job_id = await worker.claim_job()
    assert await job_runner.run_process_job(job_id, llm=None) is True
    assert started == 2
    async with job_db() as session:
        job = await session.get(Job, job_id)
        assert job.document_done == 2
        assert await _live_stages(session, job_id) == {}


async def _live_stages(session, job_id: int) -> dict[str, str]:
    from sqlalchemy import select

    from app.models import JobDocumentStage

    rows = (await session.execute(select(JobDocumentStage).where(JobDocumentStage.job_id == job_id))).scalars()
    return {str(row.document_id): row.stage for row in rows}


async def test_parallel_files_do_not_lose_stage_or_done_updates(job_db, monkeypatch):
    """Every file publishes stages concurrently: no stage row and no completion may be lost."""
    import asyncio

    from app.services import job_runner, pipeline

    doc_ids = list(range(1, 9))
    concurrent_peak = 0
    in_flight = 0

    async def fake_process(session, llm, document_id, force=False, on_stage=None):
        nonlocal concurrent_peak, in_flight
        in_flight += 1
        concurrent_peak = max(concurrent_peak, in_flight)
        try:
            for stage in ("PARSING", "CLASSIFYING", "EXTRACTING"):
                await on_stage(document_id, stage)
                await asyncio.sleep(0)
        finally:
            in_flight -= 1

    monkeypatch.setattr(pipeline, "process_document", fake_process)
    async with job_db() as session:
        session.add(Job(kind="PROCESS_DOCUMENTS", payload={"document_ids": doc_ids}, idempotency_key="many"))
    job_id = await worker.claim_job()

    # Another file can finish between two independent reads, so the invariant is
    # "never higher than the number of files" plus the end state below.
    original = job_runner._set_document_stage
    observed: list[int] = []

    async def spy(job, document_id, stage, attempt=None):
        await original(job, document_id, stage, attempt)
        if stage is None:
            async with job_db() as session:
                observed.append((await session.get(Job, job)).document_done)

    monkeypatch.setattr(job_runner, "_set_document_stage", spy)
    assert await job_runner.run_process_job(job_id, llm=None) is True
    assert concurrent_peak > 1
    assert observed and max(observed) <= len(doc_ids)
    async with job_db() as session:
        job = await session.get(Job, job_id)
        assert job.document_done == len(doc_ids)
        assert job.document_total == len(doc_ids)
        assert await _live_stages(session, job_id) == {}
        assert job.current_stage == "FINALIZING"


async def test_duplicate_document_ids_are_processed_once(job_db, monkeypatch):
    from app.services import job_runner, pipeline

    calls: list[int] = []

    async def fake_process(session, llm, document_id, force=False, on_stage=None):
        calls.append(document_id)

    monkeypatch.setattr(pipeline, "process_document", fake_process)
    async with job_db() as session:
        session.add(Job(kind="PROCESS_DOCUMENTS", payload={"document_ids": [5, 5, 6]}, idempotency_key="dup"))
    job_id = await worker.claim_job()

    assert await job_runner.run_process_job(job_id, llm=None) is True
    assert sorted(calls) == [5, 6]
    async with job_db() as session:
        job = await session.get(Job, job_id)
        assert job.document_total == 2
        assert job.document_done == 2


async def test_document_stages_are_recorded_per_file(job_db):
    from app.services import job_runner

    async with job_db() as session:
        session.add(Job(kind="PROCESS_DOCUMENTS", payload={"document_ids": [31, 32]}, idempotency_key="stg"))
    job_id = await worker.claim_job()
    async with job_db() as session:
        (await session.get(Job, job_id)).document_total = 2

    await job_runner._set_document_stage(job_id, 31, "PARSING")
    await job_runner._set_document_stage(job_id, 32, "CLASSIFYING")
    async with job_db() as session:
        job = await session.get(Job, job_id)
        assert await _live_stages(session, job_id) == {"31": "PARSING", "32": "CLASSIFYING"}
        assert job.document_done == 0
        assert job.current_document_id == 32

    await job_runner._set_document_stage(job_id, 31, None)
    async with job_db() as session:
        job = await session.get(Job, job_id)
        assert await _live_stages(session, job_id) == {"32": "CLASSIFYING"}
        assert job.document_done == 1


async def test_parallel_limit_reads_environment(monkeypatch):
    from app.services import job_runner

    monkeypatch.delenv("MAX_PARALLEL_DOCUMENTS", raising=False)
    assert job_runner.parallel_limit() == job_runner.DEFAULT_PARALLEL_DOCUMENTS
    monkeypatch.setenv("MAX_PARALLEL_DOCUMENTS", "2")
    assert job_runner.parallel_limit() == 2
    monkeypatch.setenv("MAX_PARALLEL_DOCUMENTS", "nonsense")
    assert job_runner.parallel_limit() == job_runner.DEFAULT_PARALLEL_DOCUMENTS
    monkeypatch.setenv("MAX_PARALLEL_DOCUMENTS", "0")
    assert job_runner.parallel_limit() == 1
