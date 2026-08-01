"""브라우저가 백엔드마다 분기하지 않도록 접수 표면이 계약과 같은지 대조한다."""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from tests.support.contract import (
    PINNED_VERSION,
    conformance_case,
    contract_version,
    shared_contract,
    wire_contract,
)
from tracer_agent.shared.agents.chat.intake.models import PostMessagePayload
from tracer_agent.shared.agents.chat.intake.router import (
    ACCEPTED_STATUS,
    CHAT_CANCEL_PATH,
    CHAT_MESSAGES_PATH,
    DEFAULT_USER_ID,
    MONITOR_USER_HEADER,
    error_envelope,
)
from tracer_agent.shared.agents.chat.intake.turn import IDEMPOTENCY_CONFLICT, THREAD_NOT_FOUND


def _자리표시자를_구현체_표기로_바꾼다(path: str) -> str:
    """계약이 쓰는 lowerCamelCase 자리표시자를 구현체의 snake_case 표기로 정규화한다."""
    return path.replace("{threadId}", "{thread_id}").replace("{executionId}", "{execution_id}")


def test_계약_판이_고정한_값과_같다() -> None:
    assert contract_version() == PINNED_VERSION


def test_접수_경로가_계약과_같다() -> None:
    case = conformance_case("chat.intake")

    assert _자리표시자를_구현체_표기로_바꾼다(case["path"]) == CHAT_MESSAGES_PATH
    assert _자리표시자를_구현체_표기로_바꾼다(case["cancelPath"]) == CHAT_CANCEL_PATH
    assert case["acceptedStatus"] == ACCEPTED_STATUS


def test_자기신고_사용자_헤더가_계약과_같다() -> None:
    header = conformance_case("chat.intake")["userHeader"]

    assert header["name"] == MONITOR_USER_HEADER
    assert header["defaultValue"] == DEFAULT_USER_ID


def test_요청_스키마의_제약이_계약과_같다() -> None:
    case = conformance_case("chat.intake")
    constraints = case["body"]["constraints"]

    assert set(PostMessagePayload.model_fields) == set(case["body"]["fields"])

    for field_name in ("clientRequestId", "content"):
        rule = constraints[field_name]
        body = {"clientRequestId": "r1", "content": "안녕"}
        body[field_name] = "a" * rule["maxLength"]
        assert PostMessagePayload.model_validate(body)

        body[field_name] = "a" * (rule["maxLength"] + 1)
        with pytest.raises(ValidationError):
            PostMessagePayload.model_validate(body)

        body[field_name] = "   "
        with pytest.raises(ValidationError):
            PostMessagePayload.model_validate(body)

    model_rule = constraints["model"]
    accepted = PostMessagePayload.model_validate(
        {"clientRequestId": "r1", "content": "안녕", "model": "a" * model_rule["minLength"]}
    )
    assert accepted.model == "a" * model_rule["minLength"]
    with pytest.raises(ValidationError):
        PostMessagePayload.model_validate({"clientRequestId": "r1", "content": "안녕", "model": "   "})


def test_언어_선택지가_계약과_같다() -> None:
    constraints = conformance_case("chat.intake")["body"]["constraints"]
    languages = shared_contract("languages.json")["languages"]

    assert constraints["language"]["enum"] == languages

    for language in languages:
        accepted = PostMessagePayload.model_validate(
            {"clientRequestId": "r1", "content": "안녕", "language": language}
        )
        assert accepted.language == language

    with pytest.raises(ValidationError):
        PostMessagePayload.model_validate({"clientRequestId": "r1", "content": "안녕", "language": "fr"})


def test_접수_본문_케이스가_계약과_같은_판정을_받는다() -> None:
    cases = conformance_case("chat.intake")["body"]["cases"]

    for case in cases:
        if case["accepted"]:
            assert PostMessagePayload.model_validate(case["body"])
        else:
            with pytest.raises(ValidationError):
                PostMessagePayload.model_validate(case["body"])


def test_멱등_해시가_계약과_같은_바이트를_먹는다() -> None:
    idempotency = conformance_case("chat.intake")["idempotency"]

    assert idempotency["digest"] == "sha256"
    for spec in idempotency["cases"]:
        payload = PostMessagePayload.model_validate(spec["body"])

        assert payload.input_hash() == hashlib.sha256(spec["canonical"].encode()).hexdigest()
        assert payload.input_hash() == spec["hash"]


def test_거절_사유가_계약과_같은_상태와_코드를_쓴다() -> None:
    rejections = {rejection["code"]: rejection for rejection in conformance_case("chat.intake")["rejections"]}

    not_found = rejections["not_found"]
    assert (not_found["status"], not_found["code"], not_found["message"]) == THREAD_NOT_FOUND

    conflict = rejections["chat.execution-idempotency-conflict"]
    assert (conflict["status"], conflict["code"], conflict["message"]) == IDEMPOTENCY_CONFLICT


def test_응답_봉투가_계약과_같다() -> None:
    envelope = wire_contract("envelope.json")

    success_fields = envelope["success"]["fields"]
    assert success_fields["ok"]["const"] is True
    assert "data" in success_fields

    error_fields = envelope["error"]["fields"]
    body = json.loads(error_envelope(*THREAD_NOT_FOUND).body)

    assert body["ok"] is error_fields["ok"]["const"]
    assert isinstance(body["error"]["code"], str)
    assert isinstance(body["error"]["message"], str)
