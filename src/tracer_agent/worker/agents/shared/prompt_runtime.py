"""실행 봉투가 최종 prompt 해시를 실었을 때 조립한 번들을 그 해시와 대조한다."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from tracer_agent.shared.agents.shared.models import AgentExecutionEnvelope
from tracer_agent.shared.agents.shared.prompt_integrity import ResolvedFragmentsIntegrityDTO

from .fragment_registry import ResolvedFragmentSnapshot
from .resolved_prompt_hash import verify_resolved_prompt_bundle_hash


def resolve_execution_prompt_bundle(
    request: AgentExecutionEnvelope,
    startup_snapshot: ResolvedFragmentSnapshot | None,
    build: Callable[[ResolvedFragmentSnapshot | None], dict[str, str]],
    template_names: Mapping[str, str],
) -> tuple[dict[str, str], ResolvedFragmentsIntegrityDTO | None]:
    """봉투가 해시를 실었으면 이번 실행이 조립한 번들이 그 해시와 같은지 확인한다."""
    prompts = build(startup_snapshot)
    integrity = request.promptIntegrity
    if not isinstance(integrity, ResolvedFragmentsIntegrityDTO):
        return prompts, None
    templates = {template_key: prompts[name] for name, template_key in template_names.items()}
    verify_resolved_prompt_bundle_hash(
        templates,
        integrity.resolvedPromptHash,
        {item.templateKey: item.contentHash for item in integrity.resolvedPromptHashes},
    )
    return prompts, integrity
