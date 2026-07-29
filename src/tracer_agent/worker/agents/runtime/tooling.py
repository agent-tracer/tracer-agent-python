"""도구 하나를 이름·스키마·실행·근거로 닫아 도구 수와 무관한 실행 기계를 제공한다."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, ClassVar

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from .telemetry.spans import tool_span


class AgentTool[ArgsT: BaseModel](ABC):
    """도구의 이름과 설명과 인자 스키마와 실행과 근거 기록을 한 클래스에 모은다."""

    name: ClassVar[str]
    description: ClassVar[str]
    transient_errors: ClassVar[tuple[type[Exception], ...]] = ()
    # ClassVar는 타입 변수를 담지 못해 인자 모델의 구체 타입은 execute 시그니처가 말한다.
    args_model: ClassVar[type[BaseModel]]

    @abstractmethod
    async def execute(self, args: ArgsT) -> str:
        """검증된 인자로 자기 백엔드에서 도구를 실행해 응답 본문을 낸다."""

    def record(self, _args: ArgsT, _content: str, /) -> None:
        """도구가 실제로 돌려준 값만 인용 가능한 근거로 자기 장부에 올린다."""
        return


class ToolRegistry:
    """등록한 도구를 검증과 관측과 근거 기록으로 감싸 도구가 늘어도 이 코드는 불변이다."""

    def __init__(self, tools: Sequence[AgentTool[Any]], *, agent_name: str) -> None:
        self._tools = list(tools)
        self._by_name = {tool.name: tool for tool in self._tools}
        self._agent_name = agent_name

    async def invoke(self, name: str, raw_args: dict[str, Any]) -> str:
        """모델이 고른 도구를 인자 검증과 스팬 뒤 실행하고 근거를 남긴다."""
        tool = self._by_name[name]
        args = tool.args_model.model_validate(raw_args)
        parameters = args.model_dump(exclude_none=True)
        async with tool_span(name, agent_name=self._agent_name, parameters=parameters):
            content = await tool.execute(args)
        tool.record(args, content)
        return content

    def langchain_tools(self, names: tuple[str, ...] | None = None) -> list[BaseTool]:
        """등록된 도구를 args_model을 스키마로 쓰는 langchain 도구로 어댑트한다."""
        chosen = self._tools if names is None else [t for t in self._tools if t.name in names]
        return [self._as_langchain(tool) for tool in chosen]

    def transient_errors(self) -> tuple[type[Exception], ...]:
        """등록된 도구들이 선언한 일시 오류를 중복 없이 합산한다."""
        merged: list[type[Exception]] = []
        for tool in self._tools:
            for error in tool.transient_errors:
                if error not in merged:
                    merged.append(error)
        return tuple(merged)

    def _as_langchain(self, tool: AgentTool[Any]) -> BaseTool:
        async def run(**kwargs: Any) -> str:
            return await self.invoke(tool.name, kwargs)

        # noinspection PyTypeChecker
        return StructuredTool(
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_model,
            coroutine=run,
        )
