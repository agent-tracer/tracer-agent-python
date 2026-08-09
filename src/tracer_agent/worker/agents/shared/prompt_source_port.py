"""조립 함수가 프롬프트 조각을 받는 자리이며 출처를 말하지 않는다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from string import Template
from typing import Protocol

from tracer_agent.shared.agents.settings.execution import fallback_language

# 조립 시점에 값을 알 수 없고 slot 을 부르는 자리가 채우는 자리표시자다.
RUNTIME_PLACEHOLDERS: frozenset[str] = frozenset({"turns"})


class PromptSlotMissing(LookupError):
    """조립 함수가 부른 template 이나 slot 을 프롬프트가 갖고 있지 않다."""


def directive_for(directives: Mapping[str, str], language: str) -> str:
    """출력 언어 하나의 지시문이며 계약의 onUnknown 대로 목록 밖의 값은 기본 언어로 낮춘다."""
    found = directives.get(language)
    if found is not None:
        return found
    fallback = directives.get(fallback_language())
    if fallback is None:
        raise PromptSlotMissing(f"language directive is missing: {fallback_language()}")
    return fallback


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

    def slot(self, name: str, **values: str) -> str:
        """slot 하나의 본문을 내며 호출마다 달라지는 자리는 values 가 채운다."""
        found = self.slots.get(name)
        if found is None:
            raise PromptSlotMissing(f"prompt slot is missing: {name}")
        if not values:
            return found.content
        try:
            return Template(found.content).substitute(values)
        except KeyError as unknown:
            raise PromptSlotMissing(f"prompt placeholder has no value: {unknown.args[0]}") from unknown


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
        """출력 언어 하나의 지시문이며 계약의 onUnknown 대로 목록 밖의 값은 기본 언어로 낮춘다."""
        return directive_for(self.language_directives, language)


class PromptSourcePort(Protocol):
    """에이전트 이름 하나로 그 에이전트의 프롬프트를 낸다."""

    def resolve(self, agent_name: str) -> AgentPrompt: ...
