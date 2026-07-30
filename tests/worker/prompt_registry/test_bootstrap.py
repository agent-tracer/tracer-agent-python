"""부팅마다 python 백엔드로 프롬프트 조각을 등록하고 해석하는 창구를 검증한다."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.support.contract import shared_contract
from tracer_agent.worker.agents.shared.fragment_registry import fragment_content_hash, fragment_placeholders
from tracer_agent.worker.agents.task_cleanup.prompt_fragments import TASK_CLEANUP_FRAGMENT_REGISTRY
from tracer_agent.worker.prompt_registry.bootstrap import (
    PromptRegistrationError,
    _manifest_entry,
    register_and_resolve_fragments,
)

BASE_URL = "http://agent-api:8800"


def client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def resolved_from_request(request: httpx.Request) -> list[dict[str, object]]:
    body = json.loads(request.content)
    rows: list[dict[str, object]] = []
    for item in body["manifest"]:
        for binding in item["bindings"]:
            content = item["defaultContent"]
            rows.append(
                {
                    **binding,
                    "definitionKey": item["definitionKey"],
                    "codeName": item["codeName"],
                    "backend": "python",
                    "language": "en",
                    "versionId": f"version:{item['definitionKey']}",
                    "semanticVersion": "v1",
                    "content": content,
                    "contentHash": fragment_content_hash(content),
                    "placeholders": list(fragment_placeholders(content)),
                    "toolContractVersion": "1",
                    "outputSchemaVersion": "1",
                    "source": "code-default",
                }
            )
    return rows


async def test_조각_등록이_검증된_묶음을_온전히_지킨다() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{BASE_URL}/internal/prompts/fragments/register-and-resolve"
        return httpx.Response(200, json={"ok": True, "data": resolved_from_request(request)})

    async with client(respond) as http_client:
        snapshot = await register_and_resolve_fragments(http_client, BASE_URL, "local")
    assert len(snapshot) == 31


def test_묶음_항목이_언어_중립_계약과_같다() -> None:
    expected = shared_contract("prompt.fragment.manifest.json")["manifest"][0]
    actual = _manifest_entry(TASK_CLEANUP_FRAGMENT_REGISTRY["TASK_CLEANUP_REPAIR_DIRECTIVE"])
    assert actual == expected


@pytest.mark.parametrize("failure", ["missing", "hash", "backend"])
async def test_조각_등록이_어긋나면_닫힌_채로_실패한다(failure: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        rows = resolved_from_request(request)
        if failure == "missing":
            rows.pop()
        elif failure == "hash":
            rows[0]["contentHash"] = "tampered"
        else:
            rows[0]["backend"] = "claude-sdk"
        return httpx.Response(200, json={"ok": True, "data": rows})

    async with client(respond) as http_client:
        with pytest.raises(PromptRegistrationError):
            await register_and_resolve_fragments(http_client, BASE_URL, "prd")


async def test_봉투가_아닌_응답이면_조각_등록이_실패한다() -> None:
    def bare(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=resolved_from_request(request))

    async with client(bare) as http_client:
        with pytest.raises(PromptRegistrationError):
            await register_and_resolve_fragments(http_client, BASE_URL, "prd")


async def test_창구를_쓸_수_없으면_조각_등록이_실패한다() -> None:
    def unavailable(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    async with client(unavailable) as http_client:
        with pytest.raises(PromptRegistrationError):
            await register_and_resolve_fragments(http_client, BASE_URL, "prd")
