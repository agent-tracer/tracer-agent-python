"""task-cleanup 노드를 그래프 밖에서 직접 실행해 실패 하향을 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

import asyncio
from typing import Any

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, FakeToolLoopChat, mk_ai, mk_rates
from tests.support.prompts import TASK_CLEANUP_PROMPT
from tracer_agent.shared.agents.task_cleanup.models import (
    InspectAssignment,
    InspectDispatch,
    TaskCleanupRequest,
)
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.budget import ExecutionBudget
from tracer_agent.worker.agents.runtime.llm.client import ChatPair
from tracer_agent.worker.agents.runtime.scoped_event_reader import ScopedEventReader
from tracer_agent.worker.agents.task_cleanup.deps import CleanupDeps, new_cleanup_caller
from tracer_agent.worker.agents.task_cleanup.nodes.inspect import InspectNode
from tracer_agent.worker.agents.task_cleanup.prompts import build_prompt_bundle

_COMPLETION = {"url": "http://worker:8810/runs/complete", "token": "done-1"}

_EVENT_ROWS = [
    {
        "id": "event-1",
        "seq": "1",
        "kind": "agent_tracer.user.message",
        "title": "x",
        "filePaths": [],
        "metadata": {},
        "occurredAt": "2026-07-14T00:00:00Z",
    }
]


def _request(*candidates: dict[str, object]) -> TaskCleanupRequest:
    return TaskCleanupRequest(
        model="claude-sonnet-4-6",
        apiKey="sk-test",
        modelRates=WIRE_MODEL_RATES,
        limits=WIRE_LIMITS,
        scannedAt="2026-07-14T00:00:00Z",
        userId="user-1",
        maxSuggestions=5,
        language="ko",
        batch={"candidates": list(candidates), "batchTruncated": False},  # type: ignore[arg-type]
        completionCallback=_COMPLETION,  # type: ignore[arg-type]
    )


def _candidate(task_id: str, *, has_events: bool) -> dict[str, object]:
    return {
        "id": task_id,
        "visibleTitle": f"제목 {task_id}",
        "status": "running",
        "lastEventAt": None,
        "hasEvents": has_events,
        "activeChildCount": 0,
        "candidateReasons": ["stale"],
    }


async def test_후보_조사_예외는_실패_보고로_강등된다() -> None:
    class BoomChat(FakeToolLoopChat):
        async def ainvoke(self, _messages: list[Any]) -> Any:
            raise RuntimeError("inspect blew up")

    req = _request(_candidate("task-1", has_events=True))
    node = InspectNode(
        CleanupDeps(
            req=req,
            reader=ScopedEventReader(FakeTracerApi()),
            usage=ExecutionTrace(),
            caller=new_cleanup_caller(ChatPair(BoomChat([]), None)),  # type: ignore[arg-type]
            budget=ExecutionBudget(1.0, mk_rates()),
            prompts=build_prompt_bundle(TASK_CLEANUP_PROMPT),
            prompt=TASK_CLEANUP_PROMPT,
            language_directives=TASK_CLEANUP_PROMPT.language_directives,
        )
    )

    result = await node.run(
        InspectDispatch(
            assignment=InspectAssignment(taskId="task-1", depth="normal"), max_turns=2, cost_budget=0.25
        )
    )

    # 조사가 실패한 후보는 안전하게 보관 불가로, 사유는 실패로 올린다.
    report = result["reports"][0]
    assert report.taskId == "task-1"
    assert report.archivable is False
    assert report.reason == "Investigation failed: inspect blew up"
    assert report.citedEventIds == []
    assert "model_cost_usd" in result


class _ReadingChat:
    """맡은 후보의 이벤트를 한 번 읽고 판정을 내는 도구 루프 대역이다."""

    def __init__(self, gate: asyncio.Barrier) -> None:
        self.gate = gate

    def bind_tools(self, _tools: list[Any], **_kwargs: Any) -> _ReadingChat:
        return self

    def bind(self, **_kwargs: Any) -> _ReadingChat:
        return self

    async def ainvoke(self, messages: list[Any]) -> Any:
        text = " ".join(str(getattr(message, "content", message)) for message in messages)
        task_id = next(name for name in ("task-1", "task-2") if name in text)
        if not any(getattr(message, "type", "") == "tool" for message in messages):
            await self.gate.wait()
            return mk_ai(
                tool_calls=[
                    {
                        "name": "get_task_events",
                        "args": {"taskId": task_id},
                        "id": f"call-read-{task_id}",
                        "type": "tool_call",
                    }
                ]
            )
        return mk_ai(
            tool_calls=[
                {
                    "name": "InspectReport",
                    "args": {
                        "taskId": task_id,
                        "archivable": True,
                        "reason": "활동이 없다",
                        "citedEventIds": ["event-1"],
                    },
                    "id": f"call-report-{task_id}",
                    "type": "tool_call",
                }
            ]
        )


async def test_병렬로_도는_검토자는_자기_후보의_이벤트만_장부에_쌓는다() -> None:
    req = _request(_candidate("task-1", has_events=True), _candidate("task-2", has_events=True))
    deps = CleanupDeps(
        req=req,
        reader=ScopedEventReader(FakeTracerApi(_EVENT_ROWS)),  # type: ignore[arg-type]
        usage=ExecutionTrace(),
        caller=new_cleanup_caller(ChatPair(_ReadingChat(asyncio.Barrier(2)), None)),  # type: ignore[arg-type]
        budget=ExecutionBudget(1.0, mk_rates()),
        prompts=build_prompt_bundle(TASK_CLEANUP_PROMPT),
        prompt=TASK_CLEANUP_PROMPT,
        language_directives=TASK_CLEANUP_PROMPT.language_directives,
    )
    node = InspectNode(deps)

    results = await asyncio.gather(
        node.run(
            InspectDispatch(
                assignment=InspectAssignment(taskId="task-1", depth="shallow"), max_turns=2, cost_budget=0.25
            )
        ),
        node.run(
            InspectDispatch(
                assignment=InspectAssignment(taskId="task-2", depth="shallow"), max_turns=2, cost_budget=0.25
            )
        ),
    )

    # 두 검토가 컴파일된 agent 하나를 함께 써도 장부는 각자의 것으로 남는다.
    assert deps.caller.compiled_agents() == 1
    assert set(results[0]["event_ids_by_task"]) == {"task-1"}
    assert set(results[1]["event_ids_by_task"]) == {"task-2"}
