"""task-cleanup의 결정 검증과 조건부 분기 정책을 소유한다."""

from __future__ import annotations

from tracer_agent.shared.agents.task_cleanup.models import (
    CleanupCandidate,
    CleanupDraftSuggestion,
    TaskCleanupState,
)

from ..runtime.routing import ValidationReasons


def validate_suggestions(
    suggestions: list[CleanupDraftSuggestion],
    state: TaskCleanupState,
) -> tuple[list[CleanupDraftSuggestion], list[str]]:
    """겹치거나 상한을 넘은 제안은 지우고 근거가 어긋난 제안만 모델이 고칠 사유로 남긴다."""
    exposed = state["exposed_candidates"]
    valid: list[CleanupDraftSuggestion] = []
    errors: list[str] = []
    seen: set[str] = set()
    for suggestion in _deduplicated(suggestions, seen):
        item_errors = _evidence_errors(suggestion, exposed, state["event_ids_by_task"])
        if item_errors:
            errors.extend(item_errors)
        else:
            valid.append(suggestion)
    # 어느 꼬리를 버릴지는 프롬프트가 알린 우선순위가 정하며 여기서는 사유 없이 그 꼬리를 지운다.
    return valid[: state["max_suggestions"]], errors


def _deduplicated(suggestions: list[CleanupDraftSuggestion], seen: set[str]) -> list[CleanupDraftSuggestion]:
    """같은 태스크를 두 번 제안하면 뒤의 것은 앞의 것과 같은 말이라 지운다."""
    kept: list[CleanupDraftSuggestion] = []
    for suggestion in suggestions:
        if suggestion.taskId in seen:
            continue
        seen.add(suggestion.taskId)
        kept.append(suggestion)
    return kept


def _evidence_errors(
    suggestion: CleanupDraftSuggestion,
    exposed: dict[str, CleanupCandidate],
    event_ids_by_task: dict[str, set[str]],
) -> list[str]:
    """제안이 도구가 실제로 보여준 후보와 이벤트에 기대는지 본다."""
    errors: list[str] = []
    candidate = exposed.get(suggestion.taskId)
    if candidate is None:
        errors.append(f"unsupported candidate task ID {suggestion.taskId}")
    inspected = suggestion.taskId in event_ids_by_task
    supported_events = event_ids_by_task.get(suggestion.taskId, set())
    cited_events = set(suggestion.evidenceEventIds)
    unknown_events = sorted(cited_events - supported_events)
    if unknown_events:
        errors.append(f"unsupported event IDs for task {suggestion.taskId}: {', '.join(unknown_events)}")
    if candidate is not None and candidate.hasEvents and not inspected:
        errors.append(f"eventful task {suggestion.taskId} was never inspected")
    elif supported_events and not cited_events:
        errors.append(f"eventful task {suggestion.taskId} has no inspected event evidence")
    return errors


VALIDATION_REASONS = ValidationReasons(
    passed="suggestions passed deterministic provenance validation",
    repairable="suggestions failed validation and one repair attempt remains",
    exhausted="invalid suggestions were dropped after the repair attempt",
)


def has_suggestions(state: TaskCleanupState) -> bool:
    """검증을 통과해도 남은 제안이 없으면 빈 결과로 보내야 하므로 그 조건을 여기서 정한다."""
    return bool(state["suggestions"])
