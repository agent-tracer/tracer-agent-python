"""TS와 같은 최종 prompt template별 hash와 bundle hash 계약을 구현한다."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from .fragment_registry import fragment_content_hash


@dataclass(frozen=True)
class ResolvedPromptTemplateHash:
    template_key: str
    content_hash: str


@dataclass(frozen=True)
class ResolvedPromptBundleHash:
    resolved_prompt_hashes: tuple[ResolvedPromptTemplateHash, ...]
    resolved_prompt_hash: str


def resolved_prompt_bundle_hash(templates: Mapping[str, str]) -> ResolvedPromptBundleHash:
    """template key 정렬과 UTF-8 byte-length prefix로 TypeScript 구현체와 같은 bundle hash를 만든다."""
    if not templates:
        raise ValueError("resolved-prompt-bundle.empty")
    if any(not key.strip() for key in templates):
        raise ValueError("resolved-prompt-bundle.empty-template-key")
    hashes = tuple(
        ResolvedPromptTemplateHash(key, fragment_content_hash(content))
        for key, content in sorted(templates.items())
    )
    if len(hashes) == 1:
        return ResolvedPromptBundleHash(hashes, hashes[0].content_hash)
    canonical = "".join(
        f"{len(item.template_key.encode('utf-8'))}:{item.template_key}"
        f"{len(item.content_hash.encode('utf-8'))}:{item.content_hash}"
        for item in hashes
    )
    overall = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return ResolvedPromptBundleHash(hashes, overall)


def verify_resolved_prompt_bundle_hash(
    templates: Mapping[str, str], expected_hash: str, expected_hashes: Mapping[str, str]
) -> ResolvedPromptBundleHash:
    """전체 hash와 template별 hash가 모두 같지 않으면 실행을 거부한다."""
    actual = resolved_prompt_bundle_hash(templates)
    actual_hashes = {item.template_key: item.content_hash for item in actual.resolved_prompt_hashes}
    if actual.resolved_prompt_hash != expected_hash or actual_hashes != dict(expected_hashes):
        raise ValueError("resolved-prompt-bundle.hash-mismatch")
    return actual
