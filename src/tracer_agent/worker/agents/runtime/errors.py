"""에이전트 실행 오류를 워커가 재시도 분류에 쓰는 errorSubtype으로 정규화한다."""

from __future__ import annotations

import re
from typing import Final

from anthropic import APIConnectionError, APIError, APIStatusError
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langgraph.errors import NodeTimeoutError

from tracer_agent.shared.agents.shared.models import AgentErrorDTO

_SECRET_PATTERN = re.compile(r"(?:sk-ant-|lsv2_|bearer\s+|oauth|api[_-]?key)", re.IGNORECASE)


def _redact_error_message(msg: str) -> str:
    if _SECRET_PATTERN.search(msg):
        return "<redacted>"
    return msg


def _redact_exception(err: BaseException) -> BaseException:
    # APIStatusError 등 provider exception의 body/headers를 지운다.
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


class DeadlineExceeded(Exception):
    """Temporal의 startToCloseTimeout보다 안쪽에서 먼저 끊는 벽시계 데드라인 초과다."""


class BudgetExceeded(Exception):
    """쿼리당 USD 상한 초과이며 재시도해도 예산만 더 태우므로 비재시도다."""


class OutputTruncated(Exception):
    """max_tokens에서 잘려 구조화 출력을 완성하지 못했고 같은 입력이면 재시도해도 다시 잘린다."""


def _status_subtype(status: int | None) -> str:
    # 요청 자체가 틀린 4xx는 같은 요청으로 다시 보내도 같은 응답이 오므로 비재시도 어휘로 접는다.
    if status is not None and 400 <= status < 500 and status not in (408, 429):
        return INVALID_REQUEST_ERROR
    return API_ERROR


def _anthropic_subtype(err: APIStatusError) -> str:
    body = getattr(err, "body", None)
    if isinstance(body, dict):
        inner = body.get("error")
        if isinstance(inner, dict):
            kind = inner.get("type")
            if isinstance(kind, str) and kind:
                return kind
    return _status_subtype(getattr(err, "status_code", None))


def classify_exception(err: BaseException) -> AgentErrorDTO:
    """예외를 두 백엔드가 공유하는 오류 서브타입 어휘로 옮긴다."""
    if isinstance(err, DeadlineExceeded):
        summary = _redact_error_message(str(err) or "agent deadline exceeded")
        return AgentErrorDTO(subtype=DEADLINE_EXCEEDED, summary=summary)
    if isinstance(err, BudgetExceeded):
        return AgentErrorDTO(subtype=BUDGET_EXCEEDED, summary=_redact_error_message(str(err)))
    if isinstance(err, OutputTruncated):
        return AgentErrorDTO(subtype=MAX_TOKENS, summary=_redact_error_message(str(err)))
    # 도구 예산을 다 쓴 실행은 같은 예산으로 재시도해도 같은 자리에서 끝나므로 비재시도로 넘긴다.
    if isinstance(err, ModelCallLimitExceededError):
        return AgentErrorDTO(subtype=MAX_TURNS_EXCEEDED, summary=_redact_error_message(str(err)))
    # 노드 하나의 벽시계 초과이며, Temporal 경계에서 잡 전체가 걸리는 DeadlineExceeded와는 다른 자리다.
    if isinstance(err, NodeTimeoutError):
        return AgentErrorDTO(subtype=MAX_TURNS_EXCEEDED, summary=_redact_error_message(str(err)))
    # Anthropic SDK에서 APIConnectionError는 APIError의 서브클래스다.
    if isinstance(err, APIConnectionError):
        return AgentErrorDTO(subtype=CONNECTION_ERROR, summary=_redact_error_message(str(err)))
    if isinstance(err, APIStatusError):
        return AgentErrorDTO(subtype=_anthropic_subtype(err), summary=_redact_error_message(str(err)))
    if isinstance(err, APIError):
        return AgentErrorDTO(subtype=API_ERROR, summary=_redact_error_message(str(err)))
    summary = _redact_error_message(str(err)) or type(err).__name__
    return AgentErrorDTO(subtype=AGENT_EXECUTION_ERROR, summary=summary)


_RETRYABLE_SUBTYPES: Final = frozenset({API_ERROR, CONNECTION_ERROR, "rate_limit_error", "overloaded_error"})


def is_retryable_node_failure(err: Exception) -> bool:
    """공급자가 일시적으로 응답하지 못한 실패만 그래프 노드 재시도 대상으로 본다."""
    return classify_exception(err).subtype in _RETRYABLE_SUBTYPES
