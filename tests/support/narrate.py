"""테스트 실행 결과를 pytest -s로 보이는 동작 흐름 서사로 렌더링한다."""

from __future__ import annotations

from typing import Any

from tracer_agent.shared.agents.shared.models import AgentResponse


def narrate(label: str, res: AgentResponse) -> None:
    """시나리오 이름 아래 노드 전환과 모델 응답의 타임라인과 결과를 순서대로 찍는다."""
    print(f"\n===== {label} =====")
    _print_timeline(res)
    _print_result(res)


def _print_timeline(res: AgentResponse) -> None:
    print("-- timeline --")
    steps = [step for step in res.steps if step.role in {"graph", "assistant", "tool"}]
    if not steps:
        print("  (기록된 스텝이 없다)")
        return
    for step in steps:
        if step.role == "graph":
            duration = f" {step.durationMs}ms" if step.durationMs is not None else ""
            print(f"  [{step.seq}] {step.nodeName} {step.eventKind}{duration}: {step.content}")
        elif step.role == "assistant":
            for call in step.toolCalls:
                print(f"  [{step.seq}]   ai → {call.name}({_short(str(call.args), 300)})")
            if step.content and not step.toolCalls:
                print(f"  [{step.seq}]   ai: {_short(step.content, 300)}")
        else:
            print(f"  [{step.seq}]   tool {step.toolName}: {_short(step.content, 200)}")


def _print_result(res: AgentResponse) -> None:
    print("-- result --")
    if res.error is not None:
        print(f"  error[{res.error.subtype}]: {res.error.summary}")
    else:
        print(f"  data: {res.data}")
    print(f"  turns={res.numTurns} usage={res.usage}")


def _short(content: Any, limit: int) -> str:
    text = content if isinstance(content, str) else str(content)
    return text if len(text) <= limit else text[:limit] + "…"
