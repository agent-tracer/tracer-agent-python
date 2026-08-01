"""title-suggestion 제목 검증 판정을 계약의 케이스로 검증한다."""

from __future__ import annotations

from typing import Any

import pytest

from tests.support.contract import agent_cases
from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionDraft
from tracer_agent.worker.agents.title_suggestion.policy import validate_title_candidate

_CONTRACT = agent_cases("title-suggestion")["cases"]


@pytest.mark.parametrize("case", _CONTRACT["cases"], ids=lambda case: str(case["name"]))
def test_계약의_케이스마다_같은_판정과_같은_사유를_낸다(case: dict[str, Any]) -> None:
    candidate = TitleSuggestionDraft.model_validate({"suggestions": case["suggestions"]})

    errors = validate_title_candidate(candidate, _CONTRACT["currentTitle"])

    assert errors == case["expect"]["errors"]
    assert (not errors) == case["expect"]["valid"]
