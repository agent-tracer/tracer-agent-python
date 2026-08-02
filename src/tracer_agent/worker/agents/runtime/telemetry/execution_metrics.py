"""실행 하나가 예산을 어디에 썼고 무엇에 걸렸는지를 계약이 정한 이름으로 낸다."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml
from opentelemetry import metrics

from tracer_agent.worker.agents.shared.contract_prompt_source import CONTRACT_ROOT

_METRICS_PATH = CONTRACT_ROOT / "workflow" / "metrics.yaml"

EXHAUSTION_RATIO = "agent.probe.budget.exhaustion_ratio"
REDISPATCH_ROUNDS = "agent.redispatch.rounds"
VALIDATION_FAILURE = "agent.validation.failure"
MODEL_LANDED = "agent.model.landed"


@lru_cache(maxsize=1)
def _declared() -> dict[str, dict[str, Any]]:
    """계약이 이름을 소유하므로 그 목록에 없는 지표는 세울 수 없다."""
    document = yaml.safe_load(_METRICS_PATH.read_text(encoding="utf-8"))
    return {one["name"]: one for one in document["executionMetrics"]["instruments"]}


def _unit(name: str) -> str:
    declared = _declared().get(name)
    if declared is None:
        raise ValueError(f"metrics.instrument-missing:{name}")
    return str(declared["unit"])


@lru_cache(maxsize=1)
def _instruments() -> dict[str, Any]:
    meter = metrics.get_meter("tracer-agent.execution")
    return {
        EXHAUSTION_RATIO: meter.create_histogram(EXHAUSTION_RATIO, unit=_unit(EXHAUSTION_RATIO)),
        REDISPATCH_ROUNDS: meter.create_histogram(REDISPATCH_ROUNDS, unit=_unit(REDISPATCH_ROUNDS)),
        VALIDATION_FAILURE: meter.create_counter(VALIDATION_FAILURE, unit=_unit(VALIDATION_FAILURE)),
        MODEL_LANDED: meter.create_counter(MODEL_LANDED, unit=_unit(MODEL_LANDED)),
    }


def record_probe_exhaustion(agent: str, probe: str, spent_usd: float | None, granted_usd: float) -> None:
    """전문가 하나가 배분받은 몫 가운데 실제로 쓴 비율이며 1에 가까울수록 그 축의 몫이 모자랐다."""
    if spent_usd is None or granted_usd <= 0:
        return
    _instruments()[EXHAUSTION_RATIO].record(
        min(spent_usd / granted_usd, 1.0), {"agent": agent, "probe": probe}
    )


def record_redispatch_rounds(agent: str, rounds: int) -> None:
    """조율자가 종합 대신 전문가를 다시 부른 라운드 수다."""
    _instruments()[REDISPATCH_ROUNDS].record(rounds, {"agent": agent})


def record_validation_failure(agent: str, repaired: bool) -> None:
    """결정론 검증이 산출을 거절한 실행이며 repaired가 그 뒤 수리가 살렸는지를 가른다."""
    _instruments()[VALIDATION_FAILURE].add(1, {"agent": agent, "repaired": repaired})


def record_model_landed(agent: str) -> None:
    """예산이 다해 도구를 거두고 결론만 받은 실행이다."""
    _instruments()[MODEL_LANDED].add(1, {"agent": agent})
