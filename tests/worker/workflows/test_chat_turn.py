"""서버가 만든 봉투와 원장이 든 사실이 실제로 한 턴의 실행 요청이 되는지 검증한다."""

from __future__ import annotations

from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from tracer_agent.shared.workflows.chat_spec import PreparedChatExecution
from tracer_agent.worker.workflows.chat_turn import INVALID_ENVELOPE, turn_request

READ_API = "http://tracer-api:3902"

# tracer-api의 봉투 창구가 내주고 봉투 창구 클라이언트가 옮겨 놓은 그대로다.
ENVELOPE: dict[str, Any] = {
    "model": "claude-sonnet-4-6",
    "apiKey": "sk-test",
    "modelRates": {"claude-sonnet-4-6": {"input": 3, "output": 15, "cacheWrite": 3.75, "cacheRead": 0.3}},
    "limits": {"budgetUsd": 1.2, "maxTurns": 14, "maxOutputTokens": 4000},
    "deadlineMs": 600000,
    "readApiBaseUrl": READ_API,
    "scopeToken": "ms1.scope",
    "toolDescriptions": {"get_timeline": "설명"},
    "attempt": 1,
}

PREPARED = PreparedChatExecution("e1", "t1", "u1", "ko", "claude-opus-5")


def test_봉투와_원장의_사실이_한_턴의_실행_요청이_된다() -> None:
    request = turn_request(PREPARED, ENVELOPE)

    assert (request.executionId, request.threadId, request.userId) == ("e1", "t1", "u1")
    assert request.language == "ko"
    # 사용자가 고른 모델은 원장에 있으므로 봉투의 기본 모델을 덮는다.
    assert request.model == "claude-opus-5"
    assert request.apiKey == "sk-test"
    assert request.limits.maxTurns == 14
    assert request.readApiBaseUrl == READ_API
    assert request.scopeToken == "ms1.scope"
    assert request.attempt == 1
    # 이력은 봉투가 아니라 재생 API에서 오므로 여기서는 비어 있다.
    assert request.messages == []


def test_원장이_모델을_고르지_않았으면_봉투의_기본_모델로_돈다() -> None:
    request = turn_request(PreparedChatExecution("e1", "t1", "u1", "auto"), ENVELOPE)

    assert request.model == "claude-sonnet-4-6"


@pytest.mark.parametrize("missing", ["apiKey", "modelRates", "limits"])
def test_단가나_한도나_자격이_빠진_봉투는_다시_태우지_않는다(missing: str) -> None:
    partial = {key: value for key, value in ENVELOPE.items() if key != missing}

    with pytest.raises(ApplicationError) as raised:
        turn_request(PREPARED, partial)

    assert raised.value.type == INVALID_ENVELOPE
    assert raised.value.non_retryable is True


def test_계약에_없는_값을_실은_봉투는_거절한다() -> None:
    with pytest.raises(ApplicationError) as raised:
        turn_request(PREPARED, {**ENVELOPE, "unknownField": 1})

    assert raised.value.non_retryable is True
