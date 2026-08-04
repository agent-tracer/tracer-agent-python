"""잡 그래프 실행 하나가 쓰는 그래프와 설정과 이어받은 지출을 한 객체로 닫는다."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver

from tracer_agent.shared.agents.shared.models import AgentExecutionRequest

from .checkpoint import GraphCheckpointProvider
from .durable_graph import (
    CompiledGraph,
    DurableGraph,
    PriorSpend,
    execution_config,
    job_durability,
    prior_spend,
    resume_input,
)
from .telemetry.disclosure import TraceSafeMetadata


class GraphSession:
    """이어받을 실행이면 앞선 시도의 지출을 안고 열리고, 아니면 처음부터 여는 실행 하나다."""

    def __init__(
        self,
        graph: CompiledGraph,
        config: RunnableConfig,
        prior: PriorSpend,
        saver: BaseCheckpointSaver[Any] | None,
    ) -> None:
        self._graph = graph
        self._config = config
        self._prior = prior
        self._saver = saver

    @classmethod
    async def open(
        cls,
        topology: DurableGraph,
        *,
        agent_name: str,
        req: AgentExecutionRequest,
        prompt_version: str,
        recursion_limit: int,
        checkpoints: GraphCheckpointProvider | None,
    ) -> GraphSession:
        """이 실행이 밟을 그래프를 열고 이어받을 체크포인트가 있으면 그 지출을 함께 읽는다."""
        # 열쇠를 모르면 이어받을 자리가 없으므로 그 실행은 보존하지 않는다.
        resume_key = req.executionId or req.jobId
        saver = None if checkpoints is None or resume_key is None else await checkpoints.saver()
        graph = topology.compiled(saver)
        config = execution_config(
            recursion_limit,
            TraceSafeMetadata(
                agent_name=agent_name,
                model_requested=req.model,
                prompt_version=prompt_version,
                job_id=req.jobId,
            ),
            resume_key,
        )
        return cls(graph, config, await prior_spend(graph, config, saver), saver)

    @property
    def prior(self) -> PriorSpend:
        """앞선 시도가 이미 쓴 달러와 턴이며 예산이 이 값을 안고 열린다."""
        return self._prior

    async def invoke(self, initial: Any, context: Any) -> dict[str, Any]:
        """이어갈 상태가 있으면 상태를 다시 넣지 않고 끝난 노드부터 건너뛴다."""
        final: dict[str, Any] = await self._graph.ainvoke(
            resume_input(initial, self._prior),
            context=context,
            config=self._config,
            durability=job_durability(self._saver),
        )
        return final
