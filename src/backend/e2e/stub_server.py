"""Backend for the Playwright e2e suite.

Runs the real FastAPI app against a throwaway SQLite file and a deterministic stub LLM,
so upload -> process -> review -> export works without network access or the real
BioTuring gateway. An in-process worker loop claims queued jobs, which is what the UI's
2-second polling expects.

Usage: .venv/bin/python e2e/stub_server.py --port 8001
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import uvicorn
from fastapi import HTTPException
from pydantic import BaseModel

from app import deps as deps_module
from app.config import LLMSettings, Settings
from app.main import create_app

log = logging.getLogger("e2e.stub")

DOC_NO = "LS2628"
WAYBILL = "YMJAI245332642"
CONTAINER = "YMMU6509610"
VESSEL = "YM CONTINENT"
SEAL = "YMAW339171"
GROSS_WEIGHT = "26400.000"
NET_WEIGHT = "26000.000"
VOLUME = "38.030"
TOTAL = "67175.4552"
GOODS_DESC = "OAK VENEER (4.0x 190 x 1900)MM"
SHIPPER = "DALIAN CHUNLING WOOD INDUSTRY CO., LTD."
CONSIGNEE = "GREEN WAY COMPANY LIMITED"


class StubLLM:
    """Deterministic stand-in for the extraction gateway (same shape as the API tests)."""

    settings = SimpleNamespace(max_tokens=32000)

    def extraction_model(self) -> str:
        return "vision_sonya"

    async def probe(self):
        return {"json_schema": False, "json_object": False, "vision": True, "strategy": "json_prompt"}

    async def complete_json_validated(
        self,
        prompt,
        schema_name,
        schema,
        validator,
        image_png=None,
        max_attempts=3,
        max_tokens=None,
        model_override=None,
    ):
        if schema_name == "classification":
            body = prompt.split("Nội dung", 1)[-1].upper()
            doc_type = "TRANSPORT_DOCUMENT"
            if "COMMERCIAL INVOICE" in body:
                doc_type = "COMMERCIAL_INVOICE"
            elif "PACKING LIST" in body:
                doc_type = "PACKING_LIST"
            elif "SALES CONTRACT" in prompt.upper() or "合同" in body:
                doc_type = "SALES_CONTRACT_PO"
            return validator({"doc_type": doc_type, "confidence": 0.95, "reason": "e2e stub"})

        data = {
            "doc_number": {"value": DOC_NO, "source_quote": DOC_NO},
            "issue_date": {"value": "2026-09-09", "source_quote": "2026-09-09"},
            "vessel": {"value": VESSEL, "source_quote": VESSEL},
            "voyage": {"value": "2608S", "source_quote": "2608S"},
            "waybill_no": {"value": WAYBILL, "source_quote": WAYBILL},
            "container_no": {"value": CONTAINER, "source_quote": CONTAINER},
            "seal_no": {"value": SEAL, "source_quote": SEAL},
            "packages": {"value": "20", "source_quote": "20"},
            "gross_weight": {"value": GROSS_WEIGHT, "source_quote": GROSS_WEIGHT},
            "net_weight": {"value": NET_WEIGHT, "source_quote": NET_WEIGHT},
            "volume": {"value": VOLUME, "source_quote": VOLUME},
            "currency": {"value": "USD", "source_quote": "USD"},
            "total_amount": {"value": TOTAL, "source_quote": "TOTAL"},
            "number_locale": "period_decimal",
            "parties": [
                {"name": SHIPPER, "role": "SHIPPER"},
                {"name": CONSIGNEE, "role": "CONSIGNEE"},
            ],
            "line_items": [
                {
                    "description": {"value": GOODS_DESC, "source_quote": "OAK VENEER"},
                    "quantity": {"value": "38.03", "source_quote": VOLUME},
                    "amount": {"value": TOTAL, "source_quote": "TOTAL"},
                }
            ],
        }
        return validator(data)


async def _worker_loop() -> None:
    """Claim and run queued jobs, mirroring the durable worker process."""
    from app.services.job_runner import run_process_job
    from app.worker import _finish_job, _heartbeat, claim_job

    while True:
        try:
            job_id = await claim_job()
            if job_id is None:
                await asyncio.sleep(0.2)
                continue
            heartbeat = asyncio.create_task(_heartbeat(job_id))
            failed = True
            try:
                failed = not await run_process_job(job_id, deps_module._state["llm"])
            except Exception:  # pragma: no cover - surfaced through the job row
                log.exception("e2e job %s failed", job_id)
            finally:
                heartbeat.cancel()
            await _finish_job(job_id, failed=failed)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - keep the harness alive
            log.exception("e2e worker iteration failed")
            await asyncio.sleep(0.5)


def build_settings() -> Settings:
    scratch = Path(tempfile.mkdtemp(prefix="importdoc-e2e-"))
    return Settings(
        llm=LLMSettings(
            api_key="e2e-stub",
            base_url="http://127.0.0.1:9/v1",
            text_model="sonya",
            vision_model="vision_sonya",
        ),
        data_dir=scratch / "storage",
        database_url=f"sqlite+aiosqlite:///{scratch / 'e2e.db'}",
    )


def build_app(settings: Settings):
    """Real app whose LLM is replaced by the stub; the worker below uses it too."""
    app = create_app(settings)
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app_instance):
        async with original_lifespan(app_instance):
            deps_module._state["llm"] = StubLLM()
            worker = asyncio.create_task(_worker_loop())
            try:
                yield
            finally:
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass
                deps_module._state.clear()

    app.router.lifespan_context = lifespan
    _mount_declaration_seed(app)
    return app


class DeclarationSeed(BaseModel):
    """Rows the extraction pipeline would have written for a Tờ khai document."""

    header: dict[str, str | None] = {}
    goods: list[dict[str, str | None]] = []
    taxes: list[dict[str, str | None]] = []
    goods_taxes: dict[int, list[dict[str, str | None]]] = {}
    licenses: list[dict[str, str | None]] = []
    instructions: list[dict[str, str | None]] = []
    adjustments: list[dict[str, str | None]] = []


def _mount_declaration_seed(app) -> None:
    """Test-only hook: the stub LLM never emits `declaration.*`, so seed those rows directly.

    Mounted only by this e2e server, never by `create_app`, so production routes are untouched.
    """

    @app.post("/__e2e__/shipments/{shipment_id}/declaration", status_code=201)
    async def seed_declaration(shipment_id: int, body: DeclarationSeed):
        from sqlalchemy import select

        from app.db import session_scope
        from app.models import Shipment
        from app.services.declaration import upsert_declaration

        async with session_scope() as session:
            if (await session.execute(select(Shipment).where(Shipment.id == shipment_id))).scalars().first() is None:
                raise HTTPException(404, "Không tìm thấy shipment")
            await upsert_declaration(
                session,
                shipment_id,
                header=body.header,
                goods=body.goods,
                taxes=body.taxes,
                goods_taxes={int(k): v for k, v in body.goods_taxes.items()} or None,
                licenses=body.licenses or None,
                instructions=body.instructions or None,
                adjustments=body.adjustments or None,
            )
        return {"shipment_id": shipment_id, "seeded": True}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    settings = build_settings()
    log.info("e2e backend scratch dir: %s", settings.data_dir.parent)

    uvicorn.run(build_app(settings), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
