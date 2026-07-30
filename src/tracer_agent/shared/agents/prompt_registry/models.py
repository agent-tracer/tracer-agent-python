"""등록 창구가 받는 요청 본문의 wire 계약을 소유한다."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PRODUCTION_CHANNEL = "production"
STAGING_CHANNEL = "staging"
CODE_DEFAULT_ORIGIN = "code-default"
DATABASE_AUTHORED_ORIGIN = "database-authored"
DATABASE_OVERRIDE_SOURCE = "database-override"

# prompt_fragment_versions.origin 이 담는 값 전부이며 후보는 계약의 조각 레지스트리가 소유한다.
VERSION_ORIGINS: frozenset[str] = frozenset({CODE_DEFAULT_ORIGIN, DATABASE_AUTHORED_ORIGIN})

# 배포 프로파일마다 실행에 쓰는 조각 채널이며 값은 계약의 조각 레지스트리 선언이 소유한다.
PROFILE_CHANNELS: Mapping[str, str] = MappingProxyType({"local": STAGING_CHANNEL, "prd": PRODUCTION_CHANNEL})


def channel_for_profile(profile: str) -> str:
    """이 배포가 실행에 쓸 조각 채널이며 선언되지 않은 프로파일은 거절한다."""
    channel = PROFILE_CHANNELS.get(profile)
    if channel is None:
        raise ValueError(f"prompt-fragment.unknown-profile:{profile}")
    return channel


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
