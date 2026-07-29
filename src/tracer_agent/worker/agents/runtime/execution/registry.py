"""실행 취소 레지스트리와 멱등 실행 캐시를 관리한다."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from tracer_agent.shared.agents.shared.models import AgentResponse

ResponseFactory = Callable[[], Awaitable[AgentResponse]]

_IDEMPOTENCY_TTL_S = 900.0

_log = logging.getLogger(__name__)


class IdempotencyConflict(Exception):
    """같은 멱등 키가 다른 실행 입력에 재사용되었음을 알린다."""


@dataclass
class IdempotencyEntry:
    """진행 중 또는 성공한 멱등 실행의 프로세스 로컬 상태다."""

    model: str
    job_id: str | None
    input_hash: str
    task: asyncio.Task[AgentResponse]
    expires_at: float | None = None


_idempotency_cache: dict[str, IdempotencyEntry] = {}
_run_tasks: dict[str, asyncio.Task[AgentResponse]] = {}


def cancel_run(run_id: str) -> bool:
    """runId로 등록된 진행 중 실행을 취소한다."""
    task = _run_tasks.get(run_id)
    if task is None or task.done():
        return False
    task.cancel()
    return True


async def run_registered(
    factory: ResponseFactory,
    *,
    label: str,
    model: str,
    job_id: str | None,
    input_hash: str,
    idempotency_key: str | None,
    run_key: str | None,
) -> AgentResponse:
    """실행을 취소 레지스트리와 선택적 멱등 캐시에 등록해 기다린다."""
    if idempotency_key is not None:
        now = time.monotonic()
        _prune_idempotency_cache(now)
        cache_key = _cache_key(label, idempotency_key)
        cached = _idempotency_cache.get(cache_key)
        if cached is not None:
            if cached.model != model or cached.job_id != job_id or cached.input_hash != input_hash:
                _log.warning("idempotency key reused with a different model or input: %s", cache_key)
                raise IdempotencyConflict("idempotency key was reused with a different model or input")
            return await _await_idempotent(cache_key, run_key, cached.task)
        task = asyncio.ensure_future(factory())
        _idempotency_cache[cache_key] = IdempotencyEntry(model, job_id, input_hash, task)
        return await _await_idempotent(cache_key, run_key, task)
    if run_key is None:
        return await factory()
    task = asyncio.ensure_future(factory())
    return await _await_registered(run_key, task)


def _prune_idempotency_cache(now: float) -> None:
    expired = [
        key
        for key, entry in _idempotency_cache.items()
        if entry.expires_at is not None and entry.expires_at <= now
    ]
    for key in expired:
        del _idempotency_cache[key]


def _cache_key(label: str, idempotency_key: str) -> str:
    return f"{label}:{idempotency_key}"


async def _await_registered(
    key: str | None,
    task: asyncio.Task[AgentResponse],
) -> AgentResponse:
    if key:
        _run_tasks[key] = task
    try:
        return await task
    finally:
        if key and _run_tasks.get(key) is task:
            del _run_tasks[key]


def _drop_cached_task(cache_key: str, task: asyncio.Task[AgentResponse]) -> None:
    cached = _idempotency_cache.get(cache_key)
    if cached is not None and cached.task is task:
        del _idempotency_cache[cache_key]


def _cache_success(cache_key: str, task: asyncio.Task[AgentResponse]) -> None:
    cached = _idempotency_cache.get(cache_key)
    if cached is not None and cached.task is task:
        cached.expires_at = time.monotonic() + _IDEMPOTENCY_TTL_S


async def _await_idempotent(
    cache_key: str,
    run_key: str | None,
    task: asyncio.Task[AgentResponse],
) -> AgentResponse:
    try:
        response = await _await_registered(run_key, task)
    except BaseException:
        _drop_cached_task(cache_key, task)
        raise
    if response.error is not None:
        _drop_cached_task(cache_key, task)
    else:
        _cache_success(cache_key, task)
    return response
