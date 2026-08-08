"""신원 없이 열리는 프로브 둘이 봉투 없이 답하는지 검증한다."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi.testclient import TestClient

from tests.support.services import fake_services
from tracer_agent.shared.agents.runtime.ledger import LedgerSql


class UnreachableSql:
    """원장에 닿지 못하는 창구를 대신한다."""

    def connect(self) -> AbstractAsyncContextManager[LedgerSql]:
        """빌리려는 순간 닿지 못했다고 알린다."""
        return self._fail()

    @asynccontextmanager
    async def _fail(self) -> AsyncIterator[LedgerSql]:
        raise ConnectionError("원장에 닿지 못한다")
        yield


def test_헬스체크가_정상_응답을_낸다(client: TestClient) -> None:
    res = client.get("/health")

    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_준비_프로브가_원장에_닿으면_봉투_없이_ok를_낸다(client: TestClient) -> None:
    res = client.get("/health/ready")

    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_준비_프로브가_원장에_닿지_못하면_503을_낸다(client: TestClient) -> None:
    client.app.state.services = fake_services(execution_sql=UnreachableSql())

    res = client.get("/health/ready")

    assert res.status_code == 503
    assert res.json() == {"status": "unready"}
