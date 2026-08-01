"""공용 execute() 래퍼의 데드라인·API 오류 분류를 검증한다."""

from __future__ import annotations

import asyncio

import httpx
from anthropic import AuthenticationError

from tests.support.prompts import CONTRACT_VERSION
from tracer_agent.worker.agents.runtime.execution.runner import execute


async def test_데드라인_초과를_deadline_exceeded로_잡는다() -> None:
    async def slow(_usage: object) -> dict[str, object]:
        await asyncio.sleep(5)
        return {}

    res = await execute(
        "slow",
        "claude-haiku-4-5",
        20,
        slow,
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )

    assert res.error is not None and res.error.subtype == "deadline_exceeded"


async def test_API_오류의_type을_그대로_노출한다() -> None:
    async def boom(_usage: object) -> dict[str, object]:
        response = httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com"))
        raise AuthenticationError("nope", response=response, body={"error": {"type": "authentication_error"}})

    res = await execute(
        "auth",
        "claude-haiku-4-5",
        5000,
        boom,
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )

    assert res.error is not None and res.error.subtype == "authentication_error"
