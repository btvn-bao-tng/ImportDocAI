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
    assert "private_management_code" in goods_cols


async def test_create_all_is_idempotent(tmp_path):
    db = tmp_path / "fresh.db"
    for _ in range(2):
        init_engine(_settings(tmp_path, db))
        await create_all()
        await dispose()
    con = sqlite3.connect(db)
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert {
        "declarations",
        "goods_lines",
        "goods_tax_lines",
        "declaration_licenses",
        "declaration_instructions",
        "declaration_adjustments",
        "declaration_tax_lines",
        "jobs",
    } <= tables


async def test_create_all_adds_unique_shipment_index_to_legacy_declarations(tmp_path):
    """`INSERT ... ON CONFLICT (shipment_id)` needs the index on DB files that already exist."""
    db = tmp_path / "legacy_unique.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE declarations (id INTEGER PRIMARY KEY, shipment_id INTEGER, declaration_no VARCHAR(64))")
    con.execute("INSERT INTO declarations (shipment_id, declaration_no) VALUES (1, 'A')")
    con.commit()
    con.close()

    init_engine(_settings(tmp_path, db))
    await create_all()

    from sqlalchemy import select

    from app.db import session_scope
    from app.services.declaration import get_declaration, upsert_declaration

    async with session_scope() as session:
        await upsert_declaration(
            session,
            shipment_id=1,
            header={"declaration.declaration_no": "B"},
            goods=[],
            taxes=[],
        )

    await dispose()

    con = sqlite3.connect(db)
    indexes = {row[1] for row in con.execute("PRAGMA index_list(declarations)")}
    rows = con.execute("SELECT COUNT(*), MAX(declaration_no) FROM declarations WHERE shipment_id = 1").fetchall()
    con.close()
    assert "uq_declaration_shipment" in indexes
    assert rows == [(1, "B")]


async def test_create_all_skips_unique_index_when_legacy_rows_are_duplicated(tmp_path, caplog):
    """Prototype files with duplicate declarations must still start; the index is skipped."""
    db = tmp_path / "legacy_dup.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE declarations (id INTEGER PRIMARY KEY, shipment_id INTEGER, declaration_no VARCHAR(64))")
    con.executemany(
        "INSERT INTO declarations (shipment_id, declaration_no) VALUES (?, ?)",
        [(1, "A"), (1, "B")],
    )
    con.commit()
    con.close()

    init_engine(_settings(tmp_path, db))
    await create_all()
    await dispose()

    con = sqlite3.connect(db)
    indexes = {row[1] for row in con.execute("PRAGMA index_list(declarations)")}
    con.close()
    assert "uq_declaration_shipment" not in indexes
