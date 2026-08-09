"""카탈로그와 자격과 실행 자리 정보를 실행 봉투 한 벌로 맞춘다."""

from __future__ import annotations

from typing import Any

from ..shared import scope_token
from ..shared.model_tiering import CHAT_KIND, allowed_models
from .catalog import ExecutionCatalog, wire_model_rates
from .tools import chat_tool_descriptions


def chat_envelope(
    *,
    execution_id: str,
    model: str | None,
    api_key: str,
    catalog: ExecutionCatalog,
    read_api_base_url: str,
    user_id: str,
    now_ms: int,
) -> dict[str, Any]:
    """대화 한 시도가 쓸 카탈로그 값과 자격을 봉투로 낸다."""
    return {
        "model": _model(CHAT_KIND, catalog, model),
        "apiKey": api_key,
        "modelRates": wire_model_rates(),
        "limits": catalog.wire_limits(),
        "deadlineMs": catalog.deadline_ms,
        "readApiBaseUrl": read_api_base_url,
        # 서명 비밀을 배포가 주지 않으면 자격을 만들 수 없어 자기신고 헤더로만 식별된다.
        "scopeToken": scope_token.issue(
            user_id=user_id,
            execution_id=execution_id,
            ttl_ms=catalog.deadline_ms + scope_token.margin_ms(),
            now_ms=now_ms,
        )
        or "",
        "toolDescriptions": chat_tool_descriptions(),
    }


def job_envelope(
    *, kind: str, api_key: str, catalog: ExecutionCatalog, chosen_model: str | None = None
) -> dict[str, Any]:
    """잡 한 시도가 쓸 카탈로그 값과 자격을 봉투로 내며 고른 모델만 기본 모델을 덮는다."""
    return {
        "model": _model(kind, catalog, chosen_model),
        "fallbackModel": catalog.fallback_model,
        "apiKey": api_key,
        "modelRates": wire_model_rates(),
        "limits": catalog.wire_limits(),
        "deadlineMs": catalog.deadline_ms,
    }


# 예산은 허용 목록을 전제로 잡히므로 목록 밖 모델을 실으면 상한에 닿아 끝난다.
def _model(kind: str, catalog: ExecutionCatalog, chosen: str | None) -> str:
    """상류가 고른 모델이며 그 종류가 허용하지 않은 값이면 기본 모델을 쓴다."""
    if chosen is None or chosen not in allowed_models(kind):
        return catalog.default_model
    return chosen
