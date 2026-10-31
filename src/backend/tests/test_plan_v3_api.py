from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import load_workbook

from app.config import LLMSettings, Settings
from app.main import create_app


class NoSuggestionLLM:
    """Resolution-only stub: never suggests a value, so the test exercises the backend."""

    settings = SimpleNamespace(max_tokens=32000)

    def extraction_model(self):
        return "vision_sonya"

    async def complete_json_validated(self, prompt, name, schema, validator, **kwargs):
        return validator({})


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        llm=LLMSettings(
            api_key="test",
            base_url="http://example.invalid/v1",
            text_model="sonya",
            vision_model="vision_sonya",
        ),
        data_dir=tmp_path / "storage",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}",
    )


@pytest.fixture()
async def client(tmp_path: Path):
    from app import deps as deps_module

    app = create_app(_settings(tmp_path))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            deps_module._state["llm"] = NoSuggestionLLM()
            yield ac
    deps_module._state.clear()


async def _seed_into(shipment_id: int) -> dict[str, int]:
    """Create the TAEDA-like documents under the API shipment and return their ids."""
    from app.db import session_scope
    from app.models import Document, ExtractedField, LineItem

    async with session_scope() as session:
        invoice = Document(
            shipment_id=shipment_id, filename="TAEDA PINE COLA FINAL.pdf", file_ext=".pdf",
            storage_path="/tmp/invoice.pdf", checksum="inv", doc_type="COMMERCIAL_INVOICE",
            doc_type_source="AI", status="EXTRACTED", page_count=1,
        )
        packing = Document(
            shipment_id=shipment_id, filename="packing-list.pdf", file_ext=".pdf",
            storage_path="/tmp/pl.pdf", checksum="pl", doc_type="PACKING_LIST",
            doc_type_source="AI", status="EXTRACTED", page_count=1,
        )
        reference = Document(
            shipment_id=shipment_id, filename="TOKHAI_108299395961.xlsx", file_ext=".xlsx",
            storage_path="/tmp/ref.xlsx", checksum="ref", doc_type="CUSTOMS_DECLARATION_SCAN",
            doc_type_source="AI", status="EXTRACTED", page_count=1,
        )
        session.add_all([invoice, packing, reference])
        await session.flush()

        def field(doc, key, value, item=None):
            session.add(
                ExtractedField(
                    document_id=doc.id,
                    line_item_id=item.id if item else None,
                    field_key=key,
                    raw_value=value,
                    normalized_value=value,
                    page=1,
                    provenance_type="TEXT_LAYER",
                    grounding_verified=True,
                    evidence_state="FACT_VERIFIED",
                    review_status="UNREVIEWED",
                )
            )

        field(invoice, "document.doc_number", "CD2025121801-2")
        field(invoice, "document.issue_date", "14/04/2026")
        field(invoice, "totals.total_amount", "23.230")
        field(invoice, "transport.waybill_no", "HDMUBUEA32635400")
        field(invoice, "transport.payment_method", "TTR")
        field(invoice, "transport.invoice_terms", "CIF")
        field(invoice, "party.buyer", "CÔNG TY TNHH GỖ KINGSMAN VIỆT NAM")
        field(invoice, "party.buyer.tax_code", "3702474140")
        field(invoice, "party.seller", "BERTEC ENTERPRISE CO., LTD.")
        field(invoice, "party.seller.country_code", "TW")
        for seq in (1, 2):
            item = LineItem(document_id=invoice.id, seq=seq)
            session.add(item)
            await session.flush()
            field(invoice, "line.container_no", f"HMMU684951{seq}", item)
            field(invoice, "line.description", "TAEDA PINE KD LUMBER", item)
            field(invoice, "line.hs_code", "44071100", item)
            field(invoice, "line.quantity", "46.46", item)
            field(invoice, "line.unit", "CBM", item)
            field(invoice, "line.unit_price", "250", item)
            field(invoice, "line.amount", "11615", item)
            field(invoice, "line.currency", "USD", item)
            field(invoice, "line.origin_country_code", "AR", item)

        field(packing, "transport.packages", "28")
        field(packing, "transport.package_unit", "PK")
        field(packing, "transport.gross_weight_kg", "52312")
        field(packing, "transport.gross_weight_unit", "KGM")
        field(packing, "transport.container_count", "2")

        # The reference snapshot must never populate a new draft.
        field(reference, "declaration.declaration_no", "108299395961")
        field(reference, "declaration.processing_department_code", "43IH")
        await session.flush()
        return {"invoice": invoice.id, "packing": packing.id, "reference": reference.id}


