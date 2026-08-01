"""두 축의 관측 원장 INSERT가 같은 칸 목록을 같은 수의 값으로 채우는지 검증한다."""

from __future__ import annotations

from tracer_agent.worker.agents.chat.execution_writer import _INSERT_OBSERVATION as CHAT_INSERT
from tracer_agent.worker.workflows.jobs_writer import _INSERT_OBSERVATION as JOBS_INSERT


def _columns(statement: str) -> list[str]:
    body = statement.split("agent_run_observations (", 1)[1].split(")", 1)[0]
    return [name.strip() for name in body.split(",")]


def _values(statement: str) -> list[str]:
    body = statement.split("SELECT", 1)[1].split("FROM", 1)[0]
    return [value.strip() for value in body.split(",")]


def test_대화_축의_INSERT가_칸_수만큼_값을_싣는다() -> None:
    assert len(_columns(CHAT_INSERT)) == len(_values(CHAT_INSERT))


def test_잡_축의_INSERT가_칸_수만큼_값을_싣는다() -> None:
    assert len(_columns(JOBS_INSERT)) == len(_values(JOBS_INSERT))


def test_두_축이_관측_원장의_같은_칸_목록을_쓴다() -> None:
    assert _columns(CHAT_INSERT) == _columns(JOBS_INSERT)
