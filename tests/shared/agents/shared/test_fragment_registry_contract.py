"""조각 채널과 이름 규칙을 계약이 적은 그대로 쓰는지 검증한다."""

from __future__ import annotations

import pytest

from tests.support.contract import shared_contract
from tracer_agent.shared.agents.prompt_registry.models import (
    PROFILE_CHANNELS,
    VERSION_ORIGINS,
    channel_for_profile,
)
from tracer_agent.worker.agents.shared.fragment_registry import fragment_code_name

_REGISTRY = shared_contract("prompt.fragment.registry.json")


def test_profile마다_계약이_정한_채널을_본다() -> None:
    assert dict(PROFILE_CHANNELS) == _REGISTRY["profileChannels"]


@pytest.mark.parametrize(("profile", "channel"), sorted(_REGISTRY["profileChannels"].items()))
def test_배포_프로파일이_계약이_적은_채널을_낸다(profile: str, channel: str) -> None:
    assert channel_for_profile(profile) == channel


def test_계약이_선언하지_않은_프로파일을_거절한다() -> None:
    with pytest.raises(ValueError, match="unknown-profile"):
        channel_for_profile("unknown")


def test_조각_판의_출처가_계약이_선언한_값과_같다() -> None:
    declared = set(_REGISTRY["versionOrigins"]) - {"meaning"}

    assert declared == VERSION_ORIGINS


def test_판이_어긋날_때_부팅을_끊기로_한_결정을_계약이_갖는다() -> None:
    assert _REGISTRY["drift"]["policy"] == "boot-fail"


def test_조각의_유일성이_backend를_포함한다() -> None:
    uniqueness = _REGISTRY["identity"]["uniqueness"]
    assert "backend" in uniqueness["prompt_fragment_definitions"]
    assert "backend" in uniqueness["prompt_fragment_bindings"]


def test_코드_이름이_구현체를_말하는_접두사를_달지_않는다() -> None:
    code_name = fragment_code_name("task-cleanup", "repairDirective")
    assert code_name == "TASK_CLEANUP_REPAIR_DIRECTIVE"
    assert not any(code_name.startswith(prefix) for prefix in _REGISTRY["identity"]["rejectedPrefixes"])
