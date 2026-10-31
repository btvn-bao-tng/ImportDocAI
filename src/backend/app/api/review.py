from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record_audit
from ..deps import get_session
from ..models import Document, ExtractedField, FieldValueHistory, LineItem, Party, DocumentParty, Shipment
from ..services.fields import ALL_FIELD_SPECS
from ..services.job_runner import refresh_shipment_status
from ..services.normalize import normalize_currency, normalize_date, normalize_unit, parse_decimal, number_to_str

router = APIRouter(tags=["review"])


class FieldPatch(BaseModel):
    value: str | None = None
    reason: str | None = None


def _recompute_normalized(field_key: str, value: str | None, existing_unit: str | None) -> tuple[str | None, str | None]:
    spec = ALL_FIELD_SPECS.get(field_key)
    if spec is None or value is None:
        return value, existing_unit
    if spec.kind == "date":
        parsed = normalize_date(value)
        return (parsed if parsed else value), None
    if spec.kind == "currency":
        return normalize_currency(value) or value, None
    if spec.kind == "unit":
        return normalize_unit(value) or value, None
    if spec.kind == "decimal":
        num = parse_decimal(value)
        unit = "kg" if field_key.endswith("_kg") else ("m³" if field_key.endswith("_cbm") else existing_unit)
        return (number_to_str(num) if num is not None else value), unit
    if spec.kind == "int":
        num = parse_decimal(value)
        return (str(int(num)) if num is not None else value), None
    return value, None


def serialize_field(f: ExtractedField) -> dict:
    spec = ALL_FIELD_SPECS.get(f.field_key)
    return {
        "id": f.id,
        "document_id": f.document_id,
        "line_item_id": f.line_item_id,
        "field_key": f.field_key,
        "label_vi": spec.label_vi if spec else f.field_key,
        "raw_value": f.raw_value,
        "normalized_value": f.normalized_value,
        "unit": f.unit,
        "page": f.page,
        "bbox": f.bbox,
        "cell_ref": f.cell_ref,
        "confidence": float(f.confidence) if f.confidence is not None else None,
        "provenance_type": f.provenance_type,
        "grounding_verified": f.grounding_verified,
        "review_status": f.review_status,
        "critical": f.critical,
    }


@router.get("/shipments/{shipment_id}/extractions")
async def get_extractions(shipment_id: int, session: AsyncSession = Depends(get_session)):
    shipment = await session.get(Shipment, shipment_id)
    if shipment is None:
        raise HTTPException(404, "Không tìm thấy shipment")
    docs = (await session.execute(select(Document).where(Document.shipment_id == shipment_id))).scalars().all()
    out = []
    for doc in docs:
        fields = (
            await session.execute(
                select(ExtractedField)
                .where(ExtractedField.document_id == doc.id)
                .order_by(ExtractedField.line_item_id.is_(None).desc(), ExtractedField.id)
            )
        ).scalars().all()
        items = (await session.execute(select(LineItem).where(LineItem.document_id == doc.id).order_by(LineItem.seq))).scalars().all()
        out.append(
            {
                "document": {
                    "id": doc.id,
                    "filename": doc.filename,
                    "doc_type": doc.doc_type,
                    "doc_type_source": doc.doc_type_source,
                    "status": doc.status,
                    "page_count": doc.page_count,
                    "has_text_layer": doc.has_text_layer,
                    "duplicate_of_id": doc.duplicate_of_id,
                    "version_note": doc.version_note,
                    "error": doc.error,
                },
                "fields": [serialize_field(f) for f in fields if f.line_item_id is None],
                "line_items": [
                    {
                        "id": item.id,
                        "seq": item.seq,
                        "description": item.description,
                        "raw_description": item.raw_description,
                        "spec": item.spec,
                        "quantity": str(item.quantity) if item.quantity is not None else None,
                        "unit": item.unit,
                        "unit_price": str(item.unit_price) if item.unit_price is not None else None,
                        "amount": str(item.amount) if item.amount is not None else None,
                        "fields": [serialize_field(f) for f in fields if f.line_item_id == item.id],
                    }
                    for item in items
                ],
            }
        )
    return {"shipment_id": shipment_id, "documents": out}


@router.patch("/shipments/{shipment_id}/fields/{field_id}")
async def patch_field(
    shipment_id: int,
    field_id: int,
    body: FieldPatch,
    session: AsyncSession = Depends(get_session),
):
    field = await session.get(ExtractedField, field_id)
    if field is None:
        raise HTTPException(404, "Không tìm thấy trường dữ liệu")
    doc = await session.get(Document, field.document_id)
    if doc is None or doc.shipment_id != shipment_id:
        raise HTTPException(400, "Trường dữ liệu không thuộc shipment này")

    old_value = field.normalized_value
    normalized, unit = _recompute_normalized(field.field_key, body.value, field.unit)
    field.raw_value = body.value
    field.normalized_value = normalized
    field.unit = unit
    field.review_status = "CONFIRMED" if body.value == old_value else "EDITED"
    field.grounding_verified = False if body.value != old_value else field.grounding_verified

    session.add(
        FieldValueHistory(
            field_id=field.id,
            old_value=old_value,
            new_value=normalized,
            actor="local-user",
            reason=body.reason,
        )
    )
    record_audit(
        session,
        "field",
        field.id,
        "FIELD_EDITED",
        payload={"field_key": field.field_key, "old": old_value, "new": normalized, "reason": body.reason},
    )
    await refresh_shipment_status(session, shipment_id)
    await session.commit()
    return serialize_field(field)


@router.post("/shipments/{shipment_id}/fields/{field_id}/confirm")
async def confirm_field(shipment_id: int, field_id: int, session: AsyncSession = Depends(get_session)):
    field = await session.get(ExtractedField, field_id)
    if field is None:
        raise HTTPException(404, "Không tìm thấy trường dữ liệu")
    doc = await session.get(Document, field.document_id)
    if doc is None or doc.shipment_id != shipment_id:
        raise HTTPException(400, "Trường dữ liệu không thuộc shipment này")
    old = field.review_status
    field.review_status = "CONFIRMED"
    session.add(FieldValueHistory(field_id=field.id, old_value=field.normalized_value, new_value=field.normalized_value, reason="Xác nhận giá trị"))
    record_audit(session, "field", field.id, "FIELD_CONFIRMED", payload={"field_key": field.field_key, "from": old})
    await refresh_shipment_status(session, shipment_id)
    await session.commit()
    return serialize_field(field)


@router.get("/shipments/{shipment_id}/fields/{field_id}/history")
async def field_history(shipment_id: int, field_id: int, session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(FieldValueHistory).where(FieldValueHistory.field_id == field_id).order_by(FieldValueHistory.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "old_value": r.old_value,
            "new_value": r.new_value,
            "actor": r.actor,
            "reason": r.reason,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get("/shipments/{shipment_id}/parties")
async def get_parties(shipment_id: int, session: AsyncSession = Depends(get_session)):
    parties = (await session.execute(select(Party).where(Party.shipment_id == shipment_id))).scalars().all()
    out = []
    for party in parties:
        links = (await session.execute(select(DocumentParty).where(DocumentParty.party_id == party.id))).scalars().all()
        docs = []
        for link in links:
            doc = await session.get(Document, link.document_id)
            docs.append({"document_id": link.document_id, "filename": doc.filename if doc else None, "role": link.role})
        out.append(
            {
                "id": party.id,
                "raw_name": party.raw_name,
                "canonical_name": party.canonical_name,
                "roles": sorted({d["role"] for d in docs}),
                "documents": docs,
            }
        )
    return out
