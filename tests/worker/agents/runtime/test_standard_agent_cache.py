"""표준 agent 미들웨어가 도구 결과 캐시 경계를 두는 방식을 검증한다."""

from langchain_core.messages import ToolMessage

from tracer_agent.worker.agents.runtime.llm.standard_agent import cache_tool_messages


def test_최근_도구_결과_두_개에만_캐시_경계를_둔다() -> None:
    messages = [ToolMessage(content=f"result-{index}", tool_call_id=f"call-{index}") for index in range(3)]

    cached = cache_tool_messages(messages)
    blocks = [message.content[0] for message in cached]

    assert "cache_control" not in blocks[0]
    assert [block["cache_control"] for block in blocks[1:]] == [
        {"type": "ephemeral"},
        {"type": "ephemeral"},
    ]
    assert [message.content for message in messages] == ["result-0", "result-1", "result-2"]
