
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
    ExtractedField,
    GoodsLine,
    GoodsTaxLine,
)
from .export_excel import FieldSource
from .export_tokhai import DeclarationPayload
from .fields import ALL_FIELD_SPECS
from .target_catalog import load_target_catalog

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


async def build_declaration_payload_from_fields(session: AsyncSession, shipment_id: int) -> DeclarationPayload:
    """Fallback payload built straight from extracted fields when no Declaration row exists.

    The export then still returns the template file with every matching extracted
    `declaration.*` value filled in (DRAFT mode); goods lines are only ever sourced
    from a real customs-declaration extraction. The first document that states a
    field wins, with customs-declaration documents ranked ahead of the rest.
    """
    docs = (
        await session.execute(select(Document).where(Document.shipment_id == shipment_id).order_by(Document.id))
    ).scalars().all()
    # doc_type is nullable (never-classified documents); treat missing as no match.
    ranked = sorted(docs, key=lambda d: (not (d.doc_type or "").startswith("CUSTOMS_DECLARATION"), d.id))

    header: dict[str, str | None] = {}
    sources: dict[str, FieldSource] = {}
    for doc in ranked:
        fields = (
            await session.execute(
                select(ExtractedField).where(
                    ExtractedField.document_id == doc.id,
                    ExtractedField.field_key.like("declaration.%"),
                    ExtractedField.line_item_id.is_(None),
                )
            )
        ).scalars().all()
        for f in fields:
            if f.field_key in header:
                continue
            value = f.normalized_value if f.normalized_value is not None else f.raw_value
            if value in (None, ""):
                continue
            header[f.field_key] = value
            sources[f.field_key] = FieldSource(
                document_filename=doc.filename,
                document_id=doc.id,
                page=f.page,
                cell_ref=f.cell_ref,
                provenance_type=f.provenance_type,
                review_status=f.review_status,
                grounding_verified=f.grounding_verified,
            )
    return DeclarationPayload(header=header, goods=[], taxes=[], sources=sources)


async def build_new_draft_payload(
    session: AsyncSession,
    shipment_id: int,
    template_version: str = "tokhai_import_v1",
) -> DeclarationPayload:
    """Build the NEW_DRAFT payload from canonical `ResolvedField` rows only.

    This is the single export source the reviewer edits: a value shown in the review
    surface is exactly the value written to the workbook. Unresolved and ambiguous
    fields stay blank, so a DRAFT cannot invent or inherit customs reference data.
    """
    from ..models import DeclarationFieldSource, ResolvedField

    rows = (
        await session.execute(
            select(ResolvedField).where(
                ResolvedField.shipment_id == shipment_id,
                ResolvedField.template_version == template_version,
            )
        )
    ).scalars().all()

    header: dict[str, str | None] = {}
    sources: dict[str, FieldSource] = {}
    resolved_by_key = {row.target_key: row for row in rows}

    # One query for primary evidence and one for the documents it points at: the
    # review/export path runs per shipment, not per field.
    evidence_by_field: dict[int, DeclarationFieldSource] = {}
    docs_by_id: dict[int, Document] = {}
    if rows:
        evidence_rows = (
            await session.execute(
                select(DeclarationFieldSource).where(
                    DeclarationFieldSource.resolved_field_id.in_([row.id for row in rows]),
                    DeclarationFieldSource.evidence_role == "PRIMARY",
                )
            )
        ).scalars().all()
        for evidence in evidence_rows:
            evidence_by_field.setdefault(evidence.resolved_field_id, evidence)
        doc_ids = {e.document_id for e in evidence_rows if e.document_id}
        if doc_ids:
            for doc in (await session.execute(select(Document).where(Document.id.in_(doc_ids)))).scalars().all():
                docs_by_id[doc.id] = doc

    for key, spec in ALL_FIELD_SPECS.items():
        if not key.startswith("declaration."):
            continue
        row = resolved_by_key.get(key)
        allowed = row is not None and row.origin != "CUSTOMS_GENERATED" and row.document_role != "CUSTOMS_REFERENCE"
        header[key] = row.value if allowed and row.state in {"RESOLVED", "NEEDS_REVIEW", "STALE_REVIEW"} else None
        if row is not None and header[key] not in (None, "") and row.id is not None:
            evidence = evidence_by_field.get(row.id)
            if evidence is not None:
                doc = docs_by_id.get(evidence.document_id) if evidence.document_id else None
                sources[key] = FieldSource(
                    document_filename=doc.filename if doc else None,
                    document_id=evidence.document_id or 0,
                    page=evidence.page,
                    cell_ref=evidence.cell_ref,
                    provenance_type="EXCEL_GRID" if doc and doc.file_ext in {".xls", ".xlsx", ".xlsm"} else "VLM",
                    review_status=row.review_status,
                    grounding_verified=evidence.evidence_state == "FACT_VERIFIED",
                )

    goods_by_seq: dict[int, dict[str, str | None]] = {}
    for row in rows:
        if not row.target_key.startswith("goods."):
            continue
        parts = row.target_key.split(".")
        if len(parts) < 3:
            continue
        try:
            seq = int(parts[1])
        except ValueError:
            continue
        attr = parts[2]
        bucket = goods_by_seq.setdefault(seq, {})
        allowed = row.origin != "CUSTOMS_GENERATED" and row.document_role != "CUSTOMS_REFERENCE"
        bucket[f"goods.{attr}"] = (
            row.value if allowed and row.state in {"RESOLVED", "NEEDS_REVIEW", "STALE_REVIEW"} else None
        )
    goods = [dict(goods_by_seq[seq]) for seq in sorted(goods_by_seq)]

    def is_confirmed(row) -> bool:
        return row.review_status == "CONFIRMED" and row.state != "STALE_REVIEW"

    declared = [row for row in rows if row.value not in (None, "")]
    all_confirmed = bool(declared) and all(is_confirmed(row) for row in declared)
    goods_rows = [row for row in rows if row.target_key.startswith("goods.") and row.value not in (None, "")]
    goods_reviewed = bool(goods_rows) and all(is_confirmed(row) for row in goods_rows)
    return DeclarationPayload(
        header=header,
        goods=goods,
        taxes=[],
        sources=sources,
        review_status="CONFIRMED" if all_confirmed else "UNREVIEWED",
        goods_reviewed=goods_reviewed,
        required_keys=load_target_catalog(template_version).required_keys(),
        confirmed_keys={row.target_key for row in rows if is_confirmed(row)},
    )


