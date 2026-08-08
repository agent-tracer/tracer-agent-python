"""승인된 쓰기 도구 하나가 부를 자리의 인자와 대화에 남길 문장을 도구마다 정한다."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ...shared.json_view import JsonObject, JsonValue
from ..tools.bindings import TOOL_ACTION_BINDINGS
from ..tools.surface import CONFIRM_SURFACE, tool_names_on, tool_required_by_action


class ChatToolArgsInvalid(ValueError):
    """모델이 낸 인자가 이 도구의 계약을 만족하지 않는다."""


@dataclass(frozen=True)
class ChatToolCall:
    """도구 인자 하나를 실제 호출 인자와 결과 문장으로 옮긴 것이다."""

    args: JsonObject
    describe: Callable[[JsonValue], str]


def missing_action_arguments(tool_name: str, args: JsonObject) -> tuple[str, ...]:
    """그 action 에 있어야 하는데 빠졌거나 빈 인자를 계약이 적은 순서로 낸다."""
    required = tool_required_by_action(tool_name).get(str(args.get("action", "")), ())
    return tuple(name for name in required if _blank(args.get(name)))


def _blank(value: JsonValue) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return isinstance(value, list) and not value


def plan_chat_tool_call(tool_name: str, args: JsonObject) -> ChatToolCall:
    """승인된 도구 호출 하나의 요청 인자와 결과 문장을 만든다."""
    plan = _PLANS.get(tool_name)
    if plan is None:
        raise ChatToolArgsInvalid(f"No executor for tool {tool_name}")
    return plan(args)


def _plain(args: JsonObject, sentence: str) -> ChatToolCall:
    return ChatToolCall(args=args, describe=lambda _data: sentence)


def _req(args: JsonObject, key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ChatToolArgsInvalid(f"{key} is required")
    return value


def _opt(args: JsonObject, key: str) -> str | None:
    value = args.get(key)
    return value if isinstance(value, str) and value else None


def _present(**values: str | None) -> JsonObject:
    return {key: value for key, value in values.items() if value is not None}


def _object(args: JsonObject, key: str) -> JsonObject:
    value = args.get(key)
    if not isinstance(value, dict):
        raise ChatToolArgsInvalid(f"{key} must be an object")
    return value


def _id_list(args: JsonObject, key: str) -> list[str]:
    value = args.get(key)
    if not isinstance(value, list):
        raise ChatToolArgsInvalid(f"{key} must be a list")
    return [text for text in (_id_text(one) for one in value) if text]


def _id_text(value: JsonValue) -> str:
    if isinstance(value, str):
        return value.strip()
    return str(value) if isinstance(value, int | float) and not isinstance(value, bool) else ""


def _reevaluated(data: JsonValue) -> int:
    value = data.get("reevaluated") if isinstance(data, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _job_field(data: JsonValue, field: str) -> str:
    job = data.get("job") if isinstance(data, dict) else None
    value = job.get(field) if isinstance(job, dict) else None
    return value if isinstance(value, str) else ""


def _update_task(args: JsonObject) -> ChatToolCall:
    task_id = _req(args, "taskId")
    title = _opt(args, "title")
    status = _opt(args, "status")
    changes = [text for text in (_titled(title), _stated(status)) if text]
    if not changes:
        raise ChatToolArgsInvalid("update_task needs title or status")
    return _plain(
        {"taskId": task_id, **_present(title=title, status=status)},
        f"Updated task {task_id}: {', '.join(changes)}.",
    )


def _titled(title: str | None) -> str:
    return "" if title is None else f'title="{title}"'


def _stated(status: str | None) -> str:
    return "" if status is None else f"status={status}"


def _create_memo(args: JsonObject) -> ChatToolCall:
    task_id = _req(args, "taskId")
    return _plain(
        {"taskId": task_id, "body": _req(args, "body"), **_present(eventId=_opt(args, "eventId"))},
        f"Created a memo on task {task_id}.",
    )


def _create_rule(args: JsonObject) -> ChatToolCall:
    task_id = _req(args, "taskId")
    name = _req(args, "name")
    return _plain(
        {
            "taskId": task_id,
            "anchorEventId": _req(args, "anchorEventId"),
            "name": name,
            "expect": _object(args, "expectation"),
            **_present(severity=_opt(args, "severity"), rationale=_opt(args, "rationale")),
        },
        f'Created rule "{name}" on task {task_id}.',
    )


def _update_rule(args: JsonObject) -> ChatToolCall:
    rule_id = _req(args, "ruleId")
    expectation = args.get("expectation")
    body: JsonObject = _present(
        name=_opt(args, "name"), severity=_opt(args, "severity"), rationale=_opt(args, "rationale")
    )
    if expectation is not None:
        body["expect"] = _object(args, "expectation")
    return _plain({"ruleId": rule_id, **body}, f"Updated rule {rule_id}.")


def _approve_rule(args: JsonObject) -> ChatToolCall:
    rule_id = _req(args, "ruleId")
    return ChatToolCall(
        args={"ruleId": rule_id},
        describe=lambda data: f"Approved rule {rule_id} and reevaluated {_reevaluated(data)} event(s).",
    )


def _reevaluate_rule(args: JsonObject) -> ChatToolCall:
    rule_id = _req(args, "ruleId")
    return ChatToolCall(
        args={"ruleId": rule_id},
        describe=lambda data: f"Reevaluated rule {rule_id} over {_reevaluated(data)} event(s).",
    )


def _create_tag(args: JsonObject) -> ChatToolCall:
    name = _req(args, "name")
    return _plain(
        {"name": name, **_present(color=_opt(args, "color"), description=_opt(args, "description"))},
        f'Created tag "{name}".',
    )


def _update_tag(args: JsonObject) -> ChatToolCall:
    tag_id = _req(args, "tagId")
    return _plain(
        {
            "tagId": tag_id,
            **_present(
                name=_opt(args, "name"),
                color=_opt(args, "color"),
                description=_opt(args, "description"),
            ),
        },
        f"Updated tag {tag_id}.",
    )


def _set_task_tags(args: JsonObject) -> ChatToolCall:
    task_id = _req(args, "taskId")
    tag_ids = _id_list(args, "tagIds")
    return _plain(
        {"taskId": task_id, "tagIds": list(tag_ids)},
        f"Set {len(tag_ids)} tag(s) on task {task_id}.",
    )


def _enqueue_job(args: JsonObject) -> ChatToolCall:
    kind = _req(args, "kind")
    return ChatToolCall(
        args={"kind": kind, "input": _object(args, "input")},
        describe=lambda data: (
            f"Enqueued {kind} job {_job_field(data, 'id')} (status: {_job_field(data, 'status')})."
        ),
    )


def _one_id(key: str, sentence: str) -> Callable[[JsonObject], ChatToolCall]:
    def plan(args: JsonObject) -> ChatToolCall:
        value = _req(args, key)
        return _plain({key: value}, sentence.format(value))

    return plan


@dataclass(frozen=True)
class _ActionPlans:
    """action 이 고른 계획을 실행하고 자리를 고르는 쪽이 읽도록 action 을 인자에 남긴다."""

    plans: Mapping[str, Callable[[JsonObject], ChatToolCall]]

    def __call__(self, args: JsonObject) -> ChatToolCall:
        action = _req(args, "action")
        chosen = self.plans.get(action)
        if chosen is None:
            raise ChatToolArgsInvalid(f"{action} is not an action of this tool")
        call = chosen(args)
        return ChatToolCall(args={**call.args, "action": action}, describe=call.describe)


def _by_action(
    plans: dict[str, Callable[[JsonObject], ChatToolCall]],
) -> Callable[[JsonObject], ChatToolCall]:
    """action 마다의 계획을 한 도구의 계획으로 묶는다."""
    return _ActionPlans(plans)


def _remember_fact(args: JsonObject) -> ChatToolCall:
    key = _req(args, "key")
    return _plain({"key": key, "content": _req(args, "content")}, f'Remembered "{key}" about you.')


def _update_memo(args: JsonObject) -> ChatToolCall:
    memo_id = _req(args, "memoId")
    return _plain({"memoId": memo_id, "body": _req(args, "body")}, f"Updated memo {memo_id}.")


_PLANS: dict[str, Callable[[JsonObject], ChatToolCall]] = {
    "remember_fact": _remember_fact,
    "enqueue_job": _enqueue_job,
    "propose_task_write": _by_action(
        {
            "update": _update_task,
            "archive": _one_id("taskId", "Archived task {}."),
            "unarchive": _one_id("taskId", "Unarchived task {}."),
            "delete": _one_id("taskId", "Deleted task {}."),
        }
    ),
    "propose_memo_write": _by_action(
        {
            "create": _create_memo,
            "update": _update_memo,
            "delete": _one_id("memoId", "Deleted memo {}."),
        }
    ),
    "propose_rule_write": _by_action(
        {
            "create": _create_rule,
            "update": _update_rule,
            "delete": _one_id("ruleId", "Deleted rule {}."),
            "approve": _approve_rule,
            "reevaluate": _reevaluate_rule,
        }
    ),
    "propose_tag_write": _by_action(
        {
            "create": _create_tag,
            "update": _update_tag,
            "delete": _one_id("tagId", "Deleted tag {}."),
            "assign": _set_task_tags,
        }
    ),
    "propose_recipe_write": _by_action(
        {
            "accept": _one_id("recipeId", "Accepted recipe {}."),
            "dismiss": _one_id("recipeId", "Dismissed recipe {}."),
            "retire": _one_id("recipeId", "Retired recipe {}."),
        }
    ),
    "propose_cleanup_write": _by_action(
        {
            "accept": _one_id("suggestionId", "Accepted cleanup suggestion {}."),
            "dismiss": _one_id("suggestionId", "Dismissed cleanup suggestion {}."),
        }
    ),
}


def confirmable_tools() -> frozenset[str]:
    """확인 게이트가 여는 도구는 계약의 표면 한 칸이 정하고 계획표가 그것을 빠짐없이 덮어야 한다."""
    declared = frozenset(tool_names_on(CONFIRM_SURFACE))
    planned = frozenset(_PLANS)
    if declared != planned:
        missing = ", ".join(sorted(declared - planned)) or "-"
        extra = ", ".join(sorted(planned - declared)) or "-"
        raise ValueError(f"confirm surface and tool plans disagree: missing={missing} extra={extra}")
    _assert_actions_planned()
    return declared


def planned_actions() -> dict[str, frozenset[str]]:
    """action 으로 자리를 구분하는 도구마다 계획이 갖는 action 이다."""
    return dict(_PLANNED_ACTIONS)


def _assert_actions_planned() -> None:
    """action 으로 자리를 구분하는 도구는 계약이 선언한 action 을 모두 계획으로 갖는다."""
    for name, actions in TOOL_ACTION_BINDINGS.items():
        planned = _PLANNED_ACTIONS.get(name)
        if planned is None:
            raise ValueError(f"{name} binds actions but has no plan")
        unplanned = frozenset(actions) - planned
        if unplanned:
            raise ValueError(f"{name} has no plan for actions: {', '.join(sorted(unplanned))}")


# action 으로 자리를 구분하는 도구가 계획으로 갖는 action 이다.
_PLANNED_ACTIONS: dict[str, frozenset[str]] = {
    name: frozenset(plan.plans) for name, plan in _PLANS.items() if isinstance(plan, _ActionPlans)
}

CONFIRMABLE_TOOLS: frozenset[str] = confirmable_tools()
