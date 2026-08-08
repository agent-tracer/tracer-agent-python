"""원장 연결 풀 포트의 대역이며 문장을 실행하지 않는 경로에만 쓰인다."""

from __future__ import annotations

from ..ledger import SqlSource


class FakeLedgerPool(SqlSource):
    """문장을 실행하지 않는 경로에 빌려 주는 원장 연결 풀 대역이다."""

    # 이 대역은 상한과 획득 대기와 문장 실행을 모두 지웠으므로 연결을 실제로 빌리는 경로에 세우면 깨진다.

    async def pool(self) -> FakeLedgerPool:
        return self

    async def close(self) -> None:
        return None
