"""프래그먼트 override와 최종 prompt hash의 언어 중립 wire 모델을 소유한다."""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResolvedPromptFragmentDTO(BaseModel):
    """코드 scaffold의 한 slot에 실행 수명 동안 고정된 프래그먼트다."""

    model_config = ConfigDict(extra="forbid")
    templateKey: str = Field(min_length=1)
    fragmentSlot: str = Field(min_length=1)
    definitionKey: str = Field(min_length=1)
    codeName: str = Field(min_length=1)
    backend: Literal["python"]
    language: str = Field(min_length=1)
    versionId: str = Field(min_length=1)
    semanticVersion: str = Field(min_length=1)
    content: str
    contentHash: str = Field(pattern=r"^[0-9a-f]{64}$")
    placeholders: list[str]
    toolContractVersion: str = Field(min_length=1)
    outputSchemaVersion: str = Field(min_length=1)
    source: Literal["code-default", "database-override"]


class ResolvedPromptTemplateHashDTO(BaseModel):
    """하나의 완성된 prompt template 본문 hash다."""

    model_config = ConfigDict(extra="forbid")
    templateKey: str = Field(min_length=1)
    contentHash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResolvedFragmentsIntegrityDTO(BaseModel):
    """평가나 override 실행이 제공하는 프래그먼트와 완성 prompt hash 계약이다."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["resolved-fragments"]
    fragments: list[ResolvedPromptFragmentDTO] = Field(min_length=1)
    resolvedPromptHash: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolvedPromptHashes: list[ResolvedPromptTemplateHashDTO] = Field(min_length=1)

    @model_validator(mode="after")
    def identities_are_unique_and_sorted(self) -> ResolvedFragmentsIntegrityDTO:
        """slot 중복과 정렬되지 않은 template hash 계약을 거부한다."""
        slots = [(item.templateKey, item.fragmentSlot) for item in self.fragments]
        if len(slots) != len(set(slots)):
            raise ValueError("resolved fragment bindings must be unique")
        keys = [item.templateKey for item in self.resolvedPromptHashes]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("resolved prompt hashes must be sorted and unique")
        return self


class FullPromptIntegrityDTO(BaseModel):
    """조각으로 나뉘지 않은 프롬프트 전체를 그대로 검증하는 모드다."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["full-prompt"]


PromptIntegrityDTO = FullPromptIntegrityDTO | ResolvedFragmentsIntegrityDTO

# 봉투의 promptIntegrity.mode 로 오가는 값이며 이름은 계약의 조각 무결성 선언이 소유한다.
PROMPT_INTEGRITY_MODES: Final = frozenset({"full-prompt", "resolved-fragments"})
