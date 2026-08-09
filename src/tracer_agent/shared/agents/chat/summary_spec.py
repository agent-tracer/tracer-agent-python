"""요약을 언제 만들고 재생이 몇 턴을 남기는지를 두 축이 함께 읽는 계약에서 가져온다."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from ..shared.contract_root import CONTRACT_ROOT

CHAT_SUMMARY_PATH = CONTRACT_ROOT / "agent" / "chat" / "summary.json"


@dataclass(frozen=True)
class ChatSummarySpec:
    """요약을 접는 문턱과 접지 않고 남길 턱과 재생의 절대 상한과 요약 호출의 상한이다."""

    trigger_messages: int
    trigger_chars: int
    recent_keep_count: int
    max_replay_messages: int
    max_output_tokens: int
    deadline_ms: int


@lru_cache(maxsize=1)
def chat_summary_spec() -> ChatSummarySpec:
    """계약이 소유한 요약 규칙을 이 서비스가 읽는 한 벌로 낸다."""
    declared = json.loads(CHAT_SUMMARY_PATH.read_text(encoding="utf-8"))
    trigger = declared["production"]["trigger"]
    limits = declared["limits"]
    return ChatSummarySpec(
        trigger_messages=int(trigger["messages"]),
        trigger_chars=int(trigger["chars"]),
        recent_keep_count=int(declared["production"]["recentKeepCount"]),
        max_replay_messages=int(declared["consumption"]["maxReplayMessages"]),
        max_output_tokens=int(limits["maxOutputTokens"]),
        deadline_ms=int(limits["deadlineMs"]),
    )


def should_summarize(contents: Iterable[str]) -> bool:
    """메시지 수 또는 누적 글자 수 가운데 하나라도 문턱을 넘으면 접는다."""
    spec = chat_summary_spec()
    counted = list(contents)
    if len(counted) > spec.trigger_messages:
        return True
    return sum(len(content) for content in counted) > spec.trigger_chars
