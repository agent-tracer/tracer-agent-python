"""잡이 낸 산출물을 자기 원장에 적되 쓰기 실패가 종결을 되돌리지 않게 한다."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from tracer_agent.shared.agents.runtime.ledger import LedgerSql, SqlSource

from ..shared.empty_result import DEGRADED
from .tracer_client import TracerApiPort

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobOutputTargets:
    """종결 단계가 산출물을 적을 자기 원장과 추적에 물을 창구를 한 칸에 담는다."""

    sql: SqlSource
    tracer: TracerApiPort


async def write_output(
    targets: JobOutputTargets,
    label: str,
    execution_id: str,
    work: Callable[[LedgerSql], Awaitable[None]],
) -> bool:
    """한 종결이 전부 쓰이거나 아무것도 쓰이지 않도록 한 트랜잭션으로 적는다."""
    try:
        async with targets.sql.connect() as sql, sql.transaction():
            await work(sql)
    except Exception as unwritten:
        # 종결은 이미 닫혔으므로 되돌리지 않되, 산출이 서지 않았다는 사실은 사유와 함께 남긴다.
        _log.error(
            "agent.job.output.unwritten target=%s executionId=%s emptyResultReason=%s cause=%s",
            label,
            execution_id,
            DEGRADED,
            unwritten,
        )
        return False
    return True


def object_items(data: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    """산출물 하나에서 원장에 적을 수 있는 객체 항목만 남긴다."""
    return [item for item in (data.get(key) or []) if isinstance(item, dict)]
