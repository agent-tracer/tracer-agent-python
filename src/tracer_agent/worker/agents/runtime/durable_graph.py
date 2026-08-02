"""정적 그래프를 실행 상태 보존 여부에 따라 두 판으로 컴파일해 잡이 실패 지점부터 이어가게 한다."""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph, StateGraph

# 노드마다 쓰되 쓰기 완료를 기다리지 않으며, 잡의 재개 입도는 노드 하나면 충분하다.
_JOB_DURABILITY: Literal["sync", "async", "exit"] = "async"

type CompiledGraph = CompiledStateGraph[Any, Any, Any, Any]


class DurableGraph:
    """세이버가 있으면 재개 가능한 판을, 없으면 휘발 판을 내며 판마다 한 번만 컴파일한다."""

    def __init__(self, builder: StateGraph[Any, Any, Any, Any]) -> None:
        self._builder = builder
        self._volatile: CompiledGraph | None = None
        self._durable: dict[int, CompiledGraph] = {}

    def compiled(self, saver: BaseCheckpointSaver[Any] | None) -> CompiledGraph:
        """이 실행이 쓸 그래프를 낸다."""
        if saver is None:
            if self._volatile is None:
                self._volatile = self._builder.compile()
            return self._volatile
        key = id(saver)
        compiled = self._durable.get(key)
        if compiled is None:
            compiled = self._builder.compile(checkpointer=saver)
            self._durable[key] = compiled
        return compiled


def job_durability(
    saver: BaseCheckpointSaver[Any] | None,
) -> Literal["sync", "async", "exit"] | None:
    """보존할 실행에만 쓰기 시점을 정하며 보존하지 않는 실행에는 정할 것이 없다."""
    return None if saver is None else _JOB_DURABILITY


def with_thread(config: RunnableConfig, thread_id: str) -> RunnableConfig:
    """재시도가 같은 열쇠로 와야 앞선 노드를 다시 태우지 않으므로 잡 하나를 재개의 범위로 잡는다."""
    return {**config, "configurable": {"thread_id": thread_id}}
