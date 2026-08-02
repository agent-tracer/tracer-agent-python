from __future__ import annotations

from typing import Any, ClassVar

import pytest

from tracer_agent.shared.agents.shared.axis import AGENT_BACKEND
from tracer_agent.worker.agents.runtime.llm.structured_agent import recursion_config
from tracer_agent.worker.agents.runtime.telemetry.disclosure import TraceSafeMetadata


class _CapturingClient:
    captured: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, **kwargs: Any) -> None:
        _CapturingClient.captured.append(kwargs)


class _NoopTracer:
    def __init__(self, **kwargs: Any) -> None:
        pass


def _trace() -> TraceSafeMetadata:
    return TraceSafeMetadata(
        agent_name="test_agent",
        backend=AGENT_BACKEND,
        model_requested="claude-3-5-sonnet",
        prompt_version="v1.0",
    )


@pytest.fixture(autouse=True)
def _capture_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _CapturingClient.captured = []
    monkeypatch.setattr("tracer_agent.worker.agents.runtime.llm.structured_agent.Client", _CapturingClient)
    monkeypatch.setattr(
        "tracer_agent.worker.agents.runtime.llm.structured_agent.LangChainTracer", _NoopTracer
    )
    monkeypatch.setenv("LANGSMITH_TRACING", "true")


def test_비공개_프로파일은_hide_inputs_hide_outputs로_원문_전체를_막는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGSMITH_HIDE_INPUTS", raising=False)

    recursion_config(5, _trace())

    assert _CapturingClient.captured[0]["hide_inputs"] is True
    assert _CapturingClient.captured[0]["hide_outputs"] is True


def test_공개_프로파일도_API_key_OAuth_callback_scope_token과_raw_userId가_담긴_실행을_버린다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_HIDE_INPUTS", "false")

    recursion_config(5, _trace())

    hide_inputs = _CapturingClient.captured[0]["hide_inputs"]
    hide_outputs = _CapturingClient.captured[0]["hide_outputs"]
    assert callable(hide_inputs)
    assert callable(hide_outputs)

    secret_payload = {
        "apiKey": "sk-ant-live-should-not-leak",
        "authorization": "Bearer lsv2_should-not-leak",
        "callbackToken": "callback-should-not-leak",
        "scopeToken": "scope-should-not-leak",
        "userId": "user-should-not-leak",
        "prompt": "평범한 프롬프트 본문",
    }

    for disclosed in (hide_inputs(secret_payload), hide_outputs(secret_payload)):
        assert disclosed == {}


def test_공개_프로파일은_걸릴_것이_없는_실행을_그대로_내보낸다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_HIDE_INPUTS", "false")

    recursion_config(5, _trace())

    hide_inputs = _CapturingClient.captured[0]["hide_inputs"]
    plain_payload = {"prompt": "평범한 프롬프트 본문", "language": "ko"}

    assert hide_inputs(plain_payload) == plain_payload
