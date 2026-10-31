from __future__ import annotations

import pytest

from app.config import load_settings


def test_env_overrides_config_file(config_file):
    settings = load_settings(
        config_path=config_file,
        environ={
            "BIOTURING_API_KEY": "env-key",
            "BIOTURING_BASE_URL": "https://env.example/v1",
            "DATA_DIR": "data/storage",
            "DATABASE_URL": "sqlite+aiosqlite:///data/app.db",
        },
    )
    assert settings.llm.api_key == "env-key"
    assert settings.llm.base_url == "https://env.example/v1"
    assert settings.llm.text_model == "sonya"
    assert settings.llm.vision_model == "vision_sonya"
    assert settings.llm.max_tokens == 32000


def test_config_file_used_when_env_missing(config_file):
    settings = load_settings(
        config_path=config_file,
        environ={"DATA_DIR": "data/storage", "DATABASE_URL": "sqlite+aiosqlite:///data/app.db"},
    )
    assert settings.llm.api_key == "from-file"
    assert settings.llm.base_url == "https://file.example/v1"


def test_missing_key_fails_fast(config_file, tmp_path):
    empty = tmp_path / "empty.yml"
    empty.write_text("providers: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Missing LLM API key"):
        load_settings(config_path=empty, environ={})


def test_relative_sqlite_path_is_absolute(settings):
    assert settings.database_url.startswith("sqlite+aiosqlite:////")
