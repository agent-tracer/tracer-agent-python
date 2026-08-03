"""recipe-scan 노드가 함께 받는 실행 의존성과 그 의존성으로 여는 모델 호출을 소유한다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from tracer_agent.shared.agents.recipe_scan.models import ProvenanceCatalog, RecipeScanRequest
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
from ..shared.prompt_source_port import AgentPrompt
from .langchain_agent import build_recipe_agent
from .prompts import RecipePrompts
from .reader import RecipeLedgerReader
from .search import RecipeSearchReader
from .tools import RECIPE_TOOLS, RecipeToolContext

AGENT_NAME = AgentJobKind.RECIPE_SCAN


@dataclass(frozen=True)
class RecipeDeps:
    """조사 계획과 전문가 조사와 종합 노드가 함께 받는 실행 의존성이다."""

    req: RecipeScanRequest
    reader: RecipeLedgerReader
    search: RecipeSearchReader
    usage: ExecutionTrace
    chats: ChatPair
    budget: ExecutionBudget
    prompts: RecipePrompts
    prompt: AgentPrompt
    language_directives: Mapping[str, str]
    agents: CompiledAgentCache = field(default_factory=CompiledAgentCache)

    def new_loop(self, role: str | None = None, *, max_cost_usd: float | None = None) -> SharedToolLoopBudget:
        """노드가 자기 역할 이름으로 여는 도구 루프의 예산이며 역할이 없으면 조율자가 연 것이다."""
        name = AGENT_NAME if role is None else f"{AGENT_NAME}:{role}"
        return self.budget.new_loop(name, self.req.model, max_cost_usd=max_cost_usd)

    def call_id(self, step: str) -> str:
        """한 실행 안에서 이 호출을 가리키는 이름이다."""
        return f"{self.req.executionId or self.req.jobId}:{step}"

    async def invoke[OutputT: BaseModel](
        self,
        *,
        budget: SharedToolLoopBudget,
        system_prompt: str,
        catalog: ProvenanceCatalog,
        tools: Sequence[str],
        output: type[OutputT],
        messages: list[BaseMessage],
        missing_response: str,
        max_turns: int,
        call_id: str,
        tool_owner: str = AGENT_NAME,
    ) -> StructuredAgentResult[OutputT]:
        """맡은 도구만 연 채 모델을 돌려 구조화 출력과 그 호출이 그은 턴을 낸다."""
        names = tuple(tools)
        agent = self.agents.compiled(
            (system_prompt, names, output, max_turns),
            lambda: build_recipe_agent(
                self.chats.primary,
                system_prompt,
                RECIPE_TOOLS.langchain_tools(names),
                RECIPE_TOOLS.transient_errors(names),
                output=output,
                fallback_chat=self.chats.fallback,
                max_turns=max_turns,
            ),
        )
        return await invoke_structured_agent(
            agent,
            messages=messages,
            context=RecipeToolContext(
                agent_name=budget.agent_name,
                trace=self.usage,
                budget=budget,
                max_model_turns=max_turns,
                tool_owner=tool_owner,
                reader=self.reader,
                search=self.search,
                catalog=catalog,
            ),
            response_type=output,
            recursion_limit=recursion_limit_for(max_turns),
            missing_response=missing_response,
            call_id=call_id,
        )
