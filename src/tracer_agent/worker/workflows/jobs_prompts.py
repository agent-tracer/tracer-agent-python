"""잡 종류마다 이번 배포가 조립한 프롬프트의 해시를 미리 센다."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ...shared.workflows.jobs_spec import AgentJobKind
from ..agents.recipe_scan import prompts as recipe_prompts
from ..agents.shared.prompt_source_port import AgentPrompt
from ..agents.shared.resolved_prompt_hash import ResolvedPromptBundleHash, resolved_prompt_bundle_hash
from ..agents.task_cleanup import prompts as cleanup_prompts
from ..agents.title_suggestion import prompts as title_prompts

_BUILDERS: tuple[tuple[AgentJobKind, Callable[[AgentPrompt], dict[str, str]], Mapping[str, str]], ...] = (
    ("title-suggestion", title_prompts.build_prompt_bundle, title_prompts.TEMPLATE_KEYS),
    ("recipe-scan", recipe_prompts.build_prompt_bundle, recipe_prompts.TEMPLATE_KEYS),
    ("task-cleanup", cleanup_prompts.build_prompt_bundle, cleanup_prompts.TEMPLATE_KEYS),
)


def resolved_prompts(prompts: Mapping[str, AgentPrompt]) -> dict[AgentJobKind, ResolvedPromptBundleHash]:
    """잡 종류마다 조립 결과의 template 별 해시를 낸다."""
    resolved: dict[AgentJobKind, ResolvedPromptBundleHash] = {}
    for kind, build, template_keys in _BUILDERS:
        built = build(prompts[kind])
        resolved[kind] = resolved_prompt_bundle_hash(
            {key: built[bundle_name] for bundle_name, key in template_keys.items()}
        )
    return resolved
