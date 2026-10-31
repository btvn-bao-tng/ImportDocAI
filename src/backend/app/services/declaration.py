
"""Service layer for the Tờ khai hải quan (TKN) output family.

`build_declaration_payload` is the single place that turns stored rows into the values
the template expects. It is deliberately dumb: it never derives a value that is not
stored, never picks between conflicting declarations, and never formats numbers on its
own (the template prints the exact strings the source system shows).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Declaration, DeclarationTaxLine, Document, GoodsLine
from .export_excel import FieldSource
from .export_tokhai import DeclarationPayload
from .fields import ALL_FIELD_SPECS

# Order matters: the first match wins, and a conflicting duplicate is reported, not merged.
DECLARATION_DOC_TYPES = ("CUSTOMS_DECLARATION", "CUSTOMS_DECLARATION_SCAN")


async def get_declaration(session: AsyncSession, shipment_id: int) -> Declaration | None:
    return (
        await session.execute(
            select(Declaration)
            .where(Declaration.shipment_id == shipment_id)
            .order_by(Declaration.id)
            .limit(1)
        )
    ).scalars().first()


async def build_declaration_payload(session: AsyncSession, shipment_id: int) -> DeclarationPayload | None:
    declaration = await get_declaration(session, shipment_id)
    if declaration is None:
        return None

    header: dict[str, str | None] = {}
    sources: dict[str, FieldSource] = {}
    doc = await session.get(Document, declaration.document_id) if declaration.document_id else None
    for key, spec in ALL_FIELD_SPECS.items():
        if not key.startswith("declaration."):
            continue
        column = key.split(".", 1)[1]
        header[key] = getattr(declaration, column, None)
        if header[key] not in (None, "") and doc is not None:
            sources[key] = FieldSource(
                document_filename=doc.filename,
                document_id=doc.id,
                provenance_type="EXCEL_GRID" if doc.file_ext in {".xls", ".xlsx", ".xlsm"} else "VLM",
                review_status=declaration.review_status,
                grounding_verified=declaration.review_status == "CONFIRMED",
            )

    goods_rows = (
        await session.execute(
            select(GoodsLine).where(GoodsLine.declaration_id == declaration.id).order_by(GoodsLine.seq)
        )
    ).scalars().all()
    goods = [
        {key: getattr(line, key.split(".", 1)[1], None) for key in ALL_FIELD_SPECS if key.startswith("goods.")}
        for line in goods_rows
    ]

    tax_rows = (
        await session.execute(
            select(DeclarationTaxLine)
            .where(DeclarationTaxLine.declaration_id == declaration.id)
            .order_by(DeclarationTaxLine.seq)
        )
    ).scalars().all()
    taxes = [
        {key: getattr(tax, key.split(".", 1)[1], None) for key in ALL_FIELD_SPECS if key.startswith("tax.")}
        for tax in tax_rows
    ]
    goods_reviewed = bool(goods_rows) and all(line.review_status == "CONFIRMED" for line in goods_rows)
    return DeclarationPayload(
        header=header,
        goods=goods,
        taxes=taxes,
        sources=sources,
        review_status=declaration.review_status,
        goods_reviewed=goods_reviewed,
    )


async def upsert_declaration(
    session: AsyncSession,
    shipment_id: int,
    header: dict[str, str | None],
    goods: list[dict[str, str | None]],
    taxes: list[dict[str, str | None]],
    document_id: int | None = None,
) -> Declaration:
    """Replace the shipment's declaration rows with the supplied values.

    Re-extraction is idempotent: goods/tax rows are rebuilt, never appended twice.
    """
    from ..models import utcnow

    declaration = await get_declaration(session, shipment_id)
    if declaration is None:
        declaration = Declaration(shipment_id=shipment_id, document_id=document_id)
        session.add(declaration)
        await session.flush()

    for key, value in header.items():
        if not key.startswith("declaration."):
            continue
        column = key.split(".", 1)[1]
        if hasattr(declaration, column):
            setattr(declaration, column, value)
    if declaration.declaration_no:
        declaration.declaration_no_digits = "".join(ch for ch in declaration.declaration_no if ch.isdigit()) or None
    declaration.document_id = document_id or declaration.document_id
    declaration.updated_at = utcnow()

    existing_goods = (
        await session.execute(select(GoodsLine).where(GoodsLine.declaration_id == declaration.id))
    ).scalars().all()
    confirmed = {line.seq: line for line in existing_goods if line.review_status == "CONFIRMED"}
    for existing in existing_goods:
        if existing.review_status != "CONFIRMED":
            await session.delete(existing)
    for existing in (
        await session.execute(
            select(DeclarationTaxLine).where(DeclarationTaxLine.declaration_id == declaration.id)
        )
    ).scalars().all():
        await session.delete(existing)
    await session.flush()

    for seq, row in enumerate(goods, start=1):
        # A line the reviewer confirmed is never overwritten by a re-extraction: the new
        # values become candidates only, exactly like field-level overrides.
        if seq in confirmed:
            continue
        line = GoodsLine(declaration_id=declaration.id, document_id=document_id, seq=seq)
        for key, value in row.items():
            column = key.split(".", 1)[1]
            if hasattr(line, column):
                setattr(line, column, value)
        session.add(line)
    for seq, row in enumerate(taxes):
        tax = DeclarationTaxLine(declaration_id=declaration.id, seq=seq + 1)
        for key, value in row.items():
            column = key.split(".", 1)[1]
            if hasattr(tax, column):
                setattr(tax, column, value)
        session.add(tax)
    await session.flush()
    return declaration
