"""프롬프트 조각 레지스트리와 조립 결과를 고정한다."""

from __future__ import annotations

import hashlib

import pytest

from tests.support.contract import shared_contract
from tracer_agent.worker.agents.chat.prompt_fragments import CHAT_FRAGMENT_REGISTRY
from tracer_agent.worker.agents.chat.prompts import ASSISTANT_SYSTEM_PROMPT
from tracer_agent.worker.agents.recipe_scan.prompt_fragments import RECIPE_SCAN_FRAGMENT_REGISTRY
from tracer_agent.worker.agents.recipe_scan.prompts import (
    INVESTIGATOR_SYSTEM_PROMPT as RECIPE_INVESTIGATOR_SYSTEM_PROMPT,
)
from tracer_agent.worker.agents.recipe_scan.prompts import PROBE_SYSTEM_PROMPT, SURVEY_SYSTEM_PROMPT
from tracer_agent.worker.agents.shared.fragment_registry import (
    canonical_fragment_content,
    fragment_content_hash,
    fragment_placeholders,
)
from tracer_agent.worker.agents.task_cleanup.prompt_fragments import TASK_CLEANUP_FRAGMENT_REGISTRY
from tracer_agent.worker.agents.task_cleanup.prompts import (
    INSPECT_SYSTEM_PROMPT,
    TRIAGE_SYSTEM_PROMPT,
)
from tracer_agent.worker.agents.task_cleanup.prompts import (
    INVESTIGATOR_SYSTEM_PROMPT as CLEANUP_INVESTIGATOR_SYSTEM_PROMPT,
)
from tracer_agent.worker.agents.title_suggestion.prompt_fragments import (
    TITLE_SUGGESTION_FRAGMENT_REGISTRY,
)
from tracer_agent.worker.agents.title_suggestion.prompts import (
    INVESTIGATOR_SYSTEM_PROMPT as TITLE_INVESTIGATOR_SYSTEM_PROMPT,
)

_INTEGRITY_CONTRACT = shared_contract("prompt.fragment.integrity.json")

_REGISTRIES = {
    "chat": CHAT_FRAGMENT_REGISTRY,
    "recipe-scan": RECIPE_SCAN_FRAGMENT_REGISTRY,
    "task-cleanup": TASK_CLEANUP_FRAGMENT_REGISTRY,
    "title-suggestion": TITLE_SUGGESTION_FRAGMENT_REGISTRY,
}

_PROMPT_HASHES = {
    "chat.system": (
        ASSISTANT_SYSTEM_PROMPT,
        "7eb4f16e263c58e412a65651ecb3ab4afba84d1ae8cd16d65f2dd0664dbb0b04",
    ),
    "recipe.investigator": (
        RECIPE_INVESTIGATOR_SYSTEM_PROMPT,
        "598fb27022244161c013cc1d65a0846b68be75aef2c6d8d5d138a04c176ff7cd",
    ),
    "recipe.probe": (
        PROBE_SYSTEM_PROMPT,
        "538a13b702dcda34a9f74ae1030c0601d95c13f2e60fb9df23842fccdb4d5a37",
    ),
    "recipe.survey": (
        SURVEY_SYSTEM_PROMPT,
        "b21cb48505a647b1e677b7f6abe8b56b7f0086961f60b205a06af27cc12ec61a",
    ),
    "cleanup.investigator": (
        CLEANUP_INVESTIGATOR_SYSTEM_PROMPT,
        "b19385d021b0af0cde14bab85e463616cf15cdb4f1abf03ee7e4261b3c8ec3c7",
    ),
    "cleanup.triage": (
        TRIAGE_SYSTEM_PROMPT,
        "dc9a4359063c5ed910db5637599b72edf0d7c9cc8e12fa437fb5d913f4dce89b",
    ),
    "cleanup.inspect": (
        INSPECT_SYSTEM_PROMPT,
        "354bfffd3ef18629660ff60b044303eeb918481de8e9a3bcdfb37fa389cf0c93",
    ),
    "title.investigator": (
        TITLE_INVESTIGATOR_SYSTEM_PROMPT,
        "82f99a62de17968457ec9665b9fc4d9195a0545b88e67ab943fbd5c1749eee99",
    ),
}


@pytest.mark.parametrize("name", sorted(_PROMPT_HASHES))
def test_조립된_시스템_프롬프트가_고정된_문장을_낸다(name: str) -> None:
    prompt, expected_hash = _PROMPT_HASHES[name]
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == expected_hash


@pytest.mark.parametrize("agent", sorted(_REGISTRIES))
def test_모든_프래그먼트가_구현체를_말하지_않는_식별자와_완전한_메타데이터를_갖는다(agent: str) -> None:
    for code_name, fragment in _REGISTRIES[agent].items():
        assert code_name.startswith(f"{agent.replace('-', '_').upper()}_")
        assert fragment.code_name == code_name
        assert fragment.definition_key.startswith(f"{agent}.")
        assert fragment.definition_key.endswith(".en")
        assert fragment.default_version == "v1"
        assert fragment.content_hash == fragment_content_hash(fragment.content)
        assert fragment.template_keys
        assert all(binding.fragment_slot for binding in fragment.bindings)
        assert all(key.startswith(f"{agent}.") for key in fragment.template_keys)


def test_자리표시자와_사용_프롬프트가_프래그먼트에_기록된다() -> None:
    fragment = TASK_CLEANUP_FRAGMENT_REGISTRY["TASK_CLEANUP_SUGGESTION_RULES"]
    assert fragment.placeholders == ("maxEvidenceEventIds",)
    assert fragment.template_keys == ("task-cleanup.investigator.system",)
    assert fragment.bindings[0].fragment_slot == "suggestionRules"


def test_정규화는_유니코드와_줄바꿈만_바꾸고_공백과_끝_개행을_보존한다() -> None:
    decomposed = " e\u0301 \r\nline\r\n"
    canonical = canonical_fragment_content(decomposed)
    assert canonical == " é \nline\n"
    assert fragment_content_hash(decomposed) == fragment_content_hash(canonical)


@pytest.mark.parametrize("case", _INTEGRITY_CONTRACT["cases"], ids=lambda case: case["name"])
def test_TS와_같은_정규화_해시_자리표시자_계약을_쓴다(case: dict[str, object]) -> None:
    content = case["content"]
    assert isinstance(content, str)
    assert canonical_fragment_content(content) == case["canonical"]
    assert fragment_content_hash(content) == case["hash"]
    assert list(fragment_placeholders(content)) == case["placeholders"]
