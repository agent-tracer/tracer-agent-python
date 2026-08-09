"""겹친 배정을 낸 계획이 거절되고 그 사실이 궤적에 남는지 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from typing import Any

from tests.support.contract import shared_contract
from tests.support.fakes import (
    WIRE_LIMITS,
    WIRE_MODEL_RATES,
    FakeToolLoopChat,
    mk_rates,
)
from tests.support.prompts import RECIPE_SCAN_PROMPT
from tracer_agent.shared.agents.recipe_scan.models import RecipeScanRequest
from tracer_agent.shared.agents.shared.models import AgentStepDTO
from tracer_agent.worker.agents.recipe_scan.deps import RecipeDeps, new_recipe_caller
from tracer_agent.worker.agents.recipe_scan.nodes.survey import SurveyNode
from tracer_agent.worker.agents.recipe_scan.prompts import build_prompt_bundle
from tracer_agent.worker.agents.recipe_scan.reader import RecipeLedgerReader
from tracer_agent.worker.agents.recipe_scan.search import RecipeSearchReader
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.budget import AgentBudgetLease, ExecutionBudget
from tracer_agent.worker.agents.runtime.llm.client import ChatPair

SLOT_FIELD = shared_contract("dispatch.plan.json")["uniqueness"]["keys"]["recipe-scan"]

_DUPLICATE = {
    "probes": [
        {SLOT_FIELD: "timeline", "depth": "deep", "question": "무엇이 있었나"},
        {SLOT_FIELD: "timeline", "depth": "shallow", "question": "또 무엇이 있었나"},
    ]
}
_UNIQUE = {
    "probes": [
        {SLOT_FIELD: "timeline", "depth": "deep", "question": "무엇이 있었나"},
        {SLOT_FIELD: "rules", "depth": "shallow", "question": "어떤 규칙이 걸리나"},
    ]
}


class ScriptedPlanChat(FakeToolLoopChat):
    """조율자 계획을 대본 순서대로 하나씩 되돌리는 대역이다."""

    def __init__(self, plans: list[dict[str, Any]]) -> None:
        super().__init__([])
        self._plans = list(plans)

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.plan = self._plans.pop(0) if self._plans else self.plan
        return await super().ainvoke(messages)


def _request() -> RecipeScanRequest:
    return RecipeScanRequest.model_validate(
        {
            "model": "claude-sonnet-4-6",
            "apiKey": "sk-test",
            "modelRates": WIRE_MODEL_RATES,
            "limits": WIRE_LIMITS,
            "userId": "user-1",
            "taskId": "task-1",
            "language": "ko",
            "completionCallback": {"url": "http://worker:8810/runs/complete", "token": "done-recipe"},
        }
    )


def _survey(chat: FakeToolLoopChat, usage: ExecutionTrace) -> SurveyNode:
    req = _request()
    deps = RecipeDeps(
        req=req,
        reader=RecipeLedgerReader(FakeTracerApi()),
        search=RecipeSearchReader(FakeTracerApi(), FakeTracerApi()),
        usage=usage,
        caller=new_recipe_caller(ChatPair(chat, None)),  # type: ignore[arg-type]
        budget=ExecutionBudget(1.0, mk_rates()),
        prompts=build_prompt_bundle(RECIPE_SCAN_PROMPT),
        prompt=RECIPE_SCAN_PROMPT,
        language_directives=RECIPE_SCAN_PROMPT.language_directives,
    )
    return SurveyNode(deps, AgentBudgetLease(max_turns=4, max_cost_usd=1.0))


async def test_겹친_배정을_낸_계획은_거절되고_모델이_다시_계획한다() -> None:
    usage = ExecutionTrace()

    result = await _survey(ScriptedPlanChat([_DUPLICATE, _UNIQUE]), usage).run({})  # type: ignore[arg-type]

    # 거절이 남은 축을 되살리므로 배정을 지우고 넘어가는 것과 결과가 갈린다.
    assert [assignment.probe for assignment in result["plan"].probes] == ["timeline", "rules"]


def _planned_slots(step: AgentStepDTO) -> list[str]:
    """궤적 단계 하나가 실은 계획의 자리 목록이며 계획을 싣지 않은 단계는 빈 목록이다."""
    recorded: dict[str, Any] = step.model_dump(mode="json")
    return [
        assignment[SLOT_FIELD]
        for call in recorded["toolCalls"]
        for assignment in call["args"].get("probes", [])
    ]


async def test_거절된_겹친_배정이_실행_궤적에_남는다() -> None:
    usage = ExecutionTrace()

    await _survey(ScriptedPlanChat([_DUPLICATE, _UNIQUE]), usage).run({})  # type: ignore[arg-type]

    # 계약은 겹친 배정을 조용히 버리지 말고 관측에 남기라고 적는다.
    planned = [slots for slots in (_planned_slots(step) for step in usage.steps) if slots]
    assert planned == [["timeline", "timeline"], ["timeline", "rules"]]
