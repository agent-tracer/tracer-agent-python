"""계약의 language 절이 정한 변형을 네 에이전트의 프롬프트가 그대로 싣는지 검증한다."""

from __future__ import annotations

from typing import Any, get_args

import pytest

from tests.support.contract import agent_cases, agent_prompt, shared_contract
from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES
from tests.support.prompts import (
    CHAT_PROMPT,
    RECIPE_SCAN_PROMPT,
    TASK_CLEANUP_PROMPT,
    TITLE_SUGGESTION_PROMPT,
)
from tracer_agent.shared.agents.chat.models import ChatTurnFields
from tracer_agent.shared.agents.recipe_scan.models import RecipeScanRequest
from tracer_agent.shared.agents.settings.execution import fallback_language
from tracer_agent.shared.agents.shared.models import Language
from tracer_agent.shared.agents.task_cleanup.models import TaskCleanupRequest
from tracer_agent.shared.agents.title_suggestion.models import (
    TitleSuggestionContext,
    TitleSuggestionRequest,
)
from tracer_agent.worker.agents.chat.prompts import build_context_prompt
from tracer_agent.worker.agents.recipe_scan.prompts import build_user_prompt as build_recipe_user_prompt
from tracer_agent.worker.agents.shared.prompt_source_port import AgentPrompt
from tracer_agent.worker.agents.task_cleanup.prompts import build_user_prompt as build_cleanup_user_prompt
from tracer_agent.worker.agents.title_suggestion.prompts import (
    build_user_prompt as build_title_user_prompt,
)

AGENT_IDS = ("chat", "recipe-scan", "task-cleanup", "title-suggestion")

_ENVELOPE: dict[str, Any] = {
    "model": "claude-sonnet-4-6",
    "apiKey": "sk-test",
    "modelRates": WIRE_MODEL_RATES,
    "limits": WIRE_LIMITS,
}

_TITLE_CONTEXT = TitleSuggestionContext.model_validate(agent_cases("title-suggestion")["contextExample"])


def _requested_language(agent_id: str, given: dict[str, Any]) -> str:
    """접수가 받은 입력이 이 실행의 출력 언어로 서는 값이며 빠지면 봉투의 기본값이 선다."""
    if agent_id == "chat":
        return ChatTurnFields(threadId="thread-1", executionId="exec-1", userId="user-1", **given).language
    if agent_id == "recipe-scan":
        return RecipeScanRequest(taskId="task-1", userId="user-1", **_ENVELOPE, **given).language
    if agent_id == "task-cleanup":
        return TaskCleanupRequest(
            scannedAt="2026-07-14T00:00:00Z",
            userId="user-1",
            maxSuggestions=5,
            batch={"candidates": [], "batchTruncated": False},
            **_ENVELOPE,
            **given,
        ).language
    return TitleSuggestionRequest(
        taskId="task-1", userId="user-1", context=_TITLE_CONTEXT, **_ENVELOPE, **given
    ).language


def _carrier(agent_id: str, prompt: AgentPrompt, language: str) -> str:
    """이 에이전트가 출력 언어 문장을 싣는 메시지를 실행과 같은 경로로 조립한다."""
    directive = prompt.language_directives[language]
    if agent_id == "chat":
        return build_context_prompt(directive, None, [])
    if agent_id == "recipe-scan":
        return build_recipe_user_prompt(prompt, "task-1", None, directive)
    if agent_id == "task-cleanup":
        return build_cleanup_user_prompt("2026-07-14T00:00:00Z", 5, directive)
    return build_title_user_prompt("task-1", _TITLE_CONTEXT, directive)


PROMPTS: dict[str, AgentPrompt] = {
    "chat": CHAT_PROMPT,
    "recipe-scan": RECIPE_SCAN_PROMPT,
    "task-cleanup": TASK_CLEANUP_PROMPT,
    "title-suggestion": TITLE_SUGGESTION_PROMPT,
}

LANGUAGE_CASES = [
    pytest.param(agent_id, case, id=f"{agent_id}-{case['expect']['variant']}")
    for agent_id in AGENT_IDS
    for case in agent_cases(agent_id)["language"]["cases"]
]


@pytest.mark.parametrize(("agent_id", "case"), LANGUAGE_CASES)
def test_접수가_받은_언어의_조각_본문을_프롬프트가_그대로_싣는다(agent_id: str, case: dict[str, Any]) -> None:
    variant = case["expect"]["variant"]
    fragment = agent_cases(agent_id)["language"]["fragment"]
    declared = agent_prompt(agent_id)["fragments"][fragment]["byLanguage"][variant]

    language = _requested_language(agent_id, case["input"])
    carried = _carrier(agent_id, PROMPTS[agent_id], language)

    assert language == variant
    assert "\n".join(declared) in carried


@pytest.mark.parametrize("agent_id", AGENT_IDS)
def test_계약이_선언한_변형과_접수가_받는_언어의_집합이_같다(agent_id: str) -> None:
    languages = shared_contract("languages.json")["languages"]
    declared = agent_prompt(agent_id)["fragments"]["languageDirective"]["byLanguage"]

    assert list(get_args(Language)) == languages
    assert set(declared) == set(languages)


@pytest.mark.parametrize("agent_id", AGENT_IDS)
def test_언어를_주지_않은_접수는_계약이_정한_기본값으로_선다(agent_id: str) -> None:
    assert _requested_language(agent_id, {}) == shared_contract("languages.json")["default"]


@pytest.mark.parametrize("agent_id", AGENT_IDS)
def test_출력_언어가_미치는_범위를_계약이_한_값으로_말한다(agent_id: str) -> None:
    scope = shared_contract("languages.json")["scope"]

    assert agent_cases(agent_id)["language"]["scope"] == scope[agent_id]


@pytest.mark.parametrize("agent_id", ("recipe-scan", "task-cleanup", "title-suggestion"))
def test_잡의_출력_언어는_사용자_프롬프트의_한_줄이다(agent_id: str) -> None:
    prompt = PROMPTS[agent_id]

    carried = _carrier(agent_id, prompt, "ko")

    assert f"Output language: {prompt.directive('ko')}" in carried.splitlines()


def test_대화의_출력_언어는_선행_컨텍스트_메시지의_첫_줄이다() -> None:
    carried = _carrier("chat", CHAT_PROMPT, "ko")

    assert carried.splitlines()[0] == CHAT_PROMPT.directive("ko")


class Test목록_밖의_언어:
    """계약의 onUnknown 은 목록 밖의 값에 실행을 세우지 않고 기본 언어로 낮추게 한다."""

    @pytest.mark.parametrize("agent_id", AGENT_IDS)
    def test_계약이_적은_기본_언어의_지시문으로_낮춘다(self, agent_id: str) -> None:
        prompt = PROMPTS[agent_id]

        assert prompt.directive("kl") == prompt.directive(fallback_language())

    @pytest.mark.parametrize("agent_id", AGENT_IDS)
    def test_목록_밖의_값에도_지시문을_낸다(self, agent_id: str) -> None:
        assert PROMPTS[agent_id].directive("kl")
