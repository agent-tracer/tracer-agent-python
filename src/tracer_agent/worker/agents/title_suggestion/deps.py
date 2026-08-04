"""title-suggestion 노드가 함께 받는 실행 의존성과 그 의존성으로 여는 모델 호출을 소유한다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from langchain_core.messages import BaseMessage

from tracer_agent.shared.agents.title_suggestion.models import (
    TitleSuggestionDraft,
    TitleSuggestionRequest,
)
from tracer_agent.shared.workflows.jobs_kinds import AgentJobKind

from ..runtime.execution.trace import ExecutionTrace
from ..runtime.llm.budget import ExecutionBudget
from ..runtime.llm.client import ChatPair
from ..runtime.llm.model_caller import ModelCall, StructuredModelCaller
from .prompts import TitlePrompts
from .reader import TitleLedgerReader
from .tools import TITLE_TOOLS, TitleToolContext

AGENT_NAME = AgentJobKind.TITLE_SUGGESTION


def new_title_caller(chats: ChatPair) -> StructuredModelCaller[TitleToolContext]:
    """이 실행이 쓸 모델 호출자를 세우며 같은 조건의 호출은 컴파일한 agent를 다시 쓴다."""
    return StructuredModelCaller(
        chats,
        TITLE_TOOLS,
        name="title-suggestion-investigator",
        context_schema=TitleToolContext,
    )


@dataclass(frozen=True)
class TitleCall:
    """조사 호출 하나가 낸 후보와 이어갈 메시지와 그 호출이 쓴 턴과 달러다."""

    draft: TitleSuggestionDraft
    messages: list[BaseMessage]
    turns_used: int
    cost_usd: float


@dataclass(frozen=True)
class TitleDeps:
    """제목 후보를 세우는 노드들이 함께 받는 실행 의존성이다."""

    req: TitleSuggestionRequest
    reader: TitleLedgerReader
    usage: ExecutionTrace
    caller: StructuredModelCaller[TitleToolContext]
    budget: ExecutionBudget
    prompts: TitlePrompts
    language_directives: Mapping[str, str]

    async def investigate(self, messages: list[BaseMessage]) -> TitleCall:
        """조사 도구를 연 채 모델을 호출해 제목 후보와 그 호출이 쓴 턴과 달러를 낸다."""
        max_turns = self.req.limits.maxTurns
        budget = self.budget.new_loop(AGENT_NAME, self.req.model)
        result = await self.caller.invoke(
            ModelCall(
                system_prompt=self.prompts.investigator_system,
                output=TitleSuggestionDraft,
                messages=messages,
                missing_response=f"{AGENT_NAME} produced no structured output",
                max_turns=max_turns,
            ),
            TitleToolContext(
                agent_name=AGENT_NAME,
                trace=self.usage,
                budget=budget,
                max_model_turns=max_turns,
                tool_owner=AGENT_NAME,
                reader=self.reader,
            ),
        )
        return TitleCall(result.response, result.messages, result.num_turns, budget.delta)
