"""잡 원장의 사용량 한 칸이 계약이 적은 모델 값 규칙을 지키는지 검증한다."""

from __future__ import annotations

from typing import Any

from tests.support.contract import conformance_case
from tracer_agent.shared.agents.shared.models import AgentResponse, UsageDTO
from tracer_agent.worker.workflows.jobs_outcome import job_usage

_MODEL_RULE: dict[str, Any] = conformance_case("job.intake")["response"]["usage"]["model"]
_ALIAS: str = _MODEL_RULE["example"]
# 계약은 금지하는 값을 설명 문장 뒤에 적으므로 그 문장에서 식별자만 떼어 쓴다.
_VERSIONED: str = str(_MODEL_RULE["forbidden"]).rsplit("—", 1)[-1].strip()


def _response(actual_model: str | None) -> AgentResponse:
    return AgentResponse(
        data={},
        modelUsed=_ALIAS,
        durationMs=120,
        usage=UsageDTO(inputTokens=10, outputTokens=2, cacheReadTokens=0, cacheCreationTokens=0),
        actualModel=actual_model,
    )


class Test사용량_모델_칸:
    def test_봉투가_건넨_별칭을_적는다(self) -> None:
        usage = job_usage(_response(_VERSIONED), 0.001)

        assert usage["model"] == _ALIAS

    def test_공급자가_판까지_붙은_식별자를_답해도_그것을_싣지_않는다(self) -> None:
        usage = job_usage(_response(_VERSIONED), 0.001)

        assert usage["model"] != _VERSIONED

    def test_공급자가_답하기_전에_실패해도_칸이_비지_않는다(self) -> None:
        usage = job_usage(AgentResponse(modelUsed=_ALIAS, durationMs=0), None)

        assert usage["model"] == _ALIAS
