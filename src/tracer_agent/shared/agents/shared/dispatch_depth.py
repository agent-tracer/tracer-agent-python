"""조율자가 배정 하나에 고르는 조사 깊이와 그 깊이가 받는 몫."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, get_args

# 모델은 몫의 수치가 아니라 이 셋 중 하나를 고르고 나눗셈은 코드가 소유한다.
DispatchDepth = Literal["shallow", "normal", "deep"]
DEPTH_LEVELS: tuple[str, ...] = get_args(DispatchDepth)

_CONTRACT_ROOT = Path(__file__).resolve().parents[5] / "contract"


@lru_cache(maxsize=4)
def depth_shares(agent_id: str, key: str) -> Mapping[str, int]:
    """깊이마다의 몫을 계약에서 읽으며 선언이 이 어휘와 어긋나면 거절한다."""
    declared: Any = json.loads((_CONTRACT_ROOT / "agent" / agent_id / "tool.json").read_text("utf-8"))
    shares = {str(level): int(share) for level, share in declared["orchestration"][key].items()}
    if set(shares) != set(DEPTH_LEVELS):
        raise ValueError(f"{agent_id}.{key} declares depths {sorted(shares)}")
    return shares


def depth_share(agent_id: str, key: str, depth: str) -> int:
    """배정 하나가 예산 배분에서 받는 몫이다."""
    return depth_shares(agent_id, key)[depth]
