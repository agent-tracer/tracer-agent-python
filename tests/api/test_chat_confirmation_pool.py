"""이 서비스로 되돌아오는 도구를 승인하는 요청이 원장 연결을 하나만 쥐는지 검증한다."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import httpx
import pytest

from tests.support.chat_surface import SingleSql, seed_pending_tool, seed_thread
from tests.support.services import fake_services
from tracer_agent.api import app as app_module
from tracer_agent.shared.agents.chat.surface.tool_client import HttpChatToolExecutor
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql

AGENT_BASE_URL = "http://agent-api"
TRACER_BASE_URL = "http://tracer-api"
DECIDE_PATH = "/api/agent/chat/threads/t1/confirmations/c1"
RESPONSE_TIMEOUT_S = 5.0

MEMORY_KEY = "lang"
MEMORY_CONTENT = "한국어를 쓴다"


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    seed_thread(ledger)
    seed_pending_tool(ledger, tool_name="remember_fact", args={"key": MEMORY_KEY, "content": MEMORY_CONTENT})
    yield ledger
    ledger.close()


async def test_이_서비스로_되돌아오는_도구를_승인해도_원장_연결이_마르지_않는다(
    store: SqliteLedgerSql,
) -> None:
    application = app_module.create_app()
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url=AGENT_BASE_URL) as client:
        application.state.services = fake_services(
            execution_sql=SingleSql(store, max_size=1),
            chat_tool_executor=HttpChatToolExecutor(client, TRACER_BASE_URL, AGENT_BASE_URL),
        )
        posted = client.post(DECIDE_PATH, json={"decision": "approve"})
        res = await asyncio.wait_for(posted, RESPONSE_TIMEOUT_S)

    assert res.status_code == 200
    assert res.json()["data"]["status"] == "approved"
    assert store.rows("chat_user_memories")[0]["content"] == MEMORY_CONTENT
