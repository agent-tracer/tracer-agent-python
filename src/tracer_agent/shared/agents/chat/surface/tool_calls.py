"""승인된 쓰기 도구 하나가 부를 자리의 인자와 대화에 남길 문장을 도구마다 정한다."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class ChatToolArgsInvalid(ValueError):
    """모델이 낸 인자가 이 도구의 계약을 만족하지 않는다."""


@dataclass(frozen=True)
class ChatToolCall:
    """도구 인자 하나를 실제 호출 인자와 결과 문장으로 옮긴 것이다."""

    args: dict[str, Any]
    describe: Callable[[Any], str]


def plan_chat_tool_call(tool_name: str, args: dict[str, Any]) -> ChatToolCall:
    """승인된 도구 호출 하나의 요청 인자와 결과 문장을 만든다."""
    plan = _PLANS.get(tool_name)
    if plan is None:
        raise ChatToolArgsInvalid(f"No executor for tool {tool_name}")
    return plan(args)


def _plain(args: dict[str, Any], sentence: str) -> ChatToolCall:
    return ChatToolCall(args=args, describe=lambda _data: sentence)


def _req(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ChatToolArgsInvalid(f"{key} is required")
    return value


def _opt(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    return value if isinstance(value, str) and value else None


def _present(**values: str | None) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _object(args: dict[str, Any], key: str) -> dict[str, Any]:
    value = args.get(key)
    if not isinstance(value, dict):
        raise ChatToolArgsInvalid(f"{key} must be an object")
    return value


def _id_list(args: dict[str, Any], key: str) -> list[str]:
    value = args.get(key)
    if not isinstance(value, list):
        raise ChatToolArgsInvalid(f"{key} must be a list")
    return [text for text in (_id_text(one) for one in value) if text]


def _id_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return str(value) if isinstance(value, int | float) and not isinstance(value, bool) else ""


def _reevaluated(data: Any) -> int:
    value = data.get("reevaluated") if isinstance(data, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _job_field(data: Any, field: str) -> str:
    job = data.get("job") if isinstance(data, dict) else None
    value = job.get(field) if isinstance(job, dict) else None
    return value if isinstance(value, str) else ""


def _update_task(args: dict[str, Any]) -> ChatToolCall:
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


def _create_memo(args: dict[str, Any]) -> ChatToolCall:
    task_id = _req(args, "taskId")
    return _plain(
        {"taskId": task_id, "body": _req(args, "body"), **_present(eventId=_opt(args, "eventId"))},
        f"Created a memo on task {task_id}.",
    )


def _create_rule(args: dict[str, Any]) -> ChatToolCall:
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


def _update_rule(args: dict[str, Any]) -> ChatToolCall:
    rule_id = _req(args, "ruleId")
    expectation = args.get("expectation")
    body: dict[str, Any] = _present(
        name=_opt(args, "name"), severity=_opt(args, "severity"), rationale=_opt(args, "rationale")
    )
    if expectation is not None:
        body["expect"] = _object(args, "expectation")
    return _plain({"ruleId": rule_id, **body}, f"Updated rule {rule_id}.")


def _approve_rule(args: dict[str, Any]) -> ChatToolCall:
    rule_id = _req(args, "ruleId")
    return ChatToolCall(
        args={"ruleId": rule_id},
        describe=lambda data: f"Approved rule {rule_id} and reevaluated {_reevaluated(data)} event(s).",
    )


def _reevaluate_rule(args: dict[str, Any]) -> ChatToolCall:
    rule_id = _req(args, "ruleId")
    return ChatToolCall(
        args={"ruleId": rule_id},
        describe=lambda data: f"Reevaluated rule {rule_id} over {_reevaluated(data)} event(s).",
    )


def _create_tag(args: dict[str, Any]) -> ChatToolCall:
    name = _req(args, "name")
    return _plain(
        {"name": name, **_present(color=_opt(args, "color"), description=_opt(args, "description"))},
        f'Created tag "{name}".',
    )


def _update_tag(args: dict[str, Any]) -> ChatToolCall:
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


def _set_task_tags(args: dict[str, Any]) -> ChatToolCall:
    task_id = _req(args, "taskId")
    tag_ids = _id_list(args, "tagIds")
    return _plain({"taskId": task_id, "tagIds": tag_ids}, f"Set {len(tag_ids)} tag(s) on task {task_id}.")


def _enqueue_job(args: dict[str, Any]) -> ChatToolCall:
    kind = _req(args, "kind")
    return ChatToolCall(
        args={"kind": kind, "input": _object(args, "input")},
        describe=lambda data: (
            f"Enqueued {kind} job {_job_field(data, 'id')} (status: {_job_field(data, 'status')})."
        ),
    )


def _one_id(key: str, sentence: str) -> Callable[[dict[str, Any]], ChatToolCall]:
    def plan(args: dict[str, Any]) -> ChatToolCall:
        value = _req(args, key)
        return _plain({key: value}, sentence.format(value))

    return plan


_PLANS: dict[str, Callable[[dict[str, Any]], ChatToolCall]] = {
    "update_task": _update_task,
    "archive_task": _one_id("taskId", "Archived task {}."),
    "unarchive_task": _one_id("taskId", "Unarchived task {}."),
    "delete_task": _one_id("taskId", "Deleted task {}."),
    "create_memo": _create_memo,
    "update_memo": lambda args: _plain(
        {"memoId": _req(args, "memoId"), "body": _req(args, "body")},
        f"Updated memo {_req(args, 'memoId')}.",
    ),
    "delete_memo": _one_id("memoId", "Deleted memo {}."),
    "create_rule": _create_rule,
    "update_rule": _update_rule,
    "delete_rule": _one_id("ruleId", "Deleted rule {}."),
    "approve_rule": _approve_rule,
    "reevaluate_rule": _reevaluate_rule,
    "create_tag": _create_tag,
    "update_tag": _update_tag,
    "delete_tag": _one_id("tagId", "Deleted tag {}."),
    "set_task_tags": _set_task_tags,
    "accept_recipe": _one_id("recipeId", "Accepted recipe {}."),
    "dismiss_recipe": _one_id("recipeId", "Dismissed recipe {}."),
    "retire_recipe": _one_id("recipeId", "Retired recipe {}."),
    "accept_cleanup": _one_id("suggestionId", "Accepted cleanup suggestion {}."),
    "dismiss_cleanup": _one_id("suggestionId", "Dismissed cleanup suggestion {}."),
    "enqueue_job": _enqueue_job,
}

CONFIRMABLE_TOOLS: frozenset[str] = frozenset(_PLANS)
