"""GenAI 스팬과 메트릭의 표준 속성을 구성한다."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from tracer_agent.shared.agents.shared.axis import agent_axis, axis_attribute_key
from tracer_agent.shared.agents.shared.job_kinds import JOB_KIND_BY_AGENT_NAME
from tracer_agent.shared.agents.shared.models import UsageDTO

JOB_ID_ATTRIBUTE = "agent_tracer.job.id"
TOOL_PARAMETERS_FINGERPRINT_ATTRIBUTE = "agent_tracer.tool.parameters.fingerprint"
JOB_KIND_ATTRIBUTE = "agent_tracer.job.kind"
BACKEND_ATTRIBUTE = axis_attribute_key()
INPUT_TOKENS_ATTRIBUTE = "gen_ai.usage.input_tokens"
OUTPUT_TOKENS_ATTRIBUTE = "gen_ai.usage.output_tokens"
CACHE_READ_INPUT_TOKENS_ATTRIBUTE = "gen_ai.usage.cache_read.input_tokens"
CACHE_CREATION_INPUT_TOKENS_ATTRIBUTE = "gen_ai.usage.cache_creation.input_tokens"
BILLABLE_BASE_INPUT_TOKENS_ATTRIBUTE = "agent_tracer.usage.billable_base_input_tokens"

AGENT_JOB_KIND = JOB_KIND_BY_AGENT_NAME
GEN_AI_OPERATION = {
    "invoke_agent": "invoke_agent",
    "chat": "chat",
    "execute_tool": "execute_tool",
}
GEN_AI_PROVIDER = "anthropic"


def build_invoke_agent_attributes(
    *, job_id: str | None, agent_name: str, model: str | None
) -> dict[str, str]:
    """에이전트 호출 스팬의 표준 속성을 만든다."""
    attrs = {
        JOB_ID_ATTRIBUTE: job_id,
        "gen_ai.operation.name": GEN_AI_OPERATION["invoke_agent"],
        "gen_ai.provider.name": GEN_AI_PROVIDER,
        "gen_ai.agent.name": agent_name,
        "gen_ai.request.model": model,
        JOB_KIND_ATTRIBUTE: AGENT_JOB_KIND.get(agent_name),
        BACKEND_ATTRIBUTE: agent_axis(),
    }
    return {key: value for key, value in attrs.items() if value is not None}


def build_usage_attributes(usage: UsageDTO | None) -> dict[str, int]:
    """사용량을 스팬 속성으로 변환한다."""
    if usage is None:
        return {}
    return {
        INPUT_TOKENS_ATTRIBUTE: usage.inputTokens + usage.cacheReadTokens + usage.cacheCreationTokens,
        OUTPUT_TOKENS_ATTRIBUTE: usage.outputTokens,
        CACHE_READ_INPUT_TOKENS_ATTRIBUTE: usage.cacheReadTokens,
        CACHE_CREATION_INPUT_TOKENS_ATTRIBUTE: usage.cacheCreationTokens,
        BILLABLE_BASE_INPUT_TOKENS_ATTRIBUTE: usage.inputTokens,
    }


def apply_usage_attributes(span: Any, usage: UsageDTO | None) -> None:
    """사용량 속성을 주어진 스팬에 기록한다."""
    for key, value in build_usage_attributes(usage).items():
        span.set_attribute(key, value)


def build_client_attributes(
    model: str,
    response_model: str | None = None,
    error_subtype: str | None = None,
) -> dict[str, str | int]:
    """모델 호출 메트릭의 저카디널리티 속성을 만들며 답한 모델을 모르면 요청한 모델로 적는다."""
    attrs: dict[str, str | int | None] = {
        "gen_ai.operation.name": GEN_AI_OPERATION["chat"],
        "gen_ai.provider.name": GEN_AI_PROVIDER,
        "gen_ai.request.model": model,
        "gen_ai.response.model": response_model or model,
        "error.type": error_subtype,
    }
    return {key: value for key, value in attrs.items() if value is not None}


def build_tool_attributes(tool_name: str, agent_name: str) -> dict[str, str]:
    """도구 실행 메트릭의 표준 속성을 만든다."""
    return {
        "gen_ai.operation.name": GEN_AI_OPERATION["execute_tool"],
        "gen_ai.tool.name": tool_name,
        "gen_ai.tool.type": "datastore",
        "gen_ai.agent.name": agent_name,
    }


def build_tool_span_attributes(
    tool_name: str,
    agent_name: str,
    *,
    parameters: object | None = None,
) -> dict[str, str]:
    """도구 실행 스팬에 파라미터 지문을 더한 속성을 만든다."""
    attrs = build_tool_attributes(tool_name, agent_name)
    fingerprint = tool_parameter_fingerprint(parameters)
    if fingerprint is not None:
        attrs[TOOL_PARAMETERS_FINGERPRINT_ATTRIBUTE] = fingerprint
    return attrs


def tool_parameter_fingerprint(parameters: object | None) -> str | None:
    """도구 파라미터를 노출하지 않는 안정적인 지문으로 바꾼다."""
    if parameters is None:
        return None
    payload = json.dumps(
        _normalize_parameter(parameters),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def token_measurements(usage: UsageDTO | None) -> list[tuple[int, dict[str, str]]]:
    """사용량을 입력·출력 토큰 측정값으로 변환한다."""
    if usage is None:
        return []
    return [
        (usage.inputTokens, {"gen_ai.token.type": "input"}),
        (usage.outputTokens, {"gen_ai.token.type": "output"}),
    ]


def _normalize_parameter(value: Any) -> object:
    if isinstance(value, Mapping):
        return {str(key): _normalize_parameter(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, list | tuple):
        return [_normalize_parameter(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)
