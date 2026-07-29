"""task-cleanup의 후보 선별과 후보별 조사 노드를 제공한다."""

from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel

from tracer_agent.shared.agents.task_cleanup.models import (
    CLEANUP_REVIEWER_ROLE,
    MAX_INSPECT_REASON_CHARS,
    CleanupCandidate,
    InspectDispatch,
    InspectReport,
    InspectUpdate,
    TaskCleanupRequest,
    TaskCleanupState,
    TriagePlan,
    TriageUpdate,
)

from ...runtime.execution.trace import ExecutionTrace
from ...runtime.llm.budget import ExecutionBudget
from ...runtime.llm.standard_agent import StandardAgentContext
from ...runtime.llm.structured_agent import invoke_structured_agent, recursion_limit_for
from ...runtime.node import GraphNode
from ..failures import WORKER_FAILED
from ..langchain_agent import build_cleanup_agent
from ..prompts import (
    INSPECT_SYSTEM_PROMPT,
    TRIAGE_SYSTEM_PROMPT,
    build_inspect_prompt,
    build_triage_prompt,
)
from ..reader import CleanupLedgerReader
from ..tools import INSPECT_TOOL_NAMES, TRIAGE_TOOL_NAMES, build_cleanup_registry

_log = logging.getLogger(__name__)


def _failure_reason(exc: Exception) -> str:
    summary = str(exc).strip() or type(exc).__name__
    return WORKER_FAILED.format(reason=summary)[:MAX_INSPECT_REASON_CHARS]


class TriageNode(GraphNode):
    """조율자가 후보 목록만 보고 어느 것을 열어볼지 스스로 정하게 한다."""

    name = "triage"

    def __init__(
        self,
        req: TaskCleanupRequest,
        reader: CleanupLedgerReader,
        usage: ExecutionTrace,
        chat: BaseChatModel,
        fallback_chat: BaseChatModel | None,
        budget: ExecutionBudget,
        *,
        agent_name: str,
        system_prompt: str = TRIAGE_SYSTEM_PROMPT,
    ) -> None:
        self._req = req
        self._reader = reader
        self._usage = usage
        self._chat = chat
        self._fallback_chat = fallback_chat
        self._budget = budget
        self._agent_name = agent_name
        self._system_prompt = system_prompt

    async def run(self, _state: TaskCleanupState) -> TriageUpdate:
        req = self._req
        exposed: dict[str, CleanupCandidate] = {}
        event_ids: dict[str, set[str]] = {}
        triage_name = f"{self._agent_name}:triage"
        budget = self._budget.new_loop(triage_name, req.model)
        registry = build_cleanup_registry(
            self._reader, req.batch, exposed, event_ids, agent_name=self._agent_name
        )
        agent = build_cleanup_agent(
            self._chat,
            self._system_prompt,
            registry.langchain_tools(TRIAGE_TOOL_NAMES),
            registry.transient_errors(),
            output=TriagePlan,
            fallback_chat=self._fallback_chat,
            max_turns=self._req.limits.maxTurns,
        )
        result = await invoke_structured_agent(
            agent,
            messages=[
                {
                    "role": "user",
                    "content": build_triage_prompt(len(req.batch.candidates)),
                }
            ],
            context=StandardAgentContext(
                agent_name=triage_name,
                trace=self._usage,
                budget=budget,
                max_model_turns=self._req.limits.maxTurns,
            ),
            response_type=TriagePlan,
            recursion_limit=recursion_limit_for(self._req.limits.maxTurns),
            missing_response=f"{self._agent_name} triage produced no structured plan",
        )
        plan = result.response
        chosen = ", ".join(f"{item.taskId}:{item.weight}" for item in plan.assignments) or "없음"
        self._usage.record_graph_event(
            "route.selected",
            f"{self.name} -> {chosen}",
            node_name=self.name,
        )
        return {
            "plan": plan,
            "exposed_candidates": exposed,
            "event_ids_by_task": event_ids,
            "model_cost_usd": budget.delta,
        }


class InspectNode(GraphNode):
    """후보 하나를 자기 예산과 자기 장부로 열어보고 판정을 올린다."""

    name = "inspect"

    def __init__(
        self,
        req: TaskCleanupRequest,
        reader: CleanupLedgerReader,
        usage: ExecutionTrace,
        chat: BaseChatModel,
        fallback_chat: BaseChatModel | None,
        budget: ExecutionBudget,
        *,
        agent_name: str,
        system_prompt: str = INSPECT_SYSTEM_PROMPT,
    ) -> None:
        self._req = req
        self._reader = reader
        self._usage = usage
        self._chat = chat
        self._fallback_chat = fallback_chat
        self._budget = budget
        self._agent_name = agent_name
        self._system_prompt = system_prompt

    async def run(self, payload: InspectDispatch) -> InspectUpdate:
        req = self._req
        assignment = payload.assignment
        # 장부를 조사마다 새로 두어 다른 후보의 이벤트를 인용하지 못하게 한다.
        event_ids: dict[str, set[str]] = {}
        name = f"{self._agent_name}:{CLEANUP_REVIEWER_ROLE}"
        budget = self._budget.new_loop(name, req.model, max_cost_usd=payload.cost_budget)
        # 취소(BaseException 계열)는 잡 전체를 멈추라는 신호이므로 잡지 않고 전파한다.
        try:
            registry = build_cleanup_registry(
                self._reader, req.batch, {}, event_ids, agent_name=self._agent_name
            )
            agent = build_cleanup_agent(
                self._chat,
                self._system_prompt,
                registry.langchain_tools(INSPECT_TOOL_NAMES),
                registry.transient_errors(),
                output=InspectReport,
                fallback_chat=self._fallback_chat,
                max_turns=self._req.limits.maxTurns,
            )
            result = await invoke_structured_agent(
                agent,
                messages=[
                    {
                        "role": "user",
                        "content": build_inspect_prompt(assignment.taskId),
                    }
                ],
                context=StandardAgentContext(
                    agent_name=name,
                    trace=self._usage,
                    budget=budget,
                    max_model_turns=self._req.limits.maxTurns,
                ),
                response_type=InspectReport,
                recursion_limit=recursion_limit_for(self._req.limits.maxTurns),
                missing_response=f"{assignment.taskId} inspection produced no structured report",
            )
            report = result.response
        except Exception as exc:
            # 조사가 무너진 후보는 안전하게 보존하도록 보관 불가로 올린다.
            reason = _failure_reason(exc)
            _log.warning("inspect failed for %s: %s", assignment.taskId, exc)
            report = InspectReport(
                taskId=assignment.taskId,
                archivable=False,
                reason=reason,
                citedEventIds=[],
            )
        return {
            "reports": [report],
            "event_ids_by_task": event_ids,
            "model_cost_usd": budget.delta,
        }
