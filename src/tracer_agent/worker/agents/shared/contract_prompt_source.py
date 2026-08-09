"""계약 파일에서 프롬프트 조각을 읽고 상한 값으로 치환을 끝낸다."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from string import Template
from typing import Any

from tracer_agent.shared.agents.shared.contract_root import CONTRACT_ROOT

from .prompt_source_port import RUNTIME_PLACEHOLDERS, AgentPrompt, PromptSlot, PromptTemplate

_LANGUAGE_DIRECTIVE = "languageDirective"
# 조율자가 부를 도구는 값이 아니라 이름이라 상한이 아니라 오케스트레이션 절이 갖는다.
_TOOL_PLACEHOLDER_SUFFIX = "Tool"


class ContractPromptUnavailable(RuntimeError):
    """계약이 이 배포에 없거나 읽을 수 없어 프롬프트를 세울 수 없다."""


class ContractPromptSource:
    """계약이 소유한 조각과 template 을 이 실행이 쓸 프롬프트로 낸다."""

    def __init__(self, root: Path = CONTRACT_ROOT) -> None:
        self._root = root

    def resolve(self, agent_name: str) -> AgentPrompt:
        """에이전트 하나의 조각을 상한으로 치환해 template 별로 묶는다."""
        prompt = self._prompt(agent_name)
        tools = self._tools(agent_name)
        values = _placeholder_values(tools)
        fragments = prompt["fragments"]
        templates = {
            key: PromptTemplate(
                version=str(template["version"]),
                slots={
                    slot: PromptSlot(
                        content=_render(_joined(fragments[slot]["content"]), values),
                        version=str(fragments[slot]["version"]),
                    )
                    for slot in template["slots"]
                },
            )
            for key, template in prompt["templates"].items()
        }
        return AgentPrompt(
            templates=templates,
            language_directives=_directives(fragments, values),
            tool_contract_version=str(tools["version"]),
        )

    def tool(self, agent_name: str, tool_name: str) -> dict[str, Any]:
        """도구 하나의 설명과 인자 선언을 계약에서 그대로 읽는다."""
        found = self._tools(agent_name).get("tools", {}).get(tool_name)
        if isinstance(found, dict):
            return found
        raise ContractPromptUnavailable(f"tool contract is missing: {agent_name}.{tool_name}")

    def _prompt(self, agent_name: str) -> dict[str, Any]:
        payload = self._read(f"agent/{agent_name}/prompt.json")
        if isinstance(payload, dict):
            return payload
        raise ContractPromptUnavailable(f"prompt contract is invalid: {agent_name}")

    def _tools(self, agent_name: str) -> dict[str, Any]:
        payload = self._read(f"agent/{agent_name}/tool.json")
        if isinstance(payload, dict):
            return payload
        raise ContractPromptUnavailable(f"tool contract is invalid: {agent_name}")

    def _read(self, relative: str) -> Any:
        path = self._root / relative
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except OSError as unreadable:
            raise ContractPromptUnavailable(f"contract file is unreadable: {path}") from unreadable
        except ValueError as invalid:
            raise ContractPromptUnavailable(f"contract file is not valid JSON: {path}") from invalid


def _placeholder_values(tools: Mapping[str, Any]) -> Mapping[str, str]:
    values = {name: str(value) for name, value in tools.get("limits", {}).items()}
    for name in tools.get("orchestration", {}).get("coordinatorTools", []):
        values[_tool_placeholder(str(name))] = str(name)
    return values


def _tool_placeholder(tool_name: str) -> str:
    """snake_case 도구 이름을 프롬프트가 부르는 자리표시자 이름으로 옮긴다."""
    head, *rest = tool_name.split("_")
    return head + "".join(part.capitalize() for part in rest) + _TOOL_PLACEHOLDER_SUFFIX


def _joined(lines: list[str]) -> str:
    return "\n".join(lines)


# 호출마다 달라지는 자리는 조립 시점에 값이 없으므로 그대로 두고 slot 이 채운다.
def _render(content: str, values: Mapping[str, str]) -> str:
    rendered = Template(content).safe_substitute(values)
    # 채우지 못한 자리를 그대로 내보내면 모델이 리터럴 자리표시자를 지시로 읽는다.
    unresolved = sorted(set(Template(rendered).get_identifiers()) - RUNTIME_PLACEHOLDERS)
    if unresolved:
        raise ContractPromptUnavailable(f"prompt placeholders have no value: {', '.join(unresolved)}")
    return rendered


def _directives(fragments: Mapping[str, Any], values: Mapping[str, str]) -> Mapping[str, str]:
    declared = fragments.get(_LANGUAGE_DIRECTIVE)
    if declared is None:
        return {}
    return {language: _render(_joined(lines), values) for language, lines in declared["byLanguage"].items()}
