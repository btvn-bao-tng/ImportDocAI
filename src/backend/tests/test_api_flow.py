from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import LLMSettings, Settings
from app.main import create_app



def _settings(tmp_path: Path) -> Settings:
    return Settings(
        llm=LLMSettings(api_key="test", base_url="http://example.invalid/v1", text_model="sonya", vision_model="vision_sonya"),
        data_dir=tmp_path / "storage",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}",
    )


BILL_TEXT_HINTS = {"YMJAI245332642": "transport.waybill_no", "YMMU6509610": "transport.container_no", "YM CONTINENT": "transport.vessel"}


class StubLLM:
    """Deterministic stand-in for the BioTuring gateway."""

    settings = SimpleNamespace(max_tokens=32000)

    def extraction_model(self):
        return "vision_sonya"

    async def probe(self):
        return {"json_schema": False, "json_object": False, "vision": True, "strategy": "json_prompt"}

    async def complete_json_validated(self, prompt, schema_name, schema, validator, image_png=None, max_attempts=3, max_tokens=None, model_override=None):
        if schema_name == "classification":
            body = prompt.split("Nội dung", 1)[-1].upper()
            doc_type = "TRANSPORT_DOCUMENT"
            if "COMMERCIAL INVOICE" in body:
                doc_type = "COMMERCIAL_INVOICE"
            elif "PACKING LIST" in body:
                doc_type = "PACKING_LIST"
            elif "SALES CONTRACT" in prompt.upper() or "合同" in body:
                doc_type = "SALES_CONTRACT_PO"
            return validator({"doc_type": doc_type, "confidence": 0.95, "reason": "stub"})

        data = {
            "doc_number": {"value": "LS2628", "source_quote": "LS2628"},
            "issue_date": {"value": "2026-09-09", "source_quote": "2026-09-09"},
            "vessel": {"value": "YM CONTINENT", "source_quote": "YM CONTINENT"},
            "voyage": {"value": "2608S", "source_quote": "2608S"},
            "waybill_no": {"value": "YMJAI245332642", "source_quote": "YMJAI245332642"},
            "container_no": {"value": "YMMU6509610", "source_quote": "YMMU6509610"},
            "seal_no": {"value": "YMAW339171", "source_quote": "YMAW339171"},
            "packages": {"value": "20", "source_quote": "20"},
            "gross_weight": {"value": "26400.000", "source_quote": "26400.000"},
            "net_weight": {"value": "26000.000", "source_quote": "26000.000"},
            "volume": {"value": "38.030", "source_quote": "38.030"},
            "currency": {"value": "USD", "source_quote": "USD"},
            "total_amount": {"value": "67175.4552", "source_quote": "TOTAL"},
            "invented_field": {"value": "SHOULD_NOT_APPEAR", "source_quote": "not in document at all"},
            "number_locale": "period_decimal",
            "parties": [
                {"name": "DALIAN CHUNLING WOOD INDUSTRY CO., LTD.", "role": "SHIPPER"},
                {"name": "GREEN WAY COMPANY LIMITED", "role": "CONSIGNEE"},
            ],
            "line_items": [
                {
                    "description": {"value": "OAK VENEER (4.0x 190 x 1900)MM", "source_quote": "OAK VENEER"},
                    "quantity": {"value": "38.03", "source_quote": "38.030"},
                    "amount": {"value": "67175.4552", "source_quote": "TOTAL"},
                }
            ],
        }
        return validator(data)


async def run_pending_jobs() -> None:
    """Simulate the durable worker process claiming and executing queued jobs."""
    from app import deps as deps_module
    from app.services.job_runner import run_process_job
    from app.worker import _finish_job, claim_job

    while True:
        job_id = await claim_job()
        if job_id is None:
            return
        failed = not await run_process_job(job_id, deps_module._state["llm"])
        await _finish_job(job_id, failed=failed)


@pytest.fixture()
async def client(tmp_path: Path, monkeypatch):
    """App wired to a stub LLM so the whole pipeline runs without network access."""
    from app import deps as deps_module

    settings = _settings(tmp_path)
    stub = StubLLM()


    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            deps_module._state["llm"] = stub
            yield ac
    deps_module._state.clear()


