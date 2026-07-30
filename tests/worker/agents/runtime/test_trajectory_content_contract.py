"""궤적 한 줄의 본문이 계약이 적은 값 규칙을 그대로 지키는지 검증한다."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tests.support.contract import conformance_case
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.trajectory import MAX_STEP_CONTENT_BYTES

_RULE: dict[str, Any] = conformance_case("chat.query")["valueRules"]["stepContent"]


class Test궤적_본문_규칙:
    def test_상한이_계약이_적은_바이트_수와_같다(self) -> None:
        assert _RULE["maxBytes"] == MAX_STEP_CONTENT_BYTES

    def test_블록_배열은_계약의_예시_그대로_평문이_된다(self) -> None:
        example = _RULE["example"]
        trace = ExecutionTrace()

        trace.record_message(AIMessage(content=list(example["blocks"])))

        assert trace.steps[0].content == example["content"]

    def test_도구_호출_블록은_본문이_아니라_tool_calls로_간다(self) -> None:
        trace = ExecutionTrace()

        trace.record_message(
            AIMessage(
                content=[
                    {"type": "text", "text": "먼저 요약을 읽는다"},
                    {"type": "tool_use", "id": "c1", "name": "get_task_summary", "input": {"taskId": "t1"}},
                ],
                tool_calls=[
                    {"name": "get_task_summary", "args": {"taskId": "t1"}, "id": "c1", "type": "tool_call"}
                ],
            )
        )

        step = trace.steps[0]
        assert step.content == "먼저 요약을 읽는다"
        assert [call.name for call in step.toolCalls] == ["get_task_summary"]

    def test_이을_텍스트가_없고_도구_호출도_없으면_seq를_쓰지_않고_버린다(self) -> None:
        trace = ExecutionTrace()

        trace.record_message(AIMessage(content=[{"type": "thinking", "thinking": "속으로만"}]))
        trace.record_message(HumanMessage(content="   "))
        trace.record_message(AIMessage(content="4"))

        assert [(step.seq, step.content) for step in trace.steps] == [(0, "4")]

    def test_도구_결과_줄의_본문은_그_도구가_낸_텍스트다(self) -> None:
        trace = ExecutionTrace()

        trace.record_message(
            ToolMessage(
                content=[{"type": "text", "text": '{"events": []}'}],
                tool_call_id="c1",
                name="get_task_events",
            )
        )

        assert trace.steps[0].content == '{"events": []}'

    def test_상한을_넘겨도_여러_바이트_글자를_가운데서_자르지_않는다(self) -> None:
        trace = ExecutionTrace()

        trace.record_message(HumanMessage(content="가" * MAX_STEP_CONTENT_BYTES))

        step = trace.steps[0]
        assert step.truncated is True
        assert len(step.content.encode("utf-8")) <= MAX_STEP_CONTENT_BYTES
        assert step.content == "가" * (MAX_STEP_CONTENT_BYTES // 3)
