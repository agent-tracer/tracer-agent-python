"""ExecutionTrace의 사용량 집계와 실행 궤적 기록을 검증한다."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from tracer_agent.shared.agents.shared.models import ModelRateDTO
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.trajectory import MAX_STEP_CONTENT_BYTES
from tracer_agent.worker.agents.runtime.pricing import ModelRates


def _ai_with_usage(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    cache_creation: int = 0,
    cache_creation_subkeys: dict[str, int] | None = None,
    response_metadata: dict[str, object] | None = None,
    content: str = "assistant turn",
) -> AIMessage:
    details: dict[str, int] = {"cache_read": cache_read, "cache_creation": cache_creation}
    if cache_creation_subkeys:
        details.update(cache_creation_subkeys)
    return AIMessage(
        content=content,
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_token_details": details,
        },
        response_metadata=response_metadata or {},
    )


class TestExecutionTrace:
    def test_input_tokens에서_캐시분을_빼서_베이스만_남긴다(self) -> None:
        # langchain의 usage_metadata.input_tokens는 캐시 읽기·생성분까지 합산된 총량이라 그대로 누적하면 pricing이 같은 토큰을 두 번 매기므로 여기서 뺀다.
        acc = ExecutionTrace()
        acc.add_message(
            _ai_with_usage(input_tokens=1000, output_tokens=50, cache_read=200, cache_creation=100)
        )

        assert acc.input_tokens == 700  # 1000 - 200 - 100
        assert acc.cache_read_tokens == 200
        assert acc.cache_creation_tokens == 100
        assert acc.output_tokens == 50

    def test_캐시가_없으면_input_tokens를_그대로_쓴다(self) -> None:
        acc = ExecutionTrace()
        acc.add_message(_ai_with_usage(input_tokens=500, output_tokens=20))

        assert acc.input_tokens == 500
        assert acc.cache_read_tokens == 0
        assert acc.cache_creation_tokens == 0

    def test_ephemeral_서브키로_온_캐시_생성분도_잡는다(self) -> None:
        # cache_creation이 5분/1시간으로 세분화되면 generic 값은 0이 되고 실제 값은 ephemeral_5m/1h_input_tokens로 옮겨간다.
        acc = ExecutionTrace()
        acc.add_message(
            _ai_with_usage(
                input_tokens=1000,
                output_tokens=10,
                cache_read=0,
                cache_creation=0,
                cache_creation_subkeys={"ephemeral_5m_input_tokens": 300},
            )
        )

        assert acc.cache_creation_tokens == 300
        assert acc.input_tokens == 700  # 1000 - 0 - 300

    def test_여러_턴이_누적된다(self) -> None:
        acc = ExecutionTrace()
        acc.add_message(
            _ai_with_usage(input_tokens=1000, output_tokens=50, cache_read=200, cache_creation=100)
        )
        acc.add_message(_ai_with_usage(input_tokens=900, output_tokens=40, cache_read=800, cache_creation=0))

        assert acc.input_tokens == 700 + 100  # (1000-200-100) + (900-800-0)
        assert acc.cache_read_tokens == 1000
        assert acc.cache_creation_tokens == 100

    def test_캐시_토큰이_베이스_단가로_이중과금되지_않는다(self) -> None:
        # 캐시를 포함한 총량을 베이스 단가에 적용하면 비용이 부풀어 BudgetExceeded를 조기에 발화시킨다.
        acc = ExecutionTrace()
        acc.add_message(
            _ai_with_usage(input_tokens=100_000, output_tokens=0, cache_read=99_000, cache_creation=0)
        )
        usage = acc.to_usage_dto()
        assert usage is not None

        rates = ModelRates(
            {"claude-sonnet-4-6": ModelRateDTO(input=3.0, output=15.0, cacheWrite=3.75, cacheRead=0.3)}
        )
        cost = rates.estimate_cost_usd("claude-sonnet-4-6", usage)
        assert cost is not None
        expected = (1_000 * 3.0 + 99_000 * 0.30) / 1_000_000
        assert cost == round(expected, 6)
        # 캐시 토큰을 중복 반영한 비용보다 작아야 한다.
        assert cost < (100_000 * 3.0) / 1_000_000


class TestRecordStep:
    def test_메시지_타입별로_role을_매핑한다(self) -> None:
        acc = ExecutionTrace()
        acc.record_message(SystemMessage(content="system prompt"))
        acc.record_message(HumanMessage(content="user turn"))
        acc.record_message(_ai_with_usage(input_tokens=100, output_tokens=10))
        acc.record_message(ToolMessage(content="tool result", tool_call_id="call-1", name="get_task_summary"))

        assert [step.role for step in acc.steps] == ["system", "user", "assistant", "tool"]
        assert [step.seq for step in acc.steps] == [0, 1, 2, 3]

    def test_tool_message는_toolName과_toolCallId를_남긴다(self) -> None:
        acc = ExecutionTrace()
        acc.record_message(ToolMessage(content="{}", tool_call_id="call-1", name="search_recipes"))

        step = acc.steps[0]
        assert step.toolName == "search_recipes"
        assert step.toolCallId == "call-1"

    def test_ai_message의_tool_calls를_기록한다(self) -> None:
        acc = ExecutionTrace()
        acc.record_message(
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "get_task_summary", "args": {"taskId": "t1"}, "id": "c1", "type": "tool_call"}
                ],
            )
        )

        step = acc.steps[0]
        assert len(step.toolCalls) == 1
        assert step.toolCalls[0].name == "get_task_summary"
        assert step.toolCalls[0].args == {"taskId": "t1"}
        assert step.toolCalls[0].id == "c1"

    def test_ai_message의_토큰과_stop_reason을_기록한다(self) -> None:
        acc = ExecutionTrace()
        message = _ai_with_usage(input_tokens=1000, output_tokens=50, cache_read=200, cache_creation=0)
        message.response_metadata["stop_reason"] = "end_turn"

        acc.record_message(message)

        step = acc.steps[0]
        assert step.inputTokens == 800  # 1000 - 200
        assert step.outputTokens == 50
        assert step.cacheReadTokens == 200
        assert step.stopReason == "end_turn"

    def test_상한을_넘는_콘텐츠는_바이트_기준으로_잘리고_truncated가_켜진다(self) -> None:
        acc = ExecutionTrace()
        acc.record_message(HumanMessage(content="가" * (MAX_STEP_CONTENT_BYTES)))

        step = acc.steps[0]
        assert step.truncated is True
        assert len(step.content.encode("utf-8")) <= MAX_STEP_CONTENT_BYTES

    def test_상한_이내_콘텐츠는_잘리지_않는다(self) -> None:
        acc = ExecutionTrace()
        acc.record_message(HumanMessage(content="short"))

        step = acc.steps[0]
        assert step.truncated is False
        assert step.content == "short"

    def test_블록_배열_content는_텍스트만_이어_붙인다(self) -> None:
        acc = ExecutionTrace()
        acc.record_message(SystemMessage(content=[{"type": "text", "text": "hi"}]))

        step = acc.steps[0]
        assert step.content == "hi"
        assert step.role == "system"

    def test_실제_응답_모델과_요청_식별자를_기록한다(self) -> None:
        acc = ExecutionTrace()
        message = _ai_with_usage(
            input_tokens=10,
            output_tokens=5,
            response_metadata={"model": "claude-sonnet-4-6-20260101", "id": "msg_abc"},
        )

        acc.record_message(message)

        assert acc.actual_model == "claude-sonnet-4-6-20260101"
        assert acc.provider_request_id == "msg_abc"

    def test_실패_직전_기록된_스텝만_있어도_실제_모델을_잡는다(self) -> None:
        # max_tokens 절단처럼 record_message()만 호출된 뒤 예외가 나는 경로에서도 실제 모델이 유실되지 않아야 한다.
        acc = ExecutionTrace()
        truncated = _ai_with_usage(
            input_tokens=10,
            output_tokens=5,
            response_metadata={
                "model": "claude-haiku-4-5-20251015",
                "id": "msg_truncated",
                "stop_reason": "max_tokens",
            },
        )

        acc.record_message(truncated)

        assert acc.actual_model == "claude-haiku-4-5-20251015"
        assert acc.provider_request_id == "msg_truncated"
