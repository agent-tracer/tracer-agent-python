"""chat의 시스템 프롬프트 조합을 콘솔에 펴 보이고 핵심 구성을 단언한다."""

from __future__ import annotations

from tracer_agent.shared.agents.chat.models import ChatFact
from tracer_agent.worker.agents.chat.prompt_fragments import CHAT_FRAGMENT_REGISTRY
from tracer_agent.worker.agents.chat.prompts import SYSTEM_PROMPT, build_context_prompt, build_system_prompt
from tracer_agent.worker.agents.shared.fragment_registry import fragment_content_hash, fragment_placeholders


def test_시스템_프롬프트는_정체성과_도구_전반_정책만_말한다() -> None:
    print("\n───────── chat :: converse (시스템) ─────────")
    print(SYSTEM_PROMPT)

    assert "Agent Tracer" in SYSTEM_PROMPT
    assert "reach for the tools that answer it" in SYSTEM_PROMPT
    assert "Ground every factual claim in what a tool returned" in SYSTEM_PROMPT
    assert "Find first, then drill in" in SYSTEM_PROMPT
    assert "Stop calling tools the moment you can answer" in SYSTEM_PROMPT
    assert "say so plainly instead of guessing" in SYSTEM_PROMPT
    # 사실 주입만 하고 쓰기 지침을 빼면 모델이 remember_fact를 한 번도 부르지 않는다.
    assert "remember_fact" in SYSTEM_PROMPT
    assert "stable preferences" in SYSTEM_PROMPT
    # 도구가 닿지 않는 자원을 정체성 문장이 세면 사용자가 그것을 시키고 실패를 본다.
    assert "settings" not in SYSTEM_PROMPT


def test_도구_하나의_사용법은_도구_설명에_두고_프롬프트에_적지_않는다() -> None:
    # 커서 페이징과 과금 경고는 계약이 소유한 도구 설명에 있어 프롬프트가 되풀이하면 갈라진다.
    assert "cursor" not in SYSTEM_PROMPT
    assert "metered" not in SYSTEM_PROMPT
    assert "enqueue_job" not in SYSTEM_PROMPT
    # 도구 이름을 박은 조사 순서는 도구가 늘거나 이름이 바뀌면 조용히 낡는다.
    for name in ("search_tasks", "search_events", "get_task", "get_timeline", "get_rule_evidence"):
        assert name not in SYSTEM_PROMPT


def test_DB_override는_지정한_fragment_slot만_바꾼다() -> None:
    snapshot: dict[tuple[str, str], dict[str, object]] = {}
    for fragment in CHAT_FRAGMENT_REGISTRY.values():
        binding = fragment.bindings[0]
        content = fragment.content
        source = "code-default"
        semantic_version = fragment.default_version
        if binding.fragment_slot == "groundingRules":
            content = "Use the evaluated grounding policy."
            source = "database-override"
            semantic_version = "v2"
        snapshot[(binding.template_key, binding.fragment_slot)] = {
            "definitionKey": fragment.definition_key,
            "codeName": fragment.code_name,
            "backend": "python",
            "content": content,
            "contentHash": fragment_content_hash(content),
            "placeholders": list(fragment_placeholders(content)),
            "source": source,
            "semanticVersion": semantic_version,
            "toolContractVersion": fragment.tool_contract_version,
            "outputSchemaVersion": fragment.output_schema_version,
        }
    resolved = build_system_prompt(snapshot)
    assert "Use the evaluated grounding policy." in resolved
    assert CHAT_FRAGMENT_REGISTRY["CHAT_MEMORY_RULE"].content in resolved
    assert CHAT_FRAGMENT_REGISTRY["CHAT_GROUNDING_RULES"].content not in resolved


def test_턴별로_바뀌는_값은_선행_컨텍스트_메시지가_싣는다() -> None:
    context = build_context_prompt("앞선 대화 요약", [ChatFact(key="tone", content="간결하게")], "ko")

    print("\n───────── chat :: converse (턴 컨텍스트) ─────────")
    print(context)

    assert context.startswith("Reply in Korean (한국어).")
    assert "- tone: 간결하게" in context
    assert "앞선 대화 요약" in context
