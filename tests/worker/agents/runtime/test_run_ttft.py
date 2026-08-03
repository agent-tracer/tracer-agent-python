"""첫 토큰까지의 시간을 잰 실행만 그 값을 관측에 싣는지 검증한다."""

from __future__ import annotations

from tests.support.prompts import CONTRACT_VERSION
from tracer_agent.shared.agents.shared.json_view import JsonObject
from tracer_agent.worker.agents.runtime.execution.runner import execute
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace


async def _observation(execution_id: str, streams: bool) -> object:
    async def body(trace: ExecutionTrace) -> JsonObject:
        if streams:
            trace.mark_first_token()
        return {}

    response = await execute(
        "chat",
        "claude-haiku-4-5",
        5000,
        body,
        execution_id=execution_id,
        attempt_id="1",
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )
    return response.observation


class TestRunTtft:
    async def test_첫_조각을_받은_실행은_그_시각까지의_밀리초를_싣는다(self) -> None:
        observation = await _observation("streamed", streams=True)

        assert observation is not None
        assert observation.ttftMs is not None  # type: ignore[attr-defined]
        assert observation.ttftMs >= 0  # type: ignore[attr-defined]

    async def test_조각을_받지_못한_실행은_그_칸을_비운다(self) -> None:
        observation = await _observation("whole", streams=False)

        assert observation is not None
        assert observation.ttftMs is None  # type: ignore[attr-defined]


class TestFirstTokenMark:
    def test_첫_조각의_시각을_한_번만_남긴다(self) -> None:
        trace = ExecutionTrace()

        trace.mark_first_token()
        first = trace.first_token_at
        trace.mark_first_token()

        assert trace.first_token_at == first

    def test_조각을_받지_않으면_시각이_없다(self) -> None:
        assert ExecutionTrace().first_token_at is None
