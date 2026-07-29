"""chat 도구 표면의 인자 계약을 계약과 같은 값으로 소유하고 인자 모델을 만든다."""

from __future__ import annotations

import keyword
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ConfigDict, Field, create_model

from tracer_agent.shared.agents.shared.models import TrimmedStr

from .arg_descriptions import ARG_DESCRIPTIONS

STATUS_VALUES = ("running", "waiting", "completed", "errored")
SEVERITY_VALUES = ("info", "warn", "block")
SETTING_KEYS = (
    "anthropic.api_key",
    "anthropic.model",
    "ruleGen.maxRulesPerTask",
    "taskCleanup.maxSuggestions",
    "claude.outputLanguage",
)
JOB_KINDS = ("title.suggestion", "recipe.scan", "task.cleanup", "rule.generation")
BACKENDS = ("python", "claude-sdk")


@dataclass(frozen=True)
class NumberArg:
    """수치 인자의 기본값과 상하한이며 실행이 기본값을 채운다."""

    default: int
    minimum: int
    maximum: int


@dataclass(frozen=True)
class EnumArg:
    """열거 인자의 허용값과, 있으면 기본값이다."""

    values: tuple[str, ...]
    default: str | None = None


ArgConstraint = NumberArg | EnumArg


@dataclass(frozen=True)
class ToolSpec:
    """도구 하나의 필수·선택 인자와 mutation 여부와 제약 있는 인자를 소유한다."""

    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    mutation: bool = False
    constraints: dict[str, ArgConstraint] = field(default_factory=dict)


def _limit(default: int, maximum: int) -> dict[str, ArgConstraint]:
    return {"limit": NumberArg(default=default, minimum=1, maximum=maximum)}


TOOL_SPECS: dict[str, ToolSpec] = {
    "search_tasks": ToolSpec(
        optional=("status", "origin", "archived", "root", "parentTaskId", "cursor", "limit"),
        constraints={
            "status": EnumArg(STATUS_VALUES),
            "origin": EnumArg(("user", "server-sdk")),
            "archived": EnumArg(("true", "false")),
            "root": EnumArg(("true", "false")),
            "limit": NumberArg(default=30, minimum=1, maximum=100),
        },
    ),
    "get_task": ToolSpec(required=("taskId",)),
    "get_timeline": ToolSpec(
        required=("taskId",), optional=("cursor", "limit"), constraints=_limit(100, 500)
    ),
    "search_events": ToolSpec(
        optional=("q", "taskId", "kind", "lane", "from", "to", "limit"), constraints=_limit(20, 100)
    ),
    "list_memos": ToolSpec(optional=("taskId", "eventId")),
    "list_rules": ToolSpec(optional=("taskId", "all"), constraints={"all": EnumArg(("true",))}),
    "get_rule_evidence": ToolSpec(required=("ruleId",), optional=("taskId",)),
    "list_tags": ToolSpec(),
    "list_recipes": ToolSpec(
        optional=("status",),
        constraints={"status": EnumArg(("candidate", "active", "dismissed", "superseded", "retired"))},
    ),
    "list_cleanup_suggestions": ToolSpec(
        optional=("status",),
        constraints={"status": EnumArg(("pending", "accepted", "dismissed"))},
    ),
    "get_job": ToolSpec(required=("jobId",)),
    "list_settings": ToolSpec(),
    "recall_facts": ToolSpec(),
    "remember_fact": ToolSpec(required=("key", "content")),
    "update_task": ToolSpec(
        required=("taskId",),
        optional=("title", "status"),
        mutation=True,
        constraints={"status": EnumArg(STATUS_VALUES)},
    ),
    "archive_task": ToolSpec(required=("taskId",), mutation=True),
    "unarchive_task": ToolSpec(required=("taskId",), mutation=True),
    "delete_task": ToolSpec(required=("taskId",), mutation=True),
    "create_memo": ToolSpec(required=("taskId", "body"), optional=("eventId",), mutation=True),
    "update_memo": ToolSpec(required=("memoId", "body"), mutation=True),
    "delete_memo": ToolSpec(required=("memoId",), mutation=True),
    "create_rule": ToolSpec(
        required=("taskId", "anchorEventId", "name", "expectation"),
        optional=("severity", "rationale"),
        mutation=True,
        constraints={"severity": EnumArg(SEVERITY_VALUES)},
    ),
    "update_rule": ToolSpec(
        required=("ruleId",),
        optional=("name", "expectation", "severity", "rationale"),
        mutation=True,
        constraints={"severity": EnumArg(SEVERITY_VALUES)},
    ),
    "delete_rule": ToolSpec(required=("ruleId",), mutation=True),
    "approve_rule": ToolSpec(required=("ruleId",), mutation=True),
    "reevaluate_rule": ToolSpec(required=("ruleId",), mutation=True),
    "create_tag": ToolSpec(required=("name",), optional=("color", "description"), mutation=True),
    "update_tag": ToolSpec(required=("tagId",), optional=("name", "color", "description"), mutation=True),
    "delete_tag": ToolSpec(required=("tagId",), mutation=True),
    "set_task_tags": ToolSpec(required=("taskId", "tagIds"), mutation=True),
    "accept_recipe": ToolSpec(required=("recipeId",), mutation=True),
    "dismiss_recipe": ToolSpec(required=("recipeId",), mutation=True),
    "retire_recipe": ToolSpec(required=("recipeId",), mutation=True),
    "accept_cleanup": ToolSpec(required=("suggestionId",), mutation=True),
    "dismiss_cleanup": ToolSpec(required=("suggestionId",), mutation=True),
    "upsert_setting": ToolSpec(
        required=("key", "value"), mutation=True, constraints={"key": EnumArg(SETTING_KEYS)}
    ),
    "delete_setting": ToolSpec(required=("key",), mutation=True, constraints={"key": EnumArg(SETTING_KEYS)}),
    "enqueue_job": ToolSpec(
        required=("kind", "input"),
        optional=("agentBackend",),
        mutation=True,
        constraints={"kind": EnumArg(JOB_KINDS), "agentBackend": EnumArg(BACKENDS)},
    ),
}

