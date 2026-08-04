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

from ...runtime.execution.trace import ExecutionTrace
from ...runtime.node import GraphNode
from ...runtime.routes import EMPTY, FINALIZE
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


class ValidateCandidateNode(GraphNode[TitleSuggestionState, ValidateCandidateUpdate]):
    """쓸모없는 후보를 떨어뜨리고 모델이 고쳐야 하는 부족만 사유로 남긴다."""

    name = "validate_candidate"

    def __init__(self, usage: ExecutionTrace) -> None:
        self._usage = usage

    async def run(self, state: TitleSuggestionState) -> ValidateCandidateUpdate:
        candidate, errors = normalize_title_candidate(state["candidate"], state["context"].title)
        if errors:
            self._usage.record_orchestration_event(
                "validation.failed",
                "; ".join(errors),
                node_name=self.name,
            )
        return {"candidate": candidate, "validation_errors": errors}


class FinalizeNode(GraphNode[TitleSuggestionState, ResultUpdate]):
    """검증된 제목 후보를 외부 결과로 직렬화한다."""

    name = FINALIZE

    async def run(self, state: TitleSuggestionState) -> ResultUpdate:
        return {"result": state["candidate"] or TitleSuggestionDraft()}


class EmptyNode(GraphNode[TitleSuggestionState, ResultUpdate]):
    """후보가 없는 제목 제안 결과를 반환한다."""

    name = EMPTY

    async def run(self, _state: TitleSuggestionState) -> ResultUpdate:
        return {"result": TitleSuggestionDraft()}
