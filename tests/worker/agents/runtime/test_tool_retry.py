"""도구 계층의 일시 오류 재시도를 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from typing import Any

import pytest
from asyncpg import CannotConnectNowError, PostgresConnectionError
from langchain.tools import tool
from opensearchpy.exceptions import ConnectionError as OpenSearchConnectionError

from tests.support.fakes import FakeToolLoopChat, FakeTracerApi, mk_rates
from tracer_agent.shared.agents.recipe_scan.models import ProvenanceCatalog, RecipeDraft
from tracer_agent.worker.agents.recipe_scan.langchain_agent import build_recipe_agent
from tracer_agent.worker.agents.recipe_scan.reader import RecipeLedgerReader
from tracer_agent.worker.agents.recipe_scan.search import RecipeSearchReader
from tracer_agent.worker.agents.recipe_scan.tools import build_recipe_registry
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.budget import ToolLoopBudget
from tracer_agent.worker.agents.runtime.llm.standard_agent import StandardAgentContext
from tracer_agent.worker.agents.runtime.tracer_client import TracerApiUnavailable
from tracer_agent.worker.agents.task_cleanup.tools import GetTaskEventsTool

RECIPE_TRANSIENT = build_recipe_registry(
    RecipeLedgerReader(FakeTracerApi()),  # type: ignore[arg-type]
    RecipeSearchReader(FakeTracerApi()),  # type: ignore[arg-type]
    ProvenanceCatalog(),
    agent_name="recipe-scan",
).transient_errors()

CLEANUP_TRANSIENT = GetTaskEventsTool.transient_errors

_MODEL = "claude-sonnet-4-6"


def _flaky_tool(fail_times: int, error: BaseException) -> tuple[Any, list[int]]:
    calls = [0]

    @tool("get_task_events")
    def get_task_events(taskId: str) -> str:  # noqa: ARG001
        """맡은 태스크 이벤트를 읽되 앞선 몇 번은 오류를 낸다."""
        calls[0] += 1
        if calls[0] <= fail_times:
            raise error
        return '{"events": [], "truncated": false, "total": 0}'

    return get_task_events, calls


def _context() -> StandardAgentContext:
    return StandardAgentContext(
        agent_name="recipe-scan",
        trace=ExecutionTrace(),
        budget=ToolLoopBudget("recipe-scan", _MODEL, 2.0, mk_rates(), 0.0),
        max_model_turns=5,
    )


def test_재시도_대상은_연결_계열_일시_오류만이다() -> None:
    # 창구에 닿지 못한 것만 일시적이며, 검증·도메인 오류는 이 목록에 없다.
    assert TracerApiUnavailable in RECIPE_TRANSIENT
    assert ConnectionError in RECIPE_TRANSIENT and TimeoutError in RECIPE_TRANSIENT
    assert ValueError not in RECIPE_TRANSIENT
    # 두 에이전트가 같은 창구를 부르므로 재시도 대상이 같다.
    assert TracerApiUnavailable in CLEANUP_TRANSIENT
    # 원장과 색인에 직접 붙지 않으므로 그 오류는 재시도 대상이 아니다.
    assert PostgresConnectionError not in RECIPE_TRANSIENT
    assert CannotConnectNowError not in RECIPE_TRANSIENT
    assert OpenSearchConnectionError not in RECIPE_TRANSIENT


async def test_일시_오류는_도구_계층에서_재시도해_실행이_이어진다() -> None:
    flaky, calls = _flaky_tool(1, ConnectionError("transient blip"))
    chat = FakeToolLoopChat(
        [
            [{"name": "get_task_events", "args": {"taskId": "t1"}}],
            {"recipes": []},
        ]
    )
    agent = build_recipe_agent(chat, "system", (flaky,), RECIPE_TRANSIENT, max_turns=5, output=RecipeDraft)

    output = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "go"}]},
        context=_context(),
        config={"recursion_limit": 100},
    )

    # 첫 호출이 끊겨도 도구를 한 번 더 부르고, 조사는 무너지지 않고 이어진다.
    assert calls[0] == 2
    assert isinstance(output.get("structured_response"), RecipeDraft)


async def test_소진해도_실패하면_오류가_그대로_올라온다() -> None:
    flaky, calls = _flaky_tool(9, TracerApiUnavailable("tracer api down"))
    chat = FakeToolLoopChat([[{"name": "get_task_events", "args": {"taskId": "t1"}}]])
    agent = build_recipe_agent(chat, "system", (flaky,), RECIPE_TRANSIENT, max_turns=5, output=RecipeDraft)

    with pytest.raises(TracerApiUnavailable):
        await agent.ainvoke(
            {"messages": [{"role": "user", "content": "go"}]},
            context=_context(),
            config={"recursion_limit": 100},
        )

    # 최초 1회 + 재시도 2회로 세 번 시도한 뒤 실패 의미를 보존해 올린다.
    assert calls[0] == 3


async def test_도메인_오류는_재시도하지_않는다() -> None:
    flaky, calls = _flaky_tool(9, ValueError("bad citation"))
    chat = FakeToolLoopChat([[{"name": "get_task_events", "args": {"taskId": "t1"}}]])
    agent = build_recipe_agent(chat, "system", (flaky,), RECIPE_TRANSIENT, max_turns=5, output=RecipeDraft)

    with pytest.raises(ValueError, match="bad citation"):
        await agent.ainvoke(
            {"messages": [{"role": "user", "content": "go"}]},
            context=_context(),
            config={"recursion_limit": 100},
        )

    # 검증·도메인 오류는 한 번 시도하고 곧장 올린다.
    assert calls[0] == 1
