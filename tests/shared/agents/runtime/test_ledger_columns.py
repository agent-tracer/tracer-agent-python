"""원장에 적는 문장이 계약의 표에 실재하는 열만 쓰는지 검증한다."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tracer_agent.shared.agents.shared.contract_root import CONTRACT_ROOT

SOURCE_ROOT = Path(__file__).resolve().parents[4] / "src"
MIGRATIONS = CONTRACT_ROOT / "db" / "migrations"

_CREATE = re.compile(r'CREATE TABLE(?: IF NOT EXISTS)?\s+"?(\w+)"?\s*\((.*?)\n\);', re.S)
_ADD = re.compile(r'ALTER TABLE\s+"?(\w+)"?\s+ADD COLUMN(?: IF NOT EXISTS)?\s+"?(\w+)"?')
_RENAME = re.compile(r'ALTER TABLE\s+"?(\w+)"?\s*\n?\s*RENAME COLUMN\s+"?(\w+)"?\s+TO\s+"?(\w+)"?')
_DROP = re.compile(r'ALTER TABLE\s+"?(\w+)"?\s+DROP COLUMN(?: IF EXISTS)?\s+"?(\w+)"?')
_COLUMN = re.compile(r'^"?(\w+)"?\s+\w')
_INSERT = re.compile(r"INSERT INTO\s+(\w+)\s*\(([^)]*)\)", re.S)

# 열이 아니라 표 전체에 걸리는 선언이라 열 이름으로 세지 않는다.
_TABLE_LEVEL = frozenset({"PRIMARY", "UNIQUE", "CONSTRAINT", "FOREIGN", "CHECK", "EXCLUDE"})


def _columns() -> dict[str, set[str]]:
    """마이그레이션을 차례로 적용한 뒤 표마다 남는 열 이름을 낸다."""
    tables: dict[str, set[str]] = {}
    for migration in sorted(MIGRATIONS.glob("*.sql")):
        ddl = migration.read_text(encoding="utf-8")
        for table, body in _CREATE.findall(ddl):
            declared = set()
            for line in body.splitlines():
                stripped = line.strip().rstrip(",")
                named = _COLUMN.match(stripped)
                if named and stripped.split()[0].upper() not in _TABLE_LEVEL:
                    declared.add(named.group(1))
            tables[table] = declared
        for table, column in _ADD.findall(ddl):
            tables.setdefault(table, set()).add(column)
        for table, before, after in _RENAME.findall(ddl):
            tables.setdefault(table, set()).discard(before)
            tables[table].add(after)
        for table, column in _DROP.findall(ddl):
            tables.setdefault(table, set()).discard(column)
    return tables


def _statements() -> list[tuple[str, str, frozenset[str]]]:
    """소스가 적는 적재 문장마다 표 이름과 열 이름을 낸다."""
    found = []
    for source in sorted(SOURCE_ROOT.rglob("*.py")):
        for table, columns in _INSERT.findall(source.read_text(encoding="utf-8")):
            named = frozenset(name.strip() for name in columns.split(",") if name.strip())
            found.append((source.name, table, named))
    return found


TABLES = _columns()
STATEMENTS = _statements()


def test_문장을_실제로_찾는다() -> None:
    # 뽑히는 문장이 없으면 아래 검사가 아무것도 보지 않으면서 통과한다.
    assert len(STATEMENTS) >= 13


@pytest.mark.parametrize(
    ("source", "table", "columns"), STATEMENTS, ids=[f"{name}-{table}" for name, table, _ in STATEMENTS]
)
def test_적재_문장이_계약의_표에_있는_열만_쓴다(source: str, table: str, columns: frozenset[str]) -> None:
    assert table in TABLES, f"{source}: {table}"
    assert not columns - TABLES[table], f"{source}: {table}"