async def test_full_phase1_flow(client: AsyncClient, monkeypatch):
    sample = Path(__file__).resolve().parents[3] / "data" / "sample" / "input" / "bill.pdf"

    res = await client.post("/api/v1/shipments", json={"code": "LS2628", "customer_name": "U&I"})
    assert res.status_code == 201, res.text
    shipment_id = res.json()["id"]

    with open(sample, "rb") as fh:
        files = [("files", ("bill.pdf", fh.read(), "application/pdf"))]
    res = await client.post(f"/api/v1/shipments/{shipment_id}/documents", files=files)
    assert res.status_code == 201, res.text
    assert res.json()["documents"][0]["status"] == "UPLOADED"
    doc_id = res.json()["documents"][0]["document_id"]

    # duplicate upload by checksum is detected (AT-01)
    with open(sample, "rb") as fh:
        files = [("files", ("bill-copy.pdf", fh.read(), "application/pdf"))]
    res = await client.post(f"/api/v1/shipments/{shipment_id}/documents", files=files)
    assert res.json()["documents"][0]["status"] == "DUPLICATE"

    res = await client.post(f"/api/v1/shipments/{shipment_id}/process", json={})
    assert res.status_code == 202, res.text
    job_id = res.json()["job_id"]
    queued = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert queued["status"] == "PENDING"

    await run_pending_jobs()

    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert job["status"] == "DONE", job

    extractions = (await client.get(f"/api/v1/shipments/{shipment_id}/extractions")).json()
    doc = extractions["documents"][0]
    assert doc["document"]["status"] == "EXTRACTED"
    assert doc["document"]["doc_type"] == "TRANSPORT_DOCUMENT"
    fields = {f["field_key"]: f for f in doc["fields"]}
    assert fields["transport.gross_weight_kg"]["normalized_value"] == "26400"
    assert fields["transport.volume_cbm"]["normalized_value"] == "38.03"
    assert fields["transport.waybill_no"]["grounding_verified"] is True
    assert "invented_field" not in fields

    # edit a field -> history + audit
    field_id = fields["transport.gross_weight_kg"]["id"]
    res = await client.patch(
        f"/api/v1/shipments/{shipment_id}/fields/{field_id}",
        json={"value": "26450.000", "reason": "Đối chiếu lại bản gốc", "actor": "local-user"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["normalized_value"] == "26450"
    assert res.json()["review_status"] == "EDITED"
    history = (await client.get(f"/api/v1/shipments/{shipment_id}/fields/{field_id}/history")).json()
    assert history and history[0]["new_value"] == "26450"

    for f in doc["fields"]:
        if f["critical"]:
            await client.post(f"/api/v1/shipments/{shipment_id}/fields/{f['id']}/confirm")

    res = await client.post(f"/api/v1/shipments/{shipment_id}/exports", json={})
    assert res.status_code == 201, res.text
    export = res.json()
    assert export["filename"].endswith(".xlsx")

    res = await client.get(f"/api/v1/exports/{export['id']}/download")
    assert res.status_code == 200
    assert res.content[:2] == b"PK"

    res = await client.get(f"/api/v1/exports/{export['id']}/report")
    assert res.status_code == 200
    import json as jsonlib

    report = jsonlib.loads(res.content)
    assert report["template_version"] == "uandi_import_v1"
    assert report["cells"]


async def test_tokhai_export_endpoint(client: AsyncClient):
    """Tờ khai path: store declaration rows, export with tokhai_import_v1, download both files."""
    from app.services.declaration import upsert_declaration

    res = await client.post("/api/v1/shipments", json={"code": "TKN108", "customer_name": "Kingsman"})
    assert res.status_code == 201, res.text
    shipment_id = res.json()["id"]

    from app.db import session_scope

    async with session_scope() as session:
        await upsert_declaration(
            session,
            shipment_id,
            header={
                "declaration.declaration_no": "108299395961",
                "declaration.registered_at": "02/06/2026 11:00:52",
                "declaration.importer_name": "CôNG TY TNHH Gỗ KINGSMAN VIệT NAM",
                "declaration.exporter_name": "BERTEC ENTERPRISE CO., LTD.",
                "declaration.bill_of_lading_no": "140426HDMUBUEA32635400",
                "declaration.invoice_no": "A - CD2025121801-2",
                "declaration.invoice_total": "23.230",
                "declaration.package_count": "28",
                "declaration.gross_weight": "52.312",
                "declaration.tax_exchange_rate": "26.143",
            },
            goods=[
                {
                    "goods.hs_code": "44071110",
                    "goods.description": "Gỗ thông xẻ (Pinus Taeda)",
                    "goods.quantity_1": "46.46",
                    "goods.unit_price": "250",
                    "goods.invoice_amount": "11.615",
                    "goods.customs_value": "303.650.945",
                    "goods.origin_country_code": "AR",
                }
            ],
            taxes=[],
        )

    res = await client.post(
        f"/api/v1/shipments/{shipment_id}/exports", json={"template_version": "tokhai_import_v1"}
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["mode"] in {"DRAFT", "FINAL"}
    assert body["filename"].endswith(".xlsx")

    download = await client.get(f"/api/v1/exports/{body['id']}/download")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith(
        "application/vnd.openxmlformats"
    ) or download.content[:2] == b"PK"

    report = await client.get(f"/api/v1/exports/{body['id']}/report")
    assert report.status_code == 200


async def test_tokhai_export_requires_declaration_data(client: AsyncClient):
    res = await client.post("/api/v1/shipments", json={"code": "TKNEMPTY"})
    shipment_id = res.json()["id"]
    res = await client.post(
        f"/api/v1/shipments/{shipment_id}/exports", json={"template_version": "tokhai_import_v1"}
    )
    assert res.status_code == 422
    assert "tờ khai" in res.json()["detail"].lower()


async def test_progress_endpoint_reports_stage_plan(client: AsyncClient):
    sample = Path(__file__).resolve().parents[3] / "data" / "sample" / "input" / "bill.pdf"

    res = await client.post("/api/v1/shipments", json={"code": "PROG1"})
    shipment_id = res.json()["id"]
    empty = (await client.get(f"/api/v1/shipments/{shipment_id}/progress")).json()
    assert empty["job"] is None

    with open(sample, "rb") as fh:
        files = [("files", ("bill.pdf", fh.read(), "application/pdf"))]
    await client.post(f"/api/v1/shipments/{shipment_id}/documents", files=files)
    await client.post(f"/api/v1/shipments/{shipment_id}/process", json={})

    queued = (await client.get(f"/api/v1/shipments/{shipment_id}/progress")).json()
    assert queued["job"]["status"] == "PENDING"
    assert queued["job"]["progress"]["document_total"] == 1
    assert queued["job"]["progress"]["document_done"] == 0
    assert queued["job"]["progress"]["stages"] == ["PARSING", "CLASSIFYING", "EXTRACTING", "PERSISTING"]
    assert queued["job"]["progress"]["final_stage"] == "FINALIZING"

    await run_pending_jobs()

    done = (await client.get(f"/api/v1/shipments/{shipment_id}/progress")).json()
    assert done["job"]["status"] == "DONE"
    assert done["job"]["progress"]["document_done"] == 1
    assert done["job"]["progress"]["current_stage"] == "FINALIZING"


async def test_verify_preview_and_export_builds_final_xlsx(client: AsyncClient):
    import json as jsonlib

    sample = Path(__file__).resolve().parents[3] / "data" / "sample" / "input" / "bill.pdf"

    res = await client.post("/api/v1/shipments", json={"code": "VERIFY1"})
    shipment_id = res.json()["id"]
    with open(sample, "rb") as fh:
        files = [("files", ("bill.pdf", fh.read(), "application/pdf"))]
    await client.post(f"/api/v1/shipments/{shipment_id}/documents", files=files)
    await client.post(f"/api/v1/shipments/{shipment_id}/process", json={})
    await run_pending_jobs()

    preview = (await client.get(f"/api/v1/shipments/{shipment_id}/verify-preview")).json()
    assert preview["field_count"] > 0
    assert preview["documents"][0]["filename"] == "bill.pdf"
    assert preview["documents"][0]["fields"][0]["label_vi"]
    assert {row["filename"] for row in preview["rows"]} == {"bill.pdf"}

    res = await client.post(
        f"/api/v1/shipments/{shipment_id}/exports", json={"template_version": "verify_sheet_v1"}
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["mode"] == "FINAL"
    assert body["filename"].endswith(".xlsx")
    assert body["filled"] > 0

    download = await client.get(f"/api/v1/exports/{body['id']}/download")
    assert download.content[:2] == b"PK"

    report = jsonlib.loads((await client.get(f"/api/v1/exports/{body['id']}/report")).content)
    assert report["filled"] == body["filled"]

    # a verify run is not a customs export: the shipment stays in review, not EXPORTED
    shipment = (await client.get(f"/api/v1/shipments/{shipment_id}")).json()
    assert shipment["status"] != "EXPORTED"


async def test_templates_list_offers_the_verify_sheet(client: AsyncClient):
    items = (await client.get("/api/v1/templates")).json()
    verify = next(item for item in items if item["version"] == "verify_sheet_v1")
    assert verify["sheets"] == ["DOI_CHIEU"]
