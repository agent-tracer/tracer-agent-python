"""도구 호출 관측이 실패한 호출을 성공으로 세지 않는지 검증한다."""

from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage

from tracer_agent.shared.agents.shared.models import AgentRunObservationDTO
from tracer_agent.worker.agents.runtime.execution.trace import (
    INCOMPLETE_TOOL_CALL,
    ExecutionTrace,
)


def _call(call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "get_task_events", "args": {"taskId": "t1"}, "id": call_id, "type": "tool_call"}
        ],
    )


def _observation(trace: ExecutionTrace) -> AgentRunObservationDTO:
    return trace.to_observation(
        execution_id="e1",
        attempt_id="1",
        job_id=None,
        agent_name="recipe-scan",
        model_requested="claude-haiku-4-5",
        prompt_version="v0.0.1",
        tool_contract_version="v0.0.1",
        duration_ms=10,
        ttft_ms=None,
        error_subtype=None,
    )


def _tool_calls(trace: ExecutionTrace) -> dict[str, tuple[str, str | None]]:
    return {call.toolCallId: (call.status, call.errorType) for call in _observation(trace).toolCalls}


def test_status_error여도_결과가_돌아온_호출은_성공으로_기록한다() -> None:
    # 모델에게 읽힐 도구 결과가 남았으면 status 값과 무관하게 호출은 완료로 본다.
    trace = ExecutionTrace()
    trace.record_message(_call("c1"))
    trace.record_message(
        ToolMessage(
            content="Tool get_task_events failed: 403.",
            name="get_task_events",
            tool_call_id="c1",
            status="error",
        )
    )

    assert _tool_calls(trace) == {"c1": ("succeeded", None)}


def test_결과를_돌려준_호출은_성공으로_기록한다() -> None:
    trace = ExecutionTrace()
    trace.record_message(_call("c1"))
    trace.record_message(ToolMessage(content='{"events": []}', name="get_task_events", tool_call_id="c1"))

    assert _tool_calls(trace) == {"c1": ("succeeded", None)}


def test_결과가_남지_않은_호출은_실행의_결말을_따른다() -> None:
    trace = ExecutionTrace()
    trace.record_message(_call("c1"))

    assert _tool_calls(trace) == {"c1": ("succeeded", None)}

    failed = ExecutionTrace()
    failed.record_message(_call("c2"))
    observed = failed.to_observation(
        execution_id="e1",
        attempt_id="1",
        job_id=None,
        agent_name="recipe-scan",
        model_requested="claude-haiku-4-5",
        prompt_version="v0.0.1",
        tool_contract_version="v0.0.1",
        duration_ms=10,
        ttft_ms=None,
        error_subtype="agent_execution_error",
    )

    assert [(one.status, one.errorType) for one in observed.toolCalls] == [("failed", INCOMPLETE_TOOL_CALL)]
