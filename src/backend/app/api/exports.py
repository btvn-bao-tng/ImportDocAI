from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record_audit
from ..deps import get_settings, get_session
from ..models import Document, ExportJob, ExtractedField, LineItem, Party, Shipment
from ..services.declaration import build_declaration_payload
from ..services.export_tokhai import TOKHAI_TEMPLATE_VERSION, write_tokhai_export
from ..services.linegroup import group_line_items
from ..services.export_excel import (
    DEFAULT_TEMPLATE_VERSION,
    FieldSource,
    ExportError,
    VERIFY_TEMPLATE_VERSION,
    available_templates,
    load_mapping,
    load_verify_mapping,
    write_export,
    write_verify_export,
)

router = APIRouter(tags=["exports"])

LINE_PRIORITY = {
    "INVOICE": ["COMMERCIAL_INVOICE", "SALES_CONTRACT_PO", "OTHER"],
    "PACKING LIST": ["PACKING_LIST", "COMMERCIAL_INVOICE", "OTHER"],
    "HOP DONG": ["SALES_CONTRACT_PO", "COMMERCIAL_INVOICE", "PACKING_LIST", "OTHER"],
}

PARTY_KEYS = {
    "contract.seller": ("SELLER", "party.seller"),
    "contract.buyer": ("BUYER", "party.buyer"),
    "contract.shipper": ("SHIPPER", "party.shipper"),
    "contract.consignee": ("CONSIGNEE", "party.consignee"),
}


class ExportRequest(BaseModel):
    template_version: str = DEFAULT_TEMPLATE_VERSION


def _documents_in_order(docs: list[Document], preferred: list[str]) -> list[Document]:
    seen: list[Document] = []
    for doc_type in preferred:
        seen.extend([d for d in docs if d.doc_type == doc_type and d not in seen])
    seen.extend([d for d in docs if d not in seen])
    return seen


async def _linked_party(session: AsyncSession, document_id: int, role: str):
    from ..models import DocumentParty

    link = (
        await session.execute(
            select(DocumentParty).where(DocumentParty.document_id == document_id, DocumentParty.role == role)
        )
    ).scalars().first()
    if link is None:
        return None
    return await session.get(Party, link.party_id)


def _pick_document(docs: list[Document], preferred: list[str]) -> Document | None:
    for doc_type in preferred:
        for doc in docs:
            if doc.doc_type == doc_type and doc.status == "EXTRACTED":
                return doc
    return None