MEMORY_TOOL_NAMES: tuple[str, ...] = ("recall_facts", "remember_fact")
READ_TOOL_NAMES: tuple[str, ...] = tuple(
    name for name, spec in TOOL_SPECS.items() if not spec.mutation and name not in MEMORY_TOOL_NAMES
)
WRITE_TOOL_NAMES: tuple[str, ...] = tuple(name for name, spec in TOOL_SPECS.items() if spec.mutation)


def _enum_annotation(values: tuple[str, ...]) -> Any:
    # 런타임 튜플에서 만든 Literal이라 정적으로는 표현할 수 없어 Any로 넘긴다.
    return Literal[values]


def _field(
    constraint: ArgConstraint | None, *, required: bool, alias: str | None, described: str
) -> tuple[Any, Any]:
    if isinstance(constraint, EnumArg):
        annotation = _enum_annotation(constraint.values)
        if required:
            return annotation, Field(alias=alias, description=described)
        return annotation | None, Field(default=None, alias=alias, description=described)
    if isinstance(constraint, NumberArg):
        # 계약 default는 실행이 채우므로 스키마에는 상하한만 두고 생략을 허용한다.
        return int | None, Field(
            default=None,
            ge=constraint.minimum,
            le=constraint.maximum,
            alias=alias,
            description=described,
        )
    if required:
        return TrimmedStr, Field(min_length=1, alias=alias, description=described)
    return TrimmedStr | None, Field(default=None, min_length=1, alias=alias, description=described)


def build_args_model(name: str, spec: ToolSpec) -> type[Any]:
    """도구 하나의 필수·선택 인자를 계약대로 갖는 Pydantic 인자 모델을 만든다."""
    fields: dict[str, Any] = {}
    described = ARG_DESCRIPTIONS[name]
    for arg in (*spec.required, *spec.optional):
        # from 같은 파이썬 예약어는 별칭으로 와이어 이름을 유지하고 안전한 필드명으로 담는다.
        key = f"{arg}_" if keyword.iskeyword(arg) else arg
        alias = arg if key != arg else None
        fields[key] = _field(
            spec.constraints.get(arg),
            required=arg in spec.required,
            alias=alias,
            described=described[arg],
        )
    model_name = "".join(part.capitalize() for part in name.split("_")) + "Args"
    model: type[Any] = create_model(
        model_name, __config__=ConfigDict(extra="forbid", populate_by_name=True), **fields
    )
    return model


ARGS_MODELS: dict[str, type[Any]] = {name: build_args_model(name, spec) for name, spec in TOOL_SPECS.items()}
