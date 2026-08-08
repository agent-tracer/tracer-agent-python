"""재시도가 소진된 도구 실패를 모델이 읽고 복구할 수 있는 결과로 바꾼다."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

from ..errors import BudgetExceeded, DeadlineExceeded, OutputTruncated, exception_summary

# 실행 전체를 멈추는 신호이며 도구 하나의 실패로 낮춰 모델에게 넘기지 않는다.
EXECUTION_HALTING_ERRORS: tuple[type[BaseException], ...] = (
    GraphBubbleUp,
    asyncio.CancelledError,
    BudgetExceeded,
    OutputTruncated,
    DeadlineExceeded,
)


class ToolFailureMiddleware(AgentMiddleware[Any, Any, Any]):
    """도구 하나의 실패가 그래프를 뚫고 나가 조사 전체를 잃지 않게 막는다."""

    def __init__(self, failure_text: str) -> None:
        """두 구현체가 같은 실패에 같은 글자를 내도록 계약이 소유한 문구를 받는다."""
        super().__init__()
        self._failure_text = failure_text

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        try:
            return await handler(request)
        except EXECUTION_HALTING_ERRORS:
            raise
        except Exception as failed:
            call = request.tool_call
            return ToolMessage(
                content=self._failure_text.format(tool=call["name"], reason=exception_summary(failed)),
                name=call["name"],
                tool_call_id=call["id"] or "",
                status="error",
            )
