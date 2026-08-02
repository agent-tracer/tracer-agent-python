"""recipe-scan 그래프의 위상·비용 배분·모델 주도 조사를 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from anthropic import AuthenticationError

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, FakeToolLoopChat, FakeTracerApi
from tests.support.narrate import narrate
from tests.support.prompts import CONTRACT_VERSION, RECIPE_SCAN_PROMPT
from tracer_agent.shared.agents.recipe_scan.models import DispatchPlan, RecipeScanRequest
from tracer_agent.shared.agents.shared.models import AgentResponse
from tracer_agent.worker.agents.recipe_scan import agent as recipe_mod
from tracer_agent.worker.agents.runtime.errors import BudgetExceeded, OutputTruncated
from tracer_agent.worker.agents.runtime.execution.runner import execute

_COMPLETION = {"url": "http://worker:8810/runs/complete", "token": "done-recipe"}


def _event_row(event_id: str, turn_id: str, title: str) -> dict[str, object]:
    return {
        "id": event_id,
        "seq": "1",
        "turnId": turn_id,
        "kind": "execute_tool",
        "title": title,
        "filePaths": [],
        "metadata": {},
        "occurredAt": "2026-07-14T00:00:00Z",
    }


def _default_ledger() -> FakeTracerApi:
    return FakeTracerApi(
        [
            _event_row("event-1", "turn-1", "마이그레이션"),
            _event_row("event-2", "turn-2", "대시보드"),
        ],
        rules=[
            {
                "id": "rule-1",
                "name": "규칙",
                "expectation": {"kind": "action", "tool": "Bash"},
                "taskId": "t1",
                "anchorEventId": "event-1",
                "source": "agent",
                "severity": "info",
                "rationale": None,
                "signature": "sig-1",
                "reviewState": "active",
                "createdAt": "2026-07-14T00:00:00Z",
            }
        ],
    )


def _request(**overrides: Any) -> RecipeScanRequest:
    values: dict[str, Any] = {
        "model": "claude-sonnet-4-6",
        "apiKey": "sk-test",
        "modelRates": WIRE_MODEL_RATES,
        "limits": WIRE_LIMITS,
        "taskId": "t1",
        "language": "ko",
        "userId": "user-1",
        "completionCallback": _COMPLETION,
    }
    values.update(overrides)
    return RecipeScanRequest.model_validate(values)


def _recipe(**overrides: Any) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "Add migration",
        "intent": "마이그레이션",
        "description": "설명",
        "summary_md": "- a",
        "request": "사용자가 마이그레이션 작업을 recipe로 만들라고 했다.",
        "corrections": [],
        "pitfalls": [],
        "governing_rules": ["rule-1"],
        "contributing_slices": [{"taskId": "t1", "turnIds": ["turn-1"], "eventIds": ["event-1"]}],
        "rationale": "근거",
    }
    base.update(overrides)
    return base


# 조율자는 근거를 직접 캐지 않으므로 timeline·rules 전문가가 병렬로 장부를 채워 준다.
_EVIDENCE_PLAN = DispatchPlan(
    probes=[
        {"probe": "timeline", "weight": 5, "question": "무엇을 했나"},  # type: ignore[list-item]
        {"probe": "rules", "weight": 3, "question": "어떤 규칙이 걸렸나"},  # type: ignore[list-item]
    ]
)


def _evidence_probes() -> dict[str, list[Any]]:
    """두 전문가가 이벤트와 규칙을 읽어 조율자가 인용할 병합 장부를 만든다."""
    return {
        "무엇을 했나": [
            [{"name": "get_task_events", "args": {"taskId": "t1"}}],
            {
                "probe": "timeline",
                "verdict": "마이그레이션과 대시보드 작업을 확인했다",
                "excerpts": [{"taskId": "t1", "eventId": "event-1", "text": "마이그레이션"}],
                "exhausted": False,
            },
        ],
        "어떤 규칙이 걸렸나": [
            [{"name": "list_rules", "args": {"taskId": "t1"}}],
            {"probe": "rules", "verdict": "규칙 하나가 적용된다", "excerpts": [], "exhausted": False},
        ],
    }


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    chat: FakeToolLoopChat,
    ledger: FakeTracerApi | None = None,
) -> AgentResponse:
    req = _request()
    monkeypatch.setattr(recipe_mod, "make_chat", lambda *_a, **_k: chat)
    fake_ledger = ledger if ledger is not None else _default_ledger()
    return await execute(
        "recipe-scan",
        req.model,
        req.deadlineMs,
        lambda usage: recipe_mod.run_recipe_scan(req, fake_ledger, usage, RECIPE_SCAN_PROMPT),
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )


async def test_전문가가_모은_장부로_조율자가_후보를_낸다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = FakeToolLoopChat([{"recipes": [_recipe()]}], plan=_EVIDENCE_PLAN, worker_turns=_evidence_probes())

    res = await _run(monkeypatch, chat)

    assert res.error is None
    assert res.data is not None and res.data["recipes"][0]["title"] == "Add migration"
    # 근거를 캐는 도구 호출은 조율자가 아니라 두 전문가에게서만 나온다.
    assert sorted(step.toolName for step in res.steps if step.role == "tool") == [
        "get_task_events",
        "list_rules",
    ]
    narrate("recipe-scan :: 전문가가 모은 장부로 조율자가 후보를 낸다", res)


async def test_도구를_한_번도_부르지_않아도_빈_결과로_끝난다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = FakeToolLoopChat([{"recipes": []}])
    ledger = _default_ledger()

    res = await _run(monkeypatch, chat, ledger)

    assert res.error is None and res.data["recipes"] == []
    # 도구를 부르지 않았으니 원장을 한 번도 조회하지 않는다.
    assert ledger.calls == []
    narrate("recipe-scan :: 도구를 한 번도 부르지 않아도 빈 결과로 끝난다", res)


async def test_띄울_전문가가_없으면_조율자를_부르지_않고_빈_결과로_끝난다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 조율자 턴 대본을 비워 두어, 조사가 조율자에게 닿으면 그 자리에서 무너지게 한다.
    chat = FakeToolLoopChat([], plan=DispatchPlan())
    ledger = _default_ledger()

    res = await _run(monkeypatch, chat, ledger)

    assert res.error is None and res.data["recipes"] == []
    assert not any(step.nodeName in {"probe", "investigate"} for step in res.steps)
    assert ledger.calls == []
    assert any("survey -> no specialists" in step.content for step in res.steps)
    narrate("recipe-scan :: 띄울 전문가가 없으면 조율자를 부르지 않고 빈 결과로 끝난다", res)


async def test_도구가_돌려주지_않은_ID는_한_번_수정한_뒤_검증한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = _recipe(governing_rules=["invented-rule"])
    chat = FakeToolLoopChat(
        [{"recipes": [invalid]}, {"recipes": [_recipe()]}],
        plan=_EVIDENCE_PLAN,
        worker_turns=_evidence_probes(),
    )

    res = await _run(monkeypatch, chat)

    assert res.error is None and res.data is not None
    assert res.data["recipes"][0]["governing_rules"] == ["rule-1"]
    failures = [step for step in res.steps if step.eventKind == "validation.failed"]
    assert len(failures) == 1 and "invented-rule" in failures[0].content
    assert sum(step.nodeName == "repair" and step.eventKind == "node.started" for step in res.steps) == 1
    narrate("recipe-scan :: 도구가 돌려주지 않은 ID는 한 번 수정한 뒤 검증한다", res)


async def test_수정_후에도_ID가_거짓이면_후보를_버린다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = _recipe(contributing_slices=[{"taskId": "t1", "turnIds": [], "eventIds": ["ghost"]}])
    chat = FakeToolLoopChat(
        [
            [{"name": "get_task_events", "args": {"taskId": "t1"}}],
            {"recipes": [invalid]},
            {"recipes": [invalid]},
        ]
    )

    res = await _run(monkeypatch, chat)

    assert res.error is None and res.data["recipes"] == []
    assert sum(step.eventKind == "validation.failed" for step in res.steps) == 2
    narrate("recipe-scan :: 수정 후에도 ID가 거짓이면 후보를 버린다", res)


async def test_서로_다른_turn은_각각의_후보로_남는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    second = _recipe(
        title="Add dashboard",
        contributing_slices=[{"taskId": "t1", "turnIds": ["turn-2"], "eventIds": ["event-2"]}],
    )
    chat = FakeToolLoopChat(
        [{"recipes": [_recipe(), second]}], plan=_EVIDENCE_PLAN, worker_turns=_evidence_probes()
    )

    res = await _run(monkeypatch, chat)

    assert res.error is None and res.data is not None
    assert [recipe["title"] for recipe in res.data["recipes"]] == ["Add migration", "Add dashboard"]
    narrate("recipe-scan :: 서로 다른 turn은 각각의 후보로 남는다", res)


async def test_같은_turn을_두_후보가_주장하면_수정을_요구한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duplicate = _recipe(title="Add dashboard")
    chat = FakeToolLoopChat(
        [{"recipes": [_recipe(), duplicate]}, {"recipes": [_recipe()]}],
        plan=_EVIDENCE_PLAN,
        worker_turns=_evidence_probes(),
    )

    res = await _run(monkeypatch, chat)

    assert res.error is None and res.data is not None
    assert [recipe["title"] for recipe in res.data["recipes"]] == ["Add migration"]
    failures = [step for step in res.steps if step.eventKind == "validation.failed"]
    assert len(failures) == 1 and "turn-1" in failures[0].content
    narrate("recipe-scan :: 같은 turn을 두 후보가 주장하면 수정을 요구한다", res)


def _redispatch_probe(question: str) -> dict[str, list[Any]]:
    """조율자가 추가로 부른 전문가 하나가 다시 근거를 훑고 보고하는 대본이다."""
    return {
        question: [
            {"probe": "repetition", "verdict": "다른 태스크에도 반복된다", "excerpts": [], "exhausted": False}
        ]
    }


_REDISPATCH_QUESTION = "다른 태스크에도 있나"
_REDISPATCH = [{"probe": "repetition", "weight": 2, "question": _REDISPATCH_QUESTION}]


async def test_조율자가_재파견을_요청하면_전문가를_한_번_더_부르고_완주한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = FakeToolLoopChat(
        [{"recipes": [], "redispatch": _REDISPATCH}, {"recipes": [_recipe()]}],
        plan=_EVIDENCE_PLAN,
        worker_turns={**_evidence_probes(), **_redispatch_probe(_REDISPATCH_QUESTION)},
    )

    res = await _run(monkeypatch, chat)

    assert res.error is None and res.data is not None
    assert [recipe["title"] for recipe in res.data["recipes"]] == ["Add migration"]
    # 조율자가 두 번 종합했고, 그 사이 추가 파견이 궤적에 남는다.
    completed = [
        step for step in res.steps if step.nodeName == "investigate" and step.eventKind == "node.completed"
    ]
    assert len(completed) == 2
    assert any("redispatch repetition:2" in step.content for step in res.steps)
    # 초기 두 전문가에 재파견 하나를 더해 전문가는 세 번 돈다.
    probes = [step for step in res.steps if step.nodeName == "probe" and step.eventKind == "node.completed"]
    assert len(probes) == 3
    narrate("recipe-scan :: 조율자가 재파견을 요청하면 전문가를 한 번 더 부르고 완주한다", res)


async def test_재파견_상한을_넘긴_두_번째_요청은_무시하고_끝낸다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = FakeToolLoopChat(
        [{"recipes": [], "redispatch": _REDISPATCH}, {"recipes": [], "redispatch": _REDISPATCH}],
        plan=_EVIDENCE_PLAN,
        worker_turns={**_evidence_probes(), **_redispatch_probe(_REDISPATCH_QUESTION)},
    )

    res = await _run(monkeypatch, chat)

    # 두 번째 재파견 요청은 상한(1회)을 넘어 무시되고 가진 것(빈 후보)으로 끝난다.
    assert res.error is None and res.data["recipes"] == []
    redispatched = [step for step in res.steps if "redispatch repetition" in step.content]
    assert len(redispatched) == 1
    # 재파견은 한 번만 실렸으므로 전문가는 초기 둘에 하나만 더해 세 번 돈다.
    probes = [step for step in res.steps if step.nodeName == "probe" and step.eventKind == "node.completed"]
    assert len(probes) == 3
    narrate("recipe-scan :: 재파견 상한을 넘긴 두 번째 요청은 무시하고 끝낸다", res)


async def test_조율자_모델_호출이_무너지면_빈_계획으로_강등하고_잡은_성공한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingChat(FakeToolLoopChat):
        def with_structured_output(self, _schema: object, **_kwargs: object) -> object:
            return self

        async def ainvoke(self, _messages: list[object]) -> object:
            raise AuthenticationError(
                "bad key",
                response=httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com")),
                body=None,
            )

    chat = FailingChat([])

    res = await _run(monkeypatch, chat)

    # 재시도 대상이 아닌 실패는 조율자를 빈 계획으로 강등하고 잡은 성공한다.
    assert res.error is None and res.data is not None and res.data["recipes"] == []
    # 계획 단계가 첫 모델 호출이므로 실패도 거기서 궤적에 남는다.
    events = [step.eventKind for step in res.steps if step.nodeName == "survey"]
    assert events == ["node.started", "node.failed"]
    narrate("recipe-scan :: 조율자 모델 호출이 무너지면 빈 계획으로 강등하고 잡은 성공한다", res)


async def test_예산_초과는_조율자를_재시도하지_않고_바로_강등한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BudgetBlownChat(FakeToolLoopChat):
        def with_structured_output(self, _schema: object, **_kwargs: object) -> object:
            return self

        def __init__(self) -> None:
            super().__init__([])
            self.calls = 0

        async def ainvoke(self, _messages: list[object]) -> object:
            self.calls += 1
            raise BudgetExceeded("survey exceeded internal model budget")

    chat = BudgetBlownChat()

    res = await _run(monkeypatch, chat)

    # 재시도했다면 한 번보다 많이 불렸을 것이다.
    assert chat.calls == 1
    assert res.error is None and res.data is not None and res.data["recipes"] == []
    narrate("recipe-scan :: 예산 초과는 조율자를 재시도하지 않고 바로 강등한다", res)


async def test_출력_절단은_조율자를_재시도하지_않고_바로_강등한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TruncatedChat(FakeToolLoopChat):
        def with_structured_output(self, _schema: object, **_kwargs: object) -> object:
            return self

        def __init__(self) -> None:
            super().__init__([])
            self.calls = 0

        async def ainvoke(self, _messages: list[object]) -> object:
            self.calls += 1
            raise OutputTruncated("survey structured output truncated at max_tokens")

    chat = TruncatedChat()

    res = await _run(monkeypatch, chat)

    assert chat.calls == 1
    assert res.error is None and res.data is not None and res.data["recipes"] == []
    narrate("recipe-scan :: 출력 절단은 조율자를 재시도하지 않고 바로 강등한다", res)


async def test_조율자가_세운_계획이_조사_지시문에_반영된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = DispatchPlan(probes=[{"probe": "rules", "weight": 3, "question": "어떤 규칙이 걸렸나"}])  # type: ignore[list-item]
    chat = FakeToolLoopChat([{"recipes": []}], plan=plan)

    res = await _run(monkeypatch, chat)

    assert res.error is None
    sent = " ".join(
        str(getattr(message, "content", message)) for request in chat.requests for message in request
    )
    # 계획이 조사 지시문으로 펴지고 배분한 weight가 그대로 예산 비율이 된다.
    assert "rules (weight 3): 어떤 규칙이 걸렸나" in sent
    assert any("survey -> rules:3" in step.content for step in res.steps)
    narrate("recipe-scan :: 조율자가 세운 계획이 조사 지시문에 반영된다", res)


async def test_계획한_전문가들이_각자_도구만_쥐고_병렬로_돈다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = DispatchPlan(
        probes=[
            {"probe": "timeline", "weight": 4, "question": "무엇을 했나"},  # type: ignore[list-item]
            {"probe": "rules", "weight": 3, "question": "어떤 규칙이"},  # type: ignore[list-item]
        ]
    )
    chat = FakeToolLoopChat([{"recipes": []}], plan=plan)

    res = await _run(monkeypatch, chat)

    assert res.error is None
    # 전문가는 자기 근거 원천의 도구만 쥔다.
    assert sorted(sorted(names) for names in chat.probe_calls) == [
        ["check_citations", "get_task_events", "get_task_summary", "search_events"],
        ["check_citations", "list_rules", "search_recipes"],
    ]
    # 두 전문가가 모두 돌았음이 궤적에 남는다.
    probe_nodes = [step for step in res.steps if step.nodeName == "probe"]
    assert sum(1 for step in probe_nodes if step.eventKind == "node.completed") == 2
    narrate("recipe-scan :: 계획한 전문가들이 각자 도구만 쥐고 병렬로 돈다", res)


async def test_전문가_하나가_무너져도_그래프가_완주하고_나머지가_합쳐진다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OneProbeFails(FakeToolLoopChat):
        async def ainvoke(self, messages: list[object]) -> object:
            names = {getattr(tool, "name", "") for tool in self.bound_tools}
            # RecipeDraft를 쥔 조율자는 걸리지 않아 rules 전문가만 골라 무너진다.
            if "ProbeReport" in names and "list_rules" in names:
                raise RuntimeError("rules probe blew up")
            return await super().ainvoke(messages)

    plan = DispatchPlan(
        probes=[
            {"probe": "timeline", "weight": 4, "question": "무엇을 했나"},  # type: ignore[list-item]
            {"probe": "rules", "weight": 3, "question": "어떤 규칙이"},  # type: ignore[list-item]
        ]
    )
    chat = OneProbeFails([{"recipes": []}], plan=plan)

    res = await _run(monkeypatch, chat)

    # 한 전문가가 예외를 던져도 잡은 실패하지 않고 완주한다.
    assert res.error is None and res.data["recipes"] == []
    probe_nodes = [step for step in res.steps if step.nodeName == "probe"]
    # 두 분기 모두 노드로는 완주하고, 실패로 무너진 분기는 없다.
    assert sum(1 for step in probe_nodes if step.eventKind == "node.completed") == 2
    assert not any(step.eventKind == "node.failed" for step in probe_nodes)
    narrate("recipe-scan :: 전문가 하나가 무너져도 그래프가 완주하고 나머지가 합쳐진다", res)
