"""실행 궤적에서 근거 수집과 중복 조사를 재는 지표를 낸다."""

from __future__ import annotations

import json
from collections.abc import Sequence

from tracer_agent.shared.agents.shared.models import AgentStepDTO, AgentStepToolCall


def _call_key(call: AgentStepToolCall) -> str:
    """같은 도구를 같은 인자로 부른 것인지 가리는 열쇠다."""
    args = json.dumps(call.args, sort_keys=True, ensure_ascii=False)
    return f"{call.name}({args})"


def duplicate_tool_calls(steps: Sequence[AgentStepDTO]) -> int:
    """한 실행에서 같은 도구를 같은 인자로 다시 부른 횟수이며 조사가 겹쳤다는 신호다."""
    seen: set[str] = set()
    duplicates = 0
    for step in steps:
        for call in step.toolCalls:
            key = _call_key(call)
            if key in seen:
                duplicates += 1
            seen.add(key)
    return duplicates


def called_tool_names(steps: Sequence[AgentStepDTO]) -> set[str]:
    """이 실행이 실제로 부른 도구 이름이다."""
    return {call.name for step in steps for call in step.toolCalls}


def covers_expected_calls(steps: Sequence[AgentStepDTO], expected: Sequence[str]) -> bool:
    """모델이 같은 결론에 다른 순서로 닿는 것은 회귀가 아니므로 기대한 호출이 모두 나왔는지만 본다."""
    return not missing_tool_calls(steps, expected)


def missing_tool_calls(steps: Sequence[AgentStepDTO], expected: Sequence[str]) -> list[str]:
    """기대했는데 한 번도 부르지 않은 도구다."""
    called = called_tool_names(steps)
    return [name for name in expected if name not in called]
