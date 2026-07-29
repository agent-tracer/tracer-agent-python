"""설정 조회와 쓰기와 삭제와 모델 목록을 계약이 정한 경로와 봉투로 받는다."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..runtime.ledger import SqlSource
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
MONITOR_USER_HEADER = "x-monitor-user"
DEFAULT_SCOPE = "local"
INVALID_REQUEST = (400, "validation_error", "Invalid request")


async def list_settings(request: Request) -> JSONResponse:
    """그 사용자가 저장해 둔 설정을 키마다 하나씩 낸다."""
    async with _store(request) as store:
        stored = await store.list_by_scope(_resolve_scope(request))
    items = [setting_view(one.key, one.value, one.updated_at) for one in stored]
    return JSONResponse(status_code=200, content={"ok": True, "data": {"items": items}})


async def list_setting_models() -> JSONResponse:
    """모델 설정에 고를 수 있는 값을 낸다."""
    items = [option.model_dump() for option in model_options()]
    return JSONResponse(status_code=200, content={"ok": True, "data": {"items": items}})


async def put_setting(key: str, request: Request) -> JSONResponse:
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
    async with _store(request) as store:
        await store.save(_resolve_scope(request), key, payload.value, updated_at)
    return JSONResponse(
        status_code=200, content={"ok": True, "data": setting_view(key, payload.value, updated_at)}
    )


async def delete_setting(key: str, request: Request) -> JSONResponse:
    """설정 하나를 지우고 지울 것이 있었는지 낸다."""
    if not is_setting_key(key):
        return _error_envelope(*INVALID_REQUEST)
    async with _store(request) as store:
        deleted = await store.remove(_resolve_scope(request), key)
    return JSONResponse(status_code=200, content={"ok": True, "data": {"key": key, "deleted": deleted}})


@asynccontextmanager
async def _store(request: Request) -> AsyncIterator[AppSettingStore]:
    source: SqlSource = request.app.state.execution_sql
    cipher: SettingCipher = request.app.state.setting_cipher
    async with source.connect() as sql:
        yield AppSettingStore(sql, cipher)


def _resolve_scope(request: Request) -> str:
    trimmed = (request.headers.get(MONITOR_USER_HEADER) or "").strip()
    return trimmed if trimmed else DEFAULT_SCOPE


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
