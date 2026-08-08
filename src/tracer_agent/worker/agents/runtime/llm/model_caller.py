"""도구 레지스트리와 모델 쌍을 가지고 구조화 호출 하나를 여는 자리를 소유한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from ..tooling import ToolRegistry
from .agent_cache import CompiledAgentCache
from .client import ChatPair
from .standard_agent import StandardAgentContext
from .structured_agent import (
    StructuredAgentResult,
    build_structured_agent,
    invoke_structured_agent,
    recursion_limit_for,
)


@dataclass(frozen=True)
class ModelCall[OutputT: BaseModel]:
    """호출 하나가 여는 도구와 프롬프트와 요구하는 산출 계약과 그 호출의 턴 상한이다."""

    system_prompt: str
    output: type[OutputT]
    messages: list[BaseMessage]
    missing_response: str
    max_turns: int
    # 이름을 주지 않으면 이 레지스트리의 도구 전체가 열린다.
    tools: tuple[str, ...] | None = None
    # 부모 그래프가 실행 상태를 보존하면 이 호출도 자기 열쇠를 갖는다.
    call_id: str | None = None


class StructuredModelCaller[ContextT: StandardAgentContext]:
    """같은 프롬프트와 도구와 상한으로 다시 부르면 컴파일한 agent를 그대로 다시 쓴다."""

    def __init__(
        self,
        chats: ChatPair,
        registry: ToolRegistry[Any],
        *,
        name: str,
        context_schema: type[ContextT],
        tool_failure_text: str,
        serializes_tools: bool = False,
    ) -> None:
        self._chats = chats
        self._registry = registry
        self._name = name
        self._context_schema = context_schema
        self._tool_failure_text = tool_failure_text
        self._serializes_tools = serializes_tools
        self._agents = CompiledAgentCache()

    def compiled_agents(self) -> int:
        """이 실행이 지금까지 컴파일한 agent 수이며 같은 조건의 호출은 이 수를 늘리지 않는다."""
        return self._agents.size()

    async def invoke[OutputT: BaseModel](
        self, call: ModelCall[OutputT], context: ContextT
    ) -> StructuredAgentResult[OutputT]:
        """맡은 도구만 연 채 모델을 호출해 구조화 출력과 그 호출이 그은 턴을 낸다."""
        agent = self._agents.compiled(
            (call.system_prompt, call.tools, call.output, call.max_turns),
            lambda: build_structured_agent(
                self._chats.primary,
                call.system_prompt,
                self._registry.langchain_tools(call.tools),
                self._registry.transient_errors(call.tools),
                output=call.output,
                context_schema=self._context_schema,
                name=self._name,
                max_turns=call.max_turns,
                tool_failure_text=self._tool_failure_text,
                fallback_chat=self._chats.fallback,
                serializes_tools=self._serializes_tools,
            ),
        )
        return await invoke_structured_agent(
            agent,
            messages=call.messages,
            context=context,
            response_type=call.output,
            recursion_limit=recursion_limit_for(call.max_turns),
            missing_response=call.missing_response,
            call_id=call.call_id,
        )
