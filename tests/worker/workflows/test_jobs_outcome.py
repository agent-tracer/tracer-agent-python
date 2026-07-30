"""잡 원장의 사용량 한 칸이 계약이 적은 모델 값 규칙을 지키는지 검증한다."""

from __future__ import annotations

from typing import Any

from tests.support.contract import conformance_case
from tracer_agent.shared.agents.shared.models import AgentErrorDTO, AgentResponse, UsageDTO
from tracer_agent.worker.workflows.jobs_outcome import job_usage

_USAGE: dict[str, Any] = conformance_case("job.intake")["response"]["usage"]
_MODEL_RULE: dict[str, Any] = _USAGE["model"]
_FAILED_RULE: dict[str, Any] = _USAGE["failed"]
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


def _failed(subtype: str | None = "api_error") -> AgentResponse:
    return AgentResponse(
        modelUsed=_ALIAS,
        durationMs=90,
        usage=UsageDTO(inputTokens=10, outputTokens=2, cacheReadTokens=0, cacheCreationTokens=0),
        error=AgentErrorDTO(subtype=subtype, summary="provider refused the call"),
        providerRequestId="req-1",
    )


class Test끝내지_못한_잡의_사용량:
    def test_계약이_적은_모양_하나로_시도_이력을_싣는다(self) -> None:
        usage = job_usage(_failed(), 0.002, attempt=2)

        assert list(usage) == [_FAILED_RULE["shape"]]

    def test_시도_하나의_기록이_계약이_적은_칸을_그대로_갖는다(self) -> None:
        record = job_usage(_failed(), 0.002, attempt=2)[_FAILED_RULE["shape"]][0]

        assert sorted(record) == sorted(_FAILED_RULE["fields"])

    def test_이번_시도의_회차와_사유를_적는다(self) -> None:
        record = job_usage(_failed(), 0.002, attempt=2)[_FAILED_RULE["shape"]][0]

        assert record["attempt"] == 2
        assert record["status"] == "failed"
        assert record["subtype"] == "api_error"
        assert record["errorMessage"] == "provider refused the call"
        assert record["providerRequestId"] == "req-1"

    def test_봉투가_건넨_별칭과_비용을_함께_적는다(self) -> None:
        record = job_usage(_failed(), 0.002)[_FAILED_RULE["shape"]][0]

        assert record["model"] == _ALIAS
        assert record["costUsd"] == 0.002
        assert record["durationMs"] == 90
        assert record["usage"] == {
            "inputTokens": 10,
            "outputTokens": 2,
            "cacheReadTokens": 0,
            "cacheCreationTokens": 0,
        }

    def test_취소된_잡도_같은_모양으로_싣는다(self) -> None:
        usage = job_usage(_failed(subtype="cancelled"), None)

        assert list(usage) == [_FAILED_RULE["shape"]]
        assert usage["attempts"][0]["subtype"] == "cancelled"

    def test_공급자가_답하기_전에_실패해도_시도_이력이_남는다(self) -> None:
        response = AgentResponse(
            modelUsed=_ALIAS,
            durationMs=0,
            error=AgentErrorDTO(subtype=None, summary="connection reset"),
        )

        record = job_usage(response, None)[_FAILED_RULE["shape"]][0]

        assert record["usage"] is None
        assert record["model"] == _ALIAS
