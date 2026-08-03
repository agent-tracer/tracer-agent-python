"""원장 연결 풀 포트의 대역이며 문장을 실행하지 않는 경로에만 쓰인다."""

from __future__ import annotations

from ..ledger import SqlSource


class FakeLedgerPool(SqlSource):
    """문장을 돌리지 않는 실행 경로에 빌려 주는 원장 연결 풀 대역이다."""

    async def pool(self) -> FakeLedgerPool:
        return self

    async def close(self) -> None:
        return None
