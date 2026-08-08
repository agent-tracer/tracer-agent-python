"""도구 스팬이 싣는 파라미터가 모델이 실제로 고른 인자인지 검증한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from tracer_agent.worker.agents.runtime import tooling
from tracer_agent.worker.agents.runtime.tooling import AgentTool, ToolRegistry

_DEFAULT_LIMIT = 100


class _Args(BaseModel):
    taskId: str
    limit: int = Field(default=_DEFAULT_LIMIT)
    cursor: str | None = None


@dataclass
class _Context:
    tool_owner: str = "task-cleanup"


class _Events(AgentTool[_Args, _Context]):
    name = "get_task_events"
    description = "reads events"
    args_model = _Args

    async def execute(self, args: _Args, _context: _Context) -> str:
        return f"{args.taskId}:{args.limit}"


def _registry() -> ToolRegistry[_Context]:
    return ToolRegistry([_Events()])


async def _span_parameters(raw: dict[str, Any], monkeypatch: Any) -> dict[str, Any]:
    seen: list[dict[str, Any]] = []
    original = tooling.tool_span

    def capture(name: str, *, agent_name: str, parameters: Any = None) -> Any:
        seen.append(dict(parameters))
        return original(name, agent_name=agent_name, parameters=parameters)

    monkeypatch.setattr(tooling, "tool_span", capture)
    await _registry().invoke("get_task_events", raw, _Context())
    return seen[0]


async def test_모델이_넘긴_인자만_스팬에_실린다(monkeypatch: Any) -> None:
    assert await _span_parameters({"taskId": "t1"}, monkeypatch) == {"taskId": "t1"}


async def test_모델이_고른_기본값과_같은_값도_고른_것으로_실린다(monkeypatch: Any) -> None:
    parameters = await _span_parameters({"taskId": "t1", "limit": _DEFAULT_LIMIT}, monkeypatch)

    assert parameters == {"taskId": "t1", "limit": _DEFAULT_LIMIT}


async def test_실행은_모델이_생략한_인자를_기본값으로_채운다() -> None:
    answered = await _registry().invoke("get_task_events", {"taskId": "t1"}, _Context())

    assert answered == f"t1:{_DEFAULT_LIMIT}"
