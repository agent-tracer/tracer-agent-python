"""앵커 태스크에 적용되는 규칙을 읽고 규칙 식별자를 근거로 올리는 도구를 소유한다."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from tracer_agent.shared.agents.recipe_scan.models import ProvenanceCatalog
from tracer_agent.shared.agents.shared.models import TrimmedStr

from ...runtime.tooling import AgentTool
from ...runtime.tracer_client import TRANSIENT_TRACER_ERRORS
from ..reader import RecipeLedgerReader
from .provenance import add_rule_ids, loaded

LIST_RULES = "list_rules"


class ListRulesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taskId: TrimmedStr = Field(min_length=1, description="The anchor task ID")


LIST_RULES_DESCRIPTION = (
    "List existing global and task-scoped rules that apply to the anchor task, so friction a rule "
    "already governs is cited by rule ID in governing_rules instead of re-described."
)


class ListRulesTool(AgentTool[ListRulesArgs]):
    """앵커 태스크에 적용되는 살아 있는 규칙을 읽고 규칙 식별자를 근거로 올린다."""

    name = LIST_RULES
    description = LIST_RULES_DESCRIPTION
    args_model = ListRulesArgs
    # 창구에 닿지 못한 것만 일시적이며 창구가 거절한 요청은 재시도하지 않는다.
    transient_errors = TRANSIENT_TRACER_ERRORS

    def __init__(self, reader: RecipeLedgerReader, catalog: ProvenanceCatalog) -> None:
        self._reader = reader
        self._catalog = catalog

    async def execute(self, args: ListRulesArgs) -> str:
        rules = await self._reader.applicable_rules(args.taskId)
        return json.dumps(rules, ensure_ascii=False)

    def record(self, _args: ListRulesArgs, content: str, /) -> None:
        add_rule_ids(self._catalog, loaded(content))
