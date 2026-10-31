from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import Settings
from .models import Base

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(settings: Settings):
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(settings.database_url, future=True)
        if settings.database_url.startswith("sqlite"):

            @event.listens_for(_engine.sync_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=5000")
                cur.close()

        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_engine():
    if _engine is None:
        raise RuntimeError("Database engine not initialised")
    return _engine


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("Database engine not initialised")
    return _sessionmaker


@asynccontextmanager
async def session_scope():
    async with sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_all() -> None:
    """Create missing tables and add missing nullable columns.

    `create_all` is a silent no-op for tables that already exist, so a new column
    (e.g. `declarations.review_status`) would never reach an existing SQLite file and
    the first insert would fail with "no column named ...". This additive sync keeps
    local prototypes working until Alembic is introduced; it only ADDs nullable
    columns and never drops or rewrites anything.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "sqlite":
            await conn.run_sync(_add_missing_columns)


def _add_missing_columns(sync_conn) -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        present = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            if not column.nullable and column.default is None and not column.autoincrement:
                raise RuntimeError(
                    f"Cột mới bắt buộc '{table.name}.{column.name}' cần migration — thêm Alembic trước khi phát hành"
                )
            col_type = column.type.compile(dialect=sync_conn.dialect)
            sync_conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}'))


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
