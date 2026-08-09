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
from tests.support.recipes import RecordingArchiver, RecordingSearch, RecordingTaskReader
from tests.support.services import fake_services
from tracer_agent.api import app as app_module
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql


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
def recipe_search() -> RecordingSearch:
    return RecordingSearch()


@pytest.fixture
def recipe_tasks() -> RecordingTaskReader:
    return RecordingTaskReader()


@pytest.fixture
def task_archiver() -> RecordingArchiver:
    return RecordingArchiver()


@pytest.fixture
def client(
    store: SqliteLedgerSql,
    executor: RecordingExecutor,
    updates: RecordingUpdates,
    dispatch: RecordingDispatch,
    scan_anchors: FakeScanAnchors,
    recipe_search: RecordingSearch,
    recipe_tasks: RecordingTaskReader,
    task_archiver: RecordingArchiver,
) -> Iterator[TestClient]:
    with TestClient(app_module.create_app()) as test_client:
        test_client.app.state.services = fake_services(
            execution_sql=SingleSql(store),
            chat_tool_executor=executor,
            execution_updates=updates,
            execution_dispatch=dispatch,
            execution_watch=SilentWatch(),
            scan_anchors=scan_anchors,
            recipe_search=recipe_search,
            recipe_tasks=recipe_tasks,
            task_archiver=task_archiver,
        )
        yield test_client
