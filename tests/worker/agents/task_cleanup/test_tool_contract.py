"""task-cleanup 도구 표면과 제안 종류를 계약으로 검증한다."""

from __future__ import annotations

from typing import Any, get_args

from pydantic import BaseModel

from tests.support.contract import (
    agent_tools,
    tool_arg_descriptions,
    tool_arg_partition,
    tool_descriptions,
)
from tracer_agent.shared.agents.task_cleanup.models import (
    CLEANUP_REVIEWER_ROLE,
    MAX_EVIDENCE_EVENT_IDS,
    MAX_INSPECT_TURNS,
    MAX_REDISPATCH_ROUNDS,
    MAX_SUGGESTIONS,
    CleanupBatch,
    CleanupCandidate,
    CleanupEvent,
    CleanupSuggestionKind,
    EventPage,
    InspectAssignment,
    InspectReport,
)
from tracer_agent.worker.agents.task_cleanup.failures import WORKER_FAILED
from tracer_agent.worker.agents.task_cleanup.tools import (
    CLEANUP_TOOLS,
    COORDINATOR_TOOL_NAMES,
    GET_TASK_EVENTS,
    GET_TASK_EVENTS_DESCRIPTION,
    GetTaskEventsArgs,
)


def _contract() -> Any:
    return agent_tools("task-cleanup")


def _langchain_tools() -> dict[str, Any]:
    return {tool.name: tool for tool in CLEANUP_TOOLS.langchain_tools()}


def _partition(args_model: type[BaseModel]) -> tuple[set[str], set[str]]:
    required = {name for name, field in args_model.model_fields.items() if field.is_required()}
    return required, set(args_model.model_fields) - required


def test_모델에게_여는_도구_이름이_계약과_같다() -> None:
    assert set(_langchain_tools()) == set(_contract()["tools"])


def test_정리_후보_검토_전문가의_역할과_보고가_계약과_같다() -> None:
    orchestration = _contract()["orchestration"]

    assert orchestration["workerMaxTurns"] == MAX_INSPECT_TURNS
    assert orchestration["roles"] == {CLEANUP_REVIEWER_ROLE: [GET_TASK_EVENTS]}
    assert list(InspectReport.model_fields) == orchestration["workerReport"]["required"]


def test_조율자_도구와_재파견_상한이_계약과_같다() -> None:
    orchestration = _contract()["orchestration"]
    redispatch = orchestration["redispatchRequest"]

    assert orchestration["coordinatorTools"] == list(COORDINATOR_TOOL_NAMES)
    assert _contract()["limits"]["maxRedispatchRounds"] == MAX_REDISPATCH_ROUNDS
    assert orchestration["emptyAssignmentEndsEmpty"] is True
    assert redispatch["required"] == list(InspectAssignment.model_fields)
    assert redispatch["maxTasks"] == MAX_SUGGESTIONS


def test_표준_tool이_runtime을_숨기고_계약이_적은_인자만_노출한다() -> None:
    tools = _langchain_tools()

    assert set(tools) == set(_contract()["tools"])
    for name, tool in tools.items():
        schema = tool.tool_call_schema.model_json_schema()
        required, optional = tool_arg_partition("task-cleanup", name)
        assert set(schema.get("required", [])) == required
        assert set(schema["properties"]) == required | optional
        assert "runtime" not in schema["properties"]


def test_get_task_events의_필수와_선택_인자가_계약과_같다() -> None:
    declared = tool_arg_partition("task-cleanup", GET_TASK_EVENTS)

    assert _partition(GetTaskEventsArgs) == declared


def test_제안_종류가_계약과_같다() -> None:
    assert list(get_args(CleanupSuggestionKind)) == _contract()["outputKinds"]


def test_제안_상한과_근거_상한이_계약과_같다() -> None:
    limits = _contract()["limits"]

    assert limits["maxSuggestions"] == MAX_SUGGESTIONS
    assert limits["maxEvidenceEventIds"] == MAX_EVIDENCE_EVENT_IDS


def test_접수가_실어_보내는_후보의_본문이_계약과_같다() -> None:
    assert set(CleanupCandidate.model_fields) == set(_contract()["candidateBatch"]["item"])


def test_get_task_events의_응답_본문이_계약과_같다() -> None:
    responses = _contract()["responses"][GET_TASK_EVENTS]

    assert set(EventPage.model_fields) == set(responses["page"])
    assert set(CleanupEvent.model_fields) == set(responses["item"])


def test_접수가_필드를_늘려도_후보_배치가_깨지지_않는다() -> None:
    batch = CleanupBatch.model_validate(
        {
            "candidates": [
                {
                    "id": "task-1",
                    "visibleTitle": "",
                    "status": "running",
                    "lastEventAt": None,
                    "hasEvents": False,
                    "activeChildCount": 0,
                    "candidateReasons": ["no-events"],
                    "archivedAt": None,
                }
            ],
            "batchTruncated": False,
            "scannedAt": "2026-07-14T00:00:00Z",
        }
    )

    assert [candidate.id for candidate in batch.candidates] == ["task-1"]


def test_도구_설명이_계약과_같다() -> None:
    assert tool_descriptions("task-cleanup") == {GET_TASK_EVENTS: GET_TASK_EVENTS_DESCRIPTION}


def test_검토가_무너진_사유_문구가_계약과_같다() -> None:
    failures = _contract()["failures"]

    assert failures["workerFailed"] == WORKER_FAILED
    assert WORKER_FAILED.format(reason="ledger down") == "Investigation failed: ledger down"


def test_인자_설명이_계약과_같다() -> None:
    contract = tool_arg_descriptions("task-cleanup")
    shown = {
        GET_TASK_EVENTS: {arg: field.description for arg, field in GetTaskEventsArgs.model_fields.items()},
    }

    assert shown == contract
