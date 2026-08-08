"""recipe-scan 도구 계약과 레지스트리 실행과 근거 원장을 검증한다."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from tests.support.agents import mk_recipe_agent
from tests.support.fakes import FakeToolLoopChat
from tests.support.tool_contexts import mk_recipe_context
from tracer_agent.shared.agents.recipe_scan.models import (
    MAX_EXCERPT_CHARS,
    MAX_EXCERPTS_PER_PROBE,
    Excerpt,
    ProbeReport,
    ProvenanceCatalog,
    RecipeDraft,
    merged_provenance,
)
from tracer_agent.worker.agents.recipe_scan.tools import (
    PROBE_TOOLS,
    RECIPE_TOOL_CLASSES,
    RECIPE_TOOLS,
    GetTaskEventsArgs,
    GetTaskEventsTool,
    ListRulesArgs,
    ListRulesTool,
    SearchEventsArgs,
    SearchEventsTool,
    SearchRecipesArgs,
    SearchRecipesTool,
)
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi


def test_Python이_도구_이름_설명_인자스키마를_소유한다() -> None:
    catalog = [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.tool_call_schema.model_json_schema(),
        }
        for tool in RECIPE_TOOLS.langchain_tools()
    ]

    assert [tool["name"] for tool in catalog] == [
        "get_task_summary",
        "get_task_events",
        "list_rules",
        "search_events",
        "find_similar_tasks",
        "search_recipes",
    ]
    assert all(tool["description"] and tool["input_schema"] for tool in catalog)
    search_schema = next(tool["input_schema"] for tool in catalog if tool["name"] == "search_events")
    assert isinstance(search_schema, dict)
    assert "kind" in search_schema["properties"]
    assert search_schema["required"] == ["q"]


def _validated(args: dict[str, object]) -> dict[str, object]:
    """레지스트리가 도구를 부르기 전에 지나는 인자 검증과 같은 자리다."""
    return SearchEventsArgs.model_validate(args).model_dump(exclude_none=True)


def test_taskId_없이도_태스크를_가로질러_검색한다() -> None:
    assert _validated({"q": "failure"}) == {"q": "failure", "limit": 20, "offset": 0}


def test_도구_스키마에_없는_인자는_콜백_전에_거부한다() -> None:
    with pytest.raises(ValidationError):
        _validated({"q": "failure", "drifted": "arg"})


def test_알_수_없는_이벤트_종류는_콜백_전에_거부한다() -> None:
    with pytest.raises(ValidationError):
        _validated({"q": "failure", "kind": "drifted.kind"})


def test_아는_이벤트_종류로_거를_수_있다() -> None:
    validated = _validated({"q": "failure", "kind": "agent_tracer.user.message"})

    assert validated["kind"] == "agent_tracer.user.message"


async def test_모델이_없는_도구를_부르면_거부한다() -> None:
    with pytest.raises(KeyError):
        await RECIPE_TOOLS.invoke("delete_everything", {}, mk_recipe_context())


async def test_유효하지_않은_도구_인자는_실제_조회를_하지_않는다() -> None:
    tracer = FakeTracerApi()

    with pytest.raises(ValidationError):
        await RECIPE_TOOLS.invoke(
            "search_events",
            {"q": "failure", "taskId": "task-1", "kind": "drifted.kind"},
            mk_recipe_context(tracer=tracer),
        )

    assert tracer.calls == []


def test_빈_이벤트_커서는_콜백_전에_거부한다() -> None:
    with pytest.raises(ValidationError):
        GetTaskEventsArgs.model_validate({"taskId": "task-1", "cursor": ""})


def test_revision이_있는_recipe만_수정_근거로_인정한다() -> None:
    catalog = ProvenanceCatalog()
    SearchRecipesTool().record(
        SearchRecipesArgs(q="migration"),
        json.dumps([{"id": "versioned", "rev": 2}, {"id": "boolean", "rev": True}, {"id": "unversioned"}]),
        mk_recipe_context(catalog),
    )

    assert catalog.recipeRevs == {"versioned": 2}


async def test_모델이_생략한_인자는_도구_기본값으로_채워_조회한다() -> None:
    tracer = FakeTracerApi(
        [
            {
                "id": "event-1",
                "seq": "1",
                "kind": "execute_tool",
                "title": "x",
                "filePaths": [],
                "metadata": {},
                "occurredAt": "2026-07-14T00:00:00Z",
            }
        ]
    )

    content = await RECIPE_TOOLS.invoke(
        "get_task_events", {"taskId": "task-1"}, mk_recipe_context(tracer=tracer)
    )

    assert tracer.calls == [
        {
            "path": "/api/v1/tasks/task-1/timeline",
            "params": {"limit": 100, "cursor": None, "order": "asc"},
        }
    ]
    assert "event-1" in content


def test_도구가_돌려준_이벤트만_인용_가능한_근거로_올린다() -> None:
    catalog = ProvenanceCatalog()
    GetTaskEventsTool().record(
        GetTaskEventsArgs(taskId="task-1"),
        '{"events": [{"id": "event-1", "turnId": "turn-1"}]}',
        mk_recipe_context(catalog),
    )

    assert catalog.eventIdsByTask == {"task-1": {"event-1"}}
    assert catalog.turnIdsByTask == {"task-1": {"turn-1"}}


def test_이벤트_근거는_태스크별_원장으로_모으고_불완전한_행은_버린다() -> None:
    catalog = ProvenanceCatalog()
    content = json.dumps(
        {
            "events": [
                {"id": "event-1"},
                {"id": "event-2", "taskId": "related-task"},
                {"id": ""},
                {"taskId": "anchor-task"},
                "not-an-event",
            ]
        }
    )

    SearchEventsTool().record(
        SearchEventsArgs(q="failure", taskId="anchor-task"), content, mk_recipe_context(catalog)
    )

    assert catalog.eventIdsByTask == {
        "anchor-task": {"event-1"},
        "related-task": {"event-2"},
    }


def test_규칙_ID를_근거_원장에_기록한다() -> None:
    catalog = ProvenanceCatalog()
    ListRulesTool().record(
        ListRulesArgs(taskId="task-1"), json.dumps([{"id": "rule-1"}]), mk_recipe_context(catalog)
    )

    assert catalog.ruleIds == {"rule-1"}


async def test_요약이_돌려준_태스크는_근거_원장에_오르지_않는다() -> None:
    catalog = ProvenanceCatalog()

    await RECIPE_TOOLS.invoke("get_task_summary", {"taskId": "task-1"}, mk_recipe_context(catalog))

    assert catalog.eventIdsByTask == {}


async def test_유사_태스크가_돌려준_태스크는_근거_원장에_오르지_않는다() -> None:
    catalog = ProvenanceCatalog()
    tracer = FakeTracerApi(hits={"tasks": [{"id": "task-2", "title": "x", "status": "completed"}]})

    await RECIPE_TOOLS.invoke(
        "find_similar_tasks", {"anchorTaskId": "task-1"}, mk_recipe_context(catalog, tracer=tracer)
    )

    assert catalog.eventIdsByTask == {}


def test_전문가의_장부가_조율자의_장부로_합쳐진다() -> None:
    coordinator = ProvenanceCatalog(
        eventIdsByTask={"task-1": {"event-1"}},
        ruleIds={"rule-1"},
    )
    probe = ProvenanceCatalog(
        eventIdsByTask={"task-1": {"event-2"}, "task-2": {"event-3"}},
        turnIdsByTask={"task-2": {"turn-1"}},
        recipeRevs={"recipe-1": 7},
    )

    coordinator = merged_provenance(coordinator, probe)

    assert coordinator.eventIdsByTask == {"task-1": {"event-1", "event-2"}, "task-2": {"event-3"}}
    assert coordinator.turnIdsByTask == {"task-2": {"turn-1"}}
    assert coordinator.ruleIds == {"rule-1"} and coordinator.recipeRevs == {"recipe-1": 7}


def test_병합된_장부는_인용_확인이_그대로_읽는다() -> None:
    coordinator = ProvenanceCatalog()
    coordinator = merged_provenance(coordinator, ProvenanceCatalog(eventIdsByTask={"task-1": {"event-9"}}))

    # 전문가가 읽은 것을 조율자가 인용해도 되는지 같은 술어로 확인된다.
    assert "event-9" in coordinator.eventIdsByTask["task-1"]


def test_발췌는_상한을_넘으면_거부한다() -> None:
    with pytest.raises(ValidationError):
        Excerpt(taskId="t", eventId="e", text="x" * (MAX_EXCERPT_CHARS + 1))

    with pytest.raises(ValidationError):
        ProbeReport(
            probe="timeline",
            verdict="v",
            excerpts=[
                Excerpt(taskId="t", eventId=f"e{index}", text="x")
                for index in range(MAX_EXCERPTS_PER_PROBE + 1)
            ],
        )


def test_전문가는_자기_근거_원천의_도구만_쥔다() -> None:
    rosters = {probe: set(names) for probe, names in PROBE_TOOLS.items()}

    assert rosters == {
        "timeline": {"get_task_summary", "get_task_events", "search_events"},
        "rules": {"list_rules", "search_recipes"},
        "repetition": {"search_events", "find_similar_tasks"},
    }
    # 세 전문가를 합치면 조율자가 단독으로 쓸 때와 같은 도구 집합이라 계약이 안 바뀐다.
    assert set().union(*rosters.values()) == {cls.name for cls in RECIPE_TOOL_CLASSES}


def test_전문가는_후보가_아니라_보고를_내도록_조립된다() -> None:
    probe = mk_recipe_agent(
        FakeToolLoopChat([]),
        RECIPE_TOOLS.langchain_tools(PROBE_TOOLS["rules"]),
        RECIPE_TOOLS.transient_errors(PROBE_TOOLS["rules"]),
        max_turns=3,
        output=ProbeReport,
    )
    coordinator = mk_recipe_agent(
        FakeToolLoopChat([]),
        RECIPE_TOOLS.langchain_tools(),
        RECIPE_TOOLS.transient_errors(),
        max_turns=15,
        output=RecipeDraft,
    )

    assert probe is not None and coordinator is not None
