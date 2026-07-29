"""chat 도구 인자의 설명을 계약과 같은 값으로 소유한다."""

from __future__ import annotations

# 모델이 인자를 고를 때 읽는 문장이며 계약이 문장을 소유한다.
ARG_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "search_tasks": {
        "status": (
            "Keep only tasks in this lifecycle state: running, waiting, completed, or errored. "
            "Omit to see every state."
        ),
        "origin": (
            'Keep only tasks started by "user" (the person working) or "server-sdk" (this '
            "system's own agent runs). Omit to see both."
        ),
        "archived": (
            'Pass "true" to list archived tasks instead of live ones. Omit to list only live tasks.'
        ),
        "root": (
            'Pass "true" to keep only top-level tasks and drop the subagent runs the user never '
            "sees. Use it unless the question is about subagents."
        ),
        "parentTaskId": (
            "Keep only the subagent runs launched inside this task. Use it to look inside one "
            "task instead of listing everything."
        ),
        "cursor": "Opaque cursor from a previous call's nextCursor. Omit to start from the newest page.",
        "limit": (
            "Max tasks in this page, 1 to 100, default 30. Every row you pull stays in your "
            "context, so ask for the narrowest page that answers the question."
        ),
    },
    "get_task": {
        "taskId": "The task to load, as returned by search_tasks.",
    },
    "get_timeline": {
        "taskId": "The task whose timeline to read.",
        "cursor": (
            "Opaque cursor from a previous call's nextCursor. Omit to start from the newest "
            "entries and page backwards in time."
        ),
        "limit": "Max timeline entries in this page, 1 to 500, default 100.",
    },
    "search_events": {
        "q": "Free-text query matched against event titles and bodies. Omit to browse by filters alone.",
        "taskId": "Keep only hits inside this task. Omit to search across all of the user's tasks.",
        "kind": "Keep only events of this kind, for example execute_tool or agent_tracer.user.message.",
        "lane": (
            "Keep only events in this lane: user, assistant, exploration, planning, "
            "implementation, questions, todos, background, coordination, telemetry, or rule."
        ),
        "from": "Keep only events at or after this ISO-8601 timestamp.",
        "to": "Keep only events at or before this ISO-8601 timestamp.",
        "limit": "Max hits to return, 1 to 100, default 20.",
    },
    "list_memos": {
        "taskId": "Keep only memos on this task. Omit to list every memo the user wrote.",
        "eventId": (
            "Keep only the memos threaded on this single event. Pass it together with taskId when "
            "the user asks about one moment."
        ),
    },
    "list_rules": {
        "taskId": "The task whose applicable rules to list, both global and task-scoped.",
        "all": (
            'Pass "true" to list every rule the user has instead of the ones that apply to one '
            "task. Do not combine it with taskId."
        ),
    },
    "get_rule_evidence": {
        "ruleId": "The rule whose verdict you want the ledger evidence for, as returned by list_rules.",
        "taskId": (
            "Narrow the evidence to one task when the rule is global and has verdicts on several tasks."
        ),
    },
    "list_tags": {},
    "list_recipes": {
        "status": (
            "Keep only recipes in this state: candidate, active, dismissed, superseded, or "
            "retired. Omit to list every recipe."
        ),
    },
    "list_cleanup_suggestions": {
        "status": (
            "Keep only suggestions in this state: pending, accepted, or dismissed. Omit to list "
            "every suggestion."
        ),
    },
    "get_job": {
        "jobId": "The AI job to report on.",
    },
    "list_settings": {},
    "recall_facts": {},
    "remember_fact": {
        "key": (
            'Short slug naming the fact, for example "preferred_language". Remembering the same '
            "key again overwrites the old content."
        ),
        "content": ("The fact itself, written so it still makes sense in a different thread weeks from now."),
    },
    "update_task": {
        "taskId": "The task to change.",
        "title": "The new title. Omit to leave the title as it is.",
        "status": (
            "The new lifecycle state: running, waiting, completed, or errored. Omit to leave the "
            "status as it is."
        ),
    },
    "archive_task": {
        "taskId": "The task to archive.",
    },
    "unarchive_task": {
        "taskId": "The archived task to restore.",
    },
    "delete_task": {
        "taskId": "The task to hide, together with its subagent descendants.",
    },
    "create_memo": {
        "taskId": "The task the memo belongs to.",
        "body": "The memo text to write.",
        "eventId": "Thread the memo on this single event. Omit to attach it to the task as a whole.",
    },
    "update_memo": {
        "memoId": "The memo to rewrite.",
        "body": "The new memo text, which replaces the old body entirely.",
    },
    "delete_memo": {
        "memoId": "The memo to delete.",
    },
    "create_rule": {
        "taskId": "The task the rule watches.",
        "anchorEventId": (
            "The single utterance the rule hangs on, normally the user message that stated the request."
        ),
        "name": "Short human-readable name for the rule.",
        "expectation": (
            'JSON object saying what must happen: {"kind":"command","commandMatches":[...]}, '
            '{"kind":"pattern","pattern":"...","tool":"..."}, or {"kind":"action","tool":"..."}.'
        ),
        "severity": (
            "How hard an unmet rule pushes back: info, warn, or block. Omit to take the server's default."
        ),
        "rationale": "Why the rule exists, shown to the user when the verdict is unmet.",
    },
    "update_rule": {
        "ruleId": "The rule to edit.",
        "name": "The new name. Omit to leave it as it is.",
        "expectation": (
            "The new expectation as a JSON object in the same form create_rule takes. Omit to "
            "leave it as it is."
        ),
        "severity": "The new severity: info, warn, or block. Omit to leave it as it is.",
        "rationale": "The new rationale. Omit to leave it as it is.",
    },
    "delete_rule": {
        "ruleId": "The rule to delete.",
    },
    "approve_rule": {
        "ruleId": "The proposed rule to approve and backfill over its task.",
    },
    "reevaluate_rule": {
        "ruleId": "The rule whose verdict to re-run over its task.",
    },
    "create_tag": {
        "name": "The tag name the user will see.",
        "color": "Lowercase #rrggbb hex color. Omit to let the server pick one.",
        "description": "What the tag is for. Omit when the name already says it.",
    },
    "update_tag": {
        "tagId": "The tag to edit.",
        "name": "The new name. Omit to leave it as it is.",
        "color": "The new lowercase #rrggbb hex color. Omit to leave it as it is.",
        "description": "The new description. Omit to leave it as it is.",
    },
    "delete_tag": {
        "tagId": "The tag to delete and detach from every task.",
    },
    "set_task_tags": {
        "taskId": "The task whose tags to replace.",
        "tagIds": (
            "JSON array of tag ids that becomes the task's full tag set. An empty array detaches every tag."
        ),
    },
    "accept_recipe": {
        "recipeId": "The recipe to accept, activating it.",
    },
    "dismiss_recipe": {
        "recipeId": "The recipe to dismiss.",
    },
    "retire_recipe": {
        "recipeId": "The active recipe to retire.",
    },
    "accept_cleanup": {
        "suggestionId": "The cleanup suggestion to accept and apply.",
    },
    "dismiss_cleanup": {
        "suggestionId": "The cleanup suggestion to dismiss.",
    },
    "upsert_setting": {
        "key": (
            "Which setting to write: anthropic.api_key, anthropic.model, ruleGen.maxRulesPerTask, "
            "taskCleanup.maxSuggestions, or claude.outputLanguage."
        ),
        "value": "The new value as a string. Numeric settings still take their number as text.",
    },
    "delete_setting": {
        "key": (
            "Which setting to clear: anthropic.api_key, anthropic.model, ruleGen.maxRulesPerTask, "
            "taskCleanup.maxSuggestions, or claude.outputLanguage."
        ),
    },
    "enqueue_job": {
        "kind": "Which job to launch: title.suggestion, recipe.scan, task.cleanup, or rule.generation.",
        "input": (
            'JSON object carrying what that kind needs, for example {"taskId":"..."} for '
            "title.suggestion and recipe.scan."
        ),
        "agentBackend": (
            "Force the executor to python or claude-sdk. Omit to let the server pick the default."
        ),
    },
}
