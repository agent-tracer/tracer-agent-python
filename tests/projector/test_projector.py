"""배경 작업 배포 단위의 기동 순서와 배선과 프로브를 검증한다(색인과 브로커 없음)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support.chat_surface import SingleSql
from tests.support.contract import wire_contract
from tests.support.recipes import OTHER_AXIS
from tracer_agent.projector import app as app_module
from tracer_agent.projector.runtime import ProjectorJobs, ProjectorResources, build_projector
from tracer_agent.shared.agents.recipe.consumer import LedgerEventConsumer
from tracer_agent.shared.agents.recipe.index import (
    SearchIndexDefinition,
    projector_startup_order,
    recipes_index_definition,
    search_outbox_drain_interval_s,
    search_outbox_drain_lock_key,
)
from tracer_agent.shared.agents.recipe.outbox import SearchOutboxDrainScheduler
from tracer_agent.shared.agents.recipe.search import OpenSearchIndexAdmin
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.runtime.ledger import LedgerPoolProvider, PooledSql
from tracer_agent.shared.agents.shared.axis import AGENT_BACKEND
from tracer_agent.shared.config import Settings

_DRAIN = next(
    stage for stage in wire_contract("search.index.json")["pipeline"]["stages"] if stage["name"] == "drain"
)


@dataclass
class RecordingAdmin:
    """색인을 세우지 않고 세우라는 요청과 그 차례를 기록한다."""

    order: list[str]
    ensured: list[SearchIndexDefinition] = field(default_factory=list)

    async def ensure_index(self, definition: SearchIndexDefinition) -> None:
        """세우라는 요청을 기록한다."""
        self.order.append("index")
        self.ensured.append(definition)


@dataclass
class RecordingDrain:
    """원장을 열지 않고 배출이 불린 차례를 기록한다."""

    order: list[str]

    async def run_once(self) -> int:
        """배출이 불렸다고 기록하고 아무 행도 배출하지 않았다고 낸다."""
        self.order.append("drain")
        return 0


@dataclass
class RecordingScheduler:
    """배출을 실행하지 않고 시작하라는 요청의 차례만 기록한다."""

    order: list[str]

    def start(self) -> None:
        """배출기를 시작했다고 기록한다."""
        self.order.append("drain")

    async def stop(self) -> None:
        """멈추라는 요청에 아무것도 하지 않는다."""


@dataclass
class SilentKafkaConsumer:
    """브로커에 붙지 않고 사건이 없는 스트림을 흉내 낸다."""

    order: list[str]

    async def start(self) -> None:
        """읽기를 시작했다고 기록한다."""
        self.order.append("consumer")

    async def stop(self) -> None:
        """읽기를 멈춘다."""

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._empty()

    async def _empty(self) -> AsyncIterator[Any]:
        """이 시험은 사건을 보내지 않으므로 읽기가 곧바로 끝난다."""
        return
        yield


def _jobs(order: list[str]) -> tuple[ProjectorJobs, RecordingAdmin]:
    admin = RecordingAdmin(order)
    consumer = SilentKafkaConsumer(order)
    return (
        ProjectorJobs(
            index_admin=admin,
            drain=SearchOutboxDrainScheduler(RecordingDrain(order), interval_s=0),
            ledger_events=LedgerEventConsumer("broker:0", SingleSql(SqliteLedgerSql()), lambda *_: consumer),
        ),
        admin,
    )


class Test기동이_배경_작업_셋을_모두_맡는다:
    async def test_색인을_먼저_세우고_배출과_투영을_시작한다(self) -> None:
        order: list[str] = []
        jobs, admin = _jobs(order)

        await jobs.start()
        await asyncio.sleep(0.05)
        await jobs.stop()

        assert admin.ensured == [recipes_index_definition()]
        assert order[0] == "index"
        assert "consumer" in order
        assert "drain" in order

    async def test_시작하는_차례가_계약이_적은_순서와_같다(self) -> None:
        # 색인이 서기 전에 배출기가 문서를 쓰면 동적 매핑이 계약의 분석기를 대신한다.
        order: list[str] = []
        consumer = SilentKafkaConsumer(order)
        jobs = ProjectorJobs(
            index_admin=RecordingAdmin(order),
            drain=RecordingScheduler(order),
            ledger_events=LedgerEventConsumer("broker:0", SingleSql(SqliteLedgerSql()), lambda *_: consumer),
        )

        await jobs.start()
        await jobs.stop()

        assert order == ["index", "drain", "consumer"]
        assert len(order) == len(projector_startup_order())

    async def test_멈추면_배출이_더_불리지_않는다(self) -> None:
        order: list[str] = []
        jobs, _ = _jobs(order)
        await jobs.start()
        await asyncio.sleep(0.05)

        await jobs.stop()
        drained = order.count("drain")
        await asyncio.sleep(0.05)

        assert order.count("drain") == drained


class Test배선이_계약이_정한_협력자를_세운다:
    def test_열린_연결에_색인과_배출과_투영을_모두_이어_낸다(self) -> None:
        settings = Settings()
        ledger = LedgerPoolProvider(settings.agent_dsn(), min_size=1, max_size=1)
        opened = ProjectorResources(
            ledger=ledger,
            search_http=httpx.AsyncClient(),
            execution_sql=PooledSql(ledger, settings.agent_db_acquire_timeout_s),
        )

        jobs = build_projector(settings, opened)

        assert isinstance(jobs.index_admin, OpenSearchIndexAdmin)
        assert isinstance(jobs.drain, SearchOutboxDrainScheduler)
        assert isinstance(jobs.ledger_events, LedgerEventConsumer)


class Test축마다_다른_잠금과_같은_주기를_쓴다:
    def test_자문_잠금_열쇠를_계약의_base_와_axisOffset_에서_만든다(self) -> None:
        lock = _DRAIN["advisoryLock"]

        assert search_outbox_drain_lock_key() == lock["base"] + lock["axisOffset"][AGENT_BACKEND]

    def test_자문_잠금_열쇠가_상대_축의_열쇠와_다르다(self) -> None:
        # 두 축이 같은 열쇠를 얻으려 하면 한 축이 배출하는 동안 다른 축이 아무것도 하지 못한다.
        assert search_outbox_drain_lock_key() != _DRAIN["advisoryLock"]["byAxis"][OTHER_AXIS]

    def test_배출_주기가_계약이_적은_밀리초와_같다(self) -> None:
        assert search_outbox_drain_interval_s() * 1000 == _DRAIN["intervalMs"]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    order: list[str] = []
    store = SqliteLedgerSql()
    jobs, _ = _jobs(order)
    ledger = LedgerPoolProvider("postgresql://none/none", min_size=1, max_size=1)
    opened = ProjectorResources(
        ledger=ledger, search_http=httpx.AsyncClient(), execution_sql=SingleSql(store)
    )

    @asynccontextmanager
    async def resources(_settings: Settings) -> AsyncIterator[ProjectorResources]:
        yield opened

    monkeypatch.setattr(app_module, "projector_resources", resources)
    monkeypatch.setattr(app_module, "build_projector", lambda *_: jobs)
    with TestClient(app_module.create_app()) as test_client:
        yield test_client
    store.close()


class Test프로브가_배포에_기동_여부를_알린다:
    def test_헬스체크가_정상_응답을_낸다(self, client: TestClient) -> None:
        res = client.get("/health")

        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

    def test_준비_프로브가_원장에_닿으면_ok를_낸다(self, client: TestClient) -> None:
        res = client.get("/health/ready")

        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

    def test_준비_프로브가_원장에_닿지_못하면_503을_낸다(self, client: TestClient) -> None:
        client.app.state.execution_sql = UnreachableSql()

        res = client.get("/health/ready")

        assert res.status_code == 503
        assert res.json() == {"status": "unready"}

    def test_계약의_창구를_열지_않는다(self, client: TestClient) -> None:
        res = client.get("/api/agent/recipes/search", params={"q": "test"})

        assert res.status_code == 404


class UnreachableSql:
    """원장에 닿지 못하는 창구를 대신한다."""

    def connect(self) -> Any:
        """빌리려는 순간 닿지 못했다고 알린다."""
        return self._fail()

    @asynccontextmanager
    async def _fail(self) -> AsyncIterator[Any]:
        raise ConnectionError("원장에 닿지 못한다")
        yield
