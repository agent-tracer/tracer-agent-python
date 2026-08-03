"""골든 데이터셋의 품질 회귀 기준을 검증한다(기본은 대역 모델, 실제 모델은 환경변수로 켠다)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.quality.dataset import AGENTS, GoldenCase, load_cases
from tests.quality.harness import LIVE_ENV, CaseRun, collect_sources, live_enabled, run_case
from tests.quality.judge import (
    DETERMINISTIC,
    PROMPT,
    TRAJECTORY,
    SuiteReport,
    format_report,
    judge,
    judge_case,
)
from tracer_agent.shared.agents.shared.models import AgentResponse

_CASES = load_cases()

# 회귀를 막는 하한이며 이 값을 내리려면 데이터셋이나 프롬프트의 변경이 함께 설명되어야 한다.
_MIN_DETERMINISTIC_PASS_RATE = 1.0
_MIN_TRAJECTORY_PASS_RATE = 1.0
_MIN_PROMPT_PASS_RATE = 1.0
_MAX_MEAN_TOOL_CALLS = 1.5

_LIVE_REASON = f"{LIVE_ENV}=1과 ANTHROPIC_API_KEY가 있어야 실제 모델 모드를 실행한다"


class TestGoldenDataset:
    """골든 데이터셋"""

    def test_모든_에이전트가_사례를_최소_하나씩_갖는다(self) -> None:
        covered = {case.agent for case in _CASES}
        assert covered == set(AGENTS)

    def test_사례_식별자가_서로_겹치지_않는다(self) -> None:
        identifiers = [case.id for case in _CASES]
        assert len(identifiers) == len(set(identifiers))


class TestOfflineJudge:
    """대역 모델 판정기"""

    @pytest.mark.parametrize("case", _CASES, ids=lambda case: case.id)
    async def test_사례가_기대_성질을_모두_충족한다(self, case: GoldenCase) -> None:
        report = judge_case(await run_case(case))

        assert report.passed, "\n".join(f"{check.name}: {check.detail}" for check in report.failures())

    async def test_묶음_지표가_회귀_기준을_지킨다(self) -> None:
        suite = await _run_suite(live=False)
        metrics = suite.metrics()

        print(format_report(suite))
        assert metrics["deterministic_pass_rate"] >= _MIN_DETERMINISTIC_PASS_RATE
        assert metrics["trajectory_pass_rate"] >= _MIN_TRAJECTORY_PASS_RATE
        assert metrics["budget_pass_rate"] >= _MIN_TRAJECTORY_PASS_RATE
        assert metrics["prompt_pass_rate"] >= _MIN_PROMPT_PASS_RATE
        assert metrics["mean_tool_calls"] <= _MAX_MEAN_TOOL_CALLS


class TestJudgeSensitivity:
    """판정기의 민감도"""

    def test_수집하지_않은_event_ID를_인용한_결과를_잡아낸다(self) -> None:
        case = load_cases("recipe-scan")[0]
        forged = _forged_recipe(case, eventIds=["ghost-event"])

        report = judge_case(_synthetic_run(case, {"recipes": [forged]}))

        failed = {check.name for check in report.failures()}
        assert "deterministic_validation" in failed

    def test_앵커를_인용하지_않은_후보를_잡아낸다(self) -> None:
        case = load_cases("recipe-scan")[0]
        forged = _forged_recipe(case, taskId="other-task")

        report = judge_case(_synthetic_run(case, {"recipes": [forged]}))

        failed = {check.name for check in report.failures()}
        assert {"deterministic_validation", "includes_anchor_task"} <= failed

    def test_프롬프트가_잃어버린_문구를_잡아낸다(self) -> None:
        case = load_cases("recipe-scan")[0]

        report = judge_case(_synthetic_run(case, {"recipes": []}, prompt_text="아무것도 싣지 않았다"))

        failures = {check.name: check.axis for check in report.failures()}
        assert failures.get("prompt_mentions") == PROMPT

    def test_밟지_않은_노드를_요구한_기대를_잡아낸다(self) -> None:
        case = load_cases("recipe-scan")[0]

        report = judge_case(_synthetic_run(case, {"recipes": []}))

        failures = {check.name: check.axis for check in report.failures()}
        assert failures.get("visits_nodes") == TRAJECTORY
        assert DETERMINISTIC in {check.axis for check in report.checks}


class TestLiveJudge:
    """실제 모델 판정기"""

    @pytest.mark.skipif(not live_enabled(), reason=_LIVE_REASON)
    async def test_묶음_지표가_회귀_기준을_지킨다(self) -> None:
        suite = await _run_suite(live=True)
        metrics = suite.metrics()

        print(format_report(suite))
        assert metrics["deterministic_pass_rate"] >= _MIN_DETERMINISTIC_PASS_RATE


async def _run_suite(*, live: bool) -> SuiteReport:
    return judge([await run_case(case, live=live) for case in _CASES])


def _forged_recipe(case: GoldenCase, **slice_overrides: Any) -> dict[str, Any]:
    """사례의 유효한 후보 하나를 가져와 인용만 위조한다."""
    recipe = dict(case.script["turns"][0]["recipes"][0])
    original = recipe["contributing_slices"][0]
    recipe["contributing_slices"] = [{**original, **slice_overrides}]
    return recipe


def _synthetic_run(case: GoldenCase, data: dict[str, Any], prompt_text: str = "") -> CaseRun:
    """그래프를 거치지 않은 결과를 판정기에 그대로 물린다."""
    response = AgentResponse(data=data, modelUsed=case.input["model"], durationMs=0)
    return CaseRun(
        case=case,
        response=response,
        sources=collect_sources(case),
        model=case.input["model"],
        prompt_text=prompt_text,
    )
