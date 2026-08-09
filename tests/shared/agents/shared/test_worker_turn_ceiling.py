"""전문가에게 알린 턴 상한이 가장 깊은 배정이 실제로 받는 몫과 같은지 검증한다."""

from __future__ import annotations

import json

import pytest

from tracer_agent.shared.agents.recipe_scan.models import MAX_PROBE_TURNS, PROBE_DEPTH_KEY
from tracer_agent.shared.agents.shared.dispatch_depth import depth_shares
from tracer_agent.shared.agents.shared.model_rates import MODEL_RATES_PATH
from tracer_agent.shared.agents.task_cleanup.models import INSPECT_DEPTH_KEY, MAX_INSPECT_TURNS
from tracer_agent.worker.agents.runtime.llm.envelope import model_envelope

CONTRACT_ROOT = MODEL_RATES_PATH.parents[2]

FANOUT = [
    ("task-cleanup", INSPECT_DEPTH_KEY, MAX_INSPECT_TURNS),
    ("recipe-scan", PROBE_DEPTH_KEY, MAX_PROBE_TURNS),
]


@pytest.mark.parametrize(("agent_id", "key", "constant"), FANOUT, ids=lambda one: str(one))
def test_알린_상한이_가장_깊은_몫과_같다(agent_id: str, key: str, constant: int) -> None:
    assert max(depth_shares(agent_id, key).values()) == constant


def test_봉투를_덮는_모델이_출력_예산을_나눠_쓰는_모델과_같다() -> None:
    limits = json.loads((CONTRACT_ROOT / "agent" / "shared" / "execution.limits.json").read_text("utf-8"))
    envelope = json.loads((CONTRACT_ROOT / "agent" / "shared" / "model.envelope.json").read_text("utf-8"))
    overridden = {name for name, value in limits["modelEnvelope"].items() if isinstance(value, dict)}

    assert overridden == set(envelope["sharedOutputBudget"]["appliesTo"])
    assert {name: model_envelope(name).effort for name in overridden} == {"claude-opus-5": "low"}
