"""원장을 빌리지 못한 창구가 낼 어휘를 계약에서 가져온다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from .contract_root import CONTRACT_ROOT

LEDGER_AVAILABILITY_PATH = CONTRACT_ROOT / "agent" / "shared" / "ledger.availability.json"


@dataclass(frozen=True)
class LedgerAvailabilityRejection:
    """원장 연결을 빌리지 못한 요청의 거절이며 두 구현체가 같은 글자를 낸다."""

    status: int
    code: str
    message: str


@lru_cache(maxsize=1)
def ledger_unavailable_rejection() -> LedgerAvailabilityRejection:
    """계약이 정한 상태와 코드와 문구를 낸다."""
    declared = json.loads(LEDGER_AVAILABILITY_PATH.read_text(encoding="utf-8"))
    return LedgerAvailabilityRejection(
        status=int(declared["status"]),
        code=str(declared["code"]),
        message=str(declared["message"]),
    )
