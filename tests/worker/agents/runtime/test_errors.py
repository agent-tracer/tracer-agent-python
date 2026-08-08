from __future__ import annotations

import httpx
from anthropic import APIConnectionError, APIError, AuthenticationError, InternalServerError
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langgraph.errors import NodeTimeoutError

from tests.support.contract import shared_contract
from tracer_agent.shared.agents.shared.redaction import marker
from tracer_agent.worker.agents.runtime.errors import (
    EMITTED_SUBTYPES,
    AgentExecutionError,
    BudgetExceeded,
    DeadlineExceeded,
    OutputTruncated,
    classify_exception,
    is_retryable_node_failure,
)


def _status_error(status: int, body: object | None = None) -> AuthenticationError | InternalServerError:
    request = httpx.Request("POST", "https://api.anthropic.com")
    response = httpx.Response(status, request=request)
    kind = AuthenticationError if status < 500 else InternalServerError
    return kind("boom", response=response, body=body)


def _every_classifiable() -> list[BaseException]:
    """분류기가 실제로 만날 수 있는 입력이며 표본이 아니라 갈래마다 하나씩 든다."""
    request = httpx.Request("POST", "https://api.anthropic.com")
    provider_types = [*shared_contract("error.subtypes.json")["provider"], "some_new_provider_error"]
    return [
        DeadlineExceeded("초과"),
        BudgetExceeded("예산"),
        OutputTruncated("절단"),
        ModelCallLimitExceededError(thread_count=0, run_count=1, thread_limit=None, run_limit=1),
        NodeTimeoutError("converse", 1.0, kind="run", run_timeout=1.0),
        APIConnectionError(message="down", request=request),
        APIError("boom", request=request, body=None),
        RuntimeError("boom"),
        *[_status_error(status) for status in (400, 401, 403, 404, 408, 413, 429, 500, 503)],
        *[_status_error(400, {"error": {"type": kind}}) for kind in provider_types],
        *[_status_error(529, {"error": {"type": kind}}) for kind in provider_types],
    ]


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

    def test_계약이_모르는_공급자_type은_상태로_접는다(self) -> None:
        err = _status_error(400, {"error": {"type": "some_new_provider_error"}})

        assert classify_exception(err).subtype == "invalid_request_error"

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


class TestErrorSummaryRedaction:
    """자격의 이름이 나오는 사유는 남고 자격의 값이 실린 자리만 가려진다."""

    def test_잘못된_키의_사유가_통째로_지워지지_않는다(self) -> None:
        err = _status_error(401, {"error": {"type": "authentication_error"}})
        err.args = ("invalid x-api-key",)

        summary = classify_exception(err).summary

        assert "x-api-key" in summary
        assert marker() not in summary

    def test_사유에_실린_자격의_몸통만_가려진다(self) -> None:
        secret = "sk-ant-" + "a" * 32

        summary = classify_exception(RuntimeError(f"provider refused {secret} for this key")).summary

        assert secret not in summary
        assert marker() in summary
        assert summary.startswith("provider refused ")
        assert summary.endswith(" for this key")


class TestThirdPartyChainOrder:
    """서드파티 예외 사슬은 좁은 서브클래스가 상위 예외보다 먼저 걸려야 뜻이 보존된다."""

    def test_연결오류가_상위_APIError보다_먼저_걸린다(self) -> None:
        request = httpx.Request("POST", "https://api.anthropic.com")
        err = APIConnectionError(message="down", request=request)

        # 이 관계가 성립하는 동안에만 사슬의 순서가 뜻을 갖는다.
        assert isinstance(err, APIError)
        assert classify_exception(err).subtype == "connection_error"

    def test_상태오류가_상위_APIError보다_먼저_걸린다(self) -> None:
        err = _status_error(401, {"error": {"type": "authentication_error"}})

        assert isinstance(err, APIError)
        assert classify_exception(err).subtype == "authentication_error"


class TestOwnErrorSubtypes:
    """자기 예외는 분류 사슬이 아니라 자기 자신이 서브타입을 든다."""

    def test_자기_예외가_스스로_서브타입을_든다(self) -> None:
        assert DeadlineExceeded.subtype == "deadline_exceeded"
        assert BudgetExceeded.subtype == "budget_exceeded"
        assert OutputTruncated.subtype == "max_tokens"

    def test_사슬을_고치지_않아도_새_자기_예외가_분류된다(self) -> None:
        class ProviderRefusedOutput(AgentExecutionError):
            subtype = "invalid_request_error"

        assert classify_exception(ProviderRefusedOutput("nope")).subtype == "invalid_request_error"

    def test_본문이_빈_데드라인은_기본_요약을_쓴다(self) -> None:
        assert classify_exception(DeadlineExceeded()).summary == "agent deadline exceeded"


class TestErrorSubtypeContract:
    def test_계약이_선언한_python_어휘와_같다(self) -> None:
        contract = shared_contract("error.subtypes.json")

        declared = {subtype for subtype, axes in contract["emittedBy"].items() if "python" in axes}

        assert declared == EMITTED_SUBTYPES

    def test_분류기가_이름_짓는_값은_모두_선언된_어휘다(self) -> None:
        contract = shared_contract("error.subtypes.json")
        declared = EMITTED_SUBTYPES | frozenset(contract["provider"])

        assert {classify_exception(err).subtype for err in _every_classifiable()} <= declared

    def test_공급자가_주는_모든_type이_계약의_공급자_절_안에_머문다(self) -> None:
        contract = shared_contract("error.subtypes.json")

        named = {
            classify_exception(_status_error(400, {"error": {"type": kind}})).subtype
            for kind in [*contract["provider"], "some_new_provider_error", "", "refusal_v2"]
        }

        assert named <= EMITTED_SUBTYPES | frozenset(contract["provider"])

    def test_공급자_어휘의_재시도_판정을_계약에서_읽는다(self) -> None:
        contract = shared_contract("error.subtypes.json")

        for kind, rule in contract["provider"].items():
            err = _status_error(429 if rule["retryable"] else 400, {"error": {"type": kind}})

            assert is_retryable_node_failure(err) is rule["retryable"]

    def test_계약이_일시로_적은_분류만_노드_재시도_대상이다(self) -> None:
        contract = shared_contract("error.subtypes.json")
        retryable = {
            subtype
            for group in contract["classes"].values()
            if group["retryable"]
            for subtype in group["subtypes"]
        }

        for err in _every_classifiable():
            subtype = classify_exception(err).subtype
            if subtype not in EMITTED_SUBTYPES:
                continue
            assert is_retryable_node_failure(err) is (subtype in retryable)
