"""도구가 실패했을 때 모델이 읽는 문구를 계약에서 읽는다."""

from __future__ import annotations

from ..shared.contract_failures import TOOL_FAILED_KEY, failure_text

CONTRACT_AGENT = "title-suggestion"

TOOL_FAILED = failure_text(CONTRACT_AGENT, TOOL_FAILED_KEY)
