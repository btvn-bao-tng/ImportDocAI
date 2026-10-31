
"""Service layer for the Tờ khai hải quan (TKN) output family.

`build_declaration_payload` is the single place that turns stored rows into the values
the template expects. It is deliberately dumb: it never derives a value that is not
stored, never picks between conflicting declarations, and never formats numbers on its
own (the template prints the exact strings the source system shows).
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Declaration,
    DeclarationAdjustment,
    DeclarationInstruction,
    DeclarationLicense,
    DeclarationTaxLine,
    Document,
    GoodsLine,
    GoodsTaxLine,
)
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
    tax_rows = (
        await session.execute(
            select(GoodsTaxLine).where(GoodsTaxLine.goods_line_id.in_([g.id for g in goods_rows]))
        )
    ).scalars().all() if goods_rows else []
    taxes_by_line: dict[int, list[GoodsTaxLine]] = {}
    for tax in tax_rows:
        taxes_by_line.setdefault(tax.goods_line_id, []).append(tax)
    goods = []
    for line in goods_rows:
        row = {
            key: getattr(line, key.split(".", 1)[1], None)
            for key in ALL_FIELD_SPECS
            if key.startswith("goods.")
        }
        row["taxes"] = [
            {k: getattr(t, k.split(".", 1)[1], None) for k in ALL_FIELD_SPECS if k.startswith("goods_tax.")}
            for t in sorted(taxes_by_line.get(line.id, []), key=lambda t: t.seq)
        ]
        goods.append(row)

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

    license_rows = (
        await session.execute(
            select(DeclarationLicense).where(DeclarationLicense.declaration_id == declaration.id).order_by(DeclarationLicense.seq)
        )
    ).scalars().all()
    licenses = [
        {key: getattr(row, key.split(".", 1)[1], None) for key in ALL_FIELD_SPECS if key.startswith("license.")}
        for row in license_rows
    ]

    instruction_rows = (
        await session.execute(
            select(DeclarationInstruction)
            .where(DeclarationInstruction.declaration_id == declaration.id)
            .order_by(DeclarationInstruction.seq)
        )
    ).scalars().all()
    instructions = [
        {key: getattr(row, key.split(".", 1)[1], None) for key in ALL_FIELD_SPECS if key.startswith("instruction.")}
        for row in instruction_rows
    ]

    adjustment_rows = (
        await session.execute(
            select(DeclarationAdjustment)
            .where(DeclarationAdjustment.declaration_id == declaration.id)
            .order_by(DeclarationAdjustment.seq)
        )
    ).scalars().all()
    adjustments = [
        {key: getattr(row, key.split(".", 1)[1], None) for key in ALL_FIELD_SPECS if key.startswith("adjustment.")}
        for row in adjustment_rows
    ]

    goods_reviewed = bool(goods_rows) and all(line.review_status == "CONFIRMED" for line in goods_rows)
    return DeclarationPayload(
        header=header,
        goods=goods,
        taxes=taxes,
        sources=sources,
        review_status=declaration.review_status,
        goods_reviewed=goods_reviewed,
        licenses=licenses,
        instructions=instructions,
        adjustments=adjustments,
    )


async def upsert_declaration(
    session: AsyncSession,
    shipment_id: int,
    header: dict[str, str | None],
    goods: list[dict[str, str | None]],
    taxes: list[dict[str, str | None]],
    goods_taxes: dict[int, list[dict[str, str | None]]] | None = None,
    licenses: list[dict[str, str | None]] | None = None,
    instructions: list[dict[str, str | None]] | None = None,
    adjustments: list[dict[str, str | None]] | None = None,
    document_id: int | None = None,
) -> Declaration:
    """Replace the shipment's declaration rows with the supplied values.

    Re-extraction is idempotent: goods/tax rows are rebuilt, never appended twice.
    A goods line the reviewer confirmed is never overwritten by re-extraction — it
    keeps its values and its goods-tax child rows. Child rows of rebuilt lines and
    the declaration-level children (taxes, licenses, instructions, adjustments) are
    always replaced wholesale.
    """
    from ..models import utcnow

    # Two declaration files of one shipment can be mirrored at the same time; the unique
    # key on shipment_id makes this an upsert instead of a duplicate insert.
    await session.execute(
        sqlite_insert(Declaration)
        .values(shipment_id=shipment_id, document_id=document_id)
        .on_conflict_do_nothing(index_elements=["shipment_id"])
    )
    declaration = await get_declaration(session, shipment_id)
    if declaration is None:  # pragma: no cover - the upsert above guarantees a row
        return

    for key, value in header.items():
        if not key.startswith("declaration."):
            continue
        column = key.split(".", 1)[1]
        if hasattr(declaration, column):
            setattr(declaration, column, value)
    declaration.document_id = document_id or declaration.document_id
    declaration.updated_at = utcnow()

    existing_goods = (
        await session.execute(select(GoodsLine).where(GoodsLine.declaration_id == declaration.id))
    ).scalars().all()
    confirmed = {line.seq: line for line in existing_goods if line.review_status == "CONFIRMED"}
    doomed_ids = [line.id for line in existing_goods if line.review_status != "CONFIRMED"]
    if doomed_ids:
        # Goods tax children must go before their lines: SQLite runs with
        # foreign_keys=ON and the delete would otherwise fail on the first re-extraction.
        await session.execute(delete(GoodsTaxLine).where(GoodsTaxLine.goods_line_id.in_(doomed_ids)))
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

    goods_taxes = goods_taxes or {}
    for seq, row in enumerate(goods, start=1):
        # A line the reviewer confirmed is never overwritten by a re-extraction: the new
        # values become candidates only, exactly like field-level overrides.
        if seq in confirmed:
            continue
        line = GoodsLine(declaration_id=declaration.id, document_id=document_id, seq=seq)
        for key, value in row.items():
            # payload-shaped goods dicts carry a nested "taxes" list; child rows only
            # arrive through the goods_taxes argument.
            if "." not in key:
                continue
            column = key.split(".", 1)[1]
            if hasattr(line, column):
                setattr(line, column, value)
        session.add(line)
        await session.flush()
        for t_seq, tax_row in enumerate(goods_taxes.get(seq, []), start=1):
            tax = GoodsTaxLine(goods_line_id=line.id, seq=t_seq)
            for key, value in tax_row.items():
                if "." not in key:
                    continue
                column = key.split(".", 1)[1]
                if hasattr(tax, column):
                    setattr(tax, column, value)
            session.add(tax)
    for seq, row in enumerate(taxes, start=1):
        tax = DeclarationTaxLine(declaration_id=declaration.id, seq=seq)
        for key, value in row.items():
            column = key.split(".", 1)[1]
            if hasattr(tax, column):
                setattr(tax, column, value)
        session.add(tax)

    for child_model, rows in (
        (DeclarationLicense, licenses or []),
        (DeclarationInstruction, instructions or []),
        (DeclarationAdjustment, adjustments or []),
    ):
        for existing in (
            await session.execute(select(child_model).where(child_model.declaration_id == declaration.id))
        ).scalars().all():
            await session.delete(existing)
        await session.flush()
        for seq, row in enumerate(rows, start=1):
            child = child_model(declaration_id=declaration.id, seq=seq)
            for key, value in row.items():
                column = key.split(".", 1)[1]
                if hasattr(child, column):
                    setattr(child, column, value)
            session.add(child)
    await session.flush()
    return declaration
