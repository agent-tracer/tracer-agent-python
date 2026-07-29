"""python 백엔드가 부팅마다 코드에 핀한 프롬프트 번들 해시를 tracer DB의 계약 뷰와 대조한다."""

from __future__ import annotations

from ...shared.agents.runtime.ledger import LedgerPoolProvider, LedgerSql, acquire_sql
from .pin import resolve_pinned_prompt_registrations

# 기반 테이블에는 권한이 없는 읽기 전용 역할이 볼 수 있는 계약 뷰이며 production 채널만 담는다.
_REGISTRY_QUERY = (
    "SELECT semantic_version, content_hash FROM agent_prompt_registry_view "
    "WHERE agent_name = $1 AND backend = 'python'"
)


class PromptRegistryOutOfSyncError(Exception):
    """코드가 핀한 프롬프트 version이 DB의 production 채널과 갈라져 실행할 본문이 불확실할 때 낸다."""

    def __init__(self, agent_name: str, reason: str) -> None:
        super().__init__(f"prompt registry out of sync for {agent_name}: {reason}")
        self.agent_name = agent_name
        self.reason = reason


async def assert_prompt_registry_synced(sql: LedgerSql) -> None:
    """계약 뷰의 production 표시가 코드 pin과 같은지만 읽어서 검사하며, 쓰지 않는다."""
    for pin in resolve_pinned_prompt_registrations():
        try:
            rows = await sql.fetch(_REGISTRY_QUERY, pin.agent_name)
        except Exception as unreachable:
            raise PromptRegistryOutOfSyncError(
                pin.agent_name, f"registry query unreachable: {unreachable}"
            ) from unreachable
        if not rows:
            raise PromptRegistryOutOfSyncError(pin.agent_name, "production channel missing")
        row = rows[0]
        if row["semantic_version"] != pin.semantic_version or row["content_hash"] != pin.resolved_prompt_hash:
            raise PromptRegistryOutOfSyncError(pin.agent_name, "content hash mismatch")


async def assert_prompt_registry_synced_at(tracer_dsn: str) -> None:
    """검사만 하는 짧은 수명의 연결 풀을 열어 대조하고 검사가 끝나면 바로 닫는다."""
    registry = LedgerPoolProvider(tracer_dsn)
    try:
        pool = await registry.pool()
        async with acquire_sql(pool) as sql:
            await assert_prompt_registry_synced(sql)
    finally:
        await registry.close()
