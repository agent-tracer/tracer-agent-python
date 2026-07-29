"""세 에이전트 파일 프롬프트 번들이 코드에 핀한 version과 resolved hash를 DB 없이 계산한다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..agents.recipe_scan.prompts import PROMPT_BUNDLE as RECIPE_SCAN_BUNDLE
from ..agents.recipe_scan.prompts import PROMPT_VERSION as RECIPE_SCAN_PROMPT_VERSION
from ..agents.shared.resolved_prompt_hash import resolved_prompt_bundle_hash
from ..agents.task_cleanup.prompts import PROMPT_BUNDLE as TASK_CLEANUP_BUNDLE
from ..agents.task_cleanup.prompts import PROMPT_VERSION as TASK_CLEANUP_PROMPT_VERSION
from ..agents.title_suggestion.prompts import PROMPT_BUNDLE as TITLE_SUGGESTION_BUNDLE
from ..agents.title_suggestion.prompts import PROMPT_VERSION as TITLE_SUGGESTION_PROMPT_VERSION


@dataclass(frozen=True)
class PinnedPromptRegistration:
    """python 백엔드 production 채널이 (agentName, language)마다 가리켜야 할 version과 해시다."""

    agent_name: str
    semantic_version: str
    resolved_prompt_hash: str


# 세 에이전트 모두 시스템 프롬프트를 언어로 나누지 않으므로 모든 언어가 같은 pin을 본다.
_PINNED_BUNDLES: tuple[tuple[str, str, Mapping[str, str]], ...] = (
    ("title-suggestion", TITLE_SUGGESTION_PROMPT_VERSION, TITLE_SUGGESTION_BUNDLE),
    ("recipe-scan", RECIPE_SCAN_PROMPT_VERSION, RECIPE_SCAN_BUNDLE),
    ("task-cleanup", TASK_CLEANUP_PROMPT_VERSION, TASK_CLEANUP_BUNDLE),
)


def resolve_pinned_prompt_registrations() -> tuple[PinnedPromptRegistration, ...]:
    """부트 검사와 시드 적재가 같은 값을 보도록 이 함수 하나로 세 에이전트의 pin을 계산한다."""
    return tuple(
        PinnedPromptRegistration(
            agent_name=agent_name,
            semantic_version=semantic_version,
            resolved_prompt_hash=resolved_prompt_bundle_hash(bundle).resolved_prompt_hash,
        )
        for agent_name, semantic_version, bundle in _PINNED_BUNDLES
    )
