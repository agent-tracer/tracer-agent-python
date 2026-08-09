"""레시피 원장의 조회와 상태 변경을 계약이 정한 경로와 봉투로 받는다."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from ..runtime.dependencies import ExecutionSql, UserId
from ..runtime.ledger import SqlSource
from ..shared.ids import generate_ulid
from ..shared.json_view import JsonObject
from ..shared.wire import (
    INVALID_REQUEST,
    SuccessEnvelope,
    error_envelope,
    error_responses,
    ok,
    read_body,
    validation_details,
)
from .models import RecipeRejected
from .search import RecipeSearchPort
from .service import (
    RecipeLedger,
    accept_recipe,
    delete_recipe,
    dismiss_recipe,
    edit_recipe,
    get_recipe,
    list_recipes,
    report_recipe_outcome,
    retire_recipe,
    search_recipes,
)
from .tasks import RecipeTaskReader
from .wire_models import ListRecipesQuery, RecipeEditBody, RecipeOutcomeBody, SearchRecipesQuery

RECIPES_PATH = "/api/agent/recipes"
RECIPE_SEARCH_PATH = f"{RECIPES_PATH}/search"
RECIPE_PATH = f"{RECIPES_PATH}/{{recipe_id}}"
RECIPE_ACCEPT_PATH = f"{RECIPE_PATH}/accept"
RECIPE_DISMISS_PATH = f"{RECIPE_PATH}/dismiss"
RECIPE_RETIRE_PATH = f"{RECIPE_PATH}/retire"
RECIPE_OUTCOME_PATH = f"{RECIPE_PATH}/outcome"

router = APIRouter()


def get_recipe_search(request: Request) -> RecipeSearchPort:
    """앱 수명이 세운 레시피 색인 질의 창구를 낸다."""
    search: RecipeSearchPort = request.app.state.services.recipe_search
    return search


def get_recipe_tasks(request: Request) -> RecipeTaskReader:
    """앱 수명이 세운 태스크 제목 조회 창구를 낸다."""
    tasks: RecipeTaskReader = request.app.state.services.recipe_tasks
    return tasks


RecipeSearch = Annotated[RecipeSearchPort, Depends(get_recipe_search)]
RecipeTasks = Annotated[RecipeTaskReader, Depends(get_recipe_tasks)]


def _rejection(rejected: RecipeRejected) -> JSONResponse:
    return error_envelope(rejected.status, rejected.code, rejected.message)


def _query[Query: BaseModel](request: Request, model: type[Query]) -> Query | JSONResponse:
    """질의를 모델로 해석하고 어긋나면 계약이 정한 400 을 낸다."""
    try:
        return model.model_validate(dict(request.query_params))
    except ValidationError as invalid:
        return error_envelope(*INVALID_REQUEST, details=validation_details(invalid))


async def _payload[Body: BaseModel](request: Request, model: type[Body]) -> Body | JSONResponse:
    """본문을 모델로 해석하고 어긋나면 계약이 정한 400 을 낸다."""
    body = await read_body(request)
    if body is None:
        return error_envelope(*INVALID_REQUEST)
    try:
        return model.model_validate(body)
    except ValidationError as invalid:
        return error_envelope(*INVALID_REQUEST, details=validation_details(invalid))


async def _commit(source: SqlSource, work: Callable[[RecipeLedger], Awaitable[JsonObject]]) -> JSONResponse:
    """레시피 쓰기와 색인 적재를 한 커밋으로 묶고 거절을 계약의 봉투로 낸다."""
    try:
        async with source.connect() as sql, sql.transaction():
            data = await work(RecipeLedger.open(sql))
    except RecipeRejected as rejected:
        return _rejection(rejected)
    return ok(data)


@router.get(RECIPES_PATH, response_model=SuccessEnvelope, responses=error_responses(400))
async def list_recipes_window(
    request: Request, source: ExecutionSql, tasks: RecipeTasks, user_id: UserId
) -> JSONResponse:
    """이 사용자의 레시피를 상태로 걸러 통계와 태스크 제목 표까지 낸다."""
    query = _query(request, ListRecipesQuery)
    if isinstance(query, JSONResponse):
        return query
    async with source.connect() as sql:
        data = await list_recipes(RecipeLedger.open(sql), tasks, user_id, query.status)
    return ok(data)


@router.get(RECIPE_SEARCH_PATH, response_model=SuccessEnvelope, responses=error_responses(400))
async def search_recipes_window(request: Request, search: RecipeSearch, user_id: UserId) -> JSONResponse:
    """레시피를 본문 유사도로 검색해 고르는 데 필요한 칸만 낸다."""
    query = _query(request, SearchRecipesQuery)
    if isinstance(query, JSONResponse):
        return query
    return ok(await search_recipes(search, user_id, query.q, query.limit))


@router.get(RECIPE_PATH, response_model=SuccessEnvelope, responses=error_responses(404))
async def get_recipe_window(recipe_id: str, source: ExecutionSql, user_id: UserId) -> JSONResponse:
    """레시피 하나를 전문과 적용 이력까지 낸다."""
    try:
        async with source.connect() as sql:
            data = await get_recipe(RecipeLedger.open(sql), user_id, recipe_id)
    except RecipeRejected as rejected:
        return _rejection(rejected)
    return ok(data)


@router.post(RECIPE_ACCEPT_PATH, response_model=SuccessEnvelope, responses=error_responses(404, 409))
async def accept_recipe_window(recipe_id: str, source: ExecutionSql, user_id: UserId) -> JSONResponse:
    """후보 레시피를 채택하고 부모가 있으면 그 부모를 대체됨으로 함께 옮긴다."""
    now = datetime.now(UTC)
    outbox_row_ids = [generate_ulid(now), generate_ulid(now)]
    return await _commit(
        source, lambda ledger: accept_recipe(ledger, outbox_row_ids, user_id, recipe_id, now)
    )


@router.post(RECIPE_DISMISS_PATH, response_model=SuccessEnvelope, responses=error_responses(404, 409))
async def dismiss_recipe_window(recipe_id: str, source: ExecutionSql, user_id: UserId) -> JSONResponse:
    """후보 레시피를 보류한다."""
    now = datetime.now(UTC)
    row_id = generate_ulid(now)
    return await _commit(source, lambda ledger: dismiss_recipe(ledger, row_id, user_id, recipe_id, now))


@router.post(RECIPE_RETIRE_PATH, response_model=SuccessEnvelope, responses=error_responses(404, 409))
async def retire_recipe_window(recipe_id: str, source: ExecutionSql, user_id: UserId) -> JSONResponse:
    """쓰이던 레시피를 폐기한다."""
    now = datetime.now(UTC)
    row_id = generate_ulid(now)
    return await _commit(source, lambda ledger: retire_recipe(ledger, row_id, user_id, recipe_id, now))


@router.post(RECIPE_OUTCOME_PATH, response_model=SuccessEnvelope, responses=error_responses(400, 404))
async def report_recipe_outcome_window(
    recipe_id: str, request: Request, source: ExecutionSql, user_id: UserId
) -> JSONResponse:
    """레시피를 쓴 결과를 자기보고한다."""
    body = await _payload(request, RecipeOutcomeBody)
    if isinstance(body, JSONResponse):
        return body
    now = datetime.now(UTC)
    row_id = generate_ulid(now)
    values: dict[str, str | None] = {
        "taskId": body.taskId,
        "outcome": body.outcome,
        "note": body.note,
    }
    return await _commit(
        source, lambda ledger: report_recipe_outcome(ledger, row_id, user_id, recipe_id, values, now)
    )


@router.patch(RECIPE_PATH, response_model=SuccessEnvelope, responses=error_responses(400, 404, 409))
async def edit_recipe_window(
    recipe_id: str, request: Request, source: ExecutionSql, user_id: UserId
) -> JSONResponse:
    """채택된 레시피의 본문을 사용자가 고친다."""
    body = await _payload(request, RecipeEditBody)
    if isinstance(body, JSONResponse):
        return body
    now = datetime.now(UTC)
    row_id = generate_ulid(now)
    revision = body.revision()
    return await _commit(
        source, lambda ledger: edit_recipe(ledger, row_id, user_id, recipe_id, revision, now)
    )


@router.delete(RECIPE_PATH, response_model=SuccessEnvelope, responses=error_responses(400, 404))
async def delete_recipe_window(recipe_id: str, source: ExecutionSql, user_id: UserId) -> JSONResponse:
    """보류하거나 폐기한 레시피를 지운다."""
    now = datetime.now(UTC)
    row_id = generate_ulid(now)
    return await _commit(source, lambda ledger: delete_recipe(ledger, row_id, user_id, recipe_id, now))


__all__ = [
    "RECIPES_PATH",
    "RECIPE_ACCEPT_PATH",
    "RECIPE_DISMISS_PATH",
    "RECIPE_OUTCOME_PATH",
    "RECIPE_PATH",
    "RECIPE_RETIRE_PATH",
    "RECIPE_SEARCH_PATH",
    "router",
]
