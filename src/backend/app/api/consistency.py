from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record_audit
from ..deps import get_llm, get_session
from ..models import ConsistencyFinding, Shipment
from ..services.consistency import consistency_report, dismiss_finding, run_consistency_check

router = APIRouter(tags=["consistency"])


class DismissBody(BaseModel):
    note: str | None = None


async def _require_shipment(session: AsyncSession, shipment_id: int) -> None:
    if await session.get(Shipment, shipment_id) is None:
        raise HTTPException(404, "Không tìm thấy lô hàng")


@router.get("/shipments/{shipment_id}/consistency")
async def get_consistency(shipment_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    await _require_shipment(session, shipment_id)
    return await consistency_report(session, shipment_id)


@router.post("/shipments/{shipment_id}/consistency/run")
async def run_consistency(
    shipment_id: int, session: AsyncSession = Depends(get_session), llm=Depends(get_llm)
) -> dict:
    await _require_shipment(session, shipment_id)
    await run_consistency_check(session, llm, shipment_id)
    return await consistency_report(session, shipment_id)


@router.post("/consistency-findings/{finding_id}/dismiss")
async def dismiss(finding_id: int, body: DismissBody, session: AsyncSession = Depends(get_session)) -> dict:
    finding = await session.get(ConsistencyFinding, finding_id)
    if finding is None:
        raise HTTPException(404, "Không tìm thấy cảnh báo")
    await dismiss_finding(session, finding, body.note)
    record_audit(session, "consistency_finding", finding_id, "DISMISSED", payload={"note": body.note})
    await session.commit()
    return await consistency_report(session, finding.shipment_id)
