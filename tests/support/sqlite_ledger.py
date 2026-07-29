"""원장 문장을 브로커 없이 평가하도록 chat 쓰기 테이블을 sqlite로 세운 문장 실행 표면이다."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from tracer_agent.shared.agents.runtime.ledger import SqlRow, UniqueViolation

# 사전식 비교가 곧 시간 비교가 되도록 원장의 모든 시각을 UTC 한 형식으로만 적는다.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"

TIMESTAMP_COLUMNS = frozenset({"created_at", "updated_at", "started_at", "completed_at"})
JSON_COLUMNS = frozenset({"usage", "tool_calls", "validation", "model_calls", "result"})

_PLACEHOLDER = re.compile(r"\$(\d+)")

SCHEMA = """
CREATE TABLE chat_threads (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    backend TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE VIEW agent_chat_thread_view AS SELECT id, user_id, summary FROM chat_threads;

CREATE TABLE chat_executions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    user_message_id TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_backend TEXT,
    model TEXT,
    language TEXT,
    draft_text TEXT NOT NULL DEFAULT '',
    draft_seq INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 0,
    draft_token_hash TEXT,
    assistant_message_id TEXT,
    model_used TEXT,
    cost_usd REAL,
    num_turns INTEGER,
    stop_reason TEXT,
    usage TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE UNIQUE INDEX chat_executions_running_thread
    ON chat_executions (thread_id) WHERE status = 'running';

CREATE UNIQUE INDEX chat_executions_idempotency
    ON chat_executions (user_id, thread_id, client_request_id);

CREATE TABLE chat_messages (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_calls TEXT,
    tool_call_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE chat_execution_steps (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    truncated INTEGER NOT NULL DEFAULT 0,
    tool_calls TEXT,
    tool_name TEXT,
    tool_call_id TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_creation_tokens INTEGER,
    stop_reason TEXT,
    node_name TEXT,
    event_kind TEXT,
    duration_ms INTEGER,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX chat_execution_steps_execution_attempt_seq
    ON chat_execution_steps (execution_id, attempt, seq);

CREATE TABLE graph_job_executions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    idempotency_key TEXT,
    task_id TEXT,
    status TEXT NOT NULL,
    budget_usd REAL NOT NULL,
    cost_usd REAL,
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE UNIQUE INDEX graph_job_executions_idempotency
    ON graph_job_executions (kind, idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE agent_run_observations (
    execution_id TEXT NOT NULL, attempt_id TEXT NOT NULL, user_id TEXT NOT NULL,
    job_id TEXT, experiment_id TEXT, example_id TEXT, variant_id TEXT,
    agent_name TEXT NOT NULL, backend TEXT NOT NULL, model_requested TEXT NOT NULL,
    model_actual TEXT, prompt_version TEXT NOT NULL, prompt_content_hash TEXT NOT NULL,
    tool_contract_version TEXT NOT NULL, evaluator_set_version TEXT, status TEXT NOT NULL,
    duration_ms INTEGER NOT NULL, usage TEXT NOT NULL, cost_usd REAL, landed INTEGER NOT NULL,
    repair_attempted INTEGER NOT NULL, validation TEXT NOT NULL, model_calls TEXT NOT NULL,
    tool_calls TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (execution_id, attempt_id)
);
"""


def encode(value: Any) -> Any:
    """원장 열 값을 sqlite가 저장할 수 있는 스칼라로 바꾼다."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime(TIMESTAMP_FORMAT)
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return int(value)
    return value


def decode(column: str, value: Any) -> Any:
    """저장된 스칼라를 원장 열의 본래 형태로 되돌린다."""
    if value is None:
        return None
    if column in TIMESTAMP_COLUMNS:
        return datetime.strptime(str(value), TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    if column in JSON_COLUMNS:
        return json.loads(str(value))
    return value


def _row(row: sqlite3.Row) -> SqlRow:
    return {key: decode(key, value) for key, value in dict(row).items()}


def rewrite(sql: str) -> tuple[str, list[int]]:
    """번호 자리표시자를 sqlite 물음표로 바꾸고 그 자리에 올 인자 순서를 함께 낸다."""
    order = [int(match.group(1)) for match in _PLACEHOLDER.finditer(sql)]
    return _PLACEHOLDER.sub("?", sql), order


class SqliteLedgerSql:
    """chat 쓰기 테이블을 메모리에 세우고 원장 문장을 그대로 평가한다."""

    def __init__(self) -> None:
        # HTTP 테스트는 앱을 다른 스레드에서 돌리므로 연결을 만든 스레드 밖에서도 쓰게 연다.
        self._connection = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(SCHEMA)

    async def fetch(self, sql: str, *args: Any) -> list[SqlRow]:
        """문장 하나를 돌리고 돌아온 행을 원장 열의 형태로 낸다."""
        if "INSERT INTO agent_run_observations" in sql:
            return self._insert_observation(str(args[0]), json.dumps(args[1]), args[2])
        statement, order = rewrite(sql)
        bound = [encode(args[index - 1]) for index in order]
        try:
            cursor = self._connection.execute(statement, bound)
        except sqlite3.IntegrityError as error:
            raise UniqueViolation(str(error)) from error
        return [_row(row) for row in cursor.fetchall()]

    def _insert_observation(self, user_id: str, raw: str, created_at: Any) -> list[SqlRow]:
        payload = json.loads(raw)
        columns = {
            "execution_id": payload["executionId"],
            "attempt_id": payload["attemptId"],
            "user_id": user_id,
            "job_id": payload.get("jobId"),
            "experiment_id": payload.get("experimentId"),
            "example_id": payload.get("exampleId"),
            "variant_id": payload.get("variantId"),
            "agent_name": payload["agentName"],
            "backend": payload["backend"],
            "model_requested": payload["modelRequested"],
            "model_actual": payload.get("modelActual"),
            "prompt_version": payload["promptVersion"],
            "prompt_content_hash": payload["promptContentHash"],
            "tool_contract_version": payload["toolContractVersion"],
            "evaluator_set_version": payload.get("evaluatorSetVersion"),
            "status": payload["status"],
            "duration_ms": payload["durationMs"],
            "usage": payload["usage"],
            "cost_usd": payload.get("costUsd"),
            "landed": payload["landed"],
            "repair_attempted": payload["repairAttempted"],
            "validation": payload["validation"],
            "model_calls": payload["modelCalls"],
            "tool_calls": payload["toolCalls"],
            "created_at": created_at,
        }
        names = ", ".join(columns)
        marks = ", ".join("?" for _ in columns)
        cursor = self._connection.execute(
            f"INSERT OR IGNORE INTO agent_run_observations ({names}) VALUES ({marks})",
            [encode(value) for value in columns.values()],
        )
        return [{"execution_id": payload["executionId"]}] if cursor.rowcount == 1 else []

    def transaction(self) -> AbstractAsyncContextManager[None]:
        """여러 문장이 함께 남거나 함께 사라지는 경계를 연다."""
        return self._transaction()

    def seed(self, table: str, rows: Iterable[Mapping[str, Any]]) -> None:
        """테스트가 준비한 행을 그대로 심는다."""
        for row in rows:
            columns = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            values = [encode(value) for value in row.values()]
            self._connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", values)

    def rows(self, table: str) -> list[SqlRow]:
        """테이블에 남은 행을 저장 순서대로 읽는다."""
        cursor = self._connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
        return [_row(row) for row in cursor.fetchall()]

    def close(self) -> None:
        """메모리 데이터베이스를 닫는다."""
        self._connection.close()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[None]:
        self._connection.execute("BEGIN")
        try:
            yield
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        self._connection.execute("COMMIT")
