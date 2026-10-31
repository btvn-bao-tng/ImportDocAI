from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record_audit
from ..deps import get_session
from ..models import Document, Job, JobDocumentStage, Shipment
from ..services.job_runner import DOCUMENT_STAGES, FINAL_STAGE

router = APIRouter(tags=["jobs"])


class ProcessRequest(BaseModel):
    document_ids: list[int] | None = None
    force: bool = False


@router.post("/shipments/{shipment_id}/process", status_code=202)
async def start_process(
    shipment_id: int,
    body: ProcessRequest,
    session: AsyncSession = Depends(get_session),
):
    """Queue extraction work. The durable job row is picked up by the worker process."""
    shipment = await session.get(Shipment, shipment_id)
    if shipment is None:
        raise HTTPException(404, "Không tìm thấy shipment")

    docs = (await session.execute(select(Document).where(Document.shipment_id == shipment_id))).scalars().all()
    if not docs:
        raise HTTPException(400, "Shipment chưa có chứng từ")
    if body.document_ids:
        wanted = set(body.document_ids)
        docs = [d for d in docs if d.id in wanted]
        if not docs:
            raise HTTPException(400, "Không có chứng từ hợp lệ để xử lý")

    doc_ids = [d.id for d in docs]
    key = f"process:{shipment_id}:{'-'.join(map(str, sorted(doc_ids)))}:{int(body.force)}"
    existing = (
        await session.execute(
            select(Job).where(Job.idempotency_key == key, Job.status.in_(["PENDING", "RUNNING"]))
        )
    ).scalars().first()
    if existing is not None:
        return {"job_id": existing.id, "status": existing.status, "reused": True}

    job = Job(
        kind="PROCESS_DOCUMENTS",
        payload={"shipment_id": shipment_id, "document_ids": doc_ids, "force": body.force},
        idempotency_key=key,
        document_total=len(doc_ids),
    )
    session.add(job)
    shipment.status = "PROCESSING"
    record_audit(session, "shipment", shipment_id, "PROCESS_QUEUED", payload={"documents": doc_ids})
    await session.commit()
    return {"job_id": job.id, "status": "PENDING"}


def serialize_job(job: Job, stage_rows: list[JobDocumentStage] | None = None) -> dict:
    stages = {str(row.document_id): row.stage for row in stage_rows or []}
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "attempts": job.attempts,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "progress": {
            "document_total": job.document_total or 0,
            "document_done": job.document_done or 0,
            "current_document_id": job.current_document_id,
            "current_stage": job.current_stage,
            "document_stages": stages,
            "stages": list(DOCUMENT_STAGES),
            "final_stage": FINAL_STAGE,
        },
    }


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, session: AsyncSession = Depends(get_session)):
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Không tìm thấy job")
    return serialize_job(job, await _stage_rows(session, job.id))


async def _stage_rows(session: AsyncSession, job_id: int) -> list[JobDocumentStage]:
    return list(
        (await session.execute(select(JobDocumentStage).where(JobDocumentStage.job_id == job_id))).scalars().all()
    )


@router.get("/shipments/{shipment_id}/progress")
async def shipment_progress(shipment_id: int, session: AsyncSession = Depends(get_session)):
    """Latest processing job of a shipment (RUNNING first), so the progress page can resume on reload."""
    shipment = await session.get(Shipment, shipment_id)
    if shipment is None:
        raise HTTPException(404, "Không tìm thấy shipment")
    jobs = (
        await session.execute(select(Job).order_by(Job.id.desc()).limit(50))
    ).scalars().all()
    own = [j for j in jobs if (j.payload or {}).get("shipment_id") == shipment_id and j.kind == "PROCESS_DOCUMENTS"]
    active = next((j for j in own if j.status in {"PENDING", "RUNNING"}), None)
    latest = active or (own[0] if own else None)
    rows = await _stage_rows(session, latest.id) if latest else []
    return {"shipment_status": shipment.status, "job": serialize_job(latest, rows) if latest else None}


@router.get("/shipments/{shipment_id}/jobs")
async def list_jobs(shipment_id: int, session: AsyncSession = Depends(get_session)):
    jobs = (
        await session.execute(select(Job).order_by(Job.id.desc()).limit(50))
    ).scalars().all()
    return [
        {"id": j.id, "kind": j.kind, "status": j.status, "attempts": j.attempts, "error": j.error}
        for j in jobs
        if (j.payload or {}).get("shipment_id") == shipment_id
    ]
