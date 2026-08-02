"""chat 도구가 어느 REST API의 뷰인지를 계약과 같은 값으로 소유한다."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import quote

_PLACEHOLDER = re.compile(r"\{([^}]+)\}")


@dataclass(frozen=True)
class ToolBinding:
    """도구 하나가 되읽거나 부를 API 한 자리이며, 인자가 경로·쿼리·본문 중 어디로 가는지를 담는다."""

    method: str
    path: str
    path_args: tuple[str, ...] = ()
    query: Mapping[str, str] = field(default_factory=dict)
    body: Mapping[str, str] = field(default_factory=dict)
    body_constants: Mapping[str, str] = field(default_factory=dict)

    def placeholders(self) -> tuple[str, ...]:
        """경로 틀에 적힌 자리 이름을 순서대로 낸다."""
        return tuple(_PLACEHOLDER.findall(self.path))


def _get(path: str, *, path_args: tuple[str, ...] = (), query: tuple[str, ...] = ()) -> ToolBinding:
    # 읽기 도구는 쿼리 이름이 도구 인자 이름과 같아 대응을 항등으로 만든다.
    return ToolBinding(
        method="GET",
        path=path,
        path_args=path_args,
        query={name: name for name in query},
    )


def _write(
    method: str,
    path: str,
    *,
    path_args: tuple[str, ...] = (),
    body: Mapping[str, str] | None = None,
    body_constants: Mapping[str, str] | None = None,
) -> ToolBinding:
    return ToolBinding(
        method=method,
        path=path,
        path_args=path_args,
        body=dict(body or {}),
        body_constants=dict(body_constants or {}),
    )


def _same(*names: str) -> dict[str, str]:
    return {name: name for name in names}


TOOL_BINDINGS: dict[str, ToolBinding] = {
    "search_tasks": _get(
        "/api/v1/tasks",
        query=("status", "origin", "archived", "root", "parentTaskId", "cursor", "limit"),
    ),
    "get_task": _get("/api/v1/tasks/{taskId}", path_args=("taskId",)),
    "get_timeline": _get("/api/v1/tasks/{taskId}/timeline", path_args=("taskId",), query=("cursor", "limit")),
    "search_events": _get(
        "/api/v1/events/search", query=("q", "taskId", "kind", "lane", "from", "to", "limit")
    ),
    "list_memos": _get("/api/v1/memos", query=("taskId", "eventId")),
    "list_rules": _get("/api/v1/rules", query=("taskId", "all")),
    "get_rule_evidence": _get("/api/v1/rules/{ruleId}/evidence", path_args=("ruleId",), query=("taskId",)),
    "list_tags": _get("/api/v1/tags"),
    "list_recipes": _get("/api/v1/recipes", query=("status",)),
    "list_cleanup_suggestions": _get("/api/v1/task-cleanup/suggestions", query=("status",)),
    "get_job": _get("/api/agent/jobs/{jobId}", path_args=("jobId",)),
    "update_task": _write(
        "PATCH", "/api/v1/tasks/{taskId}", path_args=("taskId",), body=_same("title", "status")
    ),
    "archive_task": _write("POST", "/api/v1/tasks/{taskId}/archive", path_args=("taskId",)),
    "unarchive_task": _write("DELETE", "/api/v1/tasks/{taskId}/archive", path_args=("taskId",)),
    "delete_task": _write("DELETE", "/api/v1/tasks/{taskId}", path_args=("taskId",)),
    "create_memo": _write(
        "POST",
        "/api/v1/memos",
        body=_same("taskId", "body", "eventId"),
        # 저자는 모델이 정할 수 없고 에이전트가 쓴 메모임을 실행이 못박는다.
        body_constants={"author": "agent"},
    ),
    "update_memo": _write("PATCH", "/api/v1/memos/{memoId}", path_args=("memoId",), body=_same("body")),
    "delete_memo": _write("DELETE", "/api/v1/memos/{memoId}", path_args=("memoId",)),
    "create_rule": _write(
        "POST",
        "/api/v1/rules",
        body={
            **_same("taskId", "anchorEventId", "name"),
            "expectation": "expect",
            **_same("severity", "rationale"),
        },
    ),
    "update_rule": _write(
        "PATCH",
        "/api/v1/rules/{ruleId}",
        path_args=("ruleId",),
        body={"name": "name", "expectation": "expect", **_same("severity", "rationale")},
    ),
    "delete_rule": _write("DELETE", "/api/v1/rules/{ruleId}", path_args=("ruleId",)),
    "approve_rule": _write("POST", "/api/v1/rules/{ruleId}/approve", path_args=("ruleId",)),
    "reevaluate_rule": _write("POST", "/api/v1/rules/{ruleId}/reevaluate", path_args=("ruleId",)),
    "create_tag": _write("POST", "/api/v1/tags", body=_same("name", "color", "description")),
    "update_tag": _write(
        "PATCH",
        "/api/v1/tags/{tagId}",
        path_args=("tagId",),
        body=_same("name", "color", "description"),
    ),
    "delete_tag": _write("DELETE", "/api/v1/tags/{tagId}", path_args=("tagId",)),
    "set_task_tags": _write("PUT", "/api/v1/task-tags", body=_same("taskId", "tagIds")),
    "accept_recipe": _write("POST", "/api/v1/recipes/{recipeId}/accept", path_args=("recipeId",)),
    "dismiss_recipe": _write("POST", "/api/v1/recipes/{recipeId}/dismiss", path_args=("recipeId",)),
    "retire_recipe": _write("POST", "/api/v1/recipes/{recipeId}/retire", path_args=("recipeId",)),
    "accept_cleanup": _write(
        "POST", "/api/v1/task-cleanup/suggestions/{suggestionId}/accept", path_args=("suggestionId",)
    ),
    "dismiss_cleanup": _write(
        "POST",
        "/api/v1/task-cleanup/suggestions/{suggestionId}/dismiss",
        path_args=("suggestionId",),
    ),
    "enqueue_job": _write("POST", "/api/agent/jobs", body=_same("kind", "input")),
    "recall_facts": _get("/api/agent/chat/memories"),
    "remember_fact": ToolBinding(
        method="PUT",
        path="/api/agent/chat/memories/{key}",
        path_args=("key",),
        body=_same("content"),
    ),
}

# 대응하는 REST API가 아직 없어 실행 프로세스 안에서 처리되는 도구는 이제 없다.
LOCAL_TOOL_NAMES: tuple[str, ...] = ()


def fill_path(binding: ToolBinding, args: Mapping[str, object]) -> str:
    """경로 틀의 자리를 도구 인자 값으로 채운다."""
    path = binding.path
    for name in binding.path_args:
        path = path.replace("{" + name + "}", quote(str(args[name]), safe=""))
    return path
