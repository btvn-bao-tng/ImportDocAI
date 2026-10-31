from __future__ import annotations

import sqlite3

from app.config import Settings
from app.db import create_all, dispose, init_engine


def _settings(tmp_path, db):
    return Settings.model_validate(
        {
            "llm": {"api_key": "t", "base_url": "u", "text_model": "a", "vision_model": "b"},
            "data_dir": tmp_path,
            "database_url": f"sqlite+aiosqlite:///{db}",
        }
    )


async def test_create_all_adds_missing_columns_to_existing_tables(tmp_path):
    """A legacy DB built before review_status existed must be upgraded, not silently broken."""
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE declarations (id INTEGER PRIMARY KEY, shipment_id INTEGER, declaration_no VARCHAR(64))")
    con.execute("CREATE TABLE goods_lines (id INTEGER PRIMARY KEY, declaration_id INTEGER, seq INTEGER)")
    con.commit()
    con.close()

    init_engine(_settings(tmp_path, db))
    await create_all()
    await dispose()

    con = sqlite3.connect(db)
    decl_cols = {row[1] for row in con.execute("PRAGMA table_info(declarations)")}
    goods_cols = {row[1] for row in con.execute("PRAGMA table_info(goods_lines)")}
    con.close()
    assert "review_status" in decl_cols
    assert "review_status" in goods_cols
    assert "source_located" in goods_cols


async def test_create_all_is_idempotent(tmp_path):
    db = tmp_path / "fresh.db"
    for _ in range(2):
        init_engine(_settings(tmp_path, db))
        await create_all()
        await dispose()
    con = sqlite3.connect(db)
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert {"declarations", "goods_lines", "declaration_tax_lines", "jobs"} <= tables
