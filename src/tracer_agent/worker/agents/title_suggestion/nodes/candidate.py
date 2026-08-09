"""title-suggestion의 조사와 검증과 복구와 결과 노드를 제공한다."""

from __future__ import annotations

from abc import ABC
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import HumanMessage

from tracer_agent.shared.agents.shared.graph_state import remaining_cost_usd, remaining_turns
from tracer_agent.shared.agents.title_suggestion.models import (
    InvestigateUpdate,
    RepairUpdate,
    ResultUpdate,
    TitleSuggestionDraft,
    TitleSuggestionState,
    ValidateCandidateUpdate,
)

from ...runtime.llm.budget import AgentBudgetLease, pool_spend, reserved_spend
from ...runtime.node import GraphNode
from ...runtime.validation_nodes import ValidationNode
from ...shared.prompt_source_port import directive_for
from ..deps import TitleDeps
from ..policy import normalize_title_candidate
from ..prompts import build_user_prompt


class _CandidateAgent[UpdateT: Mapping[str, Any]](GraphNode[TitleSuggestionState, UpdateT], ABC):
    def __init__(self, deps: TitleDeps, lease: AgentBudgetLease) -> None:
        self._deps = deps
        self._lease = lease


class InvestigateNode(GraphNode[TitleSuggestionState, InvestigateUpdate]):
    """대화 발췌와 필요한 이벤트로 제목 후보를 조사한다."""

    name = "investigate"

    def __init__(self, deps: TitleDeps) -> None:
        self._deps = deps

    async def run(self, state: TitleSuggestionState) -> InvestigateUpdate:
        # 리페어 예약을 뗀 나머지가 조사의 몫이며 조사가 총량을 다 쓰면 고칠 턴이 남지 않는다.
        lease = AgentBudgetLease(max_turns=remaining_turns(state), max_cost_usd=remaining_cost_usd(state))
        if lease.max_turns <= 0:
            return {
                "candidate": TitleSuggestionDraft(),
                "messages": [],
                **pool_spend(cost_usd=0.0, turns_used=0),
            }
        call = await self._deps.investigate(
            [
                HumanMessage(
                    content=build_user_prompt(
                        state["task_id"],
                        state["context"],
                        directive_for(self._deps.language_directives, state["language"]),
                    )
                )
            ],
            lease,
        )
        return {
            "candidate": call.draft,
            "messages": call.messages,
            **pool_spend(cost_usd=call.cost_usd, turns_used=call.turns_used),
        }


class RepairNode(_CandidateAgent[RepairUpdate]):
    """검증에서 걸린 후보를 한 번 더 고쳐 쓴다."""

    name = "repair"

    async def run(self, state: TitleSuggestionState) -> RepairUpdate:
        if self._lease.max_turns <= 0:
            return {
                "candidate": state["candidate"] or TitleSuggestionDraft(),
                "messages": state["messages"],
                "repair_attempted": True,
                **reserved_spend(cost_usd=0.0, turns_used=0),
            }
        repair_prompt = [
            *state["messages"],
            HumanMessage(
                content=self._deps.prompts.repair_directive.format(
                    errors="\n".join(state["validation_errors"])
                )
            ),
        ]
        call = await self._deps.investigate(repair_prompt, self._lease)
        return {
            "candidate": call.draft,
            "messages": call.messages,
            "repair_attempted": True,
            **reserved_spend(cost_usd=call.cost_usd, turns_used=call.turns_used),
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
