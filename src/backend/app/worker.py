from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from .config import load_settings
from .db import create_all, dispose, init_engine, session_scope
from .deps import init_state
from .models import Job
from .services.job_runner import run_process_job
from .services.llm import LLMClient

log = logging.getLogger("importdoc.worker")

LEASE_SECONDS = 120
IDLE_SLEEP_SECONDS = 1.5
MAX_ATTEMPTS = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def requeue_expired_leases() -> int:
    async with session_scope() as session:
        expired = (
            await session.execute(select(Job).where(Job.status == "RUNNING", Job.lease_until < _now()))
        ).scalars().all()
        for job in expired:
            job.status = "PENDING"
            job.lease_until = None
            log.warning("requeued expired lease job %s (attempts=%s)", job.id, job.attempts)
        return len(expired)


async def renew_lease(job_id: int) -> None:
    async with session_scope() as session:
        await session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "RUNNING")
            .values(lease_until=_now() + timedelta(seconds=LEASE_SECONDS))
        )


async def _heartbeat(job_id: int) -> None:
    """Keep the lease alive while a long extraction call is in flight."""
    while True:
        await asyncio.sleep(max(5.0, LEASE_SECONDS / 3))
        try:
            await renew_lease(job_id)
        except Exception:
            log.warning("lease renewal failed for job %s", job_id, exc_info=True)


async def claim_job() -> int | None:
    """Atomically claim one pending job (single-writer SQLite; guarded by the status predicate).

    A job that has already used its attempts stays FAILED instead of being retried forever;
    expired leases below MAX_ATTEMPTS are requeued by `requeue_expired_leases`.
    """
    async with session_scope() as session:
        job = (
            await session.execute(
                select(Job)
                .where(Job.status == "PENDING", Job.attempts < MAX_ATTEMPTS)
                .order_by(Job.id)
                .limit(1)
            )
        ).scalars().first()
        if job is None:
            return None
        claimed = await session.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == "PENDING")
            .values(status="RUNNING", lease_until=_now() + timedelta(seconds=LEASE_SECONDS), attempts=Job.attempts + 1)
        )
        if claimed.rowcount != 1:
            return None
        return job.id


async def _pending_remaining() -> int:
    async with session_scope() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(Job)
                .where(Job.status == "PENDING", Job.attempts < MAX_ATTEMPTS)
            )
        ).scalar_one()


async def _finish_job(job_id: int, *, failed: bool) -> None:
    """Release the lease and apply the retry budget on failure."""
    async with session_scope() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return
        job.lease_until = None
        job.attempts = max(job.attempts, 1)
        if failed and job.attempts < MAX_ATTEMPTS:
            job.status = "PENDING"
            log.warning("job %s failed (attempts=%s/%s) — requeued", job_id, job.attempts, MAX_ATTEMPTS)
        elif failed:
            job.status = "FAILED"
            log.error("job %s exhausted %s attempts", job_id, MAX_ATTEMPTS)
        else:
            job.status = "DONE"


async def run_worker(poll_seconds: float = IDLE_SLEEP_SECONDS, once: bool = False) -> None:
    settings = load_settings()
    init_engine(settings)
    await create_all()
    llm = LLMClient(settings.llm)
    init_state(settings, llm, lambda job_id: asyncio.sleep(0))
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
    log.info("worker %s started", worker_id)
    recovered = await requeue_expired_leases()
    if recovered:
        log.info("recovered %s jobs with expired leases", recovered)

    try:
        while True:
            job_id = await claim_job()
            if job_id is None:
                if once:
                    return
                await asyncio.sleep(poll_seconds)
                continue
            log.info("claimed job %s", job_id)
            heartbeat = asyncio.create_task(_heartbeat(job_id))
            failed = False
            try:
                failed = not await run_process_job(job_id, llm)
            except Exception:
                log.exception("job %s failed unexpectedly", job_id)
                failed = True
            finally:
                heartbeat.cancel()
            await _finish_job(job_id, failed=failed)
            if once and await _pending_remaining() == 0:
                return
    finally:
        await dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="ImportDoc AI job worker")
    parser.add_argument("--once", action="store_true", help="Drain the pending queue (incl. retries) then exit")
    parser.add_argument("--poll", type=float, default=IDLE_SLEEP_SECONDS)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run_worker(poll_seconds=args.poll, once=args.once))


if __name__ == "__main__":
    main()
