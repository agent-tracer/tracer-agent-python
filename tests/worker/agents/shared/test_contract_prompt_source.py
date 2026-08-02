"""계약에서 조각을 읽는 어댑터가 치환을 끝내고 판을 함께 낸다."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.support.contract import agent_prompt, agent_tools
from tracer_agent.worker.agents.shared.contract_prompt_source import (
    ContractPromptSource,
    ContractPromptUnavailable,
)
from tracer_agent.worker.agents.shared.prompt_source_port import (
    RUNTIME_PLACEHOLDERS,
    AgentPrompt,
    PromptSlotMissing,
    PromptTemplate,
    PromptVersionDiverged,
)

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z][A-Za-z0-9_]*)\}")

AGENTS = ("chat", "recipe-scan", "task-cleanup", "title-suggestion")


@pytest.mark.parametrize("agent", AGENTS)
def test_template과_slot이_계약이_선언한_이름_그대로다(agent: str) -> None:
    declared = agent_prompt(agent)["templates"]

    resolved = ContractPromptSource().resolve(agent)

    assert set(resolved.templates) == set(declared)
    for key, template in declared.items():
        assert set(resolved.template(key).slots) == set(template["slots"])
        assert resolved.template(key).version == template["version"]


@pytest.mark.parametrize("agent", AGENTS)
def test_조립이_채울_수_있는_자리표시자는_하나도_남지_않는다(agent: str) -> None:
    resolved = ContractPromptSource().resolve(agent)

    for template in resolved.templates.values():
        for slot in template.slots.values():
            # 호출마다 달라지는 자리만 남고 그 자리는 slot 을 부를 때 채워진다.
            assert set(_PLACEHOLDER.findall(slot.content)) <= RUNTIME_PLACEHOLDERS


@pytest.mark.parametrize("agent", AGENTS)
def test_계약이_준_판을_프롬프트와_도구_양쪽에서_낸다(agent: str) -> None:
    declared = agent_prompt(agent)["templates"]
    resolved = ContractPromptSource().resolve(agent)

    assert resolved.version() == next(iter(declared.values()))["version"]
    assert resolved.tool_contract_version == agent_tools(agent)["version"]


def test_template_의_판이_갈리면_실을_판을_고르지_않는다() -> None:
    templates = {
        "a": PromptTemplate(version="v0.0.1", slots={}),
        "b": PromptTemplate(version="v0.0.2", slots={}),
    }

    with pytest.raises(PromptVersionDiverged):
        AgentPrompt(templates=templates, language_directives={}, tool_contract_version="v0.0.1").version()


@pytest.mark.parametrize("agent", AGENTS)
def test_다섯_언어의_지시문을_함께_낸다(agent: str) -> None:
    resolved = ContractPromptSource().resolve(agent)

    assert set(resolved.language_directives) == {"auto", "ko", "en", "ja", "zh"}


def test_언어_지시문도_조각과_같은_상한으로_치환된다(tmp_path: Path) -> None:
    declared = agent_prompt("task-cleanup")
    declared["fragments"]["languageDirective"]["byLanguage"]["ko"] = [
        "Write at most ${maxSuggestions} rationales in Korean."
    ]
    (tmp_path / "agent" / "task-cleanup").mkdir(parents=True)
    (tmp_path / "agent" / "task-cleanup" / "prompt.json").write_text(json.dumps(declared), encoding="utf-8")
    (tmp_path / "agent" / "task-cleanup" / "tool.json").write_text(
        json.dumps(agent_tools("task-cleanup")), encoding="utf-8"
    )

    resolved = ContractPromptSource(tmp_path).resolve("task-cleanup")

    limit = agent_tools("task-cleanup")["limits"]["maxSuggestions"]
    assert resolved.directive("ko") == f"Write at most {limit} rationales in Korean."


def test_상한이_계약의_limits_와_조율자_도구_이름에서_온다() -> None:
    resolved = ContractPromptSource().resolve("recipe-scan")

    survey = resolved.template("recipe-scan.survey.system")
    investigator = resolved.template("recipe-scan.investigator.system")

    assert "weight from 1 to 10" in survey.slot("dispatchWeighting")
    assert "check_citations" in investigator.slot("evidenceSourcing")


def test_없는_언어를_물으면_던진다() -> None:
    resolved = ContractPromptSource().resolve("chat")

    with pytest.raises(PromptSlotMissing):
        resolved.directive("de")


def test_계약을_읽지_못하면_프롬프트를_세우지_않는다(tmp_path: Path) -> None:
    with pytest.raises(ContractPromptUnavailable):
        ContractPromptSource(tmp_path).resolve("chat")


def test_상한이_비어_있으면_그_자리가_채워지지_않은_채_남는다(tmp_path: Path) -> None:
    (tmp_path / "agent" / "recipe-scan").mkdir(parents=True)
    (tmp_path / "agent" / "recipe-scan" / "prompt.json").write_text(
        json.dumps(agent_prompt("recipe-scan")), encoding="utf-8"
    )
    (tmp_path / "agent" / "recipe-scan" / "tool.json").write_text(
        json.dumps({"version": "v0.0.1"}), encoding="utf-8"
    )

    resolved = ContractPromptSource(tmp_path).resolve("recipe-scan")
    template = resolved.template("recipe-scan.investigator.system")

    # 계약이 값을 주지 않은 자리는 조립을 멈추지 않고 남으며, 채우려는 자리에서 걸린다.
    assert "${recipeCandidateLimit}" in template.slot("candidateBudget")
    with pytest.raises(PromptSlotMissing):
        template.slot("candidateBudget", unrelated="값")
