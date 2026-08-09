"""task-cleanup 도구 루프가 사용하는 프롬프트."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tracer_agent.shared.agents.task_cleanup.models import (
    MAX_INSPECT_EXCERPTS,
    MAX_INSPECT_REASON_CHARS,
    CleanupBatch,
    CleanupCandidate,
    InspectReport,
)

from ..shared.prompt_source_port import AgentPrompt
from .limits import load_triage_candidate_list_limit

TRIAGE_USER_TEMPLATE = "task-cleanup.triage.user"


@dataclass(frozen=True)
class CleanupPrompts:
    """이 에이전트가 이번 실행에서 쓸 조립된 프롬프트다."""

    investigator_system: str
    triage_system: str
    inspect_system: str
    repair_directive: str


def build_prompt_bundle(prompt: AgentPrompt) -> CleanupPrompts:
    """받은 조각을 이 에이전트의 scaffold 문장 사이에 끼워 프롬프트 넷을 만든다."""
    return CleanupPrompts(
        investigator_system=_investigator(prompt),
        triage_system=_triage(prompt),
        inspect_system=_inspect(prompt),
        repair_directive=_repair(prompt),
    )


def _investigator(prompt: AgentPrompt) -> str:
    template = prompt.template("task-cleanup.investigator.system")
    return "\n".join(
        [
            "You are the coordinator of a task-cleanup scan for Agent Tracer, an observability tool that",
            "records coding-agent sessions.",
            "",
            "Your job is to decide which cleanup candidates should be archived, and to write one short",
            "rationale for each.",
            template.slot("reviewGuarantee"),
            "",
            template.slot("reviewerSourcing"),
            "",
            "Evidence discipline. This is the rule that matters:",
            template.slot("evidenceDiscipline"),
            "",
            "Rules:",
            template.slot("suggestionRules"),
            "",
            template.slot("redispatchProtocol"),
            "",
            "Return the suggestions as structured output conforming to the provided schema.",
        ]
    )


def _repair(prompt: AgentPrompt) -> str:
    return "\n".join(
        [
            "Deterministic validation rejected part of your output:",
            "{errors}",
            "",
            prompt.template("task-cleanup.investigator.repair").slot("repairDirective"),
        ]
    )


def _triage(prompt: AgentPrompt) -> str:
    template = prompt.template("task-cleanup.triage.system")
    return "\n".join(
        [
            "You open the cleanup scan by choosing which candidates to hand to reviewers.",
            "",
            template.slot("candidateFields"),
            "",
            template.slot("triagePolicy"),
            "",
            template.slot("inspectWeighting"),
        ]
    )


def _inspect(prompt: AgentPrompt) -> str:
    return "\n".join(
        [
            "You judge one cleanup candidate by reading what actually happened in it.",
            "",
            prompt.template("task-cleanup.inspect.system").slot("reviewerCharter"),
            "",
            f"Keep the reason under {MAX_INSPECT_REASON_CHARS} characters and cite at most "
            f"{MAX_INSPECT_EXCERPTS} event IDs. A report over either limit is rejected.",
        ]
    )


def build_user_prompt(
    scanned_at: str,
    max_suggestions: int,
    directive: str,
    reports: Sequence[InspectReport] | None = None,
) -> str:
    """정리 스캔 시점과 제안 상한과 출력 언어와 조사 결과를 담은 최초 지시문이다."""
    return "\n".join(
        [
            f"Scan time: {scanned_at}",
            f"Propose at most {max_suggestions} tasks to archive.",
            (
                f"If more than {max_suggestions} qualify, order them by the strength of the evidence "
                f"you cited; only the first {max_suggestions} are kept."
            ),
            f"Output language: {directive}",
        ]
    ) + render_reports(reports)


def build_triage_prompt(
    prompt: AgentPrompt, batch: CleanupBatch, limit: int | None = None
) -> tuple[str, list[CleanupCandidate]]:
    """서버가 선별한 후보를 요청이 직접 실어, 조율자가 되읽지 않고 고르게 한다."""
    template = prompt.template(TRIAGE_USER_TEMPLATE)
    ceiling = load_triage_candidate_list_limit() if limit is None else limit
    listed = batch.candidates[:ceiling]
    lines = [f"Candidates in this batch: {len(batch.candidates)}"]
    if listed:
        lines.append("")
        lines.append(template.slot("candidateList"))
        lines.extend(_candidate_line(candidate) for candidate in listed)
    if len(batch.candidates) > len(listed) or batch.batchTruncated:
        lines.append("")
        lines.append(template.slot("candidateOverflow"))
    return "\n".join(lines), listed


def _candidate_line(candidate: CleanupCandidate) -> str:
    """후보 하나가 조율자에게 보이는 한 줄이며 제목은 구분자를 담을 수 있어 뒤에 둔다."""
    reasons = ", ".join(reason.value for reason in candidate.candidateReasons)
    return (
        f"- {candidate.id} | {candidate.status}"
        f" | events: {'yes' if candidate.hasEvents else 'no'}"
        f" | last: {candidate.lastEventAt or 'none'}"
        f" | reasons: {reasons}"
        f" | title: {candidate.visibleTitle}"
    )


def build_inspect_prompt(task_id: str) -> str:
    """조사자가 후보 하나에만 집중하도록 맡은 범위만 싣는다."""
    return f"Task to judge: {task_id}"


def render_reports(reports: Sequence[InspectReport] | None) -> str:
    """후보별 조사 결과를 조율자가 읽을 근거로 정리한다."""
    if not reports:
        return ""
    lines = [
        f"- {report.taskId}: {'archivable' if report.archivable else 'keep'} — {report.reason}"
        + (f" (events: {', '.join(report.citedEventIds)})" if report.citedEventIds else "")
        for report in reports
    ]
    return "\n\nWhat the cleanup candidate reviewers reported:\n" + "\n".join(lines)
