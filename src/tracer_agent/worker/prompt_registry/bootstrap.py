"""python 백엔드가 부팅마다 프롬프트 조각을 agent-api의 내부 창구에 등록하고 해석한다."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

import httpx

from ..agents.chat.prompt_fragments import CHAT_FRAGMENT_REGISTRY
from ..agents.recipe_scan.prompt_fragments import RECIPE_SCAN_FRAGMENT_REGISTRY
from ..agents.shared.fragment_registry import (
    PromptFragment,
    fragment_content_hash,
    fragment_placeholders,
)
from ..agents.task_cleanup.prompt_fragments import TASK_CLEANUP_FRAGMENT_REGISTRY
from ..agents.title_suggestion.prompt_fragments import TITLE_SUGGESTION_FRAGMENT_REGISTRY

# 배포 단위 사이에서만 오가는 창구이며 게이트웨이가 바깥에 열지 않는다.
REGISTER_TIMEOUT_S = 20.0
FRAGMENT_REGISTER_PATH = "/internal/prompts/fragments/register-and-resolve"

# 두 백엔드가 각자 다른 시점에 자기 계약을 올릴 수 있으므로 값이 같아도 각자 든다.
TOOL_CONTRACT_VERSION = "1"
OUTPUT_SCHEMA_VERSION = "1"


class PromptRegistrationError(Exception):
    """등록 창구가 실패했거나 받은 판이 코드 선언과 어긋났음을 담으며 부팅을 멈춘다."""


_FRAGMENT_REGISTRIES: tuple[Mapping[str, PromptFragment], ...] = (
    CHAT_FRAGMENT_REGISTRY,
    RECIPE_SCAN_FRAGMENT_REGISTRY,
    TASK_CLEANUP_FRAGMENT_REGISTRY,
    TITLE_SUGGESTION_FRAGMENT_REGISTRY,
)


async def register_and_resolve_fragments(
    client: httpx.AsyncClient, agent_api_url: str, profile: str
) -> Mapping[tuple[str, str], Mapping[str, object]]:
    """네 에이전트의 조각을 등록하고 받은 판을 코드 선언과 대조해 실행 snapshot으로 고정한다."""
    manifest = [
        _manifest_entry(fragment) for registry in _FRAGMENT_REGISTRIES for fragment in registry.values()
    ]
    try:
        response = await client.post(
            f"{agent_api_url.rstrip('/')}{FRAGMENT_REGISTER_PATH}",
            json={"profile": profile, "manifest": manifest},
            timeout=REGISTER_TIMEOUT_S,
        )
        response.raise_for_status()
    except (httpx.HTTPError, ValueError) as error:
        raise PromptRegistrationError(f"fragment registration failed: {error}") from error
    return _verified_snapshot(manifest, _envelope_data(response.json()))


def _envelope_data(payload: object) -> list[object]:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise PromptRegistrationError("fragment registration returned a failure envelope")
    data = payload.get("data")
    if not isinstance(data, list):
        raise PromptRegistrationError("fragment registration returned a non-list payload")
    return data


def _manifest_entry(fragment: PromptFragment) -> dict[str, object]:
    agent_name = fragment.definition_key.rsplit(".", 2)[0]
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
