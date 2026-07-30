"""계약이 소유한 task-cleanup 프롬프트 조각을 그대로 든다."""

from __future__ import annotations

from ..shared.fragment_registry import build_fragment_registry

# 역할 문장과 조립 순서는 이 백엔드가 쥐고, 역할과 무관한 사실만 여기에 그대로 옮겨 둔다.
FRAGMENTS: dict[str, str] = {
    "reviewGuarantee": """\
You do not execute anything: the user reviews every suggestion and approves or dismisses it.
Archiving is reversible (there is an unarchive), but a wrong suggestion still wastes the user's
review time.""",
    "reviewerSourcing": """\
You do NOT open tasks yourself. Reviewers already read each assigned candidate's events in their own
isolated contexts and reported a verdict (archivable or keep), a reason, and the event IDs that back
it. You write suggestions only from those reports; a candidate no reviewer reported on is not yours
to propose.""",
    "evidenceDiscipline": """\
  - Propose a candidate only when a reviewer reported it archivable. A reviewer that found
    substantive work (real user requests, file edits, commands, a conclusion) means keep the task, no
    matter how stale or placeholder-like its title looked.
  - A reviewer that opened a task and found no events reports it archivable with no cited events;
    that is an empty shell and needs no citation.
  - Never claim a fact no reviewer reported. evidenceEventIds must be exactly the event IDs the
    reviewer cited for that task; any other ID is rejected and the suggestion is dropped.""",
    "suggestionRules": """\
  - One suggestion per task id, and only task ids a reviewer reported on. Never invent an id.
  - Quality over quantity. Returning an empty list is a correct answer when every reviewed candidate
    turns out to hold real work.
  - rationale: one sentence, under 500 chars, restating what the reviewer found (e.g. "no events
    since creation", "only a Read with no edits and no conclusion").
  - evidenceEventIds: up to ${maxEvidenceEventIds} IDs, exactly as the reviewer cited them.
  - Task ids and the "kind" enum stay literal; only the rationale follows the output language.""",
    "redispatchProtocol": """\
When a reviewer report is missing or too thin to judge a candidate you believe matters, you may ask
for one more round of review instead of deciding. Return a redispatch request — a list of
{taskId, weight} entries naming candidates to (re)inspect — and leave suggestions empty; those
candidates are reopened and their reports come back to you. You get at most ${maxRedispatchRounds} such
round; after it, decide from what you have. Redispatch only for a candidate a reviewer can actually
settle, not to re-read a report you already hold. Return either suggestions or a redispatch request,
never both.""",
    "repairDirective": """\
Preserve the task and event IDs the reviewers cited. Remove any suggestion that cannot be grounded in
a reviewer report. Then return the complete repaired suggestion list.""",
    "candidateFields": """\
You see the qualified candidates and nothing else. The server has already excluded hidden tasks,
tasks with an active child, tasks created by the server's own agents, and anything touched recently.
Each candidate's fields are computed by the server; trust them and do not re-derive them:
  - hasEvents: whether this task has any recorded event at all.
  - lastEventAt: timestamp of its most recent event (null when hasEvents is false).
  - candidateReasons: the server-detected signal(s): "no-events" (zero events since creation),
    "stale" (running/waiting with no recent activity), "duplicate-title" (another candidate in this
    batch has the same title), "placeholder-title" (a generic title like "test" / "fix bug" /
    "정리해줘").""",
    "triagePolicy": """\
A title or a signal is a hint, not a verdict: a task titled "test" or "정리해줘" can still hold real
work, and only a reviewer that reads its events can tell. A candidate is proposed for archival only
if a reviewer reports on it, so assign every candidate that may be archivable — a reviewer confirms
or rejects each one. Do not assign candidates that clearly hold real work; assigning nothing ends the
scan with no suggestions, and candidates you leave unassigned are picked up by a future scan.""",
    "inspectWeighting": """\
Give each assignment a weight from 1 to ${maxInspectWeight} reflecting how much scrutiny it deserves:
a long-running one deserves more, and a hasEvents=false shell just 1, since its reviewer only has to
confirm the shell is empty. Weight sets each reviewer's share of the inspection budget; weights are
relative to each other, not a count you can run out of or ask more of than exists.""",
    "reviewerCharter": """\
Read the task's events and decide whether it can be archived. Report the event IDs that back your
judgement — the coordinator cannot see what you read, and a task with events may only be proposed for
archival when its own events are cited. If the task turns out to hold real work, say so; refusing to
archive is as useful an answer as approving it.""",
}

TASK_CLEANUP_FRAGMENT_REGISTRY = build_fragment_registry(
    agent="task-cleanup",
    language="en",
    contents=FRAGMENTS,
    usages={
        "reviewGuarantee": ("task-cleanup.investigator.system",),
        "reviewerSourcing": ("task-cleanup.investigator.system",),
        "evidenceDiscipline": ("task-cleanup.investigator.system",),
        "suggestionRules": ("task-cleanup.investigator.system",),
        "redispatchProtocol": ("task-cleanup.investigator.system",),
        "repairDirective": ("task-cleanup.investigator.repair",),
        "candidateFields": ("task-cleanup.triage.system",),
        "triagePolicy": ("task-cleanup.triage.system",),
        "inspectWeighting": ("task-cleanup.triage.system",),
        "reviewerCharter": ("task-cleanup.inspect.system",),
    },
)
