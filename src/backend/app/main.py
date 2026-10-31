from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from .api import declaration, documents, exports, jobs, review, shipments
from .config import load_settings
from .db import create_all, dispose, init_engine
from .deps import get_session, init_state
from .models import Shipment
from .services.constants import IngestError
from .services.export_excel import (
    ASSETS_DIR,
    VERIFY_TEMPLATE_VERSION,
    ExportError,
    available_templates,
    load_mapping,
)
from .services.export_tokhai import TOKHAI_TEMPLATE_VERSION
from .services.ingest import parse_document
from .services.llm import LLMClient
from .services.template_preview import build_template_preview

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("importdoc")


def _noop_runner():
    """API process never executes jobs; the durable worker does."""

    async def submit(job_id: int) -> None:
        return None

    return submit


def create_app(settings=None) -> FastAPI:
    settings = settings or load_settings()
    llm = LLMClient(settings.llm)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_engine(settings)
        await create_all()
        init_state(settings, llm, _noop_runner())
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "files").mkdir(exist_ok=True)
        log.info("ImportDoc AI API ready (data_dir=%s)", settings.data_dir)
        yield
        await dispose()

    app = FastAPI(title="ImportDoc AI", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/v1/templates")
    async def templates():
        items = available_templates()
        for item in items:
            mapping, template_path = load_mapping(item["version"])
            item["sheets"] = list((mapping.get("sheets") or {}).keys())
            item["file_code_prefix"] = mapping.get("file_code_prefix")
        if all(item.get("version") != VERIFY_TEMPLATE_VERSION for item in items):
            items.insert(
                0,
                {
                    "version": VERIFY_TEMPLATE_VERSION,
                    "name": "Bảng đối chiếu trích xuất",
                    "sheets": ["DOI_CHIEU"],
                    "file_code_prefix": None,
                },
            )
        return items

    @app.get("/api/v1/templates/{version}")
    async def template_detail(version: str):
        mapping, template_path = load_mapping(version)
        return {**mapping, "template_path": str(template_path.name)}

    def _template_file(version: str):
        try:
            _, template_path = load_mapping(version)
        except ExportError as exc:
            raise HTTPException(404, str(exc)) from exc
        # The mapping is trusted config, but only serve files that live with the assets.
        if not template_path.resolve().is_relative_to(ASSETS_DIR.resolve()):
            raise HTTPException(404, "Mẫu này không có tệp trong thư mục assets")
        return template_path

    @app.get("/api/v1/templates/{version}/grid")
    async def template_grid(version: str):
        template_path = _template_file(version)
        try:
            parsed = parse_document(template_path)
        except IngestError as exc:
            raise HTTPException(400, str(exc)) from exc
        if parsed["grid"] is None:
            raise HTTPException(400, "Mẫu này không có dạng bảng tính")
        return parsed["grid"]

    @app.get("/api/v1/templates/{version}/file")
    async def template_file(version: str):
        template_path = _template_file(version)
        return FileResponse(template_path, filename=template_path.name)

    @app.get("/api/v1/shipments/{shipment_id}/template-preview")
    async def shipment_template_preview(
        shipment_id: int,
        version: str = TOKHAI_TEMPLATE_VERSION,
        session: AsyncSession = Depends(get_session),
    ):
        if await session.get(Shipment, shipment_id) is None:
            raise HTTPException(404, "Không tìm thấy shipment")
        try:
            return await build_template_preview(session, shipment_id, version)
        except ExportError as exc:
            raise HTTPException(400, str(exc)) from exc

    app.include_router(shipments.router, prefix="/api/v1")
    app.include_router(documents.router, prefix="/api/v1")
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(review.router, prefix="/api/v1")
    app.include_router(declaration.router, prefix="/api/v1")
    app.include_router(exports.router, prefix="/api/v1")

    @app.get("/api/v1/files/{path:path}")
    async def serve_file(path: str):
        data_root = settings.data_dir.resolve()
        candidate = (data_root / path).resolve()
        if not candidate.is_relative_to(data_root) or not candidate.is_file():
            raise HTTPException(404, "Không tìm thấy file")
        return FileResponse(candidate)

    return app


app = create_app()
