"""사용자 설정 원장에 감춰 둔 모델 자격을 실행 봉투 창구에 평문으로 낸다."""

from __future__ import annotations

from ..shared.agents.envelope.models import API_KEY_SETTING
from ..shared.agents.runtime.ledger import SqlSource
from ..shared.agents.settings.secret import SettingCipher
from ..shared.agents.settings.store import AppSettingStore


class SettingModelCredentials:
    """설정 원장이 든 모델 자격을 그 사용자 범위에서 찾아 낸다."""

    def __init__(self, source: SqlSource, cipher: SettingCipher) -> None:
        self._source = source
        self._cipher = cipher

    async def api_key(self, user_id: str) -> str | None:
        """그 사용자가 저장해 둔 모델 자격이며 없으면 None이다."""
        async with self._source.connect() as sql:
            stored = await AppSettingStore(sql, self._cipher).list_by_scope(user_id)
        return next((one.value for one in stored if one.key == API_KEY_SETTING), None)
