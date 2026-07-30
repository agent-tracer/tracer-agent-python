"""잡 실행 행이 조건부 갱신으로만 전진하고 궤적이 시도와 순번으로 갈려 남는지 검증한다."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from tests.support.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.shared.models import AgentStepDTO, AgentStepToolCall
from tracer_agent.shared.workflows.jobs_ledger import JobLedger

NOW = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
LATER = datetime(2026, 7, 28, 0, 5, tzinfo=UTC)


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    yield ledger
    ledger.close()


async def claim(
    ledger: JobLedger,
    job_id: str = "j1",
    idempotency_key: str | None = None,
    task_id: str | None = None,
) -> bool:
    """대기 행 하나를 세운다."""
    return await ledger.claim(
        job_id,
        "u1",
        "title.suggestion",
        "temporal",
        task_id,
        idempotency_key,
        None if idempotency_key is None else "hash-1",
        {"taskId": task_id} if task_id else {},
        NOW,
    )


async def test_새_잡은_대기_행으로_세워진다(store: SqliteLedgerSql) -> None:
    ledger = JobLedger(store)

    assert await claim(ledger, idempotency_key="idem-1", task_id="task-1") is True

    row = store.rows("ai_jobs")[0]
    assert row["status"] == "pending"
    assert row["executor"] == "temporal"
    assert row["attempts"] == 0
    assert row["kind"] == "title.suggestion"
    assert row["idempotency_key"] == "idem-1"
    assert row["task_id"] == "task-1"
    assert row["input"] == {"taskId": "task-1"}
    assert row["result"] == {}
    assert row["usage"] == {}


async def test_같은_식별자를_다시_보내도_새_행이_생기지_않는다(store: SqliteLedgerSql) -> None:
    ledger = JobLedger(store)
    await claim(ledger, idempotency_key="idem-1")

    assert await claim(ledger, idempotency_key="idem-1") is False

    assert len(store.rows("ai_jobs")) == 1


async def test_같은_멱등키가_다른_식별자로_와도_거짓을_낸다(store: SqliteLedgerSql) -> None:
    ledger = JobLedger(store)
    await claim(ledger, idempotency_key="idem-1")

    assert await claim(ledger, job_id="j2", idempotency_key="idem-1") is False

    assert len(store.rows("ai_jobs")) == 1


async def test_멱등키로_먼저_세운_잡과_그_입력_해시를_찾는다(store: SqliteLedgerSql) -> None:
    ledger = JobLedger(store)
    await claim(ledger, idempotency_key="idem-1")

    row = await ledger.find_by_idempotency("u1", "title.suggestion", "idem-1")

    assert row is not None
    assert row["id"] == "j1"
    assert row["idempotency_input_hash"] == "hash-1"


async def test_쓰인_적_없는_멱등키는_빈_자리를_낸다(store: SqliteLedgerSql) -> None:
    ledger = JobLedger(store)
    await claim(ledger, idempotency_key="idem-1")

    assert await ledger.find_by_idempotency("u1", "title.suggestion", "idem-2") is None


async def test_멱등키가_없으면_서로_다른_잡이_각자_행을_세운다(store: SqliteLedgerSql) -> None:
    ledger = JobLedger(store)

    assert await claim(ledger) is True
    assert await claim(ledger, job_id="j2") is True

    assert len(store.rows("ai_jobs")) == 2


async def test_실행_중_표시는_시도_횟수를_올린다(store: SqliteLedgerSql) -> None:
    ledger = JobLedger(store)
    await claim(ledger)

    assert await ledger.mark_running("j1", LATER) is True

    row = store.rows("ai_jobs")[0]
    assert row["status"] == "running"
    assert row["attempts"] == 1
    assert row["started_at"] == LATER


async def test_다시_태운_시도도_시도_횟수에_더해진다(store: SqliteLedgerSql) -> None:
    ledger = JobLedger(store)
    await claim(ledger)
    await ledger.mark_running("j1", NOW)

    assert await ledger.mark_running("j1", LATER) is True

    row = store.rows("ai_jobs")[0]
    assert row["attempts"] == 2
    assert row["started_at"] == NOW


async def test_종료는_산출과_사용량을_함께_남긴다(store: SqliteLedgerSql) -> None:
    ledger = JobLedger(store)
    await claim(ledger)
    await ledger.mark_running("j1", NOW)

    settled = await ledger.settle("j1", "completed", {"suggestions": []}, {"costUsd": 0.42}, None, LATER)

    assert settled is True
    row = store.rows("ai_jobs")[0]
    assert row["status"] == "completed"
    assert row["result"] == {"suggestions": []}
    assert row["usage"] == {"costUsd": 0.42}
    assert row["completed_at"] == LATER


async def test_이미_끝난_잡은_다시_닫히지_않는다(store: SqliteLedgerSql) -> None:
    ledger = JobLedger(store)
    await claim(ledger)
    await ledger.settle("j1", "completed", {}, {}, None, NOW)

    assert await ledger.settle("j1", "failed", {}, {}, "boom", LATER) is False

    assert store.rows("ai_jobs")[0]["status"] == "completed"


async def test_취소는_살아_있는_잡만_닫는다(store: SqliteLedgerSql) -> None:
    ledger = JobLedger(store)
    await claim(ledger)

    assert await ledger.cancel("j1", LATER) is True
    assert await ledger.cancel("j1", LATER) is False

    row = store.rows("ai_jobs")[0]
    assert row["status"] == "canceled"
    assert row["completed_at"] == LATER


async def test_궤적은_시도와_순번의_오름차순으로_읽힌다(store: SqliteLedgerSql) -> None:
    ledger = JobLedger(store)
    await claim(ledger)
    await ledger.record_steps(
        "j1", "u1", 2, [AgentStepDTO(seq=0, role="assistant", content="second attempt")], LATER
    )
    await ledger.record_steps(
        "j1",
        "u1",
        1,
        [
            AgentStepDTO(
                seq=0,
                role="assistant",
                content="calling",
                toolCalls=[AgentStepToolCall(id="c1", name="get_task", args={})],
                outputTokens=7,
            ),
            AgentStepDTO(seq=1, role="tool", content="done", toolName="get_task", toolCallId="c1"),
        ],
        NOW,
    )

    steps = await ledger.steps("j1", "u1")

    assert [(row["attempt"], row["seq"]) for row in steps] == [(1, 0), (1, 1), (2, 0)]
    assert steps[0]["tool_calls"] == [{"id": "c1", "name": "get_task", "args": {}}]
    assert steps[0]["output_tokens"] == 7
    assert steps[1]["tool_name"] == "get_task"


async def test_아무것도_싣지_못한_스텝은_궤적에_적히지_않는다(store: SqliteLedgerSql) -> None:
    ledger = JobLedger(store)
    await claim(ledger)

    await ledger.record_steps("j1", "u1", 1, [AgentStepDTO(seq=0, role="assistant", content="  ")], NOW)

    assert await ledger.steps("j1", "u1") == []


async def test_같은_시도의_같은_순번은_한_번만_적힌다(store: SqliteLedgerSql) -> None:
    ledger = JobLedger(store)
    await claim(ledger)
    step = AgentStepDTO(seq=0, role="assistant", content="once")

    await ledger.record_steps("j1", "u1", 1, [step], NOW)
    await ledger.record_steps("j1", "u1", 1, [step], LATER)

    assert len(await ledger.steps("j1", "u1")) == 1


async def test_남의_잡_궤적은_읽히지_않는다(store: SqliteLedgerSql) -> None:
    ledger = JobLedger(store)
    await claim(ledger)
    await ledger.record_steps("j1", "u1", 1, [AgentStepDTO(seq=0, role="assistant", content="mine")], NOW)

    assert await ledger.steps("j1", "u2") == []
