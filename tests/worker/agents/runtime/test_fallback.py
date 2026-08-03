"""공급자 오류에서만 대체 모델로 한 번 넘어가는 FallbackModelMiddleware를 검증한다."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from anthropic import APIConnectionError, BadRequestError
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from tracer_agent.worker.agents.runtime.errors import BudgetExceeded, DeadlineExceeded, OutputTruncated
from tracer_agent.worker.agents.runtime.llm.fallback import FallbackModelMiddleware

_PRIMARY = GenericFakeChatModel(messages=iter([]))
_FALLBACK = GenericFakeChatModel(messages=iter([]))
_SUCCESS = ModelResponse(result=[])


def _request() -> ModelRequest[Any]:
    return ModelRequest(model=_PRIMARY, messages=[])


def _provider_error() -> APIConnectionError:
    return APIConnectionError(
        message="공급자 연결이 끊겼다", request=httpx.Request("POST", "https://api.anthropic.com")
    )


def _handler(
    *, fails: int, error: BaseException
) -> tuple[Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]], list[Any]]:
    calls: list[Any] = []

    async def handler(request: ModelRequest[Any]) -> ModelResponse[Any]:
        calls.append(request.model)
        if len(calls) <= fails:
            raise error
        return _SUCCESS

    return handler, calls


async def test_공급자_오류면_대체_모델로_한_번_넘어간다() -> None:
    handler, calls = _handler(fails=1, error=_provider_error())
    middleware = FallbackModelMiddleware(_FALLBACK)

    result = await middleware.awrap_model_call(_request(), handler)

    assert result is _SUCCESS
    assert calls == [_PRIMARY, _FALLBACK]


async def test_대체_모델도_실패하면_그대로_올라오고_세_번째_시도는_없다() -> None:
    error = _provider_error()
    handler, calls = _handler(fails=2, error=error)
    middleware = FallbackModelMiddleware(_FALLBACK)

    with pytest.raises(APIConnectionError):
        await middleware.awrap_model_call(_request(), handler)

    assert calls == [_PRIMARY, _FALLBACK]


async def test_취소는_대체_없이_그대로_재전파된다() -> None:
    async def handler(_request: ModelRequest[Any]) -> ModelResponse[Any]:
        raise asyncio.CancelledError

    middleware = FallbackModelMiddleware(_FALLBACK)

    with pytest.raises(asyncio.CancelledError):
        await middleware.awrap_model_call(_request(), handler)


async def test_요청이_틀린_오류는_대체_없이_그대로_재전파된다() -> None:
    # 같은 요청을 대체 모델에 보내도 같은 거절이 오므로 예산만 더 쓴다.
    request = httpx.Request("POST", "https://api.anthropic.com")
    invalid = BadRequestError(
        message="max_tokens가 모델 상한을 넘는다",
        response=httpx.Response(400, request=request),
        body=None,
    )
    handler, calls = _handler(fails=1, error=invalid)
    middleware = FallbackModelMiddleware(_FALLBACK)

    with pytest.raises(BadRequestError):
        await middleware.awrap_model_call(_request(), handler)

    assert calls == [_PRIMARY]


@pytest.mark.parametrize(
    "error", [BudgetExceeded("예산 소진"), DeadlineExceeded("데드라인 초과"), OutputTruncated("절단")]
)
async def test_예산_데드라인_절단은_대체_없이_그대로_재전파된다(error: Exception) -> None:
    handler, calls = _handler(fails=1, error=error)
    middleware = FallbackModelMiddleware(_FALLBACK)

    with pytest.raises(type(error)):
        await middleware.awrap_model_call(_request(), handler)

    assert calls == [_PRIMARY]
