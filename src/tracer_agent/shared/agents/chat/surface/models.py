"""대화 창구가 받는 본문의 와이어 계약이다."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...shared.models import TrimmedStr


class ThreadTitleBody(BaseModel):
    """스레드를 열거나 개명할 때 받는 본문이다."""

    model_config = ConfigDict(extra="ignore")

    title: TrimmedStr = Field(min_length=1, max_length=200)


class ProposeToolBody(BaseModel):
    """확인이 필요한 도구 호출 하나를 대기 행에 세울 때 받는 본문이다."""

    model_config = ConfigDict(extra="ignore")

    toolName: TrimmedStr = Field(min_length=1, max_length=100)
    args: dict[str, object] = Field(default_factory=dict)


class DecideToolBody(BaseModel):
    """대기 중인 도구 호출 하나를 해소할 때 받는 본문이다."""

    model_config = ConfigDict(extra="ignore")

    decision: Literal["approve", "reject"]


class RememberFactBody(BaseModel):
    """사용자 장기기억 한 줄을 적을 때 받는 본문이다."""

    model_config = ConfigDict(extra="ignore")

    content: str = Field(min_length=1, max_length=4000)


class DraftCheckpointBody(BaseModel):
    """실행기가 누적 답변을 통지할 때 받는 본문이다."""

    model_config = ConfigDict(extra="ignore")

    token: TrimmedStr = Field(min_length=1, max_length=200)
    attempt: int = Field(ge=1)
    draftSeq: int = Field(ge=1)
    text: str = Field(max_length=200_000)
