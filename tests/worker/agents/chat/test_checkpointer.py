"""이어받는 시도가 앞선 시도의 이력과 꼬리를 다시 심지 않는지 검증한다(네트워크 없음)."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from tracer_agent.worker.agents.chat.checkpointer import seed_checkpoint
from tracer_agent.worker.agents.runtime.checkpoint import GraphCheckpointProvider


class _State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


async def _answer(_state: _State) -> dict[str, Any]:
    return {"messages": []}


def _agent() -> Any:
    builder: StateGraph[Any, Any, Any, Any] = StateGraph(_State)
    builder.add_node("answer", _answer)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    return builder


def _history() -> list[BaseMessage]:
    return [HumanMessage(content="앞선 질문"), HumanMessage(content="이번 질문")]


async def test_처음_선_실행은_앞선_이력을_심고_마지막_줄만_입력으로_낸다() -> None:
    saver = InMemorySaver()
    agent = _agent().compile(checkpointer=saver)
    config: Any = {"configurable": {"thread_id": "execution-1"}}

    seeded = await seed_checkpoint(agent, saver, config, _history())

    assert seeded.resumed is False
    assert [str(message.content) for message in seeded.messages] == ["이번 질문"]
    stored = (await agent.aget_state(config)).values["messages"]
    assert [str(message.content) for message in stored] == ["앞선 질문"]


async def test_이어받는_시도는_이력을_다시_심지_않는다() -> None:
    saver = InMemorySaver()
    agent = _agent().compile(checkpointer=saver)
    config: Any = {"configurable": {"thread_id": "execution-1"}}
    await seed_checkpoint(agent, saver, config, _history())

    resumed = await seed_checkpoint(agent, saver, config, _history())

    assert resumed.resumed is True
    assert resumed.messages == []
    stored = (await agent.aget_state(config)).values["messages"]
    assert [str(message.content) for message in stored] == ["앞선 질문"]


async def test_이력이_없으면_심을_것도_이어받을_것도_없다() -> None:
    saver = InMemorySaver()
    agent = _agent().compile(checkpointer=saver)
    config: Any = {"configurable": {"thread_id": "execution-2"}}

    seeded = await seed_checkpoint(agent, saver, config, [])

    assert seeded.resumed is False
    assert seeded.messages == []


class _InMemoryProvider(GraphCheckpointProvider):
    """Postgres 대신 메모리 세이버를 내주어 종결이 스레드를 지우는지 볼 수 있게 한다."""

    def __init__(self) -> None:
        super().__init__("postgresql://unused")
        self._memory = InMemorySaver()

    async def saver(self) -> Any:
        self._saver = self._memory
        return self._memory


async def test_실행이_끝나면_그_턴의_체크포인트를_지운다() -> None:
    provider = _InMemoryProvider()
    saver = await provider.saver()
    config: Any = {"configurable": {"thread_id": "execution-1"}}
    agent = _agent().compile(checkpointer=saver)
    await agent.ainvoke({"messages": _history()}, config=config)
    assert await saver.aget_tuple(config) is not None

    await provider.forget("execution-1")

    assert await saver.aget_tuple(config) is None


async def test_세이버를_연_적_없는_실행은_지울_것도_없다() -> None:
    await GraphCheckpointProvider("postgresql://unused").forget("execution-1")
