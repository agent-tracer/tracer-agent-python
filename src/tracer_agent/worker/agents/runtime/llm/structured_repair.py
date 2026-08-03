"""공급자가 강제하지 못한 스키마 제약에 걸린 산출을 한 번 되먹여 다시 받는다."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.agents.structured_output import StructuredOutputValidationError
from langchain_core.messages import HumanMessage

from .prompt_cache import volatile

# 공급자는 JSON 의 모양은 강제하지만 길이와 개수 같은 제약까지는 강제하지 않는다.
REPAIR_DIRECTIVE = (
    "Your previous output did not satisfy the output schema: {reason}. "
    "Send the same answer again, corrected so every field satisfies the schema. "
    "Do not call any tool and do not add commentary."
)


class StructuredOutputRepairMiddleware(AgentMiddleware[Any, Any, Any]):
    """스키마를 어긴 산출을 사유와 함께 한 번 되돌려 주고 그 자리에서 다시 받는다."""

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """산출이 스키마를 어기면 사유를 붙여 한 번만 다시 부른다."""
        try:
            return await handler(request)
        except StructuredOutputValidationError as invalid:
            directive = REPAIR_DIRECTIVE.format(reason=_reason(invalid))
            return await handler(
                request.override(messages=[*request.messages, volatile(HumanMessage(content=directive))])
            )


def _reason(invalid: StructuredOutputValidationError) -> str:
    """모델이 무엇을 고쳐야 하는지 알 만큼만 남긴 한 줄이다."""
    return " ".join(str(invalid).split())
