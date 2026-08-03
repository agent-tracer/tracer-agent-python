"""title-suggestion 도구 표면을 계약으로 검증한다."""

from __future__ import annotations

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
from tracer_agent.shared.agents.title_suggestion.models import (
    MAX_CONTEXT_TURNS,
    RECENT_TURN_LIMIT,
    TitleSuggestionContext,
)
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.title_suggestion.reader import TitleLedgerReader
from tracer_agent.worker.agents.title_suggestion.tools import (
    DEFAULT_EVENT_LIMIT,
    DEFAULT_EVENT_ORDER,
    GET_TASK_EVENTS,
    GET_TASK_EVENTS_DESCRIPTION,
    MAX_EVENT_LIMIT,
    MIN_EVENT_LIMIT,
    TITLE_TOOLS,
    GetTaskEventsArgs,
)


def _contract() -> Any:
    return agent_tools("title-suggestion")


def _langchain_tool() -> Any:
    return TITLE_TOOLS.langchain_tools()[0]


# 계약이 선언한 선택 필드까지 채운 행이라야 렌더링이 필드를 빠뜨렸는지 드러난다.
def _row(event_id: str) -> dict[str, Any]:
    return {
        "id": event_id,
        "seq": 1,
        "kind": "agent_tracer.user.message",
        "title": "마이그레이션을 추가해줘",
        "body": "본문",
        "toolName": "Bash",
        "filePaths": ["src/app.ts"],
        "occurredAt": datetime(2026, 7, 14, tzinfo=UTC).isoformat(),
    }


def _turns(count: int) -> list[dict[str, Any]]:
    return [{"turnIndex": index, "askedText": f"ask {index}"} for index in range(count)]


def _context(turn_count: int) -> dict[str, Any]:
    return {
        "title": "Untitled",
        "status": "completed",
        "totalEventCount": 3 * turn_count,
        "totalTurnCount": turn_count,
        "truncated": True,
        "turns": _turns(turn_count),
    }


def test_최근_턴_창과_컨텍스트가_싣는_턴_수의_상한이_계약과_같다() -> None:
    limits = _contract()["limits"]

    assert limits["recentTurnLimit"] == RECENT_TURN_LIMIT
    assert limits["maxContextTurns"] == MAX_CONTEXT_TURNS
    accepted = TitleSuggestionContext.model_validate(_context(limits["maxContextTurns"]))
    assert len(accepted.turns) == limits["maxContextTurns"]
    with pytest.raises(ValidationError):
        TitleSuggestionContext.model_validate(_context(limits["maxContextTurns"] + 1))


def test_get_task_events의_필수와_선택_인자가_계약과_같다() -> None:
    declared_required, declared_optional = tool_arg_partition("title-suggestion", GET_TASK_EVENTS)

    required = {name for name, field in GetTaskEventsArgs.model_fields.items() if field.is_required()}
    optional = set(GetTaskEventsArgs.model_fields) - required

    assert required == declared_required
    assert optional == declared_optional


def test_표준_tool이_runtime을_숨기고_계약이_적은_인자만_노출한다() -> None:
    required, optional = tool_arg_partition("title-suggestion", GET_TASK_EVENTS)
    tool = _langchain_tool()
    schema = tool.tool_call_schema.model_json_schema()

    assert tool.name == "get_task_events"
    assert set(schema["required"]) == required
    assert set(schema["properties"]) == required | optional
    assert "runtime" not in schema["properties"]


def test_limit의_기본값과_최소와_최대가_계약과_같다() -> None:
    limit = tool_arg("title-suggestion", GET_TASK_EVENTS, "limit")

    assert limit["default"] == DEFAULT_EVENT_LIMIT
    assert limit["min"] == MIN_EVENT_LIMIT
    assert limit["max"] == MAX_EVENT_LIMIT
    assert GetTaskEventsArgs.model_validate({"taskId": "task-1"}).limit == limit["default"]
    assert GetTaskEventsArgs.model_validate({"taskId": "task-1", "limit": limit["max"]}).limit == limit["max"]
    with pytest.raises(ValidationError):
        GetTaskEventsArgs.model_validate({"taskId": "task-1", "limit": limit["max"] + 1})


def test_읽기_방향의_기본값과_허용_값이_계약과_같다() -> None:
    order = tool_arg("title-suggestion", GET_TASK_EVENTS, "order")
    field = GetTaskEventsArgs.model_fields["order"]

    assert order["default"] == DEFAULT_EVENT_ORDER
    assert GetTaskEventsArgs.model_validate({"taskId": "task-1"}).order == order["default"]
    assert list(get_args(field.annotation)) == order["values"]


async def test_get_task_events의_응답_본문이_계약과_같다() -> None:
    responses = _contract()["responses"][GET_TASK_EVENTS]
    reader = TitleLedgerReader(FakeTracerApi([_row("event-1"), _row("event-2")]))

    page = await reader.task_events("task-1", 1, None, "asc")

    assert page is not None
    assert set(page) == set(responses["page"])
    assert set(page["events"][0]) == set(responses["item"])


def test_도구_설명이_계약과_같다() -> None:
    assert tool_descriptions("title-suggestion") == {GET_TASK_EVENTS: GET_TASK_EVENTS_DESCRIPTION}


def test_인자_설명이_계약과_같다() -> None:
    contract = tool_arg_descriptions("title-suggestion")[GET_TASK_EVENTS]
    fields = GetTaskEventsArgs.model_fields

    assert {arg: field.description for arg, field in fields.items()} == contract
