"""recipe-scan 인용 검증 판정을 계약의 케이스로 검증한다."""

from __future__ import annotations

from typing import Any

import pytest

from tests.support.contract import agent_cases
from tracer_agent.shared.agents.recipe_scan.models import ProvenanceCatalog, RecipeCandidate
from tracer_agent.worker.agents.recipe_scan.policy import validate_recipe_candidates

_CONTRACT = agent_cases("recipe-scan")["cases"]


def _candidates(case: dict[str, Any]) -> list[RecipeCandidate]:
    return [
        RecipeCandidate.model_validate({**_CONTRACT["candidateDefaults"], **override})
        for override in case["candidates"]
    ]


@pytest.mark.parametrize("case", _CONTRACT["cases"], ids=lambda case: str(case["name"]))
def test_계약의_케이스마다_같은_판정과_같은_사유를_낸다(case: dict[str, Any]) -> None:
    provenance = ProvenanceCatalog.model_validate(_CONTRACT["provenance"])

    errors = validate_recipe_candidates(_candidates(case), _CONTRACT["anchorTaskId"], provenance)

    assert errors == case["expect"]["errors"]
    assert (not errors) == case["expect"]["valid"]
