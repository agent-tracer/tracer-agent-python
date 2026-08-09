"""종결한 스캔의 후보가 레시피 창구로 실려 나가는지 검증한다(네트워크 없음)."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from tests.support.contract import conformance_case
from tracer_agent.shared.agents.recipe_scan.models import (
    Language,
    ProvenanceWire,
    RecipeCandidate,
    RecipeScanResult,
)
from tracer_agent.worker.agents.recipe_scan.outputs import (
    MAX_RECIPES,
    RECIPES_PATH,
    deliver_recipes,
)
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.job_agent import dumped
from tracer_agent.worker.agents.shared.empty_result import DEGRADED

_DRAFT = conformance_case("tracer.outputs")["drafts"]["recipe"]


def _scan_result(**overrides: Any) -> dict[str, Any]:
    """스캔이 종결 창구에 내는 산출물이며 후보는 실제 모델을 거쳐 만든다."""
    values: dict[str, Any] = {
        "title": "Add a safe migration",
        "intent": "스키마 변경을 안전하게 넣는다",
        "description": "저장된 모양이 바뀔 때 쓴다.",
        "use_when": ["저장된 엔터티의 모양이 바뀐다"],
        "summary_md": "- 변경을 정의한다",
        "request": "사용자가 마이그레이션 추가를 요청했다.",
        "inputs": ["요청이 말한 엔터티 변경"],
        "outputs": ["정방향 마이그레이션과 되돌리기"],
        "corrections": [
            {
                "whatAgentDid": "평범한 편집으로 다뤘다",
                "howCorrected": "마이그레이션을 등록했다",
                "evidence": ["event-1"],
            }
        ],
        "pitfalls": [
            {
                "pitfall": "재시도가 순번을 다시 쓴다",
                "whyNonObvious": "한 번만 돌면 겹치지 않는다",
                "evidence": ["event-1"],
            }
        ],
        "recovery": [
            {
                "symptom": "되돌리기가 이전 스키마를 남겼다",
                "action": "down 경로를 고치고 다시 돌렸다",
                "evidence": ["event-2"],
                "stepOrder": 1,
            }
        ],
        "governing_rules": ["rule-1"],
        "revises_recipe_id": "recipe-1",
        "steps": [{"order": 1, "action": "마이그레이션을 등록한다", "evidence": ["event-1"]}],
        "touched_files": [{"path": "docs/db.md", "role": "read", "why": "명명 규칙", "loadWhen": "작성 전"}],
        "contributing_slices": [{"taskId": "task-1", "turnIds": ["turn-1"], "eventIds": ["event-1"]}],
        "rationale": "반복 가능한 절차다.",
    }
    values.update(overrides)
    result = RecipeScanResult(
        recipes=[RecipeCandidate.model_validate(values)],
        provenance=ProvenanceWire(recipeRevs={"recipe-1": 3}),
    )
    return dumped(result, exclude_none=True)


async def _delivered_draft(language: Language = "ko", **overrides: Any) -> dict[str, Any]:
    """후보 하나를 실제로 배달해 창구가 받은 draft를 낸다."""
    tracer = FakeTracerApi()

    await deliver_recipes(tracer, "job-draft", _scan_result(**overrides), language)  # type: ignore[arg-type]

    draft: dict[str, Any] = tracer.posts[0]["body"]["recipes"][0]
    return draft


async def test_레시피_후보를_한_벌로_보내고_출처_잡을_함께_싣는다() -> None:
    tracer = FakeTracerApi()

    await deliver_recipes(tracer, "job-1", {"recipes": [{"title": "하나"}, {"title": "둘"}]})  # type: ignore[arg-type]

    assert [post["path"] for post in tracer.posts] == [RECIPES_PATH]
    body = tracer.posts[0]["body"]
    assert len(body["recipes"]) == 2
    assert body["author"] == "agent"
    # 같은 잡의 재시도가 후보를 두 벌 만들지 않도록 창구가 이 값으로 멱등을 판정한다.
    assert body["sourceJobId"] == "job-1"


async def test_창구가_받는_상한까지만_보낸다() -> None:
    tracer = FakeTracerApi()

    await deliver_recipes(  # type: ignore[arg-type]
        tracer, "job-3", {"recipes": [{"title": str(n)} for n in range(MAX_RECIPES + 5)]}
    )

    assert len(tracer.posts[0]["body"]["recipes"]) == MAX_RECIPES


async def test_후보가_없으면_창구를_부르지_않는다() -> None:
    tracer = FakeTracerApi()

    await deliver_recipes(tracer, "job-4", {"recipes": []})  # type: ignore[arg-type]

    assert tracer.posts == []


async def test_창구가_실패해도_종결을_되돌리지_않되_배달_실패를_남긴다(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Unavailable:
        async def post(self, _path: str, _body: dict[str, Any]) -> Any:
            raise ConnectionError("tracer api down")

    with caplog.at_level(logging.ERROR):
        # 원장은 이미 닫혔으므로 산출물이 서지 않은 채로 잡은 완료로 남는다.
        delivered = await deliver_recipes(Unavailable(), "job-7", {"recipes": [{"title": "하나"}]})  # type: ignore[arg-type]

    # 삼킨 실패는 원장에서 성공한 실행과 갈리지 않으므로 사유와 함께 남긴다.
    assert delivered is False
    assert any(
        f"emptyResultReason={DEGRADED}" in record.getMessage() and "job-7" in record.getMessage()
        for record in caplog.records
    )


async def test_창구가_받으면_배달했다고_낸다() -> None:
    tracer = FakeTracerApi()

    assert await deliver_recipes(tracer, "job-8", {"recipes": [{"title": "하나"}]}) is True  # type: ignore[arg-type]
    assert await deliver_recipes(tracer, "job-8", {"recipes": []}) is True  # type: ignore[arg-type]


async def test_모델이_적은_신규_칸이_창구의_이름으로_끝까지_간다() -> None:
    draft = await _delivered_draft()

    # 후보에만 서고 draft 매핑에서 빠지면 모델이 적은 값이 전달 직전에 사라진다.
    assert draft["useWhen"] == ["저장된 엔터티의 모양이 바뀐다"]
    assert draft["inputs"] == ["요청이 말한 엔터티 변경"]
    assert draft["outputs"] == ["정방향 마이그레이션과 되돌리기"]
    assert draft["recovery"] == [
        {
            "symptom": "되돌리기가 이전 스키마를 남겼다",
            "action": "down 경로를 고치고 다시 돌렸다",
            "evidence": ["event-2"],
            "stepOrder": 1,
        }
    ]
    assert draft["steps"][0]["evidence"] == ["event-1"]
    assert draft["touchedFiles"][0]["why"] == "명명 규칙"
    assert draft["touchedFiles"][0]["loadWhen"] == "작성 전"


async def test_후보의_snake_case_칸을_창구의_camelCase_이름으로_옮긴다() -> None:
    draft = await _delivered_draft()

    assert draft["summaryMd"] == "- 변경을 정의한다"
    assert draft["governingRules"] == ["rule-1"]
    assert draft["touchedFiles"][0]["path"] == "docs/db.md"
    assert draft["contributingSlices"][0]["taskId"] == "task-1"
    # 창구가 갖지 않는 이름으로 실으면 그 값은 저장되지 않은 채 조용히 버려진다.
    assert not [name for name in draft if "_" in name]


async def test_고쳐_쓰는_레시피는_부모와_본_판으로_옮긴다() -> None:
    draft = await _delivered_draft()

    assert draft["parentRecipeId"] == "recipe-1"
    # 창구는 지금 판과 다르면 부모를 비우므로 이 실행이 본 판을 함께 적는다.
    assert draft["parentRecipeSeenRev"] == 3
    assert "revises_recipe_id" not in draft


async def test_고쳐_쓸_레시피가_없으면_부모_칸을_싣지_않는다() -> None:
    draft = await _delivered_draft(revises_recipe_id=None)

    assert "parentRecipeId" not in draft
    assert "parentRecipeSeenRev" not in draft


async def test_본_판을_모르는_부모는_부모_칸_없이_보낸다() -> None:
    # 부모만 싣고 본 판을 빼면 창구가 어긋남을 판정할 수 없으므로 둘을 함께 뺀다.
    draft = await _delivered_draft(revises_recipe_id="recipe-9")

    assert "parentRecipeId" not in draft
    assert "parentRecipeSeenRev" not in draft


async def test_draft가_계약이_적은_칸만_쓰고_요구한_칸을_모두_싣는다() -> None:
    draft = await _delivered_draft()
    declared = set(_DRAFT["required"]) | set(_DRAFT["optional"])

    assert set(draft) <= declared
    assert set(_DRAFT["required"]) <= set(draft)
    assert set(draft["contributingSlices"][0]) == set(_DRAFT["slice"])


@pytest.mark.parametrize("language", ["ko", "auto"])
async def test_초안이_이_실행의_답변_언어를_함께_싣는다(language: Language) -> None:
    # 정본 축이 auto 도 그대로 실으므로 값이 언어가 아닌 경우까지 같은 글자를 낸다.
    draft = await _delivered_draft(language)

    assert draft["language"] == language
