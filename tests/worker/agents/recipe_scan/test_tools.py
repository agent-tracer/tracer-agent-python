"""recipe-scan 도구 계약과 레지스트리 실행과 근거 원장을 검증한다."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tests.support.fakes import FakeLedger, FakeSearch, FakeToolLoopChat
from tracer_agent.shared.agents.recipe_scan.models import (
    MAX_EXCERPT_CHARS,
    MAX_EXCERPTS_PER_PROBE,
    Excerpt,
    ProbeReport,
    ProvenanceCatalog,
    RecipeDraft,
    merged_provenance,
)
from tracer_agent.worker.agents.recipe_scan.langchain_agent import build_recipe_agent
from tracer_agent.worker.agents.recipe_scan.reader import RecipeLedgerReader
from tracer_agent.worker.agents.recipe_scan.search import RecipeSearchReader
from tracer_agent.worker.agents.recipe_scan.tools import (
    PROBE_TOOLS,
    RECIPE_TOOL_CLASSES,
    GetTaskEventsArgs,
    GetTaskEventsTool,
    ListRulesArgs,
    ListRulesTool,
    SearchEventsArgs,
    SearchEventsTool,
    SearchRecipesArgs,
    SearchRecipesTool,
    build_recipe_registry,
    validate_tool_args,
)
from tracer_agent.worker.agents.runtime.tooling import ToolRegistry


def _registry(
    catalog: ProvenanceCatalog | None = None,
    *,
    ledger: FakeLedger | None = None,
    search: FakeSearch | None = None,
) -> ToolRegistry:
    return build_recipe_registry(
        RecipeLedgerReader(ledger or FakeLedger(), "user-1"),  # type: ignore[arg-type]
        RecipeSearchReader(search or FakeSearch(), "user-1"),  # type: ignore[arg-type]
        catalog or ProvenanceCatalog(),
        agent_name="recipe-scan",
    )


def _reader() -> RecipeLedgerReader:
    return RecipeLedgerReader(FakeLedger(), "user-1")  # type: ignore[arg-type]


def _search() -> RecipeSearchReader:
    return RecipeSearchReader(FakeSearch(), "user-1")  # type: ignore[arg-type]


def test_Python이_도구_이름_설명_인자스키마를_소유한다() -> None:
    catalog = [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.tool_call_schema.model_json_schema(),
        }
        for tool in _registry().langchain_tools()
    ]

    assert [tool["name"] for tool in catalog] == [
        "get_task_summary",
        "get_task_events",
        "list_rules",
        "search_events",
        "find_similar_tasks",
        "search_recipes",
        "check_citations",
    ]
    assert all(tool["description"] and tool["input_schema"] for tool in catalog)
    search_schema = next(tool["input_schema"] for tool in catalog if tool["name"] == "search_events")
    assert isinstance(search_schema, dict)
    assert "kind" in search_schema["properties"]
    assert search_schema["required"] == ["q"]


def test_taskId_없이도_태스크를_가로질러_검색한다() -> None:
    assert validate_tool_args("search_events", {"q": "failure"}) == {
        "q": "failure",
        "limit": 20,
        "offset": 0,
    }


def test_도구_스키마에_없는_인자는_콜백_전에_거부한다() -> None:
    with pytest.raises(ValidationError):
        validate_tool_args("search_events", {"q": "failure", "drifted": "arg"})


def test_알_수_없는_이벤트_종류는_콜백_전에_거부한다() -> None:
    with pytest.raises(ValidationError):
        validate_tool_args("search_events", {"q": "failure", "kind": "drifted.kind"})


def test_아는_이벤트_종류로_거를_수_있다() -> None:
    validated = validate_tool_args(
        "search_events",
        {"q": "failure", "kind": "agent_tracer.user.message"},
    )

    assert validated["kind"] == "agent_tracer.user.message"


def test_모델이_없는_도구를_부르면_거부한다() -> None:
    with pytest.raises(ValueError, match="unknown recipe-scan tool"):
        validate_tool_args("delete_everything", {})


async def test_유효하지_않은_도구_인자는_실제_조회를_하지_않는다() -> None:
    ledger = FakeLedger()
    search = FakeSearch()

    with pytest.raises(ValidationError):
        await _registry(ledger=ledger, search=search).invoke(
            "search_events",
            {"q": "failure", "taskId": "task-1", "kind": "drifted.kind"},
        )

    assert ledger.queries == [] and search.bodies == []


def test_빈_이벤트_커서는_콜백_전에_거부한다() -> None:
    with pytest.raises(ValidationError):
        validate_tool_args("get_task_events", {"taskId": "task-1", "cursor": ""})


def test_revision이_있는_recipe만_수정_근거로_인정한다() -> None:
    catalog = ProvenanceCatalog()
    tool = SearchRecipesTool(_search(), catalog)

    tool.record(
        SearchRecipesArgs(q="migration"),
        json.dumps([{"id": "versioned", "rev": 2}, {"id": "boolean", "rev": True}, {"id": "unversioned"}]),
    )

    assert catalog.recipeRevs == {"versioned": 2}


async def test_모델이_생략한_인자는_도구_기본값으로_채워_조회한다() -> None:
    ledger = FakeLedger(
        [
            {
                "id": "event-1",
                "seq": 1,
                "turn_id": None,
                "kind": "execute_tool",
                "title": "x",
                "body": None,
                "tool_name": None,
                "file_paths": [],
                "metadata": {},
                "occurred_at": datetime(2026, 7, 14, tzinfo=UTC),
            }
        ]
    )

    content = await _registry(ledger=ledger).invoke("get_task_events", {"taskId": "task-1"})

    # 기본 limit 100에 truncated 판별용 한 행을 더해 101을 읽는다.
    assert ledger.queries == [{"desc": False, "args": ["task-1", "user-1", None, 101]}]
    assert "event-1" in content


def test_도구가_돌려준_이벤트만_인용_가능한_근거로_올린다() -> None:
    catalog = ProvenanceCatalog()
    tool = GetTaskEventsTool(_reader(), catalog)

    tool.record(
        GetTaskEventsArgs(taskId="task-1"),
        '{"events": [{"id": "event-1", "turnId": "turn-1"}]}',
    )

    assert catalog.eventIdsByTask == {"task-1": {"event-1"}}
    assert catalog.turnIdsByTask == {"task-1": {"turn-1"}}


def test_이벤트_근거는_태스크별_원장으로_모으고_불완전한_행은_버린다() -> None:
    catalog = ProvenanceCatalog()
    tool = SearchEventsTool(_search(), catalog)
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

    tool.record(SearchEventsArgs(q="failure", taskId="anchor-task"), content)

    assert catalog.eventIdsByTask == {
        "anchor-task": {"event-1"},
        "related-task": {"event-2"},
    }


def test_규칙_ID를_근거_원장에_기록한다() -> None:
    catalog = ProvenanceCatalog()
    tool = ListRulesTool(_reader(), catalog)

    tool.record(ListRulesArgs(taskId="task-1"), json.dumps([{"id": "rule-1"}]))

    assert catalog.ruleIds == {"rule-1"}


async def test_요약이_돌려준_태스크는_근거_원장에_오르지_않는다() -> None:
    catalog = ProvenanceCatalog()

    await _registry(catalog).invoke("get_task_summary", {"taskId": "task-1"})

    assert catalog.eventIdsByTask == {}


async def test_유사_태스크가_돌려준_태스크는_근거_원장에_오르지_않는다() -> None:
    catalog = ProvenanceCatalog()
    search = FakeSearch({"tasks": [{"_id": "task-2", "_source": {"title": "x"}}]})

    await _registry(catalog, search=search).invoke("find_similar_tasks", {"anchorTaskId": "task-1"})

    assert catalog.eventIdsByTask == {}


async def test_인용_확인은_장부에_없는_식별자를_짚어준다() -> None:
    catalog = ProvenanceCatalog(
        eventIdsByTask={"task-1": {"event-1"}},
        turnIdsByTask={"task-1": {"turn-1"}},
        ruleIds={"rule-1"},
    )

    content = await _registry(catalog).invoke(
        "check_citations",
        {
            "taskId": "task-1",
            "eventIds": ["event-1", "event-9"],
            "turnIds": ["turn-9"],
            "ruleIds": ["rule-1"],
        },
    )

    assert json.loads(content) == {
        "taskSupported": True,
        "unsupportedEventIds": ["event-9"],
        "unsupportedTurnIds": ["turn-9"],
        "unsupportedRuleIds": [],
    }


async def test_인용_확인은_읽지_않은_태스크를_알려준다() -> None:
    content = await _registry(ProvenanceCatalog()).invoke(
        "check_citations",
        {"taskId": "task-9", "eventIds": ["event-1"]},
    )

    assert json.loads(content)["taskSupported"] is False


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
        "timeline": {"get_task_summary", "get_task_events", "search_events", "check_citations"},
        "rules": {"list_rules", "search_recipes", "check_citations"},
        "repetition": {"search_events", "find_similar_tasks", "check_citations"},
    }
    # 세 전문가를 합치면 조율자가 혼자 쓸 때와 같은 도구 집합이라 계약이 안 바뀐다.
    assert set().union(*rosters.values()) == {cls.name for cls in RECIPE_TOOL_CLASSES}


def test_전문가는_후보가_아니라_보고를_내도록_조립된다() -> None:
    probe_registry = build_recipe_registry(
        _reader(), _search(), ProvenanceCatalog(), PROBE_TOOLS["rules"], agent_name="recipe-scan"
    )
    probe = build_recipe_agent(
        FakeToolLoopChat([]),
        "system",
        probe_registry.langchain_tools(),
        probe_registry.transient_errors(),
        max_turns=3,
        output=ProbeReport,
    )
    coordinator_registry = build_recipe_registry(
        _reader(), _search(), ProvenanceCatalog(), agent_name="recipe-scan"
    )
    coordinator = build_recipe_agent(
        FakeToolLoopChat([]),
        "system",
        coordinator_registry.langchain_tools(),
        coordinator_registry.transient_errors(),
        max_turns=15,
        output=RecipeDraft,
    )

    assert probe is not None and coordinator is not None
