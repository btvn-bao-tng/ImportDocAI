from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .api import documents, exports, jobs, review, shipments
from .config import load_settings
from .db import create_all, dispose, init_engine
from .deps import init_state
from .services.export_excel import available_templates, load_mapping
from .services.llm import LLMClient

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
        return items

    @app.get("/api/v1/templates/{version}")
    async def template_detail(version: str):
        mapping, template_path = load_mapping(version)
        return {**mapping, "template_path": str(template_path.name)}

    app.include_router(shipments.router, prefix="/api/v1")
    app.include_router(documents.router, prefix="/api/v1")
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(review.router, prefix="/api/v1")
    app.include_router(exports.router, prefix="/api/v1")

    @app.get("/api/v1/files/{path:path}")
    async def serve_file(path: str):
        candidate = (settings.data_dir / path).resolve()
        if not str(candidate).startswith(str(settings.data_dir.resolve())) or not candidate.exists():
            return FileResponse(Path("/nonexistent"))
        return FileResponse(candidate)

    return app


app = create_app()
