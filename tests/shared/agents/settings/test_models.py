"""설정 가림 규칙과 모델 카탈로그가 계약이 정한 값을 내는지 검증한다."""

from __future__ import annotations

from datetime import UTC, datetime

from tracer_agent.shared.agents.settings.catalog import knows_model, model_options
from tracer_agent.shared.agents.settings.models import (
    is_sensitive_setting_key,
    is_setting_key,
    mask_setting_value,
    setting_view,
)

UPDATED_AT = datetime(2026, 7, 30, tzinfo=UTC)


def test_자격_키의_값은_끝_네_자만_남기고_가린다() -> None:
    assert mask_setting_value("anthropic.api_key", "sk-ant-abcdefgh1234") == "••••••••1234"


def test_자격_키의_값이_네_자_이하이면_길이만큼만_가린다() -> None:
    assert mask_setting_value("anthropic.api_key", "abc") == "•••"


def test_자격이_아닌_키의_값은_그대로_낸다() -> None:
    assert mask_setting_value("anthropic.model", "claude-sonnet-5") == "claude-sonnet-5"


def test_자격_키만_민감한_것으로_센다() -> None:
    assert is_sensitive_setting_key("anthropic.api_key")
    assert not is_sensitive_setting_key("claude.outputLanguage")


def test_카탈로그_밖의_키는_쓸_수_없다() -> None:
    assert not is_setting_key("ruleGen.maxRulesPerTask")
    assert not is_setting_key("anthropic.apiKey")


def test_저장된_설정을_가린_값과_시각_문자열로_성형한다() -> None:
    view = setting_view("anthropic.api_key", "sk-ant-abcdefgh1234", UPDATED_AT)

    assert view == {
        "key": "anthropic.api_key",
        "maskedValue": "••••••••1234",
        "hasValue": True,
        "updatedAt": "2026-07-30T00:00:00.000Z",
    }


def test_고를_수_있는_모델을_이름_오름차순으로_낸다() -> None:
    ids = [option.id for option in model_options()]

    assert ids == sorted(ids)
    assert "claude-sonnet-5" in ids


def test_어느_잡도_허용하지_않는_모델은_고를_수_없다() -> None:
    # 설정이 하나뿐이라 어느 한 종류라도 막는 모델을 내면 고른 값이 그 종류에 걸리지 않는다.
    ids = [option.id for option in model_options()]

    assert "claude-opus-5" not in ids
    assert knows_model("claude-opus-5") is False


def test_단가를_아는_모델만_고를_수_있다() -> None:
    assert knows_model("claude-haiku-4-5")
    assert not knows_model("claude-unknown")
