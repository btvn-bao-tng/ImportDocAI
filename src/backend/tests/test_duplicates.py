from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, ExtractedField, Shipment
from app.services.duplicates import _similar_type, _same_physical_kind, detect_duplicates
from app.services.ocr_diff import difference_between


def _doc(ext: str, doc_type: str) -> Document:
    return Document(filename="x", file_ext=ext, doc_type=doc_type, storage_path="", checksum="", status="EXTRACTED")


def test_physical_kind_grouping():
    assert _same_physical_kind(_doc(".pdf", "A"), _doc(".pdf", "B"))
    assert _same_physical_kind(_doc(".pdf", "A"), _doc(".jpg", "B"))
    assert _same_physical_kind(_doc(".xls", "A"), _doc(".xlsx", "B"))
    assert not _same_physical_kind(_doc(".pdf", "A"), _doc(".xls", "B"))


def test_type_similarity_requires_same_or_non_unique():
    assert _similar_type("TRANSPORT_DOCUMENT", "TRANSPORT_DOCUMENT")
    assert not _similar_type("COMMERCIAL_INVOICE", "SALES_CONTRACT_PO")
    assert _similar_type("CERTIFICATE_OF_ORIGIN", "CERTIFICATE_OF_ORIGIN")


def test_difference_between_reports_first_divergence():
    a = ["DUPLICATE 001 YMJAI245332642 20 PALLETS"]
    b = ["DUPLICATE 001 YMJAI245332642 21 PALLETS"]
    diff = difference_between(a, b)
    assert diff is not None and "20" in diff and "21" in diff
    assert difference_between(a, a) is None


async def _seed(session: AsyncSession, shipment_id: int, filename: str, ext: str, doc_type: str, values: dict[str, str]) -> Document:
    doc = Document(
        shipment_id=shipment_id,
        filename=filename,
        file_ext=ext,
        doc_type=doc_type,
        storage_path=str(Path("/nonexistent") / filename),
        checksum=filename,
        status="EXTRACTED",
    )
    session.add(doc)
    await session.flush()
    for key, value in values.items():
        session.add(ExtractedField(document_id=doc.id, field_key=key, normalized_value=value, provenance_type="TEXT_LAYER"))
    await session.flush()
    return doc


async def test_same_waybill_is_flagged_as_potential_version(db_session: AsyncSession):
    shipment = Shipment(code="LS2628")
    db_session.add(shipment)
    await db_session.flush()
    first = await _seed(db_session, shipment.id, "bill.pdf", ".pdf", "TRANSPORT_DOCUMENT", {"transport.waybill_no": "YMJAI245332642"})
    second = await _seed(db_session, shipment.id, "bill-2.pdf", ".pdf", "TRANSPORT_DOCUMENT", {"transport.waybill_no": "YMJAI245332642"})
    await db_session.commit()

    await detect_duplicates(db_session, second)
    await db_session.commit()
    await db_session.refresh(first)
    assert first.status == "IS_DUPLICATE_OF"
    assert first.duplicate_of_id == second.id


async def test_invoice_and_packing_list_sharing_number_are_not_duplicates(db_session: AsyncSession):
    shipment = Shipment(code="LS2628-B")
    db_session.add(shipment)
    await db_session.flush()
    invoice = await _seed(db_session, shipment.id, "inv.xls", ".xls", "COMMERCIAL_INVOICE", {"document.doc_number": "LS2628"})
    await _seed(db_session, shipment.id, "packing.xls", ".xls", "PACKING_LIST", {"document.doc_number": "LS2628"})
    await db_session.commit()

    await detect_duplicates(db_session, (await db_session.get(Document, invoice.id + 1)))
    await db_session.commit()
    await db_session.refresh(invoice)
    assert invoice.status == "EXTRACTED"


async def test_two_contracts_sharing_contract_number_are_not_duplicates(db_session: AsyncSession):
    shipment = Shipment(code="LS2628-C")
    db_session.add(shipment)
    await db_session.flush()
    po = await _seed(db_session, shipment.id, "po.pdf", ".pdf", "SALES_CONTRACT_PO", {"document.doc_number": "LS2628"})
    await _seed(db_session, shipment.id, "svc.pdf", ".pdf", "SERVICE_CONTRACT", {"document.doc_number": "LS2628"})
    await db_session.commit()

    await detect_duplicates(db_session, (await db_session.get(Document, po.id + 1)))
    await db_session.commit()
    await db_session.refresh(po)
    assert po.status == "EXTRACTED"