async def build_export_payload(session: AsyncSession, shipment: Shipment):
    docs = (
        await session.execute(select(Document).where(Document.shipment_id == shipment.id))
    ).scalars().all()
    docs = [d for d in docs if d.status == "EXTRACTED"]

    values: dict[str, str | None] = {}
    sources: dict[str, FieldSource] = {}

    def offer(key: str, value, field: ExtractedField | None, doc: Document, override: bool = False):
        if value in (None, ""):
            return
        if key in values and not override:
            return
        values[key] = str(value)
        if field is not None:
            sources[key] = FieldSource(
                document_filename=doc.filename,
                document_id=doc.id,
                page=field.page,
                cell_ref=field.cell_ref,
                provenance_type=field.provenance_type,
                review_status=field.review_status,
                grounding_verified=field.grounding_verified,
                confidence=float(field.confidence) if field.confidence is not None else None,
            )

    # shipment-level scalars: precedence is document-type order, first non-null wins
    type_order = [
        "TRANSPORT_DOCUMENT",
        "ARRIVAL_NOTICE",
        "COMMERCIAL_INVOICE",
        "PACKING_LIST",
        "SALES_CONTRACT_PO",
        "EXPORT_DECLARATION",
        "OTHER",
    ]
    for doc_type in type_order:
        for doc in docs:
            if doc.doc_type != doc_type:
                continue
            fields = (
                await session.execute(
                    select(ExtractedField).where(
                        ExtractedField.document_id == doc.id, ExtractedField.line_item_id.is_(None)
                    )
                )
            ).scalars().all()
            for f in fields:
                offer(f.field_key, f.normalized_value, f, doc)

    parties = (await session.execute(select(Party).where(Party.shipment_id == shipment.id))).scalars().all()
    from ..models import DocumentParty

    # contract parties: prefer the sales contract / purchase order, else the first document
    # that states the role. A company is never substituted from a different transaction.
    for contract_key, (role, party_field) in PARTY_KEYS.items():
        for doc in _documents_in_order(docs, ["SALES_CONTRACT_PO", "SERVICE_CONTRACT", "COMMERCIAL_INVOICE", "TRANSPORT_DOCUMENT"]):
            field = (
                await session.execute(
                    select(ExtractedField).where(
                        ExtractedField.document_id == doc.id,
                        ExtractedField.field_key == party_field,
                        ExtractedField.line_item_id.is_(None),
                    )
                )
            ).scalars().first()
            if field is not None and field.normalized_value:
                values[contract_key] = field.normalized_value
                sources[contract_key] = FieldSource(
                    document_filename=doc.filename,
                    document_id=doc.id,
                    page=field.page,
                    cell_ref=field.cell_ref,
                    provenance_type=field.provenance_type,
                    review_status=field.review_status,
                    grounding_verified=field.grounding_verified,
                )
                break
            link_party = await _linked_party(session, doc.id, role)
            if link_party is not None:
                values[contract_key] = link_party.raw_name
                sources[contract_key] = FieldSource(
                    document_filename=doc.filename,
                    document_id=doc.id,
                    provenance_type="TEXT_LAYER",
                    review_status="UNREVIEWED",
                    grounding_verified=True,
                )
                break

    # line items per sheet: choose the best single document for each sheet
    line_payload: dict[str, list[dict[str, str | None]]] = {}
    for sheet, order in LINE_PRIORITY.items():
        doc = _pick_document(docs, order)
        if doc is None:
            line_payload[sheet] = []
            continue
        items = (
            await session.execute(select(LineItem).where(LineItem.document_id == doc.id).order_by(LineItem.seq))
        ).scalars().all()
        item_fields = (
            await session.execute(
                select(ExtractedField).where(ExtractedField.document_id == doc.id, ExtractedField.line_item_id.is_not(None))
            )
        ).scalars().all()
        logical = group_line_items(items, list(item_fields))
        rows = []
        for idx, line in enumerate(logical, start=1):
            row = line.as_row(idx)
            row["line.packages"] = values.get("transport.packages")
            row["line.spec"] = line.components[0]["spec"] if line.components else None
            rows.append(row)
        field_rows = (
            await session.execute(
                select(ExtractedField).where(ExtractedField.document_id == doc.id, ExtractedField.line_item_id.is_not(None))
            )
        ).scalars().all()
        for f in field_rows:
            if f.field_key in {"line.quantity", "line.unit_price", "line.amount"}:
                sources.setdefault(
                    f.field_key,
                    FieldSource(
                        document_filename=doc.filename,
                        document_id=doc.id,
                        page=f.page,
                        cell_ref=f.cell_ref,
                        provenance_type=f.provenance_type,
                        review_status=f.review_status,
                        grounding_verified=f.grounding_verified,
                    ),
                )
        line_payload[sheet] = rows

    return values, sources, line_payload


@router.post("/shipments/{shipment_id}/exports", status_code=201)
async def create_export(
    shipment_id: int,
    body: ExportRequest,
    session: AsyncSession = Depends(get_session),
    settings=Depends(get_settings),
):
    shipment = await session.get(Shipment, shipment_id)
    if shipment is None:
        raise HTTPException(404, "Không tìm thấy shipment")

    export_dir = settings.data_dir.parent / "exports" / shipment.code

    if body.template_version == VERIFY_TEMPLATE_VERSION:
        preview = await verify_preview(shipment_id, session)
        if not preview["rows"]:
            raise HTTPException(422, "Lô hàng chưa có dữ liệu trích xuất để đối chiếu.")
        try:
            verify_outcome = write_verify_export(
                shipment.code, preview["rows"], template_version=body.template_version, export_dir=export_dir
            )
        except ExportError as exc:
            raise HTTPException(422, str(exc)) from exc
        return await _save_export(
            session, shipment, load_verify_mapping(), body.template_version, verify_outcome
        )

    try:
        mapping, _ = load_mapping(body.template_version)
    except ExportError as exc:
        raise HTTPException(400, str(exc)) from exc

    if body.template_version == TOKHAI_TEMPLATE_VERSION:
        declaration_payload = await build_declaration_payload(session, shipment_id)
        if declaration_payload is None:
            raise HTTPException(
                422,
                "Shipment chưa có dữ liệu tờ khai. Cần trích xuất Tờ khai hải quan trước khi xuất.",
            )
        try:
            token_outcome = write_tokhai_export(
                shipment.code, declaration_payload, template_version=body.template_version, export_dir=export_dir
            )
        except ExportError as exc:
            raise HTTPException(422, str(exc)) from exc
        return await _save_export(
            session, shipment, mapping, body.template_version, token_outcome
        )

    values, sources, line_payload = await build_export_payload(session, shipment)
    try:
        outcome = write_export(
            shipment.code,
            values,
            sources,
            line_payload,
            template_version=body.template_version,
            export_dir=export_dir,
        )
    except ExportError as exc:
        raise HTTPException(422, str(exc)) from exc
    return await _save_export(session, shipment, mapping, body.template_version, outcome)


