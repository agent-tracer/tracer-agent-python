"""실행 봉투와 startup snapshot 중 사용할 프래그먼트를 선택하고 최종 prompt hash를 검증한다."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from tracer_agent.shared.agents.shared.models import AgentExecutionEnvelope
from tracer_agent.shared.agents.shared.prompt_integrity import ResolvedFragmentsIntegrityDTO

from .fragment_registry import ResolvedFragmentSnapshot, integrity_snapshot
from .resolved_prompt_hash import verify_resolved_prompt_bundle_hash


def resolve_execution_prompt_bundle(
    request: AgentExecutionEnvelope,
    startup_snapshot: ResolvedFragmentSnapshot | None,
    build: Callable[[ResolvedFragmentSnapshot | None], dict[str, str]],
    template_names: Mapping[str, str],
) -> tuple[dict[str, str], ResolvedFragmentsIntegrityDTO | None]:
    """봉투가 실은 조각을 우선하고 full-prompt 실행에서 startup DB override를 조용히 쓰지 않는다."""
    integrity = request.promptIntegrity
    if isinstance(integrity, ResolvedFragmentsIntegrityDTO):
        snapshot = integrity_snapshot(integrity)
        prompts = build(snapshot)
        templates = {template_key: prompts[name] for name, template_key in template_names.items()}
        verify_resolved_prompt_bundle_hash(
            templates,
            integrity.resolvedPromptHash,
            {item.templateKey: item.contentHash for item in integrity.resolvedPromptHashes},
        )
        return prompts, integrity
    if startup_snapshot is not None and any(
        fragment.get("source") == "database-override" and template_key in set(template_names.values())
        for (template_key, _slot), fragment in startup_snapshot.items()
    ):
        raise ValueError("database override requires resolved-fragments prompt integrity")
    return build(startup_snapshot), None
