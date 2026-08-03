"""recipe-scan 도구 루프가 사용하는 프롬프트."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tracer_agent.shared.agents.recipe_scan.models import (
    MAX_RECIPE_CANDIDATES,
    DispatchPlan,
    ProbeAssignment,
    ProbeReport,
)

from ..shared.prompt_source_port import AgentPrompt


@dataclass(frozen=True)
class RecipePrompts:
    """이 에이전트가 이번 실행에서 쓸 조립된 프롬프트다."""

    investigator_system: str
    probe_system: str
    survey_system: str
    repair_directive: str


def build_prompt_bundle(prompt: AgentPrompt) -> RecipePrompts:
    """받은 조각을 이 에이전트의 scaffold 문장 사이에 끼워 프롬프트 넷을 만든다."""
    return RecipePrompts(
        investigator_system=_investigator(prompt),
        probe_system=_probe(prompt),
        survey_system=_survey(prompt),
        repair_directive=_repair(prompt),
    )


def _investigator(prompt: AgentPrompt) -> str:
    template = prompt.template("recipe-scan.investigator.system")
    return "\n".join(
        [
            "You are the coordinator of a recipe-scan investigation. You mine the specialists' reports for",
            'reusable "recipes".',
            "",
            template.slot("recipeDefinition"),
            "",
            template.slot("evidenceSourcing"),
            "",
            "How to work:",
            template.slot("turnSplitting"),
            "",
            template.slot("citationDiscipline"),
            "",
            template.slot("candidateBudget"),
            "",
            template.slot("redispatchProtocol"),
            "",
            template.slot("outputFields"),
            "",
            "Rules:",
            template.slot("qualityRules"),
            "",
            "When the evidence is enough, stop calling tools and emit the structured output.",
        ]
    )


def _repair(prompt: AgentPrompt) -> str:
    template = prompt.template("recipe-scan.investigator.repair")
    return "\n".join(
        [
            template.slot("repairPriorOutput"),
            "{previous}",
            "",
            "Deterministic provenance validation rejected your output:",
            "{errors}",
            "",
            template.slot("repairDirective"),
        ]
    )


def _survey(prompt: AgentPrompt) -> str:
    template = prompt.template("recipe-scan.survey.system")
    return "\n".join(
        [
            "You plan one recipe-scan investigation before it starts.",
            "",
            template.slot("specialistCatalog"),
            "",
            template.slot("dispatchWeighting"),
            "",
            template.slot("emptyPlan"),
        ]
    )


def _probe(prompt: AgentPrompt) -> str:
    template = prompt.template("recipe-scan.probe.system")
    return "\n".join(
        [
            "You are one specialist in a recipe-scan investigation.",
            "",
            template.slot("specialistCharter"),
            "",
            template.slot("specialistReporting"),
            "",
            template.slot("probeBoundary"),
            "",
            template.slot("budgetExhaustion"),
        ]
    )


def build_user_prompt(
    task_id: str,
    user_prompt: str | None,
    directive: str,
    plan: DispatchPlan | None = None,
    reports: Sequence[ProbeReport] | None = None,
) -> str:
    """앵커 태스크와 사용자 지시와 출력 언어와 스스로 세운 계획을 담은 최초 지시문이다."""
    lines = [f"Anchor taskId: {task_id}"]
    if user_prompt:
        lines.append(f"User direction: {user_prompt}")
    lines.append(f"Output language: {directive}")
    lines.append(f"Mine this task for up to {MAX_RECIPE_CANDIDATES} recipe candidates.")
    return "\n".join(lines) + render_plan(plan) + render_reports(reports)


def render_reports(reports: Sequence[ProbeReport] | None) -> str:
    """전문가들이 올린 보고를 조율자가 읽을 근거로 편다."""
    if not reports:
        return ""
    blocks = []
    for report in reports:
        lines = [f"### {report.probe}" + (" (budget exhausted)" if report.exhausted else "")]
        lines.append(report.verdict)
        lines.extend(f"- [{excerpt.taskId}/{excerpt.eventId}] {excerpt.text}" for excerpt in report.excerpts)
        blocks.append("\n".join(lines))
    return "\n\nWhat your specialists reported:\n\n" + "\n\n".join(blocks)


def render_plan(plan: DispatchPlan | None) -> str:
    """조율자가 세운 계획을 조사자가 읽을 지시문으로 편다."""
    if plan is None or not plan.probes:
        return ""
    lines = [f"- {probe.probe} (weight {probe.weight}): {probe.question}" for probe in plan.probes]
    return "\n\nYour own plan for this investigation:\n" + "\n".join(lines)


def build_survey_prompt(
    prompt: AgentPrompt, task_id: str, user_prompt: str | None, available_turns: int
) -> str:
    """조율자가 조사 계획을 세우는 데 필요한 사실과 쓸 수 있는 턴을 싣는다."""
    template = prompt.template("recipe-scan.survey.user")
    lines = [
        f"Anchor task ID: {task_id}",
        template.slot("turnAllowance", turns=str(available_turns)),
    ]
    if user_prompt:
        lines.append(f"What the user asked for: {user_prompt}")
    return "\n".join(lines)


def build_probe_prompt(
    prompt: AgentPrompt,
    task_id: str,
    question: str,
    turns: int,
    siblings: Sequence[ProbeAssignment] = (),
) -> str:
    """전문가가 자기 질문에 집중하도록 맡은 범위와 맡지 않은 범위를 함께 싣는다."""
    template = prompt.template("recipe-scan.probe.user")
    lines = [
        f"Anchor task ID: {task_id}",
        f"Your question: {question}",
        template.slot("turnAllowance", turns=str(turns)),
    ]
    if siblings:
        lines.append("")
        lines.append(template.slot("siblingAssignments"))
        lines.extend(f"- {one.probe}: {one.question}" for one in siblings)
    return "\n".join(lines)
