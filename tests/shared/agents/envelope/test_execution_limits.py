"""실행 카탈로그의 네 종류가 계약이 적은 상한과 칸마다 같은지 검증한다."""

from __future__ import annotations

import json

from tracer_agent.shared.agents.envelope.catalog import CATALOG, MODEL_RATES
from tracer_agent.shared.agents.shared.execution_limits import EXECUTION_LIMITS_PATH
from tracer_agent.shared.agents.shared.job_kinds import AgentJobKind
from tracer_agent.shared.agents.shared.model_tiering import CHAT_KIND, allowed_models


def _declared() -> dict[str, dict[str, object]]:
    document: dict[str, dict[str, dict[str, object]]] = json.loads(
        EXECUTION_LIMITS_PATH.read_text(encoding="utf-8")
    )
    return document["kinds"]


def _wire_of(agent_name: str) -> str:
    if agent_name == CHAT_KIND:
        return CHAT_KIND
    return AgentJobKind(agent_name).wire


def test_계약이_적은_종류를_빠짐없이_안다() -> None:
    assert {_wire_of(name) for name in _declared()} == set(CATALOG)


def test_상한이_계약과_칸마다_같다() -> None:
    for agent_name, limits in _declared().items():
        entry = CATALOG[_wire_of(agent_name)]

        assert entry.default_model == limits["defaultModel"]
        assert entry.fallback_model == limits.get("fallbackModel")
        assert entry.limits.budgetUsd == limits["budgetUsd"]
        assert entry.limits.maxTurns == limits["maxTurns"]
        assert entry.limits.maxOutputTokens == limits["maxOutputTokens"]
        assert entry.deadline_ms == limits["deadlineMs"]


def test_허용_모델_목록이_계약과_같다() -> None:
    for agent_name, limits in _declared().items():
        assert allowed_models(_wire_of(agent_name)) == tuple(limits["allowedModels"])  # type: ignore[arg-type]


def test_기본_모델과_대체_모델의_단가를_계약이_안다() -> None:
    for entry in CATALOG.values():
        assert entry.default_model in MODEL_RATES
        assert entry.fallback_model is None or entry.fallback_model in MODEL_RATES


def test_대체_모델이_그_종류의_허용_목록_안에_있다() -> None:
    for wire, entry in CATALOG.items():
        assert entry.fallback_model is None or entry.fallback_model in allowed_models(wire)
