from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import LLMSettings, Settings
from app.main import create_app

from .test_consistency import StubLLM


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        llm=LLMSettings(api_key="test", base_url="http://example.invalid/v1", text_model="sonya", vision_model="vision_sonya"),
        data_dir=tmp_path / "storage",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}",
    )


@pytest.fixture()
async def ctx(tmp_path: Path):
    from app import deps as deps_module

    app = create_app(_settings(tmp_path))
    transport = ASGITransport(app=app)
    llm = StubLLM()
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            deps_module._state["llm"] = llm
            yield ac, llm
    deps_module._state.clear()


async def _seed() -> dict[str, int]:
    from app.db import session_scope
    from app.models import Document, ExtractedField, Shipment

    async with session_scope() as session:
        shipment = Shipment(code="LS-API")
        session.add(shipment)
        await session.flush()
        ids: dict[str, int] = {"sid": shipment.id}
        for name, doc_type, value in (("pl", "PACKING_LIST", "26400"), ("bl", "TRANSPORT_DOCUMENT", "26040")):
            doc = Document(
                shipment_id=shipment.id, filename=f"{name}.pdf", file_ext=".pdf", storage_path=f"/tmp/{name}.pdf",
                checksum=name, doc_type=doc_type, status="EXTRACTED",
            )
            session.add(doc)
            await session.flush()
            field = ExtractedField(
                document_id=doc.id, field_key="transport.gross_weight_kg", raw_value=value, normalized_value=value, page=1
            )
            session.add(field)
            await session.flush()
            ids[name] = field.id
    return ids


def _respond(llm: StubLLM, ids: dict[str, int]) -> None:
    llm.response = {
        "findings": [
            {"severity": "ERROR", "category": "weight", "title": "Gross lệch", "explanation": "x", "field_ids": [ids["pl"], ids["bl"]]}
        ]
    }


async def test_get_consistency_empty_shipment_returns_null_run(ctx):
    client, _ = ctx
    sid = (await client.post("/api/v1/shipments", json={"code": "EMPTY"})).json()["id"]
    r = await client.get(f"/api/v1/shipments/{sid}/consistency")
    assert r.status_code == 200
    assert r.json() == {"run": None, "stale": False, "findings": [], "counts": {"error": 0, "warning": 0}}


async def test_run_then_dismiss(ctx):
    client, llm = ctx
    ids = await _seed()
    _respond(llm, ids)
    body = (await client.post(f"/api/v1/shipments/{ids['sid']}/consistency/run")).json()
    assert body["counts"] == {"error": 1, "warning": 0}
    fid = body["findings"][0]["id"]
    r = await client.post(f"/api/v1/consistency-findings/{fid}/dismiss", json={"note": "đúng"})
    assert r.status_code == 200
    body = r.json()
    assert body["findings"][0]["dismissed"] is True
    assert body["findings"][0]["dismiss_note"] == "đúng"
    assert body["counts"]["error"] == 0
    assert (await client.get(f"/api/v1/shipments/{ids['sid']}/consistency")).json() == body


async def test_edited_field_triggers_new_llm_call(ctx):
    client, llm = ctx
    ids = await _seed()
    await client.post(f"/api/v1/shipments/{ids['sid']}/consistency/run")
    await client.post(f"/api/v1/shipments/{ids['sid']}/consistency/run")
    assert len(llm.calls) == 1
    r = await client.patch(f"/api/v1/shipments/{ids['sid']}/fields/{ids['pl']}", json={"value": "26401"})
    assert r.status_code == 200
    await client.post(f"/api/v1/shipments/{ids['sid']}/consistency/run")
    assert len(llm.calls) == 2


async def test_unknown_ids_return_404(ctx):
    client, _ = ctx
    assert (await client.get("/api/v1/shipments/9999/consistency")).status_code == 404
    assert (await client.post("/api/v1/shipments/9999/consistency/run")).status_code == 404
    assert (await client.post("/api/v1/consistency-findings/9999/dismiss", json={})).status_code == 404
