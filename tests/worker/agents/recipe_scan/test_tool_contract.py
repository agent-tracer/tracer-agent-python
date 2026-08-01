"""recipe-scan 도구 표면을 계약으로 검증한다."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from tests.support.contract import (
    agent_tools,
    tool_arg,
    tool_arg_descriptions,
    tool_arg_partition,
    tool_descriptions,
)
from tests.support.fakes import FakeTracerApi
from tracer_agent.shared.agents.recipe_scan.models import (
    MAX_PROBE_TURNS,
    MAX_RECIPE_CANDIDATES,
    MAX_REDISPATCH_PROBES,
    MAX_REDISPATCH_ROUNDS,
    Excerpt,
    ProbeAssignment,
    ProbeReport,
    ProvenanceCatalog,
    RecipeCandidate,
    RecipeVerifyAction,
    RecipeVerifyCommand,
    RecipeVerifyPattern,
)
from tracer_agent.worker.agents.recipe_scan.failures import WORKER_FAILED
from tracer_agent.worker.agents.recipe_scan.reader import RecipeLedgerReader
from tracer_agent.worker.agents.recipe_scan.search import RecipeSearchReader
from tracer_agent.worker.agents.recipe_scan.tools import (
    COORDINATOR_TOOLS,
    PROBE_TOOLS,
    RECIPE_TOOL_CLASSES,
    SearchEventsArgs,
    SearchEventsTool,
    TimelineEventKind,
    build_recipe_registry,
    validate_tool_args,
)

VALID_ARGS: dict[str, dict[str, Any]] = {
    "get_task_summary": {"taskId": "task-1"},
    "get_task_events": {"taskId": "task-1"},
    "list_rules": {"taskId": "task-1"},
    "search_events": {"q": "migration"},
    "find_similar_tasks": {"anchorTaskId": "task-1"},
    "search_recipes": {"q": "migration"},
}


# 계약이 선언한 선택 필드까지 채운 행이라야 렌더링이 필드를 빠뜨렸는지 드러난다.
def _row(event_id: str) -> dict[str, Any]:
    return {
        "id": event_id,
        "seq": 1,
        "turnId": "turn-1",
        "kind": "agent_tracer.user.message",
        "title": "마이그레이션을 추가해줘",
        "body": "본문",
        "toolName": "Bash",
        "filePaths": ["src/app.ts"],
        "occurredAt": datetime(2026, 7, 14, tzinfo=UTC).isoformat(),
    }


def _contract() -> Any:
    return agent_tools("recipe-scan")


def _tools() -> Any:
    return _contract()["tools"]


def _langchain_tools() -> list[Any]:
    registry = build_recipe_registry(
        RecipeLedgerReader(FakeTracerApi()),  # type: ignore[arg-type]
        RecipeSearchReader(FakeTracerApi()),  # type: ignore[arg-type]
        ProvenanceCatalog(),
        agent_name="recipe-scan",
    )
    return registry.langchain_tools()


def _fields(tool: str) -> Any:
    return next(cls.args_model for cls in RECIPE_TOOL_CLASSES if cls.name == tool).model_fields


def _accepts(tool: str, field: str, value: object) -> bool:
    try:
        validate_tool_args(tool, {**VALID_ARGS[tool], field: value})
    except ValidationError:
        return False
    return True


def _candidate(orders: list[int]) -> RecipeCandidate:
    return RecipeCandidate(
        title="Add a migration",
        intent="마이그레이션을 안전하게 추가한다",
        description="스키마 변경이 필요할 때 쓴다.",
        summary_md="- 변경을 정의한다",
        request="사용자가 마이그레이션 추가를 요청했다.",
        steps=[{"order": order, "action": f"step-{order}"} for order in orders],  # type: ignore[list-item]
        contributing_slices=[{"taskId": "task-1", "eventIds": ["event-1"]}],  # type: ignore[list-item]
        rationale="반복 가능한 절차다.",
    )


def test_후보_상한이_계약과_같다() -> None:
    limits = _contract()["limits"]

    assert limits["recipeCandidateLimit"] == MAX_RECIPE_CANDIDATES


def test_모델에게_노출하는_도구_이름이_계약과_같다() -> None:
    assert [cls.name for cls in RECIPE_TOOL_CLASSES] == list(_tools())


def test_전문가_역할과_도구와_보고가_계약과_같다() -> None:
    orchestration = _contract()["orchestration"]
    roles = {name: list(names) for name, names in PROBE_TOOLS.items()}

    assert orchestration["workerMaxTurns"] == MAX_PROBE_TURNS
    assert roles == orchestration["roles"]
    assert list(ProbeReport.model_fields) == orchestration["workerReport"]["required"]
    assert list(Excerpt.model_fields) == orchestration["workerReport"]["excerptRequired"]


def test_조율자_도구와_재파견_상한이_계약과_같다() -> None:
    orchestration = _contract()["orchestration"]
    redispatch = orchestration["redispatchRequest"]

    limits = _contract()["limits"]

    assert orchestration["coordinatorTools"] == list(COORDINATOR_TOOLS)
    assert limits["maxRedispatchRounds"] == MAX_REDISPATCH_ROUNDS
    assert redispatch["required"] == list(ProbeAssignment.model_fields)
    assert limits["maxRedispatchProbes"] == MAX_REDISPATCH_PROBES


def test_표준_tool이_runtime을_숨기고_계약이_적은_인자만_노출한다() -> None:
    tools = {tool.name: tool for tool in _langchain_tools()}

    assert set(tools) == set(_tools())
    for name, tool in tools.items():
        schema = tool.tool_call_schema.model_json_schema()
        required, optional = tool_arg_partition("recipe-scan", name)
        assert set(schema.get("required", [])) == required
        assert set(schema["properties"]) == required | optional
        assert "runtime" not in schema["properties"]


def test_도구마다_필수와_선택_인자가_계약과_같다() -> None:
    for tool in _tools():
        fields = _fields(tool)
        required = {name for name, field in fields.items() if field.is_required()}

        assert (required, set(fields) - required) == tool_arg_partition("recipe-scan", tool)


def test_도구마다_수치_인자의_기본값과_상하한이_계약과_같다() -> None:
    for tool, contract in _tools().items():
        for field, bound in contract["args"].items():
            if bound["type"] != "integer":
                continue
            assert _fields(tool)[field].default == bound["default"]
            assert _accepts(tool, field, bound["min"])
            assert _accepts(tool, field, bound["max"])
            assert not _accepts(tool, field, bound["min"] - 1)
            assert not _accepts(tool, field, bound["max"] + 1)


def test_도구마다_열거_인자의_값과_기본값이_계약과_같다() -> None:
    for tool, contract in _tools().items():
        for field, enumeration in contract["args"].items():
            if enumeration["type"] != "enum":
                continue
            assert all(_accepts(tool, field, value) for value in enumeration["values"])
            assert not _accepts(tool, field, "drifted.value")
            if "default" in enumeration:
                assert _fields(tool)[field].default == enumeration["default"]


def test_search_events가_거르는_이벤트_종류가_계약과_같다() -> None:
    assert list(get_args(TimelineEventKind)) == tool_arg("recipe-scan", "search_events", "kind")["values"]


def test_search_events_응답의_taskId로_태스크를_가로지른_근거를_기록한다() -> None:
    response = _tools()["search_events"]["responseEvent"]
    catalog = ProvenanceCatalog()
    hit = dict.fromkeys(response["required"], "") | {"id": "event-9", "taskId": "other-task"}
    tool = SearchEventsTool(RecipeSearchReader(FakeTracerApi()), catalog)  # type: ignore[arg-type]

    tool.record(SearchEventsArgs(q="migration"), json.dumps({"events": [hit]}))

    assert "taskId" in response["required"]
    assert catalog.eventIdsByTask == {"other-task": {"event-9"}}


async def test_get_task_events의_응답_본문이_계약과_같다() -> None:
    responses = _contract()["responses"]["get_task_events"]
    reader = RecipeLedgerReader(FakeTracerApi([_row("event-1"), _row("event-2")]))  # type: ignore[arg-type]

    page = await reader.task_events("task-1", 1, None, "asc")

    assert page is not None
    assert set(page) == set(responses["page"])
    assert set(page["events"][0]) == set(responses["item"])


def test_steps의_order가_1부터_연속하지_않으면_거부한다() -> None:
    assert _contract()["steps"]["consecutiveFromOne"] is True
    assert [step.order for step in _candidate([1, 2]).steps] == [1, 2]

    with pytest.raises(ValidationError):
        _candidate([1, 3])


def test_verify_command의_개수_상하한이_계약과_같다() -> None:
    bounds = _contract()["verify"]["command"]

    RecipeVerifyCommand(kind="command", commandMatches=["x"] * bounds["matchesMin"])
    RecipeVerifyCommand(kind="command", commandMatches=["x"] * bounds["matchesMax"])
    with pytest.raises(ValidationError):
        RecipeVerifyCommand(kind="command", commandMatches=["x"] * (bounds["matchesMin"] - 1))
    with pytest.raises(ValidationError):
        RecipeVerifyCommand(kind="command", commandMatches=["x"] * (bounds["matchesMax"] + 1))


def test_verify_pattern의_길이_상한이_계약과_같다() -> None:
    max_length = _contract()["verify"]["pattern"]["maxLength"]

    RecipeVerifyPattern(kind="pattern", pattern="a" * max_length)
    with pytest.raises(ValidationError):
        RecipeVerifyPattern(kind="pattern", pattern="a" * (max_length + 1))


def test_verify_action의_도구_목록이_계약과_같다() -> None:
    for tool in _contract()["verify"]["action"]["tools"]:
        RecipeVerifyAction(kind="action", tool=tool)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        RecipeVerifyAction(kind="action", tool="drifted")  # type: ignore[arg-type]


def test_도구_설명이_계약과_같다() -> None:
    assert {cls.name: cls.description for cls in RECIPE_TOOL_CLASSES} == tool_descriptions("recipe-scan")


def test_전문가가_무너진_판정_문구가_계약과_같다() -> None:
    failures = _contract()["failures"]

    assert failures["workerFailed"] == WORKER_FAILED
    assert WORKER_FAILED.format(reason="ledger down") == "Investigation failed: ledger down"


def test_인자_설명이_계약과_같다() -> None:
    contract = tool_arg_descriptions("recipe-scan")
    shown = {
        cls.name: {arg: field.description for arg, field in cls.args_model.model_fields.items()}
        for cls in RECIPE_TOOL_CLASSES
    }

    assert shown == contract
