"""실행을 종결로 접는 창구가 계약이 정한 두 갈래를 질의에 세우는지 본다."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tracer_agent.shared.agents.chat.execution_ledger import (
    ChatExecutionLedger,
    ChatExecutionSpend,
)

_지금 = datetime(2026, 8, 2, tzinfo=UTC)
_소비 = ChatExecutionSpend(model_used="model", cost_usd=0.0, num_turns=1, stop_reason="completed", usage={})


class 질의를_모으는_연결:
    """실행된 질의만 모으고 언제나 한 행을 바꾼 것으로 답한다."""

    def __init__(self) -> None:
        self.실행한: list[str] = []

    async def fetch(self, query: str, *_: Any) -> list[dict[str, Any]]:
        self.실행한.append(query)
        return [{"id": "execution"}]


def _관측을_보는_질의(연결: 질의를_모으는_연결) -> str:
    걸린 = [질의 for 질의 in 연결.실행한 if "agent_run_observations" in 질의]
    assert 걸린, "관측을 보는 질의가 없다"
    return 걸린[0]


class Test실행을_종결로_접는_조건:
    async def test_관측이_같은_종결을_새긴_행을_접는다(self) -> None:
        연결 = 질의를_모으는_연결()

        await ChatExecutionLedger(연결).fail_active("execution", "이유", _지금)

        assert "observation.status = 'failed'" in _관측을_보는_질의(연결)

    async def test_관측이_하나도_없는_행도_접는다(self) -> None:
        연결 = 질의를_모으는_연결()

        await ChatExecutionLedger(연결).fail_active("execution", "이유", _지금)

        assert "OR NOT EXISTS (" in _관측을_보는_질의(연결)

    async def test_아직_시작하지_않은_실행은_관측을_보지_않고_접는다(self) -> None:
        연결 = 질의를_모으는_연결()

        await ChatExecutionLedger(연결).cancel_active("execution", _지금)

        assert "status = 'queued'" in _관측을_보는_질의(연결)

    async def test_마치는_길도_같은_두_갈래를_쓴다(self) -> None:
        연결 = 질의를_모으는_연결()

        await ChatExecutionLedger(연결).complete_running("execution", "message", _소비, _지금)

        assert "OR NOT EXISTS (" in _관측을_보는_질의(연결)

    async def test_산출물을_붙이는_길은_관측을_요구한다(self) -> None:
        연결 = 질의를_모으는_연결()

        await ChatExecutionLedger(연결).record_canceled_outcome("execution", "message", _소비, _지금)

        질의 = _관측을_보는_질의(연결)
        assert "observation.status = 'cancelled'" in 질의
        assert "OR NOT EXISTS (" not in 질의
