from __future__ import annotations

import asyncio
import logging
import os
from typing import Awaitable, Callable

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record_audit
from ..db import session_scope
from ..models import Document, ExtractedField, Job, JobDocumentStage, Shipment
from .llm import LLMClient
from . import pipeline

log = logging.getLogger("importdoc.jobs")

StageCallback = Callable[[int, str], Awaitable[None]]

# Pipeline stages reported per document, in order. Either the file parses its text
# layer (PARSING) or it is rendered to an image, but the UI always sees the same plan
# so the stepper does not jump around between documents.
DOCUMENT_STAGES = ("PARSING", "CLASSIFYING", "EXTRACTING", "PERSISTING")
FINAL_STAGE = "FINALIZING"

# Documents of one job run concurrently. The LLM calls are network-bound, so the wall
# clock is close to the slowest file instead of the sum of all files.
DEFAULT_PARALLEL_DOCUMENTS = 4
# Hard ceiling: each in-flight file also holds a worker thread while its file is parsed.
MAX_PARALLEL_LIMIT = 8


def parallel_limit() -> int:
    """Concurrency cap, overridable with MAX_PARALLEL_DOCUMENTS (1 = sequential mode)."""
    try:
        wanted = int(os.environ.get("MAX_PARALLEL_DOCUMENTS") or DEFAULT_PARALLEL_DOCUMENTS)
    except ValueError:
        log.warning("MAX_PARALLEL_DOCUMENTS is not a number; using %s", DEFAULT_PARALLEL_DOCUMENTS)
        return DEFAULT_PARALLEL_DOCUMENTS
    if wanted > MAX_PARALLEL_LIMIT:
        log.warning("MAX_PARALLEL_DOCUMENTS=%s clamped to %s", wanted, MAX_PARALLEL_LIMIT)
    return min(MAX_PARALLEL_LIMIT, max(1, wanted))


# A transient SQLite write error on the progress tables is retried on this cadence;
# after the last delay the update is dropped so processing can continue.
_PROGRESS_RETRY_DELAYS = (0.25, 1.0, 3.0)


async def _update_progress(job_id: int, _retry: int = 0, **values) -> None:
    """Overwrite job progress fields. Only called between files, never per stage.

    Best-effort by design: progress cosmetics must never fail (or roll back) the
    document processing that reported them.
    """
    try:
        async with session_scope() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status not in {"PENDING", "RUNNING"}:
                return
            for key, value in values.items():
                setattr(job, key, value)
    except SQLAlchemyError as exc:
        if _retry < len(_PROGRESS_RETRY_DELAYS):
            await asyncio.sleep(_PROGRESS_RETRY_DELAYS[_retry])
            return await _update_progress(job_id, _retry=_retry + 1, **values)
        log.warning("progress update for job %s failed (continuing): %s", job_id, exc)


# Live stages live in their own table, one row per in-flight file. SQLite has no row
# locks (`with_for_update` compiles to a plain SELECT), so editing a shared JSON map
# would lose entries when two files report at once; an upsert on a unique key cannot.
# The map in the API response is projected from these rows, never stored twice.
async def _set_document_stage(
    job_id: int, document_id: int, stage: str | None, attempt: int | None = None, _retry: int = 0
) -> None:
    try:
        async with session_scope() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status not in {"PENDING", "RUNNING"}:
                return
            if attempt is not None and attempt != job.attempts:
                # A worker whose lease expired must not publish into the retry that replaced it.
                return
            if stage is None:
                await session.execute(
                    delete(JobDocumentStage).where(
                        JobDocumentStage.job_id == job_id,
                        JobDocumentStage.document_id == document_id,
                    )
                )
                done = (
                    await session.execute(
                        select(func.count()).select_from(JobDocumentStage).where(JobDocumentStage.job_id == job_id)
                    )
                ).scalar_one()
                job.document_done = max(0, min(job.document_total, job.document_total - done))
                job.current_stage = FINAL_STAGE
                job.current_document_id = None
                return
            await session.execute(
                sqlite_insert(JobDocumentStage)
                .values(job_id=job_id, document_id=document_id, stage=stage, attempt=job.attempts)
                .on_conflict_do_update(
                    index_elements=[JobDocumentStage.job_id, JobDocumentStage.document_id],
                    set_={"stage": stage},
                )
            )
            job.current_stage = stage
            job.current_document_id = document_id
    except SQLAlchemyError as exc:
        # The stage table only feeds the UI stepper, so a write that cannot succeed
        # must not abort the extraction that reported the stage. Retry first: losing
        # a completion update would leave `document_done` permanently short.
        if _retry < len(_PROGRESS_RETRY_DELAYS):
            await asyncio.sleep(_PROGRESS_RETRY_DELAYS[_retry])
            return await _set_document_stage(job_id, document_id, stage, attempt, _retry=_retry + 1)
        log.warning("stage update for job %s doc %s failed (continuing): %s", job_id, document_id, exc)
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
        attempt = job.attempts
        await session.commit()

    # Duplicate ids would run the same file twice and double-count it.
    doc_ids = list(dict.fromkeys(int(doc_id) for doc_id in (payload.get("document_ids") or [])))
    await _update_progress(
        job_id,
        document_total=len(doc_ids),
        document_done=0,
        current_document_id=None,
        current_stage=None,
    )
    async with session_scope() as session:
        await session.execute(delete(JobDocumentStage).where(JobDocumentStage.job_id == job_id))

    async def on_stage(doc_id: int, stage: str) -> None:
        await _set_document_stage(job_id, doc_id, stage, attempt)

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
            await _set_document_stage(job_id, doc_id, None, attempt)

    failures: list[str] = []
    if doc_ids:
        gate = asyncio.Semaphore(parallel_limit())

        async def run_limited(doc_id: int) -> None:
            async with gate:
                await run_document(doc_id)

        await asyncio.gather(*(run_limited(doc_id) for doc_id in doc_ids))

    async with session_scope() as session:
        await session.execute(delete(JobDocumentStage).where(JobDocumentStage.job_id == job_id))
    await _update_progress(job_id, current_document_id=None, current_stage=FINAL_STAGE)

    async with session_scope() as session:
        shipment_id = payload.get("shipment_id")
        resolution_error: str | None = None
        if shipment_id:
            await pipeline.upsert_parties_and_transactions(session, shipment_id)
            try:
                from .resolution import resolve_shipment

                report = await resolve_shipment(session, shipment_id, "tokhai_import_v1", llm)
                if report.stale:
                    # Sources changed mid-resolve: nothing was written, but reporting
                    # plain DONE would hide that the shipment has no resolved values.
                    resolution_error = f"RESOLUTION_STALE: {report.message}"
            except Exception as exc:
                # Resolution is a separate, retryable stage: a failure here must not
                # erase the extraction that already succeeded, and must not be reported
                # as a generic worker crash. Roll back so a half-written resolution is
                # never committed by the surrounding session_scope.
                log.exception("resolution failed for shipment %s", shipment_id)
                await session.rollback()
                resolution_error = f"RESOLUTION_FAILED: {exc}"
            await refresh_shipment_status(session, shipment_id)
        job = await session.get(Job, job_id)
        if job is not None:
            errors = [message for message in (["; ".join(failures)] if failures else []) + ([resolution_error] if resolution_error else []) if message]
            job.error = "; ".join(errors) if errors else None
            record_audit(session, "job", job_id, "FAILED" if failures else "DONE", payload={"failures": failures})
    return not failures
