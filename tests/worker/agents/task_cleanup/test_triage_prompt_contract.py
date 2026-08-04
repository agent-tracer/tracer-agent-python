"""조율자 요청이 싣는 후보 목록을 계약의 케이스로 검증한다."""

from __future__ import annotations

from typing import Any

import pytest

from tests.support.contract import conformance_case
from tests.support.prompts import TASK_CLEANUP_PROMPT
from tracer_agent.shared.agents.task_cleanup.models import CleanupBatch
from tracer_agent.worker.agents.task_cleanup.prompts import build_triage_prompt

_CASES = conformance_case("cleanup.prompt")["triage"]["cases"]


@pytest.mark.parametrize("case", _CASES, ids=lambda case: str(case["name"]))
def test_계약의_케이스마다_같은_후보_목록을_낸다(case: dict[str, Any]) -> None:
    given = case["input"]
    batch = CleanupBatch.model_validate(
        {"candidates": given["candidates"], "batchTruncated": given["batchTruncated"]}
    )

    rendered, _listed = build_triage_prompt(TASK_CLEANUP_PROMPT, batch, given.get("triageCandidateListLimit"))

    for line in case.get("mustContain", []):
        assert line in rendered
    for line in case.get("mustNotContain", []):
        assert line not in rendered
