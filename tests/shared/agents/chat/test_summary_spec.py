"""요약을 접는 문턱과 접을 대상과 재생 창의 크기가 계약과 같은지 검증한다."""

from __future__ import annotations

from typing import Any

from contract.conformance.runner.contract import read_json

from tracer_agent.shared.agents.chat.summary_spec import chat_summary_spec, should_summarize
from tracer_agent.shared.agents.chat.surface.replay import (
    select_messages_to_fold,
    select_messages_to_keep,
)

DECLARED: dict[str, Any] = read_json("agent/chat/summary.json")

# 자리 하나가 UTF-16 단위로는 둘이라 글자를 세는 단위가 드러난다.
ASTRAL = "𝄞"


def user(content: str) -> dict[str, Any]:
    return {"id": "m", "role": "user", "content": content, "tool_calls": None, "tool_call_id": None}


def tool(content: str) -> dict[str, Any]:
    return {"id": "t", "role": "tool", "content": content, "tool_calls": None, "tool_call_id": "c1"}


class Test계약이_가진_값:
    def test_문턱과_창의_크기를_계약에서_읽는다(self) -> None:
        spec = chat_summary_spec()

        assert spec.trigger_messages == DECLARED["production"]["trigger"]["messages"]
        assert spec.trigger_chars == DECLARED["production"]["trigger"]["chars"]
        assert spec.recent_keep_count == DECLARED["production"]["recentKeepCount"]
        assert spec.max_replay_messages == DECLARED["consumption"]["maxReplayMessages"]
        # 상한이 트리거보다 좁으면 정상 흐름이 늘 닿아 관측이 신호가 아니라 잡음이 된다.
        assert spec.max_replay_messages > spec.trigger_messages

    def test_요약_호출의_상한을_계약에서_읽는다(self) -> None:
        spec = chat_summary_spec()

        assert spec.max_output_tokens == DECLARED["limits"]["maxOutputTokens"]
        assert spec.deadline_ms == DECLARED["limits"]["deadlineMs"]


class Test접는_문턱:
    def test_문턱에_닿기만_하면_접지_않는다(self) -> None:
        spec = chat_summary_spec()

        assert should_summarize(["말"] * spec.trigger_messages) is False

    def test_메시지_수가_문턱을_넘으면_접는다(self) -> None:
        spec = chat_summary_spec()

        assert should_summarize(["말"] * (spec.trigger_messages + 1)) is True

    def test_누적_글자_수가_문턱을_넘으면_접는다(self) -> None:
        spec = chat_summary_spec()

        assert should_summarize(["가" * (spec.trigger_chars + 1)]) is True

    def test_글자_수는_코드포인트로_센다(self) -> None:
        spec = chat_summary_spec()

        assert DECLARED["production"]["trigger"]["charsUnit"] == "codePoint"
        assert should_summarize([ASTRAL * spec.trigger_chars]) is False


class Test접을_대상:
    def test_재생_창_바깥에_남는_오래된_메시지만_접는다(self) -> None:
        keep = chat_summary_spec().recent_keep_count
        messages = [user(f"말{index}") for index in range(keep + 5)]

        folded = select_messages_to_fold(messages)

        assert [row["content"] for row in folded] == [f"말{index}" for index in range(5)]
        assert len(select_messages_to_keep(messages)) == keep

    def test_접을_것과_남길_것이_서로의_여집합이다(self) -> None:
        # 쓰는 쪽의 두 규칙이 한 창을 나누므로 합이 전체이며 읽는 쪽은 지점을 보아 여기 끼지 않는다.
        messages = [user(f"말{index}") for index in range(chat_summary_spec().recent_keep_count + 5)]

        assert select_messages_to_fold(messages) + select_messages_to_keep(messages) == messages

    def test_접을_것이_없으면_대상이_비어_있다(self) -> None:
        messages = [user(f"말{index}") for index in range(chat_summary_spec().recent_keep_count)]

        assert select_messages_to_fold(messages) == []

    def test_도구_결과는_턴으로_세지_않으므로_창_안에_남는다(self) -> None:
        keep = chat_summary_spec().recent_keep_count
        messages = [user(f"말{index}") for index in range(keep)] + [tool("결과")]

        assert select_messages_to_fold(messages) == []
