"""미들웨어 스택의 순서 의존이 두 에이전트에서 지켜지는지 검증한다(모델 호출 없음)."""

from __future__ import annotations

from typing import Any

import httpx
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from tracer_agent.worker.agents.chat.langchain_agent import chat_middleware
from tracer_agent.worker.agents.recipe_scan.langchain_agent import recipe_middleware

_TRANSIENT: tuple[type[Exception], ...] = (httpx.TransportError,)
_FALLBACK = GenericFakeChatModel(messages=iter([]))


def _kinds(middleware: list[Any]) -> list[str]:
    return [type(one).__name__ for one in middleware]


class Test미들웨어순서:
    def test_캐시_경계가_남은_몫을_알리는_꼬리보다_안쪽에_선다(self) -> None:
        # 꼬리가 붙기 전에 서면 경계가 호출마다 바뀌는 그 꼬리 위에 놓여 다시 읽히지 않는다.
        for kinds in (
            _kinds(recipe_middleware(_TRANSIENT, max_turns=4)),
            _kinds(chat_middleware(_TRANSIENT, max_turns=4)),
        ):
            assert kinds.index("StandardAgentMiddleware") < kinds.index("PromptCacheMiddleware")

    def test_모델에게_알리는_총량과_실제_상한이_같다(self) -> None:
        # 총량보다 상한이 크면 계약이 실행 전체의 턴 총량이라고 적은 값이 상한이 아니게 된다.
        for middleware in (
            recipe_middleware(_TRANSIENT, max_turns=4),
            chat_middleware(_TRANSIENT, max_turns=4),
        ):
            limit = next(one for one in middleware if isinstance(one, ModelCallLimitMiddleware))
            assert limit.run_limit == 4

    def test_맥락_정리가_비용_장부보다_안쪽에_선다(self) -> None:
        for kinds in (
            _kinds(recipe_middleware(_TRANSIENT, max_turns=4)),
            _kinds(chat_middleware(_TRANSIENT, max_turns=4)),
        ):
            assert kinds.index("ContextEditingMiddleware") < kinds.index("StandardAgentMiddleware")

    def test_같은_모델_재시도가_대체_모델보다_바깥에_선다(self) -> None:
        for kinds in (
            _kinds(recipe_middleware(_TRANSIENT, max_turns=4, fallback_chat=_FALLBACK)),
            _kinds(chat_middleware(_TRANSIENT, max_turns=4, fallback_chat=_FALLBACK)),
        ):
            assert kinds.index("FallbackModelMiddleware") < kinds.index("ModelRetryMiddleware")

    def test_대체_모델이_없으면_그_미들웨어를_세우지_않는다(self) -> None:
        assert "FallbackModelMiddleware" not in _kinds(recipe_middleware(_TRANSIENT, max_turns=4))
        assert "FallbackModelMiddleware" not in _kinds(chat_middleware(_TRANSIENT, max_turns=4))

    def test_도구_재시도가_모델_재시도보다_안쪽에_선다(self) -> None:
        for kinds in (
            _kinds(recipe_middleware(_TRANSIENT, max_turns=4)),
            _kinds(chat_middleware(_TRANSIENT, max_turns=4)),
        ):
            assert kinds.index("ToolRetryMiddleware") < kinds.index("ModelRetryMiddleware")

    def test_대화의_도구는_공유_장부를_직렬화한다(self) -> None:
        standard = next(
            one
            for one in chat_middleware(_TRANSIENT, max_turns=4)
            if type(one).__name__ == "StandardAgentMiddleware"
        )
        assert standard._tool_lock is not None  # noqa: SLF001
