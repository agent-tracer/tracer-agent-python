"""LangGraph agent의 넓은 실행 결과를 구조화 응답 계약으로 좁힌다."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, TypedDict
from uuid import UUID, uuid5

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langchain_core.tracers.langchain import LangChainTracer
from langgraph.graph.state import CompiledStateGraph
from langsmith.client import Client
from pydantic import BaseModel

from tracer_agent.shared.agents.shared.env import env_flag

from ..telemetry.disclosure import TraceSafeMetadata, disclosable_run_payload
from .middleware_stack import AgentMiddlewareStack
from .standard_agent import StandardAgentContext

_LANGSMITH_RUN_NAMESPACE = UUID("90dd2ae3-e1b4-43bc-9538-f70898c147bd")


@dataclass(frozen=True)
class StructuredAgentResult[Response: BaseModel]:
    """검증을 마친 구조화 응답과 다음 호출에 이어갈 메시지와 이 호출이 실제로 그은 턴이다."""

    response: Response
    messages: list[BaseMessage]
    num_turns: int


def build_structured_agent(
    chat: BaseChatModel,
    system_prompt: str,
    tools: list[BaseTool],
    transient_errors: tuple[type[Exception], ...],
    *,
    output: type[BaseModel],
    context_schema: type[Any],
    name: str,
    max_turns: int,
    fallback_chat: BaseChatModel | None = None,
    serializes_tools: bool = False,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """표준 도구 실행과 구조화 출력을 갖춘 잡 에이전트를 공통 미들웨어 순서로 컴파일한다."""
    middleware = AgentMiddlewareStack(
        max_turns=max_turns,
        transient_errors=transient_errors,
        fallback_chat=fallback_chat,
        repairs_structured_output=True,
        serializes_tools=serializes_tools,
    ).build()
    return create_agent(
        chat,
        tools=list(tools),
        system_prompt=SystemMessage(content=system_prompt),
        middleware=middleware,
        response_format=_output_tool(output),
        context_schema=context_schema,
        name=name,
    )


# 출력 타입을 그대로 넘기면 공급자 강제로 넘어가 첫 응답이 곧 산출이 되고 조사 도구를 부를 턴이 남지 않는다.
def _output_tool(output: type[BaseModel]) -> ToolStrategy[Any]:
    """산출을 도구 하나로 선언해 모델이 조사 도구를 먼저 부른 뒤 산출을 낼 수 있게 한다."""
    # 스키마를 어긴 산출은 SDK가 대신 처리하지 않고 이 저장소의 다시 받는 층과 장부를 지나야 한다.
    return ToolStrategy(output, handle_errors=False)


# 한 턴이 langchain agent의 여러 슈퍼스텝을 거치므로 재귀 한도는 예산이 아니라 폭주만 끊는 상한이다.
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

    if env_flag("LANGSMITH_TRACING"):
        # 공개 프로파일도 계약의 trace 자리를 지나고, 그 외 프로파일은 원문 자체를 보내지 않는다.
        discloses_payloads = not env_flag("LANGSMITH_HIDE_INPUTS", default=True)
        project = os.environ.get("LANGSMITH_PROJECT", "default")
        config["callbacks"] = [_tracer(discloses_payloads=discloses_payloads, project=project)]

    return config


@lru_cache(maxsize=4)
def _tracer(*, discloses_payloads: bool, project: str) -> LangChainTracer:
    """추적 창구로 나가는 연결을 프로파일마다 하나만 열어 실행마다 다시 열지 않는다."""
    client = Client(
        hide_inputs=disclosable_run_payload if discloses_payloads else True,
        hide_outputs=disclosable_run_payload if discloses_payloads else True,
    )
    return LangChainTracer(project_name=project, client=client)


def _call_config(recursion_limit: int, call_id: str | None) -> RunnableConfig:
    """부모 그래프가 실행 상태를 보존하면 이 호출도 자기 열쇠를 갖는다."""
    config = recursion_config(recursion_limit)
    if call_id is None:
        return config
    return {**config, "configurable": {"thread_id": call_id}}


class StructuredAgentOutput[Response: BaseModel](TypedDict):
    """response_format을 준 agent가 내는 출력이며 이 모듈만 그 모양을 안다."""

    messages: list[BaseMessage]
    structured_response: Response


def narrow_agent_output[Response: BaseModel](
    raw: object, response_type: type[Response], missing_response: str
) -> StructuredAgentOutput[Response]:
    """SDK의 넓은 출력을 이 호출이 요구한 응답 계약으로 좁힌다."""
    if not isinstance(raw, dict):
        raise ValueError("agent produced a non-object output")
    response = raw.get("structured_response")
    if not isinstance(response, response_type):
        raise ValueError(missing_response)
    messages = raw.get("messages")
    if not isinstance(messages, list):
        raise ValueError("agent output contains no message history")
    return {"messages": messages, "structured_response": response}


async def invoke_structured_agent[Response: BaseModel](
    agent: CompiledStateGraph[Any, Any, Any, Any],
    *,
    messages: list[BaseMessage],
    context: StandardAgentContext,
    response_type: type[Response],
    recursion_limit: int,
    missing_response: str,
    call_id: str | None = None,
) -> StructuredAgentResult[Response]:
    """agent를 실행하고 SDK의 가변 출력에서 요구한 Pydantic 응답만 꺼낸다."""
    output = narrow_agent_output(
        await agent.ainvoke(
            {"messages": messages},
            context=context,
            config=_call_config(recursion_limit, call_id),
        ),
        response_type,
        missing_response,
    )
    # 넘긴 메시지 뒤에 새로 붙은 AIMessage 수가 이 호출이 실제로 그은 모델 턴이다.
    new_messages = output["messages"][len(messages) :]
    return StructuredAgentResult(
        response=output["structured_response"],
        messages=output["messages"],
        num_turns=sum(1 for message in new_messages if isinstance(message, AIMessage)),
    )
