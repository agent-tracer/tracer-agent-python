"""외부 trace 창구로 내보내는 구조화 값이 계약의 trace 자리를 지나게 한다."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from tracer_agent.shared.agents.shared.axis import AGENT_BACKEND, AXIS_ATTRIBUTE_KEY, AgentAxis
from tracer_agent.shared.agents.shared.redaction import (
    RedactableScalar,
    RedactionStage,
    SuspectPayloadError,
    redact,
)


class TraceSafeMetadata(BaseModel):
    """LangGraph root trace에 허용하는 비민감 실행 식별자만 소유한다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_name: str
    backend: AgentAxis = AGENT_BACKEND
    model_requested: str
    prompt_version: str
    tool_contract_version: str | None = None
    job_id: str | None = None
    execution_id: str | None = None
    attempt_id: str | None = None

    def langsmith_metadata(self) -> dict[str, RedactableScalar]:
        """LangSmith 필터에서 backend 공통으로 쓸 이름으로 직렬화한다."""
        values: dict[str, RedactableScalar] = {
            "agent_tracer.agent.name": self.agent_name,
            AXIS_ATTRIBUTE_KEY: self.backend,
            "agent_tracer.model.requested": self.model_requested,
            "agent_tracer.prompt.version": self.prompt_version,
            "agent_tracer.tool.contract.version": self.tool_contract_version,
            "agent_tracer.job.id": self.job_id,
            "agent_tracer.execution.id": self.execution_id,
            "agent_tracer.attempt.id": self.attempt_id,
        }
        metadata: dict[str, RedactableScalar] = {
            key: value for key, value in values.items() if value is not None
        }
        redact(metadata, stage=RedactionStage.TRACE)
        return metadata


def disclosable_run_payload(payload: dict[Any, Any]) -> dict[Any, Any]:
    """추적 창구로 내보낼 실행 payload 를 내고 걸리는 자리가 있으면 아무것도 내지 않는다."""
    try:
        redact(payload, stage=RedactionStage.TRACE)
    except SuspectPayloadError:
        return {}
    return payload
