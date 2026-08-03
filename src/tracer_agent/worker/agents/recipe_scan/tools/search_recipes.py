"""적재된 레시피를 검색하고 개정된 레시피를 수정 근거로 올리는 도구를 소유한다."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from tracer_agent.shared.agents.shared.models import TrimmedStr

from ...runtime.tooling import AgentTool
from ...runtime.tracer_client import TRANSIENT_TRACER_ERRORS
from .context import RecipeToolContext
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


class SearchRecipesTool(AgentTool[SearchRecipesArgs, RecipeToolContext]):
    """수정 대상이 될 수 있는 레시피를 추적 창구에서 찾고 개정 근거를 올린다."""

    name = SEARCH_RECIPES
    description = SEARCH_RECIPES_DESCRIPTION
    args_model = SearchRecipesArgs
    # 창구에 닿지 못한 것만 일시적이며 창구가 거절한 요청은 재시도하지 않는다.
    transient_errors = TRANSIENT_TRACER_ERRORS

    async def execute(self, args: SearchRecipesArgs, context: RecipeToolContext) -> str:
        recipes = await context.search.search_recipes(args.q, args.limit)
        return json.dumps(recipes, ensure_ascii=False)

    def record(self, _args: SearchRecipesArgs, content: str, context: RecipeToolContext, /) -> None:
        add_recipe_revs(context.catalog, loaded(content))
