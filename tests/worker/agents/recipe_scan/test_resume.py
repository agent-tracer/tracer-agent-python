"""잡 그래프가 같은 열쇠로 다시 실행되면 끝난 노드를 다시 실행하지 않는지 검증한다."""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from tests.support.fakes import (
    WIRE_LIMITS,
    WIRE_MODEL_RATES,
    FakeToolLoopChat,
)
from tests.support.prompts import RECIPE_SCAN_PROMPT
from tracer_agent.shared.agents.recipe_scan.models import DispatchPlan, RecipeScanRequest
from tracer_agent.worker.agents.recipe_scan import agent as recipe_mod
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.client import ChatPair
from tracer_agent.worker.agents.runtime.serde import graph_serde

_PLAN = DispatchPlan(
    probes=[{"probe": "timeline", "depth": "deep", "question": "무엇을 했나"}]  # type: ignore[list-item]
)


class _SharedSaver:
    """한 실행의 두 시도가 같은 체크포인트를 보도록 세이버 하나를 가진다."""

    def __init__(self) -> None:
        self._saver = InMemorySaver(serde=graph_serde())

    async def saver(self) -> InMemorySaver:
        return self._saver


class _FailOnceChat(FakeToolLoopChat):
    """첫 시도의 종합에서만 실패해 앞선 노드가 체크포인트에 남게 한다."""

    def __init__(self, turns: list[Any], **kwargs: Any) -> None:
        super().__init__(turns, **kwargs)
        self.fail_next = True
        self.plan_calls = 0

    async def ainvoke(self, messages: list[Any]) -> Any:
        if any(getattr(tool, "name", "") == "DispatchPlan" for tool in self.bound_tools):
            self.plan_calls += 1
        answered = await super().ainvoke(messages)
        if self.fail_next and any(getattr(t, "name", "") == "RecipeDraft" for t in self.bound_tools):
            self.fail_next = False
            raise RuntimeError("synthesis blew up")
        return answered


def _request() -> RecipeScanRequest:
    return RecipeScanRequest.model_validate(
        {
            "model": "claude-sonnet-4-6",
            "apiKey": "sk-test",
            "modelRates": WIRE_MODEL_RATES,
            "limits": WIRE_LIMITS,
            "taskId": "t1",
            "language": "ko",
            "userId": "user-1",
            "executionId": "exec-resume-1",
        }
    )


async def test_같은_열쇠로_다시_실행하면_끝난_노드를_다시_태우지_않는다() -> None:
    chat = _FailOnceChat([{"recipes": []}, {"recipes": []}], plan=_PLAN)
    chats = ChatPair(chat, None)
    checkpoints = _SharedSaver()
    req = _request()

    with pytest.raises(RuntimeError):
        await recipe_mod.RECIPE_SCAN_JOB.run(  # type: ignore[arg-type]
            req, FakeTracerApi(), ExecutionTrace(), RECIPE_SCAN_PROMPT, checkpoints, chats
        )
    after_first = chat.plan_calls

    await recipe_mod.RECIPE_SCAN_JOB.run(  # type: ignore[arg-type]
        req, FakeTracerApi(), ExecutionTrace(), RECIPE_SCAN_PROMPT, checkpoints, chats
    )

    assert after_first == 1, "첫 시도가 계획을 한 번 세웠어야 한다"
    assert chat.plan_calls == after_first, "다시 실행이 계획을 다시 세우면 안 된다"
