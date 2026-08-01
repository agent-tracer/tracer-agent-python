"""recipe-scan 도구 루프가 사용하는 프롬프트."""

from __future__ import annotations

from collections.abc import Sequence

from tracer_agent.shared.agents.recipe_scan.models import MAX_RECIPE_CANDIDATES, DispatchPlan, ProbeReport

from ..shared.prompt_source_port import AgentPrompt

# 프롬프트 버전은 실행 궤적과 평가 코퍼스에서 의미 변화의 경계를 식별하는 값이다.
PROMPT_VERSION = "recipe-scan-native-v8"

# 관측이 조립 결과의 해시를 template 별로 실을 수 있도록 번들 이름과 template key 를 잇는다.
TEMPLATE_KEYS: dict[str, str] = {
    "investigatorSystemPrompt": "recipe-scan.investigator.system",
    "probeSystemPrompt": "recipe-scan.probe.system",
    "surveySystemPrompt": "recipe-scan.survey.system",
    "repairDirective": "recipe-scan.investigator.repair",
}


def build_prompt_bundle(prompt: AgentPrompt) -> dict[str, str]:
    """받은 조각을 이 에이전트의 scaffold 문장 사이에 끼워 프롬프트 넷을 만든다."""
    return {
        "investigatorSystemPrompt": _investigator(prompt),
        "probeSystemPrompt": _probe(prompt),
        "surveySystemPrompt": _survey(prompt),
        "repairDirective": _repair(prompt),
    }


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
    return "\n".join(
        [
            "Deterministic provenance validation rejected your output:",
            "{errors}",
            "",
            prompt.template("recipe-scan.investigator.repair").slot("repairDirective"),
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


# 예산 소진을 무엇으로 세는지는 실행 기계가 소유하므로 조각이 아니라 이 백엔드가 마지막 문장을 쓴다.
def _probe(prompt: AgentPrompt) -> str:
    template = prompt.template("recipe-scan.probe.system")
    return "\n".join(
        [
            "You are one specialist in a recipe-scan investigation.",
            "",
            template.slot("specialistCharter"),
            "",
            template.slot("specialistReporting"),
            "If your budget runs out with the question still open, say so in exhausted so the coordinator",
            "can decide whether to spend more.",
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


def build_survey_prompt(task_id: str, user_prompt: str | None) -> str:
    """조율자가 조사 계획을 세우는 데 필요한 사실만 싣는다."""
    lines = [f"Anchor task ID: {task_id}"]
    if user_prompt:
        lines.append(f"What the user asked for: {user_prompt}")
    return "\n".join(lines)


def build_probe_prompt(task_id: str, question: str) -> str:
    """전문가가 자기 질문 하나에 집중하도록 맡은 범위만 싣는다."""
    return "\n".join([f"Anchor task ID: {task_id}", f"Your question: {question}"])
