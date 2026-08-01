"""요청별 의존을 받아 chat의 35개 도구를 langchain 도구로 조립한다."""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, StructuredTool

from tracer_agent.shared.agents.chat.memory_policy import memory_rejection
from tracer_agent.shared.agents.chat.models import ProposedWrite

from ...runtime.telemetry.spans import tool_span
from ..reader import ChatReadClient
from ..store import (
    CONTENT_FIELD,
    FACTS_FIELD,
    KEY_FIELD,
    MEMORY_NAMESPACE,
    RECALL_LIMIT,
    REMEMBERED_STATUS,
    STATUS_FIELD,
    ChatMemoryUnavailable,
)
from ..writer import ChatWriteClient
from .specs import AGENT_READ_TOOL_NAMES, ARGS_MODELS, MEMORY_TOOL_NAMES, READ_TOOL_NAMES, WRITE_TOOL_NAMES

# 도구가 무너졌을 때 모델이 읽는 문구이며 계약이 문장을 소유한다.
TOOL_FAILED = (
    "Tool {tool} failed: {reason}. Do not call it again more than once. Continue with what you "
    "already know, and tell the user plainly which information you could not read."
)

# 읽기 진입점이 아예 없는 실행에서 도구가 모델에게 대는 사유다.
READ_BACKEND_MISSING = "the read backend is not available in this execution"

# 확인 창구가 아예 없는 실행에서 쓰기 도구가 모델에게 대는 사유다.
WRITE_BACKEND_MISSING = "the confirmation backend is not available in this execution"

# 기억 API가 아예 없는 실행에서 장기기억 도구가 모델에게 대는 사유다.
MEMORY_BACKEND_MISSING = "the memory backend is not available in this execution"


def _clean(kwargs: dict[str, Any]) -> dict[str, object]:
    return {key: value for key, value in kwargs.items() if value is not None}


class ChatToolRegistry:
    """chat 도구를 langchain 도구로 어댑트하며 도구 수와 무관한 조립을 제공한다."""

    def __init__(self, tools: list[BaseTool]) -> None:
        self._tools = tools

    def langchain_tools(self) -> list[BaseTool]:
        """등록된 chat 도구 전체를 langchain 도구 목록으로 낸다."""
        return list(self._tools)


def build_chat_registry(
    read_client: ChatReadClient | None,
    proposals: list[ProposedWrite],
    descriptions: dict[str, str],
    *,
    agent_name: str,
    write_client: ChatWriteClient | None = None,
    agent_read_client: ChatReadClient | None = None,
) -> ChatToolRegistry:
    """읽기·확인 진입점과 제안 장부를 쥔 chat 도구 레지스트리를 만든다."""
    _assert_memory_names()
    tools: list[BaseTool] = [
        *(_read_tool(name, read_client, descriptions, agent_name) for name in READ_TOOL_NAMES),
        *(_read_tool(name, agent_read_client, descriptions, agent_name) for name in AGENT_READ_TOOL_NAMES),
        *(
            _proposal_tool(name, write_client, proposals, descriptions, agent_name)
            for name in WRITE_TOOL_NAMES
        ),
        _recall_tool(descriptions, agent_name),
        _remember_tool(descriptions, agent_name),
    ]
    return ChatToolRegistry(tools)


def _structured(name: str, descriptions: dict[str, str], coroutine: Any) -> StructuredTool:
    # 인자 스키마를 계약 와이어 이름(예: from) 그대로 노출하려면 별칭을 적용한 JSON 스키마를 넘긴다.
    # noinspection PyTypeChecker
    return StructuredTool(
        name=name,
        description=descriptions.get(name, name),
        args_schema=ARGS_MODELS[name].model_json_schema(),
        coroutine=coroutine,
    )


def _read_tool(
    name: str, read_client: ChatReadClient | None, descriptions: dict[str, str], agent_name: str
) -> StructuredTool:
    async def run(**kwargs: Any) -> str:
        args = _clean(kwargs)
        async with tool_span(name, agent_name=agent_name, parameters=args):
            if read_client is None:
                return TOOL_FAILED.format(tool=name, reason=READ_BACKEND_MISSING)
            result = await read_client.read(name, args)
            if not result.ok:
                return TOOL_FAILED.format(tool=name, reason=f"the read API answered {result.status_code}")
            return result.text

    return _structured(name, descriptions, run)


def _proposal_tool(
    name: str,
    write_client: ChatWriteClient | None,
    proposals: list[ProposedWrite],
    descriptions: dict[str, str],
    agent_name: str,
) -> StructuredTool:
    async def run(**kwargs: Any) -> str:
        args = _clean(kwargs)
        async with tool_span(name, agent_name=agent_name, parameters=args):
            if write_client is None:
                return TOOL_FAILED.format(tool=name, reason=WRITE_BACKEND_MISSING)
            result = await write_client.propose(name, args)
            if not result.ok:
                reason = f"the confirmation API answered {result.status_code}"
                return TOOL_FAILED.format(tool=name, reason=reason)
            if not result.confirmation_id:
                reason = "the confirmation API answered without an id"
                return TOOL_FAILED.format(tool=name, reason=reason)
            proposals.append(ProposedWrite(confirmationId=result.confirmation_id, toolName=name, args=args))
            return result.text

    return _structured(name, descriptions, run)


def _recall_tool(descriptions: dict[str, str], agent_name: str) -> StructuredTool:
    async def run(runtime: ToolRuntime, **_kwargs: Any) -> str:
        async with tool_span("recall_facts", agent_name=agent_name, parameters={}):
            store = runtime.store
            if store is None:
                return TOOL_FAILED.format(tool="recall_facts", reason=MEMORY_BACKEND_MISSING)
            try:
                items = await store.asearch(MEMORY_NAMESPACE, limit=RECALL_LIMIT)
            except ChatMemoryUnavailable as failure:
                return TOOL_FAILED.format(tool="recall_facts", reason=str(failure))
            facts = [{KEY_FIELD: item.key, **item.value} for item in items]
            return json.dumps({FACTS_FIELD: facts}, ensure_ascii=False)

    return _structured("recall_facts", descriptions, run)


def _remember_tool(descriptions: dict[str, str], agent_name: str) -> StructuredTool:
    async def run(runtime: ToolRuntime, **kwargs: Any) -> str:
        args = _clean(kwargs)
        key = str(args["key"])
        content = str(args.get("content", ""))
        async with tool_span("remember_fact", agent_name=agent_name, parameters={"key": key}):
            store = runtime.store
            if store is None:
                return TOOL_FAILED.format(tool="remember_fact", reason=MEMORY_BACKEND_MISSING)
            rejection = memory_rejection(content)
            if rejection is not None:
                return TOOL_FAILED.format(tool="remember_fact", reason=rejection)
            try:
                # 턴이 중간에 끊겨도 남아야 하므로 산출물로 미루지 않고 부른 자리에서 즉시 적재한다.
                await store.aput(MEMORY_NAMESPACE, key, {CONTENT_FIELD: content})
            except ChatMemoryUnavailable as failure:
                return TOOL_FAILED.format(tool="remember_fact", reason=str(failure))
            remembered = {KEY_FIELD: key, CONTENT_FIELD: content, STATUS_FIELD: REMEMBERED_STATUS}
            return json.dumps(remembered, ensure_ascii=False)

    return _structured("remember_fact", descriptions, run)


def _assert_memory_names() -> None:
    if set(MEMORY_TOOL_NAMES) != {"recall_facts", "remember_fact"}:
        raise ValueError("chat memory tool names drifted from the contract")
