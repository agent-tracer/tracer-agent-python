"""프롬프트 조각의 코드 기본값과 사용 위치를 표현한다."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ....shared.agents.shared.fragment_integrity import (
    canonical_fragment_content,
    fragment_content_hash,
    fragment_placeholders,
)

__all__ = [
    "PromptFragment",
    "PromptFragmentBinding",
    "ResolvedFragmentSnapshot",
    "build_fragment_registry",
    "canonical_fragment_content",
    "fragment_code_name",
    "fragment_content",
    "fragment_content_hash",
    "fragment_placeholders",
    "resolved_fragment_content",
]

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


@dataclass(frozen=True)
class PromptFragmentBinding:
    template_key: str
    fragment_slot: str


@dataclass(frozen=True)
class PromptFragment:
    code_name: str
    definition_key: str
    default_version: str
    content: str
    content_hash: str
    placeholders: tuple[str, ...]
    tool_contract_version: str
    output_schema_version: str
    bindings: tuple[PromptFragmentBinding, ...]

    @property
    def template_keys(self) -> tuple[str, ...]:
        """이 조각이 꽂히는 template key만 돌려준다."""
        return tuple(binding.template_key for binding in self.bindings)


def fragment_code_name(agent: str, slot: str) -> str:
    """코드 이름은 에이전트와 자리 이름만으로 나오며 백엔드를 말하는 자리를 두지 않는다."""
    return f"{agent}-{_CAMEL_BOUNDARY.sub('-', slot)}".replace("-", "_").upper()


def build_fragment_registry(
    *,
    agent: str,
    language: str,
    contents: Mapping[str, str],
    usages: Mapping[str, tuple[str, ...]],
    default_version: str = "v1",
) -> Mapping[str, PromptFragment]:
    registry: dict[str, PromptFragment] = {}
    for slot, content in contents.items():
        code_name = fragment_code_name(agent, slot)
        registry[code_name] = PromptFragment(
            code_name=code_name,
            definition_key=f"{agent}.{_CAMEL_BOUNDARY.sub('-', slot).lower()}.{language}",
            default_version=default_version,
            content=content,
            content_hash=fragment_content_hash(content),
            placeholders=fragment_placeholders(content),
            tool_contract_version="1",
            output_schema_version="1",
            bindings=tuple(
                PromptFragmentBinding(template_key=template_key, fragment_slot=slot)
                for template_key in usages[slot]
            ),
        )
    return MappingProxyType(registry)


def fragment_content(registry: Mapping[str, PromptFragment], code_name: str) -> str:
    return registry[code_name].content


ResolvedFragmentSnapshot = Mapping[tuple[str, str], Mapping[str, object]]


def resolved_fragment_content(
    registry: Mapping[str, PromptFragment],
    code_name: str,
    template_key: str,
    snapshot: ResolvedFragmentSnapshot | None = None,
) -> str:
    """이 조각이 template에 꽂힌 자리를 snapshot이 실었으면 그 본문을, 아니면 코드 기본값을 낸다."""
    local = registry[code_name]
    if snapshot is None:
        return local.content
    binding = next((item for item in local.bindings if item.template_key == template_key), None)
    if binding is None:
        raise ValueError(f"fragment {code_name} is not bound to {template_key}")
    resolved = snapshot.get((template_key, binding.fragment_slot))
    content = None if resolved is None else resolved.get("content")
    return content if isinstance(content, str) else local.content
