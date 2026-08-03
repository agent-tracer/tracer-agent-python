"""모델이 남은 몫을 보고 답의 밀도를 정하도록 건네는 문구를 계약에서 읽는다."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_CONTRACT_ROOT = Path(__file__).resolve().parents[6] / "contract"
_EXECUTION_BUDGET_PATH = _CONTRACT_ROOT / "agent" / "shared" / "execution.budget.json"


@lru_cache(maxsize=1)
def _pacing() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(_EXECUTION_BUDGET_PATH.read_text(encoding="utf-8"))
    pacing: dict[str, Any] = document["pacing"]
    return pacing


def progress_notice(used: int, total: int) -> str:
    """그때까지 쓴 턴과 총량을 계약이 정한 문구에 채운다."""
    template = str(_pacing()["progressNotice"]["template"])
    return template.replace("{used}", str(used)).replace("{total}", str(total))


def finalize_directive(*, structured_output: bool) -> str:
    """예산이 다한 실행에 그 실행의 최종 산출 형태에 맞는 마무리 지시를 고른다."""
    directive = _pacing()["landingDirective"]
    return str(directive["structured"] if structured_output else directive["freeText"])
