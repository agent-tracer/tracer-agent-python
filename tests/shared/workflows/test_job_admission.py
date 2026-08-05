"""잡 종류마다의 접수 자격 심사가 자기 입력 모델에 붙어 있는지 검증한다(네트워크 없음)."""

from __future__ import annotations

from tracer_agent.shared.workflows.jobs_anchor import ScanAnchor
from tracer_agent.shared.workflows.jobs_input import (
    INELIGIBLE_SCAN_ANCHOR,
    INPUT_MODEL_BY_KIND,
    AdmissionContext,
    RecipeScanJobInput,
    TaskCleanupJobInput,
    TitleSuggestionJobInput,
)


class _Anchors:
    """스캔 앵커 창구를 정해 둔 답 하나로 대신한다."""

    def __init__(self, scan: ScanAnchor | None = None) -> None:
        self._scan = scan

    async def find(self, _user_id: str, _key: str) -> ScanAnchor | None:
        return self._scan


def _context(scan: ScanAnchor | None = None) -> AdmissionContext:
    return AdmissionContext("user-1", _Anchors(scan))  # type: ignore[arg-type]


async def test_앵커를_요구하지_않는_잡은_아무_사유도_내지_않는다() -> None:
    # 심사 자리를 잡 종류마다 열어 두면 요구하지 않는 잡이 기본 구현으로 통과해야 한다.
    for job_input in (TitleSuggestionJobInput(taskId="task-1"), TaskCleanupJobInput()):
        assert await job_input.admit(_context()) is None


async def test_없는_앵커는_스캔을_거절한다() -> None:
    job_input = RecipeScanJobInput(taskId="task-1")

    assert await job_input.admit(_context()) == INELIGIBLE_SCAN_ANCHOR


def test_거절은_계약이_정한_상태와_코드를_갖는다() -> None:
    assert (INELIGIBLE_SCAN_ANCHOR.status, INELIGIBLE_SCAN_ANCHOR.code) == (400, "job.invalid-scan-anchor")


def test_모든_잡_종류가_자기_심사_자리를_갖는다() -> None:
    # 종류를 더하면서 심사를 잊으면 접수 라우터가 아니라 이 자리가 먼저 드러나야 한다.
    for kind, model in INPUT_MODEL_BY_KIND.items():
        assert hasattr(model, "admit"), kind
