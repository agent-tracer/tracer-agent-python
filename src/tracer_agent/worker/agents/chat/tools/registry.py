"""계약이 선언한 chat 도구를 실행 기계가 쓰는 도구 클래스와 레지스트리로 세운다."""

from __future__ import annotations

import json
from abc import ABC
from collections.abc import Mapping
from functools import lru_cache
from typing import Any, cast

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.runtime import get_runtime
from pydantic import BaseModel, ValidationError

from tracer_agent.shared.agents.chat.models import ProposedWrite
from tracer_agent.shared.agents.chat.tools.surface import chat_tool_failure_text, recall_tool_name
from tracer_agent.shared.agents.shared.json_view import JsonObject
from tracer_agent.shared.agents.shared.redaction import RedactionStage, redact, redact_text

from ...runtime.tooling import AgentTool, ToolRegistry
from ...shared.contract_failures import TOOL_FAILED_KEY
from ..store import FACTS_FIELD
from .context import ChatToolContext
from .specs import AGENT_READ_TOOL_NAMES, ARGS_MODELS, MEMORY_TOOL_NAMES, READ_TOOL_NAMES, WRITE_TOOL_NAMES

# 도구가 실패했을 때 모델이 읽는 문구이며 계약이 문장을 소유한다.
TOOL_FAILED = chat_tool_failure_text(TOOL_FAILED_KEY)

# 모델이 계약 밖의 인자를 보냈을 때 그 사유로 쓰는 문구다.
INVALID_ARGS = "the arguments did not match the tool contract"

# 대기 행을 세웠다면서 인용할 id를 주지 않은 창구에 대는 사유다.
MISSING_CONFIRMATION_ID = "the confirmation API answered without an id"

# 도구는 HTTP만 타므로 연결 계열 오류만 일시적이며 도메인 응답은 재시도하지 않는다.
TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    httpx.TransportError,
    ConnectionError,
    TimeoutError,
)

# 계약 판이 바뀌지 않는 한 같은 레지스트리를 다시 쓰며, 판이 바뀐 배포 사이에만 자리를 하나 더 쓴다.
_REGISTRY_VERSIONS = 4


def chat_tool_failed(tool_name: str, reason: str) -> str:
    """도구가 실패했음을 계약이 소유한 문장으로 모델에게 알린다."""
    return TOOL_FAILED.format(tool=tool_name, reason=reason)


def for_model(text: str) -> str:
    """도구가 낸 결과를 모델의 다음 입력으로 넣기 전에 계약의 query 자리를 지난다."""
    try:
        payload = json.loads(text)
    except ValueError:
        return redact_text(text, stage=RedactionStage.QUERY)
    return json.dumps(redact(payload, stage=RedactionStage.QUERY), ensure_ascii=False)


class ChatTool(AgentTool[BaseModel, ChatToolContext], ABC):
    """계약이 선언한 chat 도구 하나이며 검증된 인자를 창구가 아는 와이어 이름으로 되돌린다."""

    transient_errors = TRANSIENT_ERRORS

    @staticmethod
    def wire_args(args: BaseModel) -> JsonObject:
        """모델이 고른 인자를 계약이 선언한 와이어 이름의 조회 인자로 만든다."""
        dumped: JsonObject = args.model_dump(by_alias=True, exclude_none=True)
        return dumped


class ChatReadTool(ChatTool):
    """확인 없이 추적 창구를 되읽는 도구다."""

    async def execute(self, args: BaseModel, context: ChatToolContext) -> str:
        result = await context.backends.read.read(self.name, self.wire_args(args))
        if not result.ok:
            return chat_tool_failed(self.name, result.reason)
        return for_model(result.text)


class ChatAgentReadTool(ChatTool):
    """원장이 에이전트 서비스에 있어 기점이 다른 되읽기 도구다."""

    async def execute(self, args: BaseModel, context: ChatToolContext) -> str:
        result = await context.backends.agent_read.read(self.name, self.wire_args(args))
        if not result.ok:
            return chat_tool_failed(self.name, result.reason)
        return for_model(result.text)


