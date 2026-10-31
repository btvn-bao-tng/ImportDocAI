from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import LLMSettings, Settings
from app.main import create_app


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        llm=LLMSettings(
            api_key="test",
            base_url="http://example.invalid/v1",
            text_model="sonya",
            vision_model="vision_sonya",
        ),
        data_dir=tmp_path / "storage",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'preview.db'}",
    )


@pytest.fixture()
async def client(tmp_path: Path):
    app = create_app(_settings(tmp_path))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            yield ac


async def test_template_grid_reports_sheets_and_merged_ranges(client: AsyncClient):
    res = await client.get("/api/v1/templates/tokhai_import_v1/grid")
    assert res.status_code == 200, res.text
    body = res.json()

    sheets = {sheet["name"]: sheet for sheet in body["sheets"]}
    assert set(sheets) == {"TKN", "HANG"}
    assert sheets["TKN"]["rows"]
    assert sheets["TKN"]["merges"], "the declaration form relies on merged cells"
    assert all(":" in ref or ref.isalnum() for ref in sheets["TKN"]["merges"])


async def test_template_file_download_serves_the_workbook(client: AsyncClient):
    res = await client.get("/api/v1/templates/tokhai_import_v1/file")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert res.content[:2] == b"PK"


async def test_unknown_template_version_is_404(client: AsyncClient):
    assert (await client.get("/api/v1/templates/does_not_exist/grid")).status_code == 404
    assert (await client.get("/api/v1/templates/does_not_exist/file")).status_code == 404


