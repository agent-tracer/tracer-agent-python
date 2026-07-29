"""공급자 오류로 모델 호출이 실패했을 때 대체 모델로 한 번만 넘어가는 런타임 소유 미들웨어."""

from __future__ import annotations

from typing import Any

from anthropic import APIError
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel


# stock ModelFallbackMiddleware는 모든 Exception에서 넘어가 예산·절단 신호까지 삼키므로 여기서는 쓰지 않는다.
class FallbackModelMiddleware(AgentMiddleware[Any, Any, Any]):
    """공급자 오류에서만 대체 모델 호출로 한 번 넘어가는 미들웨어다."""

    def __init__(self, fallback_chat: BaseChatModel) -> None:
        """대체할 채팅 모델 하나를 쥔다."""
        super().__init__()
        self._fallback_chat = fallback_chat

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Any,
    ) -> ModelResponse[Any]:
        """primary 호출이 공급자 오류로 실패하면 대체 모델로 한 번만 재호출한다."""
        try:
            response: ModelResponse[Any] = await handler(request)
        except APIError:
            response = await handler(request.override(model=self._fallback_chat))
        return response
