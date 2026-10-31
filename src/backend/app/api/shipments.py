from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record_audit
from ..deps import get_session
from ..models import Document, ExtractedField, Shipment

router = APIRouter(tags=["shipments"])


class ShipmentCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    customer_name: str | None = None


def _summary(shipment: Shipment, docs: list[Document], doc_count: int, critical_open: int) -> dict:
    return {
        "id": shipment.id,
        "code": shipment.code,
        "customer_name": shipment.customer_name,
        "status": shipment.status,
        "created_at": shipment.created_at,
        "document_count": doc_count,
        "extracted_count": len([d for d in docs if d.status == "EXTRACTED"]),
        "failed_count": len([d for d in docs if d.status == "FAILED"]),
        "critical_unreviewed": critical_open,
    }


@router.post("/shipments", status_code=201)
async def create_shipment(body: ShipmentCreate, session: AsyncSession = Depends(get_session)):
    existing = (await session.execute(select(Shipment).where(Shipment.code == body.code))).scalars().first()
    if existing is not None:
        raise HTTPException(409, f"Shipment {body.code} đã tồn tại")
    shipment = Shipment(code=body.code, customer_name=body.customer_name, status="DRAFT")
    session.add(shipment)
    await session.flush()
    record_audit(session, "shipment", shipment.id, "CREATED", payload={"code": body.code})
    await session.commit()
    return {"id": shipment.id, "code": shipment.code, "status": shipment.status}


async def _open_critical_count(session: AsyncSession, shipment_id: int) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(ExtractedField)
            .join(Document, ExtractedField.document_id == Document.id)
            .where(
                Document.shipment_id == shipment_id,
                ExtractedField.critical.is_(True),
                ExtractedField.review_status.in_(["UNREVIEWED", "NEEDS_REVIEW"]),
                ExtractedField.grounding_verified.is_(False),
            )
        )
    ).scalar_one()


@router.get("/shipments")
async def list_shipments(
    q: str | None = None,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Shipment).order_by(Shipment.created_at.desc())
    if q:
        stmt = stmt.where(Shipment.code.ilike(f"%{q}%") | Shipment.customer_name.ilike(f"%{q}%"))
    if status:
        stmt = stmt.where(Shipment.status == status)
    shipments = (await session.execute(stmt)).scalars().all()

    out = []
    for shipment in shipments:
        docs = (await session.execute(select(Document).where(Document.shipment_id == shipment.id))).scalars().all()
        out.append(_summary(shipment, docs, len(docs), await _open_critical_count(session, shipment.id)))
    return out


@router.get("/shipments/{shipment_id}")
async def get_shipment(shipment_id: int, session: AsyncSession = Depends(get_session)):
    shipment = await session.get(Shipment, shipment_id)
    if shipment is None:
        raise HTTPException(404, "Không tìm thấy shipment")
    docs = (await session.execute(select(Document).where(Document.shipment_id == shipment.id))).scalars().all()
    return {
        **_summary(shipment, docs, len(docs), await _open_critical_count(session, shipment.id)),
        "documents": [
            {
                "id": d.id,
                "filename": d.filename,
                "doc_type": d.doc_type,
                "doc_type_source": d.doc_type_source,
                "classification_confidence": float(d.classification_confidence) if d.classification_confidence else None,
                "status": d.status,
                "page_count": d.page_count,
                "has_text_layer": d.has_text_layer,
                "duplicate_of_id": d.duplicate_of_id,
                "version_note": d.version_note,
                "error": d.error,
                "created_at": d.created_at,
            }
            for d in docs
        ],
    }
