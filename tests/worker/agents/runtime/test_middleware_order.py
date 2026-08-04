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
    """LangChain이 목록의 첫 항목을 가장 바깥으로 합성하므로 낮은 자리가 더 바깥이다."""
    return [type(one).__name__ for one in middleware]


class Test미들웨어순서:
    def test_캐시_경계가_남은_몫을_알리는_꼬리보다_안쪽에_선다(self) -> None:
        # 꼬리가 붙기 전에 서면 경계가 호출마다 바뀌는 그 꼬리 위에 놓여 다시 읽히지 않는다.
        for kinds in (
            _kinds(recipe_middleware(_TRANSIENT, max_turns=4)),
            _kinds(chat_middleware(_TRANSIENT, max_turns=4)),
        ):
            assert kinds.index("StandardAgentMiddleware") < kinds.index("PromptCacheMiddleware")

    def test_상한은_알리는_총량보다_마무리_몫만큼_넉넉하다(self) -> None:
        # 도구를 부른 턴과 산출을 내는 턴이 같은 수를 나눠 쓰므로 딱 맞추면 산출을 낼 자리가 없다.
        for middleware in (
            recipe_middleware(_TRANSIENT, max_turns=4),
            chat_middleware(_TRANSIENT, max_turns=4),
        ):
            limit = next(one for one in middleware if isinstance(one, ModelCallLimitMiddleware))
            assert limit.run_limit == 6

    def test_맥락_정리가_비용_장부보다_바깥에_선다(self) -> None:
        for kinds in (
            _kinds(recipe_middleware(_TRANSIENT, max_turns=4)),
            _kinds(chat_middleware(_TRANSIENT, max_turns=4)),
        ):
            assert kinds.index("ContextEditingMiddleware") < kinds.index("StandardAgentMiddleware")

    def test_같은_모델_재시도가_대체_모델보다_안쪽에_선다(self) -> None:
        for kinds in (
            _kinds(recipe_middleware(_TRANSIENT, max_turns=4, fallback_chat=_FALLBACK)),
            _kinds(chat_middleware(_TRANSIENT, max_turns=4, fallback_chat=_FALLBACK)),
        ):
            assert kinds.index("FallbackModelMiddleware") < kinds.index("ModelRetryMiddleware")

    def test_대체_모델이_없으면_그_미들웨어를_세우지_않는다(self) -> None:
        assert "FallbackModelMiddleware" not in _kinds(recipe_middleware(_TRANSIENT, max_turns=4))
        assert "FallbackModelMiddleware" not in _kinds(chat_middleware(_TRANSIENT, max_turns=4))

    def test_도구_재시도가_모델_재시도보다_바깥에_선다(self) -> None:
        for kinds in (
            _kinds(recipe_middleware(_TRANSIENT, max_turns=4)),
            _kinds(chat_middleware(_TRANSIENT, max_turns=4)),
        ):
            assert kinds.index("ToolRetryMiddleware") < kinds.index("ModelRetryMiddleware")

    def test_구조화_복구가_비용_장부보다_바깥에_선다(self) -> None:
        # 안쪽에 서면 거부된 산출이 장부에 닿지 못해 그 호출의 토큰이 예산에서 빠진다.
        kinds = _kinds(recipe_middleware(_TRANSIENT, max_turns=4))
        assert kinds.index("StructuredOutputRepairMiddleware") < kinds.index("StandardAgentMiddleware")

    def test_대화의_도구는_공유_장부를_직렬화한다(self) -> None:
        standard = next(
            one
            for one in chat_middleware(_TRANSIENT, max_turns=4)
            if type(one).__name__ == "StandardAgentMiddleware"
        )
        assert standard._serializes_tools  # noqa: SLF001
