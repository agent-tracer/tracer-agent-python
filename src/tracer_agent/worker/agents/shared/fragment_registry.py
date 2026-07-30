"""프롬프트 조각의 코드 기본값과 사용 위치를 표현한다."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from ....shared.agents.shared.fragment_integrity import (
    canonical_fragment_content,
    fragment_content_hash,
    fragment_placeholders,
)

if TYPE_CHECKING:
    from tracer_agent.shared.agents.shared.prompt_integrity import ResolvedFragmentsIntegrityDTO

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
    "integrity_snapshot",
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
    """명시 snapshot이 있으면 그 본문을, 없으면 코드 기본값을 렌더링 전 상태로 돌려준다."""
    local = registry[code_name]
    if snapshot is None:
        return local.content
    binding = next((item for item in local.bindings if item.template_key == template_key), None)
    if binding is None:
        raise ValueError(f"fragment {code_name} is not bound to {template_key}")
    resolved = snapshot.get((template_key, binding.fragment_slot))
    if resolved is None:
        raise ValueError(f"resolved fragment is missing: {template_key}/{binding.fragment_slot}")
    if (
        resolved.get("definitionKey") != local.definition_key
        or resolved.get("codeName") != local.code_name
        or resolved.get("backend") != "python"
    ):
        raise ValueError(f"resolved fragment identity mismatch: {template_key}/{binding.fragment_slot}")
    content = resolved.get("content")
    if not isinstance(content, str) or resolved.get("contentHash") != fragment_content_hash(content):
        raise ValueError(f"resolved fragment hash mismatch: {template_key}/{binding.fragment_slot}")
    if resolved.get("placeholders") != list(fragment_placeholders(content)):
        raise ValueError(f"resolved fragment placeholders mismatch: {template_key}/{binding.fragment_slot}")
    if (
        resolved.get("toolContractVersion") != local.tool_contract_version
        or resolved.get("outputSchemaVersion") != local.output_schema_version
    ):
        raise ValueError(f"resolved fragment contract mismatch: {template_key}/{binding.fragment_slot}")
    if resolved.get("source") == "code-default":
        if resolved.get("semanticVersion") != local.default_version or content != local.content:
            raise ValueError(f"code-default fragment drift: {template_key}/{binding.fragment_slot}")
    elif resolved.get("source") != "database-override":
        raise ValueError(f"resolved fragment source is invalid: {template_key}/{binding.fragment_slot}")
    return content


def integrity_snapshot(
    integrity: ResolvedFragmentsIntegrityDTO,
) -> ResolvedFragmentSnapshot:
    """wire 프래그먼트를 중복 없는 실행 snapshot map으로 고정한다."""
    snapshot: dict[tuple[str, str], Mapping[str, object]] = {}
    for fragment in integrity.fragments:
        key = (fragment.templateKey, fragment.fragmentSlot)
        if key in snapshot:
            raise ValueError(f"duplicate resolved fragment: {key[0]}/{key[1]}")
        payload = fragment.model_dump(mode="python")
        if fragment.contentHash != fragment_content_hash(fragment.content):
            raise ValueError(f"resolved fragment hash mismatch: {key[0]}/{key[1]}")
        if fragment.placeholders != list(fragment_placeholders(fragment.content)):
            raise ValueError(f"resolved fragment placeholders mismatch: {key[0]}/{key[1]}")
        snapshot[key] = MappingProxyType(payload)
    return MappingProxyType(snapshot)
