"""시스템 프롬프트가 실행마다 같아야 캐시 경계가 실행을 건너뛰고도 맞는지 검증한다."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import pytest

from tracer_agent.worker.agents.chat.prompts import build_system_prompt
from tracer_agent.worker.agents.recipe_scan.prompts import (
    build_prompt_bundle as build_recipe_prompts,
)
from tracer_agent.worker.agents.shared.prompt_source_port import AgentPrompt
from tracer_agent.worker.agents.task_cleanup.prompts import (
    build_prompt_bundle as build_cleanup_prompts,
)
from tracer_agent.worker.agents.title_suggestion.prompts import (
    build_prompt_bundle as build_title_prompts,
)

BUILDERS: list[Callable[[AgentPrompt], Any]] = [
    build_system_prompt,
    build_title_prompts,
    build_recipe_prompts,
    build_cleanup_prompts,
]


@pytest.mark.parametrize("builder", BUILDERS, ids=lambda one: one.__module__)
def test_시스템_프롬프트는_계약_조각만_받는다(builder: Callable[[AgentPrompt], Any]) -> None:
    parameters = list(inspect.signature(builder, eval_str=True).parameters.values())

    assert [one.name for one in parameters] == ["prompt"]
    assert parameters[0].annotation is AgentPrompt
