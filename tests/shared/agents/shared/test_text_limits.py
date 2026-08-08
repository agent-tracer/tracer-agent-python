"""계약이 정한 글자 상한의 단위를 이 구현체가 코드포인트로 세는지 검증한다."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from tests.support.contract import shared_contract
from tracer_agent.shared.workflows.jobs_input import RecipeScanJobInput

# 기본 다국어 평면 밖의 글자라 코드포인트로는 1이고 UTF-16 코드 유닛으로는 2로 세인다.
ASTRAL = "🙂"


def _max_length(model: type[BaseModel], field: str) -> int:
    """상한을 세 번째로 적지 않도록 검증이 실제로 쓰는 값을 모델에서 그대로 꺼낸다."""
    return int(
        next(meta.max_length for meta in model.model_fields[field].metadata if hasattr(meta, "max_length"))
    )


def test_계약이_글자_상한의_단위를_코드포인트로_적는다() -> None:
    assert shared_contract("text.limits.json")["lengthUnit"] == "codePoint"


def test_코드_유닛으로_상한을_넘기는_글도_코드포인트로_상한_안이면_통과한다() -> None:
    limit = _max_length(RecipeScanJobInput, "taskId")
    value = ASTRAL * limit
    # 코드 유닛으로 세는 축이라면 이 값은 상한의 두 배라 거절된다.
    assert len(value.encode("utf-16-le")) // 2 == limit * 2

    assert RecipeScanJobInput(taskId=value).taskId == value


def test_코드포인트로_상한을_한_글자_넘기면_거절한다() -> None:
    limit = _max_length(RecipeScanJobInput, "taskId")

    with pytest.raises(ValidationError):
        RecipeScanJobInput(taskId=ASTRAL * (limit + 1))
