"""스레드가 계약의 문턱을 넘으면 재생 창 바깥의 오래된 메시지를 요약 한 칸으로 접는다."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from tracer_agent.shared.agents.chat.summary_spec import chat_summary_spec, should_summarize
from tracer_agent.shared.agents.chat.surface.ledger import ChatSurfaceLedger
from tracer_agent.shared.agents.chat.surface.replay import select_messages_to_fold
from tracer_agent.shared.agents.envelope.catalog import CATALOG
from tracer_agent.shared.agents.runtime.ledger import SqlRow, SqlSource
from tracer_agent.shared.agents.shared.job_kinds import AgentJobKind

from ..runtime.llm.client import make_chat
from ..runtime.llm.trajectory import step_content_text
from .prompts import CHAT_SUMMARY_SYSTEM_PROMPT, build_summary_prompt

_log = logging.getLogger(__name__)

# 요약은 도구 없는 단발 호출이라 계약이 가장 싼 등급으로 충분하다고 정한다.
SUMMARY_MODEL_KIND = AgentJobKind.TITLE_SUGGESTION.wire


class ChatThreadSummarizer(Protocol):
    """접을 메시지를 실은 프롬프트를 받아 요약 본문 하나를 내는 창구다."""

    async def summarize(self, prompt: str) -> str:
        """요약 본문 하나를 낸다."""
        ...


class ModelChatSummarizer:
    """계약이 정한 상한 안에서 모델을 도구 없이 한 번 불러 요약 본문을 만든다."""

    def __init__(self, api_key: Callable[[], Awaitable[str]]) -> None:
        self._api_key = api_key

    async def summarize(self, prompt: str) -> str:
        """자격을 받아 요약 전용 모델을 한 번 부르고 그 본문을 낸다."""
        spec = chat_summary_spec()
        chat = make_chat(
            CATALOG[SUMMARY_MODEL_KIND].default_model,
            await self._api_key(),
            spec.deadline_ms,
            feature_max_output_tokens=spec.max_output_tokens,
        )
        answer = await chat.ainvoke([SystemMessage(CHAT_SUMMARY_SYSTEM_PROMPT), HumanMessage(prompt)])
        return step_content_text(answer.content)


class ChatSummaryProjection:
    """문턱을 넘은 스레드의 오래된 메시지를 요약으로 접고 실패를 그 턴의 실패로 올리지 않는다."""

    def __init__(self, source: SqlSource, summarizer: ChatThreadSummarizer) -> None:
        self._source = source
        self._summarizer = summarizer

    async def project(self, thread_id: str, now: datetime) -> bool:
        """접을 것이 있으면 요약을 만들어 원장에 적고 적었는지 낸다."""
        try:
            return await self._project(thread_id, now)
        except Exception as failed:
            _log.warning("chat thread %s summary was not produced: %s", thread_id, failed)
            return False

    async def _project(self, thread_id: str, now: datetime) -> bool:
        folded = await self._read(thread_id)
        if folded is None:
            return False
        existing, older = folded
        summary = await self._summarizer.summarize(build_summary_prompt(older, existing))
        if not summary.strip():
            return False
        return await self._fold(thread_id, summary.strip(), now)

    async def _read(self, thread_id: str) -> tuple[str | None, list[SqlRow]] | None:
        """요약 호출이 원장 연결을 기다리지 않도록 이 자리에서 읽기를 끝낸다."""
        async with self._source.connect() as sql:
            ledger = ChatSurfaceLedger(sql)
            thread = await ledger.find_thread(thread_id)
            if thread is None:
                return None
            messages = await ledger.list_messages(thread_id)
        if not should_summarize(str(row["content"]) for row in messages):
            return None
        older = select_messages_to_fold(messages)
        if not older:
            return None
        return _existing_summary(thread), older

    async def _fold(self, thread_id: str, summary: str, now: datetime) -> bool:
        async with self._source.connect() as sql:
            return await ChatSurfaceLedger(sql).fold_thread_summary(thread_id, summary, now)


def _existing_summary(thread: SqlRow) -> str | None:
    value = thread["summary"]
    return None if value is None else str(value)
