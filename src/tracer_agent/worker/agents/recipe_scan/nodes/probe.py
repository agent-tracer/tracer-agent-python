"""recipe-scan의 전문가 조사 노드를 제공한다."""

from __future__ import annotations

import asyncio
import logging

from langchain_core.messages import HumanMessage

from tracer_agent.shared.agents.recipe_scan.models import (
    MAX_VERDICT_CHARS,
    ProbeDispatch,
    ProbeReport,
    ProbeUpdate,
    ProvenanceCatalog,
)

from ...runtime.errors import exception_summary
from ...runtime.node import GraphNode
from ...runtime.telemetry.execution_metrics import record_probe_exhaustion
from ...runtime.timeouts import weighted_wall_clock_s
from ..deps import AGENT_NAME, RecipeDeps
from ..failures import WORKER_FAILED
from ..prompts import build_probe_prompt
from ..reservation import load_wall_clock_policy
from ..tools import PROBE_TOOLS

_log = logging.getLogger(__name__)


def _failure_verdict(exc: Exception) -> str:
    summary = exception_summary(exc)
    return WORKER_FAILED.format(reason=summary)[:MAX_VERDICT_CHARS]


class ProbeNode(GraphNode[ProbeDispatch, ProbeUpdate]):
    """맡은 질문 하나를 자기 도구와 자기 예산과 자기 장부로 조사하는 전문가를 만든다."""

    name = "probe"

    def __init__(self, deps: RecipeDeps, wall_clock_ceiling_s: float) -> None:
        self._deps = deps
        self._wall_clock_ceiling_s = wall_clock_ceiling_s

    async def run(self, payload: ProbeDispatch) -> ProbeUpdate:
        deps = self._deps
        req = deps.req
        assignment = payload.assignment
        # 장부를 전문가마다 새로 두어 다른 전문가가 읽은 것을 인용하지 못하게 한다.
        catalog = ProvenanceCatalog()
        # 턴을 하나도 못 받으면 재귀 한도가 0이 되어 그래프가 시작하지 못하므로 모델을 부르지 않는다.
        if payload.max_turns <= 0:
            report = ProbeReport(
                probe=assignment.probe,
                verdict=WORKER_FAILED.format(reason="no turns allocated")[:MAX_VERDICT_CHARS],
                excerpts=[],
                exhausted=True,
            )
            return {"reports": [report], "provenance": catalog, "model_cost_usd": 0.0, "model_turns_used": 0}
        budget = deps.new_loop(assignment.probe, max_cost_usd=payload.max_cost_usd)
        wall_clock_s = weighted_wall_clock_s(
            self._wall_clock_ceiling_s,
            payload.max_cost_usd,
            req.limits.budgetUsd,
            min_fraction=load_wall_clock_policy().probe_min_fraction,
        )
        # 취소(BaseException 계열)는 잡 전체를 멈추라는 신호이므로 잡지 않고 전파한다.
        try:
            result = await asyncio.wait_for(
                deps.invoke(
                    budget=budget,
                    system_prompt=deps.prompts.probe_system,
                    catalog=catalog,
                    tools=PROBE_TOOLS[assignment.probe],
                    output=ProbeReport,
                    messages=[
                        HumanMessage(
                            content=build_probe_prompt(
                                deps.prompt,
                                req.taskId,
                                assignment.question,
                                payload.max_turns,
                                payload.siblings,
                            )
                        )
                    ],
                    missing_response=f"{assignment.probe} probe produced no structured report",
                    max_turns=payload.max_turns,
                    call_id=deps.call_id(f"{self.name}:{assignment.probe}"),
                ),
                timeout=wall_clock_s,
            )
            report = result.response
            turns_used = result.num_turns
        except Exception as exc:
            _log.warning("probe %s failed: %s", assignment.probe, exc)
            report = ProbeReport(
                probe=assignment.probe,
                verdict=_failure_verdict(exc),
                excerpts=[],
                exhausted=True,
            )
            # 실패한 호출은 실제 턴을 모르므로 계약의 정산-무보고 규칙대로 배분받은 턴 전부를 쓴 것으로 본다.
            turns_used = payload.max_turns
        record_probe_exhaustion(AGENT_NAME, assignment.probe, budget.delta, payload.max_cost_usd)
        return {
            "reports": [report],
            "provenance": catalog,
            "model_cost_usd": budget.delta,
            "model_turns_used": turns_used,
        }
