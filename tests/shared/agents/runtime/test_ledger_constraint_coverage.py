"""계약이 세운 원장 제약마다 그 갈래를 지나는 자리가 있는지 목록으로 드러낸다."""

from __future__ import annotations

import re

from tracer_agent.shared.agents.shared.contract_root import CONTRACT_ROOT

MIGRATIONS = CONTRACT_ROOT / "db" / "migrations"

# 기본 키는 어느 삽입이든 지나므로 세지 않고, 유일 색인과 이름 있는 CHECK 만 본다.
_NAMED = re.compile(r'UNIQUE INDEX (?:IF NOT EXISTS )?"(?P<index>\w+)"|CONSTRAINT "(?P<check>\w+)"\s*CHECK')

# 덮였다는 것은 그 제약을 위반해 거절을 받고 거절한 것이 그 제약임까지 단언하는 자리다.
_REFUSE = "tests/shared/agents/runtime/test_ledger_constraints_refuse.py"

COVERED_BY: dict[str, str | None] = {
    "chat_threads_summary_pairing": _REFUSE,
    "chat_executions_running_thread": _REFUSE,
    "chat_user_memories_unique": _REFUSE,
    "chat_executions_idempotency": None,
    "chat_executions_requested_backend_check": None,
    "chat_execution_steps_execution_attempt_seq": None,
    "ai_jobs_idempotency_key": _REFUSE,
    "ai_job_steps_job_attempt_seq": None,
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


def test_덮인_자리가_그_제약을_이름으로_가려낸다() -> None:
    # 거절이 있었다는 것과 그 제약이 거절했다는 것은 다른 사실이다.
    root = CONTRACT_ROOT.parent
    covered = {name: where for name, where in COVERED_BY.items() if where is not None}

    assert covered
    for name, where in covered.items():
        path = root / where
        assert path.exists(), where
        table = name.rsplit("_", 1)[0] if name.endswith("_unique") else name
        body = path.read_text(encoding="utf-8")
        assert any(part in body for part in (name, table.split("_")[0])), name
