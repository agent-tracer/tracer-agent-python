"""창구가 계약이 정한 성공과 실패 봉투를 문서로도 선언하는지 검증한다."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

SETTING_PATH = "/api/agent/settings/{key}"
JOBS_PATH = "/api/agent/jobs"
ENVELOPE_REF = "#/components/schemas/ErrorEnvelope"


def schema(client: TestClient) -> dict[str, Any]:
    """이 프로세스가 내는 OpenAPI 문서를 읽는다."""
    document: dict[str, Any] = client.get("/openapi.json").json()
    return document


def test_성공_응답이_계약의_봉투를_가리킨다(client: TestClient) -> None:
    ok_schema = schema(client)["paths"][JOBS_PATH]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]

    assert ok_schema["$ref"] == "#/components/schemas/SuccessEnvelope"


def test_실패_상태마다_계약의_오류_봉투를_선언한다(client: TestClient) -> None:
    responses = schema(client)["paths"][JOBS_PATH]["post"]["responses"]

    for status in ("400", "404", "409"):
        content = responses[status]["content"]["application/json"]["schema"]
        assert content["$ref"] == ENVELOPE_REF


def test_오류_봉투가_계약이_정한_칸을_갖는다(client: TestClient) -> None:
    components = schema(client)["components"]["schemas"]

    assert components["ErrorEnvelope"]["properties"]["ok"]["const"] is False
    error = components["WireError"]["properties"]
    assert {"code", "message", "details"} <= set(error)


def test_설정_쓰기가_잘못된_본문에_같은_상태를_선언한다(client: TestClient) -> None:
    responses = schema(client)["paths"][SETTING_PATH]["put"]["responses"]

    assert responses["400"]["content"]["application/json"]["schema"]["$ref"] == ENVELOPE_REF
