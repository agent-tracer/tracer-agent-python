"""한 턴이 모델에게 되돌려 줄 이력을 요약 지점과 절대 상한과 도구 호출 짝 맞추기로 만든다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...runtime.ledger import SqlRow
from ..summary_spec import chat_summary_spec

ASSISTANT = "assistant"
TOOL = "tool"

MESSAGE_NOT_FOUND = "Chat replay message not found"


class ChatReplayMessageMissing(LookupError):
    """실행이 가리키는 앵커 메시지가 이력에 없어 재생을 만들 수 없다."""


@dataclass(frozen=True)
class ChatReplay:
    """이번 턴이 되돌려 줄 이력과, 절대 상한에 닿아 앞을 버렸는지다."""

    messages: list[dict[str, Any]]
    stored: int
    kept: int

    @property
    def truncated(self) -> bool:
        """상한에 닿아 앞을 자른 재생이며 요약이 거듭 실패했다는 신호다."""
        return self.kept < self.stored


def build_chat_replay(
    messages: list[SqlRow], replay_anchor_message_id: str, summary_through_message_id: str | None
) -> ChatReplay:
    """이번 턴의 재생 이력을 만드는 유일한 규칙이며 자르기와 접기를 함께 소유한다."""
    until_anchor = _until_anchor(messages, replay_anchor_message_id)
    window = select_replay_messages(until_anchor, summary_through_message_id)
    paired = _paired_call_ids(window)
    replayed: list[dict[str, Any]] = []
    for row in window:
        replayed.extend(_replay_message(row, paired))
    return ChatReplay(messages=replayed, stored=len(until_anchor), kept=len(window))


def select_replay_messages(messages: list[SqlRow], summary_through_message_id: str | None) -> list[SqlRow]:
    """요약이 덮은 지점 다음부터 싣되 어느 경우에도 계약이 정한 절대 상한을 넘기지 않는다."""
    after_summary = _after_summary(messages, summary_through_message_id)
    ceiling = chat_summary_spec().max_replay_messages
    return after_summary[-ceiling:] if len(after_summary) > ceiling else after_summary


def _after_summary(messages: list[SqlRow], summary_through_message_id: str | None) -> list[SqlRow]:
    if summary_through_message_id is None:
        return messages
    for index, row in enumerate(messages):
        if row["id"] == summary_through_message_id:
            return messages[index + 1 :]
    # 앵커가 지점보다 앞이면 그 지점이 이 창에 없으므로 자르면 이 턴이 볼 앞부분이 사라진다.
    return messages


def select_messages_to_keep(messages: list[SqlRow]) -> list[SqlRow]:
    """쓰는 쪽이 접지 않고 남길 최근 턴이며 도구 결과는 턴으로 세지 않는다."""
    keep = chat_summary_spec().recent_keep_count
    turns = 0
    for index in range(len(messages) - 1, -1, -1):
        if messages[index]["role"] == TOOL:
            continue
        turns += 1
        if turns > keep:
            return messages[index + 1 :]
    return messages


def select_messages_to_fold(messages: list[SqlRow]) -> list[SqlRow]:
    """요약이 접을 대상은 남길 최근 턴 바깥의 오래된 메시지다."""
    kept = len(select_messages_to_keep(messages))
    return messages[: len(messages) - kept]


def _until_anchor(messages: list[SqlRow], replay_anchor_message_id: str) -> list[SqlRow]:
    """앵커 메시지까지가 이력이며 그 뒤 행은 이 턴이 만들 것이다."""
    for index, row in enumerate(messages):
        if row["id"] == replay_anchor_message_id:
            return messages[: index + 1]
    raise ChatReplayMessageMissing(MESSAGE_NOT_FOUND)


def _paired_call_ids(window: list[SqlRow]) -> set[str]:
    """확인 게이트 때문에 답 없는 호출이 정상적으로 쌓이고 거절당한 호출은 끝내 짝이 없다."""
    paired: set[str] = set()
    for index, row in enumerate(window):
        declared = _declared_call_ids(row)
        if not declared:
            continue
        if _answered_call_ids(window, index, declared) == declared:
            paired |= declared
    return paired


def _declared_call_ids(row: SqlRow) -> set[str]:
    if row["role"] != ASSISTANT:
        return set()
    return {str(call["id"]) for call in _tool_calls(row)}


def _answered_call_ids(window: list[SqlRow], call_index: int, declared: set[str]) -> set[str]:
    """답은 호출 바로 뒤에 이어져야 하며 다른 역할이 끼어드는 순간 그 호출은 답을 못 받았다."""
    answered: set[str] = set()
    for follower in window[call_index + 1 :]:
        if follower["role"] != TOOL:
            break
        call_id = follower["tool_call_id"]
        if call_id is not None and str(call_id) in declared:
            answered.add(str(call_id))
    return answered


def _replay_message(row: SqlRow, paired: set[str]) -> list[dict[str, Any]]:
    content = str(row["content"])
    if row["role"] == TOOL:
        call_id = row["tool_call_id"]
        # 짝을 잃은 결과도 승인된 행위의 산물이라 버리지 않고 인용만 지워 평문 문맥으로 남긴다.
        if call_id is not None and str(call_id) in paired:
            return [{"role": TOOL, "content": content, "toolCallId": str(call_id)}]
        return [{"role": TOOL, "content": content}]
    if row["role"] != ASSISTANT:
        return [{"role": str(row["role"]), "content": content}]
    calls = [call for call in _tool_calls(row) if str(call["id"]) in paired]
    if calls:
        return [{"role": ASSISTANT, "content": content, "toolCalls": calls}]
    return [{"role": ASSISTANT, "content": content}] if content else []


def _tool_calls(row: SqlRow) -> list[dict[str, Any]]:
    calls = row["tool_calls"]
    if not isinstance(calls, list):
        return []
    return [call for call in calls if isinstance(call, dict) and "id" in call]
