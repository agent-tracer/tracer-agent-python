"""title-suggestion 노드가 함께 받는 실행 의존성과 그 의존성으로 여는 모델 호출을 소유한다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from langchain_core.messages import BaseMessage

from tracer_agent.shared.agents.title_suggestion.models import (
    TitleSuggestionDraft,
    TitleSuggestionRequest,
)
from tracer_agent.shared.workflows.jobs_kinds import AgentJobKind

from ..runtime.execution.trace import ExecutionTrace
from ..runtime.llm.agent_cache import CompiledAgentCache
from ..runtime.llm.budget import ExecutionBudget, SharedToolLoopBudget
from ..runtime.llm.client import ChatPair
from ..runtime.llm.structured_agent import (
    invoke_structured_agent,
    recursion_limit_for,
)
from .langchain_agent import build_title_agent
from .prompts import TitlePrompts
from .reader import TitleLedgerReader
from .tools import TITLE_TOOLS, TitleToolContext

AGENT_NAME = AgentJobKind.TITLE_SUGGESTION


@dataclass(frozen=True)
class TitleDeps:
    """제목 후보를 세우는 노드들이 함께 받는 실행 의존성이다."""

    req: TitleSuggestionRequest
    reader: TitleLedgerReader
    usage: ExecutionTrace
    chats: ChatPair
    budget: ExecutionBudget
    prompts: TitlePrompts
    language_directives: Mapping[str, str]
    agents: CompiledAgentCache = field(default_factory=CompiledAgentCache)

    async def investigate(
        self, messages: list[BaseMessage]
    ) -> tuple[TitleSuggestionDraft, list[BaseMessage], SharedToolLoopBudget]:
        """조사 도구를 연 채 모델을 돌려 제목 후보와 그 호출이 쓴 예산을 낸다."""
        max_turns = self.req.limits.maxTurns
        budget = self.budget.new_loop(AGENT_NAME, self.req.model)
        agent = self.agents.compiled(
            (self.prompts.investigator_system, max_turns),
            lambda: build_title_agent(
                self.chats.primary,
                self.prompts.investigator_system,
                TITLE_TOOLS.langchain_tools(),
                fallback_chat=self.chats.fallback,
                max_turns=max_turns,
            ),
        )
        result = await invoke_structured_agent(
            agent,
            messages=messages,
            context=TitleToolContext(
                agent_name=AGENT_NAME,
                trace=self.usage,
                budget=budget,
                max_model_turns=max_turns,
                tool_owner=AGENT_NAME,
                reader=self.reader,
            ),
            response_type=TitleSuggestionDraft,
            recursion_limit=recursion_limit_for(max_turns),
            missing_response=f"{AGENT_NAME} produced no structured output",
        )
        return result.response, result.messages, budget
