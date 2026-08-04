"""recipe-scan의 내부 상태와 구조화 체인 계약."""

from __future__ import annotations

import json
import operator
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal, Required, TypedDict

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..shared.dispatch_depth import DispatchDepth, depth_share
from ..shared.graph_state import BudgetSnapshotState
from ..shared.models import AgentExecutionRequest, Language, ModelFacing, TrimmedStr

# 한 태스크가 서로 다른 작업 turn을 담을 수 있어 스캔 한 번이 낼 수 있는 후보 수다.
MAX_RECIPE_CANDIDATES = 4


class RecipeScanRequest(AgentExecutionRequest):
    """Python-native recipe-scan의 도메인 실행 봉투."""

    model_config = ConfigDict(extra="forbid")

    taskId: TrimmedStr = Field(min_length=1)
    language: Language = "auto"
    userPrompt: TrimmedStr | None = None


ProbeName = Literal["timeline", "rules", "repetition"]

# 전문가 인스턴스의 턴 백스톱이다.
MAX_PROBE_TURNS = 10
# 조율자가 고른 깊이를 예산 배분의 몫으로 옮기는 계약의 자리다.
PROBE_DEPTH_KEY = "dispatchDepth"
# 한 조사 계획이 부를 수 있는 전문가 수의 상한이다.
MAX_DISPATCH_PROBES = 3
# 조율자가 종합 대신 전문가를 다시 부를 수 있는 라운드 수이며 무한 루프를 이 값으로 막는다.
MAX_REDISPATCH_ROUNDS = 1
# 한 번의 추가 파견 요청이 부를 수 있는 전문가 수의 상한이다.
MAX_REDISPATCH_PROBES = 3


class ProbeAssignment(ModelFacing):
    """조율자가 한 전문가에게 맡긴 질문과 고른 조사 깊이다."""

    model_config = ConfigDict(extra="forbid")

    probe: ProbeName
    depth: DispatchDepth
    question: TrimmedStr = Field(min_length=1, max_length=300)

    @property
    def share(self) -> int:
        """이 전문가가 예산 배분에서 받는 몫이며 값은 계약이 갖는다."""
        return depth_share("recipe-scan", PROBE_DEPTH_KEY, self.depth)


class DispatchPlan(ModelFacing):
    """조율자가 세운 조사 계획이며 어디를 얼마나 팔지 스스로 정한 결과다."""

    model_config = ConfigDict(extra="forbid")

    # 빈 목록은 조사할 것이 없다는 뜻이며 그 실행은 후보 없이 끝난다.
    probes: list[ProbeAssignment] = Field(default_factory=list, max_length=MAX_DISPATCH_PROBES)

    def total_share(self) -> int:
        """계획이 요구하는 몫의 합이다."""
        return sum(probe.share for probe in self.probes)


class ProbeDispatch(BaseModel):
    """조율자가 전문가 분기 하나에 실어 보내는 조사 지시와 배분한 턴과 달러 몫이다."""

    model_config = ConfigDict(extra="forbid")

    assignment: ProbeAssignment
    # 같은 계획의 다른 전문가가 맡은 축이며 이 전문가는 그 축을 조사하지 않는다.
    siblings: list[ProbeAssignment] = Field(default_factory=list, max_length=MAX_DISPATCH_PROBES)
    # 0턴은 그 전문가에게 몫이 남지 않았다는 뜻이며 노드가 모델을 부르지 않는 길로 스스로 보낸다.
    max_turns: int = Field(ge=0)
    max_cost_usd: float = Field(ge=0.0)


# 발췌 상한이 곧 맥락 격리의 강도이며 넉넉히 열면 전문가의 맥락이 조율자에게 그대로 옮겨온다.
MAX_EXCERPTS_PER_PROBE = 12
MAX_EXCERPT_CHARS = 600
MAX_VERDICT_CHARS = 1_200


class Excerpt(ModelFacing):
    """전문가가 조율자에게 올리는 근거 한 조각이다."""

    model_config = ConfigDict(extra="forbid")

    taskId: TrimmedStr = Field(min_length=1)
    eventId: TrimmedStr = Field(min_length=1)
    text: TrimmedStr = Field(min_length=1, max_length=MAX_EXCERPT_CHARS)


class ProbeReport(ModelFacing):
    """한 전문가의 조사 결과이며 조율자는 이것만 보고 후보를 쓴다."""

    model_config = ConfigDict(extra="forbid")

    probe: ProbeName
    verdict: TrimmedStr = Field(min_length=1, max_length=MAX_VERDICT_CHARS)
    excerpts: list[Excerpt] = Field(default_factory=list, max_length=MAX_EXCERPTS_PER_PROBE)
    # 예산이 끊겨 못 본 것이 있으면 조율자가 남은 예산을 다시 줄지 판단한다.
    exhausted: bool = False


