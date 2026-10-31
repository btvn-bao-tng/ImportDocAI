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
    assert seen == [(11, "EXTRACTING"), (12, "EXTRACTING")]
    async with job_db() as session:
        job = await session.get(Job, job_id)
        assert job.document_total == 2
        assert job.document_done == 2
        assert job.current_stage == "FINALIZING"


async def test_progress_is_ignored_once_job_is_terminal(job_db):
    from app.services import job_runner

    async with job_db() as session:
        session.add(Job(kind="PROCESS_DOCUMENTS", payload={}, idempotency_key="p2", status="DONE"))
        job = (await session.execute(__import__("sqlalchemy").select(Job))).scalars().first()

    await job_runner._update_progress(job.id, current_stage="EXTRACTING")
    async with job_db() as session:
        job = await session.get(Job, job.id)
        assert job.current_stage is None


async def test_finish_job_requeues_until_budget_then_fails(job_db):
    async with job_db() as session:
        session.add(Job(kind="PROCESS_DOCUMENTS", payload={"document_ids": []}, idempotency_key="drain"))
    drained = 0
    while True:
        job_id = await worker.claim_job()
        if job_id is None:
            break
        drained += 1
        await worker._finish_job(job_id, failed=True)
    assert drained == worker.MAX_ATTEMPTS
    async with job_db() as session:
        job = (await session.execute(__import__("sqlalchemy").select(Job).where(Job.idempotency_key == "drain"))).scalars().first()
        assert job.status == "FAILED"
        assert job.attempts == worker.MAX_ATTEMPTS
