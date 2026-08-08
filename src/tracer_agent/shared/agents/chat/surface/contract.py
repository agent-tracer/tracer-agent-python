"""열린 연결이 지킬 스트림 규칙을 두 구현체가 함께 읽는 계약에서 가져온다."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType

from ...shared.contract_root import CONTRACT_ROOT

CHAT_QUERY_CASE_PATH = CONTRACT_ROOT / "conformance" / "cases" / "chat.query.json"

_MILLIS_PER_SECOND = 1000.0


@dataclass(frozen=True)
class ChatStreamRules:
    """계약이 정한 재전송 주기와 스트림 응답 헤더다."""

    resend_interval_s: float
    headers: Mapping[str, str]


@dataclass(frozen=True)
class ChatDraftRules:
    """실행기가 받은 조각을 원장에 적는 리듬이며 두 구현체가 같은 값을 읽는다."""

    interval_s: float
    edge: str


@lru_cache(maxsize=1)
def chat_stream_rules() -> ChatStreamRules:
    """계약의 밀리초 주기를 초로 바꾸고 응답 헤더를 함께 낸다."""
    declared = json.loads(CHAT_QUERY_CASE_PATH.read_text(encoding="utf-8"))["stream"]
    return ChatStreamRules(
        resend_interval_s=float(declared["resendIntervalMs"]) / _MILLIS_PER_SECOND,
        headers=MappingProxyType({str(name): str(value) for name, value in declared["headers"].items()}),
    )


@lru_cache(maxsize=1)
def chat_draft_rules() -> ChatDraftRules:
    """계약의 밀리초 간격을 초로 바꾸고 어느 엣지에서 적는지를 함께 낸다."""
    declared = json.loads(CHAT_QUERY_CASE_PATH.read_text(encoding="utf-8"))["stream"]["draft"]
    return ChatDraftRules(
        interval_s=float(declared["intervalMs"]) / _MILLIS_PER_SECOND,
        edge=str(declared["edge"]),
    )
