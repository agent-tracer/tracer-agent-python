"""캐시 쓰기 단가가 실행 기계가 실제로 쓰는 캐시 수명과 맞는지 검증한다."""

from __future__ import annotations

from tracer_agent.shared.agents.envelope.catalog import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_MULTIPLIER,
    CACHE_WRITE_TTL,
    MODEL_RATES,
)
from tracer_agent.worker.agents.runtime.llm.middleware_stack import AgentMiddlewareStack
from tracer_agent.worker.agents.runtime.llm.prompt_cache import PromptCacheMiddleware


def _stack_cache_ttl() -> str:
    """실행 기계가 세우는 층에서 캐시 경계가 쓰는 수명을 읽는다."""
    layers = AgentMiddlewareStack(max_turns=1).build()
    caches = [one for one in layers if isinstance(one, PromptCacheMiddleware)]
    assert len(caches) == 1
    # 미들웨어가 세운 경계 값을 인스턴스 상태에서 그대로 읽는다.
    ttl: str = vars(caches[0])["_cache_control"]["ttl"]
    return ttl


def test_공식_문서가_적은_배수를_그대로_갖는다() -> None:
    # https://platform.claude.com/docs/en/build-with-claude/prompt-caching
    assert CACHE_WRITE_MULTIPLIER == {"5m": 1.25, "1h": 2.0}
    assert CACHE_READ_MULTIPLIER == 0.1


def test_단가가_실제로_쓰는_캐시_수명의_배수다() -> None:
    assert _stack_cache_ttl() == CACHE_WRITE_TTL

    for rate in MODEL_RATES.values():
        assert rate.cacheWrite == rate.input * CACHE_WRITE_MULTIPLIER[CACHE_WRITE_TTL]
        assert rate.cacheRead == rate.input * CACHE_READ_MULTIPLIER
