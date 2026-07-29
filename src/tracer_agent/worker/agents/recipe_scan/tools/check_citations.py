"""인용 예정 식별자가 근거 장부에 있는지 카탈로그로 확인하는 도구를 소유한다."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from tracer_agent.shared.agents.recipe_scan.models import ProvenanceCatalog
from tracer_agent.shared.agents.shared.models import TrimmedStr

from ...runtime.tooling import AgentTool

CHECK_CITATIONS = "check_citations"
MAX_CITED_IDS = 200


class CheckCitationsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taskId: TrimmedStr = Field(min_length=1, description="The task the cited IDs belong to")
    eventIds: list[TrimmedStr] = Field(
        default_factory=list, max_length=MAX_CITED_IDS, description="Event IDs you intend to cite"
    )
    turnIds: list[TrimmedStr] = Field(
        default_factory=list, max_length=MAX_CITED_IDS, description="Turn IDs you intend to cite"
    )
    ruleIds: list[TrimmedStr] = Field(
        default_factory=list,
        max_length=MAX_CITED_IDS,
        description="Rule IDs you intend to cite in governing_rules",
    )


CHECK_CITATIONS_DESCRIPTION = (
    "Check whether the IDs you plan to cite are backed by what your tools actually returned, "
    "before you write the final candidates. Pass the task plus the event, turn, and rule IDs you "
    "intend to use; the response names the ones that are not citable. A single unsupported ID "
    "gets the whole candidate list rejected, so verify here instead of spending your one repair "
    "on it."
)


class CheckCitationsTool(AgentTool[CheckCitationsArgs]):
    """도구가 아니라 근거 장부를 읽어 인용 불가한 식별자를 짚어준다."""

    name = CHECK_CITATIONS
    description = CHECK_CITATIONS_DESCRIPTION
    args_model = CheckCitationsArgs

    def __init__(self, catalog: ProvenanceCatalog) -> None:
        self._catalog = catalog

    async def execute(self, args: CheckCitationsArgs) -> str:
        catalog = self._catalog
        seen_events = catalog.eventIdsByTask.get(args.taskId, set())
        seen_turns = catalog.turnIdsByTask.get(args.taskId, set())
        unsupported = {
            "taskSupported": args.taskId in catalog.eventIdsByTask,
            "unsupportedEventIds": sorted(set(args.eventIds) - seen_events),
            "unsupportedTurnIds": sorted(set(args.turnIds) - seen_turns),
            "unsupportedRuleIds": sorted(set(args.ruleIds) - catalog.ruleIds),
        }
        return json.dumps(unsupported, ensure_ascii=False)
