"""부팅이 올린 조각 묶음을 원장에 심고 이번 실행이 쓸 판을 채널에서 고른다."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..runtime.ledger import LedgerSql, SqlRow
from ..shared.fragment_integrity import fragment_content_hash, fragment_placeholders
from .ids import generate_ulid
from .models import (
    CODE_DEFAULT_ORIGIN,
    DATABASE_OVERRIDE_SOURCE,
    FragmentManifestEntry,
    RegisterAndResolveFragmentsPayload,
    channel_for_profile,
)

# 파일 기본값으로 심은 판의 작성자 자리이며 사람이 쓴 판과 구분된다.
CODE_DEFAULT_AUTHOR = "agent-boot"

# 조회 키를 유일 제약과 같은 칸으로 맞춰야 이긴 행을 놓치지 않는다.
_FIND_DEFINITION = (
    "SELECT id FROM prompt_fragment_definitions "
    "WHERE backend = $1 AND agent_name = $2 AND fragment_name = $3 AND language = $4"
)

_INSERT_DEFINITION = (
    "INSERT INTO prompt_fragment_definitions "
    "(id, definition_key, agent_name, backend, language, fragment_name, code_name, created_at) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) ON CONFLICT DO NOTHING RETURNING id"
)

_VERSION_COLUMNS = (
    "id, semantic_version, content, content_hash, placeholders, tool_contract_version, output_schema_version"
)

_FIND_VERSION = (
    f"SELECT {_VERSION_COLUMNS} FROM prompt_fragment_versions "
    "WHERE definition_id = $1 AND semantic_version = $2"
)

_INSERT_VERSION = (
    "INSERT INTO prompt_fragment_versions "
    "(id, definition_id, semantic_version, content, content_hash, placeholders, "
    "tool_contract_version, output_schema_version, origin, created_by, created_at) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) ON CONFLICT DO NOTHING "
    f"RETURNING {_VERSION_COLUMNS}"
)

_INSERT_BINDING = (
    "INSERT INTO prompt_fragment_bindings "
    "(id, backend, template_key, fragment_slot, definition_id, code_default_version, created_at, updated_at) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $7) ON CONFLICT DO NOTHING"
)

_INSERT_CHANNEL = (
    "INSERT INTO prompt_fragment_channels (id, definition_id, channel, version_id, updated_at) "
    "VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING"
)

_FIND_CHANNEL_VERSION = (
    "SELECT v.id, v.semantic_version, v.content, v.content_hash, v.placeholders, "
    "v.tool_contract_version, v.output_schema_version "
    "FROM prompt_fragment_channels c JOIN prompt_fragment_versions v ON v.id = c.version_id "
    "WHERE c.definition_id = $1 AND c.channel = $2"
)


class PromptFragmentRegistration:
    """조각 정의와 판과 자리와 채널을 한 트랜잭션 안에서 세우고 실행이 쓸 본문을 낸다."""

    def __init__(self, sql: LedgerSql) -> None:
        self._sql = sql

    async def register_and_resolve(
        self, payload: RegisterAndResolveFragmentsPayload, now: datetime
    ) -> list[dict[str, Any]]:
        """묶음의 항목마다 없는 행만 심고 채널이 가리키는 판을 자리마다 낸다."""
        channel = channel_for_profile(payload.profile)
        resolved: list[dict[str, Any]] = []
        async with self._sql.transaction():
            for entry in payload.manifest:
                definition_id = await self._definition_id(entry, now)
                version = await self._default_version(entry, definition_id, now)
                await self._bind(entry, definition_id, now)
                await self._seed_channel(definition_id, channel, str(version["id"]), now)
                chosen = await self._channel_version(definition_id, channel)
                if chosen is None:
                    continue
                resolved.extend(_resolved_items(entry, definition_id, chosen))
        return resolved

    async def _definition_id(self, entry: FragmentManifestEntry, now: datetime) -> str:
        found = await self._sql.fetch(
            _FIND_DEFINITION, entry.backend, entry.agentName, entry.fragmentName, entry.language
        )
        if found:
            return str(found[0]["id"])
        created = await self._sql.fetch(
            _INSERT_DEFINITION,
            generate_ulid(now),
            entry.definitionKey,
            entry.agentName,
            entry.backend,
            entry.language,
            entry.fragmentName,
            entry.codeName,
            now,
        )
        if created:
            return str(created[0]["id"])
        # 같은 조각을 동시에 올린 다른 워커가 먼저 심었으므로 그 행을 그대로 쓴다.
        settled = await self._sql.fetch(
            _FIND_DEFINITION, entry.backend, entry.agentName, entry.fragmentName, entry.language
        )
        if not settled:
            raise LookupError(f"prompt fragment definition is unresolvable: {entry.definitionKey}")
        return str(settled[0]["id"])

    async def _default_version(
        self, entry: FragmentManifestEntry, definition_id: str, now: datetime
    ) -> SqlRow:
        found = await self._sql.fetch(_FIND_VERSION, definition_id, entry.defaultVersion)
        if found:
            return found[0]
        created = await self._sql.fetch(
            _INSERT_VERSION,
            generate_ulid(now),
            definition_id,
            entry.defaultVersion,
            entry.defaultContent,
            fragment_content_hash(entry.defaultContent),
            list(fragment_placeholders(entry.defaultContent)),
            entry.toolContractVersion,
            entry.outputSchemaVersion,
            CODE_DEFAULT_ORIGIN,
            CODE_DEFAULT_AUTHOR,
            now,
        )
        if created:
            return created[0]
        settled = await self._sql.fetch(_FIND_VERSION, definition_id, entry.defaultVersion)
        if not settled:
            raise LookupError(f"prompt fragment version is unresolvable: {entry.definitionKey}")
        return settled[0]

    async def _bind(self, entry: FragmentManifestEntry, definition_id: str, now: datetime) -> None:
        for binding in entry.bindings:
            await self._sql.fetch(
                _INSERT_BINDING,
                generate_ulid(now),
                entry.backend,
                binding.templateKey,
                binding.fragmentSlot,
                definition_id,
                entry.defaultVersion,
                now,
            )

    async def _seed_channel(self, definition_id: str, channel: str, version_id: str, now: datetime) -> None:
        await self._sql.fetch(_INSERT_CHANNEL, generate_ulid(now), definition_id, channel, version_id, now)

    async def _channel_version(self, definition_id: str, channel: str) -> SqlRow | None:
        found = await self._sql.fetch(_FIND_CHANNEL_VERSION, definition_id, channel)
        return found[0] if found else None


def _resolved_items(
    entry: FragmentManifestEntry, definition_id: str, version: SqlRow
) -> list[dict[str, Any]]:
    content = str(version["content"])
    is_code_default = version["semantic_version"] == entry.defaultVersion and content == entry.defaultContent
    return [
        {
            "templateKey": binding.templateKey,
            "fragmentSlot": binding.fragmentSlot,
            "definitionId": definition_id,
            "definitionKey": entry.definitionKey,
            "codeName": entry.codeName,
            "backend": entry.backend,
            "language": entry.language,
            "versionId": str(version["id"]),
            "semanticVersion": str(version["semantic_version"]),
            "content": content,
            "contentHash": str(version["content_hash"]),
            "placeholders": list(version["placeholders"]),
            "toolContractVersion": str(version["tool_contract_version"]),
            "outputSchemaVersion": str(version["output_schema_version"]),
            "source": CODE_DEFAULT_ORIGIN if is_code_default else DATABASE_OVERRIDE_SOURCE,
        }
        for binding in entry.bindings
    ]
