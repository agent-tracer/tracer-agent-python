"""에이전트 실행 오류를 워커가 재시도 분류에 쓰는 errorSubtype으로 정규화한다."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, ClassVar, Final

from anthropic import APIConnectionError, APIError, APIStatusError
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langgraph.errors import NodeTimeoutError

from tracer_agent.shared.agents.shared.contract_root import CONTRACT_ROOT
from tracer_agent.shared.agents.shared.models import AgentErrorDTO
from tracer_agent.shared.agents.shared.redaction import RedactionStage, redact_text

_ERROR_SUBTYPES_PATH = CONTRACT_ROOT / "agent" / "shared" / "error.subtypes.json"


@lru_cache(maxsize=1)
def _declared_subtypes() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(_ERROR_SUBTYPES_PATH.read_text(encoding="utf-8"))
    return document


@lru_cache(maxsize=1)
def _provider_subtypes() -> frozenset[str]:
    """계약이 공급자 절에 적은 어휘이며 분류기는 이 안의 이름만 그대로 쓴다."""
    return frozenset(_declared_subtypes()["provider"])


@lru_cache(maxsize=1)
def _retryable_subtypes() -> frozenset[str]:
    """계약이 재시도 가능으로 적은 어휘를 상위 분류와 공급자 절 양쪽에서 모은다."""
    document = _declared_subtypes()
    classed = {
        subtype
        for group in document["classes"].values()
        if group["retryable"]
        for subtype in group["subtypes"]
    }
    provider = {name for name, rule in document["provider"].items() if rule["retryable"]}
    return frozenset(classed | provider)


def _redact_error_message(msg: str) -> str:
    """사유는 사용자와 원장이 함께 읽으므로 자격의 이름은 남기고 자격의 몸통만 가린다."""
    return redact_text(msg, stage=RedactionStage.OUTPUT)


def redact_exception(err: BaseException) -> BaseException:
    """공급자 예외가 든 본문과 헤더를 지워 스팬에 자격이 새지 않게 한다."""
    if hasattr(err, "body") and err.body:
        err.body = "<redacted>"
    if hasattr(err, "request") and hasattr(getattr(err, "request", None), "headers"):
        err.request.headers = {}
    return err


DEADLINE_EXCEEDED: Final = "deadline_exceeded"
BUDGET_EXCEEDED: Final = "budget_exceeded"
MAX_TOKENS: Final = "max_tokens"
MAX_TURNS_EXCEEDED: Final = "max_turns_exceeded"
CONNECTION_ERROR: Final = "connection_error"
API_ERROR: Final = "api_error"
INVALID_REQUEST_ERROR: Final = "invalid_request_error"
AGENT_EXECUTION_ERROR: Final = "agent_execution_error"
CANCELLED: Final = "cancelled"

# 계약의 error.subtypes.json이 이 어휘의 재시도 판정을 소유하고 계약 테스트가 둘을 대조한다.
EMITTED_SUBTYPES: Final = frozenset(
    {
        DEADLINE_EXCEEDED,
        BUDGET_EXCEEDED,
        MAX_TOKENS,
        MAX_TURNS_EXCEEDED,
        CONNECTION_ERROR,
        API_ERROR,
        INVALID_REQUEST_ERROR,
        AGENT_EXECUTION_ERROR,
        CANCELLED,
    }
)


class AgentExecutionError(Exception):
    """이 서비스가 스스로 끊는 실행 실패이며 자기 서브타입과 기본 요약을 스스로 든다."""

    subtype: ClassVar[str] = AGENT_EXECUTION_ERROR
    default_summary: ClassVar[str] = ""


class DeadlineExceeded(AgentExecutionError):
    """Temporal의 startToCloseTimeout보다 안쪽에서 먼저 끊는 벽시계 데드라인 초과다."""

    subtype = DEADLINE_EXCEEDED
    default_summary = "agent deadline exceeded"


class BudgetExceeded(AgentExecutionError):
    """쿼리당 USD 상한 초과이며 재시도해도 예산만 더 쓰므로 비재시도다."""

    subtype = BUDGET_EXCEEDED


class OutputTruncated(AgentExecutionError):
    """max_tokens에서 잘려 구조화 출력을 완성하지 못했고 같은 입력이면 재시도해도 다시 잘린다."""

    subtype = MAX_TOKENS


def _status_subtype(status: int | None) -> str:
    # 요청 자체가 틀린 4xx는 같은 요청으로 다시 보내도 같은 응답이 오므로 비재시도 어휘로 줄인다.
    if status is not None and 400 <= status < 500 and status not in (408, 429):
        return INVALID_REQUEST_ERROR
    return API_ERROR


# 계약이 이름 짓지 않은 공급자 종류를 그대로 내보내면 조회 쪽이 해석하지 못하는 어휘가 원장에 남는다.
def _anthropic_subtype(err: APIStatusError) -> str:
    body = getattr(err, "body", None)
    if isinstance(body, dict):
        inner = body.get("error")
        if isinstance(inner, dict):
            kind = inner.get("type")
            if isinstance(kind, str) and kind in _provider_subtypes():
                return kind
    return _status_subtype(getattr(err, "status_code", None))


def classify_exception(err: BaseException) -> AgentErrorDTO:
    """예외를 두 백엔드가 공유하는 오류 서브타입 어휘로 옮긴다."""
    # 자기 예외는 스스로 서브타입을 들므로 새 예외를 더해도 이 분기를 늘리지 않는다.
    if isinstance(err, AgentExecutionError):
        summary = _redact_error_message(str(err) or err.default_summary)
        return AgentErrorDTO(subtype=err.subtype, summary=summary)
    # 도구 예산을 다 쓴 실행은 같은 예산으로 재시도해도 같은 자리에서 끝나므로 비재시도로 넘긴다.
    if isinstance(err, ModelCallLimitExceededError):
        return AgentErrorDTO(subtype=MAX_TURNS_EXCEEDED, summary=_redact_error_message(str(err)))
    # 노드 하나의 벽시계 초과이며, Temporal 경계에서 잡 전체가 걸리는 DeadlineExceeded와는 다른 자리다.
    if isinstance(err, NodeTimeoutError):
        return AgentErrorDTO(subtype=MAX_TURNS_EXCEEDED, summary=_redact_error_message(str(err)))
    if isinstance(err, APIConnectionError):
        return AgentErrorDTO(subtype=CONNECTION_ERROR, summary=_redact_error_message(str(err)))
    if isinstance(err, APIStatusError):
        return AgentErrorDTO(subtype=_anthropic_subtype(err), summary=_redact_error_message(str(err)))
    if isinstance(err, APIError):
        return AgentErrorDTO(subtype=API_ERROR, summary=_redact_error_message(str(err)))
    summary = _redact_error_message(str(err)) or type(err).__name__
    return AgentErrorDTO(subtype=AGENT_EXECUTION_ERROR, summary=summary)


def exception_summary(err: BaseException) -> str:
    """예외가 남긴 한 줄이며 본문이 비면 예외의 이름을 대신 쓴다."""
    return str(err).strip() or type(err).__name__


def is_retryable_node_failure(err: Exception) -> bool:
    """공급자가 일시적으로 응답하지 못한 실패만 그래프 노드 재시도 대상으로 본다."""
    return classify_exception(err).subtype in _retryable_subtypes()
