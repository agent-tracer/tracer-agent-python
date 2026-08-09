"""정리 제안 창구가 계약이 정한 순서와 보상과 거절을 내는지 검증한다(네트워크 없음)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.support.recipes import RecordingArchiver
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.shared.tracer_window import UpstreamRejected

NOW = "2026-01-01T00:00:00.000000"
OBSERVED = "2026-01-01T00:01:00.000000"
USER = "local"


def _seed(
    store: SqliteLedgerSql,
    suggestion_id: str,
    status: str,
    *,
    user_id: str = USER,
    task_id: str = "task-1",
    observed: str | None = OBSERVED,
) -> None:
    store.seed(
        "task_cleanup_suggestions",
        [
            {
                "id": suggestion_id,
                "user_id": user_id,
                "job_id": "job-1",
                "task_id": task_id,
                "kind": "archive",
                "rationale": "사건이 오래 없다",
                "status": status,
                "created_at": NOW,
                "observed_last_event_at": observed,
            }
        ],
    )


class Test목록:
    def test_상태를_싣지_않으면_선언_순서로_이어_붙인다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        _seed(store, "s-dismissed", "dismissed", task_id="task-2")
        _seed(store, "s-pending", "pending")

        body = client.get("/api/agent/cleanup/suggestions").json()

        assert [one["id"] for one in body["data"]["suggestions"]] == ["s-pending", "s-dismissed"]

    def test_대기_행은_태스크와_종류의_쌍으로_한_벌만_남긴다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        # 유일 색인이 실물에서 이 상태를 막으므로 이 시험은 목록이 스스로 접는지만 본다.
        store.raw.execute("DROP INDEX cleanup_pending_task_kind_unique")
        _seed(store, "s1", "pending")
        _seed(store, "s2", "pending")

        body = client.get("/api/agent/cleanup/suggestions").json()

        assert [one["id"] for one in body["data"]["suggestions"]] == ["s1"]

    def test_다른_상태의_행은_중복_제거_대상이_아니다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        _seed(store, "s1", "dismissed")
        _seed(store, "s2", "dismissed")

        body = client.get("/api/agent/cleanup/suggestions").json()

        assert len(body["data"]["suggestions"]) == 2

    def test_남의_제안은_목록에_실리지_않는다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        _seed(store, "s1", "pending", user_id="other")

        assert client.get("/api/agent/cleanup/suggestions").json()["data"]["suggestions"] == []

    def test_어휘에_없는_상태는_계약이_정한_400_으로_거절한다(self, client: TestClient) -> None:
        res = client.get("/api/agent/cleanup/suggestions", params={"status": "unknown"})

        assert res.status_code == 400
        assert res.json()["error"]["code"] == "validation_error"


class Test수용:
    def test_수용을_적은_뒤_관측_시각을_조건으로_보관을_요청한다(
        self, client: TestClient, store: SqliteLedgerSql, task_archiver: RecordingArchiver
    ) -> None:
        _seed(store, "s1", "pending")

        body = client.post("/api/agent/cleanup/suggestions/s1/accept").json()["data"]

        assert body["suggestion"]["status"] == "accepted"
        assert body["suggestion"]["resolvedAt"] is not None
        assert [call[:2] for call in task_archiver.calls] == [(USER, "task-1")]
        assert task_archiver.calls[0][2] is not None

    def test_보관을_거절하면_수용을_되돌리고_같은_코드를_낸다(
        self, client: TestClient, store: SqliteLedgerSql, task_archiver: RecordingArchiver
    ) -> None:
        task_archiver.rejection = UpstreamRejected(
            409, "cleanup.stale", "Task has activity since the suggestion observed it"
        )
        _seed(store, "s1", "pending")

        res = client.post("/api/agent/cleanup/suggestions/s1/accept")

        assert res.status_code == 409
        assert res.json()["error"]["code"] == "cleanup.stale"
        row = store.rows("task_cleanup_suggestions")[0]
        assert row["status"] == "pending"
        assert row["resolved_at"] is None

    def test_이미_수용된_제안은_원장을_바꾸지_않고_보관만_다시_밟는다(
        self, client: TestClient, store: SqliteLedgerSql, task_archiver: RecordingArchiver
    ) -> None:
        _seed(store, "s1", "accepted")

        body = client.post("/api/agent/cleanup/suggestions/s1/accept").json()["data"]

        assert body["suggestion"]["status"] == "accepted"
        assert store.rows("task_cleanup_suggestions")[0]["resolved_at"] is None
        assert len(task_archiver.calls) == 1

    def test_기각된_제안은_수용하지_못한다(
        self, client: TestClient, store: SqliteLedgerSql, task_archiver: RecordingArchiver
    ) -> None:
        _seed(store, "s1", "dismissed")

        res = client.post("/api/agent/cleanup/suggestions/s1/accept")

        assert res.status_code == 409
        assert res.json()["error"]["code"] == "cleanup.not-pending"
        assert task_archiver.calls == []

    def test_남의_제안은_없는_것과_같은_거절을_낸다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        _seed(store, "s1", "pending", user_id="other")

        res = client.post("/api/agent/cleanup/suggestions/s1/accept")

        assert res.status_code == 404
        assert res.json()["error"] == {
            "code": "not_found",
            "message": "Cleanup suggestion not found",
        }


class Test기각:
    def test_해소_시각을_적고_추적을_부르지_않는다(
        self, client: TestClient, store: SqliteLedgerSql, task_archiver: RecordingArchiver
    ) -> None:
        _seed(store, "s1", "pending")

        body = client.post("/api/agent/cleanup/suggestions/s1/dismiss").json()["data"]

        assert body["suggestion"]["status"] == "dismissed"
        assert body["suggestion"]["resolvedAt"] is not None
        assert task_archiver.calls == []

    def test_대기_중이_아닌_제안은_기각하지_못한다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        _seed(store, "s1", "accepted")

        res = client.post("/api/agent/cleanup/suggestions/s1/dismiss")

        assert res.status_code == 409
        assert res.json()["error"]["code"] == "cleanup.not-pending"

    def test_남의_제안은_없는_것과_같은_거절을_낸다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        _seed(store, "s1", "pending", user_id="other")

        assert client.post("/api/agent/cleanup/suggestions/s1/dismiss").status_code == 404
