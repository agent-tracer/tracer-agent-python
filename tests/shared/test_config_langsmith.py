import pytest
from pydantic import ValidationError

from tracer_agent.shared.config import Settings


def test_langsmith는_환경설정이_없으면_꺼져_있다(monkeypatch) -> None:
    for key in (
        "LANGSMITH_TRACING",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "LANGSMITH_ENDPOINT",
        "LANGSMITH_WORKSPACE_ID",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = Settings()

    assert settings.langsmith_tracing is False
    assert settings.langsmith_api_key is None
    assert settings.langsmith_project == "agent-tracer-local"


def test_langsmith_opt_in_환경설정을_typed_value로_읽는다(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-test")
    monkeypatch.setenv("LANGSMITH_PROJECT", "agent-tracer-evaluation")
    monkeypatch.setenv("LANGSMITH_WORKSPACE_ID", "workspace-1")

    settings = Settings()

    assert settings.langsmith_tracing is True
    assert settings.langsmith_api_key is not None
    assert settings.langsmith_api_key.get_secret_value() == "lsv2-test"
    assert settings.langsmith_project == "agent-tracer-evaluation"
    assert settings.langsmith_workspace_id == "workspace-1"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"langsmith_api_key": None}, "LANGSMITH_API_KEY"),
        ({"langsmith_endpoint": "  "}, "LANGSMITH_ENDPOINT"),
        ({"langsmith_project": ""}, "LANGSMITH_PROJECT"),
    ],
)
def test_langsmith활성화는_완전한_목적지_설정을_요구한다(changes: dict[str, object], message: str) -> None:
    values: dict[str, object] = {
        "langsmith_tracing": True,
        "langsmith_api_key": "lsv2-test",
        "langsmith_endpoint": "https://api.smith.langchain.com",
        "langsmith_project": "evaluation",
    }
    values.update(changes)

    with pytest.raises(ValidationError, match=message):
        Settings(**values)  # type: ignore[arg-type]
