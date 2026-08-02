"""잡이 매달릴 앵커를 추적 창구에서 그 사용자 범위로 읽는다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

ANCHOR_PATH = "/api/v1/events/{event_id}"
TASK_ANCHOR_PATH = "/api/v1/tasks/{task_id}"
ANCHOR_TIMEOUT_S = 10.0
MONITOR_USER_HEADER = "x-monitor-user"
NOT_FOUND_STATUS = 404
# 근거가 사용자 발화인지를 가르는 이벤트 종류다.
USER_MESSAGE_KIND = "agent_tracer.user.message"


class RuleAnchorUnavailable(RuntimeError):
    """근거를 읽을 수 없어 접수 판정을 내릴 수 없음을 알린다."""


@dataclass(frozen=True)
class RuleAnchor:
    """규칙 생성이 근거로 삼는 이벤트 하나이며 소속 태스크와 발화 여부를 함께 나른다."""

    id: str
    task_id: str
    user_message: bool


class RuleAnchorSource(Protocol):
    """근거 이벤트 하나를 그 사용자 범위로 읽어 주는 창구다."""

    async def find(self, user_id: str, event_id: str) -> RuleAnchor | None:
        """그 사용자가 볼 수 있는 근거 이벤트이며 없으면 None이다."""
        ...


class RuleAnchorClient:
    """근거 이벤트를 자기신고 사용자 범위로 읽는 tracer-api 창구다."""

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def find(self, user_id: str, event_id: str) -> RuleAnchor | None:
        """그 사용자가 볼 수 있는 근거 이벤트를 읽고, 창구가 없다고 답하면 None을 낸다."""
        path = ANCHOR_PATH.format(event_id=event_id)
        try:
            response = await self._client.get(
                f"{self._base_url}{path}",
                headers={MONITOR_USER_HEADER: user_id},
                timeout=ANCHOR_TIMEOUT_S,
            )
        except httpx.HTTPError as unreachable:
            raise RuleAnchorUnavailable(f"rule anchor unreachable: {unreachable}") from unreachable
        if response.status_code == NOT_FOUND_STATUS:
            return None
        if response.status_code >= 400:
            raise RuleAnchorUnavailable(f"rule anchor HTTP {response.status_code}: {response.text[:500]}")
        return _anchor(_data(response))


def _data(response: httpx.Response) -> Any:
    try:
        body = response.json()
    except ValueError as malformed:
        raise RuleAnchorUnavailable("rule anchor is not JSON") from malformed
    if not isinstance(body, dict) or body.get("ok") is not True:
        raise RuleAnchorUnavailable("rule anchor answered outside the success envelope")
    return body.get("data")


def _anchor(data: Any) -> RuleAnchor | None:
    event = data.get("event") if isinstance(data, dict) else None
    if not isinstance(event, dict):
        return None
    event_id = event.get("id")
    task_id = event.get("taskId")
    if not isinstance(event_id, str) or not isinstance(task_id, str):
        return None
    return RuleAnchor(id=event_id, task_id=task_id, user_message=event.get("kind") == USER_MESSAGE_KIND)


@dataclass(frozen=True)
class ScanAnchor:
    """스캔이 근거를 캘 태스크 하나이며 자격 판정에 쓰는 세 값을 나른다."""

    id: str
    origin: str | None
    root: bool
    status: str | None

    def eligible(self, requires: Mapping[str, Any], conditions: Sequence[str]) -> bool:
        """그 표면이 요구하는 조건을 모두 만족하는지 판정한다."""
        return all(self._satisfies(requires, name) for name in conditions)

    def _satisfies(self, requires: Mapping[str, Any], name: str) -> bool:
        if name == "origin":
            return self.origin is None or self.origin not in requires["origin"]["excludes"]
        if name == "root":
            return self.root is requires["root"]["value"]
        return self.status is not None and self.status in requires["status"]["oneOf"]


class ScanAnchorSource(Protocol):
    """스캔 앵커 하나를 그 사용자 범위로 읽어 주는 창구다."""

    async def find(self, user_id: str, task_id: str) -> ScanAnchor | None:
        """그 사용자가 볼 수 있는 태스크이며 없으면 None이다."""
        ...


class ScanAnchorClient:
    """스캔 앵커를 자기신고 사용자 범위로 읽는 tracer-api 창구다."""

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def find(self, user_id: str, task_id: str) -> ScanAnchor | None:
        """그 사용자가 볼 수 있는 태스크를 읽고, 창구가 없다고 답하면 None을 낸다."""
        path = TASK_ANCHOR_PATH.format(task_id=task_id)
        try:
            response = await self._client.get(
                f"{self._base_url}{path}",
                headers={MONITOR_USER_HEADER: user_id},
                timeout=ANCHOR_TIMEOUT_S,
            )
        except httpx.HTTPError as unreachable:
            raise RuleAnchorUnavailable(f"scan anchor unreachable: {unreachable}") from unreachable
        if response.status_code == NOT_FOUND_STATUS:
            return None
        if response.status_code >= 400:
            raise RuleAnchorUnavailable(f"scan anchor HTTP {response.status_code}: {response.text[:500]}")
        return _scan_anchor(_data(response))


def _scan_anchor(data: Any) -> ScanAnchor | None:
    task = data.get("task") if isinstance(data, dict) else None
    if not isinstance(task, dict):
        return None
    identifier = task.get("id")
    if not isinstance(identifier, str) or not identifier:
        return None
    origin = task.get("origin")
    status = task.get("status")
    return ScanAnchor(
        id=identifier,
        origin=origin if isinstance(origin, str) else None,
        root=task.get("parentTaskId") is None,
        status=status if isinstance(status, str) else None,
    )
