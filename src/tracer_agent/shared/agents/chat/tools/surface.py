"""도구가 모델에게 열리는 표면을 계약 한 칸에서 읽어 게이트와 분류의 유일한 근거로 삼는다."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

# 계약 저장소는 배포 이미지의 서비스 루트에 함께 실린다.
CHAT_TOOLS_PATH = Path(__file__).resolve().parents[6] / "contract" / "agent" / "chat" / "tool.json"

READ_SURFACE = "read"
AGENT_READ_SURFACE = "agentRead"
MEMORY_SURFACE = "memory"
CONFIRM_SURFACE = "confirm"

# 세 되읽기 표면은 확인을 받지 않으므로 같은 창구가 함께 받는다.
READ_SURFACES = frozenset({READ_SURFACE, AGENT_READ_SURFACE, MEMORY_SURFACE})


@lru_cache(maxsize=1)
def chat_tool_declarations() -> Mapping[str, Mapping[str, Any]]:
    """도구 이름마다 열리는 표면과 인자 선언을 낸다."""
    declared = json.loads(CHAT_TOOLS_PATH.read_text(encoding="utf-8"))["tools"]
    return {str(name): dict(tool) for name, tool in declared.items()}


def tool_surface(name: str) -> str:
    """도구가 모델에게 열리는 표면이며 확인 게이트와 네 분류가 모두 이 한 값에서 나온다."""
    return str(chat_tool_declarations()[name]["surface"])


def tool_names_on(surface: str) -> tuple[str, ...]:
    """그 표면으로 열린 도구의 이름을 계약이 선언한 순서로 낸다."""
    return tuple(name for name in chat_tool_declarations() if tool_surface(name) == surface)


def recall_tool_name() -> str:
    """기억을 되읽는 도구 하나의 이름이며 계약이 그 표면을 열지 않았으면 거절한다."""
    names = tool_names_on(MEMORY_SURFACE)
    if len(names) != 1:
        raise ValueError("contract declares no single memory surface tool")
    return names[0]
