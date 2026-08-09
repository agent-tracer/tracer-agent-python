"""단가표가 계약과 같고 캐시 쓰기 단가가 실행 기계가 실제로 요청하는 수명과 맞는지 검증한다."""

from __future__ import annotations

import json

from tracer_agent.shared.agents.envelope.catalog import CACHE_WRITE_TTL, MODEL_RATES
from tracer_agent.shared.agents.shared.model_rates import MODEL_RATES_PATH, contract_model_rates
from tracer_agent.worker.agents.runtime.llm.middleware_stack import AgentMiddlewareStack
from tracer_agent.worker.agents.runtime.llm.prompt_cache import PromptCacheMiddleware


def _stack_cache_ttl() -> str:
    """실행 기계가 세우는 층에서 캐시 경계가 요청하는 수명을 읽는다."""
    layers = AgentMiddlewareStack(max_turns=1).build()
    caches = [one for one in layers if isinstance(one, PromptCacheMiddleware)]
    assert len(caches) == 1
    # 미들웨어가 세운 경계 값을 인스턴스 상태에서 그대로 읽는다.
    ttl: str = vars(caches[0])["_cache_control"]["ttl"]
    return ttl


def test_계약이_적은_모델을_빠짐없이_같은_단가로_안다() -> None:
    declared = json.loads(MODEL_RATES_PATH.read_text(encoding="utf-8"))["base"]

    assert set(MODEL_RATES) == set(declared)
    for name, rate in declared.items():
        assert MODEL_RATES[name].input == rate["input"]
        assert MODEL_RATES[name].output == rate["output"]


def test_단가가_실제로_요청하는_캐시_수명의_배수다() -> None:
    assert _stack_cache_ttl() == CACHE_WRITE_TTL

    declared = contract_model_rates()
    for rate in MODEL_RATES.values():
        assert rate.cacheWrite == rate.input * declared.write_multiplier_for(CACHE_WRITE_TTL)
        assert rate.cacheRead == rate.input * declared.read_multiplier
