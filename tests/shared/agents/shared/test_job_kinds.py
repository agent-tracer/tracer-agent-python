"""잡 종류를 손으로 다시 적는 자리가 없고 모든 표가 정본 하나를 덮는지 검증한다."""

from __future__ import annotations

from tracer_agent.shared.agents.envelope.catalog import CATALOG
from tracer_agent.shared.agents.envelope.catalog import JOB_KINDS as CATALOG_JOB_KINDS
from tracer_agent.shared.agents.shared.job_kinds import (
    JOB_AGENT_NAMES,
    JOB_EXECUTOR,
    JOB_KIND_BY_AGENT_NAME,
    JOB_KINDS,
    AgentJobKind,
)
from tracer_agent.shared.agents.shared.model_tiering import ALLOWED_MODELS, CHAT_KIND
from tracer_agent.shared.workflows.jobs_input import IDEMPOTENCY_KEYS, INPUT_MODEL_BY_KIND
from tracer_agent.shared.workflows.jobs_query import JOB_LEDGER_KINDS
from tracer_agent.worker.agents.runtime.telemetry.attributes import AGENT_JOB_KIND
from tracer_agent.worker.worker import JOB_AGENT_NAMES as WORKER_JOB_AGENT_NAMES

_WIRE = frozenset(kind.wire for kind in AgentJobKind)


def test_정본이_세_종류를_계약의_값으로_갖는다() -> None:
    assert {"title.suggestion", "recipe.scan", "task.cleanup"} == _WIRE
    assert frozenset(JOB_KINDS) == _WIRE
    assert frozenset(JOB_AGENT_NAMES) == {kind.value for kind in AgentJobKind}


def test_잡_종류를_읽는_표가_모두_정본에서_나온다() -> None:
    derived = (
        frozenset(JOB_EXECUTOR),
        frozenset(JOB_LEDGER_KINDS),
        frozenset(CATALOG_JOB_KINDS),
        frozenset(CATALOG) - {CHAT_KIND},
        frozenset(ALLOWED_MODELS) - {CHAT_KIND},
        frozenset(INPUT_MODEL_BY_KIND),
        frozenset(IDEMPOTENCY_KEYS),
        frozenset(AGENT_JOB_KIND.values()),
        frozenset(JOB_KIND_BY_AGENT_NAME.values()),
    )

    assert all(table == _WIRE for table in derived)


def test_에이전트_이름을_읽는_표도_정본에서_나온다() -> None:
    assert JOB_AGENT_NAMES == WORKER_JOB_AGENT_NAMES
    assert frozenset(AGENT_JOB_KIND) == frozenset(JOB_AGENT_NAMES)
