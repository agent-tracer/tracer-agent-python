"""모델에 닿기까지 거치는 층을 네 에이전트가 함께 쓰는 순서 하나로 세운다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware
from langchain_core.language_models import BaseChatModel

from tracer_agent.shared.agents.shared.model_rates import cache_write_ttl

from .fallback import FallbackModelMiddleware
from .pacing import landing_reserve_calls
from .prompt_cache import PromptCacheMiddleware
from .retry import model_retry_middleware, tool_retry_middleware
from .standard_agent import StandardAgentMiddleware, context_editing_middleware
from .structured_repair import StructuredOutputRepairMiddleware
from .tool_failure import ToolFailureMiddleware
from .turn_limit import TurnLimitMiddleware


@dataclass(frozen=True)
class AgentMiddlewareStack:
    """계약이 그린 순서대로 층을 세우며 층을 빼는 자리는 이 선언에만 있다."""

    max_turns: int
    transient_errors: tuple[type[Exception], ...] = ()
    fallback_chat: BaseChatModel | None = None
    # 스키마에 걸려 버려진 산출을 다시 받는 층이며 구조화 출력을 요구하는 호출만 세운다.
    repairs_structured_output: bool = False
    # 공유 장부를 쓰는 도구를 이 호출의 락으로 직렬화할지 정한다.
    serializes_tools: bool = False
    # 상한에 닿았을 때 그때까지의 답을 남기고 끝낼지, 예외로 끊을지 정한다.
    ends_on_turn_limit: bool = False
    # 재시도가 소진된 도구 실패를 모델에게 돌려줄 때 쓰는 계약 문구이며 없으면 그 층을 세우지 않는다.
    tool_failure_text: str | None = None

    def build(self) -> list[AgentMiddleware[Any, Any, Any]]:
        """목록의 첫 항목이 가장 바깥이며 안쪽으로 갈수록 모델에 가깝다."""
        middleware: list[AgentMiddleware[Any, Any, Any]] = [self._turn_limit()]
        # 장부가 정리 뒤의 토큰을 세도록 StandardAgentMiddleware보다 앞에 둔다.
        middleware.append(context_editing_middleware())
        if self.repairs_structured_output:
            # 거부된 산출도 장부를 지나야 하므로 다시 받는 자리는 StandardAgentMiddleware보다 앞에 둔다.
            middleware.append(StructuredOutputRepairMiddleware())
        middleware.append(StandardAgentMiddleware(serialize_tools=self.serializes_tools))
        # 남은 몫을 알리는 꼬리가 붙은 뒤에 서야 경계를 그 꼬리 앞에 놓을 수 있다.
        middleware.append(PromptCacheMiddleware(ttl=cache_write_ttl()))
        if self.tool_failure_text is not None:
            # 재시도 바깥이어야 소진된 뒤에만 실패가 모델이 읽는 결과로 바뀐다.
            middleware.append(ToolFailureMiddleware(self.tool_failure_text))
        middleware.append(tool_retry_middleware(self.transient_errors))
        # 재시도가 더 안쪽이어야 같은 모델로 소진된 뒤에만 대체 모델로 넘어간다.
        if self.fallback_chat is not None:
            middleware.append(FallbackModelMiddleware(self.fallback_chat))
        middleware.append(model_retry_middleware())
        return middleware

    def _turn_limit(self) -> ModelCallLimitMiddleware[Any, Any]:
        # 모델에게 알린 총량 위에 마무리 호출의 몫을 얹어, 도구를 닫은 뒤의 호출이 설 자리를 남긴다.
        run_limit = self.max_turns + landing_reserve_calls()
        if self.ends_on_turn_limit:
            # error로 끊으면 그때까지의 답변과 도구 결과를 통째로 잃어 SDK 백엔드와 결과가 나뉜다.
            return TurnLimitMiddleware(run_limit=run_limit, exit_behavior="end")
        return ModelCallLimitMiddleware(run_limit=run_limit, exit_behavior="error")
