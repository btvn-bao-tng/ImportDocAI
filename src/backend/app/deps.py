from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, load_settings
from .db import sessionmaker
from .services.llm import LLMClient

_state: dict[str, Any] = {}


def init_state(settings: Settings, llm: LLMClient, job_runner: Callable[[int], Awaitable[None]]) -> None:
    _state["settings"] = settings
    _state["llm"] = llm
    _state["job_runner"] = job_runner


def get_settings() -> Settings:
    return _state["settings"]


def get_llm() -> LLMClient:
    return _state["llm"]


async def get_session() -> AsyncSession:
    async with sessionmaker()() as session:
        yield session


def get_background_runner() -> Callable[[int], Awaitable[None]]:
    return _state["job_runner"]


async def recover_stale_jobs() -> int:
    """Requeue jobs left RUNNING by a previous process (SQLite has no external queue)."""
    from .db import session_scope
    from .models import Job

    async with session_scope() as session:
        stale = (await session.execute(select(Job).where(Job.status == "RUNNING"))).scalars().all()
        for job in stale:
            job.status = "PENDING"
    runner: Callable[[int], Awaitable[None]] = _state["job_runner"]
    for job in stale:
        result = runner(job.id)
        if inspect.isawaitable(result):
            await result
    return len(stale)
