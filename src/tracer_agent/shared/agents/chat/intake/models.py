"""브라우저가 보내는 접수 요청과 그 응답의 와이어 계약이다."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from ...shared.models import Language, TrimmedStr

ModelName = Annotated[TrimmedStr, Field(min_length=1)]


class PostMessagePayload(BaseModel):
    """브라우저가 보내는 접수 본문이며 계약이 정한 제약을 건다."""

    model_config = ConfigDict(extra="ignore")

    clientRequestId: TrimmedStr = Field(min_length=1, max_length=200)
    content: TrimmedStr = Field(min_length=1, max_length=10_000)
    model: ModelName | None = None
    language: Language | None = None

    def input_hash(self) -> str:
        """같은 요청 식별자가 같은 입력인지 가릴 해시를 두 구현체가 같은 바이트로 만든다."""
        payload = {
            "content": self.content,
            "model": self.model,
            "language": self.language,
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()
