from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

from app.services.export_excel import ExportError, load_mapping
from app.services.export_tokhai import (
    DeclarationPayload,
    parse_vn_number,
    template_diff_problems,
    write_tokhai_export,
)

REPO = Path(__file__).resolve().parents[3]
EXPECTED = REPO / "ToKhaiHQ7N_QDTQ_108299395961 THONG QUAN.xlsx"

HEADER = {
    "declaration.declaration_no": "108299395961",
    "declaration.registered_at": "02/06/2026 11:00:52",
    "declaration.registered_changed_at": "12/06/2026 11:19:28",
    "declaration.declaration_kind": "A12  2 [ 4 ]",
    "declaration.check_code": "2",
    "declaration.customs_office": "SONGTHANBD",
    "declaration.processing_department_code": "00",
    "declaration.representative_hs_code": "4407",
    "declaration.importer_tax_code": "3702474140",
    "declaration.importer_name": "CôNG TY TNHH Gỗ KINGSMAN VIệT NAM",
    "declaration.bill_of_lading_no": "140426HDMUBUEA32635400",
    "declaration.exporter_name": "BERTEC ENTERPRISE CO., LTD.",
    "declaration.exporter_country_code": "TW",
    "declaration.invoice_no": "A - CD2025121801-2",
    "declaration.invoice_date": "14/04/2026",
    "declaration.invoice_terms": "A - CIF - USD - ",
    "declaration.invoice_total": "23.230",
    "declaration.tax_exchange_rate": "26.143",
    "declaration.tax_deadline_code": "D",
    "declaration.taxpayer_code": "1",
    "declaration.tax_payment_classification": "A",
    "declaration.user_ref_number": "03039",
    "declaration.customs_directive_code": "B",
    "declaration.importer_postcode": "(+84)43",
    "declaration.package_count": "28",
    "declaration.gross_weight": "52.312",
}

LICENSES = [
    {"license.code": "VU01", "license.number": "38508/26/0201/KDTV"},
    {"license.code": "VV02", "license.number": "26/029/BKGNK"},
]

INSTRUCTIONS = [
    {
        "instruction.date": "02/06/2026",
        "instruction.name": "Hàng kiểm dịch tại cửa khẩu",
        "instruction.content": "Hàng kiểm dịch tại cửa khẩu",
    },
    {
        "instruction.date": "12/06/2026",
        "instruction.name": "Bổ sung kết quả KDTV",
        "instruction.content": "Bổ sung kết quả KDTV số 38508/26/0201/KDTV",
    },
]

GOODS_TAXES = [
    {
        "goods_tax.name": "Thuế GTGT",
        "goods_tax.rate_code": "VB245",
        "goods_tax.base": "303.650.945",
        "goods_tax.rate": "8%",
        "goods_tax.amount": "24.292.075,6",
    }
]

GOODS = [
    {
        "goods.hs_code": "44071110",
        "goods.private_management_code": "143IH",
        "goods.description": "Gỗ thông xẻ (Pinus Taeda)",
        "goods.quantity_1": "46.46",
        "goods.quantity_1_unit": "MTQ",
        "goods.invoice_amount": "11615",
        "goods.unit_price": "250",
        "goods.unit_price_currency": "USD",
        "goods.customs_value": "303650945",
        "goods.import_duty_rate_code": "A",
        "goods.import_duty_rate": "0%",
        "goods.origin_country_code": "AR",
        "taxes": [dict(GOODS_TAXES[0])],
    },
    {
        "goods.hs_code": "44071110",
        "goods.private_management_code": "143IH",
        "goods.description": "Gỗ thông xẻ (Pinus Taeda)",
        "goods.quantity_1": "46.46",
        "goods.quantity_1_unit": "MTQ",
        "goods.invoice_amount": "11615",
        "goods.unit_price": "250",
        "goods.unit_price_currency": "USD",
        "goods.customs_value": "303650945",
        "goods.import_duty_rate_code": "A",
        "goods.import_duty_rate": "0%",
        "goods.origin_country_code": "AR",
        "taxes": [],
    },
]


