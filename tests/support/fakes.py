"""테스트용 페이크: 네트워크 없이 그래프 배선을 검증한다."""

from __future__ import annotations

import json as _json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage
from langgraph.store.base import BaseStore

from tracer_agent.shared.agents.recipe_scan.models import DispatchPlan
from tracer_agent.shared.agents.shared.models import ModelRateDTO
from tracer_agent.shared.workflows.jobs_anchor import ScanAnchor
from tracer_agent.worker.agents.runtime.pricing import ModelRates

# 서버 카탈로그가 실행 봉투로 실어 보내는 단가를 대신한다.
WIRE_MODEL_RATES: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cacheWrite": 3.75, "cacheRead": 0.3},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cacheWrite": 1.25, "cacheRead": 0.1},
}


# 서버 카탈로그가 실행 봉투로 실어 보내는 한도를 대신한다.
WIRE_LIMITS: dict[str, float] = {"budgetUsd": 2.0, "maxTurns": 16, "maxOutputTokens": 16_000}


def mk_rates() -> ModelRates:
    return ModelRates({name: ModelRateDTO(**rate) for name, rate in WIRE_MODEL_RATES.items()})


def mk_tool_runtime(store: BaseStore | None = None) -> ToolRuntime[Any, Any]:
    """도구가 그래프 안에서 주입받는 런타임을 그래프 없이 세운다."""
    return ToolRuntime(
        state={},
        context=None,
        config={},
        stream_writer=lambda _chunk: None,
        tool_call_id=None,
        store=store,
    )


def mk_ai(
    content: str = "",
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
    response_metadata: dict[str, Any] | None = None,
) -> AIMessage:
    return AIMessage(
        content=content,
        tool_calls=tool_calls or [],
        usage_metadata=usage
        or {
            "input_tokens": 100,
            "output_tokens": 40,
            "total_tokens": 140,
            "input_token_details": {"cache_read": 10, "cache_creation": 5},
        },
        response_metadata=response_metadata or {},
    )


# 전문가 보고와 선별 계획은 조율자 턴을 소비하지 않고 대역이 직접 채운다.
_AUTO_REPORTS: dict[str, dict[str, Any]] = {
    "ProbeReport": {"probe": "timeline", "verdict": "조사했다"},
    "InspectReport": {"taskId": "task-1", "archivable": True, "reason": "의미 있는 활동이 없다"},
    "TriagePlan": {"inspect": []},
}


# 계획을 주지 않은 테스트는 전체 예산을 한 전문가에게 몰아준 계획으로 실행한다.
_DEFAULT_PLAN = DispatchPlan.model_validate(
    {"probes": [{"probe": "timeline", "depth": "deep", "question": "무엇을 했나"}]}
)


def _assert_system_messages_lead(messages: list[Any]) -> None:
    """Anthropic이 강제하는 자리를 대역도 강제한다: system은 메시지 목록 앞에 연속으로만 온다."""
    seen_non_system = False
    for message in messages:
        if getattr(message, "type", None) == "system":
            if seen_non_system:
                raise AssertionError("system 메시지가 human·ai 뒤에 왔다")
        else:
            seen_non_system = True


_OWN_QUESTION = "Your question: "


def _addressed_questions(text: str, declared: Mapping[str, Any]) -> set[str]:
    """전문가 프롬프트는 다른 전문가의 질문도 실으므로 자기 질문의 자리에 선 것만 고른다."""
    if _OWN_QUESTION not in text:
        return {key for key in declared if key in text}
    own = text.split(_OWN_QUESTION, 1)[1].split("\n", 1)[0].strip()
    return {key for key in declared if key == own}


