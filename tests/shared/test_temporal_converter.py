"""워크플로 연결이 페이로드를 도메인 모델 그대로 싣는 컨버터를 쓰는지 검증한다."""

from __future__ import annotations

from typing import Any

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter

from tracer_agent.shared import config as config_module
from tracer_agent.shared.config import Settings


def _settings() -> Settings:
    return Settings(
        agent_db_host="db",
        agent_db_port=5432,
        agent_db_name="agent",
        agent_db_user="app",
        agent_db_password="app-secret",
    )


async def test_연결이_pydantic_컨버터를_붙인다(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class RecordingClient:
        @staticmethod
        async def connect(address: str, **options: Any) -> object:
            seen["address"] = address
            seen.update(options)
            return object()

    monkeypatch.setattr(config_module, "Client", RecordingClient)

    await _settings().connect_temporal()

    assert seen["data_converter"] is pydantic_data_converter
