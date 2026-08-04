"""잡 종류 하나가 요청 조립과 그래프 실행과 산출물 배달을 한 벌로 내는 자리다."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel

from tracer_agent.shared.agents.shared.json_view import JsonObject
from tracer_agent.shared.agents.shared.models import AgentExecutionRequest
from tracer_agent.shared.workflows.jobs_kinds import AgentJobKind

from ..shared.prompt_source_port import AgentPrompt
from .checkpoint import GraphCheckpointProvider
from .durable_graph import DurableGraph, PriorSpend
from .execution.trace import ExecutionTrace
from .graph_session import GraphSession
from .llm.client import ChatPair, make_chat_pair
from .tracer_client import TracerApiPort
from .validation_graph import ValidationGraphContext


@dataclass(frozen=True)
class GraphRun[StateT]:
    """이 실행이 그래프에 넣을 초기 상태와 노드가 받을 요청별 의존성이다."""

    initial: StateT
    context: ValidationGraphContext[StateT]


class JobGraphAgent[RequestT: AgentExecutionRequest, StateT](ABC):
    """접수 payload를 자기 요청으로 세우고 그래프를 실행하며 산출물을 창구로 보내는 잡 하나다."""

    kind: ClassVar[AgentJobKind]
    topology: ClassVar[DurableGraph]
    # 이 에이전트의 단계가 실행되는 동안 그래프가 밟는 노드 수의 상한이다.
    recursion_limit: ClassVar[int]

    async def collect_context(self, payload: JsonObject, _tracer: TracerApiPort) -> JsonObject:
        """자격을 알지 못한 채 실행 입력에 도메인 문맥만 실어 내며 모을 것이 없으면 그대로 낸다."""
        return payload

    @abstractmethod
    async def prepare(self, payload: JsonObject, tracer: TracerApiPort) -> RequestT:
        """문맥과 봉투가 실린 입력으로 이 시도의 요청을 세운다."""

    async def settle_outputs(
        self, _tracer: TracerApiPort, _execution_id: str, _data: JsonObject | None
    ) -> None:
        """종결한 잡의 산출물을 배달하며 보낼 것이 없는 잡은 아무 창구도 부르지 않는다."""
        return

    async def run(
        self,
        req: RequestT,
        tracer: TracerApiPort,
        usage: ExecutionTrace,
        prompt: AgentPrompt,
        checkpoints: GraphCheckpointProvider | None = None,
        chats: ChatPair | None = None,
    ) -> JsonObject:
        """실행 하나를 열고 이 에이전트가 세운 노드로 그래프를 수행해 산출물을 낸다."""
        session = await GraphSession.open(
            self.topology,
            agent_name=self.kind,
            req=req,
            prompt_version=prompt.version(),
            recursion_limit=self.recursion_limit,
            checkpoints=checkpoints,
        )
        plan = self.compose(req, tracer, usage, prompt, chats or make_chat_pair(req), session.prior)
        return self.result_of(await session.invoke(plan.initial, plan.context))

    @abstractmethod
    def compose(
        self,
        req: RequestT,
        tracer: TracerApiPort,
        usage: ExecutionTrace,
        prompt: AgentPrompt,
        chats: ChatPair,
        prior: PriorSpend,
    ) -> GraphRun[StateT]:
        """이어받은 지출을 안은 예산과 노드와 초기 상태를 이 실행의 한 벌로 세운다."""

    @abstractmethod
    def result_of(self, final: dict[str, Any]) -> JsonObject:
        """그래프가 낸 마지막 상태를 이 잡의 산출물 계약으로 좁힌다."""


def dumped(result: BaseModel, *, exclude_none: bool = False) -> JsonObject:
    """산출물 모델을 창구가 받는 JSON 모양으로 만든다."""
    payload: JsonObject = result.model_dump(mode="json", exclude_none=exclude_none)
    return payload
