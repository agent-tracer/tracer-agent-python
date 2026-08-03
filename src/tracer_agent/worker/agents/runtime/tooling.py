"""도구 하나를 이름·스키마·실행·근거로 닫아 도구 수와 무관한 실행 기계를 제공한다."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, ClassVar, Protocol, cast

from langchain_core.tools import BaseTool, StructuredTool
from langgraph.runtime import get_runtime
from pydantic import BaseModel

from tracer_agent.shared.agents.shared.json_view import JsonObject

from .telemetry.spans import tool_span


class ToolContext(Protocol):
    """도구가 호출마다 실려 받는 실행 컨텍스트는 관측이 도구를 귀속시킬 소유자 이름을 갖는다."""

    tool_owner: str


class AgentTool[ArgsT: BaseModel, ContextT: ToolContext](ABC):
    """도구의 이름과 설명과 인자 스키마와 실행과 근거 기록을 한 클래스에 모은다."""

    name: ClassVar[str]
    description: ClassVar[str]
    transient_errors: ClassVar[tuple[type[Exception], ...]] = ()
    # ClassVar는 타입 변수를 담지 못해 인자 모델의 구체 타입은 execute 시그니처가 말한다.
    args_model: ClassVar[type[BaseModel]]

    @abstractmethod
    async def execute(self, args: ArgsT, context: ContextT) -> str:
        """검증된 인자와 이 호출에 실려 온 조회 진입점으로 도구를 실행해 응답 본문을 낸다."""

    def record(self, _args: ArgsT, _content: str, _context: ContextT, /) -> None:
        """도구가 실제로 돌려준 값만 인용 가능한 근거로 이 호출의 장부에 올린다."""
        return


class ToolRegistry[ContextT: ToolContext]:
    """등록한 도구를 검증과 관측과 근거 기록으로 감싸 도구가 늘어도 이 코드는 불변이다."""

    def __init__(self, tools: Sequence[AgentTool[Any, ContextT]]) -> None:
        self._tools = list(tools)
        self._by_name = {tool.name: tool for tool in self._tools}
        self._adapted: dict[tuple[str, ...] | None, list[BaseTool]] = {}

    async def invoke(self, name: str, raw_args: JsonObject, context: ContextT) -> str:
        """모델이 고른 도구를 인자 검증과 스팬 뒤 실행하고 이 호출의 장부에 근거를 남긴다."""
        tool = self._by_name[name]
        args = tool.args_model.model_validate(raw_args)
        parameters = args.model_dump(exclude_none=True)
        async with tool_span(name, agent_name=context.tool_owner, parameters=parameters):
            content = await tool.execute(args, context)
        tool.record(args, content, context)
        return content

    def langchain_tools(self, names: tuple[str, ...] | None = None) -> list[BaseTool]:
        """등록된 도구를 args_model을 스키마로 쓰는 langchain 도구로 어댑트한다."""
        adapted = self._adapted.get(names)
        if adapted is None:
            adapted = [self._as_langchain(tool) for tool in self._chosen(names)]
            self._adapted[names] = adapted
        return list(adapted)

    def transient_errors(self, names: tuple[str, ...] | None = None) -> tuple[type[Exception], ...]:
        """고른 도구들이 선언한 일시 오류를 중복 없이 합산한다."""
        merged: list[type[Exception]] = []
        for tool in self._chosen(names):
            for error in tool.transient_errors:
                if error not in merged:
                    merged.append(error)
        return tuple(merged)

    def _chosen(self, names: tuple[str, ...] | None) -> list[AgentTool[Any, ContextT]]:
        if names is None:
            return self._tools
        return [tool for tool in self._tools if tool.name in names]

    def _as_langchain(self, tool: AgentTool[Any, ContextT]) -> BaseTool:
        async def run(**kwargs: Any) -> str:
            # 그래프가 실행마다 새로 세우는 런타임이라 팬아웃이 겹쳐도 이 호출의 컨텍스트만 온다.
            runtime = get_runtime()
            return await self.invoke(tool.name, kwargs, cast("ContextT", runtime.context))

        return StructuredTool(
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_model,
            coroutine=run,
        )
