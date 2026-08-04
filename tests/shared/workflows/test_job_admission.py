"""잡 종류마다의 접수 자격 심사가 자기 입력 모델에 붙어 있는지 검증한다(네트워크 없음)."""

from __future__ import annotations

from tracer_agent.shared.workflows.jobs_anchor import RuleAnchor, ScanAnchor
from tracer_agent.shared.workflows.jobs_input import (
    INELIGIBLE_SCAN_ANCHOR,
    INPUT_MODEL_BY_KIND,
    INVALID_RULE_ANCHOR,
    AdmissionContext,
    RecipeScanJobInput,
    RuleGenerationJobInput,
    TaskCleanupJobInput,
    TitleSuggestionJobInput,
)


class _Anchors:
    """규칙과 스캔의 앵커 창구를 정해 둔 답 하나로 대신한다."""

    def __init__(self, rule: RuleAnchor | None = None, scan: ScanAnchor | None = None) -> None:
        self._rule = rule
        self._scan = scan

    async def find(self, _user_id: str, _key: str) -> RuleAnchor | ScanAnchor | None:
        return self._rule if self._rule is not None else self._scan


def _context(rule: RuleAnchor | None = None, scan: ScanAnchor | None = None) -> AdmissionContext:
    anchors = _Anchors(rule, scan)
    return AdmissionContext("user-1", anchors, anchors)  # type: ignore[arg-type]


async def test_앵커를_요구하지_않는_잡은_아무_사유도_내지_않는다() -> None:
    # 심사 자리를 잡 종류마다 열어 두면 요구하지 않는 잡이 기본 구현으로 통과해야 한다.
    for job_input in (TitleSuggestionJobInput(taskId="task-1"), TaskCleanupJobInput()):
        assert await job_input.admit(_context()) is None


async def test_규칙_생성은_자기_태스크의_사용자_발화만_근거로_받는다() -> None:
    owned = RuleAnchor(id="event-1", task_id="task-1", user_message=True)
    job_input = RuleGenerationJobInput(taskId="task-1", anchorEventId="event-1")

    assert await job_input.admit(_context(rule=owned)) is None


async def test_다른_태스크의_근거는_규칙_생성을_거절한다() -> None:
    other = RuleAnchor(id="event-1", task_id="task-2", user_message=True)
    job_input = RuleGenerationJobInput(taskId="task-1", anchorEventId="event-1")

    assert await job_input.admit(_context(rule=other)) == INVALID_RULE_ANCHOR


async def test_사용자_발화가_아닌_근거는_규칙_생성을_거절한다() -> None:
    machine = RuleAnchor(id="event-1", task_id="task-1", user_message=False)
    job_input = RuleGenerationJobInput(taskId="task-1", anchorEventId="event-1")

    assert await job_input.admit(_context(rule=machine)) == INVALID_RULE_ANCHOR


async def test_없는_앵커는_스캔을_거절한다() -> None:
    job_input = RecipeScanJobInput(taskId="task-1")

    assert await job_input.admit(_context()) == INELIGIBLE_SCAN_ANCHOR


def test_거절은_계약이_정한_상태와_코드를_갖는다() -> None:
    assert (INVALID_RULE_ANCHOR.status, INVALID_RULE_ANCHOR.code) == (400, "job.invalid-rule-anchor")
    assert (INELIGIBLE_SCAN_ANCHOR.status, INELIGIBLE_SCAN_ANCHOR.code) == (400, "job.invalid-scan-anchor")


def test_모든_잡_종류가_자기_심사_자리를_갖는다() -> None:
    # 종류를 더하면서 심사를 잊으면 접수 라우터가 아니라 이 자리가 먼저 드러나야 한다.
    for kind, model in INPUT_MODEL_BY_KIND.items():
        assert hasattr(model, "admit"), kind