class FakeToolLoopChat:
    """턴마다 도구 호출이나 구조화 출력을 순서대로 재생하는 도구 루프 대역이다."""

    def __init__(
        self,
        turns: list[Any],
        plan: Any = None,
        report: Any = None,
        worker_turns: dict[str, list[Any]] | None = None,
    ) -> None:
        self.turns = list(turns)
        self.plan = plan
        self.report = report
        # worker_turns는 지시문 부분 문자열을 열쇠로, 전문가·선별이 보고 전에 밟을 턴 대본을 잇는다.
        self.worker_turns = worker_turns or {}
        self._worker_cursor: dict[str, int] = {}
        self.probe_calls: list[list[str]] = []
        self.bound_tools: list[dict[str, Any]] = []
        self.output_config: dict[str, Any] | None = None
        self.requests: list[list[Any]] = []

    def bind_tools(self, tools: list[Any], **_kwargs: Any) -> FakeToolLoopChat:
        self.bound_tools = tools
        return self

    def bind(self, **kwargs: Any) -> FakeToolLoopChat:
        self.output_config = kwargs.get("output_config")
        return self

    def _auto_report(self, name: str) -> Any:
        override = (self.report or {}).get(name) if isinstance(self.report, dict) else self.report
        return override if override is not None else _AUTO_REPORTS[name]

    def _next_worker_turn(self, messages: list[Any]) -> Any | None:
        text = " ".join(str(getattr(message, "content", message)) for message in messages)
        for key, turns in self.worker_turns.items():
            if key not in _addressed_questions(text, self.worker_turns):
                continue
            cursor = self._worker_cursor.get(key, 0)
            if cursor >= len(turns):
                continue
            self._worker_cursor[key] = cursor + 1
            return turns[cursor]
        return None

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        _assert_system_messages_lead(messages)
        self.requests.append(list(messages))
        auto_names = {"ProbeReport", "InspectReport", "TriagePlan"}
        probe_tool = next(
            (tool for tool in self.bound_tools if getattr(tool, "name", "") in auto_names), None
        )
        if probe_tool is not None:
            name = probe_tool.name
            scripted = self._next_worker_turn(messages)
            if isinstance(scripted, list):
                # 전문가·선별이 보고 전에 자기 도구를 부르는 턴이라 도구 호출로 되돌린다.
                calls = [
                    {
                        "name": call["name"],
                        "args": call.get("args", {}),
                        "id": f"call-worker-{index}",
                        "type": "tool_call",
                    }
                    for index, call in enumerate(scripted)
                ]
                return mk_ai(tool_calls=calls)
            # 전문가 보고는 조율자 턴을 소비하지 않으므로 무엇을 가지고 실행했는지만 기록한다.
            self.probe_calls.append(
                [getattr(tool, "name", "") for tool in self.bound_tools if tool is not probe_tool]
            )
            report = scripted if scripted is not None else self._auto_report(name)
            return mk_ai(tool_calls=[{"name": name, "args": report, "id": "call-probe", "type": "tool_call"}])
        plan_tool = next(
            (tool for tool in self.bound_tools if getattr(tool, "name", "") == "DispatchPlan"), None
        )
        if plan_tool is not None:
            # 조율자 조사 계획은 턴 대본을 소비하지 않고 대역이 만든 계획을 그대로 되돌린다.
            plan = self.plan if self.plan is not None else _DEFAULT_PLAN
            args = plan.model_dump() if isinstance(plan, DispatchPlan) else plan
            return mk_ai(
                tool_calls=[{"name": plan_tool.name, "args": args, "id": "call-plan", "type": "tool_call"}]
            )
        if not self.turns:
            raise AssertionError("no fake turn remains")
        turn = self.turns.pop(0)
        if isinstance(turn, list):
            calls = [
                {
                    "name": call["name"],
                    "args": call.get("args", {}),
                    "id": f"call-{index}",
                    "type": "tool_call",
                }
                for index, call in enumerate(turn)
            ]
            return mk_ai(tool_calls=calls)
        structured_names = {"TitleSuggestionDraft", "CleanupDraft", "RecipeDraft"}
        structured_tool = next(
            (tool for tool in self.bound_tools if getattr(tool, "name", "") in structured_names), None
        )
        if structured_tool is not None:
            tool_name = structured_tool.name
            return mk_ai(
                tool_calls=[{"name": tool_name, "args": turn, "id": "call-structured", "type": "tool_call"}]
            )
        return mk_ai(content=_json.dumps(turn, ensure_ascii=False))


TRACER_API_URL = "http://tracer-api.test"
AGENT_API_URL = "http://agent-api.test"


_ELIGIBLE_SCAN_ANCHOR = ScanAnchor(id="task-1", origin="user", root=True, status="completed")


class FakeScanAnchors:
    """접수가 스캔 앵커의 자격을 묻는 창구이며 시험이 그 답을 정한다."""

    def __init__(self, anchor: ScanAnchor | None = _ELIGIBLE_SCAN_ANCHOR) -> None:
        self.anchor = anchor
        self.asked: list[tuple[str, str]] = []

    async def find(self, user_id: str, task_id: str) -> ScanAnchor | None:
        """물어 온 사용자와 태스크를 적고 시험이 정한 앵커를 낸다."""
        self.asked.append((user_id, task_id))
        return None if self.anchor is None else replace(self.anchor, id=task_id)
