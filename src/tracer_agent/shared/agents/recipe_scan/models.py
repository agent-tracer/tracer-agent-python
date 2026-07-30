"""recipe-scan의 내부 상태와 구조화 체인 계약."""

from __future__ import annotations

import operator
from typing import Annotated, Literal, Required, TypedDict

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..shared.models import AgentExecutionRequest, Language, ModelFacing, TrimmedStr

# 한 태스크가 서로 다른 작업 turn을 담을 수 있어 스캔 한 번이 낼 수 있는 후보 수다.
MAX_RECIPE_CANDIDATES = 4


class RecipeScanRequest(AgentExecutionRequest):
    """Python-native recipe-scan의 도메인 실행 봉투."""

    model_config = ConfigDict(extra="forbid")

    taskId: TrimmedStr = Field(min_length=1)
    # 조회 범위를 정하는 값이라 도메인 입력이며 멱등 해시에 함께 든다.
    userId: TrimmedStr = Field(min_length=1)
    language: Language = "auto"
    userPrompt: TrimmedStr | None = None


ProbeName = Literal["timeline", "rules", "repetition"]

# 전문가 인스턴스의 턴 백스톱과, 조율자가 전문가 하나에 줄 수 있는 최대 weight다.
MAX_PROBE_TURNS = 10
MAX_PROBE_WEIGHT = 10
# 한 조사 계획이 부를 수 있는 전문가 수의 상한이다.
MAX_DISPATCH_PROBES = 3
# 조율자가 종합 대신 전문가를 다시 부를 수 있는 라운드 수이며 무한 루프를 이 값으로 막는다.
MAX_REDISPATCH_ROUNDS = 1
# 한 번의 추가 파견 요청이 부를 수 있는 전문가 수의 상한이다.
MAX_REDISPATCH_PROBES = 3


class ProbeAssignment(ModelFacing):
    """조율자가 한 전문가에게 맡긴 질문과 배분한 weight다."""

    model_config = ConfigDict(extra="forbid")

    probe: ProbeName
    weight: int = Field(ge=1, le=MAX_PROBE_WEIGHT)
    question: TrimmedStr = Field(min_length=1, max_length=300)


class DispatchPlan(ModelFacing):
    """조율자가 세운 조사 계획이며 어디를 얼마나 팔지 스스로 정한 결과다."""

    model_config = ConfigDict(extra="forbid")

    # 빈 목록은 조사할 것이 없다는 뜻이며 그 실행은 후보 없이 끝난다.
    probes: list[ProbeAssignment] = Field(default_factory=list, max_length=MAX_DISPATCH_PROBES)

    def total_weight(self) -> int:
        """계획이 요구하는 weight 합이다."""
        return sum(probe.weight for probe in self.probes)


class ProbeDispatch(BaseModel):
    """조율자가 전문가 분기 하나에 실어 보내는 조사 지시와 배분한 비용 예산이다."""

    model_config = ConfigDict(extra="forbid")

    assignment: ProbeAssignment
    cost_budget: float = Field(gt=0.0)


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


def _sum_cost(left: float, right: float) -> float:
    return left + right


class SurveyUpdate(TypedDict):
    """조율자 노드가 갱신하는 상태 부분집합이다."""

    plan: DispatchPlan
    model_cost_usd: float


class ProbeUpdate(TypedDict):
    """전문가 조사 노드가 갱신하는 상태 부분집합이다."""

    reports: list[ProbeReport]
    provenance: ProvenanceCatalog
    model_cost_usd: float


class InvestigateUpdate(TypedDict):
    """결정 노드가 갱신하는 상태 부분집합이다."""

    candidates: list[RecipeCandidate]
    messages: list[BaseMessage]
    provenance: ProvenanceCatalog
    model_cost_usd: float
    redispatch: DispatchPlan | None
    redispatch_ceiling: float
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


class ResultUpdate(TypedDict):
    """종단 노드가 갱신하는 상태 부분집합이다."""

    result: dict[str, object]


class RecipeScanState(TypedDict):
    plan: DispatchPlan | None
    # 조율자가 종합 대신 요청한 추가 파견 계획이며 없으면 검증으로 넘어간다.
    redispatch: DispatchPlan | None
    # 추가 파견에 넘길 수 있는 남은 비용 상한과, 상한을 지키기 위해 센 파견 횟수다.
    redispatch_ceiling: float
    redispatch_count: int
    # 전문가가 병렬로 보고를 올리므로 동시 갱신을 누적으로 합치는 리듀서가 필요하다.
    reports: Annotated[list[ProbeReport], operator.add]
    task_id: str
    language: Language
    user_prompt: str | None
    # 근거는 프롬프트에 다시 붙이지 않고 대화 이력에 그대로 남아 캐시된다.
    messages: list[BaseMessage]
    provenance: Annotated[ProvenanceCatalog, merged_provenance]
    model_cost_usd: Annotated[float, _sum_cost]
    # 팬아웃이 남은 몫을 나눌 때 봐야 하는 이 실행의 달러 상한이다.
    max_cost_usd: float
    candidates: list[RecipeCandidate]
    validation_errors: list[str]
    repair_attempted: bool
    result: dict[str, object] | None
