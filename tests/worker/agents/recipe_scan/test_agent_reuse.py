"""컴파일된 agent를 다시 써도 전문가마다 자기 근거 장부를 갖는지 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, mk_ai, mk_rates
from tests.support.prompts import RECIPE_SCAN_PROMPT
from tracer_agent.shared.agents.recipe_scan.models import (
    DispatchPlan,
    ProbeReport,
    ProvenanceCatalog,
    RecipeScanRequest,
)
from tracer_agent.worker.agents.recipe_scan.deps import RecipeDeps, new_recipe_caller
from tracer_agent.worker.agents.recipe_scan.prompts import build_prompt_bundle
from tracer_agent.worker.agents.recipe_scan.reader import RecipeLedgerReader
from tracer_agent.worker.agents.recipe_scan.search import RecipeSearchReader
from tracer_agent.worker.agents.recipe_scan.tools import PROBE_TOOLS, SURVEY_TOOLS
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.budget import ExecutionBudget
from tracer_agent.worker.agents.runtime.llm.client import ChatPair

_COMPLETION = {"url": "http://worker:8810/runs/complete", "token": "done-recipe"}
_TASK_MARKER = "anchor task: "
_PLAN_MARKER = "plan wanted"

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


class _ReadingProbeChat:
    """자기 태스크의 이벤트만 읽고 보고를 올리는 전문가를 재생하는 도구 루프 대역이다."""

    def __init__(self, gate: asyncio.Barrier | None = None) -> None:
        self.gate = gate

    def bind_tools(self, _tools: list[Any], **_kwargs: Any) -> _ReadingProbeChat:
        return self

    def bind(self, **_kwargs: Any) -> _ReadingProbeChat:
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        task_id = _addressed_task(messages)
        if _PLAN_MARKER in _joined(messages):
            return _call("DispatchPlan", {"probes": []})
        read_results = [message for message in messages if getattr(message, "type", "") == "tool"]
        if not read_results:
            if self.gate is not None:
                await self.gate.wait()
            return _call("get_task_events", {"taskId": task_id})
        return _call(
            "ProbeReport",
            {"probe": "timeline", "verdict": str(read_results[-1].content)},
        )


def _call(name: str, args: dict[str, Any]) -> AIMessage:
    return mk_ai(tool_calls=[{"name": name, "args": args, "id": f"call-{name}", "type": "tool_call"}])


def _joined(messages: list[Any]) -> str:
    return " ".join(str(getattr(message, "content", message)) for message in messages)


def _addressed_task(messages: list[Any]) -> str:
    return _joined(messages).split(_TASK_MARKER, 1)[1].split()[0]


def _request() -> RecipeScanRequest:
    return RecipeScanRequest.model_validate(
        {
            "model": "claude-sonnet-4-6",
            "apiKey": "sk-test",
            "modelRates": WIRE_MODEL_RATES,
            "limits": WIRE_LIMITS,
            "taskId": "task-a",
            "language": "ko",
            "userId": "user-1",
            "completionCallback": _COMPLETION,
        }
    )


def _deps(chat: Any) -> RecipeDeps:
    api = FakeTracerApi(_EVENT_ROWS)
    return RecipeDeps(
        req=_request(),
        reader=RecipeLedgerReader(api),  # type: ignore[arg-type]
        search=RecipeSearchReader(api),  # type: ignore[arg-type]
        usage=ExecutionTrace(),
        caller=new_recipe_caller(ChatPair(chat, None)),  # type: ignore[arg-type]
        budget=ExecutionBudget(1.0, mk_rates()),
        prompts=build_prompt_bundle(RECIPE_SCAN_PROMPT),
        prompt=RECIPE_SCAN_PROMPT,
        language_directives=RECIPE_SCAN_PROMPT.language_directives,
    )


async def _probe(deps: RecipeDeps, task_id: str, catalog: ProvenanceCatalog) -> ProbeReport:
    result = await deps.invoke(
        budget=deps.new_loop("timeline"),
        system_prompt=deps.prompts.probe_system,
        catalog=catalog,
        tools=PROBE_TOOLS["timeline"],
        output=ProbeReport,
        messages=[HumanMessage(content=f"{_TASK_MARKER}{task_id}")],
        missing_response="no report",
        max_turns=6,
        call_id=f"probe:{task_id}",
    )
    return result.response


async def test_같은_역할의_전문가는_컴파일된_agent를_다시_쓴다() -> None:
    deps = _deps(_ReadingProbeChat())

    await _probe(deps, "task-a", ProvenanceCatalog())
    await _probe(deps, "task-b", ProvenanceCatalog())

    assert deps.caller.compiled_agents() == 1


async def test_도구_집합이_다른_역할은_자기_agent를_갖는다() -> None:
    deps = _deps(_ReadingProbeChat())

    await _probe(deps, "task-a", ProvenanceCatalog())
    await deps.invoke(
        budget=deps.new_loop("survey"),
        system_prompt=deps.prompts.survey_system,
        catalog=ProvenanceCatalog(),
        tools=SURVEY_TOOLS,
        output=DispatchPlan,
        messages=[HumanMessage(content=f"{_PLAN_MARKER} {_TASK_MARKER}task-a")],
        missing_response="no plan",
        max_turns=6,
        call_id="survey",
    )

    assert deps.caller.compiled_agents() == 2


async def test_전문가는_컴파일된_agent를_함께_써도_자기_장부만_쌓는다() -> None:
    deps = _deps(_ReadingProbeChat())
    first, second = ProvenanceCatalog(), ProvenanceCatalog()

    await _probe(deps, "task-a", first)
    await _probe(deps, "task-b", second)

    assert deps.caller.compiled_agents() == 1
    assert set(first.eventIdsByTask) == {"task-a"}
    assert set(second.eventIdsByTask) == {"task-b"}


async def test_병렬로_도는_전문가는_남이_읽은_이벤트를_인용하지_못한다() -> None:
    gate = asyncio.Barrier(2)
    deps = _deps(_ReadingProbeChat(gate))
    first, second = ProvenanceCatalog(), ProvenanceCatalog()

    reports = await asyncio.gather(
        _probe(deps, "task-a", first),
        _probe(deps, "task-b", second),
    )

    assert deps.caller.compiled_agents() == 1
    # 장부가 자기 태스크만 담으므로 검증은 남이 읽은 이벤트를 인용으로 받지 않는다.
    assert set(first.eventIdsByTask) == {"task-a"}
    assert set(second.eventIdsByTask) == {"task-b"}
    assert [report.probe for report in reports] == ["timeline", "timeline"]
