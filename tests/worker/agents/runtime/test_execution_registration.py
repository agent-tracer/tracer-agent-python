"""실행 등록이 프로세스 상태를 남기지 않고 취소를 그대로 전파하는지 검증한다."""

from __future__ import annotations

import asyncio

import pytest

from tests.support.fakes import mk_ai
from tests.support.prompts import CONTRACT_VERSION
from tracer_agent.shared.agents.shared.json_view import JsonObject
from tracer_agent.shared.agents.shared.models import ModelRateDTO
from tracer_agent.worker.agents.runtime.execution.runner import ExecutionRequest, execute
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace


def _request(**overrides: object) -> ExecutionRequest:
    fields: dict[str, object] = {
        "label": "title-suggestion",
        "model": "model",
        "deadline_ms": 1_000,
        "prompt_version": CONTRACT_VERSION,
        "tool_contract_version": CONTRACT_VERSION,
    }
    fields.update(overrides)
    return ExecutionRequest(**fields)  # type: ignore[arg-type]


class TestNoProcessState:
    async def test_같은_이름의_실행을_두_번_부르면_본체도_두_번_돈다(self) -> None:
        calls = 0

        async def body(_trace: ExecutionTrace) -> JsonObject:
            nonlocal calls
            calls += 1
            return {"ok": True}

        first = await execute(_request(job_id="job-a"), body)
        second = await execute(_request(job_id="job-a"), body)

        assert calls == 2
        assert first.data == second.data == {"ok": True}

    async def test_먼저_끝난_실행이_뒤에_오는_실행의_답을_대신하지_않는다(self) -> None:
        answers = iter([{"turn": 1}, {"turn": 2}])

        async def body(_trace: ExecutionTrace) -> JsonObject:
            return dict(next(answers))

        first = await execute(_request(execution_id="e1", attempt_id="1"), body)
        second = await execute(_request(execution_id="e1", attempt_id="2"), body)

        assert first.data == {"turn": 1}
        assert second.data == {"turn": 2}


class TestTraceOwnership:
    async def test_호출자가_넘긴_궤적에_실행이_그대로_쌓는다(self) -> None:
        trace = ExecutionTrace()

        async def body(running: ExecutionTrace) -> JsonObject:
            running.mark_first_token()
            return {}

        await execute(_request(execution_id="e1"), body, trace)

        assert trace.first_token_at is not None

    async def test_궤적을_넘기지_않으면_실행이_스스로_하나를_연다(self) -> None:
        async def body(running: ExecutionTrace) -> JsonObject:
            running.mark_first_token()
            return {}

        response = await execute(_request(execution_id="e1"), body)

        assert response.observation is not None
        assert response.observation.ttftMs is not None

    async def test_단가와_사용량이_있으면_실행과_모델_호출에_같은_비용을_싣는다(self) -> None:
        async def body(running: ExecutionTrace) -> JsonObject:
            running.add_message(mk_ai(response_metadata={"model_name": "model"}))
            return {}

        response = await execute(
            _request(
                execution_id="e1",
                model_rates={"model": ModelRateDTO(input=1, output=5, cacheWrite=1.25, cacheRead=0.1)},
            ),
            body,
        )

        assert response.observation is not None
        assert response.observation.costUsd == 0.000292
        assert [call.costUsd for call in response.observation.modelCalls] == [0.000292]


class TestCancellation:
    async def test_실행_식별자가_없는_실행의_취소는_그대로_재전파한다(self) -> None:
        started = asyncio.Event()

        async def body(_trace: ExecutionTrace) -> JsonObject:
            started.set()
            await asyncio.Event().wait()  # 취소로만 끝나도록 영원히 대기한다.
            return {}

        task = asyncio.ensure_future(execute(_request(job_id="job-cancel"), body))
        await started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_실행_식별자가_있으면_취소를_cancelled_응답으로_접는다(self) -> None:
        started = asyncio.Event()

        async def body(_trace: ExecutionTrace) -> JsonObject:
            started.set()
            await asyncio.Event().wait()
            return {}

        task = asyncio.ensure_future(execute(_request(execution_id="e1"), body))
        await started.wait()
        task.cancel()

        response = await task
        assert response.error is not None and response.error.subtype == "cancelled"
