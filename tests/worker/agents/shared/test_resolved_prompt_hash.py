"""최종 prompt bundle hash가 TypeScript 구현체와 같은 바이트를 먹는지 검증한다."""

from tracer_agent.worker.agents.shared.resolved_prompt_hash import resolved_prompt_bundle_hash


def test_single_template_hash는_content_hash와_같다() -> None:
    result = resolved_prompt_bundle_hash({"title.investigator.system": "prompt"})
    assert result.resolved_prompt_hash == "cf07194ee232eb531e15f690000d19846dea69cf05504782658afcfacb9228a2"


def test_multi_template_hash는_TS_byte_length_prefix_기대값과_같다() -> None:
    result = resolved_prompt_bundle_hash(
        {
            "recipe.survey.system": "survey",
            "recipe.investigator.system": "main",
        }
    )
    assert [item.template_key for item in result.resolved_prompt_hashes] == [
        "recipe.investigator.system",
        "recipe.survey.system",
    ]
    assert result.resolved_prompt_hash == "eb82d9f29d74d25cad5e8ab0290b606ba5da89345aad55766910ae099a9cf86e"
