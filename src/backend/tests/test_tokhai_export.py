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
EXPECTED = REPO / "data" / "sample" / "input batch2" / "ToKhaiHQ7N_QDTQ_108299395961 THONG QUAN.xlsx"

HEADER = {
    "declaration.declaration_no": "108299395961",
    "declaration.registered_at": "02/06/2026 11:00:52",
    "declaration.registered_changed_at": "12/06/2026 11:19:28",
    "declaration.declaration_kind": "A12  2 [ 4 ]",
    "declaration.check_code": "2",
    "declaration.customs_office": "SONGTHANBD",
    "declaration.representative_hs_code": "4407",
    "declaration.importer_tax_code": "3702474140",
    "declaration.importer_name": "CôNG TY TNHH Gỗ KINGSMAN VIệT NAM",
    "declaration.bill_of_lading_no": "140426HDMUBUEA32635400",
    "declaration.exporter_name": "BERTEC ENTERPRISE CO., LTD.",
    "declaration.exporter_country_code": "TW",
    "declaration.invoice_no": "A - CD2025121801-2",
    "declaration.invoice_date": "14/04/2026",
    "declaration.invoice_total": "23.230",
    "declaration.tax_exchange_rate": "26.143",
    "declaration.importer_postcode": "(+84)43",
    "declaration.package_count": "28",
    "declaration.gross_weight": "52.312",
}

GOODS = [
    {
        "goods.hs_code": "44071110",
        "goods.description": "Gỗ thông xẻ (Pinus Taeda)",
        "goods.quantity_1": "46.46",
        "goods.quantity_1_unit": "MTQ",
        "goods.invoice_amount": "11615",
        "goods.unit_price": "250",
        "goods.unit_price_currency": "USD",
        "goods.customs_value": "303650945",
        "goods.origin_country_code": "AR",
    },
    {
        "goods.hs_code": "44071110",
        "goods.description": "Gỗ thông xẻ (Pinus Taeda)",
        "goods.quantity_1": "46.46",
        "goods.quantity_1_unit": "MTQ",
        "goods.invoice_amount": "11615",
        "goods.unit_price": "250",
        "goods.unit_price_currency": "USD",
        "goods.customs_value": "303650945",
        "goods.origin_country_code": "AR",
    },
]


def _payload(goods=None, reviewed: bool = True) -> DeclarationPayload:
    return DeclarationPayload(
        header=dict(HEADER),
        goods=list(GOODS) if goods is None else goods,
        taxes=[],
        sources={},
        review_status="CONFIRMED" if reviewed else "UNREVIEWED",
        goods_reviewed=reviewed,
    )


def test_tokhai_template_differs_from_reference_only_in_owned_cells():
    """No stale data may survive, and no label/layout cell may be lost."""
    mapping, template_path = load_mapping("tokhai_import_v1")
    assert template_diff_problems(mapping, EXPECTED, template_path) == []


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
    assert tkn["AF1"].value == "1/3"
    assert tkn["AF139"].value == "3/3"
    assert tkn["C148"].value == "<01>"
    assert tkn["G149"].value == "44071110"
    assert tkn["C201"].value == "<02>"
    assert tkn["G202"].value == "44071110"
    assert outcome.mode == "FINAL"


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
    one = write_tokhai_export("LS2628", _payload([GOODS[0]]), export_dir=tmp_path)
    tkn = load_workbook(one.output_path)["TKN"]
    assert tkn["AF1"].value == "1/2"
    assert tkn["AF76"].value == "2/2"
    assert tkn["AF139"].value is None

    two = write_tokhai_export("LS2628", _payload(), export_dir=tmp_path)
    tkn2 = load_workbook(two.output_path)["TKN"]
    assert tkn2["AF1"].value == "1/3"
    assert tkn2["AF139"].value == "3/3"


def test_no_goods_is_never_final(tmp_path: Path):
    outcome = write_tokhai_export("LS2628", _payload([]), export_dir=tmp_path)
    assert outcome.mode == "DRAFT"
    assert any("không có dòng hàng" in w for w in outcome.warnings)


def test_derived_page_and_line_counts_are_written(tmp_path: Path):
    outcome = write_tokhai_export("LS2628", _payload(), export_dir=tmp_path)
    tkn = load_workbook(outcome.output_path)["TKN"]
    assert tkn["U75"].value == "3"
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
                        {key.split(".", 1)[1]: {"value": value} for key, value in GOODS[0].items()}
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