def _payload(goods=None, reviewed: bool = True) -> DeclarationPayload:
    return DeclarationPayload(
        header=dict(HEADER),
        goods=list(GOODS) if goods is None else goods,
        taxes=[{"tax.name": "V  Thuế GTGT", "tax.amount": "48.584.151", "tax.line_count": "2", "tax.currency": "VND"}],
        sources={},
        review_status="CONFIRMED" if reviewed else "UNREVIEWED",
        goods_reviewed=reviewed,
        licenses=[dict(l) for l in LICENSES],
        instructions=[dict(i) for i in INSTRUCTIONS],
        adjustments=[],
    )


def test_tokhai_template_differs_from_reference_only_in_owned_cells():
    """No stale data may survive, and no label/layout cell may be lost.

    The audit found the reference workbook's exporter address (`U25`, `H26`, `U26`)
    copied into the shipped template. Those cells are now enumerated as data cells and
    must be blank; every other difference from the reference is still a release blocker.
    """
    from app.services.template_sanitize import TEMPLATE_DATA_CELLS, identifier_leaks

    mapping, template_path = load_mapping("tokhai_import_v1")
    problems = template_diff_problems(mapping, EXPECTED, template_path)
    sanitized = {f"TKN!{cell}" for cell in TEMPLATE_DATA_CELLS["tokhai_import_v1"]["TKN"]}
    problems = [p for p in problems if p.split(" ")[0] not in sanitized]
    assert problems == []
    assert identifier_leaks(template_path, "tokhai_import_v1") == []


def test_tokhai_template_keeps_hang_hidden():
    _, template_path = load_mapping("tokhai_import_v1")
    wb = load_workbook(template_path)
    assert wb["HANG"].sheet_state == "veryHidden"


def test_tokhai_export_writes_header_and_both_goods_blocks(tmp_path: Path):
    outcome = write_tokhai_export("LS2628", _payload(), export_dir=tmp_path)
    wb = load_workbook(outcome.output_path)
    tkn = wb["TKN"]
    assert tkn["E4"].value == "108299395961"
    assert tkn["AA3"].value == "*108299395961*"
    assert tkn["H11"].value == "CôNG TY TNHH Gỗ KINGSMAN VIệT NAM"
    assert tkn["J45"].value == "A - CIF - USD - "
    assert tkn["AF1"].value == "1/4"
    assert tkn["AF139"].value == "3/4"
    assert tkn["C148"].value == "<01>"
    assert tkn["G149"].value == "44071110"
    assert tkn["Q149"].value == "143IH"
    assert tkn["H159"].value == "A"
    assert tkn["I159"].value == "0%"
    assert tkn["C201"].value == "<02>"
    assert tkn["G202"].value == "44071110"
    assert outcome.mode == "FINAL"


def test_tokhai_export_writes_licenses_instructions_and_goods_taxes(tmp_path: Path):
    """Child blocks: Giấy phép nhập khẩu, chỉ thị hải quan, thuế và thu khác của dòng hàng."""
    outcome = write_tokhai_export("LS2628", _payload(), export_dir=tmp_path)
    tkn = load_workbook(outcome.output_path)["TKN"]
    # licenses (slot 1: D50/F50, slot 2: P50/R50; slots 3-5 untouched)
    assert tkn["D50"].value == "VU01"
    assert tkn["F50"].value == "38508/26/0201/KDTV"
    assert tkn["P50"].value == "VV02"
    assert tkn["R50"].value == "26/029/BKGNK"
    assert tkn["AA50"].value is None
    assert tkn["AC50"].value is None
    # customs instructions (rows 90 and 93, stride 3)
    assert tkn["D90"].value == "02/06/2026"
    assert tkn["I90"].value == "Hàng kiểm dịch tại cửa khẩu"
    assert tkn["R90"].value == "Hàng kiểm dịch tại cửa khẩu"
    assert tkn["D93"].value == "12/06/2026"
    assert tkn["I93"].value == "Bổ sung kết quả KDTV"
    assert tkn["R93"].value == "Bổ sung kết quả KDTV số 38508/26/0201/KDTV"
    # goods tax slot 1 of goods block 1 (H167..I171) and its mirror in block 2
    assert tkn["H167"].value == "Thuế GTGT"
    assert tkn["W167"].value == "VB245"
    assert tkn["I168"].value == "303.650.945"
    assert tkn["I169"].value == "8%"
    assert tkn["I170"].value == "24.292.075,6"
    assert tkn["I171"].value is None
    assert tkn["H172"].value is None
    # header scalars from the new mapping
    assert tkn["AE7"].value == "00"
    assert tkn["X73"].value == "D"
    assert tkn["AF73"].value == "1"
    assert tkn["AF74"].value == "A"
    assert tkn["AC87"].value == "03039"
    assert tkn["I88"].value == "B"


