"""진행 중인 프로세스가 자기 라우팅 표를 계약과 대조할 수 있는 어휘로 내는지 검증한다."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from tracer_agent.api.surface import served_routes

PATH = "/internal/surface"
PROBE_PATH = "/internal/probe"


def test_등록된_창구를_메서드와_경로로_낸다(client: TestClient) -> None:
    res = client.get(PATH)

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    routes = body["data"]["routes"]
    assert {"method": "POST", "path": "/api/agent/jobs"} in routes
    assert {"method": "GET", "path": "/api/agent/jobs/{execution_id}/steps"} in routes
    assert {"method": "GET", "path": "/health"} in routes
    assert {"method": "GET", "path": PATH} in routes


def test_라우팅이_스스로_붙이는_메서드는_싣지_않는다(client: TestClient) -> None:
    routes = client.get(PATH).json()["data"]["routes"]

    assert {route["method"] for route in routes}.isdisjoint({"HEAD", "OPTIONS"})


def test_표면은_메서드와_경로의_사전순이다(client: TestClient) -> None:
    routes = client.get(PATH).json()["data"]["routes"]

    assert routes == sorted(routes, key=lambda route: (route["method"], route["path"]))


def test_라우터로_조립한_경로도_싣는다() -> None:
    router = APIRouter()
    router.get(PROBE_PATH)(lambda: {"status": "ok"})
    application = FastAPI()
    application.include_router(router)

    assert {"method": "GET", "path": PROBE_PATH} in served_routes(application)
