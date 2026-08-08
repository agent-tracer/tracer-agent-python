"""창구 테스트가 앱 수명 대신 세우는 협력자 한 칸을 만든다."""

from __future__ import annotations

from typing import Any

from tests.support.chat_surface import RecordingDispatch, RecordingExecutor, SilentWatch
from tests.support.fakes import FakeScanAnchors
from tracer_agent.api.services import AgentServices
from tracer_agent.shared.agents.settings.secret import SettingCipher
from tracer_agent.shared.config import MonitorProfile

READ_API_BASE_URL = "http://tracer-api:3902"
AGENT_API_BASE_URL = "http://agent-api:8800"


class UnreachableSql:
    """빌려 줄 원장을 세우지 않은 시험이 그 사실을 곧바로 알게 한다."""

    def connect(self) -> Any:
        raise AssertionError("이 시험은 실행 원장을 세우지 않았다")


class SilentCredentials:
    """자격을 묻지 않는 시험에서 아무 자격도 내주지 않는다."""

    async def api_key(self, _user_id: str) -> str | None:
        return None

    async def chosen_model(self, _user_id: str) -> str | None:
        return None


class SilentJobDispatch:
    """잡 워크플로에 붙지 않고 기동과 취소를 삼킨다."""

    async def start(self, _kind: Any, _key: str, _payload: dict[str, Any]) -> None:
        """보낼 창구가 없으므로 아무 곳에도 신호하지 않는다."""

    async def cancel(self, _kind: Any, _key: str) -> bool:
        """보낼 창구가 없으므로 끊지 못했다고 낸다."""
        return False


def fake_services(**overrides: Any) -> AgentServices:
    """시험이 정한 협력자만 갈아 끼운 앱 협력자 한 칸을 낸다."""
    fields: dict[str, Any] = {
        "execution_sql": UnreachableSql(),
        "setting_cipher": SettingCipher(None, MonitorProfile.LOCAL),
        "model_credentials": SilentCredentials(),
        "chat_tool_executor": RecordingExecutor(),
        "execution_dispatch": RecordingDispatch(),
        "job_dispatch": SilentJobDispatch(),
        "scan_anchors": FakeScanAnchors(),
        "read_api_base_url": READ_API_BASE_URL,
        "agent_api_base_url": AGENT_API_BASE_URL,
        "execution_updates": None,
        "execution_watch": SilentWatch(),
    }
    fields.update(overrides)
    return AgentServices(**fields)
