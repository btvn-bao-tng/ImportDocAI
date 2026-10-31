from __future__ import annotations

import logging
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


async def _update_progress(job_id: int, **values) -> None:
    async with session_scope() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status not in {"PENDING", "RUNNING"}:
            return
        for key, value in values.items():
            setattr(job, key, value)


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

    doc_ids = list(payload.get("document_ids") or [])
    await _update_progress(
        job_id, document_total=len(doc_ids), document_done=0, current_document_id=None, current_stage=None
    )

    async def on_stage(doc_id: int, stage: str) -> None:
        await _update_progress(job_id, current_document_id=doc_id, current_stage=stage)

    failures: list[str] = []
    for doc_id in doc_ids:
        try:
            async with session_scope() as session:
                await pipeline.process_document(
                    session, llm, doc_id, force=bool(payload.get("force")), on_stage=on_stage
                )
        except Exception as exc:
            log.exception("process job %s doc %s failed", job_id, doc_id)
            failures.append(f"doc {doc_id}: {exc}")
        async with session_scope() as session:
            job = await session.get(Job, job_id)
            if job is not None:
                job.document_done = (job.document_done or 0) + 1

    await _update_progress(job_id, current_document_id=None, current_stage=FINAL_STAGE)

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
