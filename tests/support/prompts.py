"""테스트가 실행에 넘길 프롬프트를 계약에서 한 번만 세운다."""

from __future__ import annotations

from tracer_agent.worker.agents.shared.contract_prompt_source import ContractPromptSource
from tracer_agent.worker.agents.shared.prompt_source_port import AgentPrompt

_SOURCE = ContractPromptSource()

CHAT_PROMPT: AgentPrompt = _SOURCE.resolve("chat")
RECIPE_SCAN_PROMPT: AgentPrompt = _SOURCE.resolve("recipe-scan")
TASK_CLEANUP_PROMPT: AgentPrompt = _SOURCE.resolve("task-cleanup")
TITLE_SUGGESTION_PROMPT: AgentPrompt = _SOURCE.resolve("title-suggestion")

JOB_PROMPTS: dict[str, AgentPrompt] = {
    "recipe-scan": RECIPE_SCAN_PROMPT,
    "task-cleanup": TASK_CLEANUP_PROMPT,
    "title-suggestion": TITLE_SUGGESTION_PROMPT,
}
