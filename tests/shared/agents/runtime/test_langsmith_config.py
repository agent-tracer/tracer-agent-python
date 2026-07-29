from __future__ import annotations

import os

from tracer_agent.shared.config import Settings


def test_langsmith_bootstrap은_꺼짐과_원문_비공개를_기본으로_강제한다(monkeypatch) -> None:
    for key in ("LANGSMITH_TRACING", "LANGSMITH_HIDE_INPUTS", "LANGSMITH_HIDE_OUTPUTS", "MONITOR_PROFILE"):
        monkeypatch.delenv(key, raising=False)

    Settings().configure_langsmith()

    assert Settings().langsmith_tracing is False
    assert Settings().monitor_profile == "prd"
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGSMITH_HIDE_INPUTS"] == "true"
    assert os.environ["LANGSMITH_HIDE_OUTPUTS"] == "true"


def test_local_프로파일은_원문_공개를_기본값으로_연다(monkeypatch) -> None:
    for key in ("LANGSMITH_HIDE_INPUTS", "LANGSMITH_HIDE_OUTPUTS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MONITOR_PROFILE", "local")

    Settings().configure_langsmith()

    assert os.environ["LANGSMITH_HIDE_INPUTS"] == "false"
    assert os.environ["LANGSMITH_HIDE_OUTPUTS"] == "false"


def test_prd_프로파일은_외부_원문_공개값을_덮어쓴다(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_HIDE_INPUTS", "false")
    monkeypatch.setenv("LANGSMITH_HIDE_OUTPUTS", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-test")

    Settings().configure_langsmith()

    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_HIDE_INPUTS"] == "true"
    assert os.environ["LANGSMITH_HIDE_OUTPUTS"] == "true"


def test_optional_환경값이_없으면_이전_process값을_제거한다(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "stale-key")
    monkeypatch.setenv("LANGSMITH_WORKSPACE_ID", "stale-workspace")

    Settings(langsmith_api_key=None, langsmith_workspace_id=None).configure_langsmith()

    assert "LANGSMITH_API_KEY" not in os.environ
    assert "LANGSMITH_WORKSPACE_ID" not in os.environ
