"""프롬프트 언어 지시문과 조각을 계약으로 검증한다."""

from __future__ import annotations

from typing import get_args

import pytest

from tests.support.contract import agent_prompt, agent_tools, shared_contract
from tracer_agent.shared.agents.recipe_scan.models import (
    MAX_PROBE_WEIGHT,
    MAX_RECIPE_CANDIDATES,
    MAX_REDISPATCH_PROBES,
    MAX_REDISPATCH_ROUNDS,
)
from tracer_agent.shared.agents.shared.models import Language
from tracer_agent.shared.agents.task_cleanup.models import MAX_EVIDENCE_EVENT_IDS, MAX_INSPECT_WEIGHT
from tracer_agent.worker.agents.chat import prompts as chat_prompts
from tracer_agent.worker.agents.chat.prompt_fragments import FRAGMENTS as CHAT_FRAGMENTS
from tracer_agent.worker.agents.recipe_scan import prompts as recipe_prompts
from tracer_agent.worker.agents.recipe_scan.prompt_fragments import FRAGMENTS as RECIPE_FRAGMENTS
from tracer_agent.worker.agents.recipe_scan.tools import COORDINATOR_TOOLS
from tracer_agent.worker.agents.shared.prompt_fragments import PLACEHOLDERS, render
from tracer_agent.worker.agents.task_cleanup import prompts as cleanup_prompts
from tracer_agent.worker.agents.task_cleanup.prompt_fragments import FRAGMENTS as CLEANUP_FRAGMENTS
from tracer_agent.worker.agents.title_suggestion import prompts as title_prompts
from tracer_agent.worker.agents.title_suggestion.prompt_fragments import FRAGMENTS as TITLE_FRAGMENTS

_LANGUAGE_DIRECTIVE = "languageDirective"

_AGENTS: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "recipe-scan": (recipe_prompts.LANGUAGE_DIRECTIVES, RECIPE_FRAGMENTS),
    "task-cleanup": (cleanup_prompts.LANGUAGE_DIRECTIVES, CLEANUP_FRAGMENTS),
    "title-suggestion": (title_prompts.LANGUAGE_DIRECTIVES, TITLE_FRAGMENTS),
    "chat": (chat_prompts.LANGUAGE_DIRECTIVES, CHAT_FRAGMENTS),
}


def _joined(lines: list[str]) -> str:
    return "\n".join(lines)


@pytest.mark.parametrize("agent", sorted(_AGENTS))
def test_언어_지시문이_계약과_한_글자도_다르지_않다(agent: str) -> None:
    by_language = agent_prompt(agent)["fragments"][_LANGUAGE_DIRECTIVE]["byLanguage"]

    assert _AGENTS[agent][0] == {key: _joined(lines) for key, lines in by_language.items()}


def test_지원_언어_목록이_계약과_같다() -> None:
    languages = shared_contract("languages.json")

    assert sorted(get_args(Language)) == sorted(languages["languages"])
    assert sorted(languages["scope"]) == sorted(_AGENTS)


@pytest.mark.parametrize("agent", sorted(_AGENTS))
def test_프롬프트_조각이_계약과_한_글자도_다르지_않다(agent: str) -> None:
    fragments = agent_prompt(agent)["fragments"]
    expected = {
        key: _joined(value["content"]) for key, value in fragments.items() if key != _LANGUAGE_DIRECTIVE
    }

    assert _AGENTS[agent][1] == expected


def test_자리표시자_값이_계약의_상한과_도구_이름과_같다() -> None:
    limits = {
        key: str(value)
        for agent in ("recipe-scan", "task-cleanup")
        for key, value in agent_tools(agent)["limits"].items()
    }
    coordinator = agent_tools("recipe-scan")["orchestration"]["coordinatorTools"][0]

    assert {key: value for key, value in PLACEHOLDERS.items() if key in limits} == {
        key: value for key, value in limits.items() if key in PLACEHOLDERS
    }
    assert PLACEHOLDERS["recipeCandidateLimit"] == str(MAX_RECIPE_CANDIDATES)
    assert PLACEHOLDERS["maxRedispatchProbes"] == str(MAX_REDISPATCH_PROBES)
    assert PLACEHOLDERS["maxRedispatchRounds"] == str(MAX_REDISPATCH_ROUNDS)
    assert PLACEHOLDERS["maxProbeWeight"] == str(MAX_PROBE_WEIGHT)
    assert PLACEHOLDERS["maxInspectWeight"] == str(MAX_INSPECT_WEIGHT)
    assert PLACEHOLDERS["maxEvidenceEventIds"] == str(MAX_EVIDENCE_EVENT_IDS)
    assert PLACEHOLDERS["checkCitationsTool"] == coordinator
    assert (PLACEHOLDERS["checkCitationsTool"],) == COORDINATOR_TOOLS


@pytest.mark.parametrize("agent", sorted(_AGENTS))
def test_조각의_자리표시자는_하나도_남지_않는다(agent: str) -> None:
    for fragment in _AGENTS[agent][1].values():
        assert "${" not in render(fragment)
