"""Anthropic 채팅 클라이언트 실행 제한 검증."""

from __future__ import annotations

import pytest

from tracer_agent.worker.agents.runtime.llm import client


def test_클라이언트는_데드라인과_출력_상한을_전달한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_chat(**kwargs: object) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(client, "ChatAnthropic", fake_chat)

    result = client.make_chat("claude-sonnet-4-6", "secret", 2_500, 1_024)

    assert result is sentinel
    assert captured == {
        "model": "claude-sonnet-4-6",
        "api_key": "secret",
        "max_retries": 0,
        "timeout": 2.5,
        "max_tokens": 1_024,
        "streaming": False,
    }


def test_스트리밍을_켜면_클라이언트에_그대로_전달한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_chat(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(client, "ChatAnthropic", fake_chat)

    client.make_chat("model", "secret", 2_500, streaming=True)

    assert captured["streaming"] is True


def test_클라이언트_시간제한은_최소_1초를_보장한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_chat(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(client, "ChatAnthropic", fake_chat)

    client.make_chat("model", "secret", 10)

    assert captured["timeout"] == 1.0


def test_모델마다_다른_출력_한도가_봉투에서_온다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_chat(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(client, "ChatAnthropic", fake_chat)

    client.make_chat("claude-opus-5", "secret", 2_500, 16_000)

    assert captured["max_tokens"] == 32_000
    assert captured["reasoning_effort"] == "low"


def test_봉투에_없는_모델은_기능의_기본_출력_한도를_그대로_쓴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_chat(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(client, "ChatAnthropic", fake_chat)

    client.make_chat("claude-sonnet-4-6", "secret", 2_500, 16_000)

    assert captured["max_tokens"] == 16_000
    assert "reasoning_effort" not in captured
