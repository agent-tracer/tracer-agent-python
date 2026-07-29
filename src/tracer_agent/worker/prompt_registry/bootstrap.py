"""python 백엔드가 부팅마다 프롬프트 조각을 tracer-api의 내부 창구에 등록하고 해석한다."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from types import MappingProxyType

import httpx

from ..agents.chat.prompt_fragments import LAN_CHAT_FRAGMENT_REGISTRY
from ..agents.recipe_scan.prompt_fragments import LAN_RECIPE_SCAN_FRAGMENT_REGISTRY
from ..agents.shared.fragment_registry import (
    LanPromptFragment,
    fragment_content_hash,
    fragment_placeholders,
)
from ..agents.task_cleanup.prompt_fragments import LAN_TASK_CLEANUP_FRAGMENT_REGISTRY
from ..agents.title_suggestion.prompt_fragments import LAN_TITLE_SUGGESTION_FRAGMENT_REGISTRY

_log = logging.getLogger(__name__)

# 배포 단위 사이에서만 오가는 창구이며 edge가 바깥에 열지 않는다.
REGISTER_TIMEOUT_S = 20.0
FRAGMENT_REGISTER_PATH = "/internal/prompts/fragments/register-and-resolve"

# claude-sdk 등록이 쓰는 것과 같은 상수값이되 두 백엔드가 각자 다른 시점에 자기 계약을 올릴 수 있다.
TOOL_CONTRACT_VERSION = "1"
OUTPUT_SCHEMA_VERSION = "1"


class PromptRegistrationError(Exception):
    """등록 창구가 실패했을 때 원인을 그대로 담아, 부팅을 멈출지는 호출자가 정한다."""


_FRAGMENT_REGISTRIES: tuple[Mapping[str, LanPromptFragment], ...] = (
    LAN_CHAT_FRAGMENT_REGISTRY,
    LAN_RECIPE_SCAN_FRAGMENT_REGISTRY,
    LAN_TASK_CLEANUP_FRAGMENT_REGISTRY,
    LAN_TITLE_SUGGESTION_FRAGMENT_REGISTRY,
)


async def register_and_resolve_fragments(
    client: httpx.AsyncClient, tracer_api_url: str, profile: str
) -> Mapping[tuple[str, str], Mapping[str, object]]:
    manifest = [
        _manifest_entry(fragment) for registry in _FRAGMENT_REGISTRIES for fragment in registry.values()
    ]
    try:
        response = await client.post(
            f"{tracer_api_url.rstrip('/')}{FRAGMENT_REGISTER_PATH}",
            json={"profile": profile, "manifest": manifest},
            timeout=REGISTER_TIMEOUT_S,
        )
        response.raise_for_status()
    except (httpx.HTTPError, ValueError) as error:
        raise PromptRegistrationError(f"fragment registration failed: {error}") from error
    payload = response.json()
    if not isinstance(payload, list):
        raise PromptRegistrationError("fragment registration returned a non-list payload")
    return _verified_snapshot(manifest, payload)


async def resolve_fragments_or_fallback(
    client: httpx.AsyncClient, tracer_api_url: str, profile: str
) -> Mapping[tuple[str, str], Mapping[str, object]] | None:
    """조각 해석이 실패하면 파일 기본값으로 물러서고 그 사실을 경고 로그로 남긴다."""
    try:
        return await register_and_resolve_fragments(client, tracer_api_url, profile)
    except PromptRegistrationError as error:
        _log.warning("agent.prompt.fallback reason=%s", error)
        return None


def _manifest_entry(fragment: LanPromptFragment) -> dict[str, object]:
    agent_name = fragment.definition_key.split(".")[1]
    fragment_name = fragment.bindings[0].fragment_slot
    return {
        "backend": "python",
        "agentName": agent_name,
        "language": "en",
        "codeName": fragment.code_name,
        "definitionKey": fragment.definition_key,
        "fragmentName": fragment_name,
        "defaultVersion": fragment.default_version,
        "defaultContent": fragment.content,
        "toolContractVersion": fragment.tool_contract_version,
        "outputSchemaVersion": fragment.output_schema_version,
        "bindings": [
            {"templateKey": binding.template_key, "fragmentSlot": binding.fragment_slot}
            for binding in fragment.bindings
        ],
    }


def _verified_snapshot(
    manifest: list[dict[str, object]], payload: list[object]
) -> Mapping[tuple[str, str], Mapping[str, object]]:
    expected: dict[tuple[str, str], dict[str, object]] = {}
    for item in manifest:
        bindings = item["bindings"]
        if not isinstance(bindings, list):
            raise PromptRegistrationError("fragment manifest bindings are invalid")
        for binding in bindings:
            if not isinstance(binding, dict):
                raise PromptRegistrationError("fragment manifest binding is invalid")
            expected[(str(binding["templateKey"]), str(binding["fragmentSlot"]))] = item
    received: dict[tuple[str, str], Mapping[str, object]] = {}
    for raw in payload:
        if not isinstance(raw, dict):
            raise PromptRegistrationError("fragment resolution contains a non-object")
        key = (str(raw.get("templateKey", "")), str(raw.get("fragmentSlot", "")))
        local = expected.get(key)
        if (
            local is None
            or raw.get("backend") != "python"
            or raw.get("definitionKey") != local["definitionKey"]
        ):
            raise PromptRegistrationError(f"fragment resolution identity mismatch: {key}")
        content = raw.get("content")
        placeholders = raw.get("placeholders")
        if not isinstance(content, str) or raw.get("contentHash") != fragment_content_hash(content):
            raise PromptRegistrationError(f"fragment resolution hash mismatch: {key}")
        if placeholders != list(fragment_placeholders(content)):
            raise PromptRegistrationError(f"fragment resolution placeholder mismatch: {key}")
        if (
            raw.get("toolContractVersion") != TOOL_CONTRACT_VERSION
            or raw.get("outputSchemaVersion") != OUTPUT_SCHEMA_VERSION
        ):
            raise PromptRegistrationError(f"fragment resolution contract mismatch: {key}")
        source = raw.get("source")
        if source == "code-default":
            if raw.get("semanticVersion") != local["defaultVersion"] or content != local["defaultContent"]:
                raise PromptRegistrationError(f"code-default fragment drift: {key}")
        elif source != "database-override":
            raise PromptRegistrationError(f"fragment resolution source mismatch: {key}")
        received[key] = MappingProxyType(dict(raw))
    if received.keys() != expected.keys():
        raise PromptRegistrationError("fragment resolution is incomplete")
    return MappingProxyType(received)
