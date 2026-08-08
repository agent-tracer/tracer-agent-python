"""계약이 정한 성공 봉투를 벗기는 자리가 하나인지 검증한다."""

from __future__ import annotations

import pytest

from tracer_agent.shared.agents.shared.wire import MalformedEnvelope, unwrap_envelope


def test_성공_봉투에서_실은_것을_꺼낸다() -> None:
    assert unwrap_envelope({"ok": True, "data": {"id": "e1"}}) == {"id": "e1"}


def test_실은_것이_없어도_봉투이면_받는다() -> None:
    assert unwrap_envelope({"ok": True}) is None


@pytest.mark.parametrize(
    "payload",
    [{"ok": False, "error": {"code": "not_found"}}, {"data": {"id": "e1"}}, [1, 2], "text", None],
)
def test_봉투가_아니면_거절한다(payload: object) -> None:
    with pytest.raises(MalformedEnvelope):
        unwrap_envelope(payload)  # type: ignore[arg-type]
