"""recipe-scan이 이벤트 창에서 태스크 요약을 계산한다."""

from __future__ import annotations

from collections import Counter

from tracer_agent.shared.agents.shared.json_view import (
    JsonObject,
    JsonValue,
    as_object,
    text,
    text_list,
)

from .reader import TaskEventWindow, slim_recipe_event

TOP_ENTRIES = 12

# 이벤트를 터미널 명령으로 판별하는 원장 속성이며 값이 비면 명령이 아니다.
COMMAND_ATTR = "agent_tracer.command"
USER_MESSAGE_KIND = "agent_tracer.user.message"


def _top(counts: Counter[str], key: str, value: str) -> list[JsonValue]:
    entries: list[JsonValue] = [{key: name, value: count} for name, count in counts.most_common(TOP_ENTRIES)]
    return entries


def build_task_summary(window: TaskEventWindow) -> JsonObject:
    """태스크 하나와 그 이벤트 창으로 저비용 요약을 만든다."""
    task, rows, total = window.task, window.rows, window.total
    first_user_message: JsonObject | None = None
    tools: Counter[str] = Counter()
    files: Counter[str] = Counter()
    commands: Counter[str] = Counter()

    for row in rows:
        event = slim_recipe_event(row)
        if first_user_message is None and event["kind"] == USER_MESSAGE_KIND:
            first_user_message = {
                "title": event["title"],
                **({"body": event["body"]} if "body" in event else {}),
            }
        if "toolName" in event:
            tools[text(event["toolName"])] += 1
        for path in text_list(event["filePaths"]):
            files[path] += 1
        metadata = as_object(row.get("metadata") or {})
        command = str(metadata.get(COMMAND_ATTR) or "").strip()
        title = text(event["title"]).strip()
        if command and title:
            commands[title] += 1

    summary: JsonObject = {
        "id": task["id"],
        "title": task["title"],
        "status": task["status"],
        "taskKind": task["taskKind"],
        "createdAt": task["createdAt"],
        "updatedAt": task["updatedAt"],
        "eventCount": len(rows),
        "totalEventCount": total,
        "truncated": total > len(rows),
        "toolCounts": _top(tools, "tool", "count"),
        "topFiles": _top(files, "path", "touches"),
        "topCommands": _top(commands, "command", "count"),
    }
    if task.get("workspacePath") is not None:
        summary["workspacePath"] = task["workspacePath"]
    if first_user_message is not None:
        summary["firstUserMessage"] = first_user_message
    return summary
