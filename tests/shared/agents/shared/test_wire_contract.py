"""워커가 보내는 요청·결과 계약을 계약으로 검증한다."""

from __future__ import annotations

from typing import get_args

from tests.support.contract import agent_spec, shared_contract
from tracer_agent.shared.agents.recipe_scan.models import RecipeCandidate
from tracer_agent.shared.agents.shared.models import AgentStepRole, OrchestrationEventKind
from tracer_agent.shared.agents.shared.prompt_integrity import (
    PROMPT_INTEGRITY_MODES,
    FullPromptIntegrityDTO,
)
from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionContext


def test_워커가_보내는_title_컨텍스트를_그대로_받는다() -> None:
    payload = agent_spec("title-suggestion")["contextExample"]

    context = TitleSuggestionContext.model_validate(payload)

    assert context.model_dump(mode="json", exclude_none=False) == payload


def test_공유_최종_DTO_fixture를_그대로_직렬화한다() -> None:
    payload = agent_spec("recipe-scan")["resultExample"]

    candidate = RecipeCandidate.model_validate(payload["recipes"][0])
    serialized = {"recipes": [candidate.model_dump(mode="json", exclude_none=True)]}

    assert serialized == payload


def test_언어_중립_fixture와_실행_어휘가_같다() -> None:
    vocabulary = shared_contract("execution.vocabulary.json")

    assert list(get_args(AgentStepRole)) == vocabulary["stepRoles"]
    assert list(get_args(OrchestrationEventKind)) == vocabulary["orchestrationEventKinds"]


def test_조각을_싣지_않는_봉투가_full_prompt_모드를_쓴다() -> None:
    integrity = FullPromptIntegrityDTO.model_validate({"mode": "full-prompt"})

    assert integrity.mode in PROMPT_INTEGRITY_MODES
