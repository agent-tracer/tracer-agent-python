"""에이전트 DB capability별 접속 문자열이 서로 섞이지 않는지 검증한다."""

from tracer_agent.shared.config import Settings


def test_읽기와_checkpoint가_서로_다른_제한_역할을_쓴다() -> None:
    settings = Settings(
        tracer_db_host="db",
        tracer_db_port=5432,
        tracer_db_name="tracer",
        agent_db_reader_user="reader",
        agent_db_reader_password="read-secret",
        agent_db_checkpoint_user="checkpoint",
        agent_db_checkpoint_password="write-secret",
        agent_db_execution_user="execution",
        agent_db_execution_password="ledger-secret",
    )

    assert settings.tracer_dsn() == "postgresql://reader:read-secret@db:5432/tracer"
    assert settings.checkpoint_dsn() == "postgresql://checkpoint:write-secret@db:5432/tracer"
    assert settings.execution_dsn() == "postgresql://execution:ledger-secret@db:5432/tracer"
