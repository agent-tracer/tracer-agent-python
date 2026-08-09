"""추적이 소유한 사건 하나를 적용 이력 행으로 투영하는 규칙을 소유한다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from ..shared.instant import parse_instant
from ..shared.json_view import JsonObject, JsonValue
from .models import RECIPE_INJECTED_VIA_MANUAL, RECIPE_INJECTED_VIA_PULL, RecipeApplication
from .store import RecipeApplicationStore

# 이 프로젝터가 고르는 사건의 종류이며 나머지는 아무것도 하지 않고 넘긴다.
RECIPE_INJECTED_EVENT_KIND = "agent_tracer.recipe.injected"


@dataclass(frozen=True)
class LedgerRecord:
    """추적 원장의 변경 스트림이 싣는 사건 하나이며 칸의 이름은 계약의 토픽 선언이 갖는다."""

    id: str
    seq: int | None
    user_id: str
    task_id: str
    kind: str
    occurred_at: datetime
    payload: JsonObject


def parse_ledger_record(value: JsonValue) -> LedgerRecord | None:
    """식별자와 태스크와 종류와 시각이 없으면 읽지 못한 것으로 본다."""
    if not isinstance(value, dict):
        return None
    identifier = _string(value.get("id"))
    user_id = _string(value.get("user_id"))
    task_id = _string(value.get("task_id"))
    kind = _string(value.get("kind"))
    occurred_at = _string(value.get("occurred_at"))
    if identifier is None or user_id is None or task_id is None or kind is None or occurred_at is None:
        return None
    try:
        occurred = parse_instant(occurred_at)
    except ValueError:
        return None
    return LedgerRecord(
        id=identifier,
        seq=_seq(value.get("seq")),
        user_id=user_id,
        task_id=task_id,
        kind=kind,
        occurred_at=occurred,
        payload=_payload(value.get("payload")),
    )


def _string(value: JsonValue) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return None


def _seq(value: JsonValue) -> int | None:
    """원장이 매긴 일련번호는 정수가 아닌 문자열로 실려 올 수 있다."""
    found = _string(value)
    if found is None:
        return None
    try:
        return int(found)
    except ValueError:
        return None


def _payload(value: JsonValue) -> JsonObject:
    """payload 는 객체이거나 JSON 문자열로 실려 올 수 있다."""
    if isinstance(value, str):
        try:
            parsed: JsonValue = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return value if isinstance(value, dict) else {}


class RecipeProjection:
    """레시피 주입 사건을 적용 이력 행으로 투영하며 고르지 않은 종류는 넘긴다."""

    def __init__(self, applications: RecipeApplicationStore) -> None:
        self._applications = applications

    async def handle(self, record: LedgerRecord) -> None:
        """고른 사건 하나를 행 하나로 옮기며 만들 수 없거나 이미 있으면 아무것도 하지 않는다."""
        if record.kind != RECIPE_INJECTED_EVENT_KIND:
            return
        application_id = _string(record.payload.get("applicationId"))
        recipe_id = _string(record.payload.get("recipeId"))
        # 행의 식별자와 대상이 없으면 만들 수 있는 행이 없다.
        if application_id is None or recipe_id is None:
            return
        opened = await self._applications.find_by_task(record.task_id)
        # 같은 레시피를 한 태스크에서 여러 번 가져와도 성공률의 분모가 부풀지 않게 한다.
        if any(one.recipe_id == recipe_id for one in opened):
            return
        await self._applications.upsert(_to_application(application_id, recipe_id, record))


def _to_application(application_id: str, recipe_id: str, record: LedgerRecord) -> RecipeApplication:
    return RecipeApplication(
        id=application_id,
        user_id=record.user_id,
        recipe_id=recipe_id,
        task_id=record.task_id,
        injected_via=_injected_via(record.payload.get("injectedVia")),
        # 행이 쓰인 시각이 아니라 사건이 일어난 시각을 적어 이력의 시각이 배출 지연을 따라가지 않게 한다.
        created_at=record.occurred_at,
        outcome=None,
        note=None,
        anchor_event_id=record.id,
        anchor_seq=record.seq,
    )


def _injected_via(value: JsonValue) -> str:
    """사건이 주입 경로를 싣지 않았으면 도구가 가져온 것으로 본다."""
    return RECIPE_INJECTED_VIA_MANUAL if value == RECIPE_INJECTED_VIA_MANUAL else RECIPE_INJECTED_VIA_PULL
