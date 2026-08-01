"""task-cleanup 도구 루프가 사용하는 프롬프트."""

from __future__ import annotations

from collections.abc import Sequence

from tracer_agent.shared.agents.task_cleanup.models import InspectReport

from ..shared.prompt_source_port import AgentPrompt

# 관측이 조립 결과의 해시를 template 별로 실을 수 있도록 번들 이름과 template key 를 잇는다.
TEMPLATE_KEYS: dict[str, str] = {
    "investigatorSystemPrompt": "task-cleanup.investigator.system",
    "triageSystemPrompt": "task-cleanup.triage.system",
    "inspectSystemPrompt": "task-cleanup.inspect.system",
    "repairDirective": "task-cleanup.investigator.repair",
}


def build_prompt_bundle(prompt: AgentPrompt) -> dict[str, str]:
    """받은 조각을 이 에이전트의 scaffold 문장 사이에 끼워 프롬프트 넷을 만든다."""
    return {
        "investigatorSystemPrompt": _investigator(prompt),
        "triageSystemPrompt": _triage(prompt),
        "inspectSystemPrompt": _inspect(prompt),
        "repairDirective": _repair(prompt),
    }


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
            f"Output language: {directive}",
        ]
    ) + render_reports(reports)


def build_triage_prompt(candidate_count: int) -> str:
    """조율자가 무엇을 열어볼지 정하는 데 필요한 사실만 싣는다."""
    return "\n".join(
        [
            f"Candidates in this batch: {candidate_count}",
            "Call list_candidate_tasks to see them before deciding.",
        ]
    )


def build_inspect_prompt(task_id: str) -> str:
    """조사자가 후보 하나에만 집중하도록 맡은 범위만 싣는다."""
    return f"Task to judge: {task_id}"


def render_reports(reports: Sequence[InspectReport] | None) -> str:
    """후보별 조사 결과를 조율자가 읽을 근거로 편다."""
    if not reports:
        return ""
    lines = [
        f"- {report.taskId}: {'archivable' if report.archivable else 'keep'} — {report.reason}"
        + (f" (events: {', '.join(report.citedEventIds)})" if report.citedEventIds else "")
        for report in reports
    ]
    return "\n\nWhat the cleanup candidate reviewers reported:\n" + "\n".join(lines)
