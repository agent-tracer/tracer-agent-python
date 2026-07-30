"""카탈로그와 자격과 실행 자리 정보를 실행 봉투 한 벌로 맞춘다."""

from __future__ import annotations

from typing import Any

from .catalog import ExecutionCatalog, wire_limits, wire_model_rates
from .grants import DraftGrant
from .tools import chat_tool_descriptions

DRAFT_PATH = "/api/agent/chat/executions/{execution_id}/drafts"


def chat_envelope(
    *,
    execution_id: str,
    model: str | None,
    api_key: str,
    catalog: ExecutionCatalog,
    read_api_base_url: str,
    agent_api_base_url: str,
    grant: DraftGrant,
) -> dict[str, Any]:
    """대화 한 시도가 쓸 카탈로그 값과 자격과 draft 창구를 봉투로 낸다."""
    return {
        "model": model or catalog.default_model,
        "apiKey": api_key,
        "modelRates": wire_model_rates(),
        "limits": wire_limits(catalog),
        "deadlineMs": catalog.deadline_ms,
        "readApiBaseUrl": read_api_base_url,
        # 도구 호출을 이 사용자와 실행으로 묶는 서명 자격이 없으면 자기신고 헤더로만 식별된다.
        "scopeToken": "",
        "toolDescriptions": chat_tool_descriptions(),
        "draft": {
            "url": f"{agent_api_base_url.rstrip('/')}{DRAFT_PATH.format(execution_id=execution_id)}",
            "token": grant.token,
            "tokenHash": grant.token_hash,
        },
    }


def job_envelope(*, api_key: str, catalog: ExecutionCatalog) -> dict[str, Any]:
    """잡 한 시도가 쓸 카탈로그 값과 자격을 봉투로 낸다."""
    return {
        "model": catalog.default_model,
        "fallbackModel": catalog.fallback_model,
        "apiKey": api_key,
        "modelRates": wire_model_rates(),
        "limits": wire_limits(catalog),
        "deadlineMs": catalog.deadline_ms,
    }
