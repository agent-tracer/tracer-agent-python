"""세 슬라이스가 같은 이름으로 여는 get_task_events 인자 표면이 서로 어긋나지 않는지 검증한다."""

from __future__ import annotations

from typing import Any

from tests.support.contract import agent_tool
from tracer_agent.worker.agents.recipe_scan.tools.get_task_events import (
    GetTaskEventsArgs as RecipeArgs,
)
from tracer_agent.worker.agents.task_cleanup.tools.get_events import (
    GetTaskEventsArgs as CleanupArgs,
)
from tracer_agent.worker.agents.title_suggestion.tools.get_task_events import (
    GetTaskEventsArgs as TitleArgs,
)

_SLICES: tuple[tuple[str, type[Any]], ...] = (
    ("recipe-scan", RecipeArgs),
    ("task-cleanup", CleanupArgs),
    ("title-suggestion", TitleArgs),
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
