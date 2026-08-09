"""세 슬라이스가 같은 이름으로 여는 get_task_events 인자 표면이 서로 어긋나지 않는지 검증한다."""

from __future__ import annotations

import importlib
from copy import deepcopy
from types import ModuleType
from typing import Any

import pytest

from tests.support.contract import agent_tool
from tracer_agent.worker.agents.recipe_scan.tools import get_task_events as recipe_module
from tracer_agent.worker.agents.recipe_scan.tools.get_task_events import (
    GetTaskEventsArgs as RecipeArgs,
)
from tracer_agent.worker.agents.shared.contract_prompt_source import ContractPromptSource
from tracer_agent.worker.agents.task_cleanup.tools import get_events as cleanup_module
from tracer_agent.worker.agents.task_cleanup.tools.get_events import (
    GetTaskEventsArgs as CleanupArgs,
)
from tracer_agent.worker.agents.title_suggestion.tools import get_task_events as title_module
from tracer_agent.worker.agents.title_suggestion.tools.get_task_events import (
    GetTaskEventsArgs as TitleArgs,
)

_SLICES: tuple[tuple[str, type[Any]], ...] = (
    ("recipe-scan", RecipeArgs),
    ("task-cleanup", CleanupArgs),
    ("title-suggestion", TitleArgs),
)

_MODULES: tuple[tuple[str, ModuleType], ...] = (
    ("recipe-scan", recipe_module),
    ("task-cleanup", cleanup_module),
    ("title-suggestion", title_module),
)


def _arg_schemas(model: type[Any]) -> dict[str, Any]:
    """설명을 뺀 인자 하나하나의 타입과 상한이며 슬라이스끼리 대조할 몫이다."""
    schema = model.model_json_schema()
    return {
        name: {key: value for key, value in declared.items() if key != "description"}
        for name, declared in schema["properties"].items()
    }


def test_세_슬라이스의_get_task_events_인자_스키마가_서로_같다() -> None:
    shapes = [_arg_schemas(model) for _, model in _SLICES]

    assert shapes[1] == shapes[0]
    assert shapes[2] == shapes[0]


def test_생략할_수_있는_인자는_계약이_적은_기본값을_갖는다() -> None:
    for agent_id, model in _SLICES:
        declared = agent_tool(agent_id, "get_task_events")["args"]
        validated = model.model_validate({"taskId": "task-1"})

        assert validated.limit == declared["limit"]["default"]
        assert validated.order == declared["order"]["default"]


def test_어느_슬라이스도_생략한_인자를_null로_모델에게_보이지_않는다() -> None:
    for _, model in _SLICES:
        properties = model.model_json_schema()["properties"]

        assert properties["limit"]["type"] == "integer"
        assert "anyOf" not in properties["order"]


@pytest.mark.parametrize(("agent_id", "module"), _MODULES)
def test_계약_도구_선언이_바뀌면_각_슬라이스의_모델_표면도_함께_바뀐다(
    agent_id: str, module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = ContractPromptSource.tool

    def changed(source: ContractPromptSource, requested_agent: str, tool_name: str) -> dict[str, Any]:
        declared = deepcopy(original(source, requested_agent, tool_name))
        if requested_agent == agent_id:
            declared["description"] = "changed tool description"
            declared["args"]["limit"] |= {
                "default": 7,
                "min": 2,
                "max": 9,
                "description": "changed limit description",
            }
        return declared

    monkeypatch.setattr(ContractPromptSource, "tool", changed)
    changed_module = importlib.reload(module)
    try:
        field = changed_module.GetTaskEventsArgs.model_fields["limit"]
        assert changed_module.GET_TASK_EVENTS_DESCRIPTION == "changed tool description"
        assert (field.default, field.metadata[0].ge, field.metadata[1].le) == (7, 2, 9)
        assert field.description == "changed limit description"
    finally:
        monkeypatch.undo()
        importlib.reload(module)
