"""task-cleanup 노드가 함께 받는 실행 의존성과 그 의존성으로 여는 모델 호출을 소유한다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from tracer_agent.shared.agents.task_cleanup.models import CleanupCandidate, TaskCleanupRequest
from tracer_agent.shared.workflows.jobs_kinds import AgentJobKind

from ..runtime.execution.trace import ExecutionTrace
from ..runtime.llm.budget import ExecutionBudget, SharedToolLoopBudget
from ..runtime.llm.client import ChatPair
from ..runtime.llm.model_caller import ModelCall, StructuredModelCaller
from ..runtime.llm.structured_agent import StructuredAgentResult
from ..shared.prompt_source_port import AgentPrompt
from .prompts import CleanupPrompts
from .reader import CleanupLedgerReader
from .tools import CLEANUP_TOOLS, CleanupToolContext

AGENT_NAME = AgentJobKind.TASK_CLEANUP


def new_cleanup_caller(chats: ChatPair) -> StructuredModelCaller[CleanupToolContext]:
    """이 실행이 쓸 모델 호출자를 세우며 노출 장부를 공유하므로 도구를 직렬화한다."""
    return StructuredModelCaller(
        chats,
        CLEANUP_TOOLS,
        name="task-cleanup-investigator",
        context_schema=CleanupToolContext,
        serializes_tools=True,
    )


@dataclass(frozen=True)
class CleanupDeps:
    """정리 스캔의 선별과 조사와 결정 노드가 함께 받는 실행 의존성이다."""

    req: TaskCleanupRequest
    reader: CleanupLedgerReader
    usage: ExecutionTrace
    caller: StructuredModelCaller[CleanupToolContext]
    budget: ExecutionBudget
    prompts: CleanupPrompts
    prompt: AgentPrompt
    language_directives: Mapping[str, str]

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
        """맡은 도구만 연 채 모델을 호출해 구조화 출력과 메시지와 이 호출이 쓴 턴을 낸다."""
        max_turns = self.req.limits.maxTurns
        return await self.caller.invoke(
            ModelCall(
                system_prompt=system_prompt,
                output=output,
                messages=messages,
                missing_response=missing_response,
                max_turns=max_turns,
                tools=tuple(tool_names),
            ),
            CleanupToolContext(
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
        )
