"""모델에게 보일 chat 도구 설명을 두 구현체가 함께 읽는 계약에서 가져온다."""

from __future__ import annotations

import json
from functools import lru_cache

from ..shared.contract_root import CONTRACT_ROOT

CHAT_TOOLS_PATH = CONTRACT_ROOT / "agent" / "chat" / "tool.json"


@lru_cache(maxsize=1)
def chat_tool_descriptions() -> dict[str, str]:
    """도구 이름마다 모델이 읽을 한 문장을 낸다."""
    declared = json.loads(CHAT_TOOLS_PATH.read_text(encoding="utf-8"))["tools"]
    return {str(name): str(tool["description"]) for name, tool in declared.items()}