def test_tokhai_processing_department_repeats_on_every_page(tmp_path: Path):
    outcome = write_tokhai_export("LS2628", _payload(), export_dir=tmp_path)
    tkn = load_workbook(outcome.output_path)["TKN"]
    for ref in ("AE7", "AE82", "AE145", "AE198"):
        assert tkn[ref].value == "00", ref


def test_tokhai_header_repeats_on_continuation_pages(tmp_path: Path):
    outcome = write_tokhai_export("LS2628", _payload(), export_dir=tmp_path)
    tkn = load_workbook(outcome.output_path)["TKN"]
    for ref in ("E79", "E142", "E195"):
        assert tkn[ref].value == "108299395961", ref
    for ref in ("G83", "G146", "G199"):
        assert tkn[ref].value == "02/06/2026 11:00:52", ref
    assert tkn["L82"].value == "SONGTHANBD"


def test_tokhai_hang_keeps_labels_and_writes_only_verified_cell(tmp_path: Path):
    """HANG has no data rows in any reference sample; export must not overwrite its labels."""
    outcome = write_tokhai_export("LS2628", _payload(), export_dir=tmp_path)
    hang = load_workbook(outcome.output_path)["HANG"]
    assert hang.sheet_state == "veryHidden"
    assert hang["C18"].value == "Thuế nhập khẩu"
    assert hang["S21"].value == "Mã áp dụng thuế tuyệt đối"
    assert hang["D25"].value == "Danh mục miễn thuế nhập khẩu"
    assert hang["C12"].value == "Mô tả hàng hóa"
    assert hang["G17"].value == "44071110"
    assert hang["G18"].value == "44071110"


def test_tokhai_missing_critical_forces_draft(tmp_path: Path):
    header = dict(HEADER)
    header["declaration.invoice_no"] = None
    payload = _payload()
    payload.header = header
    outcome = write_tokhai_export("LS2628", payload, export_dir=tmp_path)
    assert outcome.mode == "DRAFT"
    assert any("declaration.invoice_no" in m for m in outcome.missing)


def test_tokhai_capacity_overflow_fails_loudly(tmp_path: Path):
    goods = GOODS + [{**GOODS[0], "goods.hs_code": "44071190"}]
    try:
        write_tokhai_export("LS2628", _payload(goods), export_dir=tmp_path)
    except ExportError as exc:
        assert "dòng hàng" in str(exc)
    else:
        raise AssertionError("expected ExportError")


def test_tokhai_matches_expected_workbook_cells(tmp_path: Path):
    """Every header value present in both files must match the reference workbook."""
    outcome = write_tokhai_export("LS2628", _payload(), export_dir=tmp_path)
    ours = load_workbook(outcome.output_path)["TKN"]
    ref = load_workbook(EXPECTED)["TKN"]
    checked = 0
    for ref_cell, spec in load_mapping("tokhai_import_v1")[0]["sheets"]["TKN"]["scalars"].items():
        key = spec["field"]
        if HEADER.get(key) in (None, ""):
            continue
        expected = ref[ref_cell].value
        if expected in (None, ""):
            continue
        assert str(ours[ref_cell].value) == str(expected), f"TKN!{ref_cell} ({key})"
        checked += 1
    assert checked >= 12


