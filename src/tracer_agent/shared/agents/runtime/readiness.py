"""배포 단위가 원장에 닿아 일을 받을 수 있는지 판정하는 규칙을 소유한다."""

from __future__ import annotations

import logging

from .ledger import SqlSource

# 원장까지 왕복하는 질의라야 연결이 살아 있다는 것을 알린다.
READINESS_PROBE = "SELECT 1"
UNREADY_STATUS = 503

_log = logging.getLogger(__name__)


async def ledger_ready(source: SqlSource, unit: str) -> bool:
    """원장에 닿으면 참을 내고 닿지 못하면 사유를 로그에 남기고 거짓을 낸다."""
    try:
        async with source.connect() as sql:
            await sql.fetch(READINESS_PROBE)
    except Exception:
        # 사유를 감추는 것은 응답이지 로그가 아니다.
        _log.warning("%s readiness probe failed", unit, exc_info=True)
        return False
    return True
