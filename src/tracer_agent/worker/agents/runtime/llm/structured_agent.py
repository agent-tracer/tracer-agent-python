"""LangGraph agent의 넓은 실행 결과를 구조화 응답 계약으로 좁힌다."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from langchain_core.runnables import RunnableConfig
from langchain_core.tracers.langchain import LangChainTracer
from langgraph.graph.state import CompiledStateGraph
from langsmith.client import Client
from pydantic import BaseModel

from ..telemetry.disclosure import TraceSafeMetadata, redact_trace_payload

_LANGSMITH_RUN_NAMESPACE = UUID("90dd2ae3-e1b4-43bc-9538-f70898c147bd")


def _redact_run_payload(payload: dict[Any, Any]) -> dict[Any, Any]:
    """langsmith Client의 dict-in/dict-out 콜백 서명에 맞춰 공용 redaction으로 위임한다."""
    redacted = redact_trace_payload(payload)
    assert isinstance(redacted, dict)
    return redacted


@dataclass(frozen=True)
class StructuredAgentResult[Response: BaseModel]:
    """검증을 마친 구조화 응답과 다음 호출에 이어갈 메시지다."""

    response: Response
    messages: list[Any]


# 한 턴이 langchain agent의 여러 슈퍼스텝을 돌므로 재귀 한도는 예산이 아니라 폭주만 끊는 그물이다.
def recursion_limit_for(max_turns: int) -> int:
    """모델 턴 상한에서 그래프 재귀 상한을 유도한다."""
    return 10 * max_turns


def recursion_config(limit: int, trace: TraceSafeMetadata | None = None) -> RunnableConfig:
    """LangGraph 재귀 상한을 정식 실행 설정 타입으로 만든다."""
    config: RunnableConfig = {"recursion_limit": limit}
    if trace is None:
        return config

    config["run_name"] = trace.agent_name
    config["tags"] = [trace.agent_name, trace.backend, trace.prompt_version]
    config["metadata"] = trace.langsmith_metadata()
    logical_execution = trace.execution_id or trace.job_id
    if logical_execution is not None and trace.attempt_id is not None:
        stable_identity = f"{trace.agent_name}:{logical_execution}:{trace.attempt_id}"
        config["run_id"] = uuid5(_LANGSMITH_RUN_NAMESPACE, stable_identity)

    if os.environ.get("LANGSMITH_TRACING") == "true":
        # 공개 프로파일도 비밀 패턴은 계속 지우고, 그 외 프로파일은 원문 자체를 보내지 않는다.
        discloses_payloads = os.environ.get("LANGSMITH_HIDE_INPUTS") == "false"
        client = Client(
            hide_inputs=_redact_run_payload if discloses_payloads else True,
            hide_outputs=_redact_run_payload if discloses_payloads else True,
        )
        tracer = LangChainTracer(
            project_name=os.environ.get("LANGSMITH_PROJECT", "default"),
            client=client,
        )
        config["callbacks"] = [tracer]

    return config


async def invoke_structured_agent[Response: BaseModel](
    agent: CompiledStateGraph[Any, Any, Any, Any],
    *,
    messages: list[Any],
    context: Any,
    response_type: type[Response],
    recursion_limit: int,
    missing_response: str,
) -> StructuredAgentResult[Response]:
    """agent를 실행하고 SDK의 가변 출력에서 요구한 Pydantic 응답만 꺼낸다."""
    raw_output: object = await agent.ainvoke(
        {"messages": messages},
        context=context,
        config=recursion_config(recursion_limit),
    )
    if not isinstance(raw_output, dict):
        raise ValueError("agent produced a non-object output")

    output = raw_output
    response = output.get("structured_response")
    if not isinstance(response, response_type):
        raise ValueError(missing_response)

    raw_messages = output.get("messages")
    if not isinstance(raw_messages, list):
        raise ValueError("agent output contains no message history")
    return StructuredAgentResult(response=response, messages=raw_messages)
