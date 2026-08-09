"""계약이 세운 원장 제약마다 그 갈래를 지나는 자리가 있는지 목록으로 드러낸다."""

from __future__ import annotations

import re

from tracer_agent.shared.agents.shared.contract_root import CONTRACT_ROOT

MIGRATIONS = CONTRACT_ROOT / "db" / "migrations"

# 기본 키는 어느 삽입이든 지나므로 세지 않고, 유일 색인과 이름 있는 CHECK 만 본다.
_NAMED = re.compile(r'UNIQUE INDEX (?:IF NOT EXISTS )?"(?P<index>\w+)"|CONSTRAINT "(?P<check>\w+)"\s*CHECK')

# 모양이 같은지는 대역 대조가 보고 여기서는 그 제약을 실제로 지나는 자리가 있는지만 본다.
COVERED_BY: dict[str, str | None] = {
    "chat_executions_idempotency": None,
    "chat_executions_requested_backend_check": None,
    "chat_executions_running_thread": None,
    "chat_execution_steps_execution_attempt_seq": None,
    "chat_user_memories_unique": None,
    "chat_threads_summary_pairing": "tests/support/chat_surface.py 의 seed_thread 가 짝을 맞춰 심는다",
    "ai_jobs_idempotency_key": "tests/shared/workflows/test_jobs_enqueue.py",
    "ai_job_steps_job_attempt_seq": "tests/shared/workflows/test_jobs_ledger.py",
}


def _declared() -> set[str]:
    found = (
        one
        for path in sorted(MIGRATIONS.glob("*.sql"))
        for one in _NAMED.finditer(path.read_text(encoding="utf-8"))
    )
    return {one["index"] or one["check"] for one in found}


def test_계약이_세운_제약을_이_표가_빠짐없이_담는다() -> None:
    # 계약이 제약을 더하면 여기가 서서 그 갈래를 지나는 자리가 필요하다는 것을 알린다.
    assert _declared() == set(COVERED_BY)


def test_덮인_자리가_가리키는_파일이_실재한다() -> None:
    root = CONTRACT_ROOT.parent
    paths = [where for where in COVERED_BY.values() if where is not None]

    assert paths
    for where in paths:
        assert (root / where.split(" ")[0]).exists(), where
