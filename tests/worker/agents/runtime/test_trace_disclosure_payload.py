from __future__ import annotations

from typing import Any, ClassVar

import pytest

from tracer_agent.worker.agents.runtime.llm.structured_agent import recursion_config
from tracer_agent.worker.agents.runtime.telemetry.disclosure import TraceBackend, TraceSafeMetadata


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
        backend=TraceBackend.PYTHON,
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


def test_공개_프로파일에서도_API_key_OAuth_callback_scope_token과_raw_userId를_가린다(
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

    for redacted in (hide_inputs(secret_payload), hide_outputs(secret_payload)):
        serialized = str(redacted)
        assert "sk-ant-live-should-not-leak" not in serialized
        assert "lsv2_should-not-leak" not in serialized
        assert "callback-should-not-leak" not in serialized
        assert "scope-should-not-leak" not in serialized
        assert "user-should-not-leak" not in serialized
        assert redacted["prompt"] == "평범한 프롬프트 본문"