RecipeVerifyTool = Literal["command", "file-read", "file-write", "web"]


class RecipeVerifyCommand(ModelFacing):
    """이 명령을 돌렸는지로 스텝 이행을 관측하는 신호다."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["command"]
    commandMatches: list[TrimmedStr] = Field(min_length=1, max_length=20)


class RecipeVerifyPattern(ModelFacing):
    """이 경로·명령 정규식 패턴에 걸리는 것을 건드렸는지로 스텝 이행을 관측하는 신호다."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["pattern"]
    pattern: TrimmedStr = Field(min_length=1, max_length=500)


class RecipeVerifyAction(ModelFacing):
    """이 도구 계열을 썼는지로 스텝 이행을 관측하는 신호다."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["action"]
    tool: RecipeVerifyTool


# 규칙 도메인의 RuleExpectation과 모양이 비슷해도 독립된 어휘이며 공통 모듈로 묶지 않는다.
RecipeVerify = Annotated[
    RecipeVerifyCommand | RecipeVerifyPattern | RecipeVerifyAction,
    Field(discriminator="kind"),
]


class RecipeStep(ModelFacing):
    order: int = Field(ge=1, le=50)
    action: TrimmedStr = Field(min_length=1, max_length=200)
    rationale: TrimmedStr | None = Field(default=None, max_length=300)
    verify: RecipeVerify | None = None


class RecipeTouchedFile(ModelFacing):
    path: TrimmedStr = Field(min_length=1, max_length=500)
    role: Literal["read", "write", "both"]


class RecipeSlice(ModelFacing):
    taskId: TrimmedStr = Field(min_length=1)
    turnIds: list[TrimmedStr] = Field(default_factory=list, max_length=50)
    eventIds: list[TrimmedStr] = Field(default_factory=list, max_length=200)


class RecipeCorrection(ModelFacing):
    whatAgentDid: TrimmedStr = Field(min_length=1, max_length=500)
    howCorrected: TrimmedStr = Field(min_length=1, max_length=500)
    evidence: list[TrimmedStr] = Field(min_length=1, max_length=50)


class RecipePitfall(ModelFacing):
    pitfall: TrimmedStr = Field(min_length=1, max_length=500)
    whyNonObvious: TrimmedStr = Field(min_length=1, max_length=500)
    evidence: list[TrimmedStr] = Field(min_length=1, max_length=50)


class RecipeCandidate(ModelFacing):
    title: TrimmedStr = Field(min_length=1, max_length=120)
    intent: TrimmedStr = Field(min_length=1, max_length=200)
    description: TrimmedStr = Field(min_length=1, max_length=400)
    summary_md: TrimmedStr = Field(min_length=1, max_length=4000)
    request: TrimmedStr = Field(min_length=1, max_length=2000)
    corrections: list[RecipeCorrection] = Field(default_factory=list, max_length=20)
    pitfalls: list[RecipePitfall] = Field(default_factory=list, max_length=20)
    governing_rules: list[TrimmedStr] = Field(default_factory=list, max_length=50)
    revises_recipe_id: TrimmedStr | None = Field(default=None, max_length=200)
    steps: list[RecipeStep] = Field(default_factory=list, max_length=20)
    touched_files: list[RecipeTouchedFile] = Field(default_factory=list, max_length=30)
    contributing_slices: list[RecipeSlice] = Field(min_length=1, max_length=20)
    rationale: TrimmedStr = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def ordered_steps(self) -> RecipeCandidate:
        orders = [step.order for step in self.steps]
        if orders != list(range(1, len(orders) + 1)):
            raise ValueError("steps must use consecutive order values starting at 1")
        return self


class RecipeDraft(ModelFacing):
    recipes: list[RecipeCandidate] = Field(default_factory=list, max_length=MAX_RECIPE_CANDIDATES)
    # 조율자는 최종 초안 대신 전문가 추가 파견을 요청할 수 있으며 둘은 함께 오지 않는다.
    redispatch: list[ProbeAssignment] = Field(default_factory=list, max_length=MAX_REDISPATCH_PROBES)

    @model_validator(mode="after")
    def _draft_or_redispatch(self) -> RecipeDraft:
        if self.recipes and self.redispatch:
            raise ValueError("return either recipes or a redispatch request, not both")
        return self


class ProvenanceCatalog(BaseModel):
    eventIdsByTask: dict[str, set[str]] = Field(default_factory=dict)
    turnIdsByTask: dict[str, set[str]] = Field(default_factory=dict)
    ruleIds: set[str] = Field(default_factory=set)
    recipeRevs: dict[str, int] = Field(default_factory=dict)


def merged_provenance(left: ProvenanceCatalog, right: ProvenanceCatalog) -> ProvenanceCatalog:
    """두 근거 장부를 합친 새 장부를 낸다."""
    combined = left.model_copy(deep=True)
    for task_id, event_ids in right.eventIdsByTask.items():
        combined.eventIdsByTask.setdefault(task_id, set()).update(event_ids)
    for task_id, turn_ids in right.turnIdsByTask.items():
        combined.turnIdsByTask.setdefault(task_id, set()).update(turn_ids)
    combined.ruleIds.update(right.ruleIds)
    combined.recipeRevs.update(right.recipeRevs)
    return combined


class SurveyUpdate(TypedDict):
    """조율자 노드가 갱신하는 상태 부분집합이다."""

    plan: DispatchPlan
    model_cost_usd: float


class ProbeUpdate(TypedDict):
    """전문가 조사 노드가 갱신하는 상태 부분집합이다."""

    reports: list[ProbeReport]
    provenance: ProvenanceCatalog
    model_cost_usd: float
    # 전문가가 그은 턴은 팬아웃 잔량 풀에서 곧장 빠져 다음 팬아웃의 가용 턴을 줄인다.
    model_turns_used: int


class InvestigateUpdate(TypedDict):
    """결정 노드가 갱신하는 상태 부분집합이다."""

    candidates: list[RecipeCandidate]
    messages: list[BaseMessage]
    provenance: ProvenanceCatalog
    model_cost_usd: float
    model_turns_used: int
    redispatch: DispatchPlan | None
    redispatch_count: int


class ValidateCandidateUpdate(TypedDict):
    """검증 노드가 갱신하는 상태 부분집합이다."""

    validation_errors: list[str]


class RepairUpdate(TypedDict, total=False):
    """수리 노드가 갱신하는 상태 부분집합이다."""

    candidates: list[RecipeCandidate]
    messages: list[BaseMessage]
    provenance: ProvenanceCatalog
    repair_attempted: Required[bool]
    model_cost_usd: float


class ProvenanceWire(BaseModel):
    """워커가 소유권 밖의 인용까지 같은 기준으로 거를 수 있는 이 실행의 근거 장부다."""

    eventIdsByTask: dict[str, list[str]] = Field(default_factory=dict)
    turnIdsByTask: dict[str, list[str]] = Field(default_factory=dict)
    ruleIds: list[str] = Field(default_factory=list)
    recipeRevs: dict[str, int] = Field(default_factory=dict)


class RecipeScanResult(BaseModel):
    """스캔이 창구에 내는 산출물이다."""

    recipes: list[RecipeCandidate] = Field(default_factory=list)
    provenance: ProvenanceWire


class ResultUpdate(TypedDict):
    """종단 노드가 갱신하는 상태 부분집합이다."""

    result: RecipeScanResult


class RecipeScanState(BudgetSnapshotState):
    plan: DispatchPlan | None
    # 조율자가 종합 대신 요청한 추가 파견 계획이며 없으면 검증으로 넘어간다.
    redispatch: DispatchPlan | None
    redispatch_count: int
    # 전문가가 병렬로 보고를 올리므로 동시 갱신을 누적으로 합치는 리듀서가 필요하다.
    reports: Annotated[list[ProbeReport], operator.add]
    task_id: str
    language: Language
    user_prompt: str | None
    # 근거는 프롬프트에 다시 붙이지 않고 대화 이력에 그대로 남아 캐시된다.
    messages: list[BaseMessage]
    provenance: Annotated[ProvenanceCatalog, merged_provenance]
    # repair·survey·synthesisFloor 예약을 뗀 뒤 팬아웃과 종합이 나눠 쓰는 턴과 달러 잔량이다.
    max_cost_usd: float
    max_turns: int
    candidates: list[RecipeCandidate]
    validation_errors: list[str]
    repair_attempted: bool
    result: RecipeScanResult | None


_ANCHOR_PATH = Path(__file__).resolve().parents[5] / "contract" / "agent" / "recipe-scan" / "agent.json"


@lru_cache(maxsize=1)
def _scan_anchor_contract() -> Mapping[str, Any]:
    document = json.loads(_ANCHOR_PATH.read_text(encoding="utf-8"))
    anchor: Mapping[str, Any] = document["anchor"]
    return anchor


def scan_anchor_requirements() -> Mapping[str, Any]:
    """스캔이 근거를 수집할 수 있는 앵커의 자격이며 값은 계약이 소유한다."""
    requires: Mapping[str, Any] = _scan_anchor_contract()["requires"]
    return requires


def scan_anchor_conditions(trigger: str | None) -> tuple[str, ...]:
    """스캔을 부른 표면이 요구하는 조건이며 표면을 말하지 않으면 dashboard 로 본다."""
    by_trigger: Mapping[str, Any] = _scan_anchor_contract()["byTrigger"]
    surface = trigger if trigger is not None and trigger in by_trigger else "dashboard"
    conditions: list[str] = by_trigger[surface]
    return tuple(conditions)
