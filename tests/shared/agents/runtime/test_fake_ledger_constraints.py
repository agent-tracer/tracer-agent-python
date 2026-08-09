"""원장 대역이 실물이 거는 제약을 빠짐없이 갖는지 검증한다."""

from __future__ import annotations

import re

from tracer_agent.shared.agents.runtime.__fakes__ import sqlite_ledger
from tracer_agent.shared.agents.shared.contract_root import CONTRACT_ROOT

MIGRATIONS = CONTRACT_ROOT / "db" / "migrations"

_CHECK = re.compile(r"CHECK\s*\((?P<body>.+?)\)\s*[;,\n]", re.S)
_UNIQUE = re.compile(r'UNIQUE INDEX IF NOT EXISTS "(?P<name>\w+)"')


def _normalized(sql: str) -> str:
    """따옴표와 공백만 다른 같은 제약을 한 글자로 본다."""
    return re.sub(r"[\s\"]+", "", sql).lower()


def _fake_schema() -> str:
    source = sqlite_ledger.__file__
    with open(source, encoding="utf-8") as handle:
        return handle.read()


def test_실물이_거는_CHECK_를_대역도_건다() -> None:
    # 대역이 실물보다 관대하면 그 관대함만큼 스위트가 통과시키는 상태가 늘어난다.
    declared = {
        _normalized(found["body"])
        for path in sorted(MIGRATIONS.glob("*.sql"))
        for found in _CHECK.finditer(path.read_text(encoding="utf-8"))
    }
    fake = _normalized(_fake_schema())

    assert declared
    for body in sorted(declared):
        assert body in fake, body


def test_실물이_거는_UNIQUE_를_대역도_건다() -> None:
    declared = {
        found["name"]
        for path in sorted(MIGRATIONS.glob("*.sql"))
        for found in _UNIQUE.finditer(path.read_text(encoding="utf-8"))
    }
    fake = _fake_schema()

    assert declared
    for name in sorted(declared):
        assert name in fake, name
