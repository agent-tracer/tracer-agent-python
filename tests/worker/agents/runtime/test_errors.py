from __future__ import annotations

import httpx
from anthropic import APIConnectionError, AuthenticationError, InternalServerError
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError

from tests.support.contract import shared_contract
from tracer_agent.worker.agents.runtime.errors import (
    EMITTED_SUBTYPES,
    BudgetExceeded,
    DeadlineExceeded,
    OutputTruncated,
    classify_exception,
)


def _status_error(status: int, body: object | None = None) -> AuthenticationError | InternalServerError:
    request = httpx.Request("POST", "https://api.anthropic.com")
    response = httpx.Response(status, request=request)
    kind = AuthenticationError if status < 500 else InternalServerError
    return kind("boom", response=response, body=body)


class TestClassifyException:
    def test_데드라인은_deadline_exceeded로_비재시도(self) -> None:
        assert classify_exception(DeadlineExceeded("초과")).subtype == "deadline_exceeded"

    def test_예산초과는_budget_exceeded(self) -> None:
        assert classify_exception(BudgetExceeded("예산")).subtype == "budget_exceeded"

    def test_출력절단은_max_tokens(self) -> None:
        assert classify_exception(OutputTruncated("절단")).subtype == "max_tokens"

    def test_도구_예산_소진은_max_turns_exceeded로_비재시도(self) -> None:
        err = ModelCallLimitExceededError(thread_count=0, run_count=18, thread_limit=None, run_limit=18)

        classified = classify_exception(err)

        assert classified.subtype == "max_turns_exceeded"
        assert "18" in classified.summary

    def test_인증오류는_API가_준_type을_그대로_쓴다(self) -> None:
        err = _status_error(401, {"error": {"type": "authentication_error", "message": "bad key"}})

        assert classify_exception(err).subtype == "authentication_error"

    def test_type이_없는_4xx는_invalid_request_error로_접는다(self) -> None:
        assert classify_exception(_status_error(400)).subtype == "invalid_request_error"

    def test_type이_없는_5xx는_api_error로_접는다(self) -> None:
        assert classify_exception(_status_error(503)).subtype == "api_error"

    def test_연결오류는_connection_error(self) -> None:
        request = httpx.Request("POST", "https://api.anthropic.com")
        err = APIConnectionError(message="down", request=request)
        assert classify_exception(err).subtype == "connection_error"

    def test_알수없는_예외는_agent_execution_error(self) -> None:
        assert classify_exception(RuntimeError("boom")).subtype == "agent_execution_error"


class TestErrorSubtypeContract:
    def test_계약이_선언한_python_어휘와_같다(self) -> None:
        contract = shared_contract("error.subtypes.json")

        declared = {
            subtype for subtype, verdict in contract["emitted"].items() if "python" in verdict["emittedBy"]
        }

        assert declared == EMITTED_SUBTYPES

    def test_분류기가_이름_짓는_값은_모두_선언된_어휘다(self) -> None:
        request = httpx.Request("POST", "https://api.anthropic.com")
        samples: list[BaseException] = [
            DeadlineExceeded("초과"),
            BudgetExceeded("예산"),
            OutputTruncated("절단"),
            ModelCallLimitExceededError(thread_count=0, run_count=1, thread_limit=None, run_limit=1),
            APIConnectionError(message="down", request=request),
            _status_error(400),
            _status_error(503),
            RuntimeError("boom"),
        ]

        assert {classify_exception(err).subtype for err in samples} <= EMITTED_SUBTYPES
