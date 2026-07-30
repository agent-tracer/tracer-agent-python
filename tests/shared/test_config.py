"""원장과 checkpoint 접속 문자열이 같은 계정으로 서로 다른 스키마를 보는지 검증한다."""

from tracer_agent.shared.config import Settings


def _settings() -> Settings:
    return Settings(
        tracer_db_host="db",
        tracer_db_port=5432,
        tracer_db_name="tracer",
        tracer_db_user="app",
        tracer_db_password="app-secret",
    )


def test_원장에_앱_계정으로_연결한다() -> None:
    assert _settings().tracer_dsn() == "postgresql://app:app-secret@db:5432/tracer"


def test_checkpoint가_구현체_전용_스키마를_본다() -> None:
    assert _settings().checkpoint_dsn() == (
        "postgresql://app:app-secret@db:5432/tracer?options=-csearch_path%3Dagent_langgraph"
    )
