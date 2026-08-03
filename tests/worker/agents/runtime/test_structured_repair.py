"""스키마를 어긴 구조화 산출이 한 번 되먹여져 다시 오는지 검증한다(네트워크 없음)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain.agents.structured_output import StructuredOutputValidationError
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field, PrivateAttr

from tracer_agent.worker.agents.runtime.llm.structured_repair import (
    REPAIR_DIRECTIVE,
    StructuredOutputRepairMiddleware,
)

# 공급자가 강제하지 못하는 제약 하나를 스키마에 둔다.
_LIMIT = 10


class _Draft(BaseModel):
    verdict: str = Field(max_length=_LIMIT)


class _ProviderModel(GenericFakeChatModel):
    """공급자 강제 경로를 밟도록 구조화 출력을 지원한다고 알리는 대역이다."""

    _seen: list[list[Any]] = PrivateAttr(default_factory=list)

    @property
    def seen(self) -> list[list[Any]]:
        return self._seen

    def bind_tools(self, _tools: Any, **_kwargs: Any) -> _ProviderModel:
        return self

    async def ainvoke(self, _input: Any, _config: Any = None, **_kwargs: Any) -> AIMessage:
        messages = list(_input) if isinstance(_input, list) else [_input]
        self._seen.append(messages)
        return next(iter(self.messages))  # type: ignore[arg-type]


def _model(payloads: list[str]) -> _ProviderModel:
    replies = iter([AIMessage(content=text) for text in payloads])
    return _ProviderModel(messages=replies, profile={"structured_output": True})


def _too_long() -> str:
    return json.dumps({"verdict": "열 글자를 확실히 넘기는 문장이다"}, ensure_ascii=False)


def _short() -> str:
    return json.dumps({"verdict": "짧다"}, ensure_ascii=False)


async def test_스키마를_어긴_산출은_사유와_함께_다시_받는다() -> None:
    model = _model([_too_long(), _short()])
    agent = create_agent(
        model, tools=[], response_format=_Draft, middleware=[StructuredOutputRepairMiddleware()]
    )

    result = await agent.ainvoke({"messages": []})

    assert result["structured_response"].verdict == "짧다"
    # 두 번째 호출은 무엇이 걸렸는지 담은 지시를 꼬리에 달고 간다.
    directive = str(model.seen[-1][-1].content)
    assert directive.startswith(REPAIR_DIRECTIVE[:40])
    assert "verdict" in directive


async def test_되먹인_뒤에도_어기면_그대로_올라온다() -> None:
    # 되먹임은 한 번뿐이므로 두 번째도 어기면 실행이 그 사실을 알고 끝난다.
    model = _model([_too_long(), _too_long()])
    agent = create_agent(
        model, tools=[], response_format=_Draft, middleware=[StructuredOutputRepairMiddleware()]
    )

    with pytest.raises(StructuredOutputValidationError):
        await agent.ainvoke({"messages": []})


async def test_스키마를_지킨_산출은_다시_묻지_않는다() -> None:
    model = _model([_short()])
    agent = create_agent(
        model, tools=[], response_format=_Draft, middleware=[StructuredOutputRepairMiddleware()]
    )

    result = await agent.ainvoke({"messages": []})

    assert result["structured_response"].verdict == "짧다"
    assert len(model.seen) == 1
