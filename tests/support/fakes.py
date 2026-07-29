"""테스트용 페이크: 네트워크 없이 그래프 배선을 검증한다."""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage
from langgraph.store.base import BaseStore

from tracer_agent.shared.agents.shared.models import ModelRateDTO
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


# 전문가 보고와 선별 계획은 조율자 턴을 소비하지 않고 대역이 알아서 채운다.
_AUTO_REPORTS: dict[str, dict[str, Any]] = {
    "ProbeReport": {"probe": "timeline", "verdict": "조사했다"},
    "InspectReport": {"taskId": "task-1", "archivable": True, "reason": "의미 있는 활동이 없다"},
    "TriagePlan": {"inspect": []},
}


class _FakePlanner:
    def __init__(self, plan: Any) -> None:
        self._plan = plan

    async def ainvoke(self, _messages: Any, **_kwargs: Any) -> Any:
        return self._plan


class FakeToolLoopChat:
    """턴마다 도구 호출이나 구조화 출력을 순서대로 재생하는 도구 루프 대역이다."""

    def __init__(
        self,
        turns: list[Any],
        plan: Any = None,
        report: Any = None,
        worker_turns: dict[str, list[Any]] | None = None,
    ) -> None:
        # turns의 각 항목은 도구 호출 목록(list)이거나 최종 구조화 출력(dict)이다.
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

    def with_structured_output(self, schema: Any, **_kwargs: Any) -> _FakePlanner:
        if self.plan is not None:
            return _FakePlanner(self.plan)
        # 계획을 안 준 테스트는 전체 예산을 한 전문가에게 몰아준 계획으로 돈다.
        return _FakePlanner(
            schema.model_validate(
                {"probes": [{"probe": "timeline", "weight": 10, "question": "무엇을 했나"}]}
            )
        )

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
            if key not in text:
                continue
            cursor = self._worker_cursor.get(key, 0)
            if cursor >= len(turns):
                continue
            self._worker_cursor[key] = cursor + 1
            return turns[cursor]
        return None

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
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
            # 전문가 보고는 조율자 턴을 소비하지 않으므로 무엇을 쥐고 돌았는지만 기록한다.
            self.probe_calls.append(
                [getattr(tool, "name", "") for tool in self.bound_tools if tool is not probe_tool]
            )
            report = scripted if scripted is not None else self._auto_report(name)
            return mk_ai(tool_calls=[{"name": name, "args": report, "id": "call-probe", "type": "tool_call"}])
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

    def cached_blocks(self) -> int:
        """마지막 요청에서 캐시 경계가 붙은 블록 수다."""
        last = self.requests[-1] if self.requests else []
        found = 0
        for message in last:
            content = getattr(message, "content", None)
            if isinstance(content, list):
                found += sum(1 for block in content if isinstance(block, dict) and "cache_control" in block)
        return found


class FakeLedgerConnection:
    """소유 확인과 페이지와 총계를 캔 데이터로 돌려주는 원장 뷰 조회 대역이다."""

    def __init__(self, ledger: FakeLedger) -> None:
        self._ledger = ledger

    async def fetchval(self, statement: str, *_args: Any) -> Any:
        if "count(*)" in statement:
            return self._ledger.total
        return 1 if self._ledger.owned else None

    async def fetchrow(self, _statement: str, *_args: Any) -> Any:
        return self._ledger.task if self._ledger.owned else None

    async def fetch(self, statement: str, *args: Any) -> list[Any]:
        if "agent_rule_view" in statement:
            return list(self._ledger.rules)
        self._ledger.queries.append({"desc": "ORDER BY seq DESC" in statement, "args": list(args)})
        return list(self._ledger.rows)


class FakeLedgerAcquire:
    """asyncpg 풀의 연결 획득 비동기 컨텍스트 대역."""

    def __init__(self, ledger: FakeLedger) -> None:
        self._ledger = ledger

    async def __aenter__(self) -> FakeLedgerConnection:
        return FakeLedgerConnection(self._ledger)

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class FakeLedger:
    """조회 인자를 기록하고 캔 행을 돌려주는 원장 연결 풀 공급자 대역이다."""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        owned: bool = True,
        total: int | None = None,
        rules: list[dict[str, Any]] | None = None,
        task: dict[str, Any] | None = None,
    ) -> None:
        self.rows = rows or []
        self.owned = owned
        self.total = len(self.rows) if total is None else total
        self.rules = rules or []
        self.task = task or {
            "id": "t1",
            "title": "x",
            "status": "completed",
            "task_kind": "monitoring",
            "workspace_path": None,
            "created_at": datetime(2026, 7, 14, tzinfo=UTC),
            "updated_at": datetime(2026, 7, 14, tzinfo=UTC),
        }
        self.queries: list[dict[str, Any]] = []

    async def pool(self) -> FakeLedger:
        return self

    def acquire(self) -> FakeLedgerAcquire:
        return FakeLedgerAcquire(self)

    async def close(self) -> None:
        return None


class FakeSearch:
    """색인별 캔 히트를 돌려주고 질의 본문을 기록하는 검색 색인 대역이다."""

    def __init__(self, hits: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.hits = hits or {}
        self.bodies: list[dict[str, Any]] = []

    async def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append({"index": index, "body": body})
        found = self.hits.get(index, [])
        return {"hits": {"total": {"value": len(found)}, "hits": found}}

    async def close(self) -> None:
        return None
