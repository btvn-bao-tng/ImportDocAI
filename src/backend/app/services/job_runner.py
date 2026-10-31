from __future__ import annotations

import asyncio
import logging
import os
from typing import Awaitable, Callable

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record_audit
from ..db import session_scope
from ..models import Document, ExtractedField, Job, Shipment
from .llm import LLMClient
from . import pipeline

log = logging.getLogger("importdoc.jobs")

StageCallback = Callable[[int, str], Awaitable[None]]

# Pipeline stages reported per document, in order. Either the file parses its text
# layer (PARSING) or it is rendered to an image, but the UI always sees the same plan
# so the stepper does not jump around between documents.
DOCUMENT_STAGES = ("PARSING", "CLASSIFYING", "EXTRACTING", "PERSISTING")
FINAL_STAGE = "FINALIZING"

# Documents of one job run concurrently. LLM calls are network-bound, so the wall clock
# is close to the slowest file instead of the sum of all files.
DEFAULT_PARALLEL_DOCUMENTS = 4
MAX_PARALLEL_DOCUMENTS = DEFAULT_PARALLEL_DOCUMENTS


def parallel_limit() -> int:
    """Concurrency cap, overridable with MAX_PARALLEL_DOCUMENTS (1 = old sequential mode)."""
    try:
        return max(1, int(os.environ.get("MAX_PARALLEL_DOCUMENTS") or DEFAULT_PARALLEL_DOCUMENTS))
    except ValueError:
        return DEFAULT_PARALLEL_DOCUMENTS


async def _update_progress(job_id: int, **values) -> None:
    """Overwrite job progress fields. Only called between files, never inside a per-file
    stage update, so the values cannot be partially written."""
    async with session_scope() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status not in {"PENDING", "RUNNING"}:
            return
        for key, value in values.items():
            setattr(job, key, value)


async def _set_document_stage(job_id: int, document_id: int, stage: str | None) -> None:
    """Record the live stage of one file.

    Files finish concurrently, so the row is read and written inside one transaction and
    the completed count is derived from the stage map itself instead of being incremented
    from a stale copy (two files finishing at once would otherwise lose an increment).
    """
    async with session_scope() as session:
        job = await session.get(Job, job_id, with_for_update=True)
        if job is None or job.status not in {"PENDING", "RUNNING"}:
            return
        stages = dict(job.document_stages or {})
        if stage is None:
            stages.pop(str(document_id), None)
        else:
            stages[str(document_id)] = stage
        job.document_stages = stages
        if stage is None:
            job.document_done = (job.document_done or 0) + 1
        else:
            job.current_stage = stage
            job.current_document_id = document_id


async def _settle_progress(job_id: int, doc_ids: list[int], final_stage: str) -> None:
    """Close the per-file bookkeeping once every file has returned.

    Late stage writes from a file that just finished are reverted here, so the page
    cannot keep showing a stage for a file the job already counted as done.
    """
    async with session_scope() as session:
        job = await session.get(Job, job_id, with_for_update=True)
        if job is None or job.status not in {"PENDING", "RUNNING"}:
            return
        job.document_stages = {}
        job.document_done = len(doc_ids)
        job.document_total = len(doc_ids)
        job.current_document_id = None
        job.current_stage = final_stage


async def refresh_shipment_status(session: AsyncSession, shipment_id: int) -> None:
    shipment = await session.get(Shipment, shipment_id)
    if shipment is None:
        return
    statuses = (await session.execute(select(Document.status).where(Document.shipment_id == shipment_id))).scalars().all()
    if not statuses:
        shipment.status = "DRAFT"
        return
    if any(s in {"PROCESSING", "UPLOADED", "CLASSIFIED"} for s in statuses):
        shipment.status = "PROCESSING"
        return
    unreviewed = (
        await session.execute(
            select(func.count())
            .select_from(ExtractedField)
            .join(Document, ExtractedField.document_id == Document.id)
            .where(
                Document.shipment_id == shipment_id,
                ExtractedField.critical.is_(True),
                or_(
                    ExtractedField.review_status.in_(["UNREVIEWED", "NEEDS_REVIEW"]),
                    ExtractedField.grounding_verified.is_(False),
                ),
            )
        )
    ).scalar_one()
    if any(s == "FAILED" for s in statuses) or unreviewed > 0:
        shipment.status = "NEEDS_REVIEW"
    else:
        shipment.status = "READY_TO_EXPORT"


async def run_process_job(job_id: int, llm: LLMClient) -> bool:
    """Background task body. Idempotent: documents already EXTRACTED are skipped.

    Returns False when any document failed. The worker owns `attempts` and the terminal
    job status; this function only records the error so retries stay consistent.
    """
    async with session_scope() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status == "DONE":
            return True
        payload = dict(job.payload or {})
        await session.commit()

    # Duplicate ids would run the same file twice and double-count it.
    doc_ids = list(dict.fromkeys(int(doc_id) for doc_id in (payload.get("document_ids") or [])))
    await _update_progress(
        job_id,
        document_total=len(doc_ids),
        document_done=0,
        current_document_id=None,
        current_stage=None,
        document_stages={},
    )

    async def on_stage(doc_id: int, stage: str) -> None:
        await _set_document_stage(job_id, doc_id, stage)

    async def run_document(doc_id: int) -> None:
        try:
            async with session_scope() as session:
                await pipeline.process_document(
                    session, llm, doc_id, force=bool(payload.get("force")), on_stage=on_stage
                )
        except Exception as exc:
            log.exception("process job %s doc %s failed", job_id, doc_id)
            failures.append(f"doc {doc_id}: {exc}")
        finally:
            await _set_document_stage(job_id, doc_id, None)

    failures: list[str] = []
    if doc_ids:
        gate = asyncio.Semaphore(parallel_limit())

        async def run_limited(doc_id: int) -> None:
            async with gate:
                await run_document(doc_id)

        await asyncio.gather(*(run_limited(doc_id) for doc_id in doc_ids))

    await _settle_progress(job_id, doc_ids, FINAL_STAGE)

    async with session_scope() as session:
        shipment_id = payload.get("shipment_id")
        if shipment_id:
            await pipeline.upsert_parties_and_transactions(session, shipment_id)
            await refresh_shipment_status(session, shipment_id)
        job = await session.get(Job, job_id)
        if job is not None:
            job.error = "; ".join(failures) if failures else None
            record_audit(session, "job", job_id, "FAILED" if failures else "DONE", payload={"failures": failures})
    return not failures
