"""레시피와 정리 제안의 응답 칸과 창구와 거절이 계약이 적은 것과 같은지 검증한다."""

from __future__ import annotations

from typing import get_args

from contract.conformance.runner.contract import read_agent_api_routes, route_key

from tests.support.contract import conformance_case, wire_contract
from tests.support.recipes import application_row, recipe_row, suggestion_row
from tracer_agent.api.app import create_app
from tracer_agent.api.surface import served_routes
from tracer_agent.shared.agents.cleanup.models import (
    CLEANUP_NOT_PENDING,
    CLEANUP_STALE,
    CLEANUP_SUGGESTION_KINDS,
    CLEANUP_SUGGESTION_NOT_FOUND,
    CLEANUP_SUGGESTION_STATUSES,
    cleanup_suggestion_view,
)
from tracer_agent.shared.agents.recipe.document import build_recipe_document
from tracer_agent.shared.agents.recipe.index import search_outbox_batch_size
from tracer_agent.shared.agents.recipe.models import (
    RECIPE_NOT_ACTIVE,
    RECIPE_NOT_CANDIDATE,
    RECIPE_NOT_DELETABLE,
    RECIPE_NOT_FOUND,
    RECIPE_OUTCOMES,
    RECIPE_STATUSES,
    recipe_application_view,
    recipe_stats,
    recipe_view,
)
from tracer_agent.shared.agents.recipe.service import RECIPE_SEARCH_LIMIT
from tracer_agent.shared.agents.recipe.wire_models import RecipeOutcomeValue, RecipeStatusValue

_CASE = conformance_case("recipe.ledger")
_SHAPES = _CASE["shapes"]
_SEARCH_INDEX = wire_contract("search.index.json")
_RECIPES_INDEX = _SEARCH_INDEX["indices"]["recipes"]


def _window(path: str) -> dict[str, object]:
    return next(one for one in _CASE["windows"] if one["path"] == path)


def _rejection(code: str) -> dict[str, object]:
    return next(one for one in _CASE["rejections"] if one["code"] == code)


class Test응답의_칸이_계약이_적은_모양과_같다:
    def test_레시피_한_건의_칸이_recipe_모양과_같다(self) -> None:
        assert sorted(recipe_view(recipe_row())) == sorted(_SHAPES["recipe"]["fields"])

    def test_목록이_내는_한_건의_칸이_recipeWithStats_모양과_같다(self) -> None:
        item = {**recipe_view(recipe_row()), "stats": recipe_stats([]).view()}

        assert sorted(item) == sorted(_SHAPES["recipeWithStats"]["fields"])

    def test_통계의_칸이_recipeStats_모양과_같다(self) -> None:
        assert sorted(recipe_stats([]).view()) == sorted(_SHAPES["recipeStats"]["fields"])

    def test_적용_이력_한_건의_칸이_recipeApplication_모양과_같다(self) -> None:
        assert sorted(recipe_application_view(application_row())) == sorted(
            _SHAPES["recipeApplication"]["fields"]
        )

    def test_정리_제안_한_건의_칸이_cleanupSuggestion_모양과_같다(self) -> None:
        assert sorted(cleanup_suggestion_view(suggestion_row())) == sorted(
            _SHAPES["cleanupSuggestion"]["fields"]
        )


class Test창구와_어휘가_계약이_적은_것과_같다:
    def test_케이스가_적은_창구를_이_프로세스가_모두_연다(self) -> None:
        declared = sorted(
            route_key({"method": str(one["method"]), "path": str(one["path"])}) for one in _CASE["windows"]
        )
        served = sorted(
            route_key(route)
            for route in served_routes(create_app())
            if "/recipes" in route["path"] or "/cleanup/" in route["path"]
        )

        assert served == declared

    def test_계약이_선언한_창구와_케이스의_창구가_같다(self) -> None:
        declared = sorted(
            route_key(route)
            for route in read_agent_api_routes()
            if "/recipes" in route["path"] or "/cleanup/" in route["path"]
        )
        cased = sorted(
            route_key({"method": str(one["method"]), "path": str(one["path"])}) for one in _CASE["windows"]
        )

        assert cased == declared

    def test_상태_전이가_내는_거절의_낱말이_계약과_같다(self) -> None:
        for status, code, message in (RECIPE_NOT_CANDIDATE, RECIPE_NOT_ACTIVE, RECIPE_NOT_DELETABLE):
            declared = _rejection(code)

            assert (status, message) == (declared["status"], declared["message"])

    def test_낡음_거절의_낱말이_계약과_같다(self) -> None:
        status, code, message = CLEANUP_STALE
        declared = _rejection(code)

        assert (status, message) == (declared["status"], declared["message"])

    def test_없음_거절의_낱말이_계약과_같다(self) -> None:
        for status, code, message in (RECIPE_NOT_FOUND, CLEANUP_SUGGESTION_NOT_FOUND):
            found = [one for one in _CASE["rejections"] if one["code"] == code and one["message"] == message]

            assert [one["status"] for one in found] == [status]

    def test_계약에_없는_거절_코드를_한_자리만_더_쓴다(self) -> None:
        # 대기 중이 아닌 제안의 해소는 정본 축이 세운 거절이며 계약의 목록에 아직 없다.
        declared = {one["code"] for one in _CASE["rejections"]}

        assert CLEANUP_NOT_PENDING[1] not in declared

    def test_목록이_거르는_상태의_어휘가_계약과_같다(self) -> None:
        window = _window("/api/agent/recipes")
        constraints = window["query"]["constraints"]

        assert list(RECIPE_STATUSES) == constraints["status"]["enum"]
        assert list(get_args(RecipeStatusValue)) == list(RECIPE_STATUSES)

    def test_정리_목록이_거르는_상태의_어휘가_계약과_같다(self) -> None:
        window = _window("/api/agent/cleanup/suggestions")

        assert list(CLEANUP_SUGGESTION_STATUSES) == window["query"]["constraints"]["status"]["enum"]

    def test_자기보고가_받는_결과의_어휘가_계약과_같다(self) -> None:
        window = _window("/api/agent/recipes/{id}/outcome")

        assert list(RECIPE_OUTCOMES) == window["body"]["constraints"]["outcome"]["enum"]
        assert list(get_args(RecipeOutcomeValue)) == list(RECIPE_OUTCOMES)

    def test_제안의_종류가_계약이_적은_것과_같다(self) -> None:
        declared = _CASE["ledgerWrite"]["drafts"]["cleanupSuggestion"]["constraints"]["kind"]["enum"]

        assert list(CLEANUP_SUGGESTION_KINDS) == declared


class Test색인_선언과_코드가_같은_값을_쓴다:
    def test_색인_문서의_칸이_계약이_선언한_칸과_같다(self) -> None:
        assert sorted(build_recipe_document(recipe_row())) == sorted(_RECIPES_INDEX["document"]["fields"])

    def test_검색_상한이_계약이_선언한_값과_같다(self) -> None:
        assert _RECIPES_INDEX["query"]["limit"] == RECIPE_SEARCH_LIMIT

    def test_배출_한_번이_읽는_행의_수가_계약과_같다(self) -> None:
        drain = next(one for one in _SEARCH_INDEX["pipeline"]["stages"] if one["name"] == "drain")

        assert drain["batchSize"] == search_outbox_batch_size()
