"""적재된 레시피를 검색하고 개정된 레시피를 수정 근거로 올리는 도구를 소유한다."""

from __future__ import annotations

import json

from opensearchpy.exceptions import (
    ConnectionError as OpenSearchConnectionError,
)
from opensearchpy.exceptions import (
    ConnectionTimeout as OpenSearchConnectionTimeout,
)
from pydantic import BaseModel, ConfigDict, Field

from tracer_agent.shared.agents.recipe_scan.models import ProvenanceCatalog
from tracer_agent.shared.agents.shared.models import TrimmedStr

from ...runtime.tooling import AgentTool
from ..search import RecipeSearchReader
from .provenance import add_recipe_revs, loaded

SEARCH_RECIPES = "search_recipes"
DEFAULT_RECIPE_LIMIT = 5
MAX_RECIPE_LIMIT = 20


class SearchRecipesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: TrimmedStr = Field(min_length=1, description="Search query")
    limit: int = Field(default=DEFAULT_RECIPE_LIMIT, ge=1, le=MAX_RECIPE_LIMIT, description="Max recipes")


SEARCH_RECIPES_DESCRIPTION = (
    "Search existing recipes for possible duplicate or outdated targets. Use this before setting "
    "revises_recipe_id."
)


class SearchRecipesTool(AgentTool[SearchRecipesArgs]):
    """수정 대상이 될 수 있는 레시피를 색인에서 찾고 개정 근거를 올린다."""

    name = SEARCH_RECIPES
    description = SEARCH_RECIPES_DESCRIPTION
    args_model = SearchRecipesArgs
    # 색인(opensearch)만 읽으므로 연결 계열 오류만 일시적이며 검증·도메인 오류는 재시도하지 않는다.
    transient_errors = (
        OpenSearchConnectionError,
        OpenSearchConnectionTimeout,
        ConnectionError,
        TimeoutError,
    )

    def __init__(self, search: RecipeSearchReader, catalog: ProvenanceCatalog) -> None:
        self._search = search
        self._catalog = catalog

    async def execute(self, args: SearchRecipesArgs) -> str:
        recipes = await self._search.search_recipes(args.q, args.limit)
        return json.dumps(recipes, ensure_ascii=False)

    def record(self, _args: SearchRecipesArgs, content: str, /) -> None:
        add_recipe_revs(self._catalog, loaded(content))
