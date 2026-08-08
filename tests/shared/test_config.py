"""원장과 checkpoint 접속 문자열이 같은 계정으로 서로 다른 스키마를 보는지, 풀의 조정치가 설정에 있는지 검증한다."""

import pytest

from tracer_agent.shared.config import Settings


def _settings() -> Settings:
    return Settings(
        agent_db_host="db",
        agent_db_port=5432,
        agent_db_name="agent",
        agent_db_user="app",
        agent_db_password="app-secret",
    )


def test_원장에_앱_계정으로_연결한다() -> None:
    assert _settings().agent_dsn() == "postgresql://app:app-secret@db:5432/agent"


def test_checkpoint가_구현체_전용_스키마를_본다() -> None:
    assert _settings().checkpoint_dsn() == (
        "postgresql://app:app-secret@db:5432/agent?options=-csearch_path%3Dagent_langgraph"
    )


_POOL_ENV = {
    "AGENT_DB_POOL_MIN_SIZE": "agent_db_pool_min_size",
    "AGENT_DB_POOL_MAX_SIZE": "agent_db_pool_max_size",
    "AGENT_DB_ACQUIRE_TIMEOUT_S": "agent_db_acquire_timeout_s",
}


def test_배포가_풀의_조정치를_주지_않으면_선언된_기본값을_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in _POOL_ENV:
        monkeypatch.delenv(variable, raising=False)

    settings = Settings()

    assert settings.agent_db_pool_min_size == 1
    assert settings.agent_db_pool_max_size == 8
    assert settings.agent_db_acquire_timeout_s == 5.0


def test_배포가_준_풀의_조정치를_그대로_읽는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_DB_POOL_MIN_SIZE", "2")
    monkeypatch.setenv("AGENT_DB_POOL_MAX_SIZE", "32")
    monkeypatch.setenv("AGENT_DB_ACQUIRE_TIMEOUT_S", "1.5")

    settings = Settings()

    assert settings.agent_db_pool_min_size == 2
    assert settings.agent_db_pool_max_size == 32
    assert settings.agent_db_acquire_timeout_s == 1.5
