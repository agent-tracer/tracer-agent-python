"""공통 DB의 대화 이력을 실행별 LangGraph 체크포인트에 한 번만 복원한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph


@dataclass(frozen=True)
class SeededTurn:
    """이번 호출이 넣을 입력과, 앞선 시도가 남긴 체크포인트를 이어받는지다."""

    messages: list[BaseMessage]
    resumed: bool


async def seed_checkpoint(
    agent: CompiledStateGraph[Any, Any, Any, Any],
    saver: BaseCheckpointSaver[Any],
    config: RunnableConfig,
    replayed: list[BaseMessage],
) -> SeededTurn:
    """실행 체크포인트가 비어 있을 때만 앞선 이력을 심고 이번 턴이 이어받을 마지막 줄을 돌려준다."""
    if await saver.aget_tuple(config) is not None:
        return SeededTurn([], resumed=True)
    if not replayed:
        return SeededTurn([], resumed=False)
    prior, latest = replayed[:-1], replayed[-1:]
    if prior:
        await agent.aupdate_state(config, {"messages": prior})
    return SeededTurn(latest, resumed=False)
