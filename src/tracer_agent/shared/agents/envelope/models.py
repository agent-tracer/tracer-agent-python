"""실행 봉투 창구가 받는 본문과 창구가 기대는 바깥 계약을 소유한다."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

API_KEY_SETTING = "anthropic.api_key"

MODEL_SETTING = "anthropic.model"

# 자격이 없어 실행을 세울 수 없다는 거절이며 접수와 봉투 창구가 같은 글자를 낸다.
CHAT_KEY_MISSING = (400, "chat.llm-key-missing", "Model credential is not configured")
JOB_KEY_MISSING = (400, "job.llm-key-missing", "Model credential is not configured")


class ModelCredentialSource(Protocol):
    """사용자 설정에 저장된 모델 자격과 고른 모델을 평문으로 내주는 창구다."""

    async def api_key(self, user_id: str) -> str | None:
        """그 사용자가 저장해 둔 모델 자격이며 없으면 None이다."""
        ...

    async def chosen_model(self, user_id: str) -> str | None:
        """그 사용자가 고른 모델이며 고르지 않았으면 None이다."""
        ...


class JobEnvelopeBody(BaseModel):
    """잡 봉투는 접수가 원장 행을 만들기 전에도 발급되므로 사용자만 받는다."""

    model_config = ConfigDict(extra="forbid")

    userId: str = Field(min_length=1)
