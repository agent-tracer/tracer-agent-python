"""chat 턴이 세우는 실행 설정이 잡과 같은 가림 수준으로 추적 창구에 나가는지 고정한다."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, ClassVar

import httpx
import pytest
from langchain_core.tracers.langchain import LangChainTracer

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, FakeToolLoopChat
from tests.support.prompts import CHAT_PROMPT
from tracer_agent.shared.agents.chat.models import ChatRequest
from tracer_agent.worker.agents.chat import agent as chat_mod
from tracer_agent.worker.agents.chat.agent import AGENT_NAME
from tracer_agent.worker.agents.runtime.durable_graph import execution_config
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm import structured_agent
from tracer_agent.worker.agents.runtime.llm.client import ChatPair
from tracer_agent.worker.agents.runtime.telemetry.disclosure import TraceSafeMetadata


class _CapturingClient:
    """추적 창구로 열리는 연결 대신 그 연결이 받은 가림 설정만 적는다."""

    captured: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, **kwargs: Any) -> None:
        _CapturingClient.captured.append(kwargs)


class _SilentAgent:
    """도구 루프를 실행하지 않고 그 호출에 실린 실행 설정만 남긴다."""

    def __init__(self, seen: list[dict[str, Any]]) -> None:
        self._seen = seen

    async def astream(self, *_args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        self._seen.append(dict(kwargs["config"]))
        return
        yield


class _SilentAgents:
    """실행이 무엇으로 판을 부르는지만 보는 판 공급자다."""

    def __init__(self, seen: list[dict[str, Any]]) -> None:
        self._seen = seen

    def compiled(self, _req: ChatRequest, _system_prompt: str, _checkpointer: Any) -> Any:
        return _SilentAgent(self._seen)


def _request(**overrides: Any) -> ChatRequest:
    values: dict[str, Any] = {
        "model": "claude-haiku-4-5",
        "apiKey": "sk-test",
        "modelRates": WIRE_MODEL_RATES,
        "limits": WIRE_LIMITS,
        "threadId": "thread-1",
        "executionId": "execution-1",
        "userId": "user-1",
        "language": "ko",
        "messages": [{"role": "user", "content": "task-1 아카이브해줘"}],
    }
    values.update(overrides)
    return ChatRequest.model_validate(values)


@pytest.fixture(autouse=True)
def _capture_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _CapturingClient.captured = []
    # 추적 창구 연결은 프로파일마다 한 번만 열리므로 이 검사가 그 기억을 비우고 시작한다.
    structured_agent._tracer.cache_clear()  # noqa: SLF001
    monkeypatch.setattr("tracer_agent.worker.agents.runtime.llm.structured_agent.Client", _CapturingClient)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("LANGSMITH_HIDE_INPUTS", raising=False)


async def _chat_config(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> dict[str, Any]:
    """chat 한 턴을 실제로 열어 그 턴이 도구 루프에 실은 실행 설정을 낸다."""
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(chat_mod, "GivenModelChatAgents", lambda _chats: _SilentAgents(seen))

    chats = ChatPair(FakeToolLoopChat(["정리했습니다"]), None)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200))) as client:
        await chat_mod.run_chat(_request(**overrides), client, ExecutionTrace(), CHAT_PROMPT, None, chats)
    return seen[0]


async def test_추적을_켠_chat_턴은_원문을_가리는_tracer_하나로_나간다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = await _chat_config(monkeypatch)

    # tracer 가 이 자리에 있어야 langchain-core 가 원문을 가리지 않는 기본 tracer 를 대신 붙이지 않는다.
    tracers = [handler for handler in config["callbacks"] if isinstance(handler, LangChainTracer)]
    assert len(tracers) == 1
    assert _CapturingClient.captured == [{"hide_inputs": True, "hide_outputs": True}]


async def test_chat과_잡이_같은_가림_수준의_추적_연결을_함께_쓴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = await _chat_config(monkeypatch)

    job = execution_config(
        10,
        TraceSafeMetadata(agent_name="task_cleanup", model_requested="claude-haiku-4-5", prompt_version="v1"),
        "job-1",
    )
    assert config["callbacks"] == job["callbacks"]


async def test_chat_턴이_실행을_가리키는_trace_metadata와_안정_run_id를_싣는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = await _chat_config(
        monkeypatch,
        attempt=2,
    )

    assert config["run_name"] == AGENT_NAME
    assert config["metadata"]["agent_tracer.execution.id"] == "execution-1"
    assert config["metadata"]["agent_tracer.attempt.id"] == "2"
    assert config["metadata"]["agent_tracer.prompt.version"] == CHAT_PROMPT.version()
    assert config["metadata"]["agent_tracer.tool.contract.version"] == CHAT_PROMPT.tool_contract_version
    # 재시도가 같은 run 에 접히도록 run_id 는 에이전트와 실행과 시도에서만 유도된다.
    assert config["run_id"] is not None
    assert config["configurable"]["thread_id"] == "execution-1"
