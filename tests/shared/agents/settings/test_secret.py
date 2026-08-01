"""자격 설정의 저장 형식과 키 유도가 계약대로인지 검증한다."""

from __future__ import annotations

import pytest

from tracer_agent.shared.agents.settings.secret import (
    MalformedSecret,
    SecretKeyMismatch,
    SecretKeyMissing,
    SettingCipher,
    is_encrypted_secret,
)
from tracer_agent.shared.config import MonitorProfile

# aes-256-gcm과 12바이트 초기벡터로 sha256("monitor-dev-key") 키가 "sk-ant-secret-9876"을 감춘 값이다.
STORED = "enc:v1:AAECAwQFBgcICQoL:OHfmeNGeJOHTUug+do+pLA==:caa1WpXO3GvGz1+jdugIB/WK"
PLAINTEXT = "sk-ant-secret-9876"


def test_다른_구현체가_감춘_값을_같은_평문으로_되돌린다() -> None:
    assert SettingCipher(None, "local").decrypt(STORED) == PLAINTEXT


def test_감춘_값을_다시_읽으면_같은_평문이_나온다() -> None:
    cipher = SettingCipher("team-key", "prd")

    assert cipher.decrypt(cipher.encrypt(PLAINTEXT)) == PLAINTEXT


def test_감춘_값을_판과_초기벡터와_인증태그와_암호문으로_적는다() -> None:
    stored = SettingCipher("team-key", "prd").encrypt(PLAINTEXT)

    assert stored.startswith("enc:v1:")
    assert len(stored[len("enc:v1:") :].split(":")) == 3


def test_다른_키로_감춘_값은_풀리지_않는다() -> None:
    with pytest.raises(SecretKeyMismatch):
        SettingCipher("other-key", "prd").decrypt(STORED)


def test_암호_형식이_아닌_값은_풀지_않는다() -> None:
    with pytest.raises(MalformedSecret):
        SettingCipher(None, "local").decrypt("plain-value")


def test_prd는_키를_주지_않으면_자격을_다루지_않는다() -> None:
    with pytest.raises(SecretKeyMissing):
        SettingCipher(None, "prd").decrypt(STORED)


def test_설정이_주는_프로파일로도_같은_판정을_받는다() -> None:
    with pytest.raises(SecretKeyMissing):
        SettingCipher(None, MonitorProfile.PRD).decrypt(STORED)

    assert SettingCipher(None, MonitorProfile.LOCAL).decrypt(STORED) == PLAINTEXT


def test_암호_형식인지를_판_접두사로_가린다() -> None:
    assert is_encrypted_secret(STORED)
    assert not is_encrypted_secret("claude-sonnet-5")
