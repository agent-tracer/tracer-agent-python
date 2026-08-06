"""recipe-scan 인용 검증 판정과 후보 스키마 수용을 계약의 케이스로 검증한다."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from tests.support.contract import agent_cases
from tracer_agent.shared.agents.recipe_scan.models import ProvenanceCatalog, RecipeCandidate
from tracer_agent.worker.agents.recipe_scan.policy import validate_recipe_candidates

_CONTRACT = agent_cases("recipe-scan")["cases"]
_SCHEMA_CASES = _CONTRACT["candidateSchema"]["cases"]


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


@pytest.mark.parametrize("case", _SCHEMA_CASES, ids=lambda case: str(case["name"]))
def test_후보_스키마가_계약이_정한_칸만_받아들인다(case: dict[str, Any]) -> None:
    payload = {**_CONTRACT["candidateDefaults"], **case["candidate"]}
    expect = case["expect"]

    if expect["accepted"]:
        RecipeCandidate.model_validate(payload)
        return
    # 인용 검증보다 앞선 자리라 거절된 후보는 사유 목록이 아니라 후보 자체가 서지 못한다.
    with pytest.raises(ValidationError) as rejected:
        RecipeCandidate.model_validate(payload)
    assert {error["loc"][0] for error in rejected.value.errors()} == {expect["field"]}
