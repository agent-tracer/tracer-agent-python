"""가리는 낱말과 비교 절차와 표시가 계약에서 오는지 검증한다."""

from __future__ import annotations

import pytest

from tests.support.contract import shared_contract
from tracer_agent.shared.agents.shared.redaction import (
    RedactionStage,
    discards,
    inspects_keys,
    inspects_values,
    is_suspect_key,
    is_suspect_text,
    marker,
    redact,
    redact_text,
)

_RULES = shared_contract("redaction.json")
_BODY = "eyJhbGciOiJIUzI1NiJ9"


class Test가린_자리의_표시:
    def test_계약이_적은_글자를_쓴다(self) -> None:
        assert marker() == _RULES["marker"]

    def test_태그로_읽히는_글자를_쓰지_않는다(self) -> None:
        assert "<" not in marker()
        assert ">" not in marker()


class Testkey_쪽_절차:
    def test_계약이_적은_낱말을_전부_알아본다(self) -> None:
        assert all(is_suspect_key(str(word)) for word in _RULES["keys"]["words"])

    @pytest.mark.parametrize("name", ["apiKey", "api_key", "api-key", "API KEY"])
    def test_구분자를_지우므로_같은_낱말로_모인다(self, name: str) -> None:
        assert is_suspect_key(name)

    def test_낱말을_품지_않은_이름은_지나간다(self) -> None:
        assert not is_suspect_key("assistantText")


class Test값_쪽_절차:
    def test_계약이_값에_둔_낱말은_셋이다(self) -> None:
        assert [str(word) for word in _RULES["values"]["words"]] == ["sk-ant-", "lsv2_", "bearer"]

    def test_자격을_말하기만_한_문장은_지나간다(self) -> None:
        assert not is_suspect_text("The bearer of this token is the worker.")

    def test_낱말_뒤에_몸통이_이어지면_걸린다(self) -> None:
        assert is_suspect_text(f"Authorization: Bearer {_BODY}")

    def test_구분자를_지키므로_대시를_지운_모양은_걸리지_않는다(self) -> None:
        assert is_suspect_text(f"sk-ant-{_BODY}")
        assert not is_suspect_text(f"skant{_BODY}")

    def test_몸통이_짧으면_걸리지_않는다(self) -> None:
        minimum = int(_RULES["values"]["requiresTrailingBody"]["minLength"])
        assert not is_suspect_text("lsv2_" + "a" * (minimum - 1))
        assert is_suspect_text("lsv2_" + "a" * minimum)


class Test자리마다의_판단:
    @pytest.mark.parametrize("stage", list(RedactionStage))
    def test_세_자리가_모두_key와_값을_함께_본다(self, stage: RedactionStage) -> None:
        assert inspects_keys(stage)
        assert inspects_values(stage)

    def test_추적만_payload를_통째로_폐기한다(self) -> None:
        assert discards(RedactionStage.TRACE)
        assert not discards(RedactionStage.QUERY)
        assert not discards(RedactionStage.OUTPUT)

    def test_가리는_자리는_걸린_자리만_바꾸고_남은_본문을_그대로_둔다(self) -> None:
        payload = {"apiKey": "sk-ant-live", "note": "평범한 본문", "items": [f"Bearer {_BODY}"]}

        covered = redact(payload, stage=RedactionStage.QUERY)

        assert covered == {"apiKey": marker(), "note": "평범한 본문", "items": [marker()]}

    def test_이름이_걸리면_그_값_전체를_바꾼다(self) -> None:
        payload = {"apiKey": f"앞 sk-ant-{_BODY} 뒤"}

        assert redact(payload, stage=RedactionStage.QUERY) == {"apiKey": marker()}

    def test_값이_걸리면_낱말과_몸통이_이룬_구간만_바꾼다(self) -> None:
        covered = redact_text(f"토큰은 sk-ant-{_BODY} 입니다", stage=RedactionStage.OUTPUT)

        assert covered == f"토큰은 {marker()} 입니다"

    def test_유니코드로_접으면_길이가_달라지는_글자가_앞에_있어도_자리가_밀리지_않는다(self) -> None:
        for 낱말 in ("Straße", "İstanbul"):
            covered = redact_text(f"{낱말} 에서 sk-ant-{_BODY} 를 받았다", stage=RedactionStage.OUTPUT)

            assert covered == f"{낱말} 에서 {marker()} 를 받았다"

    def test_낱말만_있고_몸통이_없는_본문은_그대로_나간다(self) -> None:
        본문 = "The bearer of this token is the worker."

        assert redact_text(본문, stage=RedactionStage.OUTPUT) == 본문

    def test_자격이_없는_답은_그대로_나간다(self) -> None:
        assert redact_text("정리했습니다", stage=RedactionStage.OUTPUT) == "정리했습니다"
