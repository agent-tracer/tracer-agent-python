"""최종 prompt bundle hash가 TypeScript 구현체와 같은 바이트를 먹는지 검증한다."""

from tracer_agent.worker.agents.shared.resolved_prompt_hash import resolved_prompt_bundle_hash


def test_single_template_hash는_content_hash와_같다() -> None:
    result = resolved_prompt_bundle_hash({"lan.title.investigator.system": "prompt"})
    assert result.resolved_prompt_hash == "cf07194ee232eb531e15f690000d19846dea69cf05504782658afcfacb9228a2"


def test_multi_template_hash는_TS_byte_length_prefix_기대값과_같다() -> None:
    result = resolved_prompt_bundle_hash(
        {
            "lan.recipe.survey.system": "survey",
            "lan.recipe.investigator.system": "main",
        }
    )
    assert [item.template_key for item in result.resolved_prompt_hashes] == [
        "lan.recipe.investigator.system",
        "lan.recipe.survey.system",
    ]
    assert result.resolved_prompt_hash == "b0a15fba629a5920c74848a2e313b29e6825467a05104e840b7ad99767aea4cf"
