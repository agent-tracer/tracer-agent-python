"""정적 그래프를 실행 상태 보존 여부에 따라 두 판으로 컴파일해 잡이 실패 지점부터 이어가게 한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from weakref import WeakKeyDictionary

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph, StateGraph

from .llm.structured_agent import recursion_config
from .telemetry.disclosure import TraceSafeMetadata

# 노드마다 쓰되 쓰기 완료를 기다리지 않으며, 잡의 재개 지점은 노드 하나면 충분하다.
_JOB_DURABILITY: Literal["sync", "async", "exit"] = "async"

# 실행이 그때까지 쓴 달러와 턴을 그래프 상태가 싣고 다니는 자리다.
_COST_FIELD = "model_cost_usd"
_TURNS_FIELD = "model_turns_used"

type CompiledGraph = CompiledStateGraph[Any, Any, Any, Any]


class DurableGraph:
    """세이버가 있으면 재개 가능한 판을, 없으면 휘발 판을 내며 판마다 한 번만 컴파일한다."""

    def __init__(self, builder: StateGraph[Any, Any, Any, Any]) -> None:
        self._builder = builder
        self._volatile: CompiledGraph | None = None
        # 세이버 자체를 약한 열쇠로 삼아 끝난 실행의 판이 남지도, 재사용된 id가 남의 판을 내주지도 않는다.
        self._durable: WeakKeyDictionary[BaseCheckpointSaver[Any], CompiledGraph] = WeakKeyDictionary()

    def compiled(self, saver: BaseCheckpointSaver[Any] | None) -> CompiledGraph:
        """이 실행이 쓸 그래프를 낸다."""
        if saver is None:
            if self._volatile is None:
                self._volatile = self._builder.compile()
            return self._volatile
        compiled = self._durable.get(saver)
        if compiled is None:
            compiled = self._builder.compile(checkpointer=saver)
            self._durable[saver] = compiled
        return compiled


def job_durability(
    saver: BaseCheckpointSaver[Any] | None,
) -> Literal["sync", "async", "exit"] | None:
    """보존할 실행에만 쓰기 시점을 정하며 보존하지 않는 실행에는 정할 것이 없다."""
    return None if saver is None else _JOB_DURABILITY


@dataclass(frozen=True)
class PriorSpend:
    """이어받을 체크포인트가 남긴, 이 실행이 앞선 시도까지 쓴 달러와 턴이다."""

    resumed: bool
    cost_usd: float
    turns: int


_FRESH_SPEND = PriorSpend(resumed=False, cost_usd=0.0, turns=0)


async def prior_spend(
    graph: CompiledGraph,
    config: RunnableConfig,
    saver: BaseCheckpointSaver[Any] | None,
) -> PriorSpend:
    """이어갈 체크포인트가 있으면 그 실행이 이미 쓴 몫을 읽어 상한이 처음부터 다시 열리지 않게 한다."""
    if saver is None or config.get("configurable") is None:
        return _FRESH_SPEND
    restored = await graph.aget_state(config)
    if restored.created_at is None:
        return _FRESH_SPEND
    values = restored.values
    if not isinstance(values, dict):
        return _FRESH_SPEND
    return PriorSpend(
        resumed=True,
        cost_usd=_number(values.get(_COST_FIELD), 0.0),
        turns=int(_number(values.get(_TURNS_FIELD), 0.0)),
    )


def _number(value: object, fallback: float) -> float:
    return float(value) if isinstance(value, int | float) else fallback


def resume_input[StateT](initial: StateT, prior: PriorSpend) -> StateT | None:
    """이어갈 상태가 있으면 상태를 다시 넣지 않아야 끝난 노드를 다시 실행하지 않는다."""
    return None if prior.resumed else initial


def with_thread(config: RunnableConfig, thread_id: str) -> RunnableConfig:
    """재시도가 같은 열쇠로 와야 앞선 노드를 다시 실행하지 않으므로 잡 하나를 재개의 범위로 잡는다."""
    return {**config, "configurable": {"thread_id": thread_id}}


def execution_config(limit: int, trace: TraceSafeMetadata, thread_id: str | None) -> RunnableConfig:
    """재개할 실행은 잡 하나를 열쇠로 삼고, 열쇠를 모르는 실행은 보존하지 않는다."""
    config = recursion_config(limit, trace)
    return config if thread_id is None else with_thread(config, thread_id)
