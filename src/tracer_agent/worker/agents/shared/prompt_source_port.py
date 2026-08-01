"""조립 함수가 프롬프트 조각을 받는 자리이며 출처를 말하지 않는다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


class PromptSlotMissing(LookupError):
    """조립 함수가 부른 template 이나 slot 을 프롬프트가 갖고 있지 않다."""


class PromptVersionDiverged(ValueError):
    """한 에이전트의 template 들이 서로 다른 판에 서 있어 실을 판 하나를 고를 수 없다."""


@dataclass(frozen=True)
class PromptSlot:
    """치환이 끝난 조각 본문과 그 조각의 판이다."""

    content: str
    version: str


@dataclass(frozen=True)
class PromptTemplate:
    """template 하나의 판과 그 template 이 갖는 slot 들이다."""

    version: str
    slots: Mapping[str, PromptSlot]

    def slot(self, name: str) -> str:
        """slot 하나의 본문을 내며 없으면 던진다."""
        found = self.slots.get(name)
        if found is None:
            raise PromptSlotMissing(f"prompt slot is missing: {name}")
        return found.content


@dataclass(frozen=True)
class AgentPrompt:
    """에이전트 하나가 이번 실행에서 쓸 template 과 언어 지시문과 계약이 준 판이다."""

    templates: Mapping[str, PromptTemplate]
    language_directives: Mapping[str, str]
    tool_contract_version: str

    def version(self) -> str:
        """이 에이전트의 프롬프트가 선 판이며 template 들의 판이 갈리면 던진다."""
        versions = {template.version for template in self.templates.values()}
        if len(versions) != 1:
            raise PromptVersionDiverged(f"prompt templates stand on {sorted(versions)}")
        return versions.pop()

    def template(self, key: str) -> PromptTemplate:
        """template 하나를 내며 없으면 던진다."""
        found = self.templates.get(key)
        if found is None:
            raise PromptSlotMissing(f"prompt template is missing: {key}")
        return found

    def directive(self, language: str) -> str:
        """출력 언어 하나의 지시문을 내며 없으면 던진다."""
        found = self.language_directives.get(language)
        if found is None:
            raise PromptSlotMissing(f"language directive is missing: {language}")
        return found


class PromptSourcePort(Protocol):
    """에이전트 이름 하나로 그 에이전트의 프롬프트를 낸다."""

    def resolve(self, agent_name: str) -> AgentPrompt: ...
