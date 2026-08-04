"""골든 사례의 실행 결과를 기대 성질로 판정하고 묶음 지표를 낸다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from tests.support.fakes import mk_rates
from tracer_agent.shared.agents.recipe_scan.models import (
    MAX_RECIPE_CANDIDATES,
    ProvenanceCatalog,
    RecipeCandidate,
)
from tracer_agent.shared.agents.task_cleanup.models import CleanupDraftSuggestion, TaskCleanupState
from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionDraft
from tracer_agent.worker.agents.recipe_scan.policy import validate_recipe_candidates
from tracer_agent.worker.agents.task_cleanup.policy import validate_suggestions
from tracer_agent.worker.agents.title_suggestion.policy import normalize_title_candidate

from .harness import RESULT_KEYS, CaseRun, CaseSources

# 판정 지표를 나누는 축이며 각 축의 충족률이 따로 보고된다.
DETERMINISTIC = "deterministic"
TRAJECTORY = "trajectory"
BUDGET = "budget"
PROMPT = "prompt"


@dataclass(frozen=True)
class CheckResult:
    """기대 성질 하나의 판정이다."""

    name: str
    axis: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class CaseReport:
    """사례 한 건의 판정 결과와 그 실행이 쓴 자원이다."""

    case_id: str
    agent: str
    checks: list[CheckResult]
    tool_calls: int
    turns: int
    cost_usd: float

    @property
    def passed(self) -> bool:
        """모든 기대 성질을 충족했는지다."""
        return all(check.passed for check in self.checks)

    def failures(self) -> list[CheckResult]:
        """충족하지 못한 기대 성질만 낸다."""
        return [check for check in self.checks if not check.passed]


@dataclass(frozen=True)
class SuiteReport:
    """골든 묶음 전체의 판정 결과다."""

    reports: list[CaseReport]

    def failures(self) -> list[tuple[str, CheckResult]]:
        """어느 사례의 어느 성질이 깨졌는지 낸다."""
        return [(report.case_id, check) for report in self.reports for check in report.failures()]

    def metrics(self) -> dict[str, float]:
        """회귀를 비교하는 지표를 낸다."""
        cases = len(self.reports)
        return {
            "cases": float(cases),
            "case_pass_rate": _ratio(sum(report.passed for report in self.reports), cases),
            "deterministic_pass_rate": self._axis_rate(DETERMINISTIC),
            "trajectory_pass_rate": self._axis_rate(TRAJECTORY),
            "budget_pass_rate": self._axis_rate(BUDGET),
            "prompt_pass_rate": self._axis_rate(PROMPT),
            "total_tool_calls": float(sum(report.tool_calls for report in self.reports)),
            "mean_tool_calls": _ratio(sum(report.tool_calls for report in self.reports), cases),
            "mean_turns": _ratio(sum(report.turns for report in self.reports), cases),
            "total_cost_usd": round(sum(report.cost_usd for report in self.reports), 6),
            "mean_cost_usd": round(_ratio(sum(report.cost_usd for report in self.reports), cases), 6),
        }

    def _axis_rate(self, axis: str) -> float:
        checks = [check for report in self.reports for check in report.checks if check.axis == axis]
        return _ratio(sum(check.passed for check in checks), len(checks))


def judge(runs: list[CaseRun]) -> SuiteReport:
    """실행한 사례들을 판정해 묶음 보고를 만든다."""
    return SuiteReport([judge_case(run) for run in runs])


def judge_case(run: CaseRun) -> CaseReport:
    """사례 한 건의 결과를 기대 성질과 비교해 판정한다."""
    expect = run.case.expect
    data = run.response.data or {}
    results = _results(run.case.agent, data)
    checks = [_deterministic_check(run, expect)]
    checks.extend(_result_count_checks(results, expect))
    if run.case.agent == "recipe-scan":
        checks.append(_anchor_check(results, run.sources, expect))
    checks.extend(_trajectory_checks(run, expect))
    tool_calls = sum(1 for step in run.response.steps if step.role == "tool")
    checks.extend(_resource_checks(run, tool_calls, expect))
    checks.extend(_prompt_checks(run, expect))
    return CaseReport(
        case_id=run.case.id,
        agent=run.case.agent,
        checks=checks,
        tool_calls=tool_calls,
        turns=run.response.numTurns or 0,
        cost_usd=_cost_usd(run),
    )


def _results(agent: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    value = data.get(RESULT_KEYS[agent], [])
    return list(value) if isinstance(value, list) else []


def _deterministic_check(run: CaseRun, expect: dict[str, Any]) -> CheckResult:
    expected_pass = expect.get("deterministicValidation", "pass") == "pass"
    errors = _validate(run)
    passed = (not errors) == expected_pass
    detail = "결정적 검증기가 오류를 내지 않았다" if not errors else "; ".join(errors)
    return CheckResult("deterministic_validation", DETERMINISTIC, passed, detail)


def _validate(run: CaseRun) -> list[str]:
    if run.response.error is not None:
        return [f"실행이 {run.response.error.subtype}로 끝났다"]
    data = run.response.data
    if data is None:
        return ["실행이 구조화 출력을 내지 않았다"]
    if run.case.agent == "recipe-scan":
        return _validate_recipe_scan(data, run.sources)
    if run.case.agent == "task-cleanup":
        return _validate_task_cleanup(data, run)
    return _validate_title_suggestion(data, run.sources)


def _validate_recipe_scan(data: dict[str, Any], sources: CaseSources) -> list[str]:
    candidates = [RecipeCandidate.model_validate(item) for item in data.get("recipes", [])]
    catalog = ProvenanceCatalog(
        eventIdsByTask=dict(sources.event_ids_by_task),
        turnIdsByTask=dict(sources.turn_ids_by_task),
        ruleIds=set(sources.rule_ids),
    )
    return validate_recipe_candidates(candidates, sources.anchor_task_id, catalog)


def _validate_task_cleanup(data: dict[str, Any], run: CaseRun) -> list[str]:
    suggestions = [CleanupDraftSuggestion.model_validate(item) for item in data.get("suggestions", [])]
    state = cast(
        TaskCleanupState,
        {
            "exposed_candidates": dict(run.sources.candidates),
            "event_ids_by_task": dict(run.sources.event_ids_by_task),
            "max_suggestions": run.case.input.get("maxSuggestions", 5),
        },
    )
    _, errors = validate_suggestions(suggestions, state)
    return errors


def _validate_title_suggestion(data: dict[str, Any], sources: CaseSources) -> list[str]:
    draft = TitleSuggestionDraft.model_validate(data)
    _filtered, errors = normalize_title_candidate(draft, sources.current_title)
    return errors


def _result_count_checks(results: list[dict[str, Any]], expect: dict[str, Any]) -> list[CheckResult]:
    checks: list[CheckResult] = []
    ceiling = expect.get("maxResults", MAX_RECIPE_CANDIDATES)
    checks.append(
        CheckResult(
            "result_count_at_most",
            DETERMINISTIC,
            len(results) <= ceiling,
            f"후보 {len(results)}건이고 상한은 {ceiling}건이다",
        )
    )
    if "minResults" in expect:
        floor = expect["minResults"]
        checks.append(
            CheckResult(
                "result_count_at_least",
                DETERMINISTIC,
                len(results) >= floor,
                f"후보 {len(results)}건이고 하한은 {floor}건이다",
            )
        )
    return checks


def _anchor_check(results: list[dict[str, Any]], sources: CaseSources, expect: dict[str, Any]) -> CheckResult:
    required = expect.get("includesAnchorTask", True)
    missing = [
        str(item.get("title"))
        for item in results
        if sources.anchor_task_id not in {slice_["taskId"] for slice_ in item.get("contributing_slices", [])}
    ]
    passed = (not missing) if required else True
    detail = (
        f"모든 후보가 앵커 태스크 {sources.anchor_task_id}를 인용한다"
        if not missing
        else f"앵커를 인용하지 않은 후보: {', '.join(missing)}"
    )
    return CheckResult("includes_anchor_task", DETERMINISTIC, passed, detail)


def _trajectory_checks(run: CaseRun, expect: dict[str, Any]) -> list[CheckResult]:
    completed = {
        step.nodeName for step in run.response.steps if step.eventKind == "node.completed" and step.nodeName
    }
    checks: list[CheckResult] = []
    if "visitsNodes" in expect:
        required = set(expect["visitsNodes"])
        missing = sorted(required - completed)
        checks.append(
            CheckResult(
                "visits_nodes",
                TRAJECTORY,
                not missing,
                f"밟은 노드 {sorted(completed)}" if not missing else f"밟지 않은 노드 {missing}",
            )
        )
    if "avoidsNodes" in expect:
        forbidden = sorted(set(expect["avoidsNodes"]) & completed)
        checks.append(
            CheckResult(
                "avoids_nodes",
                TRAJECTORY,
                not forbidden,
                "피해야 할 노드를 밟지 않았다" if not forbidden else f"밟지 말아야 할 노드 {forbidden}",
            )
        )
    return checks


def _resource_checks(run: CaseRun, tool_calls: int, expect: dict[str, Any]) -> list[CheckResult]:
    checks: list[CheckResult] = []
    if "maxToolCalls" in expect:
        ceiling = expect["maxToolCalls"]
        checks.append(
            CheckResult(
                "tool_calls_at_most",
                TRAJECTORY,
                tool_calls <= ceiling,
                f"도구 호출 {tool_calls}회이고 상한은 {ceiling}회다",
            )
        )
    if "maxCostUsd" in expect:
        ceiling = expect["maxCostUsd"]
        cost = _cost_usd(run)
        checks.append(
            CheckResult(
                "cost_at_most",
                BUDGET,
                cost <= ceiling,
                f"예산 소모 {cost}달러이고 상한은 {ceiling}달러다",
            )
        )
    return checks


def _prompt_checks(run: CaseRun, expect: dict[str, Any]) -> list[CheckResult]:
    # 실제 모델 모드는 모델이 받은 메시지를 붙들지 않으므로 이 축의 성질을 판정하지 않는다.
    if not run.prompt_text or "promptMentions" not in expect:
        return []
    missing = [phrase for phrase in expect["promptMentions"] if phrase not in run.prompt_text]
    detail = "프롬프트가 요구한 문구를 모두 실었다" if not missing else f"프롬프트에 없는 문구 {missing}"
    return [CheckResult("prompt_mentions", PROMPT, not missing, detail)]


def _cost_usd(run: CaseRun) -> float:
    return mk_rates().estimate_cost_usd(run.model, run.response.usage) or 0.0


def _ratio(numerator: float, denominator: int) -> float:
    return 0.0 if denominator == 0 else round(numerator / denominator, 4)


def format_report(suite: SuiteReport) -> str:
    """묶음 판정을 사람이 읽는 표로 만든다."""
    lines = ["", "===== golden quality suite ====="]
    for report in suite.reports:
        mark = "pass" if report.passed else "FAIL"
        lines.append(
            f"  [{mark}] {report.case_id} ({report.agent}) "
            f"tools={report.tool_calls} turns={report.turns} cost={report.cost_usd}"
        )
        lines.extend(f"      - {check.name}: {check.detail}" for check in report.failures())
    lines.append("-- metrics --")
    lines.extend(f"  {name}={value}" for name, value in suite.metrics().items())
    return "\n".join(lines)
