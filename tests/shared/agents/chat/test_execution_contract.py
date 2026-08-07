"""chat 실행 상태 기계가 계약 케이스의 판정을 그대로 내는지 대조한다."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.support.contract import agent_cases
from tracer_agent.shared.agents.chat.execution_ledger import ChatExecutionLedger, ChatExecutionSpend
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.shared.axis import AGENT_BACKEND

CONTRACT = agent_cases("chat")["cases"]

DATE_KEYS = frozenset({"createdAt", "updatedAt", "startedAt", "completedAt"})

_CAMEL_BREAK = re.compile(r"(?<!^)(?=[A-Z])")


def column(name: str) -> str:
    """계약이 쓰는 낙타 이름을 원장 열 이름으로 바꾼다."""
    return _CAMEL_BREAK.sub("_", name).lower()


def moment(value: str) -> datetime:
    """계약이 적은 시각 문자열을 UTC 시각으로 읽는다."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def wire(value: Any) -> Any:
    """원장에서 읽은 값을 계약이 적은 모양으로 되돌린다."""
    if isinstance(value, datetime):
        return f"{value.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z"
    return value


def seed_row(case_row: Mapping[str, Any]) -> dict[str, Any]:
    """계약이 부분으로 적은 행을 원장이 받는 온전한 행으로 채운다."""
    identifier = str(case_row["id"])
    row: dict[str, Any] = {
        "id": identifier,
        "replay_anchor_message_id": f"message-{identifier}",
        "client_request_id": f"request-{identifier}",
        "input_hash": f"hash-{identifier}",
        "status": "queued",
        "requested_backend": AGENT_BACKEND,
    }
    for source in (CONTRACT["row"], case_row):
        for key, value in source.items():
            if key == "id":
                continue
            row[column(key)] = moment(value) if key in DATE_KEYS and value is not None else value
    return row


def spend() -> ChatExecutionSpend:
    """완료와 취소 산출물 연산이 함께 넘기는 지출을 계약에서 만든다."""
    declared = CONTRACT["spend"]
    return ChatExecutionSpend(
        model_used=declared["modelUsed"],
        cost_usd=declared["costUsd"],
        num_turns=declared["numTurns"],
        stop_reason=declared["stopReason"],
        usage=declared["usage"],
    )


async def invoke(ledger: ChatExecutionLedger, operation: str, call: Mapping[str, Any]) -> Any:
    """계약이 부르는 연산 하나를 상태 기계에 그대로 실행한다."""
    now = moment(call["now"])
    identifier = call.get("id", "")
    if operation == "recoverStaleRunning":
        return await ledger.recover_stale_running(moment(call["idleBefore"]), now, call.get("threadId"))
    if operation == "claimQueued":
        return await ledger.claim_queued(identifier, now)
    if operation == "beginAttempt":
        return await ledger.begin_attempt(identifier, call["attempt"], call["draftTokenHash"], now)
    if operation == "checkpointRunning":
        return await ledger.checkpoint_running(
            identifier, call["attempt"], call["draftText"], call["draftSeq"], "responding", now
        )
    if operation == "completeRunning":
        return await ledger.complete_running(identifier, call["assistantMessageId"], spend(), now)
    if operation == "recordCanceledOutcome":
        return await ledger.record_canceled_outcome(identifier, call["assistantMessageId"], spend(), now)
    if operation == "failActive":
        return await ledger.fail_active(identifier, call["error"], now)
    if operation == "cancelActive":
        return await ledger.cancel_active(identifier, now)
    raise AssertionError(f"계약 케이스가 모르는 연산을 부른다: {operation}")


def cases() -> Iterator[Any]:
    """계약이 담은 연산별 케이스를 하나씩 나열한다."""
    for operation in CONTRACT["operations"]:
        for case in operation["cases"]:
            yield pytest.param(operation["operation"], case, id=f"{operation['operation']}:{case['name']}")


@pytest.mark.parametrize(("operation", "case"), list(cases()))
async def test_계약_케이스대로_상태_기계가_판정한다(operation: str, case: Mapping[str, Any]) -> None:
    store = SqliteLedgerSql()
    try:
        store.seed("chat_executions", [seed_row(row) for row in case["given"]])
        status = {"completeRunning": "succeeded", "failActive": "failed", "cancelActive": "cancelled"}.get(
            operation
        )
        if status is not None and any(row.get("status") == "running" for row in case["given"]):
            store.seed("agent_run_observations", [observation_row(status)])
        ledger = ChatExecutionLedger(store)

        result = await invoke(ledger, operation, case["call"])

        rows = []
        for expected in case["expect"]["rows"]:
            stored = await ledger.find_by_id(expected["id"])
            assert stored is not None
            rows.append({key: wire(stored[column(key)]) for key in expected})
        assert {"result": result, "rows": rows} == case["expect"]
    finally:
        store.close()


def observation_row(status: str) -> dict[str, Any]:
    return {
        "execution_id": "e1",
        "attempt_id": "1",
        "user_id": "u1",
        "agent_name": "chat",
        "backend": "python",
        "model_requested": "model",
        "prompt_version": "1.0.0",
        "tool_contract_version": "1.0.0",
        "status": status,
        "duration_ms": 1,
        "usage": {},
        "landed": True,
        "repair_attempted": False,
        "validation": {},
        "model_calls": [],
        "tool_calls": [],
        "created_at": datetime.now(UTC),
    }
