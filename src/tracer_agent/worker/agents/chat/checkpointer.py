"""공통 DB의 대화 이력을 실행별 LangGraph 체크포인트에 한 번만 복원한다."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph


async def seed_checkpoint(
    agent: CompiledStateGraph[Any, Any, Any, Any],
    saver: BaseCheckpointSaver[Any],
    config: RunnableConfig,
    replayed: list[BaseMessage],
) -> list[BaseMessage]:
    """실행 체크포인트가 비어 있을 때만 과거 이력을 심고 최신 사용자 메시지를 돌려준다."""
    if await saver.aget_tuple(config) is not None:
        return []
    if not replayed:
        return []
    prior, latest = replayed[:-1], replayed[-1:]
    if prior:
        await agent.aupdate_state(config, {"messages": prior})
    return latest
