"""창구 테스트가 쓰는 메모리 원장과 대역을 붙인 앱을 세운다."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tests.support.chat_surface import (
    RecordingDispatch,
    RecordingExecutor,
    RecordingUpdates,
    SilentWatch,
    SingleSql,
)
from tests.support.fakes import FakeScanAnchors
from tests.support.sqlite_ledger import SqliteLedgerSql
from tracer_agent.api import app as app_module


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    yield ledger
    ledger.close()


@pytest.fixture
def executor() -> RecordingExecutor:
    return RecordingExecutor()


@pytest.fixture
def updates() -> RecordingUpdates:
    return RecordingUpdates()


@pytest.fixture
def dispatch() -> RecordingDispatch:
    return RecordingDispatch()


@pytest.fixture
def scan_anchors() -> FakeScanAnchors:
    return FakeScanAnchors()


@pytest.fixture
def client(
    store: SqliteLedgerSql,
    executor: RecordingExecutor,
    updates: RecordingUpdates,
    dispatch: RecordingDispatch,
    scan_anchors: FakeScanAnchors,
) -> Iterator[TestClient]:
    with TestClient(app_module.create_app()) as test_client:
        test_client.app.state.execution_sql = SingleSql(store)
        test_client.app.state.chat_tool_executor = executor
        test_client.app.state.execution_updates = updates
        test_client.app.state.execution_dispatch = dispatch
        test_client.app.state.execution_watch = SilentWatch()
        test_client.app.state.scan_anchors = scan_anchors
        yield test_client
