"""요청이 대화 실행의 배선에 닿는 자리를 창구의 시그니처로 드러낸다."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from .intake.cancel import UpdateSignal
from .intake.dispatch import ExecutionDispatch
from .surface.tool_client import ChatToolExecutor
from .surface.updates import ChatExecutionUpdates


def get_execution_dispatch(request: Request) -> ExecutionDispatch:
    """대기 실행을 시작하고 진행 중인 실행을 끊는 신호 창구를 낸다."""
    dispatch: ExecutionDispatch = request.app.state.execution_dispatch
    return dispatch


def get_execution_updates(request: Request) -> UpdateSignal | None:
    """갱신 사실을 다른 replica 로 전송하는 창구를 내며 배선이 없으면 비운다."""
    updates: UpdateSignal | None = getattr(request.app.state, "execution_updates", None)
    return updates


def get_execution_watch(request: Request) -> ChatExecutionUpdates | None:
    """다른 replica 가 보낸 갱신을 듣는 창구를 내며 배선이 없으면 비운다."""
    watch: ChatExecutionUpdates | None = getattr(request.app.state, "execution_watch", None)
    return watch


def get_chat_tool_executor(request: Request) -> ChatToolExecutor:
    """승인된 쓰기 도구를 실제로 부르는 창구를 낸다."""
    executor: ChatToolExecutor = request.app.state.chat_tool_executor
    return executor


Dispatch = Annotated[ExecutionDispatch, Depends(get_execution_dispatch)]
Updates = Annotated[UpdateSignal | None, Depends(get_execution_updates)]
Watch = Annotated[ChatExecutionUpdates | None, Depends(get_execution_watch)]
ToolExecutor = Annotated[ChatToolExecutor, Depends(get_chat_tool_executor)]
