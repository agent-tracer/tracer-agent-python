"""부팅이 올린 프롬프트 정의와 그 판을 원장에 심고 채널이 그 판을 가리키게 한다."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..runtime.ledger import LedgerSql, SqlRow
from ..shared.fragment_integrity import fragment_content_hash
from .ids import generate_ulid
from .models import CODE_DEFAULT_ORIGIN, PRODUCTION_CHANNEL, RegisterPromptPayload

PYTHON_BACKEND = "python"

_FIND_DEFINITION = (
    "SELECT id, user_id, agent_name, backend, language, name FROM prompt_definitions "
    "WHERE user_id = $1 AND agent_name = $2 AND backend = $3 AND language = $4 AND name = $5"
)

_INSERT_DEFINITION = (
    "INSERT INTO prompt_definitions (id, user_id, agent_name, backend, language, name, created_at) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7) "
    "RETURNING id, user_id, agent_name, backend, language, name"
)

_VERSION_COLUMNS = (
    "id, definition_id, semantic_version, content_hash, tool_contract_version, output_schema_version"
)

_FIND_VERSION = (
    f"SELECT {_VERSION_COLUMNS} FROM prompt_versions WHERE definition_id = $1 AND semantic_version = $2"
)

_INSERT_VERSION = (
    "INSERT INTO prompt_versions "
    "(id, definition_id, semantic_version, content, content_hash, tool_contract_version, "
    "output_schema_version, content_origin, created_by, created_at) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) "
    f"RETURNING {_VERSION_COLUMNS}"
)

_FIND_CHANNEL = "SELECT channel, version_id FROM prompt_channels WHERE definition_id = $1 AND channel = $2"

_INSERT_CHANNEL = (
    "INSERT INTO prompt_channels (id, definition_id, channel, version_id, updated_at) "
    "VALUES ($1, $2, $3, $4, $5) RETURNING channel, version_id"
)


class PromptRegistration:
    """프롬프트 정의와 판과 채널을 한 트랜잭션 안에서 없을 때만 세운다."""

    def __init__(self, sql: LedgerSql) -> None:
        self._sql = sql

    async def register(self, user_id: str, payload: RegisterPromptPayload, now: datetime) -> dict[str, Any]:
        """등록한 정의와 판과 채널을 그대로 낸다."""
        async with self._sql.transaction():
            definition = await self._definition(user_id, payload, now)
            version = await self._version(user_id, str(definition["id"]), payload, now)
            channel = await self._channel(str(definition["id"]), str(version["id"]), now)
        return {
            "definition": {
                "id": str(definition["id"]),
                "agentName": str(definition["agent_name"]),
                "backend": str(definition["backend"]),
                "language": str(definition["language"]),
                "name": str(definition["name"]),
            },
            "version": {
                "id": str(version["id"]),
                "definitionId": str(version["definition_id"]),
                "semanticVersion": str(version["semantic_version"]),
                "contentHash": str(version["content_hash"]),
                "toolContractVersion": str(version["tool_contract_version"]),
                "outputSchemaVersion": str(version["output_schema_version"]),
            },
            "channel": {
                "channel": str(channel["channel"]),
                "versionId": str(channel["version_id"]),
            },
        }

    async def _definition(self, user_id: str, payload: RegisterPromptPayload, now: datetime) -> SqlRow:
        found = await self._sql.fetch(
            _FIND_DEFINITION, user_id, payload.agentName, PYTHON_BACKEND, payload.language, payload.name
        )
        if found:
            return found[0]
        created = await self._sql.fetch(
            _INSERT_DEFINITION,
            generate_ulid(now),
            user_id,
            payload.agentName,
            PYTHON_BACKEND,
            payload.language,
            payload.name,
            now,
        )
        return created[0]

    async def _version(
        self, user_id: str, definition_id: str, payload: RegisterPromptPayload, now: datetime
    ) -> SqlRow:
        version = payload.version
        found = await self._sql.fetch(_FIND_VERSION, definition_id, version.semanticVersion)
        if found:
            return found[0]
        created = await self._sql.fetch(
            _INSERT_VERSION,
            generate_ulid(now),
            definition_id,
            version.semanticVersion,
            version.content,
            version.contentHash or fragment_content_hash(version.content),
            version.toolContractVersion,
            version.outputSchemaVersion,
            CODE_DEFAULT_ORIGIN,
            user_id,
            now,
        )
        return created[0]

    async def _channel(self, definition_id: str, version_id: str, now: datetime) -> SqlRow:
        found = await self._sql.fetch(_FIND_CHANNEL, definition_id, PRODUCTION_CHANNEL)
        if found:
            return found[0]
        created = await self._sql.fetch(
            _INSERT_CHANNEL, generate_ulid(now), definition_id, PRODUCTION_CHANNEL, version_id, now
        )
        return created[0]
