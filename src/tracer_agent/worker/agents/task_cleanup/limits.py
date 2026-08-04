"""task-cleanup이 요청에 싣는 목록의 상한을 계약에서 읽는다."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_TOOL_PATH = Path(__file__).resolve().parents[5] / "contract" / "agent" / "task-cleanup" / "tool.json"


@lru_cache(maxsize=1)
def load_triage_candidate_list_limit() -> int:
    """조율자 요청이 싣는 후보 수의 상한을 계약에서 읽는다."""
    declared: Any = json.loads(_TOOL_PATH.read_text(encoding="utf-8"))
    return int(declared["limits"]["triageCandidateListLimit"])
