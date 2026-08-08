"""재생 계산이 창을 어디서 자르고 도구 호출의 짝을 어떻게 접는지 검증한다."""

from __future__ import annotations

from typing import Any

import pytest

from tracer_agent.shared.agents.chat.summary_spec import chat_summary_spec
from tracer_agent.shared.agents.chat.surface.replay import (
    ChatReplayMessageMissing,
    build_chat_replay,
    select_replay_messages,
)

RECENT_KEEP_COUNT = chat_summary_spec().recent_keep_count

CALL = {"id": "call-1", "name": "propose_task_write", "args": {"action": "archive", "taskId": "task-1"}}


def _row(message_id: str, role: str, content: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": message_id,
        "role": role,
        "content": content,
        "tool_calls": extra.get("tool_calls"),
        "tool_call_id": extra.get("tool_call_id"),
    }


def user(message_id: str, content: str) -> dict[str, Any]:
    return _row(message_id, "user", content)


def assistant(message_id: str, content: str, calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return _row(message_id, "assistant", content, tool_calls=calls)


def tool(message_id: str, content: str, call_id: str) -> dict[str, Any]:
    return _row(message_id, "tool", content, tool_call_id=call_id)


class Test재생_이력:
    def test_이번_턴의_사용자_메시지_뒤는_이력으로_세지_않는다(self) -> None:
        replay = build_chat_replay(
            [user("m1", "안녕"), assistant("m2", "네"), user("m3", "이어서"), assistant("m4", "아직")],
            "m3",
            None,
        )

        assert [message["content"] for message in replay] == ["안녕", "네", "이어서"]

    def test_결과가_이어진_도구_호출은_인용을_그대로_남긴다(self) -> None:
        replay = build_chat_replay(
            [assistant("m1", "부른다", [CALL]), tool("m2", "결과", "call-1"), user("m3", "고마워")],
            "m3",
            None,
        )

        assert replay[0]["toolCalls"] == [CALL]
        assert replay[1]["toolCallId"] == "call-1"

    def test_짝을_잃은_도구_결과는_인용을_지우고_평문으로_남긴다(self) -> None:
        replay = build_chat_replay(
            [
                assistant("m1", "부른다", [CALL]),
                user("m2", "잠깐"),
                tool("m3", "뒤늦은 결과", "call-1"),
                user("m4", "이어서"),
            ],
            "m4",
            None,
        )

        assert all("toolCalls" not in message for message in replay)
        assert all("toolCallId" not in message for message in replay)
        assert [message["content"] for message in replay] == ["부른다", "잠깐", "뒤늦은 결과", "이어서"]

    def test_호출만_남은_빈_어시스턴트_메시지는_재생하지_않는다(self) -> None:
        replay = build_chat_replay([assistant("m1", "", [CALL]), user("m2", "이어서")], "m2", None)

        assert [message["content"] for message in replay] == ["이어서"]

    def test_이력에_없는_사용자_메시지를_가리키면_거절한다(self) -> None:
        with pytest.raises(ChatReplayMessageMissing):
            build_chat_replay([user("m1", "안녕")], "없음", None)


class Test재생_창:
    def test_요약이_없으면_창을_자르지_않는다(self) -> None:
        rows = [user(f"m{index}", "말") for index in range(30)]

        assert len(select_replay_messages(rows, False)) == 30

    def test_요약이_있으면_최근_대화_턴만_남긴다(self) -> None:
        rows = [user(f"m{index}", "말") for index in range(30)]

        assert len(select_replay_messages(rows, True)) == RECENT_KEEP_COUNT

    def test_도구_결과는_대화_턴으로_세지_않는다(self) -> None:
        rows = [user(f"m{index}", "말") for index in range(RECENT_KEEP_COUNT)]
        rows += [tool("t1", "결과", "call-1"), tool("t2", "결과", "call-2")]

        assert len(select_replay_messages(rows, True)) == len(rows)

    def test_요약이_공백뿐이면_창을_자르지_않는다(self) -> None:
        rows = [user(f"m{index}", "말") for index in range(30)]
        replay = build_chat_replay(rows, "m29", "   ")

        assert len(replay) == 30
