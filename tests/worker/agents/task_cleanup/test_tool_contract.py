"""task-cleanup 도구 표면과 제안 종류를 계약으로 검증한다."""

from __future__ import annotations

from typing import Any, get_args

import pytest
from pydantic import BaseModel, ValidationError

from tests.support.contract import (
    agent_tools,
    tool_arg,
    tool_arg_descriptions,
    tool_arg_partition,
    tool_descriptions,
)
from tests.support.fakes import FakeTracerApi
from tracer_agent.shared.agents.task_cleanup.models import (
    CLEANUP_REVIEWER_ROLE,
    MAX_EVIDENCE_EVENT_IDS,
    MAX_INSPECT_TURNS,
    MAX_REDISPATCH_ROUNDS,
    MAX_SUGGESTIONS,
    CandidatePage,
    CleanupBatch,
    CleanupCandidate,
    CleanupEvent,
    CleanupSuggestionKind,
    EventPage,
    InspectAssignment,
    InspectReport,
)
from tracer_agent.worker.agents.task_cleanup.failures import WORKER_FAILED
from tracer_agent.worker.agents.task_cleanup.reader import CleanupLedgerReader
from tracer_agent.worker.agents.task_cleanup.tools import (
    COORDINATOR_TOOL_NAMES,
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_EVENT_LIMIT,
    DEFAULT_EVENT_ORDER,
    GET_TASK_EVENTS,
    GET_TASK_EVENTS_DESCRIPTION,
    LIST_CANDIDATE_TASKS,
    LIST_CANDIDATE_TASKS_DESCRIPTION,
    EventOrder,
    GetTaskEventsArgs,
    ListCandidateTasksArgs,
    build_cleanup_registry,
    candidate_page,
    validate_tool_args,
)


def _contract() -> Any:
    return agent_tools("task-cleanup")


def _langchain_tools() -> dict[str, Any]:
    registry = build_cleanup_registry(
        CleanupLedgerReader(FakeTracerApi()),  # type: ignore[arg-type]
        CleanupBatch(),
        {},
        {},
        agent_name="task-cleanup",
    )
    return {tool.name: tool for tool in registry.langchain_tools()}


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


def test_list_candidate_tasks의_필수와_선택_인자가_계약과_같다() -> None:
    declared = tool_arg_partition("task-cleanup", LIST_CANDIDATE_TASKS)

    assert _partition(ListCandidateTasksArgs) == declared


def test_get_task_events의_필수와_선택_인자가_계약과_같다() -> None:
    declared = tool_arg_partition("task-cleanup", GET_TASK_EVENTS)

    assert _partition(GetTaskEventsArgs) == declared


def test_list_candidate_tasks의_limit_기본값과_상하한이_계약과_같다() -> None:
    limit = tool_arg("task-cleanup", LIST_CANDIDATE_TASKS, "limit")

    assert limit["default"] == DEFAULT_CANDIDATE_LIMIT
    assert ListCandidateTasksArgs().limit is None
    assert ListCandidateTasksArgs(limit=limit["max"]).limit == limit["max"]
    assert ListCandidateTasksArgs(limit=limit["min"]).limit == limit["min"]
    with pytest.raises(ValidationError):
        ListCandidateTasksArgs(limit=limit["max"] + 1)
    with pytest.raises(ValidationError):
        ListCandidateTasksArgs(limit=limit["min"] - 1)


def test_get_task_events의_limit_기본값과_상하한이_계약과_같다() -> None:
    limit = tool_arg("task-cleanup", GET_TASK_EVENTS, "limit")

    assert limit["default"] == DEFAULT_EVENT_LIMIT
    assert GetTaskEventsArgs(taskId="task-1").limit is None
    assert GetTaskEventsArgs(taskId="task-1", limit=limit["max"]).limit == limit["max"]
    assert GetTaskEventsArgs(taskId="task-1", limit=limit["min"]).limit == limit["min"]
    with pytest.raises(ValidationError):
        GetTaskEventsArgs(taskId="task-1", limit=limit["max"] + 1)
    with pytest.raises(ValidationError):
        GetTaskEventsArgs(taskId="task-1", limit=limit["min"] - 1)


def test_get_task_events의_읽기_방향_기본값과_허용값이_계약과_같다() -> None:
    order = tool_arg("task-cleanup", GET_TASK_EVENTS, "order")

    assert order["default"] == DEFAULT_EVENT_ORDER
    assert list(get_args(EventOrder)) == order["values"]
    assert GetTaskEventsArgs(taskId="task-1").order is None
    with pytest.raises(ValidationError):
        GetTaskEventsArgs(taskId="task-1", order="sideways")  # type: ignore[arg-type]


def test_생략한_인자는_검증을_통과하고_실행이_기본값을_채운다() -> None:
    assert validate_tool_args(GET_TASK_EVENTS, {"taskId": "task-1"}) == {"taskId": "task-1"}
    assert validate_tool_args(LIST_CANDIDATE_TASKS, {}) == {}
    assert candidate_page(CleanupBatch(), None, None).candidates == []


def test_제안_종류가_계약과_같다() -> None:
    assert list(get_args(CleanupSuggestionKind)) == _contract()["outputKinds"]


def test_제안_상한과_근거_상한이_계약과_같다() -> None:
    limits = _contract()["limits"]

    assert limits["maxSuggestions"] == MAX_SUGGESTIONS
    assert limits["maxEvidenceEventIds"] == MAX_EVIDENCE_EVENT_IDS


def test_list_candidate_tasks의_응답_본문이_계약과_같다() -> None:
    responses = _contract()["responses"][LIST_CANDIDATE_TASKS]

    assert set(CandidatePage.model_fields) == set(responses["page"])
    assert set(CleanupCandidate.model_fields) == set(responses["item"])


def test_get_task_events의_응답_본문이_계약과_같다() -> None:
    responses = _contract()["responses"][GET_TASK_EVENTS]

    assert set(EventPage.model_fields) == set(responses["page"])
    assert set(CleanupEvent.model_fields) == set(responses["item"])


def test_워커가_응답에_필드를_늘려도_도구_루프가_깨지지_않는다() -> None:
    page = CandidatePage.model_validate(
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
            "truncated": False,
            "total": 1,
            "moreCandidatesOutsideBatch": False,
            "scannedAt": "2026-07-14T00:00:00Z",
        }
    )

    assert [candidate.id for candidate in page.candidates] == ["task-1"]


def test_도구_설명이_계약과_같다() -> None:
    assert tool_descriptions("task-cleanup") == {
        LIST_CANDIDATE_TASKS: LIST_CANDIDATE_TASKS_DESCRIPTION,
        GET_TASK_EVENTS: GET_TASK_EVENTS_DESCRIPTION,
    }


def test_검토가_무너진_사유_문구가_계약과_같다() -> None:
    failures = _contract()["failures"]

    assert failures["workerFailed"] == WORKER_FAILED
    assert WORKER_FAILED.format(reason="ledger down") == "Investigation failed: ledger down"


def test_인자_설명이_계약과_같다() -> None:
    contract = tool_arg_descriptions("task-cleanup")
    shown = {
        LIST_CANDIDATE_TASKS: {
            arg: field.description for arg, field in ListCandidateTasksArgs.model_fields.items()
        },
        GET_TASK_EVENTS: {arg: field.description for arg, field in GetTaskEventsArgs.model_fields.items()},
    }

    assert shown == contract
