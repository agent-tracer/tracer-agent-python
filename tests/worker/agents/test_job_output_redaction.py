"""세 잡의 산출이 사용자 경계를 넘기 전에 계약의 output 자리를 지나는지 검증한다."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.support.chat_surface import SingleSql
from tracer_agent.shared.agents.recipe_scan.models import (
    ProvenanceWire,
    RecipeCandidate,
    RecipeScanResult,
)
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.shared.json_view import JsonObject
from tracer_agent.shared.agents.shared.redaction import is_suspect_text, marker
from tracer_agent.shared.agents.task_cleanup.models import CleanupDraftSuggestion, CleanupResult
from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestion, TitleSuggestionDraft
from tracer_agent.worker.agents.recipe_scan.agent import RECIPE_SCAN_JOB
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.outputs import JobOutputTargets
from tracer_agent.worker.agents.task_cleanup.agent import TASK_CLEANUP_JOB
from tracer_agent.worker.agents.title_suggestion.agent import TITLE_SUGGESTION_JOB

CREDENTIAL = "sk-ant-api03-0123456789abcdef"


def _recipe_scan_result() -> JsonObject:
    """모델이 자유 본문마다 자격을 적은 스캔 산출이다."""
    values: dict[str, Any] = {
        "title": "Add a safe migration",
        "intent": "스키마 변경을 안전하게 넣는다",
        "description": "저장된 모양이 바뀔 때 쓴다.",
        "use_when": [f"{CREDENTIAL} 이 실린 로그를 읽는다"],
        "summary_md": f"- 열쇠 {CREDENTIAL} 를 그대로 적었다",
        "request": "사용자가 마이그레이션 추가를 요청했다.",
        "corrections": [
            {
                "whatAgentDid": f"{CREDENTIAL} 을 로그에 남겼다",
                "howCorrected": "그 값을 지웠다",
                "evidence": ["event-1"],
            }
        ],
        "pitfalls": [
            {
                "pitfall": f"{CREDENTIAL} 이 본문에 남는다",
                "whyNonObvious": "한 번만 읽으면 드러나지 않는다",
                "evidence": ["event-1"],
            }
        ],
        "contributing_slices": [{"taskId": "task-1", "turnIds": ["turn-1"], "eventIds": ["event-1"]}],
        "rationale": "반복 가능한 절차다.",
    }
    return RECIPE_SCAN_JOB.result_of(
        {
            "result": RecipeScanResult(
                recipes=[RecipeCandidate.model_validate(values)],
                provenance=ProvenanceWire(recipeRevs={"recipe-1": 3}),
            )
        }
    )


def _task_cleanup_result() -> JsonObject:
    """모델이 판단 근거에 자격을 적은 정리 산출이다."""
    return TASK_CLEANUP_JOB.result_of(
        {
            "result": CleanupResult(
                suggestions=[
                    CleanupDraftSuggestion(
                        kind="archive",
                        taskId="task-1",
                        rationale=f"{CREDENTIAL} 만 남기고 끝난 태스크다",
                        evidenceEventIds=["event-1"],
                    )
                ],
                tasksScanned=4,
            )
        }
    )


def _title_suggestion_result() -> JsonObject:
    """모델이 제목과 근거에 자격을 적은 제목 산출이다."""
    return TITLE_SUGGESTION_JOB.result_of(
        {
            "result": TitleSuggestionDraft(
                suggestions=[
                    TitleSuggestion(
                        title=f"{CREDENTIAL} 를 지운다",
                        rationale=f"대화가 {CREDENTIAL} 를 다뤘다",
                    )
                ]
            )
        }
    )


def test_시험이_쓰는_값이_계약의_자격_모양이다() -> None:
    assert is_suspect_text(CREDENTIAL)


@pytest.mark.parametrize(
    "result",
    [_recipe_scan_result, _task_cleanup_result, _title_suggestion_result],
    ids=["recipe-scan", "task-cleanup", "title-suggestion"],
)
def test_세_잡이_같은_자리에서_산출을_가린다(result: Any) -> None:
    dumped = json.dumps(result(), ensure_ascii=False)

    assert CREDENTIAL not in dumped
    assert marker() in dumped


def test_스캔은_요약과_사용_조건과_교정과_함정을_가린다() -> None:
    candidate = _recipe_scan_result()["recipes"][0]  # type: ignore[index]

    assert candidate["summary_md"] == f"- 열쇠 {marker()} 를 그대로 적었다"  # type: ignore[index]
    assert candidate["use_when"] == [f"{marker()} 이 실린 로그를 읽는다"]  # type: ignore[index]
    assert candidate["corrections"][0]["whatAgentDid"] == f"{marker()} 을 로그에 남겼다"  # type: ignore[index]
    assert candidate["pitfalls"][0]["pitfall"] == f"{marker()} 이 본문에 남는다"  # type: ignore[index]


def test_스캔은_가리면서_근거_장부와_무해한_본문을_보존한다() -> None:
    result = _recipe_scan_result()
    candidate = result["recipes"][0]  # type: ignore[index]

    assert result["provenance"] == {  # type: ignore[comparison-overlap]
        "eventIdsByTask": {},
        "turnIdsByTask": {},
        "ruleIds": [],
        "recipeRevs": {"recipe-1": 3},
    }
    assert candidate["title"] == "Add a safe migration"  # type: ignore[index]
    assert candidate["corrections"][0]["evidence"] == ["event-1"]  # type: ignore[index]


def test_정리는_판단_근거를_가린다() -> None:
    suggestion = _task_cleanup_result()["suggestions"][0]  # type: ignore[index]

    assert suggestion["rationale"] == f"{marker()} 만 남기고 끝난 태스크다"  # type: ignore[index]
    assert suggestion["taskId"] == "task-1"  # type: ignore[index]


def test_제목은_제목과_근거를_가린다() -> None:
    suggestion = _title_suggestion_result()["suggestions"][0]  # type: ignore[index]

    assert suggestion["title"] == f"{marker()} 를 지운다"  # type: ignore[index]
    assert suggestion["rationale"] == f"대화가 {marker()} 를 다뤘다"  # type: ignore[index]


async def test_원장에_적히는_후보도_같은_가림을_지난_값이다() -> None:
    store = SqliteLedgerSql()

    await RECIPE_SCAN_JOB.settle_outputs(
        JobOutputTargets(SingleSql(store), FakeTracerApi()),
        "job-1",
        _recipe_scan_result(),
        {"userId": "user-1"},
    )

    row = store.rows("recipes")[0]
    assert CREDENTIAL not in json.dumps(row, ensure_ascii=False, default=str)
    assert row["summary_md"] == f"- 열쇠 {marker()} 를 그대로 적었다"
    store.close()


async def test_원장에_적히는_제안도_같은_가림을_지난_값이다() -> None:
    store = SqliteLedgerSql()

    await TASK_CLEANUP_JOB.settle_outputs(
        JobOutputTargets(SingleSql(store), FakeTracerApi()),
        "job-2",
        _task_cleanup_result(),
        {"userId": "user-1"},
    )

    assert store.rows("task_cleanup_suggestions")[0]["rationale"] == f"{marker()} 만 남기고 끝난 태스크다"
    store.close()