async def test_review_edit_reaches_the_exported_workbook(client: AsyncClient, tmp_path: Path):
    res = await client.post("/api/v1/shipments", json={"code": "TAEDA-PINE"})
    shipment_id = res.json()["id"]
    ids = await _seed_into(shipment_id)

    resolved = await client.post(f"/api/v1/shipments/{shipment_id}/resolve", json={})
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["state"] == "RESOLVED"
    assert body["goods_rows"] == 2

    declaration = await client.get(f"/api/v1/shipments/{shipment_id}/declaration")
    assert declaration.status_code == 200
    fields = {f["target_key"]: f for f in declaration.json()["fields"]}
    assert fields["declaration.declaration_no"]["origin"] == "CUSTOMS_GENERATED"
    assert fields["declaration.declaration_no"]["value"] is None
    assert fields["declaration.processing_department_code"]["value"] is None
    assert fields["declaration.gross_weight"]["value"] == "52312"
    assert fields["declaration.gross_weight"]["evidence"][0]["role"] == "PRIMARY"

    # One review surface: edit the resolved field, then export reads that same value.
    revision = fields["declaration.gross_weight"]["revision"]
    patched = await client.patch(
        f"/api/v1/shipments/{shipment_id}/resolved-fields/declaration.gross_weight",
        json={"value": "52312.5", "reason": "cân lại", "expected_revision": revision},
    )
    assert patched.status_code == 200, patched.text

    stale = await client.patch(
        f"/api/v1/shipments/{shipment_id}/resolved-fields/declaration.gross_weight",
        json={"value": "1", "expected_revision": revision},
    )
    assert stale.status_code == 409

    # The projection (legacy preview consumer) is refreshed in the same transaction.
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Declaration

    async with session_scope() as session:
        declaration_row = (await session.execute(select(Declaration))).scalars().one()
        assert declaration_row.gross_weight == "52312.5"

    export = await client.post(
        f"/api/v1/shipments/{shipment_id}/exports",
        json={"template_version": "tokhai_import_v1", "mode": "NEW_DRAFT"},
    )
    assert export.status_code == 201, export.text
    payload = export.json()
    download = await client.get(f"/api/v1/exports/{payload['id']}/download")
    out = tmp_path / "export.xlsx"
    out.write_bytes(download.content)
    tkn = load_workbook(out)["TKN"]
    assert tkn["K37"].value == "52312.5"
    assert tkn["E4"].value is None
    assert tkn["AE7"].value is None
    assert tkn["K36"].value == "28"
    assert tkn["K38"].value == "2"
    assert tkn["AF75"].value == "2"


async def test_reference_mirror_is_explicit_and_isolated(client: AsyncClient):
    res = await client.post("/api/v1/shipments", json={"code": "TAEDA-REF"})
    shipment_id = res.json()["id"]
    ids = await _seed_into(shipment_id)

    missing = await client.post(
        f"/api/v1/shipments/{shipment_id}/exports",
        json={"template_version": "tokhai_import_v1", "mode": "REFERENCE_MIRROR"},
    )
    assert missing.status_code == 422

    mirror = await client.post(
        f"/api/v1/shipments/{shipment_id}/exports",
        json={
            "template_version": "tokhai_import_v1",
            "mode": "REFERENCE_MIRROR",
            "reference_document_id": ids["reference"],
        },
    )
    assert mirror.status_code == 201, mirror.text
    assert mirror.json()["mode"] == "REFERENCE_MIRROR"


async def test_final_export_is_refused_while_review_is_open(client: AsyncClient):
    res = await client.post("/api/v1/shipments", json={"code": "TAEDA-FINAL"})
    shipment_id = res.json()["id"]
    await _seed_into(shipment_id)
    await client.post(f"/api/v1/shipments/{shipment_id}/resolve", json={})

    final = await client.post(
        f"/api/v1/shipments/{shipment_id}/exports",
        json={"template_version": "tokhai_import_v1", "mode": "FINAL"},
    )
    assert final.status_code == 422
    assert "FINAL" in final.json()["detail"]


async def test_customs_generated_field_patch_is_rejected(client: AsyncClient):
    res = await client.post("/api/v1/shipments", json={"code": "TAEDA-GUARD"})
    shipment_id = res.json()["id"]
    await _seed_into(shipment_id)
    await client.post(f"/api/v1/shipments/{shipment_id}/resolve", json={})

    for key in ("declaration.declaration_no", "declaration.processing_department_code"):
        blocked = await client.patch(
            f"/api/v1/shipments/{shipment_id}/resolved-fields/{key}",
            json={"value": "LEAKED"},
        )
        assert blocked.status_code == 422, (key, blocked.text)

    declaration = await client.get(f"/api/v1/shipments/{shipment_id}/declaration")
    fields = {f["target_key"]: f for f in declaration.json()["fields"]}
    assert fields["declaration.declaration_no"]["value"] is None
    assert fields["declaration.processing_department_code"]["value"] is None


async def test_reference_comparison_keeps_snapshots_separate(client: AsyncClient):
    res = await client.post("/api/v1/shipments", json={"code": "TAEDA-CMP"})
    shipment_id = res.json()["id"]
    ids = await _seed_into(shipment_id)
    await client.post(f"/api/v1/shipments/{shipment_id}/resolve", json={})

    comparison = await client.get(
        f"/api/v1/shipments/{shipment_id}/reference-comparison",
        params={"reference_document_id": ids["reference"]},
    )
    assert comparison.status_code == 200
    rows = {r["target_key"]: r for r in comparison.json()["rows"]}
    assert rows["declaration.declaration_no"]["draft"] is None
    assert rows["declaration.declaration_no"]["reference"] == "108299395961"
    assert rows["declaration.declaration_no"]["same"] is False


async def test_stale_resolution_blocks_the_export(client: AsyncClient, monkeypatch):
    """If sources changed during resolve, no workbook may be produced from old values."""
    from app.api import exports as exports_module
    from app.services.resolution import ResolutionReport

    res = await client.post("/api/v1/shipments", json={"code": "TAEDA-STALE"})
    shipment_id = res.json()["id"]
    await _seed_into(shipment_id)

    async def stale_resolve(*args, **kwargs):
        return ResolutionReport(
            shipment_id=shipment_id,
            template_version="tokhai_import_v1",
            snapshot_hash="old",
            state="STALE",
            stale=True,
            message="snapshot_changed",
        )

    monkeypatch.setattr(exports_module, "resolve_shipment", stale_resolve)
    blocked = await client.post(
        f"/api/v1/shipments/{shipment_id}/exports",
        json={"template_version": "tokhai_import_v1", "mode": "NEW_DRAFT"},
    )
    assert blocked.status_code == 409
    assert "chưa xuất file" in blocked.json()["detail"]
