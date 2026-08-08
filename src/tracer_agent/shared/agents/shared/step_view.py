"""궤적 한 줄의 원장 행을 계약이 정한 와이어 표현으로 바꾼다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .json_view import JsonObject

# 값이 없으면 궤적 한 줄에 싣지 않는 자리이며 열 이름과 wire 이름을 함께 든다.
OPTIONAL_STEP_FIELDS: tuple[tuple[str, str], ...] = (
    ("tool_name", "toolName"),
    ("tool_call_id", "toolCallId"),
    ("input_tokens", "inputTokens"),
    ("output_tokens", "outputTokens"),
    ("cache_read_tokens", "cacheReadTokens"),
    ("cache_creation_tokens", "cacheCreationTokens"),
    ("stop_reason", "stopReason"),
    ("node_name", "nodeName"),
    ("event_kind", "eventKind"),
    ("duration_ms", "durationMs"),
)


def step_row_view(row: Mapping[str, Any]) -> JsonObject:
    """궤적 한 줄을 값이 있는 자리만 실은 표현으로 바꾼다."""
    step: JsonObject = {
        "seq": row["seq"],
        "attempt": row["attempt"],
        "role": row["role"],
        "content": row["content"],
        "truncated": bool(row["truncated"]),
        "toolCalls": row["tool_calls"] or [],
    }
    for column, name in OPTIONAL_STEP_FIELDS:
        if row[column] is not None:
            step[name] = row[column]
    return step
