"""recipe-scan의 검증과 조건부 분기 정책을 소유한다."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from tracer_agent.shared.agents.recipe_scan.models import (
    MAX_RECIPE_CANDIDATES,
    ProvenanceCatalog,
    RecipeCandidate,
    RecipeScanState,
)

from ..runtime.execution.trace import ExecutionTrace
from ..runtime.routing import build_validation_router

ValidationRoute = Callable[[RecipeScanState], Literal["repair", "finalize", "empty"]]


def validate_recipe_candidates(
    candidates: list[RecipeCandidate],
    anchor_task_id: str,
    provenance: ProvenanceCatalog,
) -> list[str]:
    """후보 목록이 수집한 출처만 인용하고 같은 turn을 두 번 쓰지 않는지 검증한다."""
    # 근거가 없으면 후보를 내지 않는 것이 옳은 답이므로 빈 출력은 오류가 아니다.
    if not candidates:
        return []
    if len(candidates) > MAX_RECIPE_CANDIDATES:
        return [f"At most {MAX_RECIPE_CANDIDATES} recipe candidates may be returned."]
    errors: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        errors.extend(
            f"Recipe {index}: {error}"
            for error in validate_recipe_candidate(candidate, anchor_task_id, provenance)
        )
    errors.extend(_duplicate_turn_errors(candidates))
    return errors


def _duplicate_turn_errors(candidates: list[RecipeCandidate]) -> list[str]:
    claimed: dict[tuple[str, str], int] = {}
    errors: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        for item in candidate.contributing_slices:
            for turn_id in item.turnIds:
                owner = claimed.setdefault((item.taskId, turn_id), index)
                if owner != index:
                    errors.append(f"Recipe {index}: turn {turn_id} was already claimed by recipe {owner}.")
    return errors


def validate_recipe_candidate(
    candidate: RecipeCandidate,
    anchor_task_id: str,
    provenance: ProvenanceCatalog,
) -> list[str]:
    """recipe 후보가 수집한 출처만 인용하는지 검증한다."""
    errors: list[str] = []
    slices = {item.taskId: item for item in candidate.contributing_slices}
    if anchor_task_id not in slices:
        errors.append(f"contributing_slices must include anchor task {anchor_task_id}.")
    elif not slices[anchor_task_id].eventIds:
        errors.append("The anchor contributing slice must cite at least one anchor event ID.")
    all_event_ids = set().union(*provenance.eventIdsByTask.values()) if provenance.eventIdsByTask else set()
    cited_event_ids: set[str] = set()
    for item in candidate.contributing_slices:
        # 목록에 이름만 오른 태스크는 근거가 아니라 이벤트를 읽은 태스크만 기여할 수 있다.
        if item.taskId not in provenance.eventIdsByTask:
            errors.append(f"Unsupported contributing task ID: {item.taskId}.")
        unknown_turns = sorted(set(item.turnIds) - provenance.turnIdsByTask.get(item.taskId, set()))
        if unknown_turns:
            errors.append(f"Unsupported turn IDs for task {item.taskId}: {', '.join(unknown_turns)}.")
        supported = provenance.eventIdsByTask.get(item.taskId, set())
        unknown = sorted(set(item.eventIds) - supported)
        if unknown:
            errors.append(f"Unsupported event IDs for task {item.taskId}: {', '.join(unknown)}.")
        cited_event_ids.update(item.eventIds)
    if not cited_event_ids:
        errors.append("At least one representative event ID must be cited in contributing_slices.")
    for index, correction in enumerate(candidate.corrections):
        unknown = sorted(set(correction.evidence) - all_event_ids)
        if unknown:
            errors.append(f"Correction {index + 1} cites unsupported event IDs: {', '.join(unknown)}.")
    for index, pitfall in enumerate(candidate.pitfalls):
        unknown = sorted(set(pitfall.evidence) - all_event_ids)
        if unknown:
            errors.append(f"Pitfall {index + 1} cites unsupported event IDs: {', '.join(unknown)}.")
    unknown_rules = sorted(set(candidate.governing_rules) - provenance.ruleIds)
    if unknown_rules:
        errors.append(f"Unsupported governing rule IDs: {', '.join(unknown_rules)}.")
    if candidate.revises_recipe_id and candidate.revises_recipe_id not in provenance.recipeRevs:
        errors.append(f"Unsupported revises_recipe_id: {candidate.revises_recipe_id}.")
    return errors


def build_routes(trace: ExecutionTrace, validation_node: str) -> ValidationRoute:
    """검증 결과에 따른 분기 함수를 만든다."""
    return build_validation_router(
        trace,
        validation_node,
        pass_reason="candidate passed deterministic provenance validation",
        repair_reason="candidate failed validation and the one repair attempt is available",
        exhausted_reason="candidate remained invalid after the repair attempt",
    )
