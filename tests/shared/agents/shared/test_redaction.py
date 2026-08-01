"""가리는 낱말과 비교 절차와 표시가 계약에서 오는지 검증한다."""

from __future__ import annotations

import pytest

from tests.support.contract import shared_contract
from tracer_agent.shared.agents.shared.redaction import (
    RedactionStage,
    discards,
    inspects_keys,
    is_suspect_key,
    marker,
    redact,
)

_RULES = shared_contract("redaction.json")


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


class Test자리마다의_판단:
    @pytest.mark.parametrize("stage", list(RedactionStage))
    def test_세_자리가_모두_key_를_본다(self, stage: RedactionStage) -> None:
        assert inspects_keys(stage)

    def test_추적만_payload를_통째로_폐기한다(self) -> None:
        assert discards(RedactionStage.TRACE)
        assert not discards(RedactionStage.QUERY)
        assert not discards(RedactionStage.OUTPUT)

    def test_가리는_자리는_걸린_자리만_바꾸고_남은_본문을_그대로_둔다(self) -> None:
        payload = {"apiKey": "sk-ant-live", "note": "평범한 본문"}

        covered = redact(payload, stage=RedactionStage.QUERY)

        assert covered == {"apiKey": marker(), "note": "평범한 본문"}
