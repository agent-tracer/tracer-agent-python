"""호출 상한에 닿은 도구 루프를 끝내되 그 사실을 알리는 문구가 대화에 섞이지 않게 한다."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import ModelCallLimitMiddleware, hook_config
from langgraph.runtime import Runtime


class TurnLimitMiddleware(ModelCallLimitMiddleware[Any, Any]):
    """상한에 닿으면 루프를 끝내며, 끝내는 이유를 어시스턴트 발화로 남기지 않는다."""

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: Any, runtime: Runtime[Any]) -> dict[str, Any] | None:
        """상한 판정은 그대로 두고 끝낼 때 실리는 안내 메시지만 덜어낸다."""
        update = super().before_model(state, runtime)
        if update is None:
            return None
        # 이 안내는 모델이 쓴 답이 아니므로 최종 답변을 고르는 자리가 그것을 답으로 볼 수 없어야 한다.
        return {"jump_to": update["jump_to"]}
