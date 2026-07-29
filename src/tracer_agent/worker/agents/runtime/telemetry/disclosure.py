"""외부 trace로 내보낼 구조화 값의 최소 안전 경계."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

type TraceScalar = str | int | float | bool | None
type TraceValue = TraceScalar | Mapping[str, TraceValue] | Sequence[TraceValue]


class UnsafeTracePayloadError(ValueError):
    """외부 전송 payload에서 비밀일 수 있는 필드를 발견했다."""


class TraceBackend(StrEnum):
    PYTHON = "python"


class TraceSafeMetadata(BaseModel):
    """LangGraph root trace에 허용하는 비민감 실행 식별자만 소유한다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_name: str
    backend: TraceBackend = TraceBackend.PYTHON
    model_requested: str
    prompt_version: str
    prompt_content_hash: str | None = None
    tool_contract_version: str | None = None
    job_id: str | None = None
    execution_id: str | None = None
    attempt_id: str | None = None
    experiment_id: str | None = None
    example_id: str | None = None
    variant_id: str | None = None

    def langsmith_metadata(self) -> dict[str, TraceScalar]:
        """LangSmith 필터에서 backend 공통으로 쓸 이름으로 직렬화한다."""
        values: dict[str, TraceScalar] = {
            "agent_tracer.agent.name": self.agent_name,
            "agent_tracer.backend": self.backend.value,
            "agent_tracer.model.requested": self.model_requested,
            "agent_tracer.prompt.version": self.prompt_version,
            "agent_tracer.prompt.content_hash": self.prompt_content_hash,
            "agent_tracer.tool.contract.version": self.tool_contract_version,
            "agent_tracer.job.id": self.job_id,
            "agent_tracer.execution.id": self.execution_id,
            "agent_tracer.attempt.id": self.attempt_id,
            "agent_tracer.experiment.id": self.experiment_id,
            "agent_tracer.example.id": self.example_id,
            "agent_tracer.variant.id": self.variant_id,
        }
        metadata: dict[str, TraceScalar] = {key: value for key, value in values.items() if value is not None}
        assert_trace_safe(metadata)
        return metadata


_SECRET_KEY_PARTS = frozenset(
    {
        "apikey",
        "authorization",
        "callbacktoken",
        "clientsecret",
        "oauth",
        "password",
        "privatekey",
        "scopetoken",
        "secret",
        "token",
        "userid",
    }
)


def _normalized_key(key: str) -> str:
    return "".join(character for character in key.casefold() if character.isalnum())


def assert_trace_safe(value: TraceValue, *, path: str = "$") -> None:
    """중첩 payload 전체를 검사하고 의심스러운 key가 하나라도 있으면 폐기한다."""
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = _normalized_key(key)
            if any(part in normalized for part in _SECRET_KEY_PARTS):
                raise UnsafeTracePayloadError(f"trace payload contains forbidden field at {path}.{key}")
            assert_trace_safe(nested, path=f"{path}.{key}")
        return
    if isinstance(value, str | int | float | bool) or value is None:
        return
    if isinstance(value, Sequence):
        for index, nested in enumerate(value):
            assert_trace_safe(nested, path=f"{path}[{index}]")
        return
    raise UnsafeTracePayloadError(f"trace payload contains unsupported value at {path}")


def redact_trace_payload(value: TraceValue, *, path: str = "$") -> TraceValue:
    """의심스러운 key가 있으면 그 하위 값을 전부 <redacted>로 치환한다."""
    if isinstance(value, Mapping):
        redacted: dict[str, TraceValue] = {}
        for key, nested in value.items():
            normalized = _normalized_key(key)
            if any(part in normalized for part in _SECRET_KEY_PARTS):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = redact_trace_payload(nested, path=f"{path}.{key}")
        return redacted
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, Sequence):
        return [redact_trace_payload(nested, path=f"{path}[{index}]") for index, nested in enumerate(value)]
    return "<unsupported>"