async def _save_export(session: AsyncSession, shipment: Shipment, mapping, template_version: str, outcome) -> dict:
    export = ExportJob(
        shipment_id=shipment.id,
        template_version=template_version,
        mode=outcome.mode,
        mapping_snapshot=mapping,
        output_path=str(outcome.output_path),
        report_path=str(outcome.report_path),
        status="DONE",
        message="; ".join(outcome.warnings) if outcome.warnings else None,
    )
    session.add(export)
    # A verify run is a read-only projection of the extraction, not a customs export.
    if template_version != VERIFY_TEMPLATE_VERSION:
        shipment.status = "EXPORTED"
    record_audit(
        session,
        "shipment",
        shipment.id,
        "EXPORTED",
        payload={"mode": outcome.mode, "template": template_version, "missing": outcome.missing},
    )
    await session.commit()
    return {
        "id": export.id,
        "mode": outcome.mode,
        "filled": outcome.filled,
        "missing": outcome.missing,
        "warnings": outcome.warnings,
        "filename": outcome.output_path.name,
        "report_filename": outcome.report_path.name,
    }


@router.get("/shipments/{shipment_id}/verify-preview")
async def verify_preview(shipment_id: int, session: AsyncSession = Depends(get_session)):
    """One row per extracted field, grouped by document: the data behind the verify page."""
    from ..services.fields import ALL_FIELD_SPECS

    shipment = await session.get(Shipment, shipment_id)
    if shipment is None:
        raise HTTPException(404, "Không tìm thấy shipment")

    docs = (
        await session.execute(select(Document).where(Document.shipment_id == shipment_id).order_by(Document.id))
    ).scalars().all()
    documents = []
    rows: list[dict] = []
    for doc in docs:
        fields = (
            await session.execute(
                select(ExtractedField).where(ExtractedField.document_id == doc.id).order_by(ExtractedField.id)
            )
        ).scalars().all()
        doc_rows = []
        for f in fields:
            spec = ALL_FIELD_SPECS.get(f.field_key)
            doc_rows.append(
                {
                    "field_key": f.field_key,
                    "label_vi": spec.label_vi if spec else f.field_key,
                    "value": f.normalized_value if f.normalized_value is not None else f.raw_value,
                    "unit": f.unit,
                    "page": f.page,
                    "cell_ref": f.cell_ref,
                    "bbox": f.bbox,
                    "provenance_type": f.provenance_type,
                    "review_status": f.review_status,
                    "critical": f.critical,
                }
            )
            rows.append(
                {
                    **doc_rows[-1],
                    "filename": doc.filename,
                    "doc_type": doc.doc_type,
                    "provenance": (f.provenance_type or "").replace("_", " ").title(),
                }
            )
        documents.append(
            {
                "id": doc.id,
                "filename": doc.filename,
                "doc_type": doc.doc_type,
                "status": doc.status,
                "error": doc.error,
                "page_count": doc.page_count,
                "has_text_layer": doc.has_text_layer,
                "fields": doc_rows,
            }
        )

    critical_open = sum(1 for r in rows if r["critical"] and r["review_status"] != "CONFIRMED")
    return {
        "shipment": {"id": shipment.id, "code": shipment.code, "status": shipment.status},
        "documents": documents,
        "field_count": len(rows),
        "critical_open": critical_open,
        "rows": rows,
    }


@router.get("/shipments/{shipment_id}/exports")
async def list_exports(shipment_id: int, session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(ExportJob).where(ExportJob.shipment_id == shipment_id).order_by(ExportJob.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "mode": r.mode,
            "template_version": r.template_version,
            "status": r.status,
            "created_at": r.created_at,
            "filename": Path(r.output_path).name if r.output_path else None,
            "warnings": [w for w in (r.message or "").split("; ") if w],
        }
        for r in rows
    ]


@router.get("/exports/{export_id}/download")
async def download_export(export_id: int, session: AsyncSession = Depends(get_session)):
    export = await session.get(ExportJob, export_id)
    if export is None or not export.output_path:
        raise HTTPException(404, "Không tìm thấy file xuất")
    path = Path(export.output_path)
    if not path.exists():
        raise HTTPException(404, "File xuất không còn trên storage")
    return FileResponse(path, filename=path.name)


@router.get("/exports/{export_id}/report")
async def download_report(export_id: int, session: AsyncSession = Depends(get_session)):
    export = await session.get(ExportJob, export_id)
    if export is None or not export.report_path:
        raise HTTPException(404, "Không tìm thấy báo cáo đối chiếu")
    path = Path(export.report_path)
    if not path.exists():
        raise HTTPException(404, "Báo cáo không còn trên storage")
    return FileResponse(path, filename=path.name)
