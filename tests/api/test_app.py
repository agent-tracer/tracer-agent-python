"""접수 앱이 헬스체크를 내는지 검증한다."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tracer_agent.api import app as app_module


def test_헬스체크가_정상_응답을_낸다() -> None:
    with TestClient(app_module.create_app()) as client:
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}
