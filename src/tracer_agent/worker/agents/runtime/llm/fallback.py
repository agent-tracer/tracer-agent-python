"""공급자 오류로 모델 호출이 실패했을 때 대체 모델로 한 번만 넘어가는 런타임 소유 미들웨어."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel

from .retry import PROVIDER_TRANSIENT_ERRORS


# stock ModelFallbackMiddleware는 모든 Exception에서 넘어가 예산·절단 신호까지 삼키므로 여기서는 쓰지 않는다.
class FallbackModelMiddleware(AgentMiddleware[Any, Any, Any]):
    """공급자가 일시적으로 응답하지 못할 때만 대체 모델 호출로 한 번 넘어가는 미들웨어다."""

    def __init__(self, fallback_chat: BaseChatModel) -> None:
        """대체할 채팅 모델 하나를 가진다."""
        super().__init__()
        self._fallback_chat = fallback_chat

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """primary 호출이 공급자 일시 오류로 실패하면 대체 모델로 한 번만 재호출한다."""
        try:
            response: ModelResponse[Any] = await handler(request)
        # 요청 자체가 틀린 오류는 대체 모델에도 같은 답이 오므로 넘어가지 않고 그대로 재전파한다.
        except PROVIDER_TRANSIENT_ERRORS:
            response = await handler(request.override(model=self._fallback_chat))
        return response