async def test_document_grid_includes_merges_for_spreadsheets(client: AsyncClient, tmp_path: Path):
    from openpyxl import Workbook

    source = tmp_path / "form.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "TKN"
    ws["A1"] = "Tờ khai"
    ws.merge_cells("A1:C1")
    ws["A2"] = "Số tờ khai"
    ws["B2"] = 108299395961
    wb.save(source)

    shipment = await client.post("/api/v1/shipments", json={"code": "PREVIEW", "customer_name": "U&I"})
    shipment_id = shipment.json()["id"]
    with source.open("rb") as fh:
        res = await client.post(
            f"/api/v1/shipments/{shipment_id}/documents",
            files=[("files", ("form.xlsx", fh.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
    assert res.status_code == 201, res.text
    doc_id = res.json()["documents"][0]["document_id"]

    grid = await client.get(f"/api/v1/documents/{doc_id}/grid")
    assert grid.status_code == 200, grid.text
    sheet = grid.json()["sheets"][0]
    assert sheet["name"] == "TKN"
    assert sheet["merges"] == ["A1:C1"]
    assert sheet["rows"][0][0] == "Tờ khai"


async def _seed_declaration(client: AsyncClient, tmp_path: Path) -> int:
    """Create a shipment with a declaration row, goods line and field-level evidence."""
    from sqlalchemy import select
    from app.db import sessionmaker
    from app.models import Declaration, Document, ExtractedField, GoodsLine

    source = tmp_path / "to-khai.xlsx"
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.title = "TKN"
    wb.active["E4"] = "108299395961"
    wb.active["G8"] = "02/06/2026 11:00:52"
    wb.save(source)

    shipment = await client.post("/api/v1/shipments", json={"code": "PREVIEW-DATA", "customer_name": "U&I"})
    shipment_id = shipment.json()["id"]
    with source.open("rb") as fh:
        uploaded = await client.post(
            f"/api/v1/shipments/{shipment_id}/documents",
            files=[
                (
                    "files",
                    (
                        "ToKhaiHQ7N_QDTQ_108299395961 THONG QUAN.xlsx",
                        fh.read(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                )
            ],
        )
    doc_id = uploaded.json()["documents"][0]["document_id"]

    maker = sessionmaker()
    async with maker() as session:
        doc = await session.get(Document, doc_id)
        doc.doc_type = "CUSTOMS_DECLARATION"
        decl = Declaration(
            shipment_id=shipment_id,
            document_id=doc_id,
            declaration_no="108299395961",
            registered_at="02/06/2026 11:00:52",
            importer_name="CôNG TY TNHH Gỗ KINGSMAN VIệT NAM",
            review_status="CONFIRMED",
        )
        session.add(decl)
        await session.flush()
        goods = GoodsLine(
            declaration_id=decl.id,
            document_id=doc_id,
            seq=1,
            hs_code="44071100",
            description="Gỗ thông",
            quantity_1="4165",
            quantity_1_unit="KG",
        )
        session.add(goods)
        await session.flush()
        session.add_all(
            [
                ExtractedField(
                    document_id=doc_id,
                    field_key="declaration.declaration_no",
                    raw_value="108299395961",
                    normalized_value="108299395961",
                    page=1,
                    bbox=[0.12, 0.05, 0.31, 0.07],
                    cell_ref="TKN!E4",
                    provenance_type="TEXT_LAYER",
                    grounding_verified=True,
                    review_status="CONFIRMED",
                    critical=True,
                ),
                ExtractedField(
                    document_id=doc_id,
                    field_key="declaration.registered_at",
                    raw_value="02/06/2026 11:00:52",
                    normalized_value="02/06/2026 11:00:52",
                    page=1,
                    bbox=None,
                    cell_ref="TKN!G8",
                    provenance_type="TEXT_LAYER",
                    grounding_verified=True,
                    review_status="CONFIRMED",
                    critical=True,
                ),
                ExtractedField(
                    document_id=doc_id,
                    field_key="goods.hs_code",
                    raw_value="44071100",
                    normalized_value="44071100",
                    page=1,
                    bbox=None,
                    cell_ref="TKN!G149",
                    provenance_type="TEXT_LAYER",
                    grounding_verified=True,
                    review_status="UNREVIEWED",
                    critical=True,
                ),
            ]
        )
        await session.commit()
    return shipment_id


async def test_preview_fills_template_cells_and_attaches_evidence(client: AsyncClient, tmp_path: Path):
    shipment_id = await _seed_declaration(client, tmp_path)

    res = await client.get(f"/api/v1/shipments/{shipment_id}/template-preview")
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["version"] == "tokhai_import_v1"
    assert body["has_data"] is True
    tkn_cells = body["cells"]["TKN"]
    assert tkn_cells["E4"]["value"] == "108299395961"
    assert tkn_cells["E4"]["field"] == "declaration.declaration_no"
    evidence = tkn_cells["E4"]["evidence"]
    assert evidence["document_id"] > 0
    assert evidence["cell_ref"] == "TKN!E4"
    assert evidence["bbox"] == [0.12, 0.05, 0.31, 0.07]
    assert evidence["filename"].startswith("ToKhaiHQ7N")

    # The repeated header blocks on continuation pages get the same value.
    assert tkn_cells["E79"]["value"] == "108299395961"
    # Goods row lands in the first block (G149) and carries cell evidence.
    assert tkn_cells["G149"]["value"] == "44071100"
    assert tkn_cells["G149"]["evidence"]["cell_ref"] == "TKN!G149"

    # Template layout still ships with the payload so the UI renders one response.
    sheets = {sheet["name"]: sheet for sheet in body["sheets"]}
    assert sheets["TKN"]["merges"]


async def test_preview_is_blank_when_nothing_is_extracted(client: AsyncClient):
    shipment = await client.post("/api/v1/shipments", json={"code": "EMPTY", "customer_name": None})
    shipment_id = shipment.json()["id"]

    res = await client.get(f"/api/v1/shipments/{shipment_id}/template-preview")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["has_data"] is False
    assert body["cells"] == {}
    assert body["sheets"]


async def test_preview_unknown_shipment_is_404(client: AsyncClient):
    assert (await client.get("/api/v1/shipments/99999/template-preview")).status_code == 404
