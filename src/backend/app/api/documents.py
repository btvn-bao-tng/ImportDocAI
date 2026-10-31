from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record_audit
from ..deps import get_settings, get_session
from ..models import Document, Shipment
from ..services.constants import IngestError
from ..services.ingest import compute_checksum, normalize_doc_type, parse_document, validate_upload

router = APIRouter(tags=["documents"])


@router.post("/shipments/{shipment_id}/documents", status_code=201)
async def upload_documents(
    shipment_id: int,
    files: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_session),
    settings=Depends(get_settings),
):
    shipment = await session.get(Shipment, shipment_id)
    if shipment is None:
        raise HTTPException(404, "Không tìm thấy shipment")

    results = []
    for upload in files:
        data = await upload.read()
        filename = upload.filename or "unnamed"
        try:
            ext = validate_upload(filename, data)
        except IngestError as exc:
            results.append({"filename": filename, "status": "REJECTED", "error": str(exc)})
            continue

        checksum = compute_checksum(data)
        duplicate = (
            await session.execute(
                select(Document).where(Document.shipment_id == shipment_id, Document.checksum == checksum)
            )
        ).scalars().first()
        if duplicate is not None:
            record_audit(
                session,
                "document",
                duplicate.id,
                "DUPLICATE_UPLOAD_IGNORED",
                payload={"filename": filename},
            )
            results.append(
                {
                    "filename": filename,
                    "status": "DUPLICATE",
                    "document_id": duplicate.id,
                    "duplicate_of_id": duplicate.id,
                }
            )
            continue

        doc = Document(
            shipment_id=shipment_id,
            filename=filename,
            file_ext=ext,
            storage_path="",
            checksum=checksum,
            status="UPLOADED",
        )
        session.add(doc)
        await session.flush()

        target_dir = settings.data_dir / "files" / str(shipment_id) / str(doc.id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"original{ext}"
        target.write_bytes(data)
        doc.storage_path = str(target)

        try:
            parsed = await asyncio.to_thread(parse_document, target)
            doc.page_count = parsed["page_count"]
            doc.has_text_layer = bool(parsed["has_text_layer"])
        except IngestError as exc:
            doc.status = "FAILED"
            doc.error = str(exc)
            record_audit(session, "document", doc.id, "UPLOAD_FAILED", payload={"error": str(exc)})
            results.append({"filename": filename, "status": "FAILED", "document_id": doc.id, "error": str(exc)})
            continue

        record_audit(session, "document", doc.id, "UPLOADED", payload={"filename": filename, "checksum": checksum})
        results.append({"filename": filename, "status": "UPLOADED", "document_id": doc.id, "page_count": doc.page_count})

    # Only a batch that actually stored a document can ever be processed: flipping a
    # DRAFT shipment to PROCESSING when every file was rejected would strand it there
    # (no documents means no job, and only a job's refresh would reset the status).
    if shipment.status == "DRAFT" and any(r["status"] in {"UPLOADED", "FAILED"} for r in results):
        shipment.status = "PROCESSING"
    await session.commit()
    return {"documents": results}


class ReclassifyBody(BaseModel):
    doc_type: str
    reprocess: bool = True


@router.post("/documents/{document_id}/reclassify")
async def reclassify(
    document_id: int,
    body: ReclassifyBody,
    session: AsyncSession = Depends(get_session),
):
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Không tìm thấy chứng từ")
    new_type = normalize_doc_type(body.doc_type)
    old_type = doc.doc_type
    doc.doc_type = new_type
    doc.doc_type_source = "USER"
    record_audit(session, "document", doc.id, "RECLASSIFIED", payload={"from": old_type, "to": new_type})
    if body.reprocess:
        doc.status = "UPLOADED"
    await session.commit()
    return {"id": doc.id, "doc_type": doc.doc_type, "status": doc.status}


class DuplicateResolve(BaseModel):
    keep_this: bool
    note: str | None = None


@router.post("/documents/{document_id}/resolve-duplicate")
async def resolve_duplicate(
    document_id: int,
    body: DuplicateResolve,
    session: AsyncSession = Depends(get_session),
):
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Không tìm thấy chứng từ")
    if doc.status != "IS_DUPLICATE_OF" or doc.duplicate_of_id is None:
        raise HTTPException(400, "Chứng từ này không ở trạng thái trùng lặp")

    primary = await session.get(Document, doc.duplicate_of_id)
    if body.keep_this:
        doc.status = "EXTRACTED"
        doc.duplicate_of_id = None
        doc.version_note = body.note or "Người dùng chọn làm bản chính"
        if primary is not None:
            primary.status = "SUPERSEDED_BY"
            primary.duplicate_of_id = doc.id
            primary.version_note = f"Đã thay bằng {doc.filename}"
    else:
        doc.version_note = body.note or "Giữ bản hiện tại"
    record_audit(session, "document", doc.id, "DUPLICATE_RESOLVED", payload={"keep_this": body.keep_this, "note": body.note})
    await session.commit()
    return {"id": doc.id, "status": doc.status, "primary_id": doc.duplicate_of_id}


@router.get("/documents/{document_id}/file")
async def download_original(document_id: int, session: AsyncSession = Depends(get_session)):
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Không tìm thấy chứng từ")
    path = Path(doc.storage_path)
    if not path.exists():
        raise HTTPException(404, "File gốc không còn trên storage")
    return FileResponse(path, filename=doc.filename)


@router.get("/documents/{document_id}/grid")
async def document_grid(document_id: int, session: AsyncSession = Depends(get_session)):
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Không tìm thấy chứng từ")
    path = Path(doc.storage_path)
    try:
        parsed = parse_document(path)
    except IngestError as exc:
        raise HTTPException(400, str(exc)) from exc
    if parsed["grid"] is None:
        raise HTTPException(400, "Chứng từ này không có dạng bảng tính")
    return parsed["grid"]
