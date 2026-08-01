"""테스트가 실행에 넘길 프롬프트를 계약에서 한 번만 세운다."""

from __future__ import annotations

from tracer_agent.worker.agents.shared.contract_prompt_source import ContractPromptSource
from tracer_agent.worker.agents.shared.prompt_source_port import AgentPrompt

_SOURCE = ContractPromptSource()

CHAT_PROMPT: AgentPrompt = _SOURCE.resolve("chat")
