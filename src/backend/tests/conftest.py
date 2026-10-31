from __future__ import annotations


from pathlib import Path

import pytest
import yaml

from app.config import load_settings
REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_INPUT = REPO_ROOT / "data" / "sample" / "input"
SAMPLE_OUTPUT = REPO_ROOT / "data" / "sample" / "output"


@pytest.fixture()
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "bioturing": {
                        "apiKey": "from-file",
                        "baseUrl": "https://file.example/v1",
                        "defaultModel": "vision_sonya",
                        "availableModels": {
                            "sonya": {"input": ["text"]},
                            "vision_sonya": {"input": ["text", "image"]},
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def settings(tmp_path: Path):
    return load_settings(
        config_path=None,
        environ={
            "BIOTURING_API_KEY": "test-key",
            "BIOTURING_BASE_URL": "https://example.test/v1",
            "DATA_DIR": str(tmp_path / "storage"),
            "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        },
    )


@pytest.fixture()
async def db_session(tmp_path: Path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'unit.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()
