"""잡 조회 창구가 원장 한 벌을 계약이 정한 칸과 순서로 내는지 검증한다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from tests.support.contract import conformance_case
from tests.support.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.shared.models import AgentStepDTO, AgentStepToolCall
from tracer_agent.shared.workflows.jobs_ledger import JobLedger
from tracer_agent.shared.workflows.jobs_query import (
    HISTORY_LIMIT_DEFAULT,
    HISTORY_LIMIT_MAX,
    JOB_LEDGER_KINDS,
    JOB_STATUSES,
)

PATH = "/api/agent/jobs"
_RESPONSE = conformance_case("job.intake")["response"]
JOB_FIELDS = _RESPONSE["jobFields"]
REQUIRED_STEP_FIELDS = _RESPONSE["steps"]["required"]
OPTIONAL_STEP_FIELDS = _RESPONSE["steps"]["optional"]
NOW = datetime(2026, 7, 30, tzinfo=UTC)


async def seed_job(store: SqliteLedgerSql, user_id: str = "local") -> None:
    """조회가 읽을 잡 한 행과 그 궤적을 기록한다."""
    ledger = JobLedger(store)
    await ledger.claim(
        "j1", user_id, "recipe.scan", "temporal", "task-1", None, None, {"taskId": "task-1"}, NOW
    )
    await ledger.mark_running("j1", NOW)
    await ledger.record_steps(
        "j1",
        user_id,
        2,
        [
            AgentStepDTO(
                seq=0, role="orchestration", content="retried", nodeName="survey", eventKind="node.started"
            )
        ],
        NOW,
    )
    await ledger.record_steps(
        "j1",
        user_id,
        1,
        [
            AgentStepDTO(
                seq=0,
                role="assistant",
                content="calling",
                toolCalls=[AgentStepToolCall(id="c1", name="get_task", args={"taskId": "task-1"})],
                outputTokens=11,
                stopReason="tool_use",
            ),
            AgentStepDTO(seq=1, role="tool", content="done", toolName="get_task", toolCallId="c1"),
        ],
        NOW,
    )
    await ledger.settle("j1", "completed", {"recipes": []}, {"costUsd": 0.2}, None, NOW)


async def test_잡_조회는_원장_행을_계약이_정한_칸으로_낸다(
    client: TestClient, store: SqliteLedgerSql
) -> None:
    await seed_job(store)

    res = client.get(f"{PATH}/j1")

    assert res.status_code == 200
    job = res.json()["data"]["job"]
    assert list(job) == JOB_FIELDS
    assert job["status"] == "completed"
    assert job["attempts"] == 1
    assert job["taskId"] == "task-1"
    assert job["result"] == {"recipes": []}
    assert job["usage"] == {"costUsd": 0.2}
    assert job["error"] is None


async def test_없는_잡_조회는_404다(client: TestClient) -> None:
    res = client.get(f"{PATH}/no-such")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


async def test_남의_잡은_존재_여부도_드러내지_않는다(client: TestClient, store: SqliteLedgerSql) -> None:
    await seed_job(store, user_id="u2")

    assert client.get(f"{PATH}/j1").status_code == 404
    assert client.get(f"{PATH}/j1/steps").status_code == 404
    assert client.post(f"{PATH}/j1/cancel").status_code == 404


async def test_궤적_조회는_시도와_순번의_오름차순으로_낸다(
    client: TestClient, store: SqliteLedgerSql
) -> None:
    await seed_job(store)

    res = client.get(f"{PATH}/j1/steps")

    assert res.status_code == 200
    steps = res.json()["data"]
    assert [(step["attempt"], step["seq"]) for step in steps] == [(1, 0), (1, 1), (2, 0)]


async def test_궤적_한_줄은_값이_있는_자리만_싣는다(client: TestClient, store: SqliteLedgerSql) -> None:
    await seed_job(store)

    steps = client.get(f"{PATH}/j1/steps").json()["data"]

    assert all(set(REQUIRED_STEP_FIELDS) <= set(step) for step in steps)
    assert all(set(step) <= set(REQUIRED_STEP_FIELDS) | set(OPTIONAL_STEP_FIELDS) for step in steps)
    assert steps[0]["toolCalls"] == [{"id": "c1", "name": "get_task", "args": {"taskId": "task-1"}}]
    assert steps[0]["outputTokens"] == 11
    assert steps[0]["stopReason"] == "tool_use"
    assert "toolName" not in steps[0]
    assert steps[1]["toolCalls"] == []
    assert steps[1]["toolName"] == "get_task"
    assert steps[2]["eventKind"] == "node.started"
    assert steps[2]["nodeName"] == "survey"


async def test_궤적이_없는_잡은_빈_목록을_낸다(client: TestClient, store: SqliteLedgerSql) -> None:
    await JobLedger(store).claim("j2", "local", "task.cleanup", "temporal", None, None, None, {}, NOW)

    res = client.get(f"{PATH}/j2/steps")

    assert res.status_code == 200
    assert res.json() == {"ok": True, "data": []}


async def seed_history(store: SqliteLedgerSql) -> None:
    """이력과 최신 잡 조회가 읽을 여러 행을 시각을 벌려 기록한다."""
    ledger = JobLedger(store)
    for index, (job_id, kind, task_id, status) in enumerate(
        [
            ("h1", "recipe.scan", "task-1", "completed"),
            ("h2", "title.suggestion", "task-1", "failed"),
            ("h3", "recipe.scan", "task-2", "pending"),
            ("h4", "rule.generation", None, "completed"),
        ]
    ):
        await ledger.claim(
            job_id,
            "local",
            kind,
            "temporal",
            task_id,
            None,
            None,
            {},
            NOW + timedelta(seconds=index),
        )
        if status != "pending":
            await ledger.settle(job_id, status, {}, {}, None, NOW + timedelta(seconds=index))
    await ledger.claim("other", "u2", "recipe.scan", "temporal", "task-1", None, None, {}, NOW)


class Test원장의_잡_종류:
    def test_조회가_받는_잡_종류가_케이스와_같다(self) -> None:
        assert list(JOB_LEDGER_KINDS) == _RESPONSE["ledgerKinds"]

    def test_이력이_받는_상태가_케이스와_같다(self) -> None:
        assert list(JOB_STATUSES) == _RESPONSE["history"]["query"]["status"]["enum"]


class Test대기_잡:
    async def test_대기_잡만_접수_시각의_오름차순으로_낸다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        await seed_history(store)
        spec = _RESPONSE["pending"]

        res = client.get(spec["path"], params={"kind": "recipe.scan", "status": "pending"})

        assert res.status_code == spec["status"]
        data = res.json()["data"]
        assert list(data) == list(spec["data"])
        assert [job["id"] for job in data["items"]] == ["h3"]
        assert list(data["items"][0]) == JOB_FIELDS

    async def test_남의_대기_잡은_싣지_않는다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        await seed_history(store)

        items = client.get(PATH, params={"kind": "recipe.scan"}).json()["data"]["items"]

        assert all(job["userId"] == "local" for job in items)

    def test_종류가_없거나_원장의_것이_아니면_거절한다(self, client: TestClient) -> None:
        assert client.get(PATH).status_code == 400
        assert client.get(PATH, params={"kind": "없는 종류"}).status_code == 400

    def test_대기가_아닌_상태를_실으면_거절한다(self, client: TestClient) -> None:
        res = client.get(PATH, params={"kind": "recipe.scan", "status": "completed"})

        assert res.status_code == 400


class Test잡_이력:
    async def test_이력을_접수_시각의_내림차순으로_내고_전체_개수를_함께_싣는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        await seed_history(store)
        spec = _RESPONSE["history"]

        res = client.get(spec["path"], params={"limit": 2})

        assert res.status_code == spec["status"]
        data = res.json()["data"]
        assert list(data) == list(spec["data"])
        assert [job["id"] for job in data["items"]] == ["h4", "h3"]
        assert data["total"] == 4
        assert list(data["items"][0]) == JOB_FIELDS

    async def test_건너뛰기는_전체_개수를_줄이지_않는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        await seed_history(store)

        data = client.get(f"{PATH}/history", params={"limit": 2, "offset": 2}).json()["data"]

        assert [job["id"] for job in data["items"]] == ["h2", "h1"]
        assert data["total"] == 4

    async def test_종류와_상태로_거른_이력만_낸다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        await seed_history(store)

        data = client.get(f"{PATH}/history", params={"kind": "recipe.scan", "status": "completed"}).json()[
            "data"
        ]

        assert [job["id"] for job in data["items"]] == ["h1"]
        assert data["total"] == 1

    async def test_상한을_실지_않으면_케이스가_적은_기본값을_쓴다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        await seed_history(store)
        spec = _RESPONSE["history"]["query"]

        assert spec["limit"]["default"] == HISTORY_LIMIT_DEFAULT
        assert spec["limit"]["maximum"] == HISTORY_LIMIT_MAX
        assert len(client.get(f"{PATH}/history").json()["data"]["items"]) == 4

    def test_상한과_건너뛰기가_범위를_벗어나면_조회하지_않는다(self, client: TestClient) -> None:
        assert client.get(f"{PATH}/history", params={"limit": 0}).status_code == 400
        assert client.get(f"{PATH}/history", params={"limit": 101}).status_code == 400
        assert client.get(f"{PATH}/history", params={"offset": -1}).status_code == 400
        assert client.get(f"{PATH}/history", params={"kind": "없는 종류"}).status_code == 400


class Test최신_잡:
    async def test_종류와_태스크_조합의_가장_최근_잡을_낸다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        await seed_history(store)
        spec = _RESPONSE["latest"]

        res = client.get(spec["path"], params={"kind": "recipe.scan", "taskId": "task-1"})

        assert res.status_code == spec["status"]
        data = res.json()["data"]
        assert list(data) == list(spec["data"])
        assert data["job"]["id"] == "h1"
        assert list(data["job"]) == JOB_FIELDS

    async def test_태스크를_실지_않으면_그_종류의_가장_최근_잡을_낸다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        await seed_history(store)

        data = client.get(f"{PATH}/latest", params={"kind": "recipe.scan"}).json()["data"]

        assert data["job"]["id"] == "h3"

    async def test_조건에_맞는_잡이_없으면_404가_아니라_빈_자리를_낸다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        await seed_history(store)

        res = client.get(f"{PATH}/latest", params={"kind": "task.cleanup"})

        assert res.status_code == 200
        assert res.json()["data"] == {"job": None}

    async def test_고정_경로가_잡_식별자보다_먼저_잡힌다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        await seed_history(store)

        assert client.get(f"{PATH}/latest", params={"kind": "recipe.scan"}).status_code == 200
        assert client.get(f"{PATH}/history").status_code == 200
