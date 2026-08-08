"""설정 원장의 조회와 쓰기와 삭제에 쓰는 문장 한 벌을 소유한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..runtime.ledger import LedgerSql
from .models import is_sensitive_setting_key, is_setting_key, setting_view
from .secret import SettingCipher, is_encrypted_secret

_SELECT_BY_SCOPE = """
SELECT key, value, updated_at FROM app_settings
 WHERE scope = $1
 ORDER BY key ASC
"""

_SELECT_ONE = """
SELECT value FROM app_settings
 WHERE scope = $1 AND key = $2
"""

_UPSERT = """
INSERT INTO app_settings (scope, key, value, updated_at)
VALUES ($1, $2, $3, $4)
ON CONFLICT (scope, key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
"""

_DELETE = "DELETE FROM app_settings WHERE scope = $1 AND key = $2 RETURNING key"


@dataclass(frozen=True)
class StoredSetting:
    """한 범위 안에서 키 하나가 가리키는 평문 값과 마지막으로 쓴 시각이다."""

    key: str
    value: str
    updated_at: datetime

    def view(self) -> dict[str, Any]:
        """이 설정을 창구가 내는 모양으로 낸다."""
        return setting_view(self.key, self.value, self.updated_at)


class AppSettingStore:
    """자격 키의 값만 감춘 채로 원장에 넣고 읽어 낸다."""

    def __init__(self, sql: LedgerSql, cipher: SettingCipher) -> None:
        self._sql = sql
        self._cipher = cipher

    async def list_by_scope(self, scope: str) -> list[StoredSetting]:
        """그 범위가 저장해 둔 설정을 키 오름차순으로 읽는다."""
        rows = await self._sql.fetch(_SELECT_BY_SCOPE, scope)
        return [
            StoredSetting(str(row["key"]), self._plaintext(str(row["value"])), row["updated_at"])
            for row in rows
            if is_setting_key(str(row["key"]))
        ]

    async def save(self, scope: str, key: str, value: str, updated_at: datetime) -> None:
        """그 범위의 키 하나에 값을 새로 쓰거나 갈아 끼운다."""
        await self._sql.fetch(_UPSERT, scope, key, self._stored(key, value), updated_at)

    async def remove(self, scope: str, key: str) -> bool:
        """그 범위의 키 하나를 지우고 지울 것이 있었는지 낸다."""
        rows = await self._sql.fetch(_DELETE, scope, key)
        return len(rows) > 0

    def _plaintext(self, stored: str) -> str:
        return self._cipher.decrypt(stored) if is_encrypted_secret(stored) else stored

    def _stored(self, key: str, value: str) -> str:
        return self._cipher.encrypt(value) if is_sensitive_setting_key(key) else value


class PlainSettingReader:
    """자격이 아닌 설정 하나를 그 범위에서 평문 그대로 읽는다."""

    def __init__(self, sql: LedgerSql) -> None:
        self._sql = sql

    async def read(self, scope: str, key: str) -> str | None:
        """그 범위가 저장해 둔 값이며 없으면 None 이다."""
        if is_sensitive_setting_key(key):
            raise ValueError(f"sensitive setting needs a cipher to read: {key}")
        rows = await self._sql.fetch(_SELECT_ONE, scope, key)
        return str(rows[0]["value"]) if rows else None
