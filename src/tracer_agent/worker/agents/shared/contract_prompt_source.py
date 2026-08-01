"""계약 파일에서 프롬프트 조각을 읽고 상한 값으로 치환을 끝낸다."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from string import Template
from typing import Any

from .prompt_source_port import AgentPrompt, PromptSlot, PromptTemplate

# 계약 저장소는 배포 이미지의 서비스 루트에 함께 실린다.
CONTRACT_ROOT = Path(__file__).resolve().parents[5] / "contract"

_LANGUAGE_DIRECTIVE = "languageDirective"
# 값이 아니라 조율자가 부를 도구 이름이라 상한이 아니라 오케스트레이션 절이 갖는다.
_COORDINATOR_PLACEHOLDER = "checkCitationsTool"


class ContractPromptUnavailable(RuntimeError):
    """계약이 이 배포에 없거나 읽을 수 없어 프롬프트를 세울 수 없다."""


class ContractPromptSource:
    """계약이 소유한 조각과 template 을 이 실행이 쓸 프롬프트로 낸다."""

    def __init__(self, root: Path = CONTRACT_ROOT) -> None:
        self._root = root

    def resolve(self, agent_name: str) -> AgentPrompt:
        """에이전트 하나의 조각을 상한으로 치환해 template 별로 묶는다."""
        prompt = self._read(f"agent/{agent_name}/prompt.json")
        values = self._placeholder_values(agent_name)
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
        return AgentPrompt(templates=templates, language_directives=_directives(fragments))

    def _placeholder_values(self, agent_name: str) -> Mapping[str, str]:
        tools = self._read(f"agent/{agent_name}/tool.json")
        values = {name: str(value) for name, value in tools.get("limits", {}).items()}
        coordinator = tools.get("orchestration", {}).get("coordinatorTools", [])
        if coordinator:
            values[_COORDINATOR_PLACEHOLDER] = str(coordinator[0])
        return values

    def _read(self, relative: str) -> Any:
        path = self._root / relative
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except OSError as unreadable:
            raise ContractPromptUnavailable(f"contract file is unreadable: {path}") from unreadable
        except ValueError as invalid:
            raise ContractPromptUnavailable(f"contract file is not valid JSON: {path}") from invalid


def _joined(lines: list[str]) -> str:
    return "\n".join(lines)


def _render(content: str, values: Mapping[str, str]) -> str:
    try:
        return Template(content).substitute(values)
    except KeyError as unknown:
        raise ContractPromptUnavailable(f"prompt placeholder has no value: {unknown.args[0]}") from unknown


def _directives(fragments: Mapping[str, Any]) -> Mapping[str, str]:
    declared = fragments.get(_LANGUAGE_DIRECTIVE)
    if declared is None:
        return {}
    return {language: _joined(lines) for language, lines in declared["byLanguage"].items()}