async def build_reference_mirror_payload(
    session: AsyncSession,
    shipment_id: int,
    document_id: int,
) -> DeclarationPayload:
    """Mirror ONE named customs reference snapshot for audit. Never blended."""
    doc = await session.get(Document, document_id)
    if doc is None or doc.shipment_id != shipment_id:
        raise ValueError("reference document không thuộc shipment này")
    if (doc.doc_type or "") not in {"CUSTOMS_DECLARATION", "CUSTOMS_DECLARATION_SCAN"}:
        raise ValueError("chứng từ được chọn không phải tờ khai tham chiếu")

    fields = (
        await session.execute(
            select(ExtractedField).where(
                ExtractedField.document_id == document_id,
                ExtractedField.line_item_id.is_(None),
            )
        )
    ).scalars().all()
    header: dict[str, str | None] = {}
    sources: dict[str, FieldSource] = {}
    for f in fields:
        if not f.field_key.startswith("declaration."):
            continue
        value = f.normalized_value if f.normalized_value is not None else f.raw_value
        if value in (None, ""):
            continue
        header[f.field_key] = value
        sources[f.field_key] = FieldSource(
            document_filename=doc.filename,
            document_id=doc.id,
            page=f.page,
            cell_ref=f.cell_ref,
            provenance_type=f.provenance_type,
            review_status=f.review_status,
            grounding_verified=f.grounding_verified,
        )
    return DeclarationPayload(
        header=header,
        goods=[],
        taxes=[],
        sources=sources,
        review_status=doc.doc_type or "REFERENCE",
    )


async def build_reference_comparison(
    session: AsyncSession,
    shipment_id: int,
    template_version: str,
    reference_document_id: int | None = None,
) -> dict:
    """Compare the new draft against one reference snapshot, field by field."""
    from ..models import ResolvedField

    draft = await build_new_draft_payload(session, shipment_id, template_version)
    reference = (
        await build_reference_mirror_payload(session, shipment_id, reference_document_id)
        if reference_document_id
        else None
    )
    rows = (
        await session.execute(
            select(ResolvedField).where(
                ResolvedField.shipment_id == shipment_id,
                ResolvedField.template_version == template_version,
            )
        )
    ).scalars().all()
    draft_values = {r.target_key: r.value for r in rows}
    reference_values = reference.header if reference is not None else {}
    keys = sorted(set(draft_values) | set(reference_values))
    comparison = []
    for key in keys:
        mine = draft_values.get(key)
        theirs = reference_values.get(key)
        comparison.append(
            {
                "target_key": key,
                "draft": mine,
                "reference": theirs,
                "same": mine == theirs,
            }
        )
    return {"template_version": template_version, "reference_document_id": reference_document_id, "rows": comparison,
            "reference_available": reference is not None}


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
