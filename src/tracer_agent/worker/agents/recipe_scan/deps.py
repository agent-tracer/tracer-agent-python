"""recipe-scan 노드가 함께 받는 실행 의존성과 그 의존성으로 여는 모델 호출을 소유한다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from tracer_agent.shared.agents.recipe_scan.models import ProvenanceCatalog, RecipeScanRequest
from tracer_agent.shared.workflows.jobs_kinds import AgentJobKind

from ..runtime.execution.trace import ExecutionTrace
from ..runtime.llm.budget import ExecutionBudget, SharedToolLoopBudget
from ..runtime.llm.client import ChatPair
from ..runtime.llm.model_caller import ModelCall, StructuredModelCaller
from ..runtime.llm.structured_agent import StructuredAgentResult
from ..shared.prompt_source_port import AgentPrompt
from .prompts import RecipePrompts
from .reader import RecipeLedgerReader
from .search import RecipeSearchReader
from .tools import RECIPE_TOOLS, RecipeToolContext

AGENT_NAME = AgentJobKind.RECIPE_SCAN


def new_recipe_caller(chats: ChatPair) -> StructuredModelCaller[RecipeToolContext]:
    """이 실행이 쓸 모델 호출자를 세우며 같은 조건의 호출은 컴파일한 agent를 다시 쓴다."""
    return StructuredModelCaller(
        chats,
        RECIPE_TOOLS,
        name="recipe-scan-investigator",
        context_schema=RecipeToolContext,
    )


@dataclass(frozen=True)
class RecipeDeps:
    """조사 계획과 전문가 조사와 종합 노드가 함께 받는 실행 의존성이다."""

    req: RecipeScanRequest
    reader: RecipeLedgerReader
    search: RecipeSearchReader
    usage: ExecutionTrace
    caller: StructuredModelCaller[RecipeToolContext]
    budget: ExecutionBudget
    prompts: RecipePrompts
    prompt: AgentPrompt
    language_directives: Mapping[str, str]

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
        """맡은 도구만 연 채 모델을 호출해 구조화 출력과 그 호출이 그은 턴을 낸다."""
        return await self.caller.invoke(
            ModelCall(
                system_prompt=system_prompt,
                output=output,
                messages=messages,
                missing_response=missing_response,
                max_turns=max_turns,
                tools=tuple(tools),
                call_id=call_id,
            ),
            RecipeToolContext(
                agent_name=budget.agent_name,
                trace=self.usage,
                budget=budget,
                max_model_turns=max_turns,
                tool_owner=tool_owner,
                reader=self.reader,
                search=self.search,
                catalog=catalog,
            ),
        )
