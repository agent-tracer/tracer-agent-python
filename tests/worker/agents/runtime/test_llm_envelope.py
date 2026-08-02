"""모델 이름으로 낸 봉투가 계약의 effort·thinking 어휘를 벗어나지 않는 것을 검증한다."""

from __future__ import annotations

from tracer_agent.worker.agents.runtime.llm.envelope import model_envelope


def test_봉투가_없는_모델은_빈_봉투를_낸다() -> None:
    envelope = model_envelope("claude-haiku-4-5")

    assert envelope.max_output_tokens is None
    assert envelope.effort is None
    assert envelope.thinking is None


def test_claude_opus_5는_기능의_기본_한도보다_넉넉한_출력_한도를_받는다() -> None:
    envelope = model_envelope("claude-opus-5")

    assert envelope.max_output_tokens is not None
    assert envelope.max_output_tokens > 16_000
    assert envelope.effort == "low"