def test_unreviewed_critical_fields_force_draft(tmp_path: Path):
    """Presence is not enough for FINAL — critical fields must be confirmed by a human."""
    outcome = write_tokhai_export("LS2628", _payload(reviewed=False), export_dir=tmp_path)
    assert outcome.mode == "DRAFT"
    assert any("Chưa xác nhận" in w for w in outcome.warnings)


def test_page_counters_follow_number_of_goods_lines(tmp_path: Path):
    """References print 4 pages for 2 goods: header + fixed continuation + 2 goods pages."""
    one = write_tokhai_export("LS2628", _payload([GOODS[0]]), export_dir=tmp_path)
    tkn = load_workbook(one.output_path)["TKN"]
    assert tkn["AF1"].value == "1/3"
    assert tkn["AF76"].value == "2/3"
    assert tkn["AF139"].value == "3/3"
    assert tkn["AF192"].value is None

    two = write_tokhai_export("LS2628", _payload(), export_dir=tmp_path)
    tkn2 = load_workbook(two.output_path)["TKN"]
    assert tkn2["AF1"].value == "1/4"
    assert tkn2["AF139"].value == "3/4"
    assert tkn2["AF192"].value == "4/4"


def test_no_goods_is_never_final(tmp_path: Path):
    outcome = write_tokhai_export("LS2628", _payload([]), export_dir=tmp_path)
    assert outcome.mode == "DRAFT"
    assert any("không có dòng hàng" in w for w in outcome.warnings)


def test_derived_page_and_line_counts_are_written(tmp_path: Path):
    outcome = write_tokhai_export("LS2628", _payload(), export_dir=tmp_path)
    tkn = load_workbook(outcome.output_path)["TKN"]
    assert tkn["U75"].value == "4"
    assert tkn["AF75"].value == "2"


def test_declaration_banner_uses_number_digits(tmp_path: Path):
    outcome = write_tokhai_export("LS2628", _payload(), export_dir=tmp_path)
    assert load_workbook(outcome.output_path)["TKN"]["AA3"].value == "*108299395961*"


def test_parse_vn_number_handles_grouping_and_ambiguity():
    assert parse_vn_number("607.301.890") == 607301890
    assert parse_vn_number("46,46") == Decimal("46.46")
    assert parse_vn_number("23.230") == 23230
    assert parse_vn_number("1.234,56") == Decimal("1234.56")
    assert parse_vn_number("250") == 250
    assert parse_vn_number("") is None
    assert parse_vn_number("abc") is None


def test_round_trip_detects_corrupted_cell(tmp_path: Path):
    outcome = write_tokhai_export("LS2628", _payload(), export_dir=tmp_path)
    wb = load_workbook(outcome.output_path)
    wb["TKN"]["H11"] = "SOMEONE ELSE LTD"
    tampered = tmp_path / "tampered.xlsx"
    wb.save(tampered)
    mapping, _ = load_mapping("tokhai_import_v1")
    from app.services.export_tokhai import verify_tokhai_round_trip

    problems = verify_tokhai_round_trip(tampered, mapping, _payload())
    assert any("H11" in p for p in problems)


