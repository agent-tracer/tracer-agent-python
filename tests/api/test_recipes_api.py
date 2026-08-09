"""레시피 창구가 계약이 정한 판정과 순서와 거절을 내는지 검증한다(네트워크 없음)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.support.recipes import RecordingSearch, RecordingTaskReader
from tracer_agent.shared.agents.recipe.search import RecipeSearchHit
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql

NOW = "2026-01-01T00:00:00.000000"
USER = "local"


def _seed(
    store: SqliteLedgerSql,
    recipe_id: str,
    status: str,
    *,
    user_id: str = USER,
    parent: str | None = None,
    slices: list[dict[str, Any]] | None = None,
    deleted: bool = False,
) -> None:
    store.seed(
        "recipes",
        [
            {
                "id": recipe_id,
                "user_id": user_id,
                "status": status,
                "title": f"제목 {recipe_id}",
                "intent": "의도",
                "description": "설명",
                "summary_md": "- 절차",
                "request": "요청",
                "rev": 1,
                "parent_recipe_id": parent,
                "contributing_slices": slices or [],
                "created_at": NOW,
                "updated_at": NOW,
                "deleted_at": NOW if deleted else None,
            }
        ],
    )


def _application(store: SqliteLedgerSql, row_id: str, recipe_id: str, outcome: str | None) -> None:
    store.seed(
        "recipe_applications",
        [
            {
                "id": row_id,
                "user_id": USER,
                "recipe_id": recipe_id,
                "task_id": "task-1",
                "injected_via": "pull",
                "outcome": outcome,
                "created_at": NOW,
            }
        ],
    )


class Test목록:
    def test_상태를_싣지_않으면_선언_순서로_이어_붙인다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        _seed(store, "r-active", "active")
        _seed(store, "r-candidate", "candidate")

        body = client.get("/api/agent/recipes").json()

        assert [item["id"] for item in body["data"]["items"]] == ["r-candidate", "r-active"]

    def test_지운_레시피는_어느_상태로_물어도_나오지_않는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        _seed(store, "r-gone", "dismissed", deleted=True)

        body = client.get("/api/agent/recipes", params={"status": "dismissed"}).json()

        assert body["data"]["items"] == []

    def test_남의_레시피는_목록에_실리지_않는다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        _seed(store, "r-other", "active", user_id="other")

        assert client.get("/api/agent/recipes").json()["data"]["items"] == []

    def test_적용_이력을_집계해_통계를_함께_낸다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        _seed(store, "r1", "active")
        _application(store, "a1", "r1", "completed")
        _application(store, "a2", "r1", "abandoned")
        _application(store, "a3", "r1", None)

        stats = client.get("/api/agent/recipes").json()["data"]["items"][0]["stats"]

        assert stats == {"applicationCount": 3, "decidedCount": 2, "successRate": 0.5}

    def test_인용된_태스크를_한_번만_물어_제목_표를_채운다(
        self, client: TestClient, store: SqliteLedgerSql, recipe_tasks: RecordingTaskReader
    ) -> None:
        recipe_tasks.titles["task-1"] = "첫 태스크"
        _seed(store, "r1", "active", slices=[{"taskId": "task-1"}])
        _seed(store, "r2", "active", slices=[{"taskId": "task-1"}, {"taskId": "task-2"}])

        body = client.get("/api/agent/recipes").json()

        assert body["data"]["taskTitles"] == {"task-1": "첫 태스크"}
        assert recipe_tasks.calls == [["task-1", "task-2"]]

    def test_인용된_태스크가_없으면_추적을_부르지_않는다(
        self, client: TestClient, store: SqliteLedgerSql, recipe_tasks: RecordingTaskReader
    ) -> None:
        _seed(store, "r1", "active")

        assert client.get("/api/agent/recipes").json()["data"]["taskTitles"] == {}
        assert recipe_tasks.calls == []

    def test_어휘에_없는_상태는_계약이_정한_400_으로_거절한다(self, client: TestClient) -> None:
        res = client.get("/api/agent/recipes", params={"status": "unknown"})

        assert res.status_code == 400
        assert res.json()["error"]["code"] == "validation_error"


class Test검색:
    def test_점수가_높은_것부터_얇은_적중을_낸다(
        self, client: TestClient, recipe_search: RecordingSearch
    ) -> None:
        recipe_search.hits.append(RecipeSearchHit("r1", "제목", "의도", "설명", ["조건"], 2.5))

        body = client.get("/api/agent/recipes/search", params={"q": "마이그레이션"}).json()

        assert body["data"]["items"] == [
            {
                "recipeId": "r1",
                "title": "제목",
                "intent": "의도",
                "description": "설명",
                "useWhen": ["조건"],
                "score": 2.5,
            }
        ]
        assert recipe_search.calls == [(USER, "마이그레이션", 3)]

    def test_공백뿐인_질의는_색인을_부르지_않고_빈_목록을_낸다(
        self, client: TestClient, recipe_search: RecordingSearch
    ) -> None:
        res = client.get("/api/agent/recipes/search", params={"q": "   "})

        assert res.status_code == 200
        assert res.json()["data"]["items"] == []
        assert recipe_search.calls == []

    @pytest.mark.parametrize("limit", ["0", "11", "x"])
    def test_상한_밖의_값은_계약이_정한_400_으로_거절한다(self, client: TestClient, limit: str) -> None:
        res = client.get("/api/agent/recipes/search", params={"q": "질의", "limit": limit})

        assert res.status_code == 400

    def test_질의를_싣지_않으면_거절한다(self, client: TestClient) -> None:
        assert client.get("/api/agent/recipes/search").status_code == 400

    def test_고정_경로가_식별자_경로보다_앞서_잡힌다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        _seed(store, "search", "active")

        assert client.get("/api/agent/recipes/search", params={"q": "질의"}).status_code == 200


class Test단건_조회:
    def test_전문과_통계와_적용_이력을_함께_낸다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        _seed(store, "r1", "active")
        _application(store, "a1", "r1", "completed")

        body = client.get("/api/agent/recipes/r1").json()["data"]

        assert body["recipe"]["id"] == "r1"
        assert body["stats"]["decidedCount"] == 1
        assert [one["id"] for one in body["applications"]] == ["a1"]

    def test_남의_레시피는_없는_것과_같은_거절을_낸다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        _seed(store, "r1", "active", user_id="other")

        res = client.get("/api/agent/recipes/r1")

        assert res.status_code == 404
        assert res.json()["error"] == {"code": "not_found", "message": "Recipe not found"}


class Test상태_전이:
    def test_채택은_해소_시각을_적고_색인_반영을_적재한다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        _seed(store, "r1", "candidate")

        body = client.post("/api/agent/recipes/r1/accept").json()["data"]

        assert body["recipe"]["status"] == "active"
        assert body["recipe"]["resolvedAt"] is not None
        assert [row["target_id"] for row in store.rows("search_outbox")] == ["r1"]

    def test_채택은_같은_사용자의_부모를_대체됨으로_함께_옮긴다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        _seed(store, "parent", "active")
        _seed(store, "child", "candidate", parent="parent")

        client.post("/api/agent/recipes/child/accept")

        rows = {row["id"]: row["status"] for row in store.rows("recipes")}
        assert rows == {"parent": "superseded", "child": "active"}
        assert sorted(row["target_id"] for row in store.rows("search_outbox")) == ["child", "parent"]

    def test_부모가_남의_것이면_부모를_건드리지_않고_채택만_한다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        _seed(store, "parent", "active", user_id="other")
        _seed(store, "child", "candidate", parent="parent")

        client.post("/api/agent/recipes/child/accept")

        rows = {row["id"]: row["status"] for row in store.rows("recipes")}
        assert rows == {"parent": "active", "child": "active"}

    def test_후보가_아니면_채택과_보류를_거절한다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        _seed(store, "r1", "active")

        for path in ("accept", "dismiss"):
            res = client.post(f"/api/agent/recipes/r1/{path}")

            assert res.status_code == 409
            assert res.json()["error"]["code"] == "recipe.not-candidate"

    def test_보류는_해소_시각을_적는다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        _seed(store, "r1", "candidate")

        body = client.post("/api/agent/recipes/r1/dismiss").json()["data"]

        assert body["recipe"]["status"] == "dismissed"
        assert body["recipe"]["resolvedAt"] is not None

    def test_폐기는_채택_시점의_해소_시각을_그대로_둔다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        _seed(store, "r1", "candidate")
        accepted = client.post("/api/agent/recipes/r1/accept").json()["data"]["recipe"]

        retired = client.post("/api/agent/recipes/r1/retire").json()["data"]["recipe"]

        assert retired["status"] == "retired"
        assert retired["resolvedAt"] == accepted["resolvedAt"]

    def test_채택되지_않은_레시피는_폐기하지_못한다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        _seed(store, "r1", "candidate")

        res = client.post("/api/agent/recipes/r1/retire")

        assert res.status_code == 409
        assert res.json()["error"]["code"] == "recipe.not-active"

    def test_전이가_거절되면_색인_반영도_적재하지_않는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        _seed(store, "r1", "active")

        client.post("/api/agent/recipes/r1/accept")

        assert store.rows("search_outbox") == []


class Test편집:
    def test_실은_칸만_고치고_판과_편집_주체를_옮긴다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        _seed(store, "r1", "active")

        body = client.patch("/api/agent/recipes/r1", json={"title": "새 제목"}).json()["data"]

        assert body["recipe"]["title"] == "새 제목"
        assert body["recipe"]["intent"] == "의도"
        assert body["recipe"]["rev"] == 2
        assert body["recipe"]["userEdited"] is True
        assert body["recipe"]["lastEditedBy"] == "user"

    def test_고칠_칸을_하나도_싣지_않으면_거절한다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        _seed(store, "r1", "active")

        assert client.patch("/api/agent/recipes/r1", json={}).status_code == 400

    def test_계약에_없는_칸은_거절한다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        _seed(store, "r1", "active")

        assert client.patch("/api/agent/recipes/r1", json={"status": "retired"}).status_code == 400

    def test_채택되지_않은_레시피는_고치지_못한다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        _seed(store, "r1", "candidate")

        res = client.patch("/api/agent/recipes/r1", json={"title": "새 제목"})

        assert res.status_code == 409
        assert res.json()["error"]["code"] == "recipe.not-active"


class Test삭제:
    @pytest.mark.parametrize("status", ["dismissed", "retired"])
    def test_행을_지우지_않고_지운_시각을_적는다(
        self, client: TestClient, store: SqliteLedgerSql, status: str
    ) -> None:
        _seed(store, "r1", status)

        body = client.delete("/api/agent/recipes/r1").json()["data"]

        assert body == {"deleted": True, "id": "r1"}
        assert store.rows("recipes")[0]["deleted_at"] is not None
        assert [row["target_id"] for row in store.rows("search_outbox")] == ["r1"]

    @pytest.mark.parametrize("status", ["candidate", "active", "superseded"])
    def test_지울_수_없는_상태는_400_으로_거절한다(
        self, client: TestClient, store: SqliteLedgerSql, status: str
    ) -> None:
        _seed(store, "r1", status)

        res = client.delete("/api/agent/recipes/r1")

        assert res.status_code == 400
        assert res.json()["error"]["code"] == "recipe.not-deletable"


class Test자기보고:
    def test_열린_적용_이력이_있으면_그_행에_결과를_적는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        _seed(store, "r1", "active")
        _application(store, "a1", "r1", None)

        body = client.post(
            "/api/agent/recipes/r1/outcome",
            json={"taskId": "task-1", "outcome": "completed", "note": "잘 됐다"},
        ).json()["data"]

        assert body["application"]["id"] == "a1"
        assert body["application"]["outcome"] == "completed"
        assert body["application"]["note"] == "잘 됐다"
        assert len(store.rows("recipe_applications")) == 1

    def test_열린_이력이_없으면_manual_행을_만들어_적는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        _seed(store, "r1", "active")

        body = client.post(
            "/api/agent/recipes/r1/outcome", json={"taskId": "task-9", "outcome": "abandoned"}
        ).json()["data"]

        assert body["application"]["injectedVia"] == "manual"
        assert body["application"]["note"] is None
        row = store.rows("recipe_applications")[0]
        assert row["anchor_event_id"] is None
        assert row["anchor_seq"] is None

    def test_같은_태스크에_다시_보고하면_앞의_결과를_덮는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        _seed(store, "r1", "active")
        _application(store, "a1", "r1", "completed")

        client.post("/api/agent/recipes/r1/outcome", json={"taskId": "task-1", "outcome": "abandoned"})

        rows = store.rows("recipe_applications")
        assert [row["outcome"] for row in rows] == ["abandoned"]

    def test_어휘에_없는_결과는_거절한다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        _seed(store, "r1", "active")

        res = client.post("/api/agent/recipes/r1/outcome", json={"taskId": "task-1", "outcome": "unknown"})

        assert res.status_code == 400

    def test_남의_레시피는_없는_것과_같은_거절을_낸다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        _seed(store, "r1", "active", user_id="other")

        res = client.post("/api/agent/recipes/r1/outcome", json={"taskId": "task-1", "outcome": "completed"})

        assert res.status_code == 404
