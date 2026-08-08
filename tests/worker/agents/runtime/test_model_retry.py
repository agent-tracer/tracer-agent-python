"""같은 모델로 먼저 재시도하고 소진된 뒤에만 대체 모델로 넘어가는 순서를 검증한다."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from anthropic import OverloadedError
from langchain.agents.factory import _chain_async_model_call_handlers
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from tracer_agent.worker.agents.runtime.errors import BudgetExceeded, OutputTruncated
from tracer_agent.worker.agents.runtime.llm.fallback import FallbackModelMiddleware
from tracer_agent.worker.agents.runtime.llm.retry import model_retry_middleware, tool_retry_middleware

_PRIMARY = GenericFakeChatModel(messages=iter([]))
_FALLBACK = GenericFakeChatModel(messages=iter([]))
_SUCCESS = ModelResponse(result=[])


def _overloaded() -> OverloadedError:
    return OverloadedError(
        message="일시적으로 과부하다",
        response=httpx.Response(529, request=httpx.Request("POST", "https://api.anthropic.com")),
        body=None,
    )


def _request() -> ModelRequest[Any]:
    return ModelRequest(model=_PRIMARY, messages=[])


def _handler(*, fails: int, error: BaseException) -> tuple[Any, list[Any]]:
    calls: list[Any] = []

    async def raw_handler(request: ModelRequest[Any]) -> ModelResponse[Any]:
        calls.append(request.model)
        if len(calls) <= fails:
            raise error
        return _SUCCESS

    return raw_handler, calls


def _composed() -> Callable[
    [ModelRequest[Any], Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]]], Awaitable[Any]
]:
    """FallbackModelMiddleware를 바깥에, ModelRetryMiddleware를 안쪽에 둔 합성 핸들러를 만든다."""
    fallback = FallbackModelMiddleware(_FALLBACK)
    retry = model_retry_middleware(max_retries=2)
    composed = _chain_async_model_call_handlers([fallback.awrap_model_call, retry.awrap_model_call])
    assert composed is not None
    return composed


async def test_일시_오류는_같은_모델로_재시도되고_소진된_뒤에만_대체_모델로_간다() -> None:
    raw_handler, calls = _handler(fails=3, error=_overloaded())

    await _composed()(_request(), raw_handler)

    assert calls == [_PRIMARY, _PRIMARY, _PRIMARY, _FALLBACK]


async def test_같은_모델_재시도만으로_회복되면_대체_모델을_부르지_않는다() -> None:
    raw_handler, calls = _handler(fails=1, error=_overloaded())

    await _composed()(_request(), raw_handler)

    assert calls == [_PRIMARY, _PRIMARY]


@pytest.mark.parametrize("error", [BudgetExceeded("예산 소진"), OutputTruncated("절단")])
async def test_예산_초과와_출력_절단은_재시도되지_않고_대체_모델로도_가지_않는다(error: Exception) -> None:
    raw_handler, calls = _handler(fails=1, error=error)

    with pytest.raises(type(error)):
        await _composed()(_request(), raw_handler)

    assert calls == [_PRIMARY]


def test_재시도_대기는_지터를_섞는다() -> None:
    # 팬아웃이 함께 529를 받으면 고정 백오프는 모두를 같은 시각에 다시 부딪히게 한다.
    assert model_retry_middleware().jitter is True
    assert tool_retry_middleware(()).jitter is True
