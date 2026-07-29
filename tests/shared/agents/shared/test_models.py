"""공유 실행 envelope의 폴백 모델 결정과 멱등 해시 제외를 검증한다."""

from __future__ import annotations

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES
from tracer_agent.shared.agents.shared.models import AgentExecutionRequest

_COMPLETION = {"url": "http://worker:8810/runs/complete", "token": "done-shared"}


def _request(**overrides: object) -> AgentExecutionRequest:
    values: dict[str, object] = {
        "model": "claude-sonnet-4-6",
        "apiKey": "sk-test",
        "modelRates": WIRE_MODEL_RATES,
        "limits": WIRE_LIMITS,
        "completionCallback": _COMPLETION,
    }
    values.update(overrides)
    return AgentExecutionRequest.model_validate(values)


class TestEffectiveFallbackModel:
    def test_폴백이_없으면_None이다(self) -> None:
        assert _request().effective_fallback_model() is None

    def test_폴백이_primary와_같으면_None이다(self) -> None:
        req = _request(fallbackModel="claude-sonnet-4-6")
        assert req.effective_fallback_model() is None

    def test_폴백이_primary와_다르면_그대로_돌려준다(self) -> None:
        req = _request(fallbackModel="claude-haiku-4-5")
        assert req.effective_fallback_model() == "claude-haiku-4-5"


def test_멱등_입력_해시는_폴백_모델에_영향받지_않는다() -> None:
    base = _request(idempotencyKey="key-1")
    changed = _request(idempotencyKey="key-1", fallbackModel="claude-haiku-4-5")

    assert base.idempotency_input_hash() == changed.idempotency_input_hash()
