"""설정 조회와 쓰기와 삭제와 모델 목록을 계약이 정한 경로와 봉투로 받는다."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..runtime.dependencies import ExecutionSql, UserId
from ..runtime.ledger import SqlSource
from ..shared.wire import SuccessEnvelope, error_responses
from .catalog import knows_model, model_options
from .models import (
    MODEL_SETTING_KEY,
    UNPRICED_MODEL_TYPE,
    PutSettingPayload,
    is_setting_key,
    setting_view,
)
from .secret import SettingCipher
from .store import AppSettingStore

SETTINGS_PATH = "/api/agent/settings"
SETTING_MODELS_PATH = f"{SETTINGS_PATH}/models"
SETTING_PATH = f"{SETTINGS_PATH}/{{key}}"
INVALID_REQUEST = (400, "validation_error", "Invalid request")

router = APIRouter()


def get_setting_cipher(request: Request) -> SettingCipher:
    """앱 수명이 세운 설정 자격의 봉인기를 낸다."""
    cipher: SettingCipher = request.app.state.setting_cipher
    return cipher


Cipher = Annotated[SettingCipher, Depends(get_setting_cipher)]


@router.get(SETTINGS_PATH, response_model=SuccessEnvelope)
async def list_settings(source: ExecutionSql, cipher: Cipher, user_id: UserId) -> JSONResponse:
    """그 사용자가 저장해 둔 설정을 키마다 하나씩 낸다."""
    async with _store(source, cipher) as store:
        stored = await store.list_by_scope(user_id)
    items = [setting_view(one.key, one.value, one.updated_at) for one in stored]
    return JSONResponse(status_code=200, content={"ok": True, "data": {"items": items}})


@router.get(SETTING_MODELS_PATH, response_model=SuccessEnvelope)
async def list_setting_models() -> JSONResponse:
    """모델 설정에 고를 수 있는 값을 낸다."""
    items = [option.model_dump() for option in model_options()]
    return JSONResponse(status_code=200, content={"ok": True, "data": {"items": items}})


@router.put(SETTING_PATH, response_model=SuccessEnvelope, responses=error_responses(400))
async def put_setting(
    key: str, request: Request, source: ExecutionSql, cipher: Cipher, user_id: UserId
) -> JSONResponse:
    """설정 하나를 쓰며 모델 설정은 단가를 아는 값만 받는다."""
    if not is_setting_key(key):
        return _error_envelope(*INVALID_REQUEST)
    body = await _read_body(request)
    if body is None:
        return _error_envelope(*INVALID_REQUEST)
    try:
        payload = PutSettingPayload.model_validate(body)
    except ValidationError as invalid:
        return _error_envelope(*INVALID_REQUEST, details=_details(invalid))
    if key == MODEL_SETTING_KEY and not knows_model(payload.value):
        unpriced = [{"loc": ["value"], "type": UNPRICED_MODEL_TYPE, "model": payload.value}]
        return _error_envelope(*INVALID_REQUEST, details=unpriced)

    updated_at = datetime.now(UTC)
    async with _store(source, cipher) as store:
        await store.save(user_id, key, payload.value, updated_at)
    return JSONResponse(
        status_code=200, content={"ok": True, "data": setting_view(key, payload.value, updated_at)}
    )


@router.delete(SETTING_PATH, response_model=SuccessEnvelope, responses=error_responses(400))
async def delete_setting(key: str, source: ExecutionSql, cipher: Cipher, user_id: UserId) -> JSONResponse:
    """설정 하나를 지우고 지울 것이 있었는지 낸다."""
    if not is_setting_key(key):
        return _error_envelope(*INVALID_REQUEST)
    async with _store(source, cipher) as store:
        deleted = await store.remove(user_id, key)
    return JSONResponse(status_code=200, content={"ok": True, "data": {"key": key, "deleted": deleted}})


@asynccontextmanager
async def _store(source: SqlSource, cipher: SettingCipher) -> AsyncIterator[AppSettingStore]:
    async with source.connect() as sql:
        yield AppSettingStore(sql, cipher)


def _error_envelope(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status, content={"ok": False, "error": error})


async def _read_body(request: Request) -> dict[str, Any] | None:
    try:
        body = json.loads(await request.body() or b"null")
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


def _details(invalid: ValidationError) -> Any:
    return json.loads(invalid.json(include_url=False, include_context=False, include_input=False))
