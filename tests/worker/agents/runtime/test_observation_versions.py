"""관측이 구현체가 지어낸 판이 아니라 계약이 준 판을 싣는지 검증한다."""

from __future__ import annotations

from tests.support.prompts import CONTRACT_VERSION
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace


def _observation(agent_name: str) -> object:
    return ExecutionTrace().to_observation(
        execution_id="e1",
        attempt_id="1",
        job_id=None,
        agent_name=agent_name,
        model_requested="claude-haiku-4-5",
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
        duration_ms=10,
        ttft_ms=None,
        error_subtype=None,
    )


def test_계약이_준_판을_그대로_싣는다() -> None:
    observation = _observation("chat")

    assert observation.promptVersion == "v0.0.1"  # type: ignore[attr-defined]
    assert observation.toolContractVersion == "v0.0.1"  # type: ignore[attr-defined]


def test_관측은_해시를_하나도_싣지_않는다() -> None:
    carried = _observation("chat").model_dump()  # type: ignore[attr-defined]

    assert not [key for key in carried if "Hash" in key]
