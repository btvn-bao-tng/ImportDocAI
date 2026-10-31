from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yml"


class LLMSettings(BaseModel):
    api_key: str
    base_url: str
    text_model: str
    vision_model: str
    extract_model: str = "vision_sonya"
    max_tokens: int = 32000


class Settings(BaseModel):
    llm: LLMSettings
    data_dir: Path
    database_url: str


def _load_provider_block(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return ((raw.get("providers") or {}).get("bioturing") or {})


def load_settings(
    config_path: Path | None = None,
    environ: dict[str, str] | None = None,
) -> Settings:
    """Load settings from config.yml with environment-variable override.

    Precedence: environment > config.yml. Fails fast when no API key is found so a
    misconfigured deployment cannot silently run without AI extraction.
    """
    env = dict(os.environ if environ is None else environ)
    path = config_path or Path(env.get("IMPORTDOC_CONFIG") or DEFAULT_CONFIG_PATH)
    block = _load_provider_block(path)

    models = block.get("availableModels") or {}
    default_model = block.get("defaultModel") or "vision_sonya"
    vision_model = env.get("BIOTURING_VISION_MODEL") or (
        default_model if "image" in (models.get(default_model, {}).get("input") or []) else "vision_sonya"
    )
    text_model = env.get("BIOTURING_TEXT_MODEL") or "sonya"

    api_key = env.get("BIOTURING_API_KEY") or block.get("apiKey") or ""
    if not api_key:
        raise ValueError(
            "Missing LLM API key: set BIOTURING_API_KEY or providers.bioturing.apiKey in config.yml "
            f"(looked at {path})."
        )
    base_url = env.get("BIOTURING_BASE_URL") or block.get("baseUrl") or ""
    if not base_url:
        raise ValueError("Missing BIOTURING_BASE_URL / providers.bioturing.baseUrl.")

    data_dir = Path(env.get("DATA_DIR") or "data/storage")
    if not data_dir.is_absolute():
        data_dir = REPO_ROOT / data_dir

    database_url = env.get("DATABASE_URL") or "sqlite+aiosqlite:///data/app.db"
    if database_url.startswith("sqlite") and "/" in database_url:
        db_path = database_url.split("///", 1)[-1]
        if not Path(db_path).is_absolute():
            (REPO_ROOT / db_path).parent.mkdir(parents=True, exist_ok=True)
            database_url = f"sqlite+aiosqlite:///{REPO_ROOT / db_path}"

    return Settings(
        llm=LLMSettings(
            api_key=api_key,
            base_url=base_url,
            text_model=text_model,
            vision_model=vision_model,
            extract_model=env.get("BIOTURING_EXTRACT_MODEL") or vision_model,
            max_tokens=int(env.get("MAX_TOKENS") or 32000),
        ),
        data_dir=data_dir,
        database_url=database_url,
    )
