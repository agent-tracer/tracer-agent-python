"""조율자 요청이 싣는 인용 가능한 식별자를 계약의 케이스로 검증한다."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from tests.support.contract import conformance_case
from tests.support.prompts import RECIPE_SCAN_PROMPT
from tracer_agent.shared.agents.recipe_scan.models import ProvenanceCatalog
from tracer_agent.worker.agents.recipe_scan.prompts import render_citable_ids

_CASES = conformance_case("recipe.prompt")["investigate"]["cases"]


def _catalog(declared: dict[str, Any]) -> ProvenanceCatalog:
    return ProvenanceCatalog(
        eventIdsByTask={task: set(ids) for task, ids in declared["eventIdsByTask"].items()},
        turnIdsByTask={task: set(ids) for task, ids in declared["turnIdsByTask"].items()},
        ruleIds=set(declared["ruleIds"]),
        recipeRevs=dict(declared["recipeRevs"]),
    )


@pytest.mark.parametrize("case", _CASES, ids=lambda case: str(case["name"]))
def test_계약의_케이스마다_같은_인용_목록을_낸다(case: dict[str, Any]) -> None:
    given = case["input"]
    limit = given.get("citableIdListLimit")
    target = "tracer_agent.worker.agents.recipe_scan.prompts.load_citable_id_list_limit"
    with patch(target, return_value=limit if limit is not None else 40):
        rendered = render_citable_ids(RECIPE_SCAN_PROMPT, _catalog(given["provenance"]))

    for line in case.get("mustContain", []):
        assert line in rendered
    for line in case.get("mustNotContain", []):
        assert line not in rendered