async def test_pipeline_persists_customs_declaration_rows(tmp_path):
    """Extraction rows for a Tờ khai must land in declarations/goods_lines, not only extracted_fields."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, Declaration, Document, GoodsLine, Shipment
    from app.services.extract import ExtractedRow
    from app.services.pipeline import _persist_declaration

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'decl.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        shipment = Shipment(code="TKN1")
        session.add(shipment)
        await session.flush()
        doc = Document(
            shipment_id=shipment.id,
            filename="tokhai.pdf",
            file_ext=".pdf",
            storage_path="",
            checksum="x" * 64,
            doc_type="CUSTOMS_DECLARATION_SCAN",
            status="PROCESSING",
        )
        session.add(doc)
        await session.flush()

        def row(key, value, line_index=None):
            return ExtractedRow(
                field_key=key,
                raw_value=value,
                normalized_value=value,
                unit=None,
                page=1,
                bbox=None,
                cell_ref=None,
                confidence=None,
                provenance_type="VLM",
                grounding_verified=False,
                review_status="UNREVIEWED",
                critical=False,
                line_index=line_index,
            )

        await _persist_declaration(
            session,
            doc,
            [
                row("declaration.declaration_no", "108299395961"),
                row("declaration.importer_name", "KINGS MAN"),
                row("goods.hs_code", "44071110", 1),
                row("goods.quantity_1", "46,46", 1),
                row("tax.name", "Thuế GTGT", 1),
            ],
        )
        await session.commit()

        declaration = (await session.execute(__import__("sqlalchemy").select(Declaration))).scalars().first()
        assert declaration is not None
        assert declaration.declaration_no == "108299395961"
        goods = (await session.execute(__import__("sqlalchemy").select(GoodsLine))).scalars().all()
        assert len(goods) == 1 and goods[0].hs_code == "44071110"
    await engine.dispose()


async def test_declaration_flows_from_extraction_to_export(tmp_path):
    """End-to-end: stub VLM -> extract -> persist -> payload -> export (DRAFT until reviewed)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, Declaration, Document, GoodsLine, Shipment
    from app.services.declaration import build_declaration_payload
    from app.services.extract import ExtractionService
    from app.services.pipeline import _persist_declaration

    class StubLLM:
        settings = type("S", (), {"max_tokens": 32000})()

        def extraction_model(self):
            return "vision_sonya"

        async def complete_json_validated(self, prompt, name, schema, validator, **kwargs):
            return validator(
                {
                    "declaration": {
                        key.split(".", 1)[1]: {"value": value} for key, value in HEADER.items()
                    },
                    "goods": [
                        {
                            key.split(".", 1)[1]: {"value": value}
                            for key, value in GOODS[0].items()
                            if "." in key
                        }
                    ],
                    "taxes": [{"name": {"value": "Thuế GTGT"}, "amount": {"value": "48.584.151"}}],
                }
            )

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'e2e.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    rows = await ExtractionService(StubLLM()).extract(
        "CUSTOMS_DECLARATION_SCAN", "tokhai.pdf", Path("tokhai.pdf"), ["Số tờ khai 108299395961 " * 5], None
    )
    assert sum(r.field_key.startswith("declaration.") for r in rows) >= 10
    assert sum(r.field_key.startswith("goods.") for r in rows) >= 5
    assert sum(r.field_key.startswith("tax.") for r in rows) >= 1

    async with maker() as session:
        shipment = Shipment(code="TKN9")
        session.add(shipment)
        await session.flush()
        doc = Document(
            shipment_id=shipment.id,
            filename="tokhai.pdf",
            file_ext=".pdf",
            storage_path="",
            checksum="b" * 64,
            doc_type="CUSTOMS_DECLARATION_SCAN",
            status="PROCESSING",
        )
        session.add(doc)
        await session.flush()
        await _persist_declaration(session, doc, rows)
        await session.commit()

        payload = await build_declaration_payload(session, shipment.id)
        assert payload is not None
        assert payload.header["declaration.declaration_no"] == "108299395961"
        assert payload.goods[0]["goods.hs_code"] == "44071110"
        # extraction alone never yields FINAL: critical fields are unreviewed
        assert payload.unreviewed_critical()

        outcome = write_tokhai_export("TKN9", payload, export_dir=tmp_path)
        assert outcome.mode == "DRAFT"

        declaration = (await session.execute(__import__("sqlalchemy").select(Declaration))).scalars().first()
        declaration.review_status = "CONFIRMED"
        for line in (await session.execute(__import__("sqlalchemy").select(GoodsLine))).scalars().all():
            line.review_status = "CONFIRMED"
        await session.commit()

        reviewed = await build_declaration_payload(session, shipment.id)
        final = write_tokhai_export("TKN9", reviewed, export_dir=tmp_path)
        assert final.mode == "FINAL", final.warnings
    await engine.dispose()


