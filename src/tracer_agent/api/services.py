"""앱 수명이 세운 협력자를 한 칸에 담아 창구가 이름 대신 필드로 집게 한다."""

from __future__ import annotations

from dataclasses import dataclass

from ..shared.agents.chat.intake.cancel import UpdateSignal
from ..shared.agents.chat.intake.dispatch import ExecutionDispatch
from ..shared.agents.chat.surface.tool_client import ChatToolExecutor
from ..shared.agents.chat.surface.updates import ChatExecutionUpdates
from ..shared.agents.envelope.models import ModelCredentialSource
from ..shared.agents.runtime.ledger import SqlSource
from ..shared.agents.settings.secret import SettingCipher
from ..shared.workflows.jobs_anchor import ScanAnchorSource
from ..shared.workflows.jobs_dispatch import TemporalJobDispatch

# 창구가 협력자를 꺼내는 자리이며 `app.state`에는 이 칸 하나만 둔다.
SERVICES_ATTRIBUTE = "services"


@dataclass(frozen=True)
class AgentServices:
    """이 배포가 요청마다 빌려 주는 협력자 전부이며 앱 수명이 한 번 세운다."""

    execution_sql: SqlSource
    setting_cipher: SettingCipher
    model_credentials: ModelCredentialSource
    chat_tool_executor: ChatToolExecutor
    execution_dispatch: ExecutionDispatch
    job_dispatch: TemporalJobDispatch
    scan_anchors: ScanAnchorSource
    read_api_base_url: str
    execution_updates: UpdateSignal | None = None
    execution_watch: ChatExecutionUpdates | None = None
