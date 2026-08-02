"""실행 궤적에서 근거 수집과 중복 조사를 재는 지표를 검증한다(모델 호출 없음)."""

from __future__ import annotations

from typing import Any

from tracer_agent.shared.agents.shared.models import AgentStepDTO, AgentStepToolCall
from tracer_agent.worker.agents.runtime.telemetry.trajectory_metrics import (
    called_tool_names,
    covers_expected_calls,
    duplicate_tool_calls,
    missing_tool_calls,
)


def _call(name: str, **args: Any) -> AgentStepToolCall:
    return AgentStepToolCall(id=f"c-{name}", name=name, args=dict(args))


def _step(*calls: AgentStepToolCall) -> AgentStepDTO:
    return AgentStepDTO(seq=1, role="assistant", content="", truncated=False, toolCalls=list(calls))


class Test궤적지표:
    def test_같은_도구를_같은_인자로_다시_부른_횟수를_센다(self) -> None:
        steps = [_step(_call("search_events", q="배포")), _step(_call("search_events", q="배포"))]

        assert duplicate_tool_calls(steps) == 1

    def test_인자가_다르면_같은_도구여도_중복으로_세지_않는다(self) -> None:
        steps = [_step(_call("search_events", q="배포")), _step(_call("search_events", q="시험"))]

        assert duplicate_tool_calls(steps) == 0

    def test_인자의_선언_순서가_달라도_같은_호출로_본다(self) -> None:
        steps = [
            _step(_call("search_events", q="배포", limit=5)),
            _step(_call("search_events", limit=5, q="배포")),
        ]

        assert duplicate_tool_calls(steps) == 1

    def test_기대한_도구가_모두_나왔는지_순서와_무관하게_본다(self) -> None:
        steps = [_step(_call("get_task_events")), _step(_call("check_citations"))]

        assert covers_expected_calls(steps, ["check_citations", "get_task_events"])

    def test_빠뜨린_도구를_이름으로_낸다(self) -> None:
        steps = [_step(_call("get_task_events"))]

        assert missing_tool_calls(steps, ["get_task_events", "check_citations"]) == ["check_citations"]

    def test_부른_도구_이름을_중복_없이_모은다(self) -> None:
        steps = [_step(_call("a"), _call("b")), _step(_call("a"))]

        assert called_tool_names(steps) == {"a", "b"}
