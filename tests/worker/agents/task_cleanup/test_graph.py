"""task-cleanup 도구 루프와 결정론 검증을 확인한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tests.support.contract import conformance_case
from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, FakeToolLoopChat
from tests.support.narrate import narrate
from tests.support.prompts import CONTRACT_VERSION, TASK_CLEANUP_PROMPT
from tracer_agent.shared.agents.shared.models import AgentResponse
from tracer_agent.shared.agents.task_cleanup.models import CleanupResult, TaskCleanupRequest
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.execution.runner import ExecutionRequest, execute
from tracer_agent.worker.agents.runtime.llm.client import ChatPair
from tracer_agent.worker.agents.task_cleanup import agent as cleanup_mod

_COMPLETION = {"url": "http://worker:8810/runs/complete", "token": "done-1"}


def _request(*candidates: dict[str, object]) -> TaskCleanupRequest:
    return TaskCleanupRequest(
        model="claude-sonnet-4-6",
        apiKey="sk-test",
        modelRates=WIRE_MODEL_RATES,
        limits=WIRE_LIMITS,
        scannedAt="2026-07-14T00:00:00Z",
        userId="user-1",
        maxSuggestions=5,
        language="ko",
        batch={"candidates": list(candidates), "batchTruncated": False},  # type: ignore[arg-type]
        completionCallback=_COMPLETION,  # type: ignore[arg-type]
    )


def _candidate(task_id: str, *, has_events: bool) -> dict[str, object]:
    return {
        "id": task_id,
        "visibleTitle": f"제목 {task_id}",
        "status": "running",
        "lastEventAt": None,
        "hasEvents": has_events,
        "activeChildCount": 0,
        "candidateReasons": ["stale"],
    }


def _event_rows(*event_ids: str) -> list[dict[str, Any]]:
    return [
        {
            "id": event_id,
            "seq": 1,
            "kind": "execute_tool",
            "title": "무의미한 활동",
            "body": None,
            "toolName": None,
            "filePaths": [],
            "occurredAt": datetime(2026, 7, 14, tzinfo=UTC).isoformat(),
        }
        for event_id in event_ids
    ]


def _triage(*assignments: dict[str, object]) -> dict[str, list[Any]]:
    """선별자는 요청이 실어 준 후보 목록만 보고 검토자를 배정한다."""
    return {"Candidates in this batch": [{"inspect": list(assignments)}]}


def _reviewer(task_id: str, report: dict[str, object], *, read: bool = True) -> dict[str, list[Any]]:
    """검토자가 후보 하나의 이벤트를 읽고 판정을 올리는 대본 한 조각을 만든다."""
    turns: list[Any] = []
    if read:
        turns.append([{"name": "get_task_events", "args": {"taskId": task_id}}])
    turns.append(report)
    return {f"Task to judge: {task_id}": turns}


async def _run(
    chat: FakeToolLoopChat, ledger: FakeTracerApi, *candidates: dict[str, object]
) -> AgentResponse:
    req = _request(*candidates)
    chats = ChatPair(chat, None)  # type: ignore[arg-type]
    return await execute(
        ExecutionRequest(
            label="task-cleanup",
            model=req.model,
            deadline_ms=req.deadlineMs,
            prompt_version=CONTRACT_VERSION,
            tool_contract_version=CONTRACT_VERSION,
        ),
        lambda usage: cleanup_mod.TASK_CLEANUP_JOB.run(req, ledger, usage, TASK_CLEANUP_PROMPT, None, chats),  # type: ignore[arg-type],
    )


async def test_검토자가_읽은_후보와_빈_껍데기를_조율자가_함께_제안한다() -> None:
    ledger = FakeTracerApi(_event_rows("event-1"))
    candidates = [_candidate("task-1", has_events=True), _candidate("task-2", has_events=False)]
    worker_turns = {
        **_triage({"taskId": "task-1", "depth": "normal"}),
        **_reviewer(
            "task-1",
            {
                "taskId": "task-1",
                "archivable": True,
                "reason": "의미 있는 작업이 없다",
                "citedEventIds": ["event-1"],
            },
        ),
    }
    chat = FakeToolLoopChat(
        [
            {
                "suggestions": [
                    {
                        "kind": "archive",
                        "taskId": "task-1",
                        "rationale": "의미 있는 작업이 없다",
                        "evidenceEventIds": ["event-1"],
                    },
                    {
                        "kind": "archive",
                        "taskId": "task-2",
                        "rationale": "빈 껍데기다",
                        "evidenceEventIds": [],
                    },
                ]
            }
        ],
        worker_turns=worker_turns,
    )

    res = await _run(chat, ledger, *candidates)

    assert res.error is None
    # 선별자가 고른 후보 하나만 검토자가 그 태스크의 타임라인 창구로 조회한다.
    assert [call["path"] for call in ledger.calls] == ["/api/v1/tasks/task-1/timeline"]
    assert res.data["suggestions"] == [
        {
            "kind": "archive",
            "taskId": "task-1",
            "rationale": "의미 있는 작업이 없다",
            "evidenceEventIds": ["event-1"],
        },
        {"kind": "archive", "taskId": "task-2", "rationale": "빈 껍데기다", "evidenceEventIds": []},
    ]
    narrate("task-cleanup :: 검토자가 읽은 후보와 노출된 빈 껍데기를 조율자가 함께 제안한다", res)


async def test_선별자가_노출하지_않은_후보는_버린다() -> None:
    ledger = FakeTracerApi()
    candidates = [_candidate("task-1", has_events=False)]
    worker_turns = {
        **_triage({"taskId": "task-1", "depth": "shallow"}),
        **_reviewer(
            "task-1",
            {"taskId": "task-1", "archivable": True, "reason": "빈 껍데기다", "citedEventIds": []},
            read=False,
        ),
    }
    chat = FakeToolLoopChat(
        [
            {
                "suggestions": [
                    {
                        "kind": "archive",
                        "taskId": "ghost",
                        "rationale": "없는 태스크",
                        "evidenceEventIds": [],
                    }
                ]
            },
            {"suggestions": []},
        ],
        worker_turns=worker_turns,
    )

    res = await _run(chat, ledger, *candidates)

    assert res.error is None and res.data == {"suggestions": [], "tasksScanned": 0}
    failures = [step for step in res.steps if step.eventKind == "validation.failed"]
    assert failures and "ghost" in failures[0].content
    narrate("task-cleanup :: 선별자가 노출한 적 없는 후보 제안은 검증에서 버려진다", res)


async def test_검토자가_읽지_않은_이벤트_후보는_제안으로_받지_않는다() -> None:
    ledger = FakeTracerApi()
    candidates = [_candidate("task-1", has_events=True), _candidate("task-2", has_events=False)]
    # 선별자는 이벤트가 없는 task-2만 배정하고, 조율자가 아무도 읽지 않은 task-1을 근거 없이 제안한다.
    worker_turns = {
        **_triage({"taskId": "task-2", "depth": "shallow"}),
        **_reviewer(
            "task-2",
            {"taskId": "task-2", "archivable": True, "reason": "빈 껍데기다", "citedEventIds": []},
            read=False,
        ),
    }
    chat = FakeToolLoopChat(
        [
            {
                "suggestions": [
                    {
                        "kind": "archive",
                        "taskId": "task-1",
                        "rationale": "근거 없이 제안",
                        "evidenceEventIds": [],
                    }
                ]
            },
            {"suggestions": []},
        ],
        worker_turns=worker_turns,
    )

    res = await _run(chat, ledger, *candidates)

    assert res.error is None and res.data == {"suggestions": [], "tasksScanned": 0}
    failures = [step for step in res.steps if step.eventKind == "validation.failed"]
    assert failures and "was never inspected" in failures[0].content
    narrate("task-cleanup :: 검토자가 읽지 않은 이벤트 후보는 조율자가 제안해도 버려진다", res)


async def test_검토자가_읽었더니_이벤트가_없는_후보는_인용_없이도_받는다() -> None:
    ledger = FakeTracerApi()
    candidates = [_candidate("task-1", has_events=True)]
    worker_turns = {
        **_triage({"taskId": "task-1", "depth": "shallow"}),
        **_reviewer(
            "task-1",
            {"taskId": "task-1", "archivable": True, "reason": "알맹이가 없다", "citedEventIds": []},
        ),
    }
    chat = FakeToolLoopChat(
        [
            {
                "suggestions": [
                    {
                        "kind": "archive",
                        "taskId": "task-1",
                        "rationale": "알맹이가 없다",
                        "evidenceEventIds": [],
                    }
                ]
            }
        ],
        worker_turns=worker_turns,
    )

    res = await _run(chat, ledger, *candidates)

    assert res.error is None
    assert [item["taskId"] for item in res.data["suggestions"]] == ["task-1"]
    narrate("task-cleanup :: 검토자가 읽었더니 이벤트가 없던 후보는 인용 없이도 제안으로 받는다", res)


async def test_아무_도구도_부르지_않으면_빈_결과로_끝낸다() -> None:
    ledger = FakeTracerApi()
    candidates: list[dict[str, object]] = []
    chat = FakeToolLoopChat([{"suggestions": []}])

    res = await _run(chat, ledger, *candidates)

    assert res.error is None and res.data == {"suggestions": [], "tasksScanned": 0}
    narrate("task-cleanup :: 도구를 한 번도 부르지 않으면 빈 결과로 끝난다", res)


async def test_조율자가_재파견을_요청하면_후보를_한_번_더_열어보고_완주한다() -> None:
    ledger = FakeTracerApi(_event_rows("event-1"))
    candidates = [_candidate("task-1", has_events=True), _candidate("task-2", has_events=True)]
    archivable = {"archivable": True, "reason": "의미 있는 활동이 없다", "citedEventIds": ["event-1"]}
    worker_turns = {
        **_triage({"taskId": "task-1", "depth": "normal"}),
        **_reviewer("task-1", {"taskId": "task-1", **archivable}),
        **_reviewer("task-2", {"taskId": "task-2", **archivable}),
    }
    chat = FakeToolLoopChat(
        [
            {"suggestions": [], "redispatch": [{"taskId": "task-2", "depth": "shallow"}]},
            {
                "suggestions": [
                    {
                        "kind": "archive",
                        "taskId": "task-1",
                        "rationale": "빈 작업",
                        "evidenceEventIds": ["event-1"],
                    },
                    {
                        "kind": "archive",
                        "taskId": "task-2",
                        "rationale": "빈 작업",
                        "evidenceEventIds": ["event-1"],
                    },
                ]
            },
        ],
        worker_turns=worker_turns,
    )

    res = await _run(chat, ledger, *candidates)

    assert res.error is None
    assert [item["taskId"] for item in res.data["suggestions"]] == ["task-1", "task-2"]
    # 조율자가 두 번 종합했고 그 사이 후보 하나를 더 조회하게 재파견한다.
    completed = [
        step for step in res.steps if step.nodeName == "investigate" and step.eventKind == "node.completed"
    ]
    assert len(completed) == 2
    assert any("redispatch task-2:shallow" in step.content for step in res.steps)
    inspected = [
        step for step in res.steps if step.nodeName == "inspect" and step.eventKind == "node.completed"
    ]
    assert len(inspected) == 2
    narrate("task-cleanup :: 조율자가 재파견을 요청하면 후보를 한 번 더 열어보고 완주한다", res)


async def test_후보_하나가_무너져도_그래프가_완주하고_나머지가_합쳐진다() -> None:
    class OneInspectFails(FakeToolLoopChat):
        async def ainvoke(self, messages: list[Any]) -> Any:
            names = {getattr(tool, "name", "") for tool in self.bound_tools}
            text = " ".join(str(getattr(message, "content", message)) for message in messages)
            # CleanupDraft를 내는 조율자는 걸리지 않아 task-2 조사만 골라 실패한다.
            if "InspectReport" in names and "task-2" in text:
                raise RuntimeError("inspect blew up")
            return await super().ainvoke(messages)

    plan = {"inspect": [{"taskId": "task-1", "depth": "normal"}, {"taskId": "task-2", "depth": "normal"}]}
    chat = OneInspectFails([{"suggestions": []}], report={"TriagePlan": plan})

    res = await _run(
        chat,
        FakeTracerApi(),
        _candidate("task-1", has_events=True),
        _candidate("task-2", has_events=True),
    )

    # 한 후보 조사가 예외를 던져도 잡은 실패하지 않고 끝까지 실행된다.
    assert res.error is None and res.data == {"suggestions": [], "tasksScanned": 0}
    inspected = [step for step in res.steps if step.nodeName == "inspect"]
    assert sum(1 for step in inspected if step.eventKind == "node.completed") == 2
    assert not any(step.eventKind == "node.failed" for step in inspected)
    narrate("task-cleanup :: 후보 하나의 조사가 무너져도 그래프는 완주하고 나머지 보고가 합쳐진다", res)


async def test_고른_후보만_각자_예산으로_병렬_조사된다() -> None:
    plan = {"inspect": [{"taskId": "task-1", "depth": "normal"}, {"taskId": "task-2", "depth": "normal"}]}
    chat = FakeToolLoopChat([{"suggestions": []}], report={"TriagePlan": plan})

    res = await _run(
        chat,
        FakeTracerApi(),
        _candidate("task-1", has_events=True),
        _candidate("task-2", has_events=True),
    )

    assert res.error is None
    inspected = [step for step in res.steps if step.nodeName == "inspect"]
    assert sum(1 for step in inspected if step.eventKind == "node.completed") == 2
    narrate("task-cleanup :: 조율자가 고른 후보만 각자 예산으로 병렬 조사된다", res)


def test_산출이_계약이_적은_칸을_빠짐없이_싣는다() -> None:
    # 화면이 축을 보지 않고 같은 칸을 읽어야 하므로 실을 칸을 계약에서 읽어 대조한다.
    declared = conformance_case("job.intake")["results"]["byKind"]["task.cleanup"]

    result = CleanupResult(suggestions=[], tasksScanned=3)

    assert sorted(result.model_dump(mode="json")) == sorted(declared["required"])


async def test_배치_밖_태스크를_배정한_계획은_그_배정을_파견하지_않는다() -> None:
    ledger = FakeTracerApi()
    candidates = [_candidate("task-1", has_events=False)]
    # 조율자가 배치에 없는 outsider 를 함께 배정하지만 그 배정은 파견되지 않는다.
    worker_turns = {
        **_triage(
            {"taskId": "task-1", "depth": "shallow"},
            {"taskId": "outsider", "depth": "deep"},
        ),
        **_reviewer(
            "task-1",
            {"taskId": "task-1", "archivable": True, "reason": "빈 껍데기다", "citedEventIds": []},
            read=False,
        ),
    }
    chat = FakeToolLoopChat(
        [
            {
                "suggestions": [
                    {
                        "kind": "archive",
                        "taskId": "task-1",
                        "rationale": "생성 뒤 이벤트가 없다",
                        "evidenceEventIds": [],
                    }
                ]
            }
        ],
        worker_turns=worker_turns,
    )

    res = await _run(chat, ledger, *candidates)

    assert res.error is None
    assert res.data == {
        "suggestions": [
            {
                "kind": "archive",
                "taskId": "task-1",
                "rationale": "생성 뒤 이벤트가 없다",
                "evidenceEventIds": [],
            }
        ],
        "tasksScanned": 0,
    }
    routes = [step for step in res.steps if step.eventKind == "route.selected"]
    dropped = [step for step in routes if "dropped out of batch" in step.content]
    chosen = [step for step in routes if step.content.startswith("triage -> ")]
    assert dropped and "outsider" in dropped[0].content
    assert chosen and "outsider" not in chosen[0].content
    narrate("task-cleanup :: 배치 밖 배정은 파견에서 빠지고 그 사실이 궤적에 남는다", res)
