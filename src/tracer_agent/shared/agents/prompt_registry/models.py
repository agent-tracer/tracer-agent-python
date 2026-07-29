"""등록 창구가 받는 요청 본문의 wire 계약을 소유한다."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PRODUCTION_CHANNEL = "production"
CODE_DEFAULT_ORIGIN = "code-default"
DATABASE_OVERRIDE_SOURCE = "database-override"

# 두 구현체가 같은 본문을 실행하도록 어느 배포 프로파일도 production 채널 하나만 본다.
_PROFILE_CHANNELS: Mapping[str, str] = MappingProxyType(
    {"local": PRODUCTION_CHANNEL, "prd": PRODUCTION_CHANNEL}
)


def channel_for_profile(profile: str) -> str:
    """배포 프로파일이 볼 채널을 정하며 정해진 것이 없으면 production을 본다."""
    return _PROFILE_CHANNELS.get(profile, PRODUCTION_CHANNEL)


class FragmentBindingInput(BaseModel):
    """조각 하나가 채우는 템플릿 자리다."""

    model_config = ConfigDict(extra="forbid")
    templateKey: str = Field(min_length=1)
    fragmentSlot: str = Field(min_length=1)


class FragmentManifestEntry(BaseModel):
    """워커가 파일에 담아 올린 조각 정의 하나와 그 코드 기본값이다."""

    model_config = ConfigDict(extra="forbid")
    backend: Literal["python", "claude-sdk"]
    agentName: str = Field(min_length=1)
    language: str = Field(min_length=1)
    codeName: str = Field(min_length=1)
    definitionKey: str = Field(min_length=1)
    fragmentName: str = Field(min_length=1)
    defaultVersion: str = Field(min_length=1)
    defaultContent: str = Field(min_length=1)
    toolContractVersion: str = Field(min_length=1)
    outputSchemaVersion: str = Field(min_length=1)
    bindings: list[FragmentBindingInput] = Field(min_length=1)


class RegisterAndResolveFragmentsPayload(BaseModel):
    """부팅 하나가 올린 조각 묶음 전체다."""

    model_config = ConfigDict(extra="forbid")
    profile: str = Field(min_length=1)
    manifest: list[FragmentManifestEntry]


class PromptVersionInput(BaseModel):
    """등록하려는 프롬프트 본문 판 하나다."""

    model_config = ConfigDict(extra="forbid")
    semanticVersion: str = Field(min_length=1)
    content: str = Field(min_length=1)
    contentHash: str | None = Field(default=None, min_length=1)
    toolContractVersion: str = Field(min_length=1)
    outputSchemaVersion: str = Field(min_length=1)


class RegisterPromptPayload(BaseModel):
    """워커가 파일에 담아 올린 프롬프트 정의 하나와 그 판이다."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    agentName: str = Field(min_length=1)
    language: str = Field(min_length=1)
    version: PromptVersionInput