async def test_reextraction_preserves_confirmed_goods_edits(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, GoodsLine, Shipment
    from app.services.declaration import upsert_declaration

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 're.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        shipment = Shipment(code="TKN-R")
        session.add(shipment)
        await session.flush()

        await upsert_declaration(session, shipment.id, {"declaration.declaration_no": "1"}, [{"goods.hs_code": "44071110"}], [])
        line = (await session.execute(__import__("sqlalchemy").select(GoodsLine))).scalars().first()
        line.hs_code = "44071190"
        line.review_status = "CONFIRMED"
        await session.commit()

        await upsert_declaration(session, shipment.id, {"declaration.declaration_no": "1"}, [{"goods.hs_code": "44071110"}], [])
        await session.commit()
        lines = (await session.execute(__import__("sqlalchemy").select(GoodsLine))).scalars().all()
        assert len(lines) == 1
        assert lines[0].hs_code == "44071190"
        assert lines[0].review_status == "CONFIRMED"
    await engine.dispose()


async def test_reextraction_with_goods_taxes_replaces_child_rows(tmp_path):
    """Re-extracting a declaration whose goods have tax children must not hit the FK wall."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, GoodsLine, GoodsTaxLine, Shipment
    from app.services.declaration import upsert_declaration

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'retax.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    select = __import__("sqlalchemy").select
    async with maker() as session:
        shipment = Shipment(code="TKN-RT")
        session.add(shipment)
        await session.flush()

        goods_taxes = {1: [dict(GOODS_TAXES[0])]}
        await upsert_declaration(
            session, shipment.id, {"declaration.declaration_no": "1"}, [dict(GOODS[0])], [], goods_taxes=goods_taxes
        )
        await session.commit()
        taxes = (await session.execute(select(GoodsTaxLine))).scalars().all()
        assert len(taxes) == 1 and taxes[0].name == "Thuế GTGT"

        # second extraction rebuilds the goods line; its tax children must be replaced, not crash
        await upsert_declaration(
            session, shipment.id, {"declaration.declaration_no": "1"}, [dict(GOODS[0])], [], goods_taxes=goods_taxes
        )
        await session.commit()
        lines = (await session.execute(select(GoodsLine))).scalars().all()
        taxes = (await session.execute(select(GoodsTaxLine))).scalars().all()
        assert len(lines) == 1
        assert len(taxes) == 1 and taxes[0].amount == "24.292.075,6"
    await engine.dispose()


def test_child_family_overflow_warns_instead_of_silent_truncation(tmp_path: Path):
    payload = _payload()
    payload.licenses = [dict(l) for l in LICENSES] * 3  # 6 > 5 slots
    payload.instructions = [dict(i) for i in INSTRUCTIONS] * 6  # 12 > 10 rows
    payload.adjustments = [{"adjustment.code_name": "FR"}] * 6  # 6 > 5 rows
    payload.taxes = [{"tax.name": "Thuế GTGT"}] * 7  # 7 > 6 rows
    payload.goods[0]["taxes"] = [dict(GOODS_TAXES[0])] * 6  # 6 > 5 slots
    outcome = write_tokhai_export("LS2628", payload, export_dir=tmp_path)
    assert any("giấy phép" in w for w in outcome.warnings)
    assert any("khoản điều chỉnh" in w for w in outcome.warnings)
    assert any("chỉ thị" in w for w in outcome.warnings)
    assert any("thuế khai tổng" in w for w in outcome.warnings)
    assert any("khoản thuế" in w for w in outcome.warnings)


def test_goods_line_with_only_taxes_is_treated_as_no_goods(tmp_path: Path):
    payload = _payload(goods=[{"taxes": [dict(GOODS_TAXES[0])]}])
    outcome = write_tokhai_export("LS2628", payload, export_dir=tmp_path)
    assert outcome.mode == "DRAFT"
    assert any("không có dòng hàng" in w for w in outcome.warnings)


async def test_persist_never_creates_phantom_line_items_for_child_families(tmp_path):
    """goods_tax/license/instruction/adjustment rows must not spawn LineItems; only goods lines do."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, Document, ExtractedField, LineItem, Shipment
    from app.services.extract import ExtractedRow
    from app.services.pipeline import _persist

    def row(key, value, line_index=None):
        return ExtractedRow(
            field_key=key, raw_value=value, normalized_value=value, unit=None, page=None, bbox=None,
            cell_ref=None, confidence=None, provenance_type="CELL", grounding_verified=True,
            review_status="UNREVIEWED", critical=False, line_index=line_index,
        )

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'phantom.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    select = __import__("sqlalchemy").select
    async with maker() as session:
        shipment = Shipment(code="TKN-P")
        session.add(shipment)
        await session.flush()
        doc = Document(shipment_id=shipment.id, filename="t.xlsx", file_ext=".xlsx",
                       storage_path="", checksum="c" * 64, doc_type="CUSTOMS_DECLARATION_SCAN", status="PROCESSING")
        session.add(doc)
        await session.flush()

        await _persist(session, doc, [
            row("goods.hs_code", "44071110", 1),
            row("goods.hs_code", "44071110", 2),
            row("goods_tax.name", "Thuế GTGT", 101),
            row("goods_tax.amount", "24.292.075,6", 101),
            row("goods_tax.name", "Thuế TTDB", 202),
            row("tax.name", "V  Thuế GTGT", 1),
            row("license.code", "VU01", 1),
            row("license.number", "38508/26/0201/KDTV", 1),
            row("instruction.date", "02/06/2026", 1),
            row("adjustment.code_name", "FR", 1),
        ])
        await session.commit()

        items = (await session.execute(select(LineItem).order_by(LineItem.seq))).scalars().all()
        assert [i.seq for i in items] == [1, 2]  # goods lines only — no 101/202 phantoms
        fields = (await session.execute(select(ExtractedField))).scalars().all()
        goods_line_ids = {i.id for i in items}
        # goods_tax rows link to the goods line 1's LineItem (101 // 100 == 1)
        tax_linked = [f for f in fields if f.field_key == "goods_tax.name"]
        assert len(tax_linked) == 2
        assert all(f.line_item_id in goods_line_ids for f in tax_linked)
        # family rows without a LineItem stay document-level
        family = [f for f in fields if f.field_key.startswith(("license.", "instruction.", "adjustment."))]
        assert len(family) == 4 and all(f.line_item_id is None for f in family)
    await engine.dispose()


def test_round_trip_covers_counters_banner_tax_and_hang(tmp_path: Path):
    from app.services.export_tokhai import verify_tokhai_round_trip

    payload = _payload()
    payload.taxes = [{"tax.name": "Thuế GTGT", "tax.amount": "48.584.151"}]
    outcome = write_tokhai_export("LS2628", payload, export_dir=tmp_path)
    mapping, _ = load_mapping("tokhai_import_v1")
    assert verify_tokhai_round_trip(outcome.output_path, mapping, payload) == []

    wb = load_workbook(outcome.output_path)
    for sheet, cell in [("TKN", "AF1"), ("TKN", "AA3"), ("TKN", "H68"), ("HANG", "G17")]:
        wb[sheet][cell] = "CORRUPT"
    tampered = tmp_path / "tampered2.xlsx"
    wb.save(tampered)
    problems = verify_tokhai_round_trip(tampered, mapping, payload)
    joined = " ".join(problems)
    for token in ("AF1", "AA3", "H68", "G17"):
        assert token in joined, (token, problems)


def test_round_trip_failure_demotes_final_to_draft(tmp_path, monkeypatch):
    import app.services.export_tokhai as module

    monkeypatch.setattr(module, "verify_tokhai_round_trip", lambda *a, **k: ["lệch giả lập"])
    outcome = write_tokhai_export("LS2628", _payload(), export_dir=tmp_path)
    assert outcome.mode == "DRAFT"
    assert "_DRAFT_" in outcome.output_path.name
