"""세 에이전트의 코드 pin 계산이 resolved hash 계약을 그대로 쓰는지 검증한다."""

from __future__ import annotations

from tracer_agent.worker.agents.recipe_scan.prompts import PROMPT_BUNDLE as RECIPE_SCAN_BUNDLE
from tracer_agent.worker.agents.recipe_scan.prompts import PROMPT_VERSION as RECIPE_SCAN_PROMPT_VERSION
from tracer_agent.worker.agents.shared.resolved_prompt_hash import resolved_prompt_bundle_hash
from tracer_agent.worker.agents.task_cleanup.prompts import PROMPT_BUNDLE as TASK_CLEANUP_BUNDLE
from tracer_agent.worker.agents.task_cleanup.prompts import PROMPT_VERSION as TASK_CLEANUP_PROMPT_VERSION
from tracer_agent.worker.agents.title_suggestion.prompts import PROMPT_BUNDLE as TITLE_SUGGESTION_BUNDLE
from tracer_agent.worker.agents.title_suggestion.prompts import (
    PROMPT_VERSION as TITLE_SUGGESTION_PROMPT_VERSION,
)
from tracer_agent.worker.prompt_registry.pin import resolve_pinned_prompt_registrations

_EXPECTED_VERSIONS = {
    "title-suggestion": TITLE_SUGGESTION_PROMPT_VERSION,
    "recipe-scan": RECIPE_SCAN_PROMPT_VERSION,
    "task-cleanup": TASK_CLEANUP_PROMPT_VERSION,
}
_EXPECTED_BUNDLES = {
    "title-suggestion": TITLE_SUGGESTION_BUNDLE,
    "recipe-scan": RECIPE_SCAN_BUNDLE,
    "task-cleanup": TASK_CLEANUP_BUNDLE,
}


def test_세_에이전트의_pin을_모두_계산한다() -> None:
    pins = resolve_pinned_prompt_registrations()

    assert {pin.agent_name for pin in pins} == {"title-suggestion", "recipe-scan", "task-cleanup"}


def test_각_pin의_version과_해시가_프롬프트_옆_상수와_같다() -> None:
    pins = {pin.agent_name: pin for pin in resolve_pinned_prompt_registrations()}

    for agent_name, expected_version in _EXPECTED_VERSIONS.items():
        pin = pins[agent_name]
        expected_hash = resolved_prompt_bundle_hash(_EXPECTED_BUNDLES[agent_name]).resolved_prompt_hash
        assert pin.semantic_version == expected_version
        assert pin.resolved_prompt_hash == expected_hash