class ChatProposalTool(ChatTool):
    """실행 대신 확인 대기 행을 세우고 그 id를 이 턴의 장부에 남기는 쓰기 도구다."""

    async def execute(self, args: BaseModel, context: ChatToolContext) -> str:
        wire = self.wire_args(args)
        result = await context.backends.write.propose(self.name, wire)
        if not result.ok:
            return chat_tool_failed(self.name, result.reason)
        if not result.confirmation_id:
            return chat_tool_failed(self.name, MISSING_CONFIRMATION_ID)
        context.proposals.append(
            ProposedWrite(confirmationId=result.confirmation_id, toolName=self.name, args=wire)
        )
        return for_model(result.text)


class ChatRecallTool(ChatTool):
    """확인 없이 이 사용자의 장기기억을 되읽는 도구다."""

    async def execute(self, _args: BaseModel, context: ChatToolContext) -> str:
        recalled = await context.backends.memory.recall()
        if not recalled.ok:
            return chat_tool_failed(self.name, recalled.reason)
        return for_model(json.dumps({FACTS_FIELD: recalled.facts}, ensure_ascii=False))


class ChatTools(ToolRegistry[ChatToolContext]):
    """계약이 적은 인자 스키마를 그대로 모델에게 보이고 계약 밖 인자를 모델이 읽는 실패로 되돌린다."""

    async def invoke(self, name: str, raw_args: JsonObject, context: ChatToolContext) -> str:
        """대화는 도구 하나가 틀렸다고 턴을 끊지 않으므로 인자 거절도 모델이 읽는 결과로 낸다."""
        try:
            return await super().invoke(name, raw_args, context)
        except ValidationError:
            return chat_tool_failed(name, INVALID_ARGS)

    def _as_langchain(self, tool: AgentTool[Any, ChatToolContext]) -> BaseTool:
        # 계약이 적은 별칭과 extra 금지가 그대로 서도록 모델에게는 인자 모델의 JSON 스키마를 넘긴다.
        async def run(**kwargs: Any) -> str:
            return await self.invoke(tool.name, kwargs, _tool_context())

        return StructuredTool(
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_model.model_json_schema(),
            coroutine=run,
        )


def _tool_context() -> ChatToolContext:
    return cast("ChatToolContext", get_runtime().context)


def _tool_class(base: type[ChatTool], name: str, description: str) -> type[ChatTool]:
    """계약이 선언한 도구 하나를 이름과 설명과 인자 모델을 가진 도구 클래스로 만든다."""
    label = "".join(part.capitalize() for part in name.split("_"))
    declared = {"name": name, "description": description, "args_model": ARGS_MODELS[name]}
    return cast("type[ChatTool]", type(f"{label}Tool", (base,), declared))


def _tool(base: type[ChatTool], name: str, descriptions: Mapping[str, str]) -> ChatTool:
    return _tool_class(base, name, descriptions.get(name, name))()


def build_chat_registry(descriptions: Mapping[str, str]) -> ChatTools:
    """계약이 표면마다 연 도구를 모델이 볼 설명과 함께 레지스트리 하나로 세운다."""
    _assert_memory_names()
    tools: list[ChatTool] = [
        *(_tool(ChatReadTool, name, descriptions) for name in READ_TOOL_NAMES),
        *(_tool(ChatAgentReadTool, name, descriptions) for name in AGENT_READ_TOOL_NAMES),
        *(_tool(ChatProposalTool, name, descriptions) for name in WRITE_TOOL_NAMES),
        _tool(ChatRecallTool, recall_tool_name(), descriptions),
    ]
    return ChatTools(tools)


@lru_cache(maxsize=_REGISTRY_VERSIONS)
def _registry_for(descriptions: tuple[tuple[str, str], ...]) -> ChatTools:
    return build_chat_registry(dict(descriptions))


def chat_tool_registry(descriptions: Mapping[str, str]) -> ChatTools:
    """같은 설명을 실어 보낸 턴들이 도구와 그 스키마를 다시 세우지 않고 함께 쓴다."""
    return _registry_for(tuple(sorted(descriptions.items())))


def _assert_memory_names() -> None:
    if set(MEMORY_TOOL_NAMES) != {recall_tool_name()}:
        raise ValueError("chat memory tool names drifted from the contract")
