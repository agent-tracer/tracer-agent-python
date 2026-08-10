"""프로젝터가 읽는 토픽과 소비자 그룹의 이름을 계약에서 읽는다."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from ..shared.axis import AGENT_BACKEND
from ..shared.contract_root import CONTRACT_ROOT

_TOPICS_PATH = CONTRACT_ROOT / "wire" / "topics.json"


@lru_cache(maxsize=1)
def _ledger_events() -> dict[str, Any]:
    declared = json.loads(_TOPICS_PATH.read_text(encoding="utf-8"))
    events: dict[str, Any] = declared["ledgerEvents"]
    return events


def ledger_events_topic() -> str:
    """추적이 소유한 사건 원장의 변경 스트림이며 이 축은 독자일 뿐 발행자가 아니다."""
    return str(_ledger_events()["name"])


def ledger_events_consumer_group() -> str:
    """이 축만 쓰는 소비자 그룹의 이름이며 계약의 template 에 자기 축을 넣어 만든다."""
    declared = _ledger_events()["consumerGroups"]["agentProjector"]
    return str(declared["template"]).replace(str(declared["placeholder"]), AGENT_BACKEND)
