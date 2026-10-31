from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Base,
    Declaration,
    Document,
    DocumentSection,
    ExtractedField,
    GoodsLine,
    LineItem,
    ResolvedField,
    Shipment,
)
from app.services.declaration import build_new_draft_payload
from app.services.export_excel import load_mapping
from app.services.export_tokhai import write_tokhai_export
from app.services.resolution import (
    load_consistent_snapshot,
    persist_if_snapshot_current,
    resolve_shipment,
    set_resolved_field,
)
from app.services.target_catalog import load_target_catalog


@pytest.fixture()
async def session(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'res.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        yield db
    await engine.dispose()


def _doc(session, shipment_id, name, doc_type, order=0):
    doc = Document(
        shipment_id=shipment_id,
        filename=name,
        file_ext=".pdf",
        storage_path=f"/tmp/{name}",
        checksum=f"{name}-{order}",
        doc_type=doc_type,
        doc_type_source="AI",
        status="EXTRACTED",
        page_count=1,
    )
    session.add(doc)
    return doc


def _field(session, doc, key, value, line_item=None, page=1, cell=None):
    field = ExtractedField(
        document_id=doc.id,
        line_item_id=line_item.id if line_item is not None else None,
        field_key=key,
        raw_value=value,
        normalized_value=value,
        page=page,
        cell_ref=cell,
        provenance_type="TEXT_LAYER",
        grounding_verified=True,
        evidence_state="FACT_VERIFIED",
        review_status="UNREVIEWED",
    )
    session.add(field)
    return field


async def _taeda_shipment(session, *, conflict_vessel=True):
    shipment = Shipment(code="TAEDA-PINE", status="PROCESSING")
    session.add(shipment)
    await session.flush()

    invoice = _doc(session, shipment.id, "TAEDA PINE COLA FINAL.pdf", "COMMERCIAL_INVOICE", 1)
    packing = _doc(session, shipment.id, "packing-list.pdf", "PACKING_LIST", 2)
    arrival = _doc(session, shipment.id, "Arrival Notice HDMUBUEA32635400.pdf", "ARRIVAL_NOTICE", 3)
    reference = _doc(session, shipment.id, "ToKhaiHQ7N_108299395961.xlsx", "CUSTOMS_DECLARATION_SCAN", 4)
    await session.flush()

    # Commercial invoice: header + two container-level rows.
    _field(session, invoice, "document.doc_number", "CD2025121801-2")
    _field(session, invoice, "document.issue_date", "14/04/2026")
    _field(session, invoice, "totals.total_amount", "23.230")
    _field(session, invoice, "totals.currency", "USD")
    _field(session, invoice, "transport.waybill_no", "HDMUBUEA32635400")
    _field(session, invoice, "transport.payment_method", "TTR")
    _field(session, invoice, "transport.invoice_terms", "CIF")
    _field(session, invoice, "transport.origin_country_code", "AR")
    _field(session, invoice, "party.buyer", "CÔNG TY TNHH GỖ KINGSMAN VIỆT NAM")
    _field(session, invoice, "party.buyer.tax_code", "3702474140")
    _field(session, invoice, "party.buyer.address", "Thửa đất số 334, Phường Tân Uyên, TP.HCM")
    _field(session, invoice, "party.buyer.phone", "0274 3800882")
    _field(session, invoice, "party.seller", "BERTEC ENTERPRISE CO., LTD.")
    _field(session, invoice, "party.seller.address", "NO.29, LN.496, CHANGCHUN ST.")
    _field(session, invoice, "party.seller.country_code", "TW")
    _field(session, invoice, "party.shipper", "ESTABLECIMIENTO LAS MARIAS SACIFA")
    _field(session, invoice, "transport.vessel", "HMM JAKARTA V0142E")

    for seq, container in ((1, "HMMU6849510"), (2, "KOCU4411578")):
        item = LineItem(document_id=invoice.id, seq=seq)
        # Mirrored the way `_persist` writes them, so tariff grouping can sum.
        item.quantity = Decimal("46.46")
        item.amount = Decimal("11615")
        item.currency = "USD"
        session.add(item)
        await session.flush()
        _field(session, invoice, "line.container_no", container, item)
        _field(session, invoice, "line.description", "TAEDA PINE KD LUMBER", item)
        _field(session, invoice, "line.hs_code", "44071100", item)
        _field(session, invoice, "line.quantity", "46.46", item)
        _field(session, invoice, "line.unit", "CBM", item)
        _field(session, invoice, "line.unit_price", "250", item)
        _field(session, invoice, "line.amount", "11615", item)
        _field(session, invoice, "line.currency", "USD", item)
        _field(session, invoice, "line.origin_country_code", "AR", item)

    # Packing list: shipment-level weights/packages.
    _field(session, packing, "transport.packages", "28")
    _field(session, packing, "transport.package_unit", "PK")
    _field(session, packing, "transport.gross_weight_kg", "52312")
    _field(session, packing, "transport.gross_weight_unit", "KGM")
    _field(session, packing, "transport.net_weight_kg", "51942")
    _field(session, packing, "transport.volume_cbm", "92.92")
    _field(session, packing, "transport.container_count", "2")

    # Arrival notice: ETA and a different vessel (transfer / changed vessel).
    _field(session, arrival, "transport.arrival_date", "01/06/2026")
    if conflict_vessel:
        _field(session, arrival, "transport.vessel", "NYK VESTA 094E")

    # Customs reference: must never populate a new draft.
    _field(session, reference, "declaration.declaration_no", "108299395961")
    _field(session, reference, "declaration.processing_department_code", "43IH")
    _field(session, reference, "declaration.registered_changed_at", "02/06/2026 12:33:00")
    _field(session, reference, "declaration.customs_office", "SONGTHANBD")
    _field(session, reference, "declaration.tax_exchange_rate", "26.143")
    await session.flush()
    return shipment, {"invoice": invoice, "packing": packing, "arrival": arrival, "reference": reference}


def _plan(report, key):
    return next(f for f in report.fields if f["target_key"] == key)


async def test_resolution_uses_business_input_and_excludes_customs_reference(session):
    shipment, _ = await _taeda_shipment(session)
    report = await resolve_shipment(session, shipment.id, "tokhai_import_v1", None)
    assert report.stale is False

    assert _plan(report, "declaration.invoice_no")["value"] == "CD2025121801-2"
    assert _plan(report, "declaration.invoice_date")["value"] == "14/04/2026"
    assert _plan(report, "declaration.importer_tax_code")["value"] == "3702474140"
    assert _plan(report, "declaration.package_count")["value"] == "28"
    assert _plan(report, "declaration.package_unit")["value"] == "PK"
    assert _plan(report, "declaration.gross_weight")["value"] == "52312"
    assert _plan(report, "declaration.container_count")["value"] == "2"
    assert _plan(report, "declaration.exporter_name")["value"] == "BERTEC ENTERPRISE CO., LTD."

    # Customs-generated values never leak into NEW_DRAFT, even when a reference file holds them.
    for key in (
        "declaration.declaration_no",
        "declaration.processing_department_code",
        "declaration.registered_changed_at",
        "declaration.customs_office",
        "declaration.tax_exchange_rate",
    ):
        plan = _plan(report, key)
        assert plan["origin"] == "CUSTOMS_GENERATED"
        assert plan["value"] is None, key
        assert plan["state"] == "MISSING", key


async def test_conflicting_vessel_is_ambiguous_not_last_write_wins(session):
    shipment, _ = await _taeda_shipment(session)
    report = await resolve_shipment(session, shipment.id, "tokhai_import_v1", None)
    vessel = _plan(report, "declaration.transport_vessel")
    assert vessel["state"] == "AMBIGUOUS"
    assert vessel["value"] is None
    evidence = (
        await session.execute(
            select(ResolvedField).where(
                ResolvedField.shipment_id == shipment.id,
                ResolvedField.target_key == "declaration.transport_vessel",
            )
        )
    ).scalars().one()
    assert evidence.review_status == "NEEDS_REVIEW"


async def test_two_invoice_container_rows_become_two_goods_lines(session):
    """The grouping policy decides row cardinality; it is not inferred from totals."""
    shipment, _ = await _taeda_shipment(session)
    report = await resolve_shipment(
        session, shipment.id, "tokhai_import_v1", None, group_policy="PER_INVOICE_CONTAINER_ROW"
    )
    assert report.goods_rows == 2
    assert _plan(report, "goods.1.hs_code")["value"] == "44071100"
    assert _plan(report, "goods.1.quantity_1")["value"] == "46.46"
    assert _plan(report, "goods.2.quantity_1")["value"] == "46.46"
    assert _plan(report, "goods.1.invoice_amount")["value"] == "11615"
    assert _plan(report, "goods.1.quantity_1_unit")["value"] == "MTQ"

    goods = (await session.execute(select(GoodsLine).order_by(GoodsLine.seq))).scalars().all()
    assert len(goods) == 2
    assert {g.hs_code for g in goods} == {"44071100"}
    declaration = (await session.execute(select(Declaration))).scalars().one()
    assert declaration.declaration_no is None
    assert declaration.processing_department_code is None


async def test_manual_edit_survives_rerun_and_reaches_export(session, tmp_path):
    shipment, _ = await _taeda_shipment(session)
    await resolve_shipment(session, shipment.id, "tokhai_import_v1", None)
    await set_resolved_field(
        session,
        shipment.id,
        "declaration.transport_vessel",
        "NYK VESTA 094E",
        template_version="tokhai_import_v1",
        reason="A/N là bản mới nhất",
    )
    await session.commit()

    report = await resolve_shipment(session, shipment.id, "tokhai_import_v1", None)
    assert report.stale is False
    vessel = _plan(report, "declaration.transport_vessel")
    assert vessel["value"] == "NYK VESTA 094E"
    # A manual decision with unchanged sources must not be flagged stale on rerun.
    assert vessel["state"] == "RESOLVED"
    assert vessel["method"] == "USER_EDITED"

    payload = await build_new_draft_payload(session, shipment.id, "tokhai_import_v1")
    outcome = write_tokhai_export("TAEDA-PINE", payload, export_dir=tmp_path)
    wb = load_workbook(outcome.output_path)
    mapping, _ = load_mapping("tokhai_import_v1")
    cells = {spec["field"]: ref for ref, spec in mapping["sheets"]["TKN"]["scalars"].items()}
    assert wb["TKN"][cells["declaration.transport_vessel"]].value == "NYK VESTA 094E"


async def test_review_edit_wins_over_new_extraction_value(session):
    shipment, _ = await _taeda_shipment(session)
    await resolve_shipment(session, shipment.id, "tokhai_import_v1", None)
    await set_resolved_field(
        session, shipment.id, "declaration.gross_weight", "52312",
        template_version="tokhai_import_v1", reason="xác nhận",
    )
    await session.commit()
    report = await resolve_shipment(session, shipment.id, "tokhai_import_v1", None)
    assert _plan(report, "declaration.gross_weight")["value"] == "52312"
    row = (
        await session.execute(
            select(ResolvedField).where(
                ResolvedField.shipment_id == shipment.id,
                ResolvedField.target_key == "declaration.gross_weight",
            )
        )
    ).scalars().one()
    assert row.review_status == "USER_EDITED"


async def test_stale_snapshot_is_rejected(session):
    shipment, docs = await _taeda_shipment(session)
    await session.flush()
    snapshot = await load_consistent_snapshot(session, shipment.id)
    catalog = load_target_catalog("tokhai_import_v1")

    _field(session, docs["packing"], "transport.packages", "29")
    await session.flush()

    wrote, message = await persist_if_snapshot_current(
        session, snapshot, catalog, {}, template_version="tokhai_import_v1"
    )
    assert wrote is False
    assert message == "snapshot_changed"


async def test_new_draft_export_leaves_audit_cells_blank_or_filled_from_input(session, tmp_path):
    """The audit's own cells: input-supported fields fill, customs fields stay blank."""
    shipment, _ = await _taeda_shipment(session)
    await resolve_shipment(session, shipment.id, "tokhai_import_v1", None)
    payload = await build_new_draft_payload(session, shipment.id, "tokhai_import_v1")
    outcome = write_tokhai_export("TAEDA-PINE", payload, export_dir=tmp_path)
    wb = load_workbook(outcome.output_path)
    tkn = wb["TKN"]

    # Input-supported (audit items 6-9, 19-24, 31-38)
    assert tkn["H10"].value == "3702474140"
    assert tkn["H14"].value.startswith("Thửa đất số 334")
    assert tkn["H16"].value == "0274 3800882"
    assert tkn["H25"].value == "NO.29, LN.496, CHANGCHUN ST."
    assert tkn["H27"].value == "TW"
    assert tkn["K36"].value == "28"
    assert tkn["P36"].value == "PK"
    assert tkn["K37"].value == "52312"
    assert tkn["P37"].value == "KGM"
    assert tkn["K38"].value == "2"
    assert tkn["J43"].value == "14/04/2026"
    assert tkn["J44"].value == "TTR"
    assert tkn["J45"].value == "CIF"
    assert tkn["P45"].value == "23.230"
    assert tkn["G149"].value == "44071100"
    assert tkn["V153"].value == "46.46"
    assert tkn["AE153"].value == "MTQ"
    assert tkn["I155"].value == "11615"
    assert tkn["V155"].value == "250"
    assert tkn["AC155"].value == "USD"
    assert tkn["X160"].value == "AR"
    assert tkn["G202"].value == "44071100"

    # Customs-generated / conflicting: blank, never guessed or leaked.
    assert tkn["E4"].value is None  # declaration number
    assert tkn["AE7"].value is None  # 43IH must not overwrite the processing department
    assert tkn["L7"].value is None  # customs office
    assert tkn["R8"].value is None  # registered change date
    assert tkn["Z34"].value is None  # vessel conflict
    assert tkn["AB70"].value is None  # tax exchange rate is customs-generated
    assert tkn["J46"].value is None  # total customs value needs rate + valuation rules
    assert tkn["AF75"].value == "2"  # goods line count derived from resolved rows


async def test_export_is_draft_until_critical_fields_confirmed(session, tmp_path):
    shipment, _ = await _taeda_shipment(session)
    await resolve_shipment(session, shipment.id, "tokhai_import_v1", None)
    payload = await build_new_draft_payload(session, shipment.id, "tokhai_import_v1")
    outcome = write_tokhai_export("TAEDA-PINE", payload, export_dir=tmp_path)
    assert outcome.mode == "DRAFT"
    assert payload.unreviewed_critical(), "unreviewed required fields must block FINAL"
    assert payload.critical_missing() == [], "input-supported required fields are all present"


async def test_final_export_is_possible_once_required_fields_are_confirmed(session, tmp_path):
    """Customs-generated fields must not make a legitimate FINAL impossible."""
    shipment, _ = await _taeda_shipment(session, conflict_vessel=False)
    await resolve_shipment(session, shipment.id, "tokhai_import_v1", None)
    catalog = load_target_catalog("tokhai_import_v1")
    required = set(catalog.required_keys())
    rows = (
        await session.execute(
            select(ResolvedField).where(ResolvedField.shipment_id == shipment.id)
        )
    ).scalars().all()
    goods_required_attrs = {key.split(".", 1)[1] for key in required if key.startswith("goods.")}
    confirmed = 0
    for row in rows:
        if row.value in (None, ""):
            continue
        needed = row.target_key in required
        if row.target_key.startswith("goods."):
            needed = row.target_key.split(".", 2)[2] in goods_required_attrs
        if needed:
            await set_resolved_field(
                session,
                shipment.id,
                row.target_key,
                None,
                template_version="tokhai_import_v1",
                confirm=True,
            )
            confirmed += 1
    assert confirmed > 10
    await session.commit()

    payload = await build_new_draft_payload(session, shipment.id, "tokhai_import_v1")
    assert payload.critical_missing() == []
    assert payload.unreviewed_critical() == []
    outcome = write_tokhai_export(
        "TAEDA-PINE",
        payload,
        export_dir=tmp_path,
        requested_mode="FINAL",
        required_keys=catalog.required_keys(),
    )
    assert outcome.mode == "FINAL", outcome.warnings
    wb = load_workbook(outcome.output_path)
    assert wb["TKN"]["I157"].value is None  # customs value is not required and not invented


async def test_customs_generated_field_cannot_be_typed_into_new_draft(session):
    from app.services.resolution import CustomsFieldEdit

    shipment, _ = await _taeda_shipment(session)
    await resolve_shipment(session, shipment.id, "tokhai_import_v1", None)
    blocked_keys = (
        "declaration.declaration_no",
        "declaration.processing_department_code",
        # Numbered goods rows resolve to the un-numbered catalog entry.
        "goods.1.customs_value",
        "goods.2.import_duty_rate",
        "goods.1.tax_quantity",
    )
    for key in blocked_keys:
        with pytest.raises(CustomsFieldEdit):
            await set_resolved_field(
                session,
                shipment.id,
                key,
                "LEAKED",
                template_version="tokhai_import_v1",
            )
    payload = await build_new_draft_payload(session, shipment.id, "tokhai_import_v1")
    assert payload.header["declaration.declaration_no"] is None
    assert payload.header["declaration.processing_department_code"] is None
    assert all("customs_value" not in row for row in payload.goods)


async def test_bundle_sections_decide_invoice_fields_and_goods_rows(session):
    """A mixed PDF is typed by its dominant page; each fact still follows its section."""
    shipment = Shipment(code="TAEDA-BUNDLE", status="PROCESSING")
    session.add(shipment)
    await session.flush()
    doc = _doc(session, shipment.id, "TAEDA PINE COLA FINAL.pdf", "COMMERCIAL_INVOICE", 1)
    await session.flush()
    invoice_section = DocumentSection(
        document_id=doc.id, page_start=0, page_end=1,
        section_type="COMMERCIAL_INVOICE", classification_state="CLASSIFIED",
    )
    packing_section = DocumentSection(
        document_id=doc.id, page_start=2, page_end=2,
        section_type="PACKING_LIST", classification_state="CLASSIFIED",
    )
    session.add_all([invoice_section, packing_section])
    await session.flush()

    def sectioned(key, value, section, item=None):
        field = _field(session, doc, key, value, item)
        field.section_id = section.id
        field.section_type = section.section_type
        return field

    sectioned("document.doc_number", "INV-1", invoice_section)
    sectioned("document.issue_date", "14/04/2026", invoice_section)
    # A packing-list page must not become the invoice number.
    sectioned("document.doc_number", "PL-999", packing_section)

    item_invoice = LineItem(document_id=doc.id, seq=1)
    item_packing = LineItem(document_id=doc.id, seq=2)
    session.add_all([item_invoice, item_packing])
    await session.flush()
    for item, section, container, amount in (
        (item_invoice, invoice_section, "HMMU6849510", "11615"),
        (item_packing, packing_section, "KOCU4411578", "999"),
    ):
        sectioned("line.container_no", container, section, item)
        sectioned("line.description", "TAEDA PINE KD LUMBER", section, item)
        sectioned("line.hs_code", "44071100", section, item)
        sectioned("line.quantity", "46.46", section, item)
        sectioned("line.unit", "CBM", section, item)
        sectioned("line.unit_price", "250", section, item)
        sectioned("line.amount", amount, section, item)
        sectioned("line.currency", "USD", section, item)
        sectioned("line.origin_country_code", "AR", section, item)
    await session.flush()

    report = await resolve_shipment(session, shipment.id, "tokhai_import_v1", None)
    assert _plan(report, "declaration.invoice_no")["value"] == "INV-1"
    assert report.goods_rows == 1, "only invoice-section rows are customs candidates"
    assert _plan(report, "goods.1.invoice_amount")["value"] == "11615"


async def test_per_tariff_item_merges_same_hs_rows_with_summed_amounts(session):
    """Two container rows of the same HS code become one tariff line with the sums.

    Per-row candidates alone cannot express a merged quantity: equal inputs collapse
    to one row's value (understating the declaration) and differing inputs go
    AMBIGUOUS. The merged row must carry quantity/amount computed from both.
    """
    shipment, _ = await _taeda_shipment(session, conflict_vessel=False)
    report = await resolve_shipment(
        session, shipment.id, "tokhai_import_v1", None, group_policy="PER_TARIFF_ITEM"
    )
    assert report.goods_rows == 1, "same-HS container rows merge into one tariff line"
    assert _plan(report, "goods.1.quantity_1")["value"] == "92.92", "46.46 + 46.46"
    assert _plan(report, "goods.1.invoice_amount")["value"] == "23230", "11615 + 11615"
    assert _plan(report, "goods.1.quantity_1")["method"] == "CALCULATED"
    assert _plan(report, "goods.1.quantity_1")["state"] == "RESOLVED"


async def test_merged_quantity_with_disagreeing_units_needs_review(session):
    """Summing quantities expressed in different units is not a quantity of anything."""
    shipment, _ = await _taeda_shipment(session, conflict_vessel=False)
    unit = (
        await session.execute(
            select(ExtractedField).where(
                ExtractedField.field_key == "line.unit", ExtractedField.normalized_value == "CBM"
            )
        )
    ).scalars().first()
    unit.normalized_value = "KG"
    await session.flush()

    report = await resolve_shipment(
        session, shipment.id, "tokhai_import_v1", None, group_policy="PER_TARIFF_ITEM"
    )
    quantity = _plan(report, "goods.1.quantity_1")
    assert quantity["state"] == "NEEDS_REVIEW"
    assert "different units" in (quantity["reason"] or "")
    assert _plan(report, "goods.1.quantity_1_unit")["state"] == "AMBIGUOUS"


async def test_merged_sum_with_a_missing_source_row_needs_review(session):
    """A partial sum would understate the declaration: show it, but flag it."""
    shipment, _ = await _taeda_shipment(session, conflict_vessel=False)
    amount = (
        await session.execute(
            select(ExtractedField).where(ExtractedField.field_key == "line.amount")
        )
    ).scalars().first()
    await session.delete(amount)
    await session.flush()

    report = await resolve_shipment(
        session, shipment.id, "tokhai_import_v1", None, group_policy="PER_TARIFF_ITEM"
    )
    invoice_amount = _plan(report, "goods.1.invoice_amount")
    assert invoice_amount["state"] == "NEEDS_REVIEW"
    assert "row lacks line.amount" in (invoice_amount["reason"] or "")


async def test_merged_sum_with_an_unparseable_source_value_needs_review(session):
    """A value the LineItem mirror cannot parse must not count toward a final sum."""
    shipment, _ = await _taeda_shipment(session, conflict_vessel=False)
    amount = (
        await session.execute(
            select(ExtractedField).where(ExtractedField.field_key == "line.amount")
        )
    ).scalars().first()
    amount.normalized_value = "FOC"
    await session.flush()

    report = await resolve_shipment(
        session, shipment.id, "tokhai_import_v1", None, group_policy="PER_TARIFF_ITEM"
    )
    invoice_amount = _plan(report, "goods.1.invoice_amount")
    assert invoice_amount["state"] == "NEEDS_REVIEW"
    assert "could not parse" in (invoice_amount["reason"] or "")


class ListReplyLLM:
    """A gateway that answers the gap prompt with a JSON array, not an object."""

    settings = None

    def extraction_model(self):
        return "vision_sonya"

    async def complete_json_validated(self, prompt, schema_name, schema, validator, **kwargs):
        return validator([{"key": "declaration.invoice_no", "value": "X", "evidence_id": 1}])


async def test_semantic_gap_llm_replying_with_a_list_is_ignored(session):
    """A non-object reply must be discarded, not crash the resolve endpoint."""
    shipment, _ = await _taeda_shipment(session)
    report = await resolve_shipment(session, shipment.id, "tokhai_import_v1", ListReplyLLM())
    assert report.stale is False
    assert _plan(report, "declaration.invoice_no")["value"] == "CD2025121801-2"


async def test_patch_without_value_does_not_confirm_a_field(session):
    """A null-value PATCH is a no-op for review: it must never flip CONFIRMED.

    `unreviewed_critical` gates FINAL exports on review_status, so a silent confirm
    would let an unreviewed value pass the FINAL gate.
    """
    shipment, _ = await _taeda_shipment(session)
    await resolve_shipment(session, shipment.id, "tokhai_import_v1", None)
    row = await set_resolved_field(
        session,
        shipment.id,
        "declaration.gross_weight",
        None,
        template_version="tokhai_import_v1",
        reason="không có giá trị mới",
    )
    assert row.review_status == "UNREVIEWED"
    assert row.value == "52312", "a null patch must not clear or confirm the value"
    assert row.state == "RESOLVED" and row.resolution_method == "DIRECT", (
        "a note-only patch must not recompute the state or claim the value as user-edited"
    )

    payload = await build_new_draft_payload(session, shipment.id, "tokhai_import_v1")
    assert "declaration.gross_weight" in payload.unreviewed_critical()


async def test_goods_rows_without_sources_are_blanked_on_reresolution(session, tmp_path):
    """Stale numbered goods rows must not keep printing in a NEW_DRAFT.

    Re-extraction (or a different grouping policy) can leave fewer goods rows; the
    system-owned rows that no longer have a source are blanked, while a human decision
    on such a row is preserved for the reviewer.
    """
    shipment, docs = await _taeda_shipment(session, conflict_vessel=False)
    await resolve_shipment(session, shipment.id, "tokhai_import_v1", None)

    # Simulate a re-extraction that only produced one container row.
    from sqlalchemy import delete as sa_delete

    second = (
        await session.execute(select(LineItem).where(LineItem.seq == 2, LineItem.document_id == docs["invoice"].id))
    ).scalars().one()
    await session.execute(sa_delete(ExtractedField).where(ExtractedField.line_item_id == second.id))
    await session.delete(second)
    await session.flush()

    report = await resolve_shipment(session, shipment.id, "tokhai_import_v1", None)
    assert report.goods_rows == 1
    rows = {r.target_key: r for r in await _rows(session, shipment.id)}
    assert rows["goods.2.hs_code"].value is None, "system-owned orphan must be blanked"
    assert rows["goods.2.hs_code"].state == "MISSING"
    assert rows["goods.1.hs_code"].value == "44071100", "surviving rows keep their values"

    payload = await build_new_draft_payload(session, shipment.id, "tokhai_import_v1")
    assert len([row for row in payload.goods if any(v for k, v in row.items() if k != "taxes")]) == 1

    # The blanked row must not consume a printed line block or inflate the count.
    outcome = write_tokhai_export("ORPHAN-GOODS", payload, export_dir=tmp_path)
    wb = load_workbook(outcome.output_path)
    assert wb["TKN"]["AF75"].value == "1", "AF75 counts only rows with values"
    assert not [w for w in outcome.warnings if "Round-trip lệch" in w], (
        "the round-trip self-check must not report phantom corruption for blanked buckets"
    )

    # A human decision on the orphaned row is still possible and still survives.
    await set_resolved_field(
        session, shipment.id, "goods.2.description", "GIỮ NGUYÊN",
        template_version="tokhai_import_v1", reason="người dùng đã sửa",
    )
    await resolve_shipment(session, shipment.id, "tokhai_import_v1", None)
    rows = {r.target_key: r for r in await _rows(session, shipment.id)}
    assert rows["goods.2.description"].value == "GIỮ NGUYÊN"


async def _rows(session, shipment_id):
    return (
        await session.execute(
            select(ResolvedField).where(
                ResolvedField.shipment_id == shipment_id,
                ResolvedField.template_version == "tokhai_import_v1",
            )
        )
    ).scalars().all()
