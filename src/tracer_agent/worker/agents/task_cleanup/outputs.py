"""task-cleanup이 낸 제안을 자기 원장의 정리 제안 표에 적는다."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tracer_agent.shared.agents.cleanup.models import (
    CLEANUP_STATUS_PENDING,
    CLEANUP_SUGGESTION_KIND_ARCHIVE,
    CleanupSuggestion,
)
from tracer_agent.shared.agents.cleanup.store import CleanupSuggestionStore
from tracer_agent.shared.agents.runtime.ledger import LedgerSql
from tracer_agent.shared.agents.shared.ids import generate_ulid
from tracer_agent.shared.agents.shared.instant import parse_instant
from tracer_agent.shared.agents.shared.json_view import as_objects, opt_text

from ..runtime.outputs import JobOutputTargets, object_items, write_output
from ..runtime.tracer_client import TracerApiPort

CLEANUP_OUTPUT_LABEL = "cleanup"

TASKS_PATH = "/api/v1/tasks"

# 목록 창구 한 장이 담는 최대 개수이며 값은 계약의 태스크 집합 조회가 소유한다.
MAX_IDS_PER_CALL = 100


async def write_suggestions(
    targets: JobOutputTargets, user_id: str, execution_id: str, data: dict[str, Any]
) -> bool:
    """스캔이 세운 제안을 자기 원장에 적으며 제안이 없으면 아무것도 쓰지 않는다."""
    suggestions = object_items(data, "suggestions")
    if not suggestions:
        return True
    task_ids = [text for item in suggestions if (text := opt_text(item.get("taskId")))]
    # 느린 외부 호출을 트랜잭션 밖에 두어 한 요청이 원장 연결을 쥔 채 기다리지 않게 한다.
    observed = await last_event_at_by_task(targets.tracer, task_ids)
    now = datetime.now(UTC)

    async def work(sql: LedgerSql) -> None:
        store = CleanupSuggestionStore(sql)
        for item in suggestions:
            task_id = opt_text(item.get("taskId"))
            rationale = opt_text(item.get("rationale"))
            if task_id is None or rationale is None:
                continue
            await _write_one(store, user_id, execution_id, task_id, rationale, observed, now)

    return await write_output(targets, CLEANUP_OUTPUT_LABEL, execution_id, work)


async def _write_one(
    store: CleanupSuggestionStore,
    user_id: str,
    job_id: str,
    task_id: str,
    rationale: str,
    observed: dict[str, datetime],
    now: datetime,
) -> None:
    standing = await store.find_pending(user_id, task_id, CLEANUP_SUGGESTION_KIND_ARCHIVE)
    observed_at = observed.get(task_id)
    if standing is not None:
        # 같은 태스크와 종류의 대기 행은 하나뿐이므로 새 근거와 새 관측 시각을 그 행에 겹쳐 적는다.
        await store.refresh_pending(standing.id, job_id, rationale, observed_at)
        return
    await store.insert(
        CleanupSuggestion(
            id=generate_ulid(now),
            user_id=user_id,
            job_id=job_id,
            task_id=task_id,
            kind=CLEANUP_SUGGESTION_KIND_ARCHIVE,
            rationale=rationale,
            status=CLEANUP_STATUS_PENDING,
            created_at=now,
            observed_last_event_at=observed_at,
        )
    )


async def last_event_at_by_task(tracer: TracerApiPort, task_ids: list[str]) -> dict[str, datetime]:
    """태스크는 추적 원장의 것이므로 마지막 사건 시각도 추적의 집합 조회로 읽는다."""
    found: dict[str, datetime] = {}
    unique = list(dict.fromkeys(task_ids))
    for index in range(0, len(unique), MAX_IDS_PER_CALL):
        chunk = unique[index : index + MAX_IDS_PER_CALL]
        payload = await tracer.get(TASKS_PATH, {"ids": ",".join(chunk)})
        items = payload.get("items") if isinstance(payload, dict) else None
        for item in as_objects(items):
            task_id = opt_text(item.get("id"))
            last_event_at = opt_text(item.get("lastEventAt"))
            if task_id is None or last_event_at is None:
                continue
            try:
                found[task_id] = parse_instant(last_event_at)
            except ValueError:
                continue
    return found
