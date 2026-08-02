"""recipe-scan의 전문가 조사 노드를 제공한다."""

from __future__ import annotations

import asyncio
import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from tracer_agent.shared.agents.recipe_scan.models import (
    MAX_VERDICT_CHARS,
    ProbeDispatch,
    ProbeReport,
    ProbeUpdate,
    ProvenanceCatalog,
    RecipeScanRequest,
)

from ...runtime.execution.trace import ExecutionTrace
from ...runtime.llm.budget import ExecutionBudget
from ...runtime.llm.standard_agent import StandardAgentContext
from ...runtime.llm.structured_agent import invoke_structured_agent, recursion_limit_for
from ...runtime.node import GraphNode
from ...runtime.timeouts import weighted_wall_clock_s
from ...shared.prompt_source_port import AgentPrompt
from ..failures import WORKER_FAILED
from ..langchain_agent import build_recipe_agent
from ..prompts import build_probe_prompt
from ..reader import RecipeLedgerReader
from ..search import RecipeSearchReader
from ..tools import PROBE_TOOLS, build_recipe_registry

_log = logging.getLogger(__name__)


def _failure_verdict(exc: Exception) -> str:
    summary = str(exc).strip() or type(exc).__name__
    return WORKER_FAILED.format(reason=summary)[:MAX_VERDICT_CHARS]


class ProbeNode(GraphNode[ProbeDispatch, ProbeUpdate]):
    """맡은 질문 하나를 자기 도구와 자기 예산과 자기 장부로 조사하는 전문가를 만든다."""

    name = "probe"

    def __init__(
        self,
        req: RecipeScanRequest,
        reader: RecipeLedgerReader,
        search: RecipeSearchReader,
        usage: ExecutionTrace,
        chat: BaseChatModel,
        fallback_chat: BaseChatModel | None,
        budget: ExecutionBudget,
        *,
        agent_name: str,
        system_prompt: str,
        wall_clock_ceiling_s: float,
        prompt: AgentPrompt,
    ) -> None:
        self._req = req
        self._reader = reader
        self._search = search
        self._usage = usage
        self._chat = chat
        self._fallback_chat = fallback_chat
        self._budget = budget
        self._agent_name = agent_name
        self._system_prompt = system_prompt
        self._wall_clock_ceiling_s = wall_clock_ceiling_s
        self._prompt = prompt

    async def run(self, payload: ProbeDispatch) -> ProbeUpdate:
        req = self._req
        assignment = payload.assignment
        # 장부를 전문가마다 새로 두어 다른 전문가가 읽은 것을 인용하지 못하게 한다.
        catalog = ProvenanceCatalog()
        probe_name = f"{self._agent_name}:{assignment.probe}"
        # 턴을 하나도 못 받으면 재귀 한도가 0이 되어 그래프가 시작하지 못하므로 모델을 부르지 않는다.
        if payload.max_turns <= 0:
            report = ProbeReport(
                probe=assignment.probe,
                verdict=WORKER_FAILED.format(reason="no turns allocated")[:MAX_VERDICT_CHARS],
                excerpts=[],
                exhausted=True,
            )
            return {"reports": [report], "provenance": catalog, "model_cost_usd": 0.0, "model_turns_used": 0}
        budget = self._budget.new_loop(probe_name, req.model, max_cost_usd=payload.max_cost_usd)
        wall_clock_s = weighted_wall_clock_s(
            self._wall_clock_ceiling_s, payload.max_cost_usd, req.limits.budgetUsd
        )
        # 취소(BaseException 계열)는 잡 전체를 멈추라는 신호이므로 잡지 않고 전파한다.
        try:
            registry = build_recipe_registry(
                self._reader,
                self._search,
                catalog,
                PROBE_TOOLS[assignment.probe],
                agent_name=self._agent_name,
            )
            agent = build_recipe_agent(
                self._chat,
                self._system_prompt,
                registry.langchain_tools(),
                registry.transient_errors(),
                output=ProbeReport,
                fallback_chat=self._fallback_chat,
                max_turns=payload.max_turns,
            )
            result = await asyncio.wait_for(
                invoke_structured_agent(
                    agent,
                    messages=[
                        HumanMessage(
                            content=build_probe_prompt(
                                self._prompt,
                                req.taskId,
                                assignment.question,
                                payload.max_turns,
                                payload.siblings,
                            )
                        )
                    ],
                    context=StandardAgentContext(
                        agent_name=probe_name,
                        trace=self._usage,
                        budget=budget,
                        max_model_turns=payload.max_turns,
                    ),
                    response_type=ProbeReport,
                    recursion_limit=recursion_limit_for(payload.max_turns),
                    missing_response=f"{assignment.probe} probe produced no structured report",
                ),
                timeout=wall_clock_s,
            )
            report = result.response
            turns_used = result.num_turns
        except Exception as exc:
            verdict = _failure_verdict(exc)
            _log.warning("probe %s failed: %s", assignment.probe, exc)
            report = ProbeReport(
                probe=assignment.probe,
                verdict=verdict,
                excerpts=[],
                exhausted=True,
            )
            # 무너진 호출은 실제 턴을 모르므로 계약의 정산-무보고 규칙대로 배분받은 턴 전부를 쓴 것으로 본다.
            turns_used = payload.max_turns
        return {
            "reports": [report],
            "provenance": catalog,
            "model_cost_usd": budget.delta,
            "model_turns_used": turns_used,
        }
