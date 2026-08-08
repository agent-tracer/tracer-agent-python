"""task-cleanup의 후보 선별과 후보별 조사 노드를 제공한다."""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage

from tracer_agent.shared.agents.task_cleanup.models import (
    CLEANUP_REVIEWER_ROLE,
    MAX_INSPECT_REASON_CHARS,
    CleanupCandidate,
    InspectDispatch,
    InspectReport,
    InspectUpdate,
    TaskCleanupState,
    TriagePlan,
    TriageUpdate,
)

from ...runtime.errors import exception_summary
from ...runtime.llm.budget import AgentBudgetLease, pool_spend, reserved_spend
from ...runtime.node import GraphNode
from ..deps import AGENT_NAME, CleanupDeps
from ..failures import WORKER_FAILED
from ..prompts import build_inspect_prompt, build_triage_prompt
from ..tools import INSPECT_TOOL_NAMES, TRIAGE_TOOL_NAMES

_log = logging.getLogger(__name__)


def _failure_reason(exc: Exception) -> str:
    summary = exception_summary(exc)
    return WORKER_FAILED.format(reason=summary)[:MAX_INSPECT_REASON_CHARS]


class TriageNode(GraphNode[TaskCleanupState, TriageUpdate]):
    """조율자가 후보 목록만 보고 어느 것을 조사할지 스스로 정하게 한다."""

    name = "triage"

    def __init__(self, deps: CleanupDeps, lease: AgentBudgetLease) -> None:
        self._deps = deps
        self._lease = lease

    async def run(self, _state: TaskCleanupState) -> TriageUpdate:
        deps = self._deps
        lease = self._lease
        event_ids: dict[str, set[str]] = {}
        # 예약에서 턴을 하나도 못 받으면 재귀 한도가 0이 되어 그래프가 시작하지 못하므로 모델을 부르지 않는다.
        if lease.max_turns <= 0:
            return {
                "plan": TriagePlan(),
                "exposed_candidates": {},
                "event_ids_by_task": event_ids,
                **reserved_spend(cost_usd=0.0, turns_used=0),
            }
        # 요청이 실어 준 후보가 곧 조율자가 본 후보이므로 노출 목록을 여기서 결정적으로 세운다.
        triage_prompt, listed = build_triage_prompt(deps.prompt, deps.req.batch)
        exposed: dict[str, CleanupCandidate] = {candidate.id: candidate for candidate in listed}
        budget = deps.new_loop(self.name, max_cost_usd=lease.max_cost_usd)
        call = await deps.invoke(
            budget=budget,
            system_prompt=deps.prompts.triage_system,
            tool_names=TRIAGE_TOOL_NAMES,
            output=TriagePlan,
            messages=[HumanMessage(content=triage_prompt)],
            missing_response=f"{AGENT_NAME} triage produced no structured plan",
            max_turns=lease.max_turns,
            exposed_candidates=exposed,
            event_ids_by_task=event_ids,
        )
        plan = call.response
        chosen = ", ".join(f"{item.taskId}:{item.depth}" for item in plan.assignments) or "없음"
        deps.usage.record_orchestration_event(
            "route.selected",
            f"{self.name} -> {chosen}",
            node_name=self.name,
        )
        return {
            "plan": plan,
            "exposed_candidates": exposed,
            "event_ids_by_task": event_ids,
            **reserved_spend(cost_usd=budget.delta, turns_used=call.num_turns),
        }


class InspectNode(GraphNode[InspectDispatch, InspectUpdate]):
    """후보 하나를 자기 예산과 자기 장부로 조회하고 판정을 올린다."""

    name = "inspect"

    def __init__(self, deps: CleanupDeps) -> None:
        self._deps = deps

    async def run(self, payload: InspectDispatch) -> InspectUpdate:
        deps = self._deps
        task_id = payload.assignment.taskId
        # 장부를 조사마다 새로 두어 다른 후보의 이벤트를 인용하지 못하게 한다.
        event_ids: dict[str, set[str]] = {}
        # 턴을 하나도 못 받으면 재귀 한도가 0이 되어 그래프가 시작하지 못하므로 모델을 부르지 않는다.
        if payload.max_turns <= 0:
            report = InspectReport(
                taskId=task_id,
                archivable=False,
                reason=WORKER_FAILED.format(reason="no turns allocated")[:MAX_INSPECT_REASON_CHARS],
                citedEventIds=[],
            )
            return {
                "reports": [report],
                "event_ids_by_task": event_ids,
                **pool_spend(cost_usd=0.0, turns_used=0),
            }
        budget = deps.new_loop(CLEANUP_REVIEWER_ROLE, max_cost_usd=payload.cost_budget)
        # 취소(BaseException 계열)는 잡 전체를 멈추라는 신호이므로 잡지 않고 전파한다.
        try:
            call = await deps.invoke(
                budget=budget,
                system_prompt=deps.prompts.inspect_system,
                tool_names=INSPECT_TOOL_NAMES,
                output=InspectReport,
                messages=[HumanMessage(content=build_inspect_prompt(task_id))],
                missing_response=f"{task_id} inspection produced no structured report",
                max_turns=payload.max_turns,
                event_ids_by_task=event_ids,
            )
            report, turns_used = call.response, call.num_turns
        except Exception as exc:
            # 조사가 실패한 후보는 안전하게 보존하도록 보관 불가로 올린다.
            _log.warning("inspect failed for %s: %s", task_id, exc)
            report = InspectReport(
                taskId=task_id,
                archivable=False,
                reason=_failure_reason(exc),
                citedEventIds=[],
            )
            # 실패한 호출은 실제 턴을 모르므로 계약의 정산-무보고 규칙대로 배분받은 턴 전부를 쓴 것으로 본다.
            turns_used = payload.max_turns
        return {
            "reports": [report],
            "event_ids_by_task": event_ids,
            **pool_spend(cost_usd=budget.delta, turns_used=turns_used),
        }
