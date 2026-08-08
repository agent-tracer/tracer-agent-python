"""공용 execute() 래퍼가 데드라인과 API 오류를 어떤 서브타입으로 분류하는지 검증한다."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from anthropic import AuthenticationError

from tests.support.fakes import mk_ai
from tests.support.prompts import CONTRACT_VERSION
from tracer_agent.worker.agents.runtime.execution import runner as runner_mod
from tracer_agent.worker.agents.runtime.execution.runner import ExecutionRequest, execute
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace


async def test_데드라인_초과를_deadline_exceeded로_잡는다() -> None:
    async def slow(_usage: object) -> dict[str, object]:
        await asyncio.sleep(5)
        return {}

    res = await execute(
        ExecutionRequest(
            label="slow",
            model="claude-haiku-4-5",
            deadline_ms=20,
            prompt_version=CONTRACT_VERSION,
            tool_contract_version=CONTRACT_VERSION,
        ),
        slow,
    )

    assert res.error is not None and res.error.subtype == "deadline_exceeded"


async def test_API_오류의_type을_그대로_노출한다() -> None:
    async def boom(_usage: object) -> dict[str, object]:
        response = httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com"))
        raise AuthenticationError("nope", response=response, body={"error": {"type": "authentication_error"}})

    res = await execute(
        ExecutionRequest(
            label="auth",
            model="claude-haiku-4-5",
            deadline_ms=5000,
            prompt_version=CONTRACT_VERSION,
            tool_contract_version=CONTRACT_VERSION,
        ),
        boom,
    )

    assert res.error is not None and res.error.subtype == "authentication_error"


async def test_대체_모델이_답한_실행의_계측은_실제_모델을_응답_모델로_낸다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[tuple[str, str | None]] = []

    def capture(
        request_model: str,
        response_model: str | None,
        _duration_seconds: float,
        _usage: object,
        _error_subtype: str | None,
    ) -> None:
        recorded.append((request_model, response_model))

    monkeypatch.setattr(runner_mod, "record_client_metrics", capture)
    trace = ExecutionTrace()

    async def answered(run_trace: ExecutionTrace) -> dict[str, object]:
        run_trace.add_message(mk_ai(response_metadata={"model": "claude-haiku-4-5"}))
        return {}

    res = await execute(
        ExecutionRequest(
            label="fallback",
            model="claude-sonnet-4-6",
            deadline_ms=5000,
            prompt_version=CONTRACT_VERSION,
            tool_contract_version=CONTRACT_VERSION,
        ),
        answered,
        trace,
    )

    assert res.actualModel == "claude-haiku-4-5"
    assert recorded == [("claude-sonnet-4-6", "claude-haiku-4-5")]
