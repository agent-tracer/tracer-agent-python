"""원장을 빌리지 못한 창구가 계약이 정한 어휘 하나로 거절하는지 검증한다."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from tests.support.services import fake_services
from tracer_agent.api import app as app_module
from tracer_agent.shared.agents.runtime.ledger import LedgerSql, LedgerUnavailable
from tracer_agent.shared.agents.shared.contract_root import CONTRACT_ROOT

DECLARATION = json.loads(
    (CONTRACT_ROOT / "agent" / "shared" / "ledger.availability.json").read_text(encoding="utf-8")
)

THREAD_PATH = "/api/agent/chat/threads"
SETTINGS_PATH = "/api/agent/settings"
JOBS_PATH = "/api/agent/jobs/history"


# 운영자가 읽는 사유이며 이 글자가 사용자에게 나가면 계약의 문구 규칙을 어긴 것이다.
OPERATOR_REASON = "원장 연결을 5.0초 안에 빌리지 못했다"


class DrySql:
    """빌릴 연결이 나지 않는 창구를 대신한다."""

    def connect(self) -> AbstractAsyncContextManager[LedgerSql]:
        """빌리려는 순간 원장을 쓸 수 없다고 알린다."""
        return self._dry()

    @asynccontextmanager
    async def _dry(self) -> AsyncIterator[LedgerSql]:
        raise LedgerUnavailable(OPERATOR_REASON)
        yield


@pytest.fixture
def dry_client() -> Iterator[TestClient]:
    with TestClient(app_module.create_app()) as test_client:
        test_client.app.state.services = fake_services(execution_sql=DrySql())
        yield test_client


@pytest.mark.parametrize("path", [THREAD_PATH, SETTINGS_PATH, JOBS_PATH])
def test_원장을_빌리지_못한_창구가_계약의_어휘로_거절한다(dry_client: TestClient, path: str) -> None:
    res = dry_client.get(path, headers={"x-user-id": "u1"})

    assert res.status_code == DECLARATION["status"]
    assert res.json() == {
        "ok": False,
        "error": {"code": DECLARATION["code"], "message": DECLARATION["message"]},
    }


def test_사용자에게는_운영자가_읽는_사유가_나가지_않는다(dry_client: TestClient) -> None:
    res = dry_client.get(THREAD_PATH, headers={"x-user-id": "u1"})

    assert OPERATOR_REASON not in res.text
