"""잡 종류 하나가 요청 조립과 그래프 실행과 산출물 배달을 한 벌로 내는 자리다."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from tracer_agent.shared.agents.shared.json_view import JsonObject, JsonValue
from tracer_agent.shared.agents.shared.models import AgentExecutionRequest
from tracer_agent.shared.workflows.jobs_kinds import AgentJobKind

from ..shared.prompt_source_port import AgentPrompt
from .checkpoint import GraphCheckpointProvider
from .execution.trace import ExecutionTrace
from .llm.client import ChatPair
from .tracer_client import TracerApiClient

type JobPrepare[RequestT] = Callable[[JsonObject, TracerApiClient], Awaitable[RequestT]]
type JobRun[RequestT] = Callable[
    [RequestT, TracerApiClient, ExecutionTrace, AgentPrompt, GraphCheckpointProvider | None, ChatPair | None],
    Awaitable[dict[str, JsonValue]],
]
type JobDeliver = Callable[[TracerApiClient, str, JsonObject], Awaitable[None]]


@dataclass(frozen=True)
class JobAgent[RequestT: AgentExecutionRequest]:
    """접수 payload를 자기 요청으로 세우고 그래프를 돌리며 산출물을 창구로 보내는 잡 하나다."""

    kind: AgentJobKind
    prepare: JobPrepare[RequestT]
    run: JobRun[RequestT]
    deliver: JobDeliver | None = None

    async def settle_outputs(
        self, tracer: TracerApiClient, execution_id: str, data: JsonObject | None
    ) -> None:
        """종결한 잡의 산출물을 배달하며 보낼 것이 없는 잡은 아무 창구도 부르지 않는다."""
        if self.deliver is None or not data:
            return
        await self.deliver(tracer, execution_id, data)
