"""계약에서 조각을 읽는 어댑터가 치환을 끝내고 판을 함께 낸다."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.support.contract import agent_prompt
from tracer_agent.worker.agents.shared.contract_prompt_source import (
    ContractPromptSource,
    ContractPromptUnavailable,
)
from tracer_agent.worker.agents.shared.prompt_source_port import PromptSlotMissing

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
def test_조각의_자리표시자는_하나도_남지_않는다(agent: str) -> None:
    resolved = ContractPromptSource().resolve(agent)

    for template in resolved.templates.values():
        for slot in template.slots.values():
            assert "${" not in slot.content


@pytest.mark.parametrize("agent", AGENTS)
def test_다섯_언어의_지시문을_함께_낸다(agent: str) -> None:
    resolved = ContractPromptSource().resolve(agent)

    assert set(resolved.language_directives) == {"auto", "ko", "en", "ja", "zh"}


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


def test_상한이_비어_있으면_자리표시자를_채우지_못하고_던진다(tmp_path: Path) -> None:
    (tmp_path / "agent" / "recipe-scan").mkdir(parents=True)
    (tmp_path / "agent" / "recipe-scan" / "prompt.json").write_text(
        json.dumps(agent_prompt("recipe-scan")), encoding="utf-8"
    )
    (tmp_path / "agent" / "recipe-scan" / "tool.json").write_text(json.dumps({}), encoding="utf-8")

    with pytest.raises(ContractPromptUnavailable):
        ContractPromptSource(tmp_path).resolve("recipe-scan")
