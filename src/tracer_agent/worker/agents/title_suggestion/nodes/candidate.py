"""title-suggestion의 조사와 검증과 복구와 결과 노드를 제공한다."""

from __future__ import annotations

from abc import ABC
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import HumanMessage

from tracer_agent.shared.agents.title_suggestion.models import (
    InvestigateUpdate,
    RepairUpdate,
    ResultUpdate,
    TitleSuggestionDraft,
    TitleSuggestionState,
    ValidateCandidateUpdate,
)

from ...runtime.node import GraphNode
from ...runtime.validation_nodes import ValidationNode
from ..deps import TitleDeps
from ..policy import normalize_title_candidate
from ..prompts import build_user_prompt


class _CandidateAgent[UpdateT: Mapping[str, Any]](GraphNode[TitleSuggestionState, UpdateT], ABC):
    def __init__(self, deps: TitleDeps) -> None:
        self._deps = deps


class InvestigateNode(_CandidateAgent[InvestigateUpdate]):
    """대화 발췌와 필요한 이벤트로 제목 후보를 조사한다."""

    name = "investigate"

    async def run(self, state: TitleSuggestionState) -> InvestigateUpdate:
        call = await self._deps.investigate(
            [
                HumanMessage(
                    content=build_user_prompt(
                        state["task_id"],
                        state["context"],
                        self._deps.language_directives[state["language"]],
                    )
                )
            ],
        )
        return {
            "candidate": call.draft,
            "messages": call.messages,
            "model_cost_usd": call.cost_usd,
            "model_turns_used": call.turns_used,
        }


class RepairNode(_CandidateAgent[RepairUpdate]):
    """검증에서 걸린 후보를 한 번 더 고쳐 쓴다."""

    name = "repair"

    async def run(self, state: TitleSuggestionState) -> RepairUpdate:
        repair_prompt = [
            *state["messages"],
            HumanMessage(
                content=self._deps.prompts.repair_directive.format(
                    errors="\n".join(state["validation_errors"])
                )
            ),
        ]
        call = await self._deps.investigate(repair_prompt)
        return {
            "candidate": call.draft,
            "messages": call.messages,
            "repair_attempted": True,
            "model_cost_usd": call.cost_usd,
            "model_turns_used": call.turns_used,
        }


class ValidateCandidateNode(ValidationNode[TitleSuggestionState, ValidateCandidateUpdate]):
    """쓸모없는 후보를 떨어뜨리고 모델이 고쳐야 하는 부족만 사유로 남긴다."""

    name = "validate_candidate"

    def validate(self, state: TitleSuggestionState) -> tuple[ValidateCandidateUpdate, list[str]]:
        candidate, errors = normalize_title_candidate(state["candidate"], state["context"].title)
        return {"candidate": candidate, "validation_errors": errors}, errors


def finalize_result(state: TitleSuggestionState) -> ResultUpdate:
    """검증된 제목 후보를 외부 결과로 직렬화한다."""
    return {"result": state["candidate"] or TitleSuggestionDraft()}


def empty_result(_state: TitleSuggestionState) -> ResultUpdate:
    """후보가 없는 제목 제안 결과를 만든다."""
    return {"result": TitleSuggestionDraft()}
