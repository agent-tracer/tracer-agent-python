"""recipe-scan이 이벤트 창에서 태스크 요약을 계산한다."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .reader import slim_event

TOP_ENTRIES = 12

# 이벤트를 터미널 명령으로 판별하는 원장 속성이며 값이 비면 명령이 아니다.
COMMAND_ATTR = "agent_tracer.command"
USER_MESSAGE_KIND = "agent_tracer.user.message"


def _top(counts: Counter[str], key: str, value: str) -> list[dict[str, Any]]:
    return [{key: name, value: count} for name, count in counts.most_common(TOP_ENTRIES)]


def build_task_summary(task: dict[str, Any], rows: list[dict[str, Any]], total: int) -> dict[str, Any]:
    """태스크 하나와 그 이벤트 창으로 저비용 요약을 만든다."""
    first_user_message: dict[str, Any] | None = None
    tools: Counter[str] = Counter()
    files: Counter[str] = Counter()
    commands: Counter[str] = Counter()

    for row in rows:
        event = slim_event(row)
        if first_user_message is None and event["kind"] == USER_MESSAGE_KIND:
            first_user_message = {
                "title": event["title"],
                **({"body": event["body"]} if "body" in event else {}),
            }
        if "toolName" in event:
            tools[event["toolName"]] += 1
        for path in event["filePaths"]:
            files[path] += 1
        command = str((row.get("metadata") or {}).get(COMMAND_ATTR, "")).strip()
        if command and event["title"].strip():
            commands[event["title"].strip()] += 1

    summary: dict[str, Any] = {
        "id": task["id"],
        "title": task["title"],
        "status": task["status"],
        "taskKind": task["task_kind"],
        "createdAt": str(task["created_at"].isoformat()).replace("+00:00", "Z"),
        "updatedAt": str(task["updated_at"].isoformat()).replace("+00:00", "Z"),
        "eventCount": len(rows),
        "totalEventCount": total,
        "truncated": total > len(rows),
        "toolCounts": _top(tools, "tool", "count"),
        "topFiles": _top(files, "path", "touches"),
        "topCommands": _top(commands, "command", "count"),
    }
    if task["workspace_path"] is not None:
        summary["workspacePath"] = task["workspace_path"]
    if first_user_message is not None:
        summary["firstUserMessage"] = first_user_message
    return summary
