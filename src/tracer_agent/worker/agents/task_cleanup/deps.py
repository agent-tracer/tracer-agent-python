"""task-cleanup 노드가 함께 받는 실행 의존성과 그 의존성으로 여는 모델 호출을 소유한다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from tracer_agent.shared.agents.task_cleanup.models import CleanupCandidate, TaskCleanupRequest
from tracer_agent.shared.workflows.jobs_kinds import AgentJobKind

from ..runtime.execution.trace import ExecutionTrace
from ..runtime.llm.agent_cache import CompiledAgentCache
from ..runtime.llm.budget import ExecutionBudget, SharedToolLoopBudget
from ..runtime.llm.client import ChatPair
from ..runtime.llm.structured_agent import (
    StructuredAgentResult,
    invoke_structured_agent,
    recursion_limit_for,
)
from .langchain_agent import build_cleanup_agent
from .prompts import CleanupPrompts
from .reader import CleanupLedgerReader
from .tools import CLEANUP_TOOLS, CleanupToolContext

AGENT_NAME = AgentJobKind.TASK_CLEANUP


@dataclass(frozen=True)
class CleanupDeps:
    """정리 스캔의 선별과 조사와 결정 노드가 함께 받는 실행 의존성이다."""

    req: TaskCleanupRequest
    reader: CleanupLedgerReader
    usage: ExecutionTrace
    chats: ChatPair
    budget: ExecutionBudget
    prompts: CleanupPrompts
    language_directives: Mapping[str, str]
    agents: CompiledAgentCache = field(default_factory=CompiledAgentCache)

    def new_loop(self, role: str | None = None, *, max_cost_usd: float | None = None) -> SharedToolLoopBudget:
        """노드가 자기 역할 이름으로 여는 도구 루프의 예산이며 역할이 없으면 조율자가 연 것이다."""
        name = AGENT_NAME if role is None else f"{AGENT_NAME}:{role}"
        return self.budget.new_loop(name, self.req.model, max_cost_usd=max_cost_usd)

    async def invoke[OutputT: BaseModel](
        self,
        *,
        budget: SharedToolLoopBudget,
        system_prompt: str,
        tool_names: Sequence[str],
        output: type[OutputT],
        messages: list[BaseMessage],
        missing_response: str,
        exposed_candidates: dict[str, CleanupCandidate] | None = None,
        event_ids_by_task: dict[str, set[str]] | None = None,
    ) -> StructuredAgentResult[OutputT]:
        """맡은 도구만 연 채 모델을 돌려 구조화 출력과 메시지와 이 호출이 쓴 턴을 낸다."""
        max_turns = self.req.limits.maxTurns
        names = tuple(tool_names)
        agent = self.agents.compiled(
            (system_prompt, names, output, max_turns),
            lambda: build_cleanup_agent(
                self.chats.primary,
                system_prompt,
                CLEANUP_TOOLS.langchain_tools(names),
                CLEANUP_TOOLS.transient_errors(),
                output=output,
                fallback_chat=self.chats.fallback,
                max_turns=max_turns,
            ),
        )
        return await invoke_structured_agent(
            agent,
            messages=messages,
            context=CleanupToolContext(
                agent_name=budget.agent_name,
                trace=self.usage,
                budget=budget,
                max_model_turns=max_turns,
                tool_owner=AGENT_NAME,
                reader=self.reader,
                batch=self.req.batch,
                exposed_candidates={} if exposed_candidates is None else exposed_candidates,
                event_ids_by_task={} if event_ids_by_task is None else event_ids_by_task,
            ),
            response_type=output,
            recursion_limit=recursion_limit_for(max_turns),
            missing_response=missing_response,
        )
