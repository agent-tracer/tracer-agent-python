"""표준 LangChain agent가 비용과 궤적과 캐시 계약을 지키게 한다."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from langchain.agents.middleware import (
    AgentMiddleware,
    ClearToolUsesEdit,
    ContextEditingMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from ..errors import OutputTruncated
from ..execution.trace import ExecutionTrace
from .budget import ModelCallBudget
from .trajectory import is_truncated

BUDGET_NOTICE = "You have used {used} of {total} tool-calling turns; converge as you approach the limit."

# 이 토큰 수를 넘는 실행에서 오래된 도구 결과를 정리한다.
CONTEXT_EDITING_TRIGGER_TOKENS = 100_000
# 정리 대상에서 최근 도구 결과 이만큼은 남긴다.
CONTEXT_EDITING_KEEP_TOOL_RESULTS = 2


def context_editing_middleware() -> ContextEditingMiddleware:
    """메시지 꼬리의 오래된 도구 결과만 비워 캐시 경계와 겹치지 않는 맥락 정리 미들웨어를 만든다."""
    return ContextEditingMiddleware(
        edits=[
            ClearToolUsesEdit(
                trigger=CONTEXT_EDITING_TRIGGER_TOKENS,
                keep=CONTEXT_EDITING_KEEP_TOOL_RESULTS,
            )
        ]
    )


FINALIZE_STRUCTURED_DIRECTIVE = (
    "The investigation budget is exhausted. Stop calling tools and produce the final structured "
    "output now using only the evidence you already verified."
)

FINALIZE_TEXT_DIRECTIVE = (
    "The investigation budget is exhausted. Stop calling tools and write your final answer to the "
    "user now in plain prose, using only what you already retrieved."
)


def finalize_directive(*, structured_output: bool) -> str:
    """예산이 바닥난 실행에 그 실행의 최종 산출 형태에 맞는 마무리 지시를 고른다."""
    return FINALIZE_STRUCTURED_DIRECTIVE if structured_output else FINALIZE_TEXT_DIRECTIVE


@dataclass(kw_only=True)
class StandardAgentContext:
    """표준 agent 도구와 미들웨어가 공유하는 요청별 실행 의존성이다."""

    agent_name: str
    trace: ExecutionTrace
    budget: ModelCallBudget
    # 모델이 자기 페이싱을 가늠하는 표시용 턴 총량이며 이 값 자체는 종료를 결정하지 않는다.
    max_model_turns: int


# noinspection PyTypeChecker
class StandardAgentMiddleware(AgentMiddleware[Any, StandardAgentContext, Any]):
    """모델 비용과 실행 궤적과 도구 결과를 제품 계약으로 기록한다."""

    def __init__(self, *, serialize_tools: bool = False) -> None:
        """공유 장부를 쓰는 도구를 이 인스턴스가 쥔 락으로 직렬화할지 정한다."""
        super().__init__()
        self._tool_lock = asyncio.Lock() if serialize_tools else None

    # noinspection PyTypeChecker
    async def awrap_model_call(
        self,
        request: ModelRequest[StandardAgentContext],
        handler: Any,
    ) -> ModelResponse[Any]:
        response: ModelResponse[Any] = await handler(_with_budget(request))
        for message in response.result:
            if not isinstance(message, AIMessage):
                continue
            context = request.runtime.context
            context.trace.add_message(message)
            context.trace.record_message(message)
            context.budget.charge(message)
            if is_truncated(message):
                raise OutputTruncated(f"{context.agent_name} structured output truncated at max_tokens")
        return response

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        if self._tool_lock is None:
            return await self._invoke_tool(request, handler)
        async with self._tool_lock:
            return await self._invoke_tool(request, handler)

    @staticmethod
    async def _invoke_tool(
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        response = await handler(request)
        if isinstance(response, ToolMessage):
            tool_context(request, StandardAgentContext).trace.record_message(response)
        return response


# noinspection PyTypeChecker
def tool_context[ContextT: StandardAgentContext](
    request: ToolCallRequest, _schema: type[ContextT]
) -> ContextT:
    """도구 호출 요청에 실려 온 이 실행의 컨텍스트를 꺼낸다."""
    # ToolCallRequest가 컨텍스트 타입을 싣지 않아 None으로 굳지만 실제 값은 context_schema 인스턴스다.
    return cast("ContextT", request.runtime.context)


# noinspection PyTypeChecker
def _with_budget(request: ModelRequest[StandardAgentContext]) -> ModelRequest[StandardAgentContext]:
    context = request.runtime.context
    messages = request.messages
    if not context.budget.landing:
        spent = dict(request.state).get("run_model_call_count", 0)
        used = spent if isinstance(spent, int) else 0
        notice = BUDGET_NOTICE.format(used=used, total=context.max_model_turns)
        return request.override(messages=[*messages, HumanMessage(content=notice)])
    context.budget.land()
    context.trace.mark_landed()
    # 구조화 출력 도구는 이 목록 밖에서 붙어 조사 도구만 거두며, 남은 산출 형태는 response_format이 가른다.
    directive = finalize_directive(structured_output=request.response_format is not None)
    return request.override(
        messages=[*messages, HumanMessage(content=directive)],
        tools=[],
    )
