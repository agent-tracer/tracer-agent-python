"""사용자 장기기억 창구가 사용자마다 갈린 사실을 읽고 쓰는지 검증한다."""

from __future__ import annotations

from fastapi.testclient import TestClient

MEMORIES = "/api/v1/chat/memories"


class Test장기기억:
    def test_적은_사실을_되읽고_같은_키는_덮어쓴다(self, client: TestClient) -> None:
        client.put(f"{MEMORIES}/lang", json={"content": "한국어를 쓴다"})
        client.put(f"{MEMORIES}/lang", json={"content": "영어도 쓴다"})

        facts = client.get(MEMORIES).json()["data"]["facts"]

        assert facts == [{"key": "lang", "content": "영어도 쓴다", "updatedAt": facts[0]["updatedAt"]}]

    def test_적은_사실은_상태를_함께_낸다(self, client: TestClient) -> None:
        res = client.put(f"{MEMORIES}/lang", json={"content": "한국어를 쓴다"})

        assert res.status_code == 200
        assert res.json()["data"] == {"key": "lang", "content": "한국어를 쓴다", "status": "remembered"}

    def test_기억은_사용자마다_갈린다(self, client: TestClient) -> None:
        client.put(f"{MEMORIES}/lang", json={"content": "한국어를 쓴다"})

        facts = client.get(MEMORIES, headers={"x-monitor-user": "u2"}).json()["data"]["facts"]

        assert facts == []

    def test_공백뿐인_사실은_적지_않는다(self, client: TestClient) -> None:
        res = client.put(f"{MEMORIES}/lang", json={"content": "   "})

        assert res.status_code == 400
        assert res.json()["error"]["code"] == "validation_error"
