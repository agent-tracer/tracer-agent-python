"""전문가 조사가 실패했을 때 조율자가 읽는 문구를 계약에서 읽는다."""

from __future__ import annotations

from ..shared.contract_failures import WORKER_FAILED_KEY, failure_text

CONTRACT_AGENT = "recipe-scan"

# 전문가 하나가 통째로 실패했을 때 판정 자리에 들어가며 계약이 문장을 소유한다.
WORKER_FAILED = failure_text(CONTRACT_AGENT, WORKER_FAILED_KEY)
