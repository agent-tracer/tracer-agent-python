"""골든 사례를 대역 모델이나 실제 모델로 실행하고 판정에 필요한 출처를 함께 낸다."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, FakeToolLoopChat
from tests.support.prompts import (
    CONTRACT_VERSION,
    RECIPE_SCAN_PROMPT,
    TASK_CLEANUP_PROMPT,
    TITLE_SUGGESTION_PROMPT,
)
from tracer_agent.shared.agents.recipe_scan.models import RecipeScanRequest
from tracer_agent.shared.agents.shared.models import AgentResponse
from tracer_agent.shared.agents.task_cleanup.models import CleanupCandidate, TaskCleanupRequest
from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionRequest
from tracer_agent.worker.agents.recipe_scan import agent as recipe_mod
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.execution.runner import execute
from tracer_agent.worker.agents.runtime.llm.client import ChatPair
from tracer_agent.worker.agents.task_cleanup import agent as cleanup_mod
from tracer_agent.worker.agents.title_suggestion import agent as title_mod

from .dataset import GoldenCase

# 실제 모델 모드를 켜는 환경변수이며 켜지 않으면 골든 묶음은 대역 모델로만 실행한다.
LIVE_ENV = "TRACER_QUALITY_LIVE_MODEL"
API_KEY_ENV = "ANTHROPIC_API_KEY"

_COMPLETION = {"url": "http://worker:8810/runs/complete", "token": "quality"}
_OFFLINE_KEY = "sk-test"
_DEFAULT_OCCURRED_AT = "2026-07-14T00:00:00Z"

# 사례가 결과 목록을 어느 열쇠에 담는지이며 판정기가 후보 수를 셀 때 읽는다.
RESULT_KEYS = {
    "recipe-scan": "recipes",
    "task-cleanup": "suggestions",
    "title-suggestion": "suggestions",
}


def live_enabled() -> bool:
    """실제 모델 모드가 켜졌고 자격 증명이 있는지 알린다."""
    return os.getenv(LIVE_ENV) == "1" and bool(os.getenv(API_KEY_ENV))


@dataclass(frozen=True)
class CaseSources:
    """사례가 근거로 인정하는 출처의 전부이며 판정기가 이것을 정답으로 쓴다."""

    event_ids_by_task: dict[str, set[str]] = field(default_factory=dict)
    turn_ids_by_task: dict[str, set[str]] = field(default_factory=dict)
    rule_ids: set[str] = field(default_factory=set)
    anchor_task_id: str = ""
    candidates: dict[str, CleanupCandidate] = field(default_factory=dict)
    current_title: str = ""


@dataclass(frozen=True)
class CaseRun:
    """골든 사례 한 건의 실행 결과와 그 실행이 인용해도 되는 출처다."""

    case: GoldenCase
    response: AgentResponse
    sources: CaseSources
    model: str
    # 모델이 실제로 받은 메시지 전문이며 실제 모델 모드에서는 관측하지 않아 비어 있다.
    prompt_text: str = ""


async def run_case(case: GoldenCase, *, live: bool = False) -> CaseRun:
    """사례를 대역 모델이나 실제 모델로 한 번 실행한다."""
    model = case.input.get("model", "claude-sonnet-4-6")
    chat = None if live else _fake_chat(case)
    chats = None if chat is None else ChatPair(chat, None)  # type: ignore[arg-type]
    api_key = os.environ[API_KEY_ENV] if live else _OFFLINE_KEY
    runner = _RUNNERS[case.agent]
    response = await runner(case, model, api_key, chats)
    return CaseRun(
        case=case,
        response=response,
        sources=collect_sources(case),
        model=model,
        prompt_text="" if chat is None else _prompt_text(chat),
    )


def _prompt_text(chat: FakeToolLoopChat) -> str:
    """모델이 받은 모든 메시지 본문을 한 덩어리로 잇는다."""
    return "\n".join(
        str(getattr(message, "content", message)) for request in chat.requests for message in request
    )


def collect_sources(case: GoldenCase) -> CaseSources:
    """사례가 선언한 출처를 판정기가 읽는 모양으로 세운다."""
    events = case.sources.get("events", [])
    rules = case.sources.get("rules", [])
    candidates = [CleanupCandidate.model_validate(item) for item in case.sources.get("candidates", [])]
    event_ids = {str(event["id"]) for event in events}
    turn_ids = {str(event["turnId"]) for event in events if event.get("turnId")}
    if case.agent == "task-cleanup":
        # 대역 창구는 어느 태스크의 타임라인을 물어도 같은 행을 내므로 이벤트가 있는 후보는 모두 같은 출처를 갖는다.
        by_task = {item.id: set(event_ids) for item in candidates if item.hasEvents}
        turns_by_task: dict[str, set[str]] = {}
    else:
        anchor = str(case.input.get("taskId", ""))
        by_task = {anchor: event_ids}
        turns_by_task = {anchor: turn_ids}
    return CaseSources(
        event_ids_by_task=by_task,
        turn_ids_by_task=turns_by_task,
        rule_ids={str(rule["id"]) for rule in rules},
        anchor_task_id=str(case.input.get("taskId", "")),
        candidates={item.id: item for item in candidates},
        current_title=str(case.input.get("context", {}).get("title", "")),
    )


def _fake_chat(case: GoldenCase) -> FakeToolLoopChat:
    script = case.script
    return FakeToolLoopChat(
        script.get("turns", []),
        plan=script.get("plan"),
        report=script.get("report"),
        worker_turns=script.get("workerTurns"),
    )


def _event_row(spec: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": spec["id"],
        "seq": spec.get("seq", 1),
        "kind": spec.get("kind", "execute_tool"),
        "title": spec.get("title", ""),
        "body": None,
        "toolName": None,
        "filePaths": [],
        "metadata": {},
        "occurredAt": spec.get("occurredAt", _DEFAULT_OCCURRED_AT),
    }
    if spec.get("turnId"):
        row["turnId"] = spec["turnId"]
    return row


def _rule_row(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": spec["id"],
        "name": spec.get("name", spec["id"]),
        "expectation": spec.get("expectation", {"kind": "action", "tool": "Bash"}),
        "taskId": spec.get("taskId", "t1"),
        "anchorEventId": spec.get("anchorEventId", "event-1"),
        "source": "agent",
        "severity": "info",
        "rationale": None,
        "signature": f"sig-{spec['id']}",
        "reviewState": "active",
        "createdAt": _DEFAULT_OCCURRED_AT,
    }


def _ledger(case: GoldenCase) -> FakeTracerApi:
    return FakeTracerApi(
        [_event_row(spec) for spec in case.sources.get("events", [])],
        rules=[_rule_row(spec) for spec in case.sources.get("rules", [])],
    )


def _envelope(model: str, api_key: str) -> dict[str, Any]:
    return {
        "model": model,
        "apiKey": api_key,
        "modelRates": WIRE_MODEL_RATES,
        "limits": WIRE_LIMITS,
        "userId": "user-1",
        "completionCallback": _COMPLETION,
    }


async def _run_recipe_scan(
    case: GoldenCase, model: str, api_key: str, chats: ChatPair | None
) -> AgentResponse:
    req = RecipeScanRequest.model_validate(
        {
            **_envelope(model, api_key),
            "taskId": case.input["taskId"],
            "language": case.input.get("language", "ko"),
        }
    )
    ledger = _ledger(case)
    return await execute(
        "recipe-scan",
        req.model,
        req.deadlineMs,
        lambda usage: recipe_mod.run_recipe_scan(req, ledger, usage, RECIPE_SCAN_PROMPT, None, chats),
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )


async def _run_task_cleanup(
    case: GoldenCase, model: str, api_key: str, chats: ChatPair | None
) -> AgentResponse:
    req = TaskCleanupRequest.model_validate(
        {
            **_envelope(model, api_key),
            "scannedAt": case.input.get("scannedAt", _DEFAULT_OCCURRED_AT),
            "maxSuggestions": case.input.get("maxSuggestions", 5),
            "language": case.input.get("language", "ko"),
            "batch": {"candidates": case.sources.get("candidates", []), "batchTruncated": False},
        }
    )
    ledger = _ledger(case)
    return await execute(
        "task-cleanup",
        req.model,
        req.deadlineMs,
        lambda usage: cleanup_mod.run_task_cleanup(req, ledger, usage, TASK_CLEANUP_PROMPT, None, chats),
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )


async def _run_title_suggestion(
    case: GoldenCase, model: str, api_key: str, chats: ChatPair | None
) -> AgentResponse:
    req = TitleSuggestionRequest.model_validate(
        {
            **_envelope(model, api_key),
            "jobId": case.id,
            "taskId": case.input["taskId"],
            "language": case.input.get("language", "ko"),
            "context": case.input["context"],
        }
    )
    ledger = _ledger(case)
    return await execute(
        "title-suggestion",
        req.model,
        req.deadlineMs,
        lambda usage: title_mod.run_title_suggestion(
            req, ledger, usage, TITLE_SUGGESTION_PROMPT, None, chats
        ),
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )


type CaseRunner = Callable[[GoldenCase, str, str, ChatPair | None], Awaitable[AgentResponse]]

_RUNNERS: dict[str, CaseRunner] = {
    "recipe-scan": _run_recipe_scan,
    "task-cleanup": _run_task_cleanup,
    "title-suggestion": _run_title_suggestion,
}
