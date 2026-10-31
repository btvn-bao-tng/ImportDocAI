from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record_audit
from ..deps import get_llm, get_session
from ..models import (
    DeclarationFieldSource,
    Document,
    ResolvedField,
    ResolvedFieldHistory,
    Shipment,
)
from ..services.declaration import build_new_draft_payload, build_reference_comparison
from ..services.resolution import CustomsFieldEdit, RevisionConflict, resolve_shipment, set_resolved_field
from ..services.target_catalog import load_target_catalog

router = APIRouter(tags=["declaration"])


class ResolvedFieldPatch(BaseModel):
    value: str | None = None
    reason: str | None = None
    expected_revision: int | None = None


class ResolveRequest(BaseModel):
    template_version: str = "tokhai_import_v1"
    goods_grouping: str | None = None


def _serialize(row: ResolvedField, evidence: list[dict] | None = None) -> dict:
    return {
        "id": row.id,
        "target_key": row.target_key,
        "value": row.value,
        "value_type": row.value_type,
        "unit": row.unit,
        "state": row.state,
        "origin": row.origin,
        "document_role": row.document_role,
        "resolution_method": row.resolution_method,
        "review_status": row.review_status,
        "revision": row.revision,
        "source_snapshot_hash": row.source_snapshot_hash,
        "evidence": evidence or [],
    }


async def _evidence_for(session: AsyncSession, resolved_field_id: int) -> list[dict]:
    rows = (
        await session.execute(
            select(DeclarationFieldSource).where(
                DeclarationFieldSource.resolved_field_id == resolved_field_id
            )
        )
    ).scalars().all()
    out = []
    for item in rows:
        doc = await session.get(Document, item.document_id) if item.document_id else None
        out.append(
            {
                "role": item.evidence_role,
                "document_id": item.document_id,
                "document_filename": doc.filename if doc else None,
                "document_type": doc.doc_type if doc else None,
                "section_id": item.section_id,
                "page": item.page,
                "cell_ref": item.cell_ref,
                "bbox": item.bbox,
                "evidence_state": item.evidence_state,
                "note": item.note,
            }
        )
    return out


async def _shipment_or_404(session: AsyncSession, shipment_id: int) -> Shipment:
    shipment = await session.get(Shipment, shipment_id)
    if shipment is None:
        raise HTTPException(404, "Không tìm thấy shipment")
    return shipment


@router.get("/shipments/{shipment_id}/declaration")
async def get_resolved_declaration(
    shipment_id: int,
    template_version: str = "tokhai_import_v1",
    session: AsyncSession = Depends(get_session),
):
    """Resolved target fields with alternatives, evidence, mode-neutral review state."""
    await _shipment_or_404(session, shipment_id)
    catalog = load_target_catalog(template_version)
    rows = (
        await session.execute(
            select(ResolvedField)
            .where(
                ResolvedField.shipment_id == shipment_id,
                ResolvedField.template_version == template_version,
            )
            .order_by(ResolvedField.target_key)
        )
    ).scalars().all()
    fields = []
    for row in rows:
        fields.append(_serialize(row, await _evidence_for(session, row.id)))
    payload = await build_new_draft_payload(session, shipment_id, template_version)
    return {
        "shipment_id": shipment_id,
        "template_version": template_version,
        "catalog_version": catalog.version,
        "fields": fields,
        "missing_required": [
            key
            for key in catalog.required_keys()
            if not any(f["target_key"] == key and f["value"] not in (None, "") for f in fields)
        ],
        "needs_review": [f["target_key"] for f in fields if f["review_status"] == "NEEDS_REVIEW"],
        "draft_critical_missing": payload.critical_missing(),
        "draft_unreviewed_critical": payload.unreviewed_critical(),
    }


@router.post("/shipments/{shipment_id}/resolve")
async def resolve(
    shipment_id: int,
    body: ResolveRequest | None = None,
    session: AsyncSession = Depends(get_session),
    llm=Depends(get_llm),
):
    """Async-idempotent Stage-2 resolution. Re-running never overwrites a human edit."""
    await _shipment_or_404(session, shipment_id)
    body = body or ResolveRequest()
    report = await resolve_shipment(
        session,
        shipment_id,
        body.template_version,
        llm,
        group_policy=body.goods_grouping,
    )
    record_audit(
        session,
        "shipment",
        shipment_id,
        "RESOLUTION_STALE" if report.stale else "RESOLVED",
        payload=report.summary(),
    )
    await session.commit()
    return report.summary()


@router.patch("/shipments/{shipment_id}/resolved-fields/{field_key}")
async def patch_resolved_field(
    shipment_id: int,
    field_key: str,
    body: ResolvedFieldPatch,
    template_version: str = "tokhai_import_v1",
    session: AsyncSession = Depends(get_session),
):
    await _shipment_or_404(session, shipment_id)
    try:
        row = await set_resolved_field(
            session,
            shipment_id,
            field_key,
            body.value,
            template_version=template_version,
            reason=body.reason,
            expected_revision=body.expected_revision,
        )
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except CustomsFieldEdit as exc:
        raise HTTPException(422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    record_audit(
        session,
        "resolved_field",
        row.id,
        "RESOLVED_FIELD_EDITED",
        payload={"target_key": field_key, "value": row.value, "reason": body.reason},
    )
    await session.commit()
    return _serialize(row, await _evidence_for(session, row.id))


@router.post("/shipments/{shipment_id}/resolved-fields/{field_key}/confirm")
async def confirm_resolved_field(
    shipment_id: int,
    field_key: str,
    template_version: str = "tokhai_import_v1",
    session: AsyncSession = Depends(get_session),
):
    await _shipment_or_404(session, shipment_id)
    try:
        row = await set_resolved_field(
            session,
            shipment_id,
            field_key,
            None,
            template_version=template_version,
            reason="Xác nhận giá trị đã chọn",
            confirm=True,
        )
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except CustomsFieldEdit as exc:
        raise HTTPException(422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    record_audit(session, "resolved_field", row.id, "RESOLVED_FIELD_CONFIRMED", payload={"target_key": field_key})
    await session.commit()
    return _serialize(row, await _evidence_for(session, row.id))


@router.get("/shipments/{shipment_id}/resolved-fields/{field_key}/history")
async def resolved_field_history(
    shipment_id: int,
    field_key: str,
    template_version: str = "tokhai_import_v1",
    session: AsyncSession = Depends(get_session),
):
    await _shipment_or_404(session, shipment_id)
    row = (
        await session.execute(
            select(ResolvedField).where(
                ResolvedField.shipment_id == shipment_id,
                ResolvedField.target_key == field_key,
                ResolvedField.template_version == template_version,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(404, "Không tìm thấy trường đã resolve")
    rows = (
        await session.execute(
            select(ResolvedFieldHistory)
            .where(ResolvedFieldHistory.resolved_field_id == row.id)
            .order_by(ResolvedFieldHistory.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "old_value": item.old_value,
            "new_value": item.new_value,
            "actor": item.actor,
            "reason": item.reason,
            "resolution_method": item.resolution_method,
            "created_at": item.created_at,
        }
        for item in rows
    ]


@router.get("/shipments/{shipment_id}/reference-comparison")
async def reference_comparison(
    shipment_id: int,
    template_version: str = "tokhai_import_v1",
    reference_document_id: int | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Show differences against ONE reference snapshot; never blends declarations."""
    await _shipment_or_404(session, shipment_id)
    try:
        return await build_reference_comparison(
            session, shipment_id, template_version, reference_document_id
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
